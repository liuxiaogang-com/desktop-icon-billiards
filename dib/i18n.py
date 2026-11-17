"""Message lookup, so no window has to carry its own copy of the text.

Every string a player can read lives in :mod:`dib.locales`, one module per language.
This module only resolves which catalog is active and fills in ``{name}`` placeholders.

The active language comes from :func:`set_language`, then the ``DIB_LANG`` environment
variable, then the Windows user interface language, then :data:`DEFAULT_LANGUAGE`.
A language without a catalog falls back to the closest relative that has one, so a
missing translation degrades to the default language instead of an empty window.

The language can change while the app runs: :func:`set_language` takes effect at once,
and each window re-reads its copy through :func:`t` when it is told to refresh. Fonts
follow the same call through :mod:`dib.fonts`, so a switch also changes the typeface.
"""
from __future__ import annotations

import ctypes
import os

from .locales import CATALOGS, NAMES

DEFAULT_LANGUAGE = 'zh-CN'
ENVIRONMENT = 'DIB_LANG'

# Windows LANGID -> preferred catalog code. Full ids win over the primary language, so
# a Traditional Chinese Windows is not quietly served Simplified copy.
EXACT_LANGUAGE_IDS = {0x0404: 'zh-TW', 0x0C04: 'zh-TW', 0x1404: 'zh-TW'}
PRIMARY_LANGUAGE_IDS = {0x04: 'zh-CN', 0x09: 'en', 0x11: 'ja', 0x12: 'ko', 0x2A: 'vi'}

_active = None


def available():
    """Catalog codes bundled with this build, sorted."""
    return tuple(sorted(CATALOGS))


def name(code=None):
    """The language's own name, as a picker shows it: ``中文``, ``English``."""
    return NAMES.get(code or language(), code or language())


def code_for(label):
    """The catalog code whose own name is *label*, falling back to the active language."""
    for code, own_name in NAMES.items():
        if own_name == label:
            return code
    return language()


def _system_language():
    """The Windows user interface language as a catalog code, or ``''`` when unknown."""
    try:
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
    except (AttributeError, OSError):
        return ''
    return EXACT_LANGUAGE_IDS.get(langid) or PRIMARY_LANGUAGE_IDS.get(langid & 0x3FF, '')


def _closest(code):
    """The bundled catalog that best serves *code*, falling back to the default language."""
    normalized = {language.lower(): language for language in available()}
    if not normalized:
        raise RuntimeError('This build bundles no message catalogs')
    wanted = (code or '').replace('_', '-').strip().lower()
    if wanted in normalized:
        return normalized[wanted]
    language = wanted.split('-')[0]
    for full in sorted(normalized):
        if full.split('-')[0] == language:
            return normalized[full]
    return normalized.get(DEFAULT_LANGUAGE.lower(), sorted(normalized.values())[0])


def language():
    """The active catalog code."""
    global _active
    if _active is None:
        _active = _closest(os.environ.get(ENVIRONMENT) or _system_language())
    return _active


def set_language(code=None):
    """Switch the active catalog, or pass ``None`` to go back to the system language.

    Windows that are already open are not touched here; :meth:`dib.ui.DesktopBilliardsApp
    .set_language` calls each window's ``retranslate`` after this so a switch is visible
    immediately.
    """
    global _active
    _active = None if code is None else _closest(code)
    return language()


def catalog(code=None):
    """A copy of the messages for *code*, defaulting to the active language."""
    return dict(CATALOGS[code or language()])


def t(key, /, **values):
    """The text for *key* in the active language, with ``{name}`` placeholders filled in.

    A key missing from the active catalog falls back to :data:`DEFAULT_LANGUAGE`; a key
    missing everywhere returns itself, which ``tests/test_i18n.py`` catches before a
    build ships rather than leaving a blank label in a window.
    """
    for code in (language(), DEFAULT_LANGUAGE):
        text = CATALOGS.get(code, {}).get(key)
        if text is not None:
            return text.format(**values) if values else text
    return key
