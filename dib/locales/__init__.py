"""The bundled message catalogs, loaded from GNU gettext ``.po`` files.

Each ``*.po`` file in this directory is one language. Chinese is the source language, so
``msgid`` holds the Chinese text and the file itself doubles as the translation template:
copy ``zh_CN.po``, change the header, and translate.

Languages are discovered from the directory rather than listed here, so adding one is a
single new file. ``dib/locales/po.py`` does the reading; see ``docs/I18N.md``.
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import po


def _directory():
    """Where the ``.po`` files live, in a source checkout and inside a frozen build."""
    frozen = getattr(sys, '_MEIPASS', None)
    if frozen:
        candidate = Path(frozen) / 'dib' / 'locales'
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parent


def _load():
    catalogs, names, problems = {}, {}, []
    for path in sorted(_directory().glob('*.po')):
        try:
            catalog = po.load(path)
        except po.PoError as error:
            # A broken catalog must not stop the game: it is skipped and its language falls
            # back to the default one. tests/test_i18n.py fails on any problem instead.
            problems.append(str(error))
            continue
        catalogs[catalog.language] = catalog.messages
        names[catalog.language] = catalog.name
    return catalogs, names, problems


CATALOGS, NAMES, PROBLEMS = _load()

__all__ = ['CATALOGS', 'NAMES', 'PROBLEMS']

__all__ = ['CATALOGS', 'NAMES']
