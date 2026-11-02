# Before It's Built

工事の標識（建築計画のお知らせ）を撮ると、その建物が建つ前の姿を
PLATEAU の3D都市モデルの上に立ち上げる。

**作業の前に `docs/dev-policy.md` を読むこと。** 詳しい方針はそちらにある。
**方針の正本はこの1ファイルだけ。** 方針を変えるときは、ファイルを差し替えるのではなく
`docs/dev-policy.md` を直接編集する（巻き戻りを防ぐため）。

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
| `process.py` | 写真1枚を切り出し→読み取り→検算まで通す（順番はこちらが決める） |
| `agent.py` | 同じ部品をツールとして渡し、**次の手をモデルが決める**（手順と実測は `docs/agent.md`） |
| `download_filings.py` | 目黒区の標識設置届PDFをオープンデータから取る |
| `extract_filings.py` | 届出PDFを1ページ1件として読み、確認用CSVにする |
| `evaluate.py` | 読み取り結果を届出と照合し、項目ごとの精度を数える |
| `make_verify_page.py` | 届出の目視確認用のページ（`data/eval/verify.html`）を作る |
| `make_planned_geojson.py` | 届出から計画建物のボリュームを作る（`web/public/planned.geojson`） |
| `make_demolition_geojson.py` | 解体名簿から壊される建物をPLATEAUの中で絞る（`web/public/demolition.json`） |

**比べる前に必ず `normalize.py` を通す。** 看板側と届出側を違う規則でそろえると
精度が測れない。そろえ方を変えるときはこのファイルだけを直す。
構造は 木造／S造／RC造／SRC造／その他 の5つ、元の文字は `raw` に残る。

`notice_type` には `other`（建築計画のお知らせではない看板）がある。
工事現場には労災保険関係成立票や建設業の許可票も並んでいるため、
全項目が `absent` になるだけでは別の看板だと判定できない。

**高さは3つ**（`height_m` ＝ 高さ欄の上段、`max_height_m` ＝ カッコ内の塔屋等まで、
`eave_height_m` ＝ カッコ内の軒高）。目黒区の看板は
「高さ ○m（塔屋等まで ○m、軒高 ○m）」の書式で、**軒高を `height_m` に入れる取り違え**を
一度やっている。検算の「軒高 ≤ 高さ ≤ 最高の高さ」で捕まえる。
3D表示は `max_height_m` があればそれ、なければ `height_m`（軒高は使わない）。
看板によっては法上の高さが書かれておらず軒高と最高だけのこともあるので、
評価では3つを別々に比べたうえで「3D表示に使う高さ」も別に比べる。

検算に引っかかった項目は、その項目だけを `medium` で読み直す（`read_and_check()`）。
読み直しても矛盾が残れば「要確認」として残す。**矛盾を消すために値を作らせない。**
**ほかの欄からの逆算も禁止**（「9階建てだから30m台のはず」）。桁が潰れていれば `unreadable`。
これをやられると**検算を通ってしまう**ので、`agent.py` の `extrude()` は
高さ・建築面積の確信度が `0.9` 未満なら建物を立てない。
境目の `0.9` は `read_board.py` の `LOW_CONFIDENCE` が正本（`evaluate.py` もそれを使う）。

`agent.py` は段階0〜2とは別に、**判断の記録**を `data/agent/<写真>.json` に残す
（呼んだツール、入力、結果、モデルの短い理由）。サンプルモードはこれを画面に流す。
モデルに値を比べさせない。`lookup_filing()` が `normalize.py` を通したうえで
項目ごとの一致・不一致をコードで計算して返す（`evaluate.py` と同じ規則）。

**`web/`** — NAVARA (`@navaramap/three`) + PLATEAU公式配信の3D Tiles。
ブラウザで表示確認済み（`npm run dev`）。
**tileset の URL は `web/src/tilesets.ts` に集約**。他の場所に書かない。
目黒区は再現性のため2025年度に固定してある。
`src/stage0.ts` は段階0（計画建物＝青／解体建物＝オレンジ）。`?stage=0` で出る。
`src/stage1.ts` は段階1（届出から出した計画建物＝青）。`?stage=1` で出る。
`src/stage2.ts` は段階2（＋解体建物＝オレンジ）。`?stage=2` で出る。手順は `docs/stage2.md`。
**公開URL: https://0319-2004.github.io/before-its-built/** （`main` への push で自動デプロイ）。

解体の特定は、号の位置が無いので**街区の中の候補の順位付け**でしかない。
`confident` が false のときは1棟に決めず、候補を薄く並べる。
PLATEAUの階数は当てにならない（30.8mの建物に「2階」が入っている）ので
高さからの推定と併用する。`fireproofStructureType` の「準耐火」は
「耐火」を文字として含むので部分一致で見ない。
**太陽の位置は既定が今の時刻で、夜に開くと建物が真っ黒になる**
（地図のラスタだけが見えて「タイルが出ていない」ように見える）。
`?time=10` で現地の視太陽時を渡す。手順と実測は `docs/stage1.md`。

`planned.geojson` は **`web/public/`** に直接書き出す（公開される）。
`data/` に出してから手でコピーしない。入れてよいものかは
`make_planned_geojson.py` の `check_publishable()` がコードで確かめ、
通らなければ書き出さずに止まる（確認前の印・出どころ・項目の許可リスト）。

**公開するものは `npm run build && npm run preview` の側で確かめる。**
dev では出るのにビルドでは街並みが丸ごと消える、という失敗を一度している
（NAVARA 同梱の Draco の WASM が出力に入らない。`vite.config.ts` で入れ直し済み）。

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

段階1（青）と段階2（オレンジ）とエージェント化は済み。次は優先順に:

- ~~サンプルモードの青い箱の比率を確かめる~~（済）。原因は**高さの渡し方**で、
  段階1・段階0・サンプルに共通だった。NAVARA の `extrudedHeight` は
  **底面からの相対の押し出し量**（「Extrusion height for polygons in meters」）で、
  絶対の天面高ではない。`base_z + 高さ`（≈66m）を渡していたので**全棟が塔**になり、
  ずんぐりした建物ほど縦長に見えた。`extrudedHeight: 高さ` に直した（`height` が底面）。
  真上から撮るとフットプリントは段階1もサンプルも完全な正方形（W/H=1.00）で、
  形状データは正しかった。段階1と撮り比べる計測は `?mode=sample&freecam=1` で
  URL のカメラ（`?lng/lat/height/pitch/heading`）をそのまま使える。
  ついでに `fast=1` の一発表示が `addLayer` 直後の同期 `update()` で高さを
  取りこぼしていたのも直した（生成直後の同期 update は落ちる。rAF 経由のアニメは効く）。
  実測と確認は `docs/stage1.md`
- 日影（冬至・地上1.5m。太陽は `view.atmosphere.setSolarTime()`）
- **評価の取り直し**。`evaluate.py` は `process.py` の出力を見ているので
  エージェントの結果ではまだ測っていない。逆算禁止で読み取りの指示も変わった
- 形を最寄りの道路に沿わせる（今は北向きの正方形。`tran.geojson` を使う）
- 届出の目視確認（`verify.html`。まだ0件なので全部「確認前」のまま）
- 中目黒3-5-8 のように解体が1棟に決まらない件の見せ方
