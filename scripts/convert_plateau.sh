#!/usr/bin/env bash
#
# PLATEAU CityGML -> 3D Tiles / GeoJSON
#
# PLATEAU GIS Converter (nusamai) の CLI で、目黒区の CityGML のうち
# 中目黒周辺の4メッシュだけを変換する。
# 手順と出典は docs/data-prep.md を参照。
#
# 使い方:
#   scripts/convert_plateau.sh
#
# CLI の場所は環境変数で変えられる（どちらも未設定ならデフォルトを使う）:
#   NUSAMAI=/path/to/nusamai scripts/convert_plateau.sh
#       nusamai バイナリを直接指定する。
#   PLATEAU_GIS_CONVERTER_DIR=/path/to/PLATEAU-GIS-Converter scripts/convert_plateau.sh
#       クローンしたディレクトリを指定し、target/release/nusamai を探す。
#   PATH 上に nusamai があればそれも拾う。
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# CLI の場所。docs/data-prep.md の手順でこのリポジトリの外にビルドする。
CONVERTER_DIR="${PLATEAU_GIS_CONVERTER_DIR:-$HOME/dev/tools/PLATEAU-GIS-Converter}"
if [[ -n "${NUSAMAI:-}" ]]; then
  : # 明示指定を優先
elif [[ -x "$CONVERTER_DIR/target/release/nusamai" ]]; then
  NUSAMAI="$CONVERTER_DIR/target/release/nusamai"
elif command -v nusamai >/dev/null 2>&1; then
  NUSAMAI="$(command -v nusamai)"
else
  NUSAMAI="$CONVERTER_DIR/target/release/nusamai"
fi

UDX="data/plateau/meguro/udx"
OUT="data/plateau"

# 変換対象メッシュ（中目黒駅周辺の2x2）。目黒区全域ではない。
MESHES=(53393565 53393566 53393575 53393576)

if [[ ! -x "$NUSAMAI" ]]; then
  echo "nusamai が見つかりません: $NUSAMAI" >&2
  echo "docs/data-prep.md の「CLIの準備」を実行するか、" >&2
  echo "NUSAMAI / PLATEAU_GIS_CONVERTER_DIR で場所を指定してください。" >&2
  exit 1
fi
echo "converter: $NUSAMAI"

BLDG_GML=()
TRAN_GML=()
for mesh in "${MESHES[@]}"; do
  BLDG_GML+=("$UDX/bldg/${mesh}_bldg_6697_op.gml")
  TRAN_GML+=("$UDX/tran/${mesh}_tran_6697_op.gml")
done

for gml in "${BLDG_GML[@]}" "${TRAN_GML[@]}"; do
  [[ -f "$gml" ]] || { echo "入力が見つかりません: $gml" >&2; exit 1; }
done

# geojson sink は --output をディレクトリとして扱い、地物型ごとに
# <型名>.geojson を書き出す（Building.geojson / Road.geojson）。
# 単一ファイルで欲しいので、いったん作業ディレクトリに出して移動する。
WORK="$OUT/_work"
rm -rf "$WORK"
mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT

# 1. bldg -> 3D Tiles
echo "==> 3D Tiles: $OUT/tiles_bldg"
rm -rf "$OUT/tiles_bldg"
"$NUSAMAI" --sink 3dtiles --output "$OUT/tiles_bldg" "${BLDG_GML[@]}"

# 2. bldg -> GeoJSON
echo "==> GeoJSON: $OUT/bldg.geojson"
"$NUSAMAI" --sink geojson --output "$WORK/bldg" "${BLDG_GML[@]}"
mv "$WORK/bldg/Building.geojson" "$OUT/bldg.geojson"

# 3. tran -> GeoJSON
echo "==> GeoJSON: $OUT/tran.geojson"
"$NUSAMAI" --sink geojson --output "$WORK/tran" "${TRAN_GML[@]}"
mv "$WORK/tran/Road.geojson" "$OUT/tran.geojson"

# 検証: tileset.json があるか、bldg.geojson に高さ属性が入っているか
echo "==> 検証"
test -f "$OUT/tiles_bldg/tileset.json" || { echo "tileset.json がありません" >&2; exit 1; }
python3 - "$OUT" <<'PY'
import json, sys
out = sys.argv[1]

# PLATEAU の番兵値: measuredHeight=-9999, storeysAboveGround=9999 が「不明」。
# 詳細と日影計算での扱いは docs/data-prep.md を参照。
SENTINEL_HEIGHT = -9000
MAX_PLAUSIBLE_STOREYS = 100

t = json.load(open(f"{out}/tiles_bldg/tileset.json"))
print(f"  tileset.json: asset={t['asset']} children={len(t['root'].get('children', []))}")

feats = json.load(open(f"{out}/bldg.geojson"))["features"]
missing = measured = estimated = dropped = 0
valid = []
for f in feats:
    p = f["properties"]
    h = p.get("measuredHeight")
    if h is None:
        missing += 1
    elif h > SENTINEL_HEIGHT:
        measured += 1
        valid.append(h)
    else:
        s = p.get("storeysAboveGround")
        if isinstance(s, int) and 1 <= s <= MAX_PLAUSIBLE_STOREYS:
            estimated += 1
        else:
            dropped += 1

print(f"  bldg.geojson: {len(feats)} features "
      f"(measuredHeight 欠損={missing})")
print(f"                実測={measured} 階数から推定={estimated} 高さ不明で除外={dropped}")
print(f"                実測値 min={min(valid):.1f} max={max(valid):.1f} "
      f"mean={sum(valid) / len(valid):.1f}")
if missing:
    raise SystemExit("measuredHeight キー自体が無い地物があります")

print(f"  tran.geojson: {len(json.load(open(f'{out}/tran.geojson'))['features'])} features")
PY

echo "完了。data/ は .gitignore 済みなのでコミットされない。"
