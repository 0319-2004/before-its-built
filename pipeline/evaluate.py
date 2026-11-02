"""エージェント（`agent.py`）の出力を、目黒区の届出（正解データ）と突き合わせて数える。

先に `python pipeline/agent.py --all data/photos/<日付>` を回し、
判断の記録 `data/agent/<写真>.json` を作っておく。

    python pipeline/evaluate.py

照合はエージェントが済ませている（記録の `filing_no` を使う）。項目ごとの一致・不一致は
`compare_photo` が `normalize.py` で表記をそろえてからコードで決める（エージェントと同じ規則）。
数値は小数点以下の書き方の違いを許す。

**誤りとは別に数えるもの**（dev-policy §7）:
- 読めない値を作らず**建物を立てなかった**（例: IMG_3966 の高さ）。桁を補わないのは正しい挙動。
- 看板と届出が**実際に食い違っていた**（例: IMG_3936 の敷地面積）。これは読み間違いではない。
  エージェントが `mismatch_notified` で終えた写真の不一致は、読み直して「食い違い」として
  知らせたものなので、読み間違い候補とは別の列で数える。

出力:
    data/eval/per_photo.json … 写真ごとの詳細。建物名に個人名が入ることがあるのでリポジトリに入れない
    data/eval/summary.json   … 集計（機械が読む用）
    docs/eval-summary.md     … 件数と割合だけの集計。リポジトリに入れるのはこれだけ

注意: 届出の側もモデルが読んだもので、目視確認が済んだ行（verified=1）だけが本当の正解。
まだ1件も確認が済んでいないので、ここの数字はすべて**暫定**。
"""
import csv
import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

import normalize
# 「自信なし」の境目は read_board.py を正本にする（読み取り側と評価側でずらさないため）
from read_board import LOW_CONFIDENCE, is_other_board, low_confidence_fields

FILINGS_CSV = Path("data/meguro/filings_extracted.csv")
AGENT_DIR = Path("data/agent")  # agent.py の判断の記録。これを集計する
EVAL_DIR = Path("data/eval")
SUMMARY_MD = Path("docs/eval-summary.md")

MATCH, MISMATCH = "一致", "不一致"
BOTH_ABSENT, ONLY_BOARD, ONLY_FILING = "両方なし", "看板だけ", "届出だけ"
# 不一致を2つに分ける：読み間違いの候補と、看板と届出が実際に食い違っていたもの。
MISREAD, DECLARED = "読み間違い", "実際の食い違い"

# エージェントの4つの終わり方（dev-policy §3）。
OUTCOME_LABELS = {
    "built": "3Dに立てた",
    "mismatch_notified": "食い違いを知らせた",
    "needs_review": "要確認",
    "needs_retake": "撮り直し",
}

# (看板側の項目, 表示名, 届出のCSVの列, 比べ方)
FIELD_SPECS = [
    ("lot", "地名地番", "lot_raw", "parcels"),
    ("address.chome", "住居表示の町丁目", "address_chome", "text"),
    ("address.block", "住居表示の街区番号", "address_block", "number"),
    ("address.go", "住居表示の号", "address_go", "number"),
    ("building_name", "建築物の名称", "building_name", "text"),
    ("use", "主要用途", "use", "text"),
    ("structure", "構造", "structure", "structure"),
    ("site_area_m2", "敷地面積", "site_area_m2", "number"),
    ("building_area_m2", "建築面積", "building_area_m2", "number"),
    ("total_floor_area_m2", "延べ面積", "total_floor_area_m2", "number"),
    ("floors_above", "地上の階数", "floors_above", "number"),
    ("floors_below", "地下の階数", "floors_below", "number"),
    ("height_m", "高さ", "height_m", "number"),
    ("max_height_m", "最高の高さ（塔屋等まで）", "max_height_m", "number"),
    ("eave_height_m", "軒高", "eave_height_m", "number"),
    # 看板と届出で高さの呼び方がずれていても、3Dに使う数字が合っているかは別に見る。
    # 「最高の高さがあればそれ、なければ高さ」は dev-policy §4 の表示の決まり。
    ("display_height", "3D表示に使う高さ", "display_height", "number"),
    ("units", "住戸数", "units_total", "number"),
    ("construction_start", "着工予定", "construction_start", "date"),
    ("construction_end", "完了予定", "construction_end", "date"),
    ("notice_posted", "標識の設置日", "sign_posted", "date"),
]

