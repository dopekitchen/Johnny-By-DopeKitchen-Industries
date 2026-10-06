"""Generate every branded asset from assets/logo_source.(png|jpg|jpeg|webp).

To rebrand, replace the logo_source file with your logo (square, 512px+ is best) and run:
    python installer/make_assets.py
build_exe.ps1 runs this automatically.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
BG = (16, 10, 20)
TITLE = (214, 140, 232)
MUTED = (160, 140, 170)


def source() -> Image.Image:
    for ext in ("png", "jpg", "jpeg", "webp"):
        p = ASSETS / f"logo_source.{ext}"
        if p.exists():
            return Image.open(p).convert("RGBA")
    raise SystemExit("Put your logo at assets/logo_source.png (or .jpg)")


def square(img: Image.Image) -> Image.Image:
    s = min(img.size)
    left, top = (img.width - s) // 2, (img.height - s) // 2
    return img.crop((left, top, left + s, top + s))


def rounded(img: Image.Image, size: int, radius_frac=0.22) -> Image.Image:
    img = square(img).resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size * 4 - 1, size * 4 - 1), radius=int(size * 4 * radius_frac), fill=255)
    img.putalpha(mask.resize((size, size), Image.LANCZOS))
    return img


def font(name, size):
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default()


def centered(d, text, f, y, width, fill):
    d.text(((width - d.textlength(text, font=f)) / 2, y), text, font=f, fill=fill)


def main():
    src = source()
    logo = rounded(src, 512)
    logo.save(ASSETS / "logo.png")
    logo.save(ASSETS / "johnny.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

    # Inno Setup side banner (shown at 100% scale; Inno scales it for high DPI)
    w, h = 164, 314
    big = Image.new("RGB", (w, h), BG)
    big.paste(rounded(src, 112), ((w - 112) // 2, 70), rounded(src, 112))
    d = ImageDraw.Draw(big)
    centered(d, "JOHNNY", font("seguisb.ttf", 24), 196, w, TITLE)
    centered(d, "DopeKitchen", font("segoeui.ttf", 12), 232, w, MUTED)
    centered(d, "Industries", font("segoeui.ttf", 12), 248, w, MUTED)
    big.save(ASSETS / "wizard_large.bmp")

    small = Image.new("RGB", (55, 58), (255, 255, 255))
    small.paste(rounded(src, 50), (2, 4), rounded(src, 50))
    small.save(ASSETS / "wizard_small.bmp")
    print("Assets generated in", ASSETS)


if __name__ == "__main__":
    main()
