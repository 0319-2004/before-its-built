"""看板の画像を Muse Spark 1.3 で読み取り、決まった形の JSON にする。
読み取った数値が矛盾していないかは、モデルに任せずコードで確かめる。

使い方:
    python pipeline/read_board.py samples/out/sendai_board0.jpg
"""
import base64
import json
import mimetypes
import os
import re
import sys
from pathlib import Path

import normalize
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"])

# 建築計画のお知らせでない看板（労災保険関係成立票、建設業の許可票など）を撮ったときの案内。
OTHER_BOARD_MESSAGE = "建築計画のお知らせを撮ってください"

FIELDS = {
    "notice_type": "看板の種類。'building'（建築計画のお知らせ）、"
                   "'demolition'（解体工事のお知らせ）、"
                   "'other'（そのどちらでもない看板。労災保険関係成立票、建設業の許可票、"
                   "現場の案内板など）",
    "building_name": "建築物の名称",
    "use": "主要用途。戸数のカッコ書き（「(4戸)」など）は含めず、用途の名前だけ",
    "site_area_m2": "敷地面積（数値のみ）",
    "building_area_m2": "建築面積（数値のみ。2段書きなら上段の今回の計画分）",
    "total_floor_area_m2": "延べ面積（数値のみ。2段書きなら上段）",
    "structure": "構造",
    "floors_above": "地上の階数（整数）",
    "floors_below": "地下の階数（整数。地下の欄に何も書かれていなければ0）",
    "height_m": "高さの欄の上段の数値＝建築基準法上の高さ（数値のみ）",
    "max_height_m": "高さの欄のカッコ内の「塔屋等まで」の高さ（数値のみ）",
    "eave_height_m": "高さの欄のカッコ内の「軒高」（数値のみ）",
    "units": "住戸数・戸数（整数）。用途の欄に「共同住宅(4戸)」のようにカッコ書きされていれば、"
             "その数をここに入れる",
    "construction_start": "着工予定（YYYY-MM-DD。和暦は西暦に直す。日が不明ならYYYY-MM）",
    "construction_end": "完了予定（同上）",
    "notice_posted": "標識の設置年月日（同上）",
}

PROMPT = f"""これは日本の工事現場に掲示されている看板の画像です。
「建築計画のお知らせ」または「解体工事計画のお知らせ」であれば、次の項目を読み取ってください。
工事現場にはほかの看板（労災保険関係成立票、建設業の許可票、現場の案内板など）も並んでいます。
そのどちらでもなければ、notice_type を "other" にしてください。
JSONだけを返してください。前置きやコードブロックは付けないでください。

項目:
{json.dumps(FIELDS, ensure_ascii=False, indent=2)}

各項目には、次の4つの状態のどれか1つを付けてください。
- "read": 読めた
- "absent": 看板にその欄がない、または欄はあるが何も書かれていない
- "unreadable": 欄に何か書かれているが、反射・ぼけ・遠さ・隠れなどで読めない
- "masked": 「○○」「×」「△」などの伏せ字になっている

返す形:
{{
  "fields": {{
    項目名: {{"value": 値（read以外はnull）, "status": 状態, "confidence": 0.0〜1.0, "note": 短い説明またはnull}}
  }},
  "lot": {{
    "status": 状態,
    "confidence": 0.0〜1.0,
    "raw": 「敷地の地名地番」欄の文字のうち、地名地番の部分だけをそのまま,
    "area": 地番より前の地名（例: "仙台市若林区大和町四丁目"）,
    "parcels": [{{"number": 地番（例: "502-4"）, "partial": 「の一部」と書かれていればtrue}}],
    "has_others": 「ほか」「外」などで他にも筆があると書かれていればtrue
  }},
  "address": {{
    "status": 状態,
    "confidence": 0.0〜1.0,
    "raw": 住居表示の部分の文字をそのまま（例: "3丁目17番"）,
    "chome": 町丁目（例: "上目黒三丁目"）,
    "block": 街区番号の数字だけ（"17番" なら 17）,
    "go": 号の数字だけ（"3号" なら 3。号が書かれていなければ null）
  }}
}}

ルール:
- 推測で埋めない。読めないものは "unreadable" にする。
- **ほかの欄から逆算して数字を埋めてはいけない。** 「9階建てだから高さは30m台のはず」
  のように、階数や面積から辻褄の合う桁を決めるようなことはしない。
  桁の一部が潰れていて読めなければ、その項目は "unreadable" にする。
  ほかの欄と合っているかはこちらがコードで確かめるので、合わせようとしなくてよい。
- 高さの欄は「高さ ○m（塔屋等まで ○m、軒高 ○m）」の書式が多い。3つを取り違えないこと。
  上段の数値（カッコの外）→ height_m
  カッコ内の「塔屋等まで」「最高」→ max_height_m
  カッコ内の「軒高」→ eave_height_m
  **軒高を height_m に入れてはいけない。** 必ず 軒高 ≤ 高さ ≤ 塔屋等まで の順になる。
  カッコ内に「塔屋等なし」と書かれていれば max_height_m は "absent" にする。
- 戸数が用途の欄にカッコ書きされている場合（「共同住宅(4戸)」など）は、
  その数を units に入れ、use は「共同住宅」のように用途の名前だけにする。
- 地番（筆の番号）と住居表示は別のものなので、必ず分けて取る。
  同じ欄に「1760番22（住居表示:3丁目17番）」のように両方書かれていることがあるが、
  "lot" には地名地番だけ、"address" には住居表示だけを入れる。
  住居表示がどこにも書かれていなければ "address" の status は "absent" にする。
- 町丁目が住居表示の側に書かれていなければ、地名地番の町丁目を "address" の "chome" に入れてよい。
- 構造は看板に書かれている文字をそのまま入れる（言い換えや省略をしない）。
- notice_type が "other" のときは、他の項目・"lot"・"address" は読まなくてよい
  （省略してよい）。ただし "fields" の "notice_type" は上と同じ形のまま返す。
- 建築主・設計者・施工者・連絡先の氏名、住所、電話番号は一切出力しない。
"""


