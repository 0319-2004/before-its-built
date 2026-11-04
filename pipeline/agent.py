"""看板1枚を、Muse Spark 1.3 自身がツールを選んで呼ぶループで処理する（dev-policy §3）。

`process.py` は「切り出し → 読み取り → 検算」を**こちらが決めた順番**で流すだけだった。
ここでは同じ部品をツールとして渡し、**次に何をするかをモデルが決める**。
照合で食い違いが出たときに、どの項目を読み直し、どちらの値を採り、
利用者に何を伝えるかの判断がモデル側に移る。

    python pipeline/agent.py data/photos/2026-11-01/IMG_3928.JPG
    python pipeline/agent.py --all data/photos/2026-11-01

**読むのはモデル、確かめるのはコード**（dev-policy §4）。
モデルは値を比べない。`lookup_filing` が届出を引いたうえで、
項目ごとの一致・不一致を `evaluate.py` と同じ規則（`normalize.py` を通して）で
計算して返す。`extrude` も、渡された高さが軒高でないかをコードで確かめる。

判断の記録は `data/agent/<写真の名前>.json`。呼んだツール、入力、結果、
モデルの短い理由を1手ずつ残す。**`data/` はリポジトリに入れない**。
届出の「建築物の名称」は照合の鍵・判断の根拠として記録には残るが
（「○○様邸」のように個人名が入りうる）、公開する側へはこのファイルからは出さない。
公開用に絞るのは `make_sample_mode.py` の側で行う。
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import crop_board
import evaluate
import make_planned_geojson as volume
import normalize
import read_board
import site_state
from pair_photos import read_exif
from read_board import client

MODEL = "muse-spark-1.3"  # -contributor は使わない（写真に個人名が写る。dev-policy §3）
RECORD_DIR = Path("data/agent")
MAX_TURNS = 14

# 終わり方。request_retake は needs_retake、それ以外は finish で返してもらう。
OUTCOMES = {
    "built": "届出と照合でき、3Dに立てられる",
    "mismatch_notified": "読み直しても残った食い違いを利用者に知らせる",
    "needs_review": "届出と照合できない、または矛盾が残るので要確認",
    "needs_retake": "撮り直しを頼む",
}

INSTRUCTIONS = """あなたは日本の工事現場の「建築計画のお知らせ」の看板を1枚ずつ処理する担当です。
写真は1枚だけ渡されています。下のツールを自分で選んで呼び、最後に finish か
request_retake で終えてください。

進め方の目安（そのとおりでなくてよい。結果を見て判断してください）:
1. read_board で看板を読む
2. check_consistency で数値の矛盾を確かめる
3. 矛盾があれば reread_fields でその項目だけを読み直す
4. lookup_filing で目黒区の届出を引き、看板の読み取りと照合する
5. 位置が要るなら geocode、3Dのボリュームが要るなら extrude
6. finish で結論を出す

守ること:
- **値を自分で作らない。** 数値や文字はツールが返したものだけを使う。
  矛盾や食い違いを消すために、ありそうな値を埋めてはいけない。
- 値を比べるのはコードです。lookup_filing が項目ごとの一致・不一致を返すので、
  自分で数字を見比べて判断しないでください。
- notice_type が "other"（労災保険関係成立票、建設業の許可票など）だったときは、
  ほかの項目を読まずに request_retake で終える。
- 読めない項目（status が "unreadable"）が位置や高さに関わるときは request_retake。
- **確信度が低い項目を、そのまま3Dに使わない。** check_consistency が low_confidence に
  並べた項目は、数値の検算を通っていても桁を補った値かもしれません。
  位置や高さ・面積に関わるものは reread_fields で読み直し、
  それでも確信度が上がらなければ request_retake で撮り直しを頼む。
- **ツールがエラーを返したときは、写真の問題だと決めつけない。**
  エラーの文を読み、同じツールをもう一度呼ぶか、鍵や引数を変えて呼び直す。
  それでも進めないときは、エラーの内容を unresolved に入れて needs_review で終える。
- 届出と食い違った項目があったら、**その項目だけ** reread_fields で読み直す。
  読み直しても食い違うときは、どちらが正しいか決めつけずに、
  地番や建築物の名称といった根拠を示して判断し、残った食い違いを
  finish の unresolved に入れて利用者に知らせる。
  読み間違いではなく、看板と届出で実際に数字が違うこともあります。
