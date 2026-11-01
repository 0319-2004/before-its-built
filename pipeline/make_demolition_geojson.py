"""解体される建物を PLATEAU の中から特定する（段階2）。

解体工事等標識設置届の名簿から「どこで・どんな建物が壊されるか」を取り、
その街区の中の候補を、**階数・床面積・耐火性能・街区からの距離**で絞る。

    python pipeline/make_demolition_geojson.py 中目黒3-5-8

出力は **`web/public/demolition.json`**（gml_id と、なぜそう判断したか）。
表示側は gml_id で公式3D Tiles の建物を塗り替える（dev-policy §15）。

**名簿には工事業者の名称と住所が載っているが、取らない。**
読むのは 種類・場所・構造・階数・床面積・工期だけ（dev-policy §4）。

決めきれないときは**1棟に決めない**。dev-policy §2 のとおり、
候補を複数のまま返し、表示側で薄く並べる。
`confident` が false のときは「この1棟」と言ってはいけない。
"""
import json
import math
import re
import sys
from pathlib import Path

import pypdfium2 as pdfium

sys.path.insert(0, str(Path(__file__).parent))
import normalize
from make_planned_geojson import (
    BLDG_GEOJSON, SOURCE_URL, load_block_points, meters_per_degree,
)

KAITAI_DIR = Path("data/meguro/kaitai")
OUT = Path("web/public/demolition.json")

SOURCE = "目黒区 解体工事等標識設置届出名簿"
LICENSE = "CC BY 4.0"

# 名簿1行の形。**業者名と住所より後ろだけ**を取る。
ROW = re.compile(
    r"(解体・特定|特定建設|解体)\s*(\S+?)\s+(\S*?)\s*"
    r"地上\s*(\d*)\s*階\s*地下\s*(\d*)\s*階\s*([\d.]*)\s*㎡\s*(\S+)\s+(\S+)(.*)$"
)

# 「特定建設」だけの行は解体ではない（新築や改修の工事）。除外する（dev-policy §6）。
DEMOLITION_KINDS = {"解体", "解体・特定"}

# 構造の記号 → 耐火性能。SRC造・RC造は耐火、木造は準耐火以下。
# PLATEAU の fireproofStructureType と突き合わせて候補をふるう。
FIREPROOF_EXPECTED = {"SRC": "耐火", "RC": "耐火", "S": "耐火"}

# 街区の軸からこの距離までを「その街区の中」とみなす[m]
BLOCK_REACH_M = 60.0

# 1階あたりの高さの範囲[m]。PLATEAU の階数は当てにならないので、
# 高さからも階数を見積もってどちらかが合えばよいことにする。
FLOOR_HEIGHT_RANGE = (2.6, 4.6)


def read_notices() -> list[dict]:
    """解体の名簿を読む。業者の名称と住所は取らない。"""
    notices = []
    for pdf_path in sorted(KAITAI_DIR.glob("*.pdf")):
        pdf = pdfium.PdfDocument(pdf_path)
        for page in range(len(pdf)):
            for line in pdf[page].get_textpage().get_text_range().splitlines():
                m = ROW.search(line)
                if not m:
                    continue
                kind, place, structure, above, below, area, start, end, note = m.groups()
                if kind not in DEMOLITION_KINDS:
                    continue
                notices.append({
                    "kind": kind,
                    "place": place,
                    "structure": structure or None,
                    "floors_above": int(above) if above else None,
                    "floors_below": int(below) if below else None,
                    "total_floor_area_m2": float(area) if area else None,
                    "work_start": start,
                    "work_end": end,
                    "note": note.strip() or None,
                    "source_pdf": pdf_path.name,
                })
    return notices


def parse_place(place: str) -> tuple[str | None, int | None, int | None]:
    """「中目黒3-5-8」→（中目黒三丁目, 5, 8）。"""
    m = re.match(r"^(.+?)(\d+)-(\d+)(?:-(\d+))?$", place)
    if not m:
        return None, None, None
    chome = normalize.chome(f"{m.group(1)}{m.group(2)}丁目")
    return chome, int(m.group(3)), int(m.group(4)) if m.group(4) else None


