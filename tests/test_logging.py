import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dib import diag, ui


class LoggingTests(unittest.TestCase):
    def test_diagnostic_records_rotate_with_a_strict_byte_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'diag.log'
            with patch('dib.diag._path', return_value=path):
                for _ in range(4):
                    diag.log('中文日志' * diag.MAX_BYTES)
            self.assertEqual({item.name for item in path.parent.iterdir()}, {'diag.log', 'diag.old.log'})
            for item in path.parent.iterdir():
                self.assertLessEqual(item.stat().st_size, diag.MAX_BYTES)
                self.assertTrue(item.read_text(encoding='utf-8').endswith('\n'))

    def test_repeated_errors_are_throttled_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with patch('dib.ui.runtime_directory', return_value=path) as runtime, \
                    patch.dict(ui._last_log, {}, clear=True), \
                    patch('dib.ui.time.monotonic', side_effect=[0, 1, 61]):
                for _ in range(3):
                    ui._write_log('rank.log', '错误' * diag.MAX_BYTES)
                self.assertEqual(runtime.call_count, 2)
                self.assertLessEqual((path / 'rank.log').stat().st_size, diag.MAX_BYTES)
                (path / 'rank.log').read_text(encoding='utf-8')
