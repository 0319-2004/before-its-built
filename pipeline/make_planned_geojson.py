"""届出から「これから建つ建物」のボリュームを GeoJSON にする（段階1）。

位置は街区レベル位置参照情報の代表点、形は建築面積と同じ面積の正方形（北向き）、
高さは「3D表示に使う高さ」（最高の高さがあればそれ、なければ高さ）。
底面のZは楕円体高で、近くの既存建物の底面から取る。

    python pipeline/make_planned_geojson.py

出力は `data/planned/planned.geojson`。
**届出はまだ目視確認が済んでいないので、全件に「確認前」の印が付く。**
`filings_extracted.csv` の `verified` に 1 が入った行だけ `confirmed: true` になる。

建築物の名称は入れない（「○○様邸」のように個人名が入るため。dev-policy §4）。

近似であることの断り:
  - 形は正方形で、**北向き**。dev-policy §5 の「最寄りの道路に沿わせる」はまだ。
  - 位置は街区の代表点なので、街区の中のどこに建つかは分からない。
  - 高さは看板・届出の数値で、PLATEAU の既存建物とは測り方が違うことがある。
"""
import csv
import json
import math
import sys
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import normalize

FILINGS_CSV = Path("data/meguro/filings_extracted.csv")
BLDG_GEOJSON = Path("data/PLATEAU/bldg.geojson")
OUT = Path("data/planned/planned.geojson")

# 街区レベル位置参照情報（目黒区、2025年版）。zip のまま置いてあるので直接読む。
# CP932。代表フラグ=1 の点だけを使う（dev-policy §6）。
ISJ_ZIP = Path("data/meguro/googlefieldwork/13110-24.0a.zip")
ISJ_MEMBER = "13110-24.0a/13110_2025.csv"

# 地面のZを取るために近くの既存建物を探す半径[m]。
# 近い順に広げ、最初に GROUND_MIN_SAMPLES 棟そろった半径で決める。
GROUND_RADII_M = (60.0, 120.0, 250.0, 500.0)
GROUND_MIN_SAMPLES = 3

# 近傍探索用の格子の大きさ[度]。緯度方向で約110m。
CELL_DEG = 0.001


def meters_per_degree(lat_deg: float) -> tuple[float, float]:
    """その緯度での1度あたりのメートル数（緯度方向, 経度方向）。"""
    phi = math.radians(lat_deg)
    m_lat = (111132.92 - 559.82 * math.cos(2 * phi)
             + 1.175 * math.cos(4 * phi) - 0.0023 * math.cos(6 * phi))
    m_lon = (111412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi)
             + 0.118 * math.cos(5 * phi))
    return m_lat, m_lon


def load_block_points() -> dict[tuple[str, int], tuple[float, float]]:
    """(町丁目, 街区番号) → 代表点の (緯度, 経度)。

    町丁目は `normalize.chome` を通した形で持つ。届出側の「下目黒2丁目」と
    位置参照情報側の「下目黒二丁目」をそろえるため（dev-policy §4）。
    """
    with zipfile.ZipFile(ISJ_ZIP) as z, z.open(ISJ_MEMBER) as f:
        rows = list(csv.DictReader(line.decode("cp932") for line in f))

    points = {}
    for row in rows:
        if row["代表フラグ"] != "1":
            continue
        chome = normalize.chome(row["大字・丁目名"])
        block = normalize.block_number(row["街区符号・地番"])
        if chome is None or block is None:
            continue
        points.setdefault((chome, block), (float(row["緯度"]), float(row["経度"])))
    return points


