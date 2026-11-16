"""Tests assert against the shipped copy, so the interface language is pinned here.

The application follows the Windows interface language by default; a machine with an English
Windows would otherwise run these assertions against the English catalog. ``tests/test_i18n.py``
switches the language explicitly when it exercises another catalog.
"""
import os

os.environ['DIB_LANG'] = 'zh-CN'