def close_brackets(body: str) -> str:
    """開いたままの括弧を閉じる。**足すのは閉じ括弧だけ**で、中身は補わない。

    文字列の中の括弧とエスケープは数えない。
    """
    stack, in_string, escaped = [], False, False
    for ch in body:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    return body + ('"' if in_string else "") + "".join(reversed(stack))


def parse_json(text: str) -> tuple[dict, bool]:
    """モデルの返事をJSONにする。閉じ括弧が足りないだけなら閉じて読み直す。

    notice_type が "other" の短い形を返すとき、閉じ括弧を1つ落とすことがある（実測）。
    **閉じ括弧を足す以外の手当てはしない。** 中身を補うと、読めなかったものを
    「読めた」ことにしてしまう。閉じて読めたかどうかは呼び出し側に返す。
    """
    body = text.strip().removeprefix("```json").removesuffix("```").strip()
    try:
        return json.loads(body), False
    except json.JSONDecodeError:
        pass
    repaired = close_brackets(body)
    if repaired != body:
        try:
            return json.loads(repaired), True
        except json.JSONDecodeError:
            pass
    raise ValueError("モデルの返事がJSONとして読めませんでした"
                     "（写真ではなくモデルの出力の問題）: " + body[:200])


def ask(image_path: Path, prompt: str, effort: str, attempts: int = 2) -> dict:
    mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
    data_url = f"data:{mime};base64," + base64.b64encode(image_path.read_bytes()).decode()
    failure = None
    for _ in range(attempts):
        response = client.responses.create(
            model="muse-spark-1.3",
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": data_url},
                ],
            }],
            reasoning={"effort": effort},
            store=False,
        )
        try:
            result, repaired = parse_json(response.output_text)
        except ValueError as e:
            failure = e
            continue
        if repaired:
            result["json_repaired"] = True
        return result
    raise failure


def read_board(image_path: Path) -> dict:
    return apply_normalization(ask(image_path, PROMPT, "low"))


REREAD_PROMPT = """これは日本の工事現場に掲示される「建築計画のお知らせ」の看板の画像です。
この看板を一度読み取ったところ、次の矛盾が見つかりました。

{issues}

そこで、下の項目だけをもう一度、注意して読み直してください。

{fields}

{heights}

JSONだけを返してください。前置きやコードブロックは付けないでください。

返す形:
{{
  "fields": {{
    項目名: {{"value": 値, "status": "read"|"absent"|"unreadable"|"masked",
             "confidence": 0.0〜1.0, "note": 短い説明またはnull}}
  }}
}}

ルール:
- 矛盾を解消するために値を作らない。看板に書いてあるとおりに読む。
- 読み直しても読めなければ "unreadable" にする。
- 建築主・設計者・施工者・連絡先の氏名、住所、電話番号は一切出力しない。
"""

