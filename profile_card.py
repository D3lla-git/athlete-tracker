"""
D.A.R.T. profile card image (PNG), drawn with Pillow.

One renderer feeds every place the card is shared: the link preview
(og:image), "Share card" / "Download card" and the top of the profile PDF,
so the card always looks the same. Layout follows the app's athlete card:
photo top-left, "ATHLETE PROFILE", name + verified badge, sport, category
pill, three stats, nationality, and the D.A.R.T. VERIFIED mark.

Only public sport information is drawn (age, never the date of birth).
"""
import io
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

WIDTH, HEIGHT = 1200, 630

GREEN = (25, 135, 84)
GREEN_LIGHT = (76, 211, 138)
WHITE = (255, 255, 255)
MUTED = (163, 184, 172)
LABEL = (138, 160, 148)


@dataclass
class CardData:
    name: str
    eyebrow: str = 'ATHLETE PROFILE'
    subtitle: str = ''                # e.g. "Football · Basketball"
    pill: str = ''                    # e.g. "High School Athlete"
    stats: list = field(default_factory=list)   # [(label, value)] up to 3
    nationality: str = ''
    country_code: str = ''            # ISO alpha-2, e.g. "LR"
    extra: list = field(default_factory=list)   # [(label, value)] next to nationality
    verified: bool = False
    photo: bytes | None = None


def _font(size, bold=False):
    # Pillow's built-in scalable font: no font files needed on the server.
    font = ImageFont.load_default(size=size)
    return font


def _text(draw, xy, text, size, fill, bold=False, spacing=0, anchor='la'):
    font = _font(size)
    stroke = max(1, size // 28) if bold else 0
    if spacing:
        x, y = xy
        for ch in text:
            draw.text((x, y), ch, font=font, fill=fill, stroke_width=stroke, stroke_fill=fill, anchor=anchor)
            x += draw.textlength(ch, font=font) + spacing
        return x
    draw.text(xy, text, font=font, fill=fill, stroke_width=stroke, stroke_fill=fill, anchor=anchor)
    return xy[0] + draw.textlength(text, font=font) + stroke * 2


def _fit(draw, text, max_width, size, min_size=26):
    while size > min_size and draw.textlength(text, font=_font(size)) > max_width:
        size -= 2
    while draw.textlength(text, font=_font(size)) > max_width and len(text) > 4:
        text = text[:-2].rstrip() + '…'
    return text, size


def _background():
    img = Image.new('RGB', (WIDTH, HEIGHT), (6, 13, 8))
    draw = ImageDraw.Draw(img)

    # Diagonal brand gradient #071208 -> #0d2413 -> #061008
    stops = [(0.0, (7, 18, 8)), (0.55, (13, 36, 19)), (1.0, (6, 16, 8))]
    for x in range(WIDTH):
        t = x / (WIDTH - 1)
        for (a, ca), (b, cb) in zip(stops, stops[1:]):
            if a <= t <= b:
                k = (t - a) / (b - a)
                color = tuple(int(ca[i] + (cb[i] - ca[i]) * k) for i in range(3))
                break
        draw.line([(x, 0), (x, HEIGHT)], fill=color)

    # Soft green glow (top right) like the app's cards.
    glow = Image.new('L', (WIDTH, HEIGHT), 0)
    ImageDraw.Draw(glow).ellipse((760, -220, 1360, 360), fill=120)
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    img = Image.composite(Image.new('RGB', img.size, (57, 185, 114)), img, glow.point(lambda v: int(v * 0.55)))

    # Watermark emblem (rings + big D) on the right.
    mark = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 0))
    md = ImageDraw.Draw(mark)
    cx, cy = 880, 330
    for r, a in ((250, 26), (205, 20), (160, 16)):
        md.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(255, 255, 255, a), width=3)
    md.text((cx, cy), 'D', font=_font(230), fill=(255, 255, 255, 18), anchor='mm', stroke_width=4, stroke_fill=(255, 255, 255, 18))
    img = Image.alpha_composite(img.convert('RGBA'), mark)
    return img


def _photo(data):
    size = 240
    if data.photo:
        try:
            photo = Image.open(io.BytesIO(data.photo)).convert('RGB')
            photo = ImageOps.fit(photo, (size, size), method=Image.LANCZOS)
        except Exception:
            photo = None
    else:
        photo = None

    if photo is None:
        photo = Image.new('RGB', (size, size), GREEN)
        d = ImageDraw.Draw(photo)
        initials = ''.join(part[0] for part in data.name.split()[:2]).upper() or '?'
        d.text((size / 2, size / 2), initials, font=_font(96), fill=WHITE, anchor='mm', stroke_width=3, stroke_fill=WHITE)

    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=34, fill=255)
    return photo, mask