# 分かっている読み間違いの例。直した経緯を残すために docs にも出す（dev-policy §6）。
KNOWN_MISREADS = [
    "IMG_3928（届出No.51）の住居表示：看板には「3丁目17番」と書いてあるが、"
    "地名地番と同じ欄に併記されていたため最初は「3丁目1番」と読んだ（写真で確認済み）。"
    "住居表示を`address`として分けて取るようにして直った。"
    "地番で届出を引いても17番と分かり、建物名「上目黒317」とも合う。",
    "IMG_3931・IMG_3936の高さ：軒高を`height_m`に入れていた"
    "（看板の書式が「高さ ○m（塔屋等まで ○m、軒高 ○m）」）。"
    "高さを3つに分け、検算に「軒高 ≤ 高さ ≤ 最高の高さ」を足して直った。"
    "3D表示は最高の高さ（なければ高さ）を使うので、軒高が高さの箱に入ったままだと"
    "建物が2m低く立っていた。",
]

# まだどちらが正しいか決まっていない不一致。片側だけ確かめた時点で消さずに残す。
OPEN_MISMATCHES = [
    "IMG_3936（届出No.67）の敷地面積：看板の読み取りは71.87㎡、届出の読み取りは70.32㎡。"
    "届出のスキャンを目視して70.32㎡で確定（2026-11-01）。"
    "看板の写真の側は未確認なので、どちらの読み間違いかはまだ決まっていない。",
]

def redact_key(key: str | None) -> str:
    """照合の鍵を表示用に。名称を鍵にしたときは名前を伏せる（公開する md 用）。"""
    if not key:
        return "—"
    return re.sub(r"名称\s*\S+", "名称（略）", key)


# ---------- 値の取り出し ----------

def board_item(result: dict, name: str):
    """看板側の (値, 状態, 確信度) を取り出す。"""
    if name == "display_height":
        fields = result.get("fields") or {}
        for key in ("max_height_m", "height_m"):
            field = fields.get(key) or {}
            if field.get("value") is not None:
                return field["value"], field.get("status"), field.get("confidence")
        return None, "absent", None
    if name == "lot":
        lot = result.get("lot") or {}
        numbers = [normalize.parcel(p.get("number")) for p in (lot.get("parcels") or [])]
        return [n for n in numbers if n], lot.get("status"), lot.get("confidence")
    if name.startswith("address."):
        address = result.get("address") or {}
        return address.get(name.split(".", 1)[1]), address.get("status"), address.get("confidence")
    field = (result.get("fields") or {}).get(name) or {}
    return field.get("value"), field.get("status"), field.get("confidence")


def filing_item(row: dict, column: str):
    """届出側の値を取り出す。住居表示の号は専用の列がないので、住居表示の欄から取る。"""
    if column == "address_go":
        text = normalize.text(row.get("address_raw")) or ""
        m = re.search(r"(\d+)号", text)
        return m.group(1) if m else None
    if column == "display_height":
        return (row.get("max_height_m") or "").strip() or (row.get("height_m") or "").strip() or None
    value = (row.get(column) or "").strip()
    return value or None


def has_value(value) -> bool:
    return bool(value) if isinstance(value, list) else value not in (None, "")


# ---------- 比べ方 ----------

def as_decimal(value):
    try:
        return Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def same_number(a, b) -> bool:
    """小数点以下の書き方の違いを許して比べる（385.6 と 385.60 は同じ）。"""
    da, db = as_decimal(a), as_decimal(b)
    if da is None or db is None:
        return normalize.text(a) == normalize.text(b)
    return da.normalize() == db.normalize()


def same_date(a, b) -> bool:
    """年月までしか読めなかったものは、年月が合っていれば一致とみなす。"""
    sa, sb = normalize.text(a), normalize.text(b)
    if not sa or not sb:
        return sa == sb
    return sa.startswith(sb) or sb.startswith(sa)


def same_value(kind: str, board, filing) -> bool:
    if kind == "parcels":
        return bool(set(board or []) & set(normalize.parcels_in(filing)))
    if kind == "number":
        return same_number(board, filing)
    if kind == "date":
        return same_date(board, filing)
    if kind == "structure":
        return normalize.structure(board) == normalize.structure(filing)
    return normalize.text(board) == normalize.text(filing)