def footprint_area(coords, m_lat: float, m_lon: float) -> float:
    """底面（最も低いZの面）の面積[m2]。LOD1なので建築面積に相当する。"""
    polygons = []
    stack = [coords]
    while stack:
        item = stack.pop()
        if isinstance(item[0][0], (int, float)):
            polygons.append(item)
        else:
            stack.extend(item)
    z_min = min(p[2] for poly in polygons for p in poly)
    best = 0.0
    for poly in polygons:
        if max(p[2] for p in poly) - z_min > 0.5:
            continue
        area = 0.0
        for i in range(len(poly) - 1):
            area += (poly[i][0] * m_lon * poly[i + 1][1] * m_lat
                     - poly[i + 1][0] * m_lon * poly[i][1] * m_lat)
        best = max(best, abs(area) / 2)
    return best


def block_axis(points: dict, chome: str, block: int):
    """街区の点（代表点と、あれば他の点）から、街区の軸になる2点を返す。"""
    found = [v for (c, b), v in points.items() if c == chome and b == block]
    if not found:
        return None
    return (found[0], found[-1])


def distance_to_axis(axis, lat: float, lon: float, m_lat: float, m_lon: float) -> float:
    (alat, alon), (blat, blon) = axis
    ax, ay, bx, by = alon * m_lon, alat * m_lat, blon * m_lon, blat * m_lat
    px, py = lon * m_lon, lat * m_lat
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def score(notice: dict, building: dict) -> tuple[float, list[str]]:
    """候補らしさ。小さいほど良い。理由も返す。"""
    penalty = 0.0
    reasons = []

    want_floors = notice["floors_above"]
    total = notice["total_floor_area_m2"]
    area = building["footprint_m2"]
    height = building["height_m"]
    floors = building["floors"]

    # 階数。PLATEAU の階数は欠測や明らかな誤りがあるので、
    # 高さから見積もった階数とどちらか近いほうを採る。
    if want_floors:
        candidates = []
        if floors:
            candidates.append(abs(floors - want_floors))
        if height:
            lo = height / FLOOR_HEIGHT_RANGE[1]
            hi = height / FLOOR_HEIGHT_RANGE[0]
            candidates.append(0.0 if lo <= want_floors <= hi
                              else min(abs(want_floors - lo), abs(want_floors - hi)))
        diff = min(candidates) if candidates else 3.0
        penalty += diff * 2.0
        reasons.append(f"階数のずれ {diff:.1f}")

    # 床面積。延べ面積 ÷ 階数 ≒ 建築面積 として底面と比べる。
    if total and want_floors and area:
        expected = total / want_floors
        ratio = abs(area - expected) / expected
        penalty += ratio * 10.0
        reasons.append(f"底面 {area:.0f}㎡ / 期待 {expected:.0f}㎡（{ratio*100:.0f}%ずれ）")

    # 街区の軸からの距離
    penalty += building["block_distance_m"] / 20.0
    reasons.append(f"街区の軸から {building['block_distance_m']:.0f}m")

    # 耐火性能。SRC造・RC造なら耐火のはず。準耐火なら強く下げる。
    want = FIREPROOF_EXPECTED.get((notice["structure"] or "").upper())
    got = building["fireproof"]
    if want and got:
        # 「準耐火」は「耐火」を含むので、単純な部分一致で見てはいけない。
        got_is_fireproof = got.startswith("耐火")
        if (want == "耐火") != got_is_fireproof:
            penalty += 6.0
            reasons.append(f"耐火性能が合わない（{notice['structure']}造なら{want}、"
                           f"PLATEAUは{got}）")
        else:
            reasons.append(f"耐火性能は合う（{got}）")

    return penalty, reasons