- lookup_filing は鍵を変えて何度でも呼べます。地番 → 住居表示 → 建築物の名称 の順が
  当たりやすい（地番は看板の読み間違いに強い）。
- 3D表示に使う高さは max_height_m（なければ height_m）です。eave_height_m（軒高）は
  使いません。
- 遠い敷地写真も渡されているときは、check_site で敷地の現況（建物あり／更地／工事中）を
  確かめられる。PLATEAU の2021年の建物と食い違う（PLATEAUには建物があるのに現地が更地・工事中）
  ときは、その旨を finish の unresolved に入れて利用者に知らせる（3D都市モデルの未反映）。
- 建築主・設計者・施工者・担当者の氏名、住所、電話番号は一切書かない。
- 理由は日本語で短く。

finish の outcome は次の4つから選びます:
{outcomes}
"""

TOOLS = [
    {
        "type": "function",
        "name": "read_board",
        "description": "写真から看板を切り出して読み取る（SAM 3.1 で切り出し、Muse Spark で読む）。"
                       "項目ごとに value / status / confidence を返す。"
                       "写真に看板が複数写っていたときは board で何枚目かを選べる。",
        "parameters": {
            "type": "object",
            "properties": {
                "board": {"type": "integer",
                          "description": "何枚目の看板を読むか（0から。既定は0）"},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "check_consistency",
        "description": "直前に読み取った数値どうしの矛盾をコードで確かめる。"
                       "引っかかった内容と、疑わしい項目の名前を返す。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "reread_fields",
        "description": "指定した項目だけを、推論の強さ medium で読み直す。"
                       "読み直した値で読み取り結果を差し替え、変わった項目を返す。"
                       "矛盾が見つかった項目や、届出と食い違った項目に使う。"
                       "通常の項目のほか、住居表示は \"address\"、地名地番は \"lot\" で読み直せる"
                       "（街区番号などが届出と食い違ったとき）。",
        "parameters": {
            "type": "object",
            "properties": {
                "fields": {"type": "array", "items": {"type": "string"},
                           "description": "読み直す項目名（read_board が返した名前）"},
                "reason": {"type": "string", "description": "読み直す理由。モデルに見せる"},
            },
            "required": ["fields", "reason"],
        },
    },
    {
        "type": "function",
        "name": "lookup_filing",
        "description": "目黒区の標識設置届を鍵で引き、見つかれば直前の読み取りと項目ごとに"
                       "比べた結果を返す（比べ方は normalize.py で表記をそろえたうえでコードが決める）。"
                       "鍵は組み合わせてよく、何度でも呼び直せる。",
        "parameters": {
            "type": "object",
            "properties": {
                "parcel": {"type": "string", "description": "地番（例: \"1760-22\"）"},
                "chome": {"type": "string", "description": "町丁目（例: \"上目黒三丁目\"）"},
                "block": {"type": "integer", "description": "住居表示の街区番号"},
                "building_name": {"type": "string", "description": "建築物の名称"},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "geocode",
        "description": "住居表示の町丁目と街区番号から、街区の代表点の緯度経度を返す"
                       "（街区レベル位置参照情報）。街区の中のどこかは分からない。",
        "parameters": {
            "type": "object",
            "properties": {
                "chome": {"type": "string", "description": "町丁目（例: \"上目黒三丁目\"）"},
                "block": {"type": "integer", "description": "街区番号"},
            },
            "required": ["chome", "block"],
        },
    },
    {
        "type": "function",
        "name": "extrude",
        "description": "建築面積と高さから、3Dに置くボリュームを作る。位置は直前の geocode の結果を使う。"
                       "形は面積が同じ北向きの正方形で、近似。底面の高さは近くの既存建物から取る。",
        "parameters": {
            "type": "object",
            "properties": {
                "building_area_m2": {"type": "number", "description": "建築面積"},
                "height_m": {"type": "number",
                             "description": "3D表示に使う高さ。max_height_m があればそれ、"
                                            "なければ height_m。軒高は使わない"},
            },
            "required": ["building_area_m2", "height_m"],
        },
    },
    {
        "type": "function",
        "name": "check_site",
        "description": "遠い敷地写真から、敷地が今「建物あり／更地／工事中」かを判定し、"
                       "PLATEAU の2021年の建物と突き合わせる。PLATEAU に建物があるのに現地が"
                       "更地・工事中なら『PLATEAU未反映』（3D都市モデルの数年遅れ）と返す。"
                       "遠い敷地写真が渡されているときに使う。位置は geocode 済みなら街区の"
                       "代表点、なければ写真の撮影GPSを使う。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "request_retake",
        "description": "読めない項目がある、または対象外の看板だったときに、撮り直しを頼んで終える。",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "利用者に見せる、撮り直しのお願い"},
            },
            "required": ["reason"],
        },
    },
    {
        "type": "function",
        "name": "finish",
        "description": "結論を出して終える。",
        "parameters": {
            "type": "object",
            "properties": {
                "outcome": {"type": "string", "enum": sorted(set(OUTCOMES) - {"needs_retake"})},
                "summary": {"type": "string",
                            "description": "利用者に見せる短い説明。氏名は書かない"},
                "unresolved": {"type": "array", "items": {"type": "string"},
                               "description": "残った食い違いや要確認。なければ空"},
            },
            "required": ["outcome", "summary"],
        },
    },
]

TERMINAL = {"finish", "request_retake"}


# ---------- ツールが使う、重いものの読み込み（1プロセスで1回だけ） ----------

_filings = None
_block_points = None
_ground_index = None


def filings() -> list[dict]:
    global _filings
    if _filings is None:
        _filings = evaluate.load_filings()
    return _filings


def block_points():
    global _block_points
    if _block_points is None:
        _block_points = volume.load_block_points()
    return _block_points


def ground_index():
    global _ground_index
    if _ground_index is None:
        print("  （bldg.geojson を読み込み中…）", flush=True)
        _ground_index = volume.load_ground_index()
    return _ground_index


# ---------- 1枚ぶんの状態 ----------

class Session:
    """写真1枚の処理の途中の状態と、判断の記録。"""

    def __init__(self, photo: Path, far_photo: Path | None = None):
        self.photo = photo
        self.far_photo = far_photo  # 同じ地点の遠い敷地写真（あれば）。check_site で使う
        self.site_state: dict | None = None
        self.boards: list[Path] | None = None
        self.board_detected = False
        self.board_index: int | None = None
        self.reading: dict | None = None
        self.filing: dict | None = None
        self.match_key: str | None = None
        self.point: tuple[float, float] | None = None
        self.volume: dict | None = None
        self.steps: list[dict] = []
        self.outcome: str | None = None
        self.summary: str | None = None
        self.unresolved: list[str] = []

    def crops(self) -> list[Path]:
        if self.boards is None:
            self.boards, self.board_detected = crop_board.crop_boards(self.photo)
        return self.boards


# ---------- ツールの中身 ----------

def compact_field(field: dict) -> dict:
    """モデルに見せる項目を小さくする（null の note などは落とす）。"""
    return {k: v for k, v in field.items()
            if k in ("value", "status", "confidence", "raw", "first_read") and v is not None}


def compact_reading(reading: dict) -> dict:
    out = {"fields": {name: compact_field(f)
                      for name, f in (reading.get("fields") or {}).items()}}
    for block in ("lot", "address"):
        if reading.get(block):
            out[block] = {k: v for k, v in reading[block].items() if v is not None}
    return out


def tool_read_board(s: Session, args: dict) -> dict:
    crops = s.crops()
    index = int(args.get("board") or 0)
    if not 0 <= index < len(crops):
        return {"error": f"看板は {len(crops)} 枚しかありません（0〜{len(crops) - 1}）"}

    s.board_index = index
    s.reading = read_board.read_board(crops[index])
    out = compact_reading(s.reading)
    out["boards_found"] = len(crops) if s.board_detected else 0
    if not s.board_detected:
        out["note"] = "SAM が看板を見つけられなかったので、写真全体を看板として読みました"
    return out


def tool_check_consistency(s: Session, args: dict) -> dict:
    if s.reading is None:
        return {"error": "先に read_board を呼んでください"}
    issues = read_board.check_consistency(s.reading.get("fields") or {})
    s.reading["consistency_issues"] = issues
    low = read_board.low_confidence_fields(s.reading)
    return {
        "issues": issues,
        "ok": not issues,
        "low_confidence": low,
        # 数値どうしの検算は「辻褄が合っている嘘」を捕まえられない。確信度の側でも見せる。
        "low_confidence_note": (
            f"確信度が {read_board.LOW_CONFIDENCE} 未満の項目です。"
            "検算を通っていても、モデルが桁を補った値かもしれません。"
            "位置や高さに関わるものは reread_fields で読み直してください" if low else None),
    }


def tool_reread_fields(s: Session, args: dict) -> dict:
    if s.reading is None:
        return {"error": "先に read_board を呼んでください"}
    names = [n for n in (args.get("fields") or []) if n]
    if not names:
        return {"error": "読み直す項目を fields に入れてください"}
    unknown = [n for n in names if n not in read_board.REREADABLE]
    if unknown:
        return {"error": f"読み直せない項目です: {unknown}。"
                         f"読み直せるのは {sorted(read_board.REREADABLE)}"}

    reason = args.get("reason") or "食い違いの確認"
    # reread は「矛盾の一覧」から項目を拾う形なので、理由をその形に包んで渡す
    done = read_board.reread(s.crops()[s.board_index or 0], s.reading,
                             [{"message": reason, "fields": names}])
    if done is None:
        return {"error": "読み直せる項目がありませんでした"}

    s.reading.setdefault("rereads", []).append(done)
    issues = read_board.check_consistency(s.reading.get("fields") or {})
    s.reading["consistency_issues"] = issues

    def value_of(name: str) -> dict:
        # 住居表示・地名地番は fields ではなく別ブロックなので取り出し方を分ける
        if name in read_board.BLOCK_LABELS:
            block = s.reading.get(name) or {}
            return {"value": read_board.block_summary(name, block),
                    "status": block.get("status"), "confidence": block.get("confidence")}
        return compact_field((s.reading.get("fields") or {}).get(name) or {})

    return {
        "reread": names,
        "changed": done["changed"],
        "values": {n: value_of(n) for n in names},
        "consistency_issues": issues,
    }


def filing_summary(row: dict) -> dict:
    """モデルに見せる届出。氏名の列はそもそもCSVに無いが、見せる列も絞る。"""
    keys = ("filing_no", "filing_type", "work_type", "building_name", "lot_raw",
            "address_raw", "address_chome", "address_block", "use", "structure",
            "units_total", "floors_above", "floors_below",
            "height_m", "max_height_m", "eave_height_m",
            "site_area_m2", "building_area_m2", "total_floor_area_m2",
            "construction_start", "construction_end", "sign_posted")
    out = {k: row[k] for k in keys if (row.get(k) or "").strip()}
    out["verified"] = evaluate.is_verified(row)
    out["verified_note"] = ("目視確認済み" if evaluate.is_verified(row) else
                            "**目視確認がまだ済んでいない**ので、届出の側も読み間違いがありうる")
    if (row.get("uncertain") or "").strip():
        out["filing_uncertain_fields"] = row["uncertain"]
    return out


def tool_lookup_filing(s: Session, args: dict) -> dict:
    if s.reading is None:
        return {"error": "先に read_board を呼んでください"}

    parcel = normalize.parcel(args.get("parcel"))
    chome = normalize.chome(args.get("chome"))
    block = normalize.block_number(args.get("block"))
    name = normalize.text(args.get("building_name"))
    if not any((parcel, chome, name)):
        return {"error": "鍵がありません。parcel、chome（と街区番号）、building_name の"
                         "どれかを渡してください"}

    used, hits = [], filings()
    if parcel:
        used.append(f"地番 {parcel}")
        hits = [r for r in hits if parcel in normalize.parcels_in(r.get("lot_raw"))]
    if chome:
        used.append(f"町丁目 {chome}")
        hits = [r for r in hits
                if chome in (normalize.chome(r.get("address_chome")),
                             normalize.chome_in(r.get("lot_raw")))]
    if block is not None:
        used.append(f"街区 {block}番")
        hits = [r for r in hits
                if normalize.block_number(r.get("address_block")) == block]
    if name:
        used.append(f"名称 {name}")
        hits = [r for r in hits if normalize.text(r.get("building_name")) == name]

    key = "＋".join(used)
    if not hits:
        return {"matches": 0, "key": key,
                "hint": "鍵を変えて呼び直せます（地番だけ、町丁目＋街区、名称など）。"
                        "届出は中高層の標識設置届だけなので、小規模な工事は載っていません"}
    if len(hits) > 1:
        return {"matches": len(hits), "key": key,
                "candidates": [{"filing_no": r.get("filing_no"),
                                "lot_raw": r.get("lot_raw"),
                                "address_raw": r.get("address_raw")} for r in hits[:8]],
                "hint": "鍵を足して絞り込んでください"}

    row = hits[0]
    s.filing, s.match_key = row, key

    # 比べるのはコード（evaluate.py と同じ規則）。一致した項目は名前だけ返す。
    comparisons = evaluate.compare_photo(s.reading, row)
    differences = [{k: c[k] for k in ("field", "label", "outcome", "board", "filing")}
                   for c in comparisons if c["outcome"] != evaluate.MATCH]
    return {
        "matches": 1,
        "key": key,
        "filing": filing_summary(row),
        "agreed_fields": [c["field"] for c in comparisons if c["outcome"] == evaluate.MATCH],
        "differences": differences,
        "note": "outcome は 不一致／看板だけ／届出だけ／両方なし。"
                "「不一致」だけが食い違いで、「看板だけ」「届出だけ」は片側に欄がないだけです",
    }


def tool_geocode(s: Session, args: dict) -> dict:
    chome = normalize.chome(args.get("chome"))
    block = normalize.block_number(args.get("block"))
    if chome is None or block is None:
        return {"error": "町丁目と街区番号の両方が必要です"}
    point = block_points().get((chome, block))
    if point is None:
        return {"matched": False,
                "error": f"{chome}{block}番の街区の代表点が見つかりません"}
    s.point = point
    return {"matched": True, "lat": point[0], "lon": point[1],
            "chome": chome, "block": block,
            "accuracy": "街区の代表点。街区の中のどこに建つかは分かりません"}


def tool_extrude(s: Session, args: dict) -> dict:
    if s.point is None:
        return {"error": "先に geocode で位置を決めてください"}
    area = args.get("building_area_m2")
    height = args.get("height_m")
    if not isinstance(area, (int, float)) or area <= 0:
        return {"error": "建築面積が正の数ではありません"}
    if not isinstance(height, (int, float)) or height <= 0:
        return {"error": "高さが正の数ではありません"}

    # 渡された高さが軒高でないかをコードで確かめる（一度やった取り違え。dev-policy §4）
    fields = s.reading.get("fields") or {} if s.reading else {}

    def value(name):
        f = fields.get(name) or {}
        return f.get("value") if f.get("status") == "read" else None

    eave, law, top = value("eave_height_m"), value("height_m"), value("max_height_m")
    if eave is not None and abs(height - eave) < 0.005 and (law or top):
        return {"error": f"渡された {height}m は軒高（eave_height_m）です。"
                         f"3D表示には最高の高さ（なければ高さ）を使ってください。"
                         f"この看板では max_height_m={top}、height_m={law}"}
    expected = top if top is not None else law
    if expected is not None and abs(height - expected) > 0.005:
        return {"error": f"渡された {height}m は、3D表示に使う高さ {expected}m と違います。"
                         f"max_height_m={top}、height_m={law}"}

    # 自信のない読み取りのまま建物を立てない。検算を通っていても、モデルが桁を
    # 補った値のことがある（遠い・潰れた看板で実際に起きた）。
    low = {item["field"]: item for item in read_board.low_confidence_fields(s.reading or {})}
    used = ["max_height_m" if top is not None else "height_m", "building_area_m2"]
    blocked = [low[name] for name in used if name in low]
    if blocked:
        return {"error": "3Dに使う項目の確信度が低いので立てられません: "
                         + "、".join(f"{b['field']}={b['value']}（確信度 {b['confidence']}"
                                    + (f"、モデルの注記「{b['note']}」" if b["note"] else "")
                                    + "）" for b in blocked)
                         + f"。reread_fields で読み直し、それでも {read_board.LOW_CONFIDENCE} "
                           "未満なら request_retake で撮り直しを頼んでください"}

    lat, lon = s.point
    base_z, samples, radius = volume.ground_z(ground_index(), lat, lon)
    if base_z is None:
        return {"error": "近くに既存建物がないため底面の高さを決められません"
                         "（bldg.geojson は撮影ルートの4メッシュだけ）"}

    s.volume = {
        "footprint": volume.square_footprint(lat, lon, area),
        "position": "block_representative_point",
        "shape": "square_north",
        "building_area_m2": area,
        "display_height_m": height,
        "display_height_source": "max_height_m" if top is not None else "height_m",
        "base_z_m": base_z,
        "base_z_samples": samples,
        "base_z_radius_m": radius,
        "base_z_rough": radius > volume.GROUND_RADII_M[1],
    }
    return {
        "built": True,
        "side_m": round(area ** 0.5, 2),
        "height_m": height,
        "base_z_m": base_z,
        "top_z_m": round(base_z + height, 2),
        "approximations": ["形は面積が同じ北向きの正方形（最寄りの道路に沿わせるのはまだ）",
                           "位置は街区の代表点"],
    }


def tool_check_site(s: Session, args: dict) -> dict:
    image = s.far_photo or s.photo
    photo = site_state.judge_site(image)

    lat = lon = None
    loc_src = None
    if s.point is not None:
        lat, lon = s.point
        loc_src = "geocode の街区代表点"
    else:
        ex = read_exif(image)
        if ex["lat"] is not None:
            lat, lon, loc_src = ex["lat"], ex["lon"], "写真の撮影GPS"

    if lat is not None:
        plateau = site_state.plateau_state(ground_index(), lat, lon)
    else:
        plateau = {"has_building": None, "nearby_count": 0,
                   "note": "位置が取れないので PLATEAU と照合できない"}
    verdict = site_state.reconcile(photo, plateau)

    s.site_state = {"image": str(image), "used_far_photo": s.far_photo is not None,
                    "location_source": loc_src, "photo_state": photo,
                    "plateau": plateau, "verdict": verdict}
    return {
        "used_far_photo": s.far_photo is not None,
        "state": photo["state"], "state_label": photo["state_label"],
        "confidence": photo["confidence"], "signals": photo["signals"],
        "plateau": plateau, "verdict": verdict,
        "note": "verdict が plateau_outdated のときは、PLATEAUに建物が残っているのに"
                "現地は更地・工事中（3D都市モデルの未反映）。unresolved に入れて知らせてください",
    }


def tool_request_retake(s: Session, args: dict) -> dict:
    s.outcome = "needs_retake"
    s.summary = args.get("reason") or "撮り直してください"
    return {"done": True}


def tool_finish(s: Session, args: dict) -> dict:
    outcome = args.get("outcome")
    s.outcome = outcome if outcome in OUTCOMES else "needs_review"
    s.summary = args.get("summary") or ""
    s.unresolved = [u for u in (args.get("unresolved") or []) if u]
    return {"done": True}


HANDLERS = {
    "read_board": tool_read_board,
    "check_consistency": tool_check_consistency,
    "reread_fields": tool_reread_fields,
    "lookup_filing": tool_lookup_filing,
    "geocode": tool_geocode,
    "extrude": tool_extrude,
    "check_site": tool_check_site,
    "request_retake": tool_request_retake,
    "finish": tool_finish,
}


# ---------- ループ ----------

def item_text(item: dict) -> str | None:
    """commentary の短いテキスト（モデルが次の手の前に書く理由）を取り出す。"""
    parts = [c.get("text") for c in (item.get("content") or []) if c.get("text")]
    return " ".join(parts).strip() or None


def run(photo: Path, effort: str = "low", far_photo: Path | None = None) -> dict:
    s = Session(photo, far_photo)
    started = datetime.now().astimezone()

    far_note = (f"\n同じ地点の遠い敷地写真（{far_photo.name}）も渡されています。"
                "敷地の現況を確かめたいときは check_site で使えます。" if far_photo else "")
    convo = [{"role": "user", "content":
              INSTRUCTIONS.format(outcomes="\n".join(
                  f"- {k}: {v}" for k, v in OUTCOMES.items() if k != "needs_retake"))
              + f"\n\n処理する写真: {photo.name}" + far_note}]

    pending_thought = None
    for turn in range(MAX_TURNS):
        response = client.responses.create(model=MODEL, input=convo, tools=TOOLS,
                                           reasoning={"effort": effort}, store=False)
        calls = []
        for item in response.output:
            data = item.model_dump()
            convo.append({k: v for k, v in data.items() if v is not None})
            if data["type"] == "message":
                pending_thought = item_text(data) or pending_thought
            elif data["type"] == "function_call":
                calls.append(data)

        if not calls:
            s.outcome = s.outcome or "needs_review"
            s.summary = s.summary or (pending_thought or "ツールを呼ばずに終わりました")
            break

        stop = False
        for call in calls:
            name = call["name"]
            try:
                args = json.loads(call["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            handler = HANDLERS.get(name)

            began = time.monotonic()
            if handler is None:
                output = {"error": f"{name} というツールはありません"}
            else:
                try:
                    output = handler(s, args)
                except Exception as e:  # ツールが落ちてもループは続ける（記録に残す）
                    output = {"error": f"{type(e).__name__}: {e}"}
            elapsed = round((time.monotonic() - began) * 1000)

            s.steps.append({"n": len(s.steps) + 1, "thought": pending_thought,
                            "tool": name, "input": args, "output": output,
                            "ms": elapsed})
            print(f"  {len(s.steps)}. {name}({', '.join(f'{k}={v!r}' for k, v in args.items())[:70]})"
                  f" → {'error: ' + output['error'][:60] if output.get('error') else 'ok'}"
                  f" [{elapsed}ms]", flush=True)
            pending_thought = None

            convo.append({"type": "function_call_output", "call_id": call["call_id"],
                          "output": json.dumps(output, ensure_ascii=False)})
            if name in TERMINAL and not output.get("error"):
                stop = True
        if stop:
            break
    else:
        s.outcome = s.outcome or "needs_review"
        s.summary = s.summary or f"{MAX_TURNS}手でも結論に届きませんでした"

    return {
        "photo": photo.stem,
        "source_image": str(photo),
        "model": MODEL,
        "effort": effort,
        "started": started.isoformat(timespec="seconds"),
        "finished": datetime.now().astimezone().isoformat(timespec="seconds"),
        "outcome": s.outcome,
        "outcome_label": OUTCOMES.get(s.outcome or "", ""),
        "summary": s.summary,
        "unresolved": s.unresolved,
        "board_detected": s.board_detected,
        "boards_found": len(s.boards or []),
        "read_image": str(s.boards[s.board_index]) if s.boards and s.board_index is not None else None,
        "filing_no": (s.filing or {}).get("filing_no"),
        "match_key": s.match_key,
        "filing_verified": evaluate.is_verified(s.filing) if s.filing else None,
        "volume": s.volume,
        "site_state": s.site_state,
        "far_photo": s.far_photo.name if s.far_photo else None,
        "reading": s.reading,
        "steps": s.steps,
    }


def main():
    parser = argparse.ArgumentParser(description="看板をエージェントで処理する")
    parser.add_argument("target", type=Path, help="写真、または --all のときはその入っているディレクトリ")
    parser.add_argument("--all", action="store_true", help="ディレクトリの写真を全部処理する")
    parser.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    parser.add_argument("--pairs", type=Path,
                        help="pair_photos.py の出力（近い写真に同地点の遠い写真を渡す）")
    args = parser.parse_args()

    if args.all:
        photos = sorted(p for p in args.target.iterdir()
                        if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".heic"))
    else:
        photos = [args.target]

    # 近い写真 → 同じ地点の遠い敷地写真。check_site で現況を確かめるのに使う。
    far_of: dict[str, str] = {}
    if args.pairs and args.pairs.exists():
        for c in json.loads(args.pairs.read_text(encoding="utf-8")):
            far = (c.get("far") or [None])[0]
            if far:
                for near in c.get("near") or []:
                    far_of[near] = far

    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    records = []
    for photo in photos:
        print(f"\n=== {photo.name} ===", flush=True)
        far_name = far_of.get(photo.stem)
        far_photo = photo.parent / f"{far_name}.JPG" if far_name else None
        record = run(photo, args.effort, far_photo)
        out = RECORD_DIR / f"{photo.stem}.json"
        out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  → {record['outcome']}: {record['summary']}")
        for item in record["unresolved"]:
            print(f"     要確認: {item}")
        print(f"  記録 → {out}")
        records.append(record)

    if len(records) > 1:
        print("\n=== まとめ ===")
        for r in records:
            print(f"  {r['photo']}: {r['outcome']} "
                  f"（{len(r['steps'])}手"
                  f"{'、届出No.' + str(r['filing_no']) if r['filing_no'] else ''}）")


if __name__ == "__main__":
    main()
