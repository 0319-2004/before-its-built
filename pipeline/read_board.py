"""看板の画像を Muse Spark 1.3 で読み取り、決まった形の JSON にする。
読み取った数値が矛盾していないかは、モデルに任せずコードで確かめる。

使い方:
    python pipeline/read_board.py samples/out/sendai_board0.jpg
"""
import base64
import json
import mimetypes
import os
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
    "use": "主要用途",
    "site_area_m2": "敷地面積（数値のみ）",
    "building_area_m2": "建築面積（数値のみ。2段書きなら上段の今回の計画分）",
    "total_floor_area_m2": "延べ面積（数値のみ。2段書きなら上段）",
    "structure": "構造",
    "floors_above": "地上の階数（整数）",
    "floors_below": "地下の階数（整数。地下の欄に何も書かれていなければ0）",
    "height_m": "建築基準法上の高さ（数値のみ）",
    "max_height_m": "塔屋などを含む最高の高さ（カッコ内の数値）",
    "units": "住戸数（整数）",
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


def read_board(image_path: Path) -> dict:
    mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
    data_url = f"data:{mime};base64," + base64.b64encode(image_path.read_bytes()).decode()
    response = client.responses.create(
        model="muse-spark-1.3",
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": PROMPT},
                {"type": "input_image", "image_url": data_url},
            ],
        }],
        reasoning={"effort": "low"},
        store=False,
    )
    text = response.output_text.strip().removeprefix("```json").removesuffix("```").strip()
    return apply_normalization(json.loads(text))


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

    address = result.get("address") or {}
    if address.get("chome") is not None:
        address["chome"] = normalize.chome(address["chome"])
    if address.get("block") is not None:
        address["block"] = normalize.block_number(address["block"])
    if address.get("go") is not None:
        address["go"] = normalize.block_number(address["go"])
    return result


def check_consistency(fields: dict) -> list[str]:
    """読み取った数値どうしの大きな矛盾（桁や小数点の読み間違いなど）を探す。"""
    def val(name):
        f = fields.get(name) or {}
        return f.get("value") if f.get("status") == "read" else None

    issues = []
    site, bldg, total = val("site_area_m2"), val("building_area_m2"), val("total_floor_area_m2")
    floors, below, height = val("floors_above"), val("floors_below") or 0, val("height_m")
    max_height = val("max_height_m")
    if site and bldg and bldg > site:
        issues.append(f"建築面積 {bldg}㎡ が敷地面積 {site}㎡ より大きい")
    if bldg and total and total < bldg:
        issues.append(f"延べ面積 {total}㎡ が建築面積 {bldg}㎡ より小さい")
    if floors and height and not 2.0 <= height / floors <= 10.0:
        issues.append(f"1階あたり {height / floors:.1f}m で、高さと階数が合わない可能性")
    if height and max_height and max_height < height:
        issues.append(f"最高の高さ {max_height}m が建築基準法上の高さ {height}m より低い")
    if bldg and total and floors and total > bldg * (floors + below) * 1.1:
        issues.append("延べ面積が 建築面積×階数 を大きく超えている")
    return issues


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


if __name__ == "__main__":
    result = read_board(Path(sys.argv[1]))
    result["consistency_issues"] = check_consistency(result.get("fields", {}))
    result["needs_retake"] = needs_retake(result)
    result["retake_reason"] = retake_reason(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
