"""Rebuild the logo files from the app's own typeface: `python scripts/make_brand.py`.

The icon is a pit board: white letters on a black board, the one sign the pit wall holds out to a
driver. The wordmark is the name in the same face, one colour.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONT = ROOT / "static" / "fonts" / "BarlowSemiCondensed-ExtraBoldItalic.woff"
BOARD, INK, DARK_INK = (10, 11, 14, 255), (236, 238, 242, 255), (18, 20, 26, 255)


def icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=round(size * 0.08), fill=BOARD)
    font = ImageFont.truetype(str(FONT), round(size * 0.62))
    x0, y0, x1, y1 = d.textbbox((0, 0), "PS", font=font)
    # set low and left, the way numbers sit on a pit board, not centred like a badge
    d.text((round(size * 0.12) - x0, size - round(size * 0.14) - y1), "PS", font=font, fill=INK)
    return img


def wordmark(ink, height: int = 96) -> Image.Image:
    font = ImageFont.truetype(str(FONT), round(height * 0.9))
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    x0, y0, x1, y1 = probe.textbbox((0, 0), "PurpleSector", font=font)
    img = Image.new("RGBA", (x1 - x0 + 8, y1 - y0 + 8), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((4 - x0, 4 - y0), "PurpleSector", font=font, fill=ink)
    return img


if __name__ == "__main__":
    big = icon(512)
    big.save(ROOT / "assets" / "icon.png")
    big.save(ROOT / "assets" / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    wordmark(INK).save(ROOT / "docs" / "wordmark-dark.png")    # for GitHub's dark theme
    wordmark(DARK_INK).save(ROOT / "docs" / "wordmark-light.png")
    print("wrote assets/icon.png, assets/icon.ico, docs/wordmark-dark.png, docs/wordmark-light.png")
