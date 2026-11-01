"""看板の読み取り結果を、目黒区の届出（正解データ）と突き合わせて精度を数える。

先に `python pipeline/process.py <写真>` で読み取りを済ませておく。

    python pipeline/evaluate.py

照合の鍵は、地番（筆の番号）→ 住居表示 → 建物名 の順に試す。
比べる前に表記のゆれは `normalize.py` でそろえる。数値は小数点以下の書き方の違いを許す。

出力:
    data/eval/per_photo.json … 写真ごとの詳細。建物名に個人名が入ることがあるのでリポジトリに入れない
    data/eval/summary.json   … 集計（機械が読む用）
    docs/eval-summary.md     … 件数と割合だけの集計。リポジトリに入れるのはこれだけ

注意: 届出の側もモデルが読んだもので、目視確認が済んだ行（verified=1）だけが本当の正解。
確認済みと未確認を混ぜずに出す。
"""
import csv
import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

import normalize
from read_board import is_other_board

FILINGS_CSV = Path("data/meguro/filings_extracted.csv")
READINGS_DIR = Path("samples/out")
PHOTO_PREFIX = "data/photos"  # 自分で撮った写真だけを評価に使う（拾い画像は評価に入れない）
EVAL_DIR = Path("data/eval")
SUMMARY_MD = Path("docs/eval-summary.md")

# モデルの自己申告の確信度が、これより低い項目を「自信なし」として数える。
LOW_CONFIDENCE = 0.9

MATCH, MISMATCH = "一致", "不一致"
BOTH_ABSENT, ONLY_BOARD, ONLY_FILING = "両方なし", "看板だけ", "届出だけ"

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

KEY_LABELS = {
    "parcel_chome": "地番＋町丁目",
    "parcel": "地番",
    "address": "住居表示",
    "name": "建築物の名称",
}


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


# ---------- 照合 ----------

def match_filing(result: dict, filings: list[dict]):
    """看板に対応する届出を探す。(届出, 使った鍵, 注記) を返す。"""
    parcels = set(board_item(result, "lot")[0] or [])
    lot_chome = normalize.chome_in((result.get("lot") or {}).get("area"))
    address = result.get("address") or {}
    chome = normalize.chome(address.get("chome")) or lot_chome
    block = normalize.block_number(address.get("block"))
    name = normalize.text((result.get("fields") or {}).get("building_name", {}).get("value"))

    def filing_parcels(row):
        return set(normalize.parcels_in(row.get("lot_raw")))

    tries = []
    if parcels:
        tries.append(("parcel_chome", lambda row: bool(parcels & filing_parcels(row))
                      and lot_chome is not None
                      and normalize.chome_in(row.get("lot_raw")) == lot_chome))
        tries.append(("parcel", lambda row: bool(parcels & filing_parcels(row))))
    if chome and block is not None:
        tries.append(("address", lambda row: normalize.chome(row.get("address_chome")) == chome
                      and normalize.block_number(row.get("address_block")) == block))
    if name:
        tries.append(("name", lambda row: normalize.text(row.get("building_name")) == name))

    pool, note = filings, None
    for key, test in tries:
        hits = [row for row in pool if test(row)]
        if len(hits) == 1:
            return hits[0], key, note
        if len(hits) > 1:
            pool = hits  # 絞り込めたので、次の鍵でさらに絞る
            note = f"{KEY_LABELS[key]}で{len(hits)}件に当たった"
    if pool is not filings and len(pool) > 1:
        return None, None, note
    return None, None, note or "どの鍵でも当たらなかった"


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


def aggregate(photos: list[dict]) -> dict:
    """項目ごとの一致数と、確信度との重なりを数える。"""
    by_field = {name: Counter() for name, _, _, _ in FIELD_SPECS}
    totals = Counter()
    overlap = Counter()
    filing_uncertain_on_mismatch = 0
    for photo in photos:
        for c in photo["comparisons"]:
            by_field[c["field"]][c["outcome"]] += 1
            totals[c["outcome"]] += 1
            if c["outcome"] in (MATCH, MISMATCH):
                overlap[(c["low_confidence"], c["outcome"] == MISMATCH)] += 1
            if c["outcome"] == MISMATCH and c["filing_uncertain"]:
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
    comparable = counts.get(MATCH, 0) + counts.get(MISMATCH, 0)
    return counts.get(MATCH, 0) / comparable if comparable else None


def percent(value) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


# ---------- 出力 ----------