def is_low_confidence(status, confidence) -> bool:
    """モデルが「自信なし」にした項目か。"""
    if status in ("unreadable", "masked"):
        return True
    return confidence is not None and confidence < LOW_CONFIDENCE


# ---------- 集計 ----------

def compare_photo(result: dict, row: dict) -> list[dict]:
    comparisons = []
    uncertain = set((row.get("uncertain") or "").split(","))
    for name, label, column, kind in FIELD_SPECS:
        board, status, confidence = board_item(result, name)
        filing = filing_item(row, column)
        if has_value(board) and has_value(filing):
            outcome = MATCH if same_value(kind, board, filing) else MISMATCH
        elif has_value(board):
            outcome = ONLY_BOARD
        elif has_value(filing):
            outcome = ONLY_FILING
        else:
            outcome = BOTH_ABSENT
        comparisons.append({
            "field": name,
            "label": label,
            "outcome": outcome,
            "board": board,
            "filing": filing,
            "status": status,
            "confidence": confidence,
            "low_confidence": is_low_confidence(status, confidence),
            "filing_uncertain": column in uncertain,
        })
    return comparisons


def bucket(outcome: str, declared: bool) -> str:
    """不一致を、読み間違い候補と「実際の食い違い」に振り分ける。"""
    if outcome == MISMATCH:
        return DECLARED if declared else MISREAD
    return outcome


def aggregate(photos: list[dict]) -> dict:
    """項目ごとの一致数と、確信度との重なりを数える。

    各 photo は compare_photo の結果 `comparisons` と、エージェントが
    `mismatch_notified` で終えたかの `declared` を持つ。
    """
    by_field = {name: Counter() for name, _, _, _ in FIELD_SPECS}
    totals = Counter()
    overlap = Counter()
    filing_uncertain_on_mismatch = 0
    for photo in photos:
        declared = photo.get("declared", False)
        for c in photo["comparisons"]:
            b = bucket(c["outcome"], declared)
            by_field[c["field"]][b] += 1
            totals[b] += 1
            # 確信度の当てになり方は「読み間違い」だけで見る。実際の食い違いは
            # 読み間違いではない（IMG_3936 は確信度0.95で正しく読めていた）ので外す。
            if b in (MATCH, MISREAD):
                overlap[(c["low_confidence"], b == MISREAD)] += 1
            if b == MISREAD and c["filing_uncertain"]:
                filing_uncertain_on_mismatch += 1
    return {
        "photos": len(photos),
        "by_field": {name: dict(counts) for name, counts in by_field.items()},
        "totals": dict(totals),
        "confidence_overlap": {
            "low_and_mismatch": overlap[(True, True)],
            "low_and_match": overlap[(True, False)],
            "high_and_mismatch": overlap[(False, True)],
            "high_and_match": overlap[(False, False)],
        },
        "filing_uncertain_on_mismatch": filing_uncertain_on_mismatch,
    }


def rate(counts: dict) -> float | None:
    """一致率。分母は一致＋読み間違いだけ（実際の食い違いは誤りではないので外す）。"""
    comparable = counts.get(MATCH, 0) + counts.get(MISREAD, 0)
    return counts.get(MATCH, 0) / comparable if comparable else None


def percent(value) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


# ---------- 出力 ----------

def field_table(summary: dict) -> list[str]:
    lines = ["| 項目 | 比べられた | 一致 | 読み間違い | 実際の食い違い | 一致率 | 看板だけ | 届出だけ | 両方なし |",
             "|---|---|---|---|---|---|---|---|---|"]

    def row(label, c, bold=False):
        comparable = c.get(MATCH, 0) + c.get(MISREAD, 0)
        b = "**" if bold else ""
        return (f"| {b}{label}{b} | {b}{comparable}{b} | {b}{c.get(MATCH, 0)}{b} | "
                f"{c.get(MISREAD, 0)} | {c.get(DECLARED, 0)} | {b}{percent(rate(c))}{b} | "
                f"{c.get(ONLY_BOARD, 0)} | {c.get(ONLY_FILING, 0)} | {c.get(BOTH_ABSENT, 0)} |")

    for name, label, _, _ in FIELD_SPECS:
        lines.append(row(label, summary["by_field"].get(name, {})))
    lines.append(row("合計", summary["totals"], bold=True))
    return lines


