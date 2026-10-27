"""Generate synthetic book-page photos for testing (original story text, free to use).

python tools/make_test_pages.py  -> writes sample_pages/*.jpg
"""
from __future__ import annotations

import random
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

STORY = [
    "Mira lived at the edge of the salt marsh with her grandfather, who mended fishing nets for the whole village. "
    "Every morning she carried his nets down to the jetty and counted the boats as they slipped out into the fog.",
    "One morning Mira found a silver key tangled in the oldest net. The key was cold and heavy, and it had a tiny "
    "crescent moon carved into its handle. She hid it in her pocket and told no one.",
    "Her grandfather noticed that she was quiet at supper. He asked if the sea had been kind to her. Mira only "
    "nodded, because she was thinking about the locked door under the lighthouse stairs.",
    "The lighthouse keeper, a tall man called Oren, had warned the children never to go near that door. He said "
    "the hinges were rusted and the room behind it had been empty for forty years.",
    "That night Mira crept along the shore with a lantern. The silver key fitted the lock at once. Behind the door "
    "she found shelves of glass jars, each holding a small, glowing map of the marsh.",
    "Oren appeared in the doorway, holding his breath. He was not angry. He told her that the maps showed every safe "
    "path through the marsh, and that his own grandmother had drawn them by hand.",
    "From then on Mira helped Oren keep the maps. When the fog came down, she guided the fishing boats home, and "
    "the silver key never left the chain around her neck.",
]

FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "C:/Windows/Fonts/times.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
]


def font(size: int):
    for p in FONT_PATHS:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def make_page(page_no: int | None, paras: list[str], pos: str = "bottom_center", header: str | None = None,
              size=(1200, 1600), body_number: int | None = None, seed: int = 0, blur: float = 0.6) -> Image.Image:
    rnd = random.Random(seed)
    W, H = size
    img = Image.new("RGB", size, (236, 232, 222))
    d = ImageDraw.Draw(img)
    f_body, f_num = font(34), font(32)
    margin, y = 110, 170
    text = " ".join(paras)
    if body_number is not None:
        text += f" The village had {body_number} boats in all."
    for line in textwrap.wrap(text, 52):
        d.text((margin, y), line, fill=(30, 30, 30), font=f_body)
        y += 50
    if header:
        d.text((margin + 260, 70), header, fill=(40, 40, 40), font=f_num)
    if page_no is not None:
        s = str(page_no)
        tw = d.textlength(s, font=f_num)
        xy = {
            "bottom_center": ((W - tw) / 2, H - 100), "bottom_right": (W - margin - tw, H - 100),
            "bottom_left": (margin, H - 100), "top_right": (W - margin - tw, 70), "top_left": (margin, 70),
            "top_center": ((W - tw) / 2, 70),
        }[pos]
        d.text(xy, s, fill=(30, 30, 30), font=f_num)
    img = img.rotate(rnd.uniform(-1.2, 1.2), fillcolor=(90, 80, 70), expand=False)
    return img.filter(ImageFilter.GaussianBlur(blur))


def make_spread(left: int, right: int) -> Image.Image:
    a = make_page(left, STORY[0:1], "bottom_left", size=(800, 1200), seed=1)
    b = make_page(right, STORY[1:2], "bottom_right", size=(800, 1200), seed=2)
    out = Image.new("RGB", (1600, 1200))
    out.paste(a, (0, 0))
    out.paste(b, (800, 0))
    return out


def generate(out_dir: str | Path = "sample_pages") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    positions = ["bottom_center", "bottom_right", "top_right", "bottom_left", "top_left", "bottom_center", "top_center"]
    for i, para in enumerate(STORY):
        page = 12 + i
        make_page(page, [para], positions[i], seed=i).save(out / f"page_{page}.jpg", quality=88)
    make_page(None, STORY[0:1], seed=20).save(out / "no_number.jpg", quality=88)
    make_page(None, STORY[1:2], seed=21, body_number=15).save(out / "number_in_body_only.jpg", quality=88)
    make_page(30, STORY[2:3], "bottom_center", header="Chapter 3", seed=22).save(out / "page_30_chapter_header.jpg", quality=88)
    make_page(31, STORY[3:4], "top_left", header="THE SILVER KEY", seed=23).save(out / "page_31_running_header.jpg", quality=88)
    make_spread(40, 41).save(out / "spread_40_41.jpg", quality=88)
    make_page(12, STORY[0:1], seed=24, blur=4.5).save(out / "page_12_very_blurry.jpg", quality=60)
    return out


if __name__ == "__main__":
    print("wrote", generate(sys.argv[1] if len(sys.argv) > 1 else "sample_pages"))
