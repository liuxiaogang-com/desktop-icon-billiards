"""Typefaces per language: the Tk family name and the Pillow files behind it.

UI copy needs a face that covers the language: YaHei for Chinese, Segoe UI for English.
Icon names on the table are the player's own file names and may be in any script, so they
keep :func:`data` instead of following the interface language.
"""
from __future__ import annotations

import os
from pathlib import Path

from PIL import ImageFont

from .i18n import language

FONT_DIRECTORY = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Fonts'

# language -> (Tk family, regular files, bold files, numeral files); the first file that exists wins.
FAMILIES = {
    'zh-CN': ('Microsoft YaHei UI', ('msyh.ttc',), ('msyhbd.ttc',),
              ('segoeuib.ttf', 'arialbd.ttf', 'msyhbd.ttc')),
    'en': ('Segoe UI', ('segoeui.ttf', 'arial.ttf'), ('segoeuib.ttf', 'arialbd.ttf'),
           ('segoeuib.ttf', 'arialbd.ttf')),
}
FALLBACK = FAMILIES['zh-CN']
# Latin plus CJK, so a list that mixes scripts (the language picker) always renders.
DATA_FAMILY = 'Microsoft YaHei UI'
DATA_FILES = ('msyh.ttc', 'msyhbd.ttc')


def _family():
    code = language()
    return FAMILIES.get(code) or FAMILIES.get(code.split('-')[0]) or FALLBACK


def ui_family():
    """The Tk font family for the interface language."""
    return _family()[0]


def _font(files, size):
    for name in files:
        try:
            return ImageFont.truetype(str(FONT_DIRECTORY / name), size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def text(size, bold=False):
    """The Pillow face for translatable copy: card labels, table buttons, the power meter."""
    family = _family()
    return _font(family[2] if bold else family[1], size)


def numerals(size):
    """The Pillow face for digits, which reads as a designed number rather than body text."""
    return _font(_family()[3], size)


def data(size):
    """The Pillow face for the player's own text, such as desktop icon names."""
    return _font(DATA_FILES, size)