# 高さの取り違えが疑わしいときだけ、書式の説明をもう一度見せる
HEIGHT_HINT = """高さの欄は「高さ ○m（塔屋等まで ○m、軒高 ○m）」の書式が多い。
上段の数値（カッコの外）が height_m、カッコ内の「塔屋等まで」が max_height_m、
カッコ内の「軒高」が eave_height_m。必ず 軒高 ≤ 高さ ≤ 塔屋等まで の順になる。"""

# 住居表示（address）・地名地番（lot）は fields ではなく別のブロックなので、
# 読み直しの形も別にする。届出と食い違ったときにここを読み直せるようにする
# （No.60 で看板3枚が「1番」、届出の読み取りは「19番」だった例）。
BLOCK_LABELS = {
    "address": "住居表示（町丁目・街区番号・号）",
    "lot": "地名地番（筆の番号）",
}

REREAD_BLOCK_PROMPT = """これは日本の工事現場に掲示される「建築計画のお知らせ」の看板の画像です。
この看板を一度読み取ったところ、次の確認が必要になりました。

{issues}

そこで、下の部分だけをもう一度、注意して読み直してください。

{which}

JSONだけを返してください。前置きやコードブロックは付けないでください。
読み直す部分だけを入れてください。

返す形:
{{
  "lot": {{"status": "read"|"absent"|"unreadable"|"masked", "confidence": 0.0〜1.0,
           "raw": 地名地番の部分の文字そのまま, "area": 地番より前の地名,
           "parcels": [{{"number": "502-4", "partial": false}}], "has_others": false}},
  "address": {{"status": "read"|"absent"|"unreadable"|"masked", "confidence": 0.0〜1.0,
               "raw": 住居表示の部分の文字そのまま（例: "3丁目17番"）,
               "chome": 町丁目（例: "上目黒三丁目"）, "block": 街区番号の数字だけ,
               "go": 号の数字だけ（なければ null）}}
}}

ルール:
- 矛盾や食い違いを解消するために値を作らない。看板に書いてあるとおりに読む。
- 地番（筆の番号）と住居表示は別のものなので、必ず分けて取る。
  同じ欄に併記されていても、"lot" には地名地番だけ、"address" には住居表示だけを入れる。
- 読み直しても読めなければ status を "unreadable" にする。
- 建築主・設計者・施工者・連絡先の氏名、住所、電話番号は一切出力しない。
"""


def merge_block(old: dict, new: dict) -> dict:
    """読み直したブロックを採り入れるが、**より詳しい値を捨てない**ようにする。

    読み直し（medium）は切り出しの都合で町丁目を短く読むことがある
    （「中目黒三丁目」→「三丁目」）。新しい値が古い値の一部でしかないときは、
    古いほうを残す。狙いの街区番号などは新しい値で更新される。
    """
    merged = dict(new)
    for key, old_val in old.items():
        if key == "first_read":
            continue
        new_val = new.get(key)
        if new_val in (None, "", []):
            merged[key] = old_val  # 新しい側が空なら古い値を残す
        elif (isinstance(old_val, str) and isinstance(new_val, str)
              and new_val in old_val and len(new_val) < len(old_val)):
            merged[key] = old_val  # 新しい側が古い値の一部（短く読んだ）なら古い値を残す
    return merged


def block_summary(name: str, block: dict | None) -> str | None:
    """住居表示・地名地番を短い文字列にする（読み直しの前後を比べる・記録する用）。"""
    if not block:
        return None
    if name == "address":
        if block.get("raw"):
            return block["raw"]
        parts = [block.get("chome")]
        if block.get("block") is not None:
            parts.append(f"{block['block']}番")
        if block.get("go") is not None:
            parts.append(f"{block['go']}号")
        return "".join(str(p) for p in parts if p) or None
    parcels = [p.get("number") for p in (block.get("parcels") or []) if p.get("number")]
    return "、".join(parcels) or block.get("raw")


# 読み直せるもの：通常の項目に加えて、住居表示（address）と地名地番（lot）のブロック。
REREADABLE = set(FIELDS) | set(BLOCK_LABELS)


