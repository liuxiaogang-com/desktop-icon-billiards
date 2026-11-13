"""Select real desktop lists and retain the cause of process access failures."""
from contextlib import ExitStack
from types import SimpleNamespace
import ctypes
import unittest
from unittest.mock import Mock, patch

from dib.overlay import desktop_listview, item_rects


class DesktopWindowTests(unittest.TestCase):
    def test_folder_browser_tiny_views_and_other_processes_cannot_win(self):
        classes = {100: 'Progman', 200: '_cls_shellbrowser_', 300: 'WorkerW', 400: 'WorkerW',
                   101: 'SHELLDLL_DefView', 201: 'SHELLDLL_DefView',
                   301: 'SHELLDLL_DefView', 401: 'SHELLDLL_DefView'}
        lists = {101: [110, 111], 201: [210], 301: [310], 401: [410]}
        counts = {110: 0, 111: 85, 210: 99, 310: 200, 410: 150}
        user32 = SimpleNamespace(GetShellWindow=Mock(return_value=100))

        def find(parent, previous, *_):
            children = lists[parent]
            index = children.index(previous) + 1 if previous else 0
            return children[index] if index < len(children) else 0

        with ExitStack() as stack:
            patches = {
                'ctypes.windll.user32': user32,
                'dib.overlay.monitors': Mock(return_value=(('primary', (0, 0, 2160, 1440),
                                                           (0, 0, 2160, 1368), 144, True),)),
                'dib.overlay.dpi_context': Mock(return_value=ExitStack()),
                'dib.overlay.win32gui.EnumWindows': lambda fn, arg: [fn(h, arg) for h in (200, 300, 400, 100)],
                'dib.overlay.win32gui.EnumChildWindows': lambda h, fn, arg: fn(h + 1, arg),
                'dib.overlay.win32gui.GetClassName': lambda h: classes[h],
                'dib.overlay.win32process.GetWindowThreadProcessId': lambda h: (1, 99 if h == 300 else 10),
                'dib.overlay.win32gui.FindWindowEx': find,
                'dib.overlay.win32gui.GetClientRect': lambda h: (0, 0, 1, 1) if h in (401, 410) else (0, 0, 4080, 1440),
                'dib.overlay.win32gui.ClientToScreen': lambda h, pt: (pt[0] - 1920, pt[1]),
                'dib.overlay.win32gui.IsWindowVisible': lambda h: True,
                'dib.overlay.listview_count': lambda h: counts[h],
                'dib.overlay.diag.log': Mock(),
            }
            for name, value in patches.items():
                stack.enter_context(patch(name, value))
            self.assertEqual(desktop_listview(), (101, 111))

    def test_denied_open_preserves_error_code_and_records_target(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 0
        with patch('dib.overlay.ctypes.WinDLL', return_value=kernel), \
                patch('dib.overlay.ctypes.get_last_error', return_value=5), \
                patch('dib.overlay.win32process.GetWindowThreadProcessId', return_value=(1, 123)), \
                patch('dib.overlay.diag.log') as log:
            with self.assertRaises(PermissionError) as caught:
                item_rects(456, [0])
        self.assertEqual(caught.exception.winerror, 5)
        self.assertIn('OpenProcess', str(caught.exception))
        self.assertIn('target_pid=123', log.call_args.args[0])
        kernel.VirtualAllocEx.assert_not_called()
