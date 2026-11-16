"""The shareable result card: a 4:5 poster of a cleared round, drawn with Pillow.

The card is what players screenshot and post, so it is built to stand on its own: the game's
name and icon, the player's tier and global rank as the hero, a percentile bar, the round's
numbers, and the author's credit. It carries no desktop contents (no icon names or artwork).

:func:`render_card` is pure (``view`` in, image out); :func:`save_card` and :func:`copy_card`
hand the image to the player as a PNG in Pictures or as a clipboard bitmap.
"""
from __future__ import annotations

import io
import os
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import fonts
from .i18n import t

WIDTH, HEIGHT = 1080, 1350
SCALE = 2  # drawn at twice the size and shrunk back: Pillow shapes have no anti-aliasing

BG_TOP = (8, 22, 20)
BG_BOTTOM = (15, 47, 41)
CARD = (21, 38, 35)
TEXT = (236, 248, 243)
MUTED = (138, 166, 158)
ACCENT = (105, 221, 179)
ACCENT_LIGHT = (182, 251, 224)
GOLD = (242, 202, 135)

# (minimum percentile, catalog key, colour): the first row whose minimum is reached wins.
TIERS = (
    (99, 'card.tier.legend', (242, 202, 135)),
    (95, 'card.tier.champion', (255, 170, 102)),
    (85, 'card.tier.master', (190, 154, 255)),
    (65, 'card.tier.expert', (105, 221, 179)),
    (40, 'card.tier.skilled', (140, 200, 255)),
    (0, 'card.tier.rookie', (166, 186, 180)),
)

LOGO = Path(__file__).resolve().parent.parent / 'assets' / 'desktop-icon-billiards.png'

# Labels follow the interface language; digits keep a designed numeral face.
label_font = fonts.text
number_font = fonts.numerals


def tier_for(percentile):
    """The ``(title, colour)`` a player's global percentile earns."""
    for minimum, key, colour in TIERS:
        if percentile >= minimum:
            return t(key), colour
    return t(TIERS[-1][1]), TIERS[-1][2]


def _gradient(size, top, bottom):
    ramp = Image.linear_gradient('L').resize(size, Image.Resampling.BILINEAR)
    return ImageOps.colorize(ramp, top, bottom).convert('RGBA')


