"""生成 BuildQuote 产品 logo（docs/logo.png + app/static/logo.png + favicon）。"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 1024
OUT = Path(__file__).resolve().parent.parent
FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_CJK = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"


def _font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def gradient(size: int, top=(15, 23, 42), bottom=(37, 99, 235)) -> Image.Image:
    img = Image.new("RGB", (size, size), top)
    px = img.load()
    for y in range(size):
        ty = y / (size - 1)
        for x in range(size):
            tx = x / (size - 1)
            k = min(1.0, 0.55 * ty + 0.45 * tx)
            px[x, y] = (
                int(top[0] + (bottom[0] - top[0]) * k),
                int(top[1] + (bottom[1] - top[1]) * k),
                int(top[2] + (bottom[2] - top[2]) * k),
            )
    return img


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def make() -> None:
    S = SIZE
    base = gradient(S).convert("RGBA")
    overlay = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    # 淡淡的网格（数据/工程图纸感）
    for i in range(1, 8):
        y = int(S * i / 8)
        d.line([(0, y), (S, y)], fill=(255, 255, 255, 10), width=2)
        x = int(S * i / 8)
        d.line([(x, 0), (x, S)], fill=(255, 255, 255, 8), width=2)

    # 成本曲线（监控/趋势意象）
    pts = [(0.16, 0.735), (0.30, 0.655), (0.42, 0.685), (0.56, 0.585), (0.70, 0.60), (0.84, 0.515)]
    xy = [(int(px * S), int(py * S)) for px, py in pts]
    d.line(xy, fill=(255, 255, 255, 90), width=int(S * 0.018), joint="curve")
    for (x, y) in xy:
        r = int(S * 0.016)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, 220))

    # 白色 BQ 主标识
    txt = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    dt = ImageDraw.Draw(txt)
    f_bq = _font(FONT_BOLD, int(S * 0.42))
    bbox = dt.textbbox((0, 0), "BQ", font=f_bq)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    dt.text(((S - tw) / 2 - bbox[0], int(S * 0.135) - bbox[1]), "BQ", font=f_bq, fill=(255, 255, 255, 255))

    f_name = _font(FONT_BOLD, int(S * 0.088))
    name = "BuildQuote"
    b2 = dt.textbbox((0, 0), name, font=f_name)
    tw2 = b2[2] - b2[0]
    dt.text(((S - tw2) / 2 - b2[0], int(S * 0.795)), name, font=f_name, fill=(255, 255, 255, 240))

    f_sub = _font(FONT_CJK, int(S * 0.052))
    sub = "成本监控工作台"
    b3 = dt.textbbox((0, 0), sub, font=f_sub)
    tw3 = b3[2] - b3[0]
    dt.text(((S - tw3) / 2 - b3[0], int(S * 0.895)), sub, font=f_sub, fill=(255, 255, 255, 185))

    logo = Image.alpha_composite(base, overlay)
    logo = Image.alpha_composite(logo, txt)
    logo.putalpha(rounded_mask(S, int(S * 0.22)))
    logo.save(OUT / "docs" / "logo.png")
    logo.resize((512, 512), Image.LANCZOS).save(OUT / "app" / "static" / "logo.png")
    logo.resize((128, 128), Image.LANCZOS).save(OUT / "app" / "static" / "favicon.png")
    print("logo written: docs/logo.png, app/static/logo.png, app/static/favicon.png")


if __name__ == "__main__":
    make()
