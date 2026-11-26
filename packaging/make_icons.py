#!/usr/bin/env python3
"""The logo: Burg Altena in bricks (a Cycles render of the brick model made from the Digitaler Zwilling NRW,
2026-11), cut square around the castle, with rounded corners.

    python3 packaging/make_icons.py [RENDER.png]

Without an argument it uses docs/images/burg_altena_hero.jpg (the render, kept in the repository at 1600 px); with
the full-size render it also rewrites that JPEG. Writes packaging/icons/twinbrick-it_{512,256,128,64,32}.png and
packaging/twinbrick-it.png (256 px).
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
HERO = ROOT / "docs" / "images" / "burg_altena_hero.jpg"
# square crops in the 2400 x 1350 render: the castle, its wooded slope and the layered base; the castle alone for
# the small sizes (a 32 px icon shows a silhouette at most)
CROP_LARGE = (320, 170, 1410, 1260)
CROP_SMALL = (360, 230, 1240, 1110)


def rounded(img: Image.Image, radius: float = 0.18) -> Image.Image:
    n = img.size[0]
    mask = Image.new("L", (n * 4, n * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, n * 4 - 1, n * 4 - 1], radius=int(radius * n * 4), fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask.resize((n, n), Image.LANCZOS))
    return out


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else HERO
    im = Image.open(src).convert("RGB")
    if im.size != (2400, 1350):
        im = im.resize((2400, 1350), Image.LANCZOS)
    if src != HERO:
        HERO.parent.mkdir(parents=True, exist_ok=True)
        im.resize((1600, 900), Image.LANCZOS).save(HERO, quality=88, optimize=True, progressive=True)
    icons = ROOT / "packaging" / "icons"
    icons.mkdir(parents=True, exist_ok=True)
    for px in (512, 256, 128, 64, 32):
        crop = CROP_LARGE if px >= 128 else CROP_SMALL
        rounded(im.crop(crop).resize((px, px), Image.LANCZOS)).save(icons / f"twinbrick-it_{px}.png", optimize=True)
    rounded(im.crop(CROP_LARGE).resize((256, 256), Image.LANCZOS)).save(ROOT / "packaging" / "twinbrick-it.png")
    print("icons:", ", ".join(str(p.relative_to(ROOT)) for p in sorted(icons.glob("*.png"))))


if __name__ == "__main__":
    main()
