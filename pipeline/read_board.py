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

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"])

FIELDS = {
    "notice_type": "看板の種類。'building'（建築計画）か 'demolition'（解体工事）",
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

PROMPT = f"""これは日本の工事現場に掲示される「建築計画のお知らせ」または「解体工事計画のお知らせ」の看板の画像です。
次の項目を読み取り、JSONだけを返してください。前置きやコードブロックは付けないでください。

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
    "raw": 「敷地の地名地番」欄の文字をそのまま,
    "area": 地番より前の地名（例: "仙台市若林区大和町四丁目"）,
    "parcels": [{{"number": 地番（例: "502-4"）, "partial": 「の一部」と書かれていればtrue}}],
    "has_others": 「ほか」「外」などで他にも筆があると書かれていればtrue
  }}
}}

ルール:
- 推測で埋めない。読めないものは "unreadable" にする。
- 地番は住居表示ではなく「地名地番」の欄から取る。
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
    return json.loads(text)


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


def needs_retake(result: dict) -> list[str]:
    """読めなかった項目（撮り直しを頼む材料）の名前を返す。"""
    names = [k for k, f in result.get("fields", {}).items() if f.get("status") == "unreadable"]
    if result.get("lot", {}).get("status") == "unreadable":
        names.append("lot")
    return names


if __name__ == "__main__":
    result = read_board(Path(sys.argv[1]))
    result["consistency_issues"] = check_consistency(result.get("fields", {}))
    result["needs_retake"] = needs_retake(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
