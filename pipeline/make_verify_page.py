"""届出の目視確認を楽にするページを作る。

写真と照合できた届出の行だけを並べ、1行につき
「届出のページ画像」「看板の切り出し画像」「両方の読み取り値」を横に並べる。
ブラウザで見ながら確認済みの印と気づいたことを付けられる。

先に `python pipeline/evaluate.py` を動かしておく。

    python pipeline/make_verify_page.py
    open data/eval/verify.html

出力は `data/eval/verify.html`。届出のスキャンと建物の名称が写るので**リポジトリに入れない**。
付けた印は画面の中（ブラウザのlocalStorage）に残り、「CSVを書き出す」で
`filing_no,verified,notes` の形で取り出せる。
"""
import html
import json
from pathlib import Path

EVAL_DIR = Path("data/eval")
PER_PHOTO = EVAL_DIR / "per_photo.json"
OUT_HTML = EVAL_DIR / "verify.html"

# verify.html から見た画像の置き場所（data/eval/ からの相対パス）
PAGES_DIR = "../meguro/pages"
BOARDS_DIR = "../../samples/out"

OUTCOME_CLASS = {"一致": "ok", "不一致": "bad", "看板だけ": "warn", "届出だけ": "warn",
                 "両方なし": "none"}

STYLE = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, "Hiragino Sans", sans-serif; margin: 0 auto; padding: 1.5rem;
       max-width: 1100px; line-height: 1.6; }
