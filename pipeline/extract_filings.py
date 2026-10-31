"""目黒区の標識設置届（スキャン画像のPDF）を、1ページ＝1件として Muse Spark 1.3 で読み取り、
目視確認用の CSV にまとめる。個人名・電話番号は読み取らない。

使い方:
    pip install pypdfium2
    python pipeline/extract_filings.py

出力:
    data/meguro/pages/       … 各ページの画像（目視確認で見比べる用）
    data/meguro/extracted/   … 各ページの読み取り結果（JSON。再実行時は読み取り済みを飛ばす）
    data/meguro/filings_extracted.csv … 全件の一覧（Numbers/Excelで開いて確認する）
"""
import base64
import csv
import io
import json
import os
from pathlib import Path

import pypdfium2 as pdfium
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"])

PDF_DIR = Path("data/meguro/filings")
PAGE_DIR = Path("data/meguro/pages")
JSON_DIR = Path("data/meguro/extracted")
CSV_PATH = Path("data/meguro/filings_extracted.csv")

COLUMNS = {
    "is_filing": "このページが標識設置届・変更届の表面なら true。裏面や別の書類なら false（以下はすべてnull）",
    "filing_type": "'設置' か '変更'（丸で囲まれている方）",
    "filing_no": "右下の丸囲みの受付番号（整数）",
    "sign_posted": "1 標識設置日（YYYY-MM-DD、和暦は西暦に直す。令和8年＝2026年）",
    "building_name": "2 建築物の名称",
    "lot_raw": "5(1) 地名地番の欄の文字をそのまま",
    "address_raw": "5(2) 住居表示の欄の文字をそのまま",
    "address_chome": "住居表示の町丁目（例: '上目黒三丁目'）",
    "address_block": "住居表示の街区番号（例: '19番' なら 19）。整数",
    "zoning": "5(3) 用途地域で丸が付いているもの（例: '一低'）",
    "use": "6(1) 主要用途",
    "work_type": "6(2) 工事種別で丸が付いているもの",
    "units_total": "6(3) 戸数の全体（「全」の欄）。整数",
    "height_m": "6(4) 高さ（上段の数値）",
    "max_height_m": "6(4) カッコ内の「塔屋等まで」の高さ。「塔屋等なし」なら null",
    "eave_height_m": "6(4) カッコ内の軒高",
    "floors_above": "6(5) 階数の地上。整数",
    "floors_below": "6(5) 階数の地下。整数",
    "structure": "6(6) 構造",
    "site_area_m2": "6(8) 敷地面積の合計",
    "building_area_m2": "6(9) 建築面積の「計画に係る部分」",
    "total_floor_area_m2": "6(10) 延べ面積の「計画に係る部分」",
    "construction_start": "7 着工予定（YYYY-MM-DD）",
    "construction_end": "8 完了予定（YYYY-MM-DD）",
}

PROMPT = f"""これは東京都目黒区の「標識設置届」（中高層建築物の建築計画の届出）をスキャンした画像です。
次の項目を読み取り、JSONオブジェクトだけを返してください。前置きやコードブロックは付けないでください。

{json.dumps(COLUMNS, ensure_ascii=False, indent=2)}

ルール:
- 読めない、または空欄の項目は null にする。推測で埋めない。
- 数値は単位を付けず数値で返す（例: 8.72）。
- 選択肢に丸を付ける形式の項目は、丸が付いているものを返す。
- 建築主・設計者・施工者・担当者の氏名、住所、電話番号は一切出力しない。
- 自信がない項目があれば、"uncertain" に項目名の配列として入れる。
"""


def page_images(pdf_path: Path):
    """PDFの各ページを画像にする（スキャンの細かい文字が読めるよう約200dpi）。"""
    pdf = pdfium.PdfDocument(pdf_path)
    for i in range(len(pdf)):
        yield i + 1, pdf[i].render(scale=200 / 72).to_pil().convert("RGB")


def read_page(img) -> dict:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    data_url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    response = client.responses.create(
        model="muse-spark-1.3",
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": PROMPT},
                {"type": "input_image", "image_url": data_url},
            ],
        }],
        reasoning={"effort": "medium"},  # 手書きや丸囲みがあるので、看板より少し丁寧に読ませる
        store=False,
    )
    text = response.output_text.strip().removeprefix("```json").removesuffix("```").strip()
    return json.loads(text)


def main():
    PAGE_DIR.mkdir(parents=True, exist_ok=True)
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    print(f"{len(pdfs)}個のPDFを処理します")

    for pdf_path in pdfs:
        for page_no, img in page_images(pdf_path):
            key = f"{pdf_path.stem}_p{page_no}"
            json_path = JSON_DIR / f"{key}.json"
            if json_path.exists():
                continue
            img.save(PAGE_DIR / f"{key}.jpg", quality=85)
            try:
                result = read_page(img)
            except Exception as e:  # 1ページの失敗で全体を止めない
                print(f"  失敗: {key} ({e})")
                continue
            result.update({"source_pdf": pdf_path.name, "page": page_no, "page_image": f"{key}.jpg"})
            json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print(f"  読み取り: {key}  No.{result.get('filing_no')}  {result.get('building_name')}")

    rows = [json.loads(p.read_text()) for p in sorted(JSON_DIR.glob("*.json"))]
    rows = [r for r in rows if r.get("is_filing")]
    rows.sort(key=lambda r: (r.get("sign_posted") or "", r.get("filing_no") or 0))

    fields = list(COLUMNS) + ["uncertain", "source_pdf", "page", "page_image", "verified", "notes"]
    # utf-8-sig にしておくと、Excelで開いても日本語が文字化けしない
    with CSV_PATH.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            r["uncertain"] = ",".join(r.get("uncertain") or [])
            w.writerow(r)
    print(f"\n{len(rows)}件を書き出しました → {CSV_PATH}")


if __name__ == "__main__":
    main()