def _glow(size, centre, radius, colour, strength):
    """A soft round light: a blurred disc blended over the background."""
    mask = Image.new('L', (size[0] // 4, size[1] // 4), 0)
    cx, cy, r = centre[0] // 4, centre[1] // 4, radius // 4
    ImageDraw.Draw(mask).ellipse((cx - r, cy - r, cx + r, cy + r), fill=int(255 * strength))
    mask = mask.filter(ImageFilter.GaussianBlur(r * .55)).resize(size, Image.Resampling.BILINEAR)
    layer = Image.new('RGBA', size, colour + (0,))
    layer.putalpha(mask)
    return layer


def _text_width(draw, text, font):
    return draw.textlength(text, font=font)


def _fit_font(draw, text, make_font, size, max_width):
    """The largest font up to ``size`` in which ``text`` still fits ``max_width``."""
    while size > 20 and _text_width(draw, text, make_font(size)) > max_width:
        size -= 4
    return make_font(size)


def _rounded(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def around(template, name, **values):
    """Split ``template`` around its ``{name}`` placeholder: ``(before, middle, after)``.

    A unit that a language does not have (English writes "35", Chinese "35 颗") therefore
    lives inside the translation instead of forcing an empty string, which .po files read
    as "not translated yet".
    """
    marker = '{' + name + '}'
    before, found, after = template.partition(marker)
    if not found:
        raise ValueError(f'missing {{{name}}} in {template!r}')
    return before, str(values[name]), after


def _hero_rank(draw, canvas_width, y, rank, s, colour):
    """``第 1,204 名`` / ``#1,204``: the number huge, the particles small, on one baseline."""
    number = f'{rank:,}'
    big = _fit_font(draw, number, number_font, 230 * s, 640 * s)
    small = label_font(64 * s, bold=True)
    before, middle, after = around(t('card.hero.rank'), 'rank', rank=number)
    words = [(text, font) for text, font in ((before, small), (middle, big), (after, small)) if text]
    gap = 14 * s
    widths = [_text_width(draw, text, font) for text, font in words]
    left = (canvas_width - sum(widths) - gap * (len(words) - 1)) / 2
    baseline = y + 220 * s
    for (text, font), width in zip(words, widths):
        draw.text((left, baseline), text, font=font, fill=colour, anchor='ls')
        left += width + gap
    return baseline


def render_card(view, *, when=None, credit='', site=''):
    """Draw the result card for a ``view`` (the dict the result window is fed). Returns an RGB image.

    ``view`` keys: ``time``, ``average``, ``balls``, ``shots`` (display strings) and ``rank``, a
    dict with ``phase`` and, when ``phase == 'ok'``, the numbers ``rank``, ``percentile``,
    ``best_rank`` and ``new_best``.
    """
    s = SCALE
    width, height = WIDTH * s, HEIGHT * s
    rank = view.get('rank') or {'phase': 'unavailable'}
    ranked = rank.get('phase') == 'ok'
    tier, tier_colour = tier_for(rank['percentile']) if ranked else (None, ACCENT)

    canvas = _gradient((width, height), BG_TOP, BG_BOTTOM)
    canvas.alpha_composite(_glow((width, height), (width // 2, 520 * s), 520 * s, tier_colour, .34))
    canvas.alpha_composite(_glow((width, height), (width - 40 * s, height - 120 * s), 460 * s, ACCENT, .16))
    decoration = Image.new('RGBA', (width, height))
    deco = ImageDraw.Draw(decoration)
    for cx, cy in ((0, 0), (width, 0), (0, height), (width, height)):  # four pockets in the corners
        for radius, alpha in ((210, 20), (150, 14)):
            r = radius * s
            deco.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(105, 221, 179, alpha), width=3 * s)
    canvas.alpha_composite(decoration)
    draw = ImageDraw.Draw(canvas)
    margin = 96 * s

    # -- header: icon, name, date --------------------------------------------------
    try:
        with Image.open(LOGO) as source:
            logo = source.convert('RGBA').resize((80 * s, 80 * s), Image.Resampling.LANCZOS)
        canvas.alpha_composite(logo, (margin, 82 * s))
    except OSError:
        pass
    draw.text((margin + 104 * s, 82 * s), t('product.name'), font=label_font(46 * s, bold=True), fill=TEXT)
    draw.text((margin + 106 * s, 144 * s), t('product.tagline'), font=label_font(24 * s), fill=MUTED)
    when = when or time.localtime()
    stamp = time.strftime('%Y.%m.%d', when)
    draw.text((width - margin, 92 * s), stamp, font=number_font(26 * s), fill=MUTED, anchor='ra')

    # -- hero ----------------------------------------------------------------------
    centre = width / 2
    if ranked:
        badge_font = label_font(40 * s, bold=True)
        badge_width = _text_width(draw, tier, badge_font) + 96 * s
        box = (centre - badge_width / 2, 270 * s, centre + badge_width / 2, 348 * s)
        # Translucent shapes go on their own layer: drawing RGBA fills straight onto the canvas
        # would overwrite the pixels instead of blending with them.
        layer = Image.new('RGBA', canvas.size)
        _rounded(ImageDraw.Draw(layer), box, 39 * s, fill=tier_colour + (46,))
        canvas.alpha_composite(layer)
        draw.text((centre, 309 * s), tier, font=badge_font, fill=tier_colour, anchor='mm')
        draw.text((centre, 408 * s), t('card.hero.caption'), font=label_font(30 * s), fill=MUTED, anchor='mm')
        _hero_rank(draw, width, 420 * s, rank['rank'], s, tier_colour)
        percentage = f'{rank["percentile"]:.2f}'.rstrip('0').rstrip('.')
        parts = ((t('card.percentile.prefix'), label_font(32 * s)), (f'{percentage}%', number_font(110 * s)),
                 (t('card.percentile.suffix'), label_font(32 * s)))
        lengths = [_text_width(draw, text, font) for text, font in parts]
        x = (width - sum(lengths)) / 2
        for (text, font), length in zip(parts, lengths):
            draw.text((x, 828 * s), text, font=font, fill=ACCENT_LIGHT if '%' in text else TEXT, anchor='ls')
            x += length
        bar_left, bar_right = 190 * s, width - 190 * s
        bar_top, bar_bottom = 868 * s, 882 * s
        track = Image.new('RGBA', canvas.size)
        _rounded(ImageDraw.Draw(track), (bar_left, bar_top, bar_right, bar_bottom), 7 * s,
                 fill=ACCENT_LIGHT + (70,), outline=ACCENT_LIGHT + (105,), width=s)
        canvas.alpha_composite(track)
        filled = (bar_right - bar_left) * max(0, min(100, rank['percentile'])) / 100
        if filled > 0:
            _rounded(draw, (bar_left, bar_top, bar_left + filled, bar_bottom),
                     min(7 * s, filled / 2), fill=ACCENT_LIGHT)
    else:
        draw.text((centre, 300 * s), t('card.average.caption'), font=label_font(34 * s), fill=MUTED, anchor='mm')
        value = view['average']
        big = _fit_font(draw, value, number_font, 250 * s, 680 * s)
        draw.text((centre - 30 * s, 520 * s), value, font=big, fill=ACCENT, anchor='ms')
        draw.text((centre + _text_width(draw, value, big) / 2 - 10 * s, 520 * s), t('card.average.unit'),
                  font=label_font(64 * s, bold=True), fill=ACCENT, anchor='ls')
        message = {'pending': t('card.message.pending'), 'off': t('card.message.off')}.get(
            rank.get('phase'), rank.get('message') or '')
        if message:
            font = label_font(30 * s)
            pill_width = min(width - 2 * margin, _text_width(draw, message, font) + 80 * s)
            _rounded(draw, (centre - pill_width / 2, 620 * s, centre + pill_width / 2, 690 * s), 35 * s,
                     fill=(31, 55, 51, 255), outline=(60, 100, 92, 255), width=2 * s)
            draw.text((centre, 655 * s), message, font=font, fill=MUTED, anchor='mm')

    # -- the numbers ---------------------------------------------------------------
    # Each tile is a label plus a template whose {value} slot carries the number and whose
    # remainder is the unit, so a language without a unit word simply omits it.
    tiles = ((t('card.tile.time'), view['time'], None),
             (t('card.tile.average'), view['average'], 'card.value.average'),
             (t('card.tile.icons'), view['balls'], 'card.value.icons'),
             (t('card.tile.shots'), view['shots'], 'card.value.shots'))
    tile_width = (width - 2 * margin) / 4
    draw.line((margin, 940 * s, width - margin, 940 * s), fill=(48, 84, 76), width=2 * s)
    for index, (label, value, template_key) in enumerate(tiles):
        x = margin + index * tile_width
        draw.text((x, 986 * s), label, font=label_font(27 * s), fill=MUTED)
        font = _fit_font(draw, value, number_font, 42 * s, tile_width - 42 * s)
        draw.text((x, 1080 * s), value, font=font, fill=TEXT, anchor='ls')
        unit = around(t(template_key), 'value', value=value)[2] if template_key else ''
        if unit:
            draw.text((x + _text_width(draw, value, font), 1080 * s), unit, font=label_font(24 * s),
                      fill=MUTED, anchor='ls')

    if ranked:
        text = t('card.best', rank=f'{rank["best_rank"]:,}')
        best_font = _fit_font(draw, text, label_font, 34 * s, 690 * s)
        draw.text((margin, 1186 * s), text, font=best_font, fill=ACCENT_LIGHT, anchor='lm')
        if rank.get('new_best'):
            font = label_font(30 * s, bold=True)
            best_label = t('card.new_best')
            pill = _text_width(draw, best_label, font) + 56 * s
            x1 = width - margin
            stem_x = (x1 - pill) / s - 30
            draw.line(((stem_x + 9) * s, 1205 * s, (stem_x - 5) * s, 1168 * s), fill=GOLD, width=2 * s)
            for offset in (0, 12, 24):
                y = 1200 - offset
                x = stem_x + 7 - offset / 3
                for side in (-1, 1):
                    draw.polygon(((x * s, y * s), ((x + side * 12) * s, (y - 5) * s),
                                  ((x + side * 14) * s, (y - 17) * s), ((x + side * 4) * s, (y - 11) * s)),
                                 fill=GOLD)
            _rounded(draw, (x1 - pill, 1156 * s, x1, 1216 * s), 30 * s, fill=GOLD + (255,))
            draw.text((x1 - pill / 2, 1186 * s), best_label, font=font, fill=(40, 28, 8), anchor='mm')

    # -- footer: where the game comes from ----------------------------------------------
    draw.line((margin, 1262 * s, width - margin, 1262 * s), fill=(48, 84, 76, 255), width=2 * s)
    if credit:
        draw.text((margin, 1296 * s), credit, font=number_font(28 * s), fill=ACCENT, anchor='lm')
    if site:
        draw.text((width - margin, 1296 * s), site, font=label_font(24 * s), fill=MUTED, anchor='rm')

    return canvas.convert('RGB').resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)


# -- handing the card to the player ----------------------------------------------------------
def pictures_directory():
    """``%USERPROFILE%\\Pictures\\DesktopIconBilliards``, falling back to the home folder."""
    home = Path(os.environ.get('USERPROFILE') or Path.home())
    base = home / 'Pictures'
    return (base if base.is_dir() else home) / 'DesktopIconBilliards'


def save_card(image, directory=None, when=None):
    """Write the card as a PNG and return its path."""
    directory = Path(directory) if directory else pictures_directory()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{t("card.file_prefix")}{time.strftime("%Y%m%d-%H%M%S", when or time.localtime())}.png'
    image.save(path, 'PNG', optimize=True)
    return path


def dib_bytes(image):
    """The card as clipboard ``CF_DIB`` data: a BMP without its 14-byte file header."""
    buffer = io.BytesIO()
    image.convert('RGB').save(buffer, 'BMP')
    return buffer.getvalue()[14:]


def copy_card(image):
    """Put the card on the clipboard as a bitmap so it can be pasted straight into a chat."""
    import win32clipboard
    import win32con

    data = dib_bytes(image)
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_DIB, data)
    finally:
        win32clipboard.CloseClipboard()