def overlap_table(summary: dict) -> list[str]:
    o = summary["confidence_overlap"]
    return [
        "| | 不一致 | 一致 |",
        "|---|---|---|",
        f"| 自信なし（確信度 < {LOW_CONFIDENCE}） | {o['low_and_mismatch']} | {o['low_and_match']} |",
        f"| 自信あり | {o['high_and_mismatch']} | {o['high_and_match']} |",
    ]


def outcomes_table(photos: list[dict]) -> list[str]:
    lines = ["| 写真 | 結論 | 届出 | 鍵 | 手数 | 3D |",
             "|---|---|---|---|---|---|"]
    for p in photos:
        no = f"No.{p['filing_no']}" if p.get("filing_no") else "—"
        lines.append(
            f"| {p['photo']} | {OUTCOME_LABELS.get(p['outcome'], p['outcome'])} | "
            f"{no} | {redact_key(p.get('match_key'))} | {p['steps']} | "
            f"{'✓' if p.get('built_3d') else '—'} |")
    return lines


def declared_lines(matched: list[dict]) -> list[str]:
    """看板と届出が実際に食い違った項目を、数値だけで並べる（名称は出さない）。"""
    out = []
    for m in matched:
        if not m.get("declared"):
            continue
        for c in m["comparisons"]:
            # 建築物の名称は公開する md に出さない（個人名が入りうる）
            if c["outcome"] == MISMATCH and c["field"] != "building_name":
                out.append(
                    f"- {m['photo']}（No.{m['filing_no']}）{c['label']}："
                    f"看板 {c['board']} / 届出 {c['filing']}"
                    "。読み直しても一致せず `mismatch_notified` で終了。")
    return out or ["なし（この回では実際の食い違いは出なかった）。"]