class GroundIndex:
    """既存建物の底面を格子に入れたもの。セル → [(緯度, 経度, 底面Z), …]。

    Zは `bldg.geojson` のものをそのまま使う（**楕円体高**）。
    b3dm の `_zmin` は標高なので使わない。目黒区では約36.7m低く出る（dev-policy §15）。

    `bldg.geojson` は目黒区の全域ではなく**撮影ルートの4メッシュだけ**なので
    （dev-policy §6）、範囲の外かどうかを区別できるように bbox も持つ。
    """

    def __init__(self, cells, bbox):
        self.cells = cells
        self.bbox = bbox  # (lat_min, lat_max, lon_min, lon_max)
        self.count = sum(len(v) for v in cells.values())

    def covers(self, lat: float, lon: float) -> bool:
        lat_min, lat_max, lon_min, lon_max = self.bbox
        return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def load_ground_index() -> GroundIndex:
    data = json.loads(BLDG_GEOJSON.read_text(encoding="utf-8"))
    index: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
    for feature in data["features"]:
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates")
        if not coords:
            continue
        # MultiPolygon の全頂点から、最も低いZ（底面）とその位置を取る
        lowest = None
        stack = [coords]
        while stack:
            item = stack.pop()
            if isinstance(item[0], (int, float)):
                if len(item) >= 3 and (lowest is None or item[2] < lowest[2]):
                    lowest = (item[1], item[0], item[2])
            else:
                stack.extend(item)
        if lowest is None:
            continue
        cell = (int(lowest[0] / CELL_DEG), int(lowest[1] / CELL_DEG))
        index.setdefault(cell, []).append(lowest)

    lats = [p[0] for cell in index.values() for p in cell]
    lons = [p[1] for cell in index.values() for p in cell]
    return GroundIndex(index, (min(lats), max(lats), min(lons), max(lons)))


def ground_z(index: GroundIndex, lat: float, lon: float) -> tuple[float | None, int, float | None]:
    """近くの既存建物の底面Zの中央値を返す（楕円体高）。(Z, 使った棟数, 半径)。"""
    m_lat, m_lon = meters_per_degree(lat)
    nearby = []
    reach = int(max(GROUND_RADII_M) / (CELL_DEG * m_lat)) + 1
    base = (int(lat / CELL_DEG), int(lon / CELL_DEG))
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            for blat, blon, bz in index.cells.get((base[0] + dy, base[1] + dx), ()):
                d = math.hypot((blat - lat) * m_lat, (blon - lon) * m_lon)
                nearby.append((d, bz))
    nearby.sort()

    for radius in GROUND_RADII_M:
        within = [z for d, z in nearby if d <= radius]
        if len(within) >= GROUND_MIN_SAMPLES:
            within.sort()
            mid = len(within) // 2
            median = (within[mid] if len(within) % 2
                      else (within[mid - 1] + within[mid]) / 2)
            return round(median, 2), len(within), radius
    return None, 0, None


def square_footprint(lat: float, lon: float, area_m2: float) -> list[list[float]]:
    """代表点を中心に、面積が `area_m2` と同じ北向きの正方形を置く。"""
    half = math.sqrt(area_m2) / 2
    m_lat, m_lon = meters_per_degree(lat)
    dlat, dlon = half / m_lat, half / m_lon
    south, north = round(lat - dlat, 8), round(lat + dlat, 8)
    west, east = round(lon - dlon, 8), round(lon + dlon, 8)
    # 外周は反時計回り（右手の法則）。最初の点で閉じる。
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


