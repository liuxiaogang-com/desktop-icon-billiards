"""Product constants shared by the window, the single-instance guard and the build."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlencode

from . import __version__

# Single source of truth for the release version. The build script reads it.
VERSION: str = __version__
VERSION_PARTS = tuple(int(part) for part in VERSION.split('.'))
if len(VERSION_PARTS) != 3 or any(part < 0 for part in VERSION_PARTS):
    raise RuntimeError(f'Invalid version: {VERSION!r}')

PRODUCT_NAME = '桌面图标台球'
PRODUCT_NAME_EN = 'Desktop Icon Billiards'
INTERNAL_NAME = 'DesktopIconBilliards'
AUTHOR = 'LXG'
# Shown next to the version, in the compact layouts, on the result window and faintly on the
# table, so screenshots and recordings of the game keep their source.
CREDIT = 'GitHub@liuxiaogang-com'
CREDIT_URL = 'https://github.com/liuxiaogang-com'
REPOSITORY_URL = 'https://github.com/liuxiaogang-com/desktop-icon-billiards'
DOUYIN_CREDIT = '作者抖音 @LLLLLLLLLL'
DOUYIN_URL = 'https://go.liuxiaogang.com/douyin?' + urlencode({'app': INTERNAL_NAME, 'version': VERSION})
# Author credit printed on the shareable result card.
SITE_TEXT = DOUYIN_CREDIT
WINDOW_TITLE = f'{PRODUCT_NAME} {VERSION}'

BASE = Path(__file__).resolve().parent.parent
ASSETS = BASE / 'assets'
ICON_PATH = ASSETS / 'desktop-icon-billiards.ico'

DEFAULT_UPDATE_URL = 'https://go.liuxiaogang.com/desktop-icon-billiards-update'
UPDATE_URL = os.environ.get('DIB_UPDATE_URL', DEFAULT_UPDATE_URL).strip()

# Origin of the ranking server (https, no path). Empty disables the ranking feature.
DEFAULT_RANK_URL = 'https://billiards-rank-api.liuxiaogang.com'
RANK_URL = os.environ.get('DIB_RANK_URL', DEFAULT_RANK_URL).strip()

# Separate app identity and a shared guard for the Explorer icon layer.
DESKTOP_GUARD = r'Local\MusicRhythmNativeV2'
MUTEX = r'Local\DesktopIconBilliards'
VERSION_PROPERTY = 'DesktopIconBilliards.Version'
EXE_PATTERN = r'DesktopIconBilliards-(\d+\.\d+\.\d+)-win-x64\.exe'

# Ctrl+Alt+X stops the game, restores the desktop and exits.
HOTKEY_ID = 0x5A32
HOTKEY_MODIFIERS = 0x4000 | 0x2 | 0x1  # MOD_NOREPEAT | MOD_CONTROL | MOD_ALT
HOTKEY_VK = 0x58  # X

# Ctrl+Alt+H hides the game and shows the real desktop (boss key); press it again to continue.
HIDE_HOTKEY_ID = 0x5A33
HIDE_HOTKEY_VK = 0x48  # H
