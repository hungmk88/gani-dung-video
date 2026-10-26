"""Draw the phone frame the Phone Mockup effect lays a real message screenshot into (run by hand).

    python scripts/tao_khung_dien_thoai.py

Needs Pillow; the app itself does not. Writes assets/brand/khung-dien-thoai.png: a dark phone
body with a transparent screen (the screenshot shows through it) and a small camera pill. The
sizes must match rules.json → effects.phone_mockup.frame_size / screen.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets" / "brand" / "khung-dien-thoai.png"
SCALE = 4  # drawn 4x larger, then scaled down: smooth rounded corners
W, H = 560, 1120  # frame_size
BEZEL = 20  # screen = [BEZEL, BEZEL, W - 2 * BEZEL, H - 2 * BEZEL]
BODY_R, SCREEN_R = 72, 52
BODY, RIM, PILL = (24, 24, 27, 255), (70, 70, 76, 255), (0, 0, 0, 255)


def main() -> None:
    s = SCALE
    im = Image.new("RGBA", (W * s, H * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, W * s - 1, H * s - 1), BODY_R * s, fill=RIM)
    d.rounded_rectangle((4 * s, 4 * s, (W - 4) * s - 1, (H - 4) * s - 1), (BODY_R - 4) * s,
                        fill=BODY)  # fmt: skip
    screen = (BEZEL * s, BEZEL * s, (W - BEZEL) * s - 1, (H - BEZEL) * s - 1)
    d.rounded_rectangle(screen, SCREEN_R * s, fill=(0, 0, 0, 0))
    pill_w, pill_h, top = 130, 30, BEZEL + 14
    d.rounded_rectangle(((W - pill_w) // 2 * s, top * s, (W + pill_w) // 2 * s, (top + pill_h) * s),
                        pill_h // 2 * s, fill=PILL)  # fmt: skip
    im.resize((W, H), Image.Resampling.LANCZOS).save(OUT)
    print(f"Đã ghi {OUT}")


if __name__ == "__main__":
    main()