def find_candidates(notice: dict, points: dict, features: list, top: int = 5) -> dict:
    chome, block, go = parse_place(notice["place"])
    if chome is None:
        return {"error": f"場所を読めない: {notice['place']}"}
    axis = block_axis(points, chome, block)
    if axis is None:
        return {"error": f"街区の点が見つからない: {chome}{block}番"}

    m_lat, m_lon = meters_per_degree(axis[0][0])
    scored = []
    for feature in features:
        p = feature["properties"]
        lats, lons = [], []
        stack = [feature["geometry"]["coordinates"]]
        while stack:
            item = stack.pop()
            if isinstance(item[0], (int, float)):
                lons.append(item[0])
                lats.append(item[1])
            else:
                stack.extend(item)
        lat, lon = sum(lats) / len(lats), sum(lons) / len(lons)
        dist = distance_to_axis(axis, lat, lon, m_lat, m_lon)
        if dist > BLOCK_REACH_M:
            continue

        height = p.get("measuredHeight")
        height = height if isinstance(height, (int, float)) and height > 0 else None
        floors = p.get("storeysAboveGround")
        floors = floors if isinstance(floors, int) and 0 < floors < 100 else None
        detail = (json.loads(p.get("buildingDetailAttribute") or "[]") or [{}])[0]

        building = {
            "gml_id": p["id"],
            "height_m": height,
            "floors": floors,
            "footprint_m2": round(footprint_area(
                feature["geometry"]["coordinates"], m_lat, m_lon), 1),
            "usage": p.get("usage"),
            "fireproof": detail.get("fireproofStructureType"),
            "block_distance_m": round(dist, 1),
        }
        penalty, reasons = score(notice, building)
        building["score"] = round(penalty, 2)
        building["reasons"] = reasons
        scored.append((penalty, building))

    scored.sort(key=lambda x: x[0])
    best = [b for _, b in scored[:top]]

    # 2位と十分に差がついていて、かつ1位自身がよく合っているときだけ「決まった」とする
    confident = bool(
        len(best) >= 2
        and best[0]["score"] < 3.0
        and best[1]["score"] - best[0]["score"] >= 2.0
    )
    return {
        "chome": chome, "block": block, "go": go,
        "candidates": best,
        "confident": confident,
    }


def main():
    wanted = sys.argv[1] if len(sys.argv) > 1 else "中目黒3-5-8"

    notices = read_notices()
    print(f"解体の届出 {len(notices)}件（「特定建設」だけの行は除いた）")

    matched = [n for n in notices if n["place"] == wanted]
    if not matched:
        print(f"{wanted} の解体の届出が見つかりません")
        raise SystemExit(1)
    notice = matched[0]
    print(f"\n{wanted}: {notice['structure']}造 地上{notice['floors_above']}階 "
          f"延べ{notice['total_floor_area_m2']}㎡ "
          f"（{notice['work_start']}〜{notice['work_end']}）")

    points = load_block_points()
    print(f"{BLDG_GEOJSON} を読み込み中…")
    features = json.loads(BLDG_GEOJSON.read_text(encoding="utf-8"))["features"]

    result = find_candidates(notice, points, features)
    if "error" in result:
        print(result["error"])
        raise SystemExit(1)

    print(f"\n候補（小さいほど良い）:")
    for i, b in enumerate(result["candidates"], 1):
        print(f"  {i}. score {b['score']:5.2f}  {b['gml_id']}")
        print(f"     {b['floors']}階 / 高さ{b['height_m']}m / 底面{b['footprint_m2']}㎡ "
              f"/ {b['fireproof']} / {b['usage']}")
        for r in b["reasons"]:
            print(f"     - {r}")

    print(f"\n1棟に決まったか: {'はい' if result['confident'] else 'いいえ（候補のまま出す）'}")

    out = {
        "generated": __import__("datetime").date.today().isoformat(),
        "place": wanted,
        "notice": {k: notice[k] for k in
                   ("kind", "structure", "floors_above", "floors_below",
                    "total_floor_area_m2", "work_start", "work_end")},
        "chome": result["chome"], "block": result["block"], "go": result["go"],
        "confident": result["confident"],
        "candidates": result["candidates"],
        "note": ("解体される建物をPLATEAUの中から絞ったもの。"
                 "住居表示の「号」を座標にする手段がないため、街区の中の候補を"
                 "階数・床面積・耐火性能・距離で順位付けしている。"
                 "confident が false のときは1棟に決まっていない。"),
        "sources": [
            f"{SOURCE}（目黒区オープンデータ {LICENSE}） {SOURCE_URL}",
            "3D都市モデル（Project PLATEAU）目黒区（2025年度）/ 国土交通省 を加工して作成",
        ],
    }

    # 業者の名称・住所が混ざっていないことを確かめる（この経路では取っていないが、
    # 名簿を読み直す作りに変えたときのための歯止め）。
    text = json.dumps(out, ensure_ascii=False)
    for banned in ("株式会社", "（株）", "(株)", "有限会社", "（有）", "(有)"):
        if banned in text:
            print(f"\n業者の名称らしきもの（{banned}）が混ざっています。書き出しを中止します。")
            raise SystemExit(1)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n書き出しました → {OUT}")


if __name__ == "__main__":
    main()
