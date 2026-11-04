"""遠い敷地写真から「建物あり／更地／工事中」を判定し、PLATEAUと突き合わせる。

dev-policy §14 の次タスク：
「遠い写真で敷地が建物あり／更地／工事中かを Muse Spark に判定させ、エージェントのツールにする
（PLATEAUに建物があるのに更地なら『PLATEAU未反映』と示す）」。

PLATEAU（目黒区）の建物の調査年は **2021年**（dev-policy §6）。それ以降に解体された建物は
3D都市モデルにまだ残っているので、「PLATEAUには建物がある／現地は更地・工事中」のずれは
**『3D都市モデルは数年遅れる』の実例**になる（dev-policy §15・判断の記録）。

    # ペアリング（pair_photos.py の出力）の遠い写真に判定を回す
    python pipeline/site_state.py --test data/photos_pairs.json

**読むのはモデル、判定材料が乏しければ unknown**（dev-policy §4 値を作らせない）。
出力は `data/site_state.json`（**公開しない**。写真名・GPSが入る）。
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import make_planned_geojson as volume
import normalize
import read_board
from pair_photos import read_exif

OUT = Path("data/site_state.json")
AGENT_DIR = Path("data/agent")
PHOTO_DIR = Path("data/photos/2026-11-03")

# 敷地の現況。値を作らせず、分からなければ unknown。
STATES = {
    "has_building": "既存の建物が建っている",
    "vacant": "更地（建物がない。駐車場・空き地・造成中の土地を含む）",
    "under_construction": "工事中（仮囲い・足場・重機・掘削・基礎などが見える）",
    "unknown": "写真からは判断できない",
}

# PLATEAU に「この敷地の建物がある」とみなす半径[m]。街区の代表点や
# カメラのGPSには誤差があるので、建物の有無を見るには少し広めに取る。
PLATEAU_RADIUS_M = 25.0

PROMPT = f"""これは日本の街で、ある敷地（これから建物が建つ、または建った工事現場）を
少し離れて撮った写真です。その敷地が今どの状態かを判定してください。

状態は次の4つから1つ選びます:
{json.dumps(STATES, ensure_ascii=False, indent=2)}

見分け方の目安:
- 工事中: 工事用の仮囲い（白や緑のフェンス）、足場、クレーンや重機、掘削、基礎、
  「建築計画のお知らせ」などの看板が立っている更地。
- 更地: 建物がなく、土や砂利、駐車場、空き地になっている。
- 建物あり: 既存の建物がそのまま建っている。

JSONだけを返してください。前置きやコードブロックは付けないでください。
推測で決めない。どの敷地を指しているか分からない、または写真が不鮮明で判断できないときは
"unknown" にしてください。**ほかの手がかりから「たぶんこうだろう」と埋めないこと。**

返す形:
{{
  "state": "has_building" | "vacant" | "under_construction" | "unknown",
  "confidence": 0.0〜1.0,
  "signals": [写真から読み取れた手がかりの短い語。例: "仮囲い", "重機", "更地", "既存建物"],
  "note": 短い説明またはnull
}}

