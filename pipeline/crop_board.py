"""SAM 3.1 で写真の中の看板を探し、元の画像から高解像度のまま切り出す。
看板が見つからなければ、画像全体を看板として扱う。

使い方:
    python pipeline/crop_board.py samples/sendai.png
    python pipeline/crop_board.py samples/sendai.png "white sign board"   # 探す言葉を変える
"""
import base64
import io
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image, ImageOps

load_dotenv()
client = OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"])
OUT_DIR = Path("samples/out")


def load_upright(image_path: Path) -> Image.Image:
    """スマホの写真は向きの情報(EXIF)で回転しているので、見たままの向きに直して開く。"""
    return ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")


def to_data_url(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def find_boxes(img: Image.Image, prompt: str) -> list[dict]:
    response = client.responses.create(
        model="sam-3.1",
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": to_data_url(img)},
            ],
        }],
        store=False,
    )
    boxes = []
    for box in re.findall(r"<\|box;([^|]*)\|>", response.output_text):
        boxes.append({k: float(x) for k, x in (kv.split("=", 1) for kv in box.split(";"))})
    return boxes


def crop_boards(image_path: Path, prompt: str = "notice board") -> tuple[list[Path], bool]:
    """切り出した画像のパスの一覧と、SAMが看板を見つけたかどうかを返す。"""
    img = load_upright(image_path)
    boxes = find_boxes(img, prompt)
    if not boxes:
        return [image_path], False

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, v in enumerate(boxes):
        # SAMが見た画像の大きさ(w, h)と元画像の大きさが違う場合に備えて倍率を合わせる
        sx, sy = img.width / v["w"], img.height / v["h"]
        pad = 0.03 * max(img.width, img.height)
        crop = img.crop((
            max(0, v["x1"] * sx - pad),
            max(0, v["y1"] * sy - pad),
            min(img.width, v["x2"] * sx + pad),
            min(img.height, v["y2"] * sy + pad),
        ))
        out = OUT_DIR / f"{image_path.stem}_board{i}.jpg"
        crop.save(out, quality=95)
        print(f"切り出し {i}: {crop.width}x{crop.height}px → {out}")
        paths.append(out)
    return paths, True


if __name__ == "__main__":
    prompt = sys.argv[2] if len(sys.argv) > 2 else "notice board"
    paths, found = crop_boards(Path(sys.argv[1]), prompt)
    if not found:
        print(f"看板が見つからなかったため、画像全体を使います → {paths[0]}")
