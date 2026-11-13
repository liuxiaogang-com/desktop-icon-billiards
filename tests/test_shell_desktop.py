"""Desktop discovery must not dereference an absent Shell object."""
import unittest
from unittest.mock import Mock, patch

from dib.shell import _find_desktop, ShellDesktop


class DesktopDiscoveryTests(unittest.TestCase):
    def test_transient_missing_object_or_window_retries_then_succeeds(self):
        dispatch = object()
        for missing in ((0, None), (100, None), (0, dispatch), (99, dispatch)):
            with self.subTest(missing=missing):
                lookup = Mock(side_effect=[missing, (100, dispatch)])
                with patch('win32gui.IsWindow', side_effect=lambda hwnd: hwnd == 100), \
                        patch('dib.shell.time.sleep') as sleep:
                    self.assertEqual(_find_desktop(lookup), (100, dispatch))
                self.assertEqual(lookup.call_count, 2)
                sleep.assert_called_once_with(.15)

    def test_persistent_missing_desktop_is_bounded_and_other_errors_propagate(self):
        lookup = Mock(return_value=(0, None))
        with patch('dib.shell.time.sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, '暂时无法连接 Windows 桌面'):
                _find_desktop(lookup)
        self.assertEqual(lookup.call_count, 5)
        self.assertEqual(sleep.call_count, 4)
        error = PermissionError('blocked')
        with self.assertRaises(PermissionError) as caught:
            _find_desktop(Mock(side_effect=error))
        self.assertIs(caught.exception, error)


class DesktopDisplayNameTests(unittest.TestCase):
    def desktop(self):
        desktop = object.__new__(ShellDesktop)
        desktop.folder = Mock()
        desktop.shellcon = Mock(SHGDN_NORMAL=0)
        desktop.cache = {r'C:\Users\example\Desktop\报告.lnk': object(),
                         '::{645FF040-5081-101B-9F08-00AA002F954E}': object()}
        return desktop

    def test_screenshot_com_failure_keeps_item_and_next_label_readable(self):
        import pywintypes

        desktop = self.desktop()
        identities = list(desktop.cache)
        cached = desktop.cache.copy()
        desktop.folder.GetDisplayNameOf.side_effect = [
            pywintypes.com_error(-2147467259, '未指定的错误', None, None), '回收站']
        with patch('dib.diag.log') as log:
            names = [desktop.display_name(identity) for identity in identities]
        self.assertEqual(names, ['', '回收站'])
        self.assertEqual(desktop.cache, cached)
        self.assertIn('80004005', log.call_args.args[0])
        self.assertNotIn(identities[0], log.call_args.args[0])

    def test_virtual_item_failure_or_empty_name_omits_label(self):
        import pywintypes

        desktop = self.desktop()
        identity = list(desktop.cache)[1]
        desktop.folder.GetDisplayNameOf.side_effect = [
            pywintypes.com_error(-2147467259, '未指定的错误', None, None), '', '   ']
        with patch('dib.diag.log'):
            for _ in range(3):
                self.assertEqual(desktop.display_name(identity), '')
