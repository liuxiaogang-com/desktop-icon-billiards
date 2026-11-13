"""Desktop discovery must not dereference an absent Shell object."""
import unittest
from unittest.mock import Mock, patch

from dib.shell import _find_desktop


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