def field_table(summary: dict) -> list[str]:
    lines = ["| 項目 | 比べられた | 一致 | 不一致 | 一致率 | 看板だけ | 届出だけ | 両方なし |",
             "|---|---|---|---|---|---|---|---|"]
    for name, label, _, _ in FIELD_SPECS:
        c = summary["by_field"].get(name, {})
        comparable = c.get(MATCH, 0) + c.get(MISMATCH, 0)
        lines.append(f"| {label} | {comparable} | {c.get(MATCH, 0)} | {c.get(MISMATCH, 0)} | "
                     f"{percent(rate(c))} | {c.get(ONLY_BOARD, 0)} | {c.get(ONLY_FILING, 0)} | "
                     f"{c.get(BOTH_ABSENT, 0)} |")
    t = summary["totals"]
    comparable = t.get(MATCH, 0) + t.get(MISMATCH, 0)
    lines.append(f"| **合計** | **{comparable}** | **{t.get(MATCH, 0)}** | **{t.get(MISMATCH, 0)}** | "
                 f"**{percent(rate(t))}** | {t.get(ONLY_BOARD, 0)} | {t.get(ONLY_FILING, 0)} | "
                 f"{t.get(BOTH_ABSENT, 0)} |")
    return lines


def overlap_table(summary: dict) -> list[str]:
    o = summary["confidence_overlap"]
    return [
        "| | 不一致 | 一致 |",
        "|---|---|---|",
        f"| 自信なし（確信度 < {LOW_CONFIDENCE}） | {o['low_and_mismatch']} | {o['low_and_match']} |",
        f"| 自信あり | {o['high_and_mismatch']} | {o['high_and_match']} |",
    ]


def write_markdown(report: dict):
    verified, unverified = report["verified"], report["unverified"]
    counts = report["photo_counts"]
    lines = [
        "# 読み取り精度の評価",
        "",
        f"`pipeline/evaluate.py` が作る集計。写真ごとの詳細は `data/eval/`（建物名に個人名が",
        "入ることがあるのでリポジトリに入れない）。",
        "",
        f"- 評価した写真: {counts['total']}枚",
        f"- 届出と照合できた: {counts['matched']}枚"
        f"（うち目視確認済みの届出 {verified['photos']}枚／未確認 {unverified['photos']}枚）",
        f"- 建築計画のお知らせではなかった: {counts['other_board']}枚",
        f"- 照合できなかった: {counts['unmatched']}枚",
        f"- 看板が読めなかった: {counts['unreadable']}枚",
        "",
        "照合に使った鍵（地番 → 住居表示 → 建築物の名称 の順に試した結果）:",
        "",
    ] + [f"- {KEY_LABELS[key]}: {n}枚" for key, n in report["match_keys"].items()] + [
        "",
        "## 目視確認済みの届出との比較",
        "",
    ]
    if verified["photos"]:
        lines += field_table(verified)
    else:
        lines += ["目視確認（`verified=1`）が済んだ届出がまだ1件もないため、ここは空。",
                  "下の表は未確認の読み取り結果どうしの比較なので、精度の数字としては暫定。"]
    lines += ["", "## 目視確認が済んでいない届出との比較（暫定）", ""]
    if unverified["photos"]:
        lines += field_table(unverified)
    else:
        lines += ["該当なし。"]
    lines += [
        "",
        "## 確信度の当てになり方",
        "",
        "看板を読んだモデルの自己申告の確信度と、実際の不一致の重なり"
        f"（照合できた{counts['matched']}枚、比べられた項目だけ）。",
        "",
    ] + overlap_table(report["all"]) + [
        "",
        f"- 不一致のうち、届出の側でモデルが自信なしにしていた項目: "
        f"{report['all']['filing_uncertain_on_mismatch']}件"
        "（看板ではなく届出の読み間違いの可能性がある）",
        "",
        "## 食い違った項目の読み直し",
        "",
        "検算に引っかかった項目だけを、推論の強さ`medium`で読み直した結果（dev-policy §3）。",
        "",
        f"- 読み直した写真: {report['reread_counts']['photos']}枚",
        f"- 値が変わった: {report['reread_counts']['changed_a_value']}件",
        f"- 読み直しで矛盾が解消した: {report['reread_counts']['resolved']}件",
        "",
        "## 分かっている読み間違いの例",
        "",
    ] + [f"- {example}" for example in KNOWN_MISREADS] + [
        "",
        "## 照合できなかった写真・別の看板だった写真",
        "",
    ]
    for photo in report["photos_other"]:
        lines.append(f"- {photo['photo']} — 建築計画のお知らせではない（{photo['note']}）")
    for photo in report["photos_unmatched"]:
        lines.append(f"- {photo['photo']} — 届出と照合できない（{photo['note']}）")
    for photo in report["photos_unreadable"]:
        lines.append(f"- {photo['photo']} — 看板が読めない（{photo['note']}）")
    if not (report["photos_other"] or report["photos_unmatched"] or report["photos_unreadable"]):
        lines.append("なし。")
    lines += [
        "",
        "## 比べ方",
        "",
        "- 照合の鍵は 地番（筆の番号）→ 住居表示 → 建築物の名称 の順に試す",
        "- 数値は小数点以下の書き方の違いを許す（`385.6` と `385.60` は一致）",
        "- 文字は `pipeline/normalize.py` で表記をそろえてから比べる"
        "（丁目は漢数字、「ヶ」「ケ」、全角と半角、空白）",
        "- 構造は 木造 / S造 / RC造 / SRC造 / その他 の5つにそろえてから比べる",
        "- 日付は年月までしか読めなかった場合、年月が合っていれば一致とみなす",
        "- 高さは3つ（高さ・塔屋等まで・軒高）を別々に比べる。看板と届出で呼び方がずれることが"
        "あるので、3Dに使う数字が合っているかは「3D表示に使う高さ」"
        "（最高の高さがあればそれ、なければ高さ）で別に見る",
        "- 一致率の分母は、看板と届出の両方に値があった項目だけ",
        f"- 「自信なし」は確信度 < {LOW_CONFIDENCE}、または状態が `unreadable`・`masked` の項目",
        "",
    ]
    SUMMARY_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------- 本体 ----------