h1 { font-size: 1.4rem; }
.intro { background: #f4f4f5; padding: .8rem 1rem; border-radius: 8px; font-size: .9rem; }
.row { border: 1px solid #ddd; border-radius: 10px; padding: 1rem; margin: 1.5rem 0; }
.row.done { border-color: #2a7; background: #f3fbf6; }
.head { display: flex; align-items: baseline; gap: .8rem; flex-wrap: wrap; }
.head h2 { font-size: 1.15rem; margin: 0; }
.imgs { display: flex; gap: .8rem; margin: .8rem 0; }
.imgs figure { margin: 0; flex: 1; min-width: 0; }
.imgs img { width: 100%; border: 1px solid #ccc; border-radius: 6px; background: #fff; }
.imgs figcaption { font-size: .8rem; color: #666; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { border-bottom: 1px solid #eee; padding: .35rem .5rem; text-align: left;
         vertical-align: top; }
th { background: #fafafa; font-weight: 600; }
td.v { font-variant-numeric: tabular-nums; }
tr.bad { background: #fff1f0; }
tr.warn { background: #fffaf0; }
tr.none { color: #999; }
.tag { font-size: .75rem; padding: .1rem .4rem; border-radius: 4px; background: #eee; }
.tag.bad { background: #fdd; } .tag.warn { background: #fe9; } .tag.ok { background: #dfd; }
.tag.low { background: #e5e0ff; }
.mark { display: flex; align-items: center; gap: .6rem; margin-top: .8rem; }
.mark input[type=text] { flex: 1; padding: .3rem; }
.out { width: 100%; height: 7rem; font-family: ui-monospace, monospace; font-size: .8rem; }
button { padding: .4rem .8rem; font-size: .9rem; }
"""

SCRIPT = """
const KEY = 'verify-state-v1';
const state = JSON.parse(localStorage.getItem(KEY) || '{}');

function render() {
  document.querySelectorAll('.row').forEach(row => {
    const no = row.dataset.no, s = state[no] || {};
    row.querySelector('.chk').checked = !!s.verified;
    row.querySelector('.note').value = s.notes || '';
    row.classList.toggle('done', !!s.verified);
  });
  const lines = ['filing_no,verified,notes'];
  Object.keys(state).sort((a, b) => a - b).forEach(no => {
    const s = state[no];
    if (s.verified || s.notes) {
      lines.push(`${no},${s.verified ? 1 : ''},"${(s.notes || '').replace(/"/g, '""')}"`);
    }
  });
  document.querySelector('.out').value = lines.join('\\n');
  const done = Object.values(state).filter(s => s.verified).length;
  document.querySelector('.count').textContent = done;
}

function save(no, patch) {
  state[no] = Object.assign({}, state[no], patch);
  localStorage.setItem(KEY, JSON.stringify(state));
  render();
}

document.querySelectorAll('.row').forEach(row => {
  const no = row.dataset.no;
  row.querySelector('.chk').addEventListener('change', e => save(no, {verified: e.target.checked}));
  row.querySelector('.note').addEventListener('input', e => save(no, {notes: e.target.value}));
});

document.querySelector('.download').addEventListener('click', () => {
  const blob = new Blob([document.querySelector('.out').value], {type: 'text/csv'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'verified.csv';
  a.click();
});

render();
"""


def value_text(value) -> str:
    if value is None or value == "" or value == []:
        return "—"
    if isinstance(value, list):
        return "、".join(str(v) for v in value)
    return str(value)


def row_html(entry: dict) -> str:
    no = html.escape(str(entry.get("filing_no")))
    board_image = Path(entry.get("read_image") or "").name
    page_image = entry.get("page_image") or ""
    uncertain = (entry.get("filing_uncertain") or "").strip()

    mismatches = sum(1 for c in entry["comparisons"] if c["outcome"] == "不一致")
    issues = [i["message"] if isinstance(i, dict) else str(i)
              for i in (entry.get("consistency_issues") or [])]

    rows = []
    for c in entry["comparisons"]:
        if c["outcome"] == "両方なし":
            continue
        tags = []
        if c["low_confidence"]:
            tags.append('<span class="tag low">看板:自信なし</span>')
        if c["filing_uncertain"]:
            tags.append('<span class="tag low">届出:自信なし</span>')
        rows.append(
            f'<tr class="{OUTCOME_CLASS[c["outcome"]]}">'
            f'<td>{html.escape(c["label"])}</td>'
            f'<td class="v">{html.escape(value_text(c["board"]))}</td>'
            f'<td class="v">{html.escape(value_text(c["filing"]))}</td>'
            f'<td><span class="tag {OUTCOME_CLASS[c["outcome"]]}">'
            f'{html.escape(c["outcome"])}</span> {" ".join(tags)}</td></tr>')

    parts = [f'<div class="row" data-no="{no}">',
             '<div class="head">',
             f'<h2>届出 No.{no}</h2>',
             f'<span class="tag">{html.escape(entry["photo"])}</span>',
             f'<span class="tag">照合: {html.escape(str(entry["match_key"]))}</span>']
    parts.append(f'<span class="tag {"bad" if mismatches else "ok"}">不一致 {mismatches}件</span>')
    if uncertain:
        parts.append(f'<span class="tag low">届出が自信なしにした項目: '
                     f'{html.escape(uncertain)}</span>')
    parts.append("</div>")

    if issues:
        parts.append('<p class="tag warn">検算: ' +
                     html.escape("、".join(issues)) + "</p>")

    parts.append('<div class="imgs">')
    if page_image:
        src = f"{PAGES_DIR}/{html.escape(page_image)}"
        parts.append(f'<figure><a href="{src}" target="_blank"><img src="{src}" alt=""></a>'
                     f"<figcaption>届出のページ（{html.escape(page_image)}）</figcaption></figure>")
    if board_image:
        src = f"{BOARDS_DIR}/{html.escape(board_image)}"
        parts.append(f'<figure><a href="{src}" target="_blank"><img src="{src}" alt=""></a>'
                     f"<figcaption>看板（{html.escape(board_image)}）</figcaption></figure>")
    parts.append("</div>")

    parts.append("<table><thead><tr><th>項目</th><th>看板の読み取り</th>"
                 "<th>届出の読み取り</th><th>判定</th></tr></thead><tbody>")
    parts += rows
    parts.append("</tbody></table>")

    parts.append('<div class="mark">'
                 f'<label><input type="checkbox" class="chk"> 確認済み（verified=1）</label>'
                 '<input type="text" class="note" placeholder="気づいたこと（notes）">'
                 "</div></div>")
    return "\n".join(parts)


def main():
    entries = json.loads(PER_PHOTO.read_text(encoding="utf-8"))
    entries.sort(key=lambda e: int(e.get("filing_no") or 0))

    body = "\n".join(row_html(e) for e in entries)
    page = f"""<!DOCTYPE html>
<meta charset="utf-8">
<title>届出の目視確認（{len(entries)}件）</title>
<style>{STYLE}</style>
<h1>届出の目視確認（写真と照合できた {len(entries)}件）</h1>
<div class="intro">
  <p>左が区の届出のスキャン、右が看板の切り出し。表は<strong>どちらもモデルが読んだ値</strong>で、
  まだ正解ではない。届出の画像を見て、届出側の値が合っていれば「確認済み」に印を付ける。
  間違っていれば notes に正しい値を書く。</p>
  <p><strong>不一致</strong>と<strong>自信なし</strong>の行から先に見ると早い。
  印は自動でブラウザに残る。確認済み <strong class="count">0</strong>件。</p>
  <p>下の欄が `filing_no,verified,notes` のCSVになる。
  <code>data/meguro/filings_extracted.csv</code> の同じ列に貼る。
  <strong>注意：<code>extract_filings.py</code> を回し直すとこの2列は空に戻る。</strong></p>
  <textarea class="out" readonly></textarea>
  <button class="download">verified.csv を書き出す</button>
</div>
{body}
<script>{SCRIPT}</script>
"""
    OUT_HTML.write_text(page, encoding="utf-8")
    print(f"{len(entries)}件のページを作りました → {OUT_HTML}")
    print("ブラウザで開く: open data/eval/verify.html")


if __name__ == "__main__":
    main()
