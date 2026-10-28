"""SAM 3.1 で写真の中の看板を探し、元の画像から高解像度のまま切り出す。

使い方:
    python pipeline/crop_board.py samples/sendai.png
    python pipeline/crop_board.py samples/sendai.png "white sign board"   # 探す言葉を変える
"""
import base64
import mimetypes
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image

load_dotenv()
client = OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"])

image_path = Path(sys.argv[1])
prompt = sys.argv[2] if len(sys.argv) > 2 else "notice board"

mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
data_url = f"data:{mime};base64," + base64.b64encode(image_path.read_bytes()).decode()

response = client.responses.create(
    model="sam-3.1",
    input=[{
        "role": "user",
        "content": [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": data_url},
        ],
    }],
    store=False,
)

raw = response.output_text
print("--- SAM 3.1 の生の出力（先頭500文字）---")
print(raw[:500])

# 出力の中の <|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..|> を全部拾う
boxes = re.findall(r"<\|box;([^|]*)\|>", raw)
if not boxes:
    sys.exit("看板が見つかりませんでした。探す言葉を変えて試してください。")

img = Image.open(image_path).convert("RGB")
out_dir = Path("samples/out")
out_dir.mkdir(parents=True, exist_ok=True)

for i, box in enumerate(boxes):
    v = {k: float(x) for k, x in (kv.split("=") for kv in box.split(";"))}
    # SAMが見た画像の大きさ(w, h)と元画像の大きさが違う場合に備えて倍率を合わせる
    sx, sy = img.width / v["w"], img.height / v["h"]
    pad = 0.03 * max(img.width, img.height)
    left = max(0, v["x1"] * sx - pad)
    top = max(0, v["y1"] * sy - pad)
    right = min(img.width, v["x2"] * sx + pad)
    bottom = min(img.height, v["y2"] * sy + pad)

    crop = img.crop((left, top, right, bottom))
    out = out_dir / f"{image_path.stem}_board{i}.jpg"
    crop.save(out, quality=95)
    print(f"切り出し {i}: {crop.width}x{crop.height}px → {out}")
