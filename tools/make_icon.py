"""Logo seçeneklerini çizer: python tools/make_icon.py (Pillow gerekir).

docs/logo-secenekler/ altına her seçeneğin PNG ve ICO dosyasını ve bir karşılaştırma görseli yazar.
Seçilen seçenek `--sec A` ile assets/ klasörüne kopyalanır.
"""
import argparse
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

N = 1024
NAVY = ((0x24, 0x5C, 0x94), (0x0A, 0x21, 0x45))
TEAL = ((0x4A, 0xF4, 0xD2), (0x00, 0xBF, 0xAF))
WHITE = ((255, 255, 255), (235, 250, 248))
ICO_SIZES = [(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)]
ROOT = Path(__file__).resolve().parent.parent


def gradient(colors) -> Image.Image:
    (r1, g1, b1), (r2, g2, b2) = colors
    small = Image.new("RGB", (64, 64))
    px = small.load()
    for y in range(64):
        for x in range(64):
            t = (x + y) / 126
            px[x, y] = (int(r1 + (r2 - r1) * t), int(g1 + (g2 - g1) * t), int(b1 + (b2 - b1) * t))
    return small.resize((N, N), Image.BICUBIC).convert("RGBA")


def mask(draw_fn) -> Image.Image:
    m = Image.new("L", (N, N), 0)
    draw_fn(ImageDraw.Draw(m))
    return m


def tile(d):
    d.rounded_rectangle((48, 48, N - 48, N - 48), radius=230, fill=255)


def play(d):
    # Üçgenin ağırlık merkezi tam ortada olsun diye hafif sağa kaydırıldı.
    d.polygon([(410, 322), (410, 702), (716, 512)], fill=255)


def thick_line(d, points, width):
    d.line(points, fill=255, width=width, joint="curve")
    r = width // 2
    for x, y in (points[0], points[-1]):
        d.ellipse((x - r, y - r, x + r, y + r), fill=255)


def chevrons_vertical(d):
    for y, s in ((150, 1), (N - 150, -1)):
        thick_line(d, [(392, y), (512, y + s * 78), (632, y)], 52)


def corner_arrows(d):
    for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        cx, cy = N / 2 - sx * 330, N / 2 - sy * 330
        tip = (cx + sx * 96, cy + sy * 96)
        thick_line(d, [(cx, cy), tip], 50)
        thick_line(d, [(tip[0] - sx * 84, tip[1]), tip, (tip[0], tip[1] - sy * 84)], 50)


def play_small(d):
    d.polygon([(420, 350), (420, 674), (680, 512)], fill=255)


VARIANTS = {
    "A": ("Oklar üstte ve altta", NAVY, TEAL, [play, chevrons_vertical]),
    "B": ("Köşelerde dört ok", NAVY, TEAL, [play_small, corner_arrows]),
    "C": ("Turkuaz zemin", TEAL, NAVY, [play, chevrons_vertical]),
}


def draw(key: str) -> Image.Image:
    _, bg, fg, parts = VARIANTS[key]
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    img.paste(gradient(bg), (0, 0), mask(tile))
    fg_img = gradient(fg)
    for part in parts:
        img.paste(fg_img, (0, 0), mask(part))
    return img


def comparison(images: dict[str, Image.Image], out: Path) -> None:
    big, sizes, pad = 220, [16, 24, 32, 48], 22
    row_h = big + 2 * pad + 30
    small_w = sum(s + pad for s in sizes) + pad
    W, H = pad + big + pad + 2 * small_w, row_h * len(images) + 40
    sheet = Image.new("RGB", (W, H), (255, 255, 255))
    dark_x = pad + big + pad + small_w
    sheet.paste(Image.new("RGB", (W - dark_x, H), (32, 32, 32)), (dark_x, 0))
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("segoeuib.ttf", 22)
    except OSError:
        font = ImageFont.load_default()
    d.text((pad + big + pad + 6, 10), "Açık görev çubuğu", fill=(90, 90, 90), font=font)
    d.text((dark_x + 6, 10), "Koyu görev çubuğu", fill=(200, 200, 200), font=font)
    for r, (key, img) in enumerate(images.items()):
        y = 40 + r * row_h + pad + 30
        b = img.resize((big, big), Image.LANCZOS)
        sheet.paste(b, (pad, y), b)
        d.text((pad, y - 32), key, fill=(40, 40, 40), font=font)
        for x0 in (pad + big + pad, dark_x):
            x = x0 + pad
            for s in sizes:
                sm = img.resize((s, s), Image.LANCZOS)
                sheet.paste(sm, (x, y + big // 2 - s // 2), sm)
                x += s + pad
    sheet.save(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sec", choices=VARIANTS, help="seçilen logoyu assets/ klasörüne kopyala")
    args = parser.parse_args()
    out_dir = ROOT / "docs" / "logo-secenekler"
    out_dir.mkdir(parents=True, exist_ok=True)
    images = {}
    for key, (name, *_rest) in VARIANTS.items():
        img = draw(key)
        images[f"{key}: {name}"] = img
        img.save(out_dir / f"{key}.png")
        img.save(out_dir / f"{key}.ico", sizes=ICO_SIZES)
    comparison(images, out_dir / "karsilastirma.png")
    if args.sec:
        assets = ROOT / "assets"
        shutil.copy(out_dir / f"{args.sec}.png", assets / "icon.png")
        shutil.copy(out_dir / f"{args.sec}.ico", assets / "icon.ico")
    print(out_dir)


if __name__ == "__main__":
    main()