def write_markdown(report: dict):
    counts = report["photo_counts"]
    o = report["outcome_counts"]
    agg = report["all"]
    lines = [
        "# 読み取り精度の評価（エージェント基準）",
        "",
        "`pipeline/evaluate.py` が `data/agent/*.json`（`agent.py` の判断の記録）を集計する。",
        "写真ごとの詳細は `data/eval/`（建物名に個人名が入ることがあるのでリポジトリに入れない）。",
        "",
        "**届出はまだ目視確認が済んでいない**（確認済み 0件）ので、ここの数字はすべて暫定値。",
        "",
        f"- 処理した写真: {counts['total']}枚",
        f"- 届出と照合できた: {counts['matched']}枚（すべて目視確認前の暫定）",
        f"- 3Dに立てた: {counts['built_3d']}枚",
        f"- 対象外の看板（労災票など）: {counts['other_board']}枚",
        f"- 立てずに終えた（読めない／届出に無い）: {counts['not_built']}枚",
        "",
        "## エージェントの結論",
        "",
    ] + outcomes_table(report["photos"]) + [
        "",
        "終わり方の内訳（dev-policy §3 の4つ）:",
        "",
        f"- 3Dに立てた（built）: {o.get('built', 0)}枚",
        f"- 食い違いを知らせた（mismatch_notified）: {o.get('mismatch_notified', 0)}枚",
        f"- 要確認（needs_review）: {o.get('needs_review', 0)}枚",
        f"- 撮り直し（needs_retake）: {o.get('needs_retake', 0)}枚",
        "",
        "## 立てなかった理由（誤りとは別に数える）",
        "",
        "値を作らずに立てなかったのは正しい挙動なので、読み取りの誤りとは分けて数える"
        "（dev-policy §4「ほかの欄から逆算して数字を埋めさせない」）。",
        "",
    ]
    for p in report["not_built"]:
        lines.append(f"- {p['photo']} — {p['reason']}")
    for p in report["other_board"]:
        lines.append(f"- {p['photo']} — 建築計画のお知らせではない（対象外の看板）")
    if not (report["not_built"] or report["other_board"]):
        lines.append("なし。")
    lines += [
        "",
        "## 看板の読み取り精度（届出と照合できた写真・暫定）",
        "",
        "エージェントの読み取りと届出を項目ごとに比べた。**不一致は2つに分ける**：",
        "読み間違いの候補と、看板と届出が実際に食い違っていたもの（下の別節）。",
        "一致率の分母は 一致＋読み間違い だけで、実際の食い違いは誤りに数えない。",
        "",
    ]
    if agg["photos"]:
        lines += field_table(agg)
    else:
        lines += ["照合できた写真がない。"]
    lines += [
        "",
        "## 看板と届出が実際に食い違った項目（読み間違いではない）",
        "",
        "エージェントが読み直してもそろわず、`mismatch_notified` で利用者に知らせたもの。",
        "どちらかに決めず、根拠（地番）とともに残す。",
        "",
    ] + declared_lines(report["matched"]) + [
        "",
        "## 確信度の当てになり方",
        "",
        "看板を読んだモデルの自己申告の確信度と、**読み間違い**の重なり"
        "（実際の食い違いは正しく読めているので外す）。",
        "",
    ] + overlap_table(agg) + [
        "",
        f"- 読み間違いのうち、届出の側でもモデルが自信なしにしていた項目: "
        f"{agg['filing_uncertain_on_mismatch']}件（看板ではなく届出の読み間違いの可能性）",
        "",
        "## 食い違った項目の読み直し",
        "",
        "検算や届出との食い違いで引っかかった項目だけを、推論の強さ`medium`で読み直した"
        "結果（dev-policy §3）。エージェントの `reread_fields` の手から数える。",
        "",
        f"- 読み直した写真: {report['reread_counts']['photos']}枚",
        f"- 値が変わった: {report['reread_counts']['changed_a_value']}件"
        "（読み直しても値は作らない。桁を補うのを防ぐため）",
        f"- 読み直しても検算の矛盾が残った: {report['reread_counts']['still_inconsistent']}件"
        "（この場合は建物を立てない。IMG_3966 の高さ）",
        "",
        "## 分かっている読み間違いの例",
        "",
    ] + [f"- {example}" for example in KNOWN_MISREADS] + [
        "",
        "## まだ決着していない不一致",
        "",
    ] + [f"- {example}" for example in OPEN_MISMATCHES] + [
        "",
        "## 比べ方",
        "",
        "- 照合はエージェントが済ませている（地番 → 住居表示 → 建築物の名称 の順に試す）。"
        "ここでは記録の `filing_no` の届出と項目ごとに比べる",
        "- 数値は小数点以下の書き方の違いを許す（`385.6` と `385.60` は一致）",
        "- 文字は `pipeline/normalize.py` で表記をそろえてから比べる"
        "（丁目は漢数字、「ヶ」「ケ」、全角と半角、空白）",
        "- 構造は 木造 / S造 / RC造 / SRC造 / その他 の5つにそろえてから比べる",
        "- 日付は年月までしか読めなかった場合、年月が合っていれば一致とみなす",
        "- 高さは3つ（高さ・塔屋等まで・軒高）を別々に比べ、3Dに使う数字が合っているかは"
        "「3D表示に使う高さ」（最高の高さがあればそれ、なければ高さ）で別に見る",
        f"- 「自信なし」は確信度 < {LOW_CONFIDENCE}、または状態が `unreadable`・`masked` の項目",
        f"- `mismatch_notified` で終えた写真の不一致は「実際の食い違い」として別に数える",
        "",
    ]
    SUMMARY_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------- 本体 ----------

def load_filings() -> list[dict]:
    with FILINGS_CSV.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def is_verified(row: dict) -> bool:
    return (row.get("verified") or "").strip().lower() in ("1", "true", "yes", "○")


def load_agent_records() -> list[dict]:
    """agent.py の判断の記録を写真の名前順に読む。"""
    records = []
    for path in sorted(AGENT_DIR.glob("*.json")):
        records.append(json.loads(path.read_text(encoding="utf-8")))
    return records


# 3D表示に効く項目（これが読めないと建物を立てない）。
CRITICAL_3D = {"height_m", "max_height_m", "building_area_m2"}


