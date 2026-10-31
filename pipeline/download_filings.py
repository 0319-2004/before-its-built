"""目黒区の標識設置届（中高層）のPDFを、オープンデータカタログからまとめてダウンロードする。
出典: 目黒区「標識設置届」（CC BY） https://data.bodik.jp/dataset/hyousikisettitodoke

使い方:
    python pipeline/download_filings.py                 # 2026年4月（令和8年度）以降
    python pipeline/download_filings.py --since 2025-10 # 開始の年月を変える
    python pipeline/download_filings.py --kaitai        # 解体工事等の標識設置届も取る
"""
import argparse
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://data.bodik.jp/api/3/action/package_show?id={}"
DATASETS = {
    "hyousikisettitodoke": Path("data/meguro/filings"),   # 中高層の標識設置届
    "131105_kaitai": Path("data/meguro/kaitai"),          # 解体工事等の標識設置届出名簿
}
HEADERS = {"User-Agent": "before-its-built (hackathon research; contact via GitHub)"}


def fetch(url: str) -> bytes:
    url = urllib.parse.quote(url, safe=":/?&=%~")
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def resource_month(name: str):
    """リソース名の最初の「YYYY年M月」を (年, 月) で返す。なければ None。"""
    m = re.search(r"(\d{4})年\s*(\d{1,2})月", name)
    return (int(m.group(1)), int(m.group(2))) if m else None


def download_dataset(dataset_id: str, out_dir: Path, since: tuple[int, int], all_files: bool):
    meta = json.loads(fetch(API.format(dataset_id)))
    resources = meta["result"]["resources"]
    out_dir.mkdir(parents=True, exist_ok=True)

    picked = []
    for res in resources:
        name = res.get("name") or res.get("url", "").rsplit("/", 1)[-1]
        ym = resource_month(name)
        if not all_files and (ym is None or ym < since):
            continue
        picked.append((name, res["url"], ym))

    print(f"[{dataset_id}] 全{len(resources)}件中、{len(picked)}件を対象にします")
    index = []
    for name, url, ym in picked:
        safe = re.sub(r'[\\/:*?"<>|\s]+', "_", name)
        if not safe.lower().endswith(".pdf"):
            safe += ".pdf"
        path = out_dir / safe
        if path.exists():
            print(f"  済み: {safe}")
        else:
            path.write_bytes(fetch(url))
            print(f"  保存: {safe}")
            time.sleep(1)  # 相手のサーバーに負担をかけないよう間を空ける
        index.append({"name": name, "url": url, "file": str(path), "year_month": ym})

    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2))
    print(f"  一覧 → {out_dir / 'index.json'}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--since", default="2026-04", help="この年月以降のファイルを取る（YYYY-MM）")
    p.add_argument("--kaitai", action="store_true", help="解体工事等の標識設置届も取る")
    args = p.parse_args()
    y, m = map(int, args.since.split("-"))

    download_dataset("hyousikisettitodoke", DATASETS["hyousikisettitodoke"], (y, m), all_files=False)
    if args.kaitai:
        # 解体の名簿は「○○現在」の一覧形式なので、全ファイルを取る
        download_dataset("131105_kaitai", DATASETS["131105_kaitai"], (y, m), all_files=True)