def reread(image_path: Path, result: dict, issues: list[dict]) -> dict | None:
    """検算や届出との食い違いで引っかかった項目を、推論の強さ medium で読み直す（dev-policy §3）。

    読み直せるのは通常の項目（FIELDS）と、住居表示（address）・地名地番（lot）の
    ブロック。読み直した結果で `result` の該当箇所を差し替え、元の値は `first_read` に残す。
    何を読み直して何が変わったかを返す（読み直す対象がなければ None）。
    """
    names = sorted({name for issue in issues for name in issue["fields"]} & REREADABLE)
    if not names:
        return None

    field_names = [n for n in names if n in FIELDS]
    block_names = [n for n in names if n in BLOCK_LABELS]
    issue_text = "\n".join(f"- {issue['message']}" for issue in issues)
    changed = {}

    if field_names:
        prompt = REREAD_PROMPT.format(
            issues=issue_text,
            fields=json.dumps({n: FIELDS[n] for n in field_names},
                              ensure_ascii=False, indent=2),
            heights=HEIGHT_HINT if any(n.endswith("height_m") for n in field_names) else "",
        )
        second = apply_normalization(ask(image_path, prompt, "medium")).get("fields") or {}
        fields = result.setdefault("fields", {})
        for name in field_names:
            new = second.get(name)
            if not isinstance(new, dict) or "value" not in new:
                continue
            old = fields.get(name) or {}
            if old.get("value") != new.get("value"):
                changed[name] = {"from": old.get("value"), "to": new.get("value")}
            new["first_read"] = {k: old.get(k) for k in ("value", "status", "confidence")}
            fields[name] = new

    if block_names:
        prompt = REREAD_BLOCK_PROMPT.format(
            issues=issue_text,
            which="、".join(BLOCK_LABELS[n] for n in block_names),
        )
        second = apply_normalization(ask(image_path, prompt, "medium"))
        for name in block_names:
            new = second.get(name)
            if not isinstance(new, dict):
                continue
            old = result.get(name) or {}
            before = block_summary(name, old)
            merged = merge_block(old, new)
            after = block_summary(name, merged)
            if before != after:
                changed[name] = {"from": before, "to": after}
            merged["first_read"] = before
            result[name] = merged

    return {"effort": "medium", "reason": "consistency",
            "fields": names, "changed": changed}


def apply_normalization(result: dict) -> dict:
    """読んだ文字はモデル、決まった値にそろえるのはコード。元の文字は raw に残す。"""
    # 対象外の看板のとき、モデルが notice_type を文字列だけで返してくることがあるので形をそろえる
    fields = result.setdefault("fields", {})
    if isinstance(result.get("notice_type"), str):
        fields.setdefault("notice_type", result.pop("notice_type"))
    for name, field in list(fields.items()):
        if isinstance(field, str):
            fields[name] = {"value": field, "status": "read", "confidence": None, "note": None}

    structure = (result.get("fields") or {}).get("structure") or {}
    if structure.get("value") is not None:
        structure["raw"] = structure["value"]
        structure["value"] = normalize.structure(structure["raw"])

    # 戸数が用途の欄にカッコ書きされていたら units に移す（モデルが残してきたときの保険）
    use = fields.get("use") or {}
    if isinstance(use.get("value"), str):
        m = re.search(r"[(（]\s*(\d+)\s*戸\s*[)）]", use["value"])
        if m:
            use["raw"] = use["value"]
            use["value"] = re.sub(r"[(（]\s*\d+\s*戸\s*[)）]", "", use["value"]).strip()
            units = fields.setdefault("units", {})
            if units.get("value") is None:
                units.update({"value": int(m.group(1)), "status": "read",
                              "confidence": use.get("confidence"), "note": "用途の欄のカッコ書きから"})

    address = result.get("address") or {}
    if address.get("chome") is not None:
        address["chome"] = normalize.chome(address["chome"])
    if address.get("block") is not None:
        address["block"] = normalize.block_number(address["block"])
    if address.get("go") is not None:
        address["go"] = normalize.block_number(address["go"])
    return result


