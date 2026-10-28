"""写真1枚を最後まで通す: SAMで切り出し → Muse Sparkで読み取り → 矛盾チェック。
結果は samples/out/<写真の名前>.json にも保存する。

使い方:
    python pipeline/process.py samples/sendai.png
"""
import json
import sys
from pathlib import Path

from crop_board import OUT_DIR, crop_boards
from read_board import check_consistency, needs_retake, read_board

image_path = Path(sys.argv[1])
prompt = sys.argv[2] if len(sys.argv) > 2 else "notice board"

paths, found = crop_boards(image_path, prompt)
if not found:
    print("看板が見つからなかったため、画像全体を読みます")

results = []
for p in paths:
    r = read_board(p)
    r["consistency_issues"] = check_consistency(r.get("fields", {}))
    r["needs_retake"] = needs_retake(r)
    r["source_image"] = str(image_path)
    r["read_image"] = str(p)
    r["board_detected"] = found
    results.append(r)

OUT_DIR.mkdir(parents=True, exist_ok=True)
out = OUT_DIR / f"{image_path.stem}.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
print(json.dumps(results, ensure_ascii=False, indent=2))
print(f"\n保存しました → {out}")