def _verified_badge(draw, x, y, r=17):
    # Rosette-style badge with a check mark.
    draw.ellipse((x - r, y - r, x + r, y + r), fill=GREEN_LIGHT)
    draw.ellipse((x - r + 3, y - r + 3, x + r - 3, y + r - 3), fill=GREEN)
    draw.line([(x - 8, y + 1), (x - 2, y + 7), (x + 9, y - 7)], fill=WHITE, width=4, joint='curve')


def render_card(data: CardData) -> bytes:
    img = _background()
    draw = ImageDraw.Draw(img)

    # Note: shapes are drawn with solid colours (drawing a transparent
    # colour would replace pixels, not blend).

    # Card border (the image is the card).
    draw.rounded_rectangle((8, 8, WIDTH - 9, HEIGHT - 9), radius=42, outline=(86, 120, 100), width=2)

    # Photo
    photo, mask = _photo(data)
    img.paste(photo, (64, 64), mask)
    draw.rounded_rectangle((64, 64, 64 + 239, 64 + 239), radius=34, outline=(70, 104, 84), width=2)

    # Header text
    x0 = 340
    _text(draw, (x0, 74), data.eyebrow, 22, LABEL, spacing=4)

    name, size = _fit(draw, data.name, WIDTH - x0 - 140, 58)
    end = _text(draw, (x0, 108), name, size, WHITE, bold=True)
    if data.verified:
        _verified_badge(draw, int(end) + 30, 108 + size // 2 + 4)

    if data.subtitle:
        sub, s2 = _fit(draw, data.subtitle, WIDTH - x0 - 80, 34)
        _text(draw, (x0, 188), sub, s2, (225, 235, 229))

    if data.pill:
        pill, ps = _fit(draw, data.pill, 520, 26, 18)
        w = draw.textlength(pill, font=_font(ps))
        draw.rounded_rectangle((x0, 242, x0 + w + 36, 242 + ps + 22), radius=12, fill=GREEN)
        _text(draw, (x0 + 18, 242 + 11), pill, ps, WHITE, bold=True)

    # Stats row
    cols = [64, 520, 790]
    for (label, value), x in zip(data.stats[:3], cols):
        _text(draw, (x, 352), label.upper(), 20, LABEL, spacing=3)
        value, vs = _fit(draw, str(value), (cols[cols.index(x) + 1] - x - 30) if x != cols[-1] else 300, 40, 24)
        _text(draw, (x, 384), value, vs, WHITE)

    # Nationality (+ extra facts)
    if data.nationality:
        _text(draw, (64, 470), 'NATIONALITY', 20, LABEL, spacing=3)
        x = 64
        if data.country_code:
            cw = 58
            draw.rounded_rectangle((x, 502, x + cw, 540), radius=8, fill=(34, 64, 46), outline=(110, 150, 128), width=2)
            draw.text((x + cw / 2, 521), data.country_code, font=_font(22), fill=WHITE, anchor='mm', stroke_width=1, stroke_fill=WHITE)
            x += cw + 14
        nat, ns = _fit(draw, data.nationality, 360, 36, 22)
        _text(draw, (x, 502), nat, ns, WHITE)

    for (label, value), x in zip(data.extra[:2], [520, 790]):
        _text(draw, (x, 470), label.upper(), 20, LABEL, spacing=3)
        value, vs = _fit(draw, str(value), 250, 36, 22)
        _text(draw, (x, 502), value, vs, WHITE)

    # D.A.R.T. mark (bottom right)
    brand_font = _font(50)
    brand = 'D.A.R.T.'
    bw = draw.textlength(brand, font=brand_font)
    bx, by = WIDTH - 72 - bw, 470
    # Green "D", white rest — the app's logo style.
    draw.text((bx, by), 'D', font=brand_font, fill=GREEN_LIGHT, stroke_width=2, stroke_fill=GREEN_LIGHT)
    draw.text((bx + draw.textlength('D', font=brand_font) + 4, by), '.A.R.T.', font=brand_font, fill=WHITE, stroke_width=2, stroke_fill=WHITE)
    tag = 'VERIFIED' if data.verified else 'PROFILE'
    tw = sum(draw.textlength(ch, font=_font(22)) + 6 for ch in tag) - 6
    _text(draw, (WIDTH - 72 - tw, by + 64), tag, 22, (207, 233, 218), spacing=6)

    out = io.BytesIO()
    img.convert('RGB').save(out, format='PNG', optimize=True)
    return out.getvalue()
