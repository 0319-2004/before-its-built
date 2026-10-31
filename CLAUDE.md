# Before It's Built

工事の標識（建築計画のお知らせ）を撮ると、その建物が建つ前の姿を
PLATEAU の3D都市モデルの上に立ち上げる。

**作業の前に `docs/dev-policy.md` を読むこと。** 詳しい方針はそちらにある。

## 決まりごと

- Python を動かす前に仮想環境を有効にする: `source .venv/bin/activate`
- **`data/` `samples/` `.env` はコミットしない**（`.gitignore` 済み）。
  中身を他の場所にコピーして回避しない
- **個人名を保存しない。** 標識や届出に載っている建築主・設計者・施工者・
  担当者の氏名、住所、電話番号は、抽出もファイル出力もしない
- Meta のモデルは **`-contributor` なしのID** を使う
  （`muse-spark-1.3` / `sam-3.1` / `muse-image-1.0`）。
  看板の写真に個人名が写るため

## 今の状態

**`pipeline/`** — Python。`https://api.meta.ai/v1` 経由でモデルを呼ぶ。

| スクリプト | 役割 |
|---|---|
| `check_api.py` | 必要なモデルが使えるか確認する |
| `crop_board.py` | SAM 3.1 で写真から看板を切り出す |
| `read_board.py` | 看板を Muse Spark 1.3 で読み、矛盾はコードで検算する |
| `process.py` | 写真1枚を切り出し→読み取り→検算まで通す |
| `download_filings.py` | 目黒区の標識設置届PDFをオープンデータから取る |
| `extract_filings.py` | 届出PDFを1ページ1件として読み、確認用CSVにする |

**`web/`** — NAVARA (`@navaramap/three`) + PLATEAU公式配信の3D Tiles。
ブラウザで表示確認済み（`npm run dev`）。
**tileset の URL は `web/src/tilesets.ts` に集約**。他の場所に書かない。
目黒区は再現性のため2025年度に固定してある。

**`scripts/convert_plateau.sh`** — CityGML を GeoJSON に変換する。
用途は**サーバー側の計算専用**（`data/PLATEAU/bldg.geojson` 14,334棟、`tran.geojson`）。
同時に出る自前の3D Tiles は表示に使わない。公式配信が落ちたときの退避先も、
自前変換ではなく公式のテクスチャ無しデータ（33.5MB）を置き直す。
手順と出典は `docs/data-prep.md`。

## 次にやること

計画建物（青）と解体建物（オレンジ）を3Dで重ねて表示する。