def number(row: dict, column: str) -> float | None:
    value = (row.get(column) or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def integer(row: dict, column: str) -> int | None:
    value = number(row, column)
    return int(value) if value is not None and value == int(value) else None


def text(row: dict, column: str) -> str | None:
    return (row.get(column) or "").strip() or None


def display_height(row: dict) -> tuple[float | None, str | None]:
    """3D表示に使う高さ。最高の高さがあればそれ、なければ高さ（dev-policy §4）。"""
    for column in ("max_height_m", "height_m"):
        value = number(row, column)
        if value is not None:
            return value, column
    return None, None


def build_feature(row: dict, points, index) -> tuple[dict | None, str | None]:
    """届出1件を Feature にする。作れなければ (None, 理由)。"""
    chome = normalize.chome(text(row, "address_chome"))
    block = normalize.block_number(text(row, "address_block"))
    if chome is None or block is None:
        return None, "住居表示の町丁目か街区番号がない"

    point = points.get((chome, block))
    if point is None:
        return None, f"街区の代表点が見つからない（{chome}{block}番）"
    lat, lon = point

    area = number(row, "building_area_m2")
    if area is None or area <= 0:
        return None, "建築面積がない"

    height, height_source = display_height(row)
    if height is None or height <= 0:
        return None, "高さがない"

    # 地面のZは近くの既存建物の底面から取る。半径の外に建物がなければ作れない。
    # bldg.geojson は撮影ルートの4メッシュだけなので（dev-policy §6）、
    # 区の南西側はまるごとここで落ちる。広げるには convert_plateau.sh でメッシュを足す。
    base_z, samples, radius = ground_z(index, lat, lon)
    if base_z is None:
        where = "の範囲外" if not index.covers(lat, lon) else "に近くの既存建物がない"
        return None, f"bldg.geojson {where}（{chome}{block}番）"

    confirmed = text(row, "verified") == "1"
    uncertain = text(row, "uncertain")

    return {
        "type": "Feature",
        "properties": {
            # 建築物の名称は入れない（個人名が入りうる。dev-policy §4）
            "filing_no": integer(row, "filing_no"),
            "chome": chome,
            "block": block,
            "use": text(row, "use"),
            "work_type": text(row, "work_type"),
            "structure": normalize.structure(text(row, "structure")),
            "floors_above": integer(row, "floors_above"),
            "floors_below": integer(row, "floors_below"),
            "units_total": integer(row, "units_total"),
            "site_area_m2": number(row, "site_area_m2"),
            "building_area_m2": area,
            "total_floor_area_m2": number(row, "total_floor_area_m2"),
            "height_m": number(row, "height_m"),
            "max_height_m": number(row, "max_height_m"),
            "eave_height_m": number(row, "eave_height_m"),
            # 3Dに使う高さと、それがどの欄から来たか
            "display_height_m": height,
            "display_height_source": height_source,
            "construction_start": text(row, "construction_start"),
            "construction_end": text(row, "construction_end"),
            "sign_posted": text(row, "sign_posted"),
            # 底面の楕円体高と、その根拠
            "base_z_m": base_z,
            "base_z_samples": samples,
            "base_z_radius_m": radius,
            # 近くに建物がなく遠くから取った地面は当てにならない（目黒区は起伏がある）
            "base_z_rough": radius > GROUND_RADII_M[1],
            # 近似であることと、まだ確認前であることの印
            "footprint": "square_north",
            "position": "block_representative_point",
            "confirmed": confirmed,
            "status_label": "確認前" if not confirmed else "確認済み",
            "uncertain_fields": uncertain,
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [square_footprint(lat, lon, area)],
        },
    }, None


def main():
    with FILINGS_CSV.open(encoding="utf-8-sig") as f:
        filings = list(csv.DictReader(f))
    print(f"届出 {len(filings)}件")

    points = load_block_points()
    print(f"街区の代表点 {len(points)}件（街区レベル位置参照情報）")

    print(f"{BLDG_GEOJSON} を読み込み中…")
    index = load_ground_index()
    lat_min, lat_max, lon_min, lon_max = index.bbox
    print(f"既存建物 {index.count}棟の底面を格子に入れた"
          f"（範囲 緯度 {lat_min:.4f}〜{lat_max:.4f} / 経度 {lon_min:.4f}〜{lon_max:.4f}）")

    features, skipped = [], []
    for row in filings:
        feature, reason = build_feature(row, points, index)
        if feature is None:
            skipped.append((row.get("filing_no"), reason))
        else:
            features.append(feature)

    confirmed = sum(1 for f in features if f["properties"]["confirmed"])
    collection = {
        "type": "FeatureCollection",
        # 画面に出す断りと出典をデータ自身に持たせる
        "metadata": {
            "generated": date.today().isoformat(),
            "count": len(features),
            "confirmed": confirmed,
            "unconfirmed": len(features) - confirmed,
            "note": "届出の読み取り結果。目視確認が済んでいない行は「確認前」。"
                    "位置は街区の代表点、形は建築面積と同じ面積の北向きの正方形で、"
                    "いずれも近似。",
            "sources": [
                "目黒区 中高層建築物の標識設置届（目黒区オープンデータ CC BY 4.0）",
                "国土交通省 街区レベル位置参照情報（目黒区、2025年版）",
                "3D都市モデル（Project PLATEAU）目黒区（2025年度）/ 国土交通省 を加工して作成",
            ],
        },
        "features": features,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(collection, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n{len(features)}棟を書き出しました → {OUT}")
    print(f"  確認済み {confirmed}件 / 確認前 {len(features) - confirmed}件")
    if skipped:
        print(f"\n作れなかった届出 {len(skipped)}件:")
        for filing_no, reason in skipped:
            print(f"  No.{filing_no}: {reason}")


if __name__ == "__main__":
    main()