def not_built_reason(reading: dict, has_filing: bool) -> str:
    """建物を立てなかった理由を一行で。"""
    low = {f["field"] for f in low_confidence_fields(reading)}
    unreadable = {name for name, f in (reading.get("fields") or {}).items()
                  if isinstance(f, dict) and f.get("status") == "unreadable"}
    bad = (low | unreadable) & CRITICAL_3D
    if bad:
        names = "・".join(sorted(bad))
        return (f"高さ・建築面積が読めず（{names}）、桁を補わずに値を作らなかった"
                "（dev-policy §4）。" + ("" if has_filing else "届出も中高層リストに無い。"))
    if not has_filing:
        return "届出（中高層の標識設置届）に載っていない（延べ1万㎡超など）"
    return "要確認"


def reread_stats(records: list[dict]) -> dict:
    """エージェントの reread_fields の手から、読み直しの効きを数える。"""
    photos, changed, still_inconsistent = set(), 0, 0
    for rec in records:
        for step in rec.get("steps") or []:
            if step.get("tool") != "reread_fields":
                continue
            out = step.get("output") or {}
            photos.add(rec["photo"])
            if out.get("changed"):
                changed += 1
            # 読み直した後も検算の矛盾が残っていれば、建物は立てない（IMG_3966）
            if out.get("consistency_issues"):
                still_inconsistent += 1
    return {"photos": len(photos), "changed_a_value": changed,
            "still_inconsistent": still_inconsistent}


def main():
    filings = load_filings()
    by_no = {row["filing_no"]: row for row in filings if (row.get("filing_no") or "").strip()}
    records = load_agent_records()
    print(f"届出 {len(filings)}件（目視確認済み {sum(map(is_verified, filings))}件）、"
          f"エージェントの記録 {len(records)}件")

    photos_summary = []  # 結論テーブル用（全件）
    matched, other, not_built = [], [], []
    for rec in records:
        reading = rec.get("reading") or {}
        outcome = rec.get("outcome") or "needs_review"
        filing_no = rec.get("filing_no")
        row = by_no.get(str(filing_no)) if filing_no is not None else by_no.get(filing_no)
        built_3d = bool(rec.get("volume"))

        entry = {
            "photo": rec["photo"],
            "outcome": outcome,
            "filing_no": filing_no,
            "match_key": rec.get("match_key"),
            "steps": len(rec.get("steps") or []),
            "built_3d": built_3d,
        }
        photos_summary.append(entry)

        if is_other_board(reading):
            other.append({"photo": rec["photo"]})
        elif row is not None:
            matched.append({
                "photo": rec["photo"],
                "outcome": outcome,
                "filing_no": filing_no,
                "match_key": rec.get("match_key"),
                "verified": is_verified(row),
                "built_3d": built_3d,
                # mismatch_notified で終えた写真の不一致は「実際の食い違い」
                "declared": outcome == "mismatch_notified",
                "filing_uncertain": row.get("uncertain"),
                "comparisons": compare_photo(reading, row),
            })
        else:
            not_built.append({
                "photo": rec["photo"],
                "reason": not_built_reason(reading, has_filing=False),
            })

    report = {
        "low_confidence_threshold": LOW_CONFIDENCE,
        "filings": len(filings),
        "filings_verified": sum(map(is_verified, filings)),
        "photo_counts": {
            "total": len(records),
            "matched": len(matched),
            "built_3d": sum(1 for p in photos_summary if p["built_3d"]),
            "other_board": len(other),
            "not_built": len(not_built),
        },
        "outcome_counts": dict(Counter(p["outcome"] for p in photos_summary)),
        "reread_counts": reread_stats(records),
        "all": aggregate(matched),
        "photos": photos_summary,
        "matched": matched,
        "other_board": other,
        "not_built": not_built,
    }

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    (EVAL_DIR / "per_photo.json").write_text(
        json.dumps(matched, ensure_ascii=False, indent=2), encoding="utf-8")
    (EVAL_DIR / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(report)

    t = report["all"]["totals"]
    print(f"照合できた {len(matched)}枚 / 3Dに立てた {report['photo_counts']['built_3d']}枚 / "
          f"対象外 {len(other)}枚 / 立てず {len(not_built)}枚")
    print(f"一致 {t.get(MATCH, 0)} / 読み間違い {t.get(MISREAD, 0)} / "
          f"実際の食い違い {t.get(DECLARED, 0)} （一致率 {percent(rate(t))}）")
    print(f"\n書き出しました → {EVAL_DIR}/per_photo.json, {EVAL_DIR}/summary.json, {SUMMARY_MD}")


if __name__ == "__main__":
    main()