def check_consistency(fields: dict) -> list[dict]:
    """読み取った数値どうしの大きな矛盾（桁や小数点の読み間違いなど）を探す。

    どの項目が疑わしいかも返す。引っかかった項目だけを読み直すのに使う（reread を参照）。
    """
    def val(name):
        f = fields.get(name) or {}
        return f.get("value") if f.get("status") == "read" else None

    issues = []

    def add(message, *names):
        issues.append({"message": message, "fields": list(names)})

    site, bldg, total = val("site_area_m2"), val("building_area_m2"), val("total_floor_area_m2")
    floors, below, height = val("floors_above"), val("floors_below") or 0, val("height_m")
    max_height, eave = val("max_height_m"), val("eave_height_m")
    if site and bldg and bldg > site:
        add(f"建築面積 {bldg}㎡ が敷地面積 {site}㎡ より大きい", "building_area_m2", "site_area_m2")
    if bldg and total and total < bldg:
        add(f"延べ面積 {total}㎡ が建築面積 {bldg}㎡ より小さい",
            "total_floor_area_m2", "building_area_m2")
    if floors and height and not 2.0 <= height / floors <= 10.0:
        add(f"1階あたり {height / floors:.1f}m で、高さと階数が合わない可能性",
            "height_m", "floors_above")
    if height and max_height and max_height < height:
        add(f"最高の高さ {max_height}m が建築基準法上の高さ {height}m より低い",
            "max_height_m", "height_m")
    # 軒高 ≤ 高さ ≤ 最高の高さ。軒高を高さの箱に入れる取り違えを捕まえる
    if eave and height and eave > height:
        add(f"軒高 {eave}m が高さ {height}m より高い（取り違えの可能性）",
            "eave_height_m", "height_m")
    if eave and max_height and eave > max_height:
        add(f"軒高 {eave}m が最高の高さ {max_height}m より高い（取り違えの可能性）",
            "eave_height_m", "max_height_m")
    if bldg and total and floors and total > bldg * (floors + below) * 1.1:
        add("延べ面積が 建築面積×階数 を大きく超えている",
            "total_floor_area_m2", "building_area_m2", "floors_above")
    return issues


# モデルの自己申告の確信度が、これより低い項目を「自信なし」として扱う。
# 評価（evaluate.py）もこの境目を使うので、ここを正本にする。
LOW_CONFIDENCE = 0.9


def low_confidence_fields(result: dict) -> list[dict]:
    """モデルが自信を持てなかった項目。確信度が低いまま3Dに使わせないために返す。

    看板が遠い・潰れているときに、モデルが階数などから桁を補って
    「もっともらしい値」を書くことがある。そうなると数値どうしの検算
    （`check_consistency`）では捕まらないので、確信度の側で拾う。
    """
    low = []
    for name, field in (result.get("fields") or {}).items():
        if not isinstance(field, dict):
            continue
        confidence = field.get("confidence")
        if field.get("status") == "read" and confidence is not None \
                and confidence < LOW_CONFIDENCE:
            low.append({"field": name, "value": field.get("value"),
                        "confidence": confidence, "note": field.get("note")})
    return low


def is_other_board(result: dict) -> bool:
    """建築計画のお知らせでも解体工事のお知らせでもない看板か。"""
    notice_type = (result.get("fields") or {}).get("notice_type") or {}
    return notice_type.get("value") == "other"


def needs_retake(result: dict) -> list[str]:
    """読めなかった項目（撮り直しを頼む材料）の名前を返す。"""
    names = [k for k, f in result.get("fields", {}).items() if f.get("status") == "unreadable"]
    for block in ("lot", "address"):
        if (result.get(block) or {}).get("status") == "unreadable":
            names.append(block)
    return names


def retake_reason(result: dict) -> str | None:
    """撮り直しを頼む理由。頼む必要がなければ None。"""
    if is_other_board(result):
        return OTHER_BOARD_MESSAGE
    names = needs_retake(result)
    if names:
        return f"読めない項目があります（{'、'.join(names)}）。看板に近づいて撮り直してください"
    return None


def read_and_check(image_path: Path) -> dict:
    """読み取り → 検算 → 引っかかった項目だけ medium で読み直し → もう一度検算。

    読み直しても矛盾が残った項目は `consistency_issues` に残るので、
    「要確認」としてエージェントや利用者の判断にまわす（dev-policy §3・§4）。
    """
    result = read_board(image_path)
    issues = check_consistency(result.get("fields", {}))
    if issues and not is_other_board(result):
        done = reread(image_path, result, issues)
        if done:
            issues = check_consistency(result.get("fields", {}))
            done["resolved"] = not issues
            result["reread"] = done
    result["consistency_issues"] = issues
    result["needs_retake"] = needs_retake(result)
    result["retake_reason"] = retake_reason(result)
    return result


if __name__ == "__main__":
    print(json.dumps(read_and_check(Path(sys.argv[1])), ensure_ascii=False, indent=2))
