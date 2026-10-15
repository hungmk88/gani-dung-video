"""Make the two logo images the renderer draws from the designer's file (run once by hand).

    python scripts/tao_logo_video.py

Needs `pdftocairo` (poppler) and Pillow; the app itself needs neither. Source: assets/brand/
logo-goc.ai (an Illustrator file is a PDF). Page 3 is the logo on a transparent background,
page 1 the same logo on its dark card.

- logo-video.png: the leaf and "GANI" only (EST 2014 and the slogan are unreadable at
  watermark size), transparent, with a soft shadow so it reads on bright and dark footage.
- logo-khung-ket.png: page 1 as drawn by the designer, for the black end card.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageFilter

BRAND = Path(__file__).resolve().parent.parent / "assets" / "brand"
SOURCE = BRAND / "logo-goc.ai"
RENDER_PX = 2400  # long side the vector file is rasterised at, before scaling down
WATERMARK_W = 480
END_CARD_W = 720
SATURATED = 40  # max - min channel: the grey EST/2014/slogan stay below, blue and green above


def _page(page: int, work: Path) -> Image.Image:
    out = work / f"p{page}"
    args = ["-png", "-transp", "-singlefile", "-f", str(page), "-l", str(page)]
    subprocess.run(["pdftocairo", *args, "-scale-to", str(RENDER_PX), str(SOURCE), str(out)],
                   check=True)  # fmt: skip
    return Image.open(out.with_suffix(".png")).convert("RGBA")


def _watermark(im: Image.Image) -> Image.Image:
    px = im.load()
    assert px is not None
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            if a and max(r, g, b) - min(r, g, b) < SATURATED:
                px[x, y] = (0, 0, 0, 0)
    box = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    assert box is not None
    mark = im.crop(box)
    mark = mark.resize((WATERMARK_W, round(mark.height * WATERMARK_W / mark.width)),
                       Image.Resampling.LANCZOS)  # fmt: skip
    pad, blur = 12, 6
    canvas = Image.new("RGBA", (mark.width + 2 * pad, mark.height + 2 * pad), (0, 0, 0, 0))
    shadow = Image.new("RGBA", mark.size, (0, 0, 0, 0))
    shadow.putalpha(mark.getchannel("A").point(lambda v: v * 110 // 255))
    canvas.alpha_composite(shadow, (pad, pad + 3))
    canvas = canvas.filter(ImageFilter.GaussianBlur(blur))
    canvas.alpha_composite(mark, (pad, pad))
    return canvas


def _end_card(im: Image.Image) -> Image.Image:
    box = im.getchannel("A").getbbox()
    assert box is not None
    card = im.crop(box)
    return card.resize((END_CARD_W, round(card.height * END_CARD_W / card.width)),
                       Image.Resampling.LANCZOS)  # fmt: skip


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        _watermark(_page(3, work)).save(BRAND / "logo-video.png", optimize=True)
        _end_card(_page(1, work)).save(BRAND / "logo-khung-ket.png", optimize=True)


if __name__ == "__main__":
    main()
