"""images.py — A staged clue image: turned upright, capped in size and re-encoded as WebP."""

import io
from pathlib import Path

from PIL import Image, ImageOps

IMAGE_MAX_PX = 1600
IMAGE_QUALITY = 88


def _process_image(data: bytes, out: Path) -> dict:
    img = Image.open(io.BytesIO(data))
    img.load()
    img = ImageOps.exif_transpose(img)
    if max(img.size) > IMAGE_MAX_PX:
        img.thumbnail((IMAGE_MAX_PX, IMAGE_MAX_PX), Image.LANCZOS)
    has_alpha = "A" in img.getbands() or "transparency" in img.info or "transparency" in img.info
    img = img.convert("RGBA" if has_alpha else "RGB")
    img.save(out, "WEBP", quality=IMAGE_QUALITY, method=6)
    return {
        "width": img.width,
        "height": img.height,
        "bytes_in": len(data),
        "bytes_out": out.stat().st_size,
    }
