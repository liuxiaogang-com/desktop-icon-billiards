"""Which desktop items become balls, and how sparse desktops are padded.

The table always carries between ``MIN_BALLS`` and ``MAX_BALLS`` balls: sparse
desktops are padded by cloning icons, crowded ones are trimmed. 15 and 45 are
triangular numbers, so a full rack is a clean triangle. When trimming, shortcuts
and programs (and the system icons such as This PC) win over plain files, and
files win over folders; ties keep the desktop order.
"""
from __future__ import annotations

import os

from .i18n import t

MIN_BALLS = 15
MAX_BALLS = 45
SHORTCUT_SUFFIXES = ('.lnk', '.url', '.exe', '.appref-ms', '.msi')


def item_tier(identity, is_dir=os.path.isdir):
    """0 = shortcut / program / system icon, 1 = other file, 2 = folder."""
    text = identity.lower()
    drive = len(text) > 2 and text[0].isalpha() and text[1] == ':' and text[2] in '\\/'
    is_path = drive or text.startswith('\\\\')
    if not is_path or text.endswith(SHORTCUT_SUFFIXES):
        return 0
    try:
        return 2 if is_dir(identity) else 1
    except OSError:
        return 1


def priority_order(items, is_dir=os.path.isdir):
    """Indexes of ``items`` from most to least wanted as a ball."""
    return sorted(range(len(items)), key=lambda index: (item_tier(items[index]['id'], is_dir), index))


def fill_sources(real_count, minimum=MIN_BALLS):
    """Indexes of real icons to clone so the table has at least ``minimum`` balls."""
    if real_count <= 0:
        return []
    return [index % real_count for index in range(max(0, minimum - real_count))]


def ball_summary(desktop_total, used, padded):
    """One short line telling the player how the ball count came about."""
    if padded:
        return t('balls.summary.padded', total=desktop_total, balls=used + padded)
    if desktop_total > used:
        return t('balls.summary.trimmed', total=desktop_total, balls=used)
    return t('balls.summary.exact', balls=used)