建築主・設計者・施工者・連絡先の氏名、住所、電話番号、看板に写った個人名は一切出力しない。
"""


def judge_site(image_path: Path, effort: str = "low") -> dict:
    """遠い敷地写真を Muse Spark 1.3 で判定する（read_board と同じクライアント・モデル）。"""
    raw = read_board.ask(image_path, PROMPT, effort)
    state = raw.get("state")
    if state not in STATES:
        state = "unknown"
    return {
        "state": state,
        "state_label": STATES[state],
        "confidence": raw.get("confidence"),
        "signals": raw.get("signals") or [],
        "note": raw.get("note"),
    }


# ---------- PLATEAU 照合 ----------

def count_nearby_buildings(index: "volume.GroundIndex", lat: float, lon: float,
                           radius_m: float = PLATEAU_RADIUS_M) -> int:
    """点から半径内にある既存建物の棟数（索引は1棟につき底面1点）。"""
    m_lat, m_lon = volume.meters_per_degree(lat)
    reach = int(radius_m / (volume.CELL_DEG * m_lat)) + 1
    base = (int(lat / volume.CELL_DEG), int(lon / volume.CELL_DEG))
    n = 0
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            for blat, blon, _ in index.cells.get((base[0] + dy, base[1] + dx), ()):
                if math.hypot((blat - lat) * m_lat, (blon - lon) * m_lon) <= radius_m:
                    n += 1
    return n


def plateau_state(index: "volume.GroundIndex", lat: float, lon: float,
                  radius_m: float = PLATEAU_RADIUS_M) -> dict:
    """PLATEAU（2021年）がこの地点に建物を持っているか。"""
    if not index.covers(lat, lon):
        return {"has_building": None, "nearby_count": 0, "radius_m": radius_m,
                "note": "bldg.geojson の範囲外（撮影ルートの4メッシュのみ）"}
    n = count_nearby_buildings(index, lat, lon, radius_m)
    return {"has_building": n > 0, "nearby_count": n, "radius_m": radius_m,
            "note": f"半径{radius_m:.0f}m内に既存建物{n}棟（調査年2021）"}


def reconcile(photo: dict, plateau: dict) -> dict:
    """現況（写真）とPLATEAU（2021）のずれを判定する。"""
    state = photo["state"]
    has = plateau.get("has_building")
    if has is None:
        return {"verdict": "unknown", "note": "PLATEAUの範囲外で照合できない"}
    if state == "unknown":
        return {"verdict": "unknown", "note": "現況を判定できなかった"}
    if has and state in ("vacant", "under_construction"):
        return {"verdict": "plateau_outdated",
                "note": "PLATEAU（2021）には建物があるが、現地は"
                        f"{STATES[state]}。3D都市モデルが未反映（数年遅れ）の実例"}
    if has and state == "has_building":
        return {"verdict": "consistent",
                "note": "PLATEAU・現地とも建物あり（まだ建っている）"}
    if not has and state in ("has_building", "under_construction"):
        return {"verdict": "plateau_missing",
                "note": "PLATEAUに建物がないが、現地には建物・工事がある"
                        "（新しい建物か、範囲の端）"}
    return {"verdict": "consistent",
            "note": "PLATEAU・現地とも建物なし"}


# ---------- テストハーネス（ペアリングの遠い写真に回す） ----------

def near_point(block_points: dict, photo_stem: str) -> tuple | None:
    """近い写真の住居表示（町丁目・街区番号）→ 街区の代表点。"""
    path = AGENT_DIR / f"{photo_stem}.json"
    if not path.exists():
        return None
    rec = json.loads(path.read_text(encoding="utf-8"))
    addr = (rec.get("reading") or {}).get("address") or {}
    chome = normalize.chome(addr.get("chome"))
    block = normalize.block_number(addr.get("block"))
    if chome is None or block is None:
        return None
    return block_points.get((chome, block))


def locate(cluster: dict, block_points: dict, exif: dict) -> tuple:
    """このクラスタのPLATEAU照合に使う点を決める。

    優先: 近い写真の街区代表点（位置の根拠が強い） → 遠い写真のGPS → 近い写真のGPS。
    """
    for near in cluster.get("near") or []:
        pt = near_point(block_points, near)
        if pt:
            return pt[0], pt[1], f"{near} の街区代表点"
    for group in ("far", "near"):
        for name in cluster.get(group) or []:
            ex = exif.get(name)
            if ex and ex["lat"] is not None:
                return ex["lat"], ex["lon"], f"{name} の撮影GPS"
    return None, None, None


def run_test(pairs_path: Path) -> list[dict]:
    clusters = json.loads(pairs_path.read_text(encoding="utf-8"))
    block_points = volume.load_block_points()
    print("bldg.geojson を読み込み中…", flush=True)
    index = volume.load_ground_index()

    exif = {}
    for name in {m["photo"] for c in clusters for m in c["members"]}:
        p = PHOTO_DIR / f"{name}.JPG"
        exif[name] = read_exif(p) if p.exists() else None

    results = []
    for c in clusters:
        far = c.get("far") or []
        if not far:
            continue  # 遠い写真がないクラスタは判定対象外
        lat, lon, loc_src = locate(c, block_points, exif)
        plateau = (plateau_state(index, lat, lon) if lat is not None
                   else {"has_building": None, "nearby_count": 0, "note": "位置が取れない"})
        for name in far:
            photo = judge_site(PHOTO_DIR / f"{name}.JPG")
            verdict = reconcile(photo, plateau)
            entry = {
                "cluster": c["cluster"], "site": c["site"],
                "far_photo": name, "near_photos": c.get("near") or [],
                "location_source": loc_src,
                "photo_state": photo, "plateau": plateau, "verdict": verdict,
            }
            results.append(entry)
            print(f"\nクラスタ{c['cluster']} {c['site'] or '（地点不明）'} / 遠い写真 {name}")
            print(f"  現況: {photo['state']}（{photo['state_label']}）"
                  f" 確信度 {photo['confidence']} 手がかり {photo['signals']}")
            print(f"  PLATEAU: {plateau['note']}（位置: {loc_src}）")
            print(f"  → 判定: {verdict['verdict']} — {verdict['note']}")

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n書き出し → {OUT}（公開しない）")
    return results


def main():
    args = sys.argv[1:]
    if "--test" in args:
        pairs = next((Path(a) for a in args if not a.startswith("-")),
                     Path("data/photos_pairs.json"))
        run_test(pairs)
    else:
        # 1枚だけ判定（位置が分かればPLATEAUも照合）
        image = Path(args[0])
        photo = judge_site(image)
        print(json.dumps(photo, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
