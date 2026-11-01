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
| `normalize.py` | 表記のゆれをそろえる（丁目の漢数字、「ヶ」「ケ」、構造、地番） |
| `process.py` | 写真1枚を切り出し→読み取り→検算まで通す |
| `download_filings.py` | 目黒区の標識設置届PDFをオープンデータから取る |
| `extract_filings.py` | 届出PDFを1ページ1件として読み、確認用CSVにする |
| `evaluate.py` | 読み取り結果を届出と照合し、項目ごとの精度を数える |

**比べる前に必ず `normalize.py` を通す。** 看板側と届出側を違う規則でそろえると
精度が測れない。そろえ方を変えるときはこのファイルだけを直す。
構造は 木造／S造／RC造／SRC造／その他 の5つ、元の文字は `raw` に残る。

`notice_type` には `other`（建築計画のお知らせではない看板）がある。
工事現場には労災保険関係成立票や建設業の許可票も並んでいるため、
全項目が `absent` になるだけでは別の看板だと判定できない。

**`web/`** — NAVARA (`@navaramap/three`) + PLATEAU公式配信の3D Tiles。
ブラウザで表示確認済み（`npm run dev`）。
**tileset の URL は `web/src/tilesets.ts` に集約**。他の場所に書かない。
目黒区は再現性のため2025年度に固定してある。
`src/stage0.ts` は段階0（計画建物＝青／解体建物＝オレンジ）。`?stage=0` で出る。

**3Dに置く高さは楕円体高**。標高（b3dmの`_zmin`など）を使うと目黒区では
36.7m沈んで見えなくなる。`bldg.geojson` のZはそのまま使ってよい。
表示用の3D Tilesと計算用のGeoJSONは **`gml_id`** でつなぐ。
実測と確認の手順は `docs/stage0.md`。

**`scripts/convert_plateau.sh`** — CityGML を GeoJSON に変換する。
用途は**サーバー側の計算専用**（`data/PLATEAU/bldg.geojson` 14,334棟、`tran.geojson`）。
同時に出る自前の3D Tiles は表示に使わない。公式配信が落ちたときの退避先も、
自前変換ではなく公式のテクスチャ無しデータ（33.5MB）を置き直す。
手順と出典は `docs/data-prep.md`。

## 次にやること

段階0（手で作った1棟）は済んだ。次は実データにつなぐ:
届出から出した位置と規模で計画建物を立て、解体名簿の住所から
解体される建物を `bldg.geojson` で特定してオレンジにする。