def load_filings() -> list[dict]:
    with FILINGS_CSV.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def is_verified(row: dict) -> bool:
    return (row.get("verified") or "").strip().lower() in ("1", "true", "yes", "○")


def load_readings() -> list[tuple[str, list[dict]]]:
    """自分で撮った写真の読み取り結果を、写真ごとにまとめて返す。"""
    photos = []
    for path in sorted(READINGS_DIR.glob("*.json")):
        results = json.loads(path.read_text(encoding="utf-8"))
        results = [r for r in results if (r.get("source_image") or "").startswith(PHOTO_PREFIX)]
        if results:
            photos.append((path.stem, results))
    return photos


def main():
    filings = load_filings()
    readings = load_readings()
    print(f"届出 {len(filings)}件（目視確認済み {sum(map(is_verified, filings))}件）、"
          f"写真 {len(readings)}枚")

    # medium での読み直しがどれだけ効いたか（照合できなかった写真も含めて数える）
    rereads = []
    for photo, results in readings:
        for result in results:
            done = result.get("reread")
            if done:
                rereads.append({"photo": photo, "fields": done["fields"],
                                "changed": done["changed"], "resolved": done.get("resolved"),
                                "remaining": [i["message"] for i in
                                              (result.get("consistency_issues") or [])]})

    matched, unmatched, other, unreadable = [], [], [], []
    for photo, results in readings:
        candidates = [r for r in results
                      if (r.get("fields") or {}).get("notice_type", {}).get("value")
                      in ("building", "demolition")]
        if not candidates:
            # 1枚に看板が複数写っていると同じ理由が並ぶので、重複を落とす
            reasons = dict.fromkeys(filter(None, (r.get("retake_reason") for r in results)))
            note = "、".join(reasons) or "判定できず"
            target = other if any(is_other_board(r) for r in results) else unreadable
            target.append({"photo": photo, "note": note})
            continue

        for result in candidates:
            row, key, note = match_filing(result, filings)
            if row:
                matched.append({
                    "photo": photo,
                    "read_image": result.get("read_image"),
                    "filing_no": row.get("filing_no"),
                    "match_key": key,
                    "verified": is_verified(row),
                    "page_image": row.get("page_image"),
                    "filing_uncertain": row.get("uncertain"),
                    "consistency_issues": result.get("consistency_issues") or [],
                    "comparisons": compare_photo(result, row),
                })
                break
        else:
            unmatched.append({"photo": photo, "note": note or "どの鍵でも当たらなかった"})

    report = {
        "low_confidence_threshold": LOW_CONFIDENCE,
        "filings": len(filings),
        "filings_verified": sum(map(is_verified, filings)),
        "photo_counts": {
            "total": len(readings),
            "matched": len(matched),
            "unmatched": len(unmatched),
            "other_board": len(other),
            "unreadable": len(unreadable),
        },
        "match_keys": dict(Counter(m["match_key"] for m in matched)),
        "rereads": rereads,
        "reread_counts": {
            "photos": len({r["photo"] for r in rereads}),
            "changed_a_value": sum(1 for r in rereads if r["changed"]),
            "resolved": sum(1 for r in rereads if r["resolved"]),
        },
        "all": aggregate(matched),
        "verified": aggregate([m for m in matched if m["verified"]]),
        "unverified": aggregate([m for m in matched if not m["verified"]]),
        "photos_unmatched": unmatched,
        "photos_other": other,
        "photos_unreadable": unreadable,
    }

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    (EVAL_DIR / "per_photo.json").write_text(
        json.dumps(matched, ensure_ascii=False, indent=2), encoding="utf-8")
    (EVAL_DIR / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(report)

    t = report["all"]["totals"]
    print(f"照合できた {len(matched)}枚 / 別の看板 {len(other)}枚 / "
          f"照合できず {len(unmatched)}枚 / 読めず {len(unreadable)}枚")
    print(f"一致 {t.get(MATCH, 0)} / 不一致 {t.get(MISMATCH, 0)} "
          f"（一致率 {percent(rate(t))}）")
    print(f"\n書き出しました → {EVAL_DIR}/per_photo.json, {EVAL_DIR}/summary.json, {SUMMARY_MD}")


if __name__ == "__main__":
    main()
