"""Layer blits must land on whole pixels.

The versus viewport maps the logical table through a scale, so several drawing paths hand
``present()`` floats. Win32 rejects those at ``UpdateLayeredWindow`` — a crash that only
appears in a network match, when the player charging the cue is on the letterboxed client
(the single-player viewport is the identity, so its coordinates are already whole).
"""
import unittest
from unittest.mock import patch

from PIL import Image

from dib import overlay as overlay_module
from dib.overlay import BilliardsOverlay


class PresentCoordinateTests(unittest.TestCase):
    def make_overlay(self):
        overlay = object.__new__(BilliardsOverlay)
        overlay.primary_bounds = (0, 0, 1920, 1080)
        overlay.empty_frame = Image.new('RGBA', (1, 1))
        overlay.surfaces = {}
        return overlay

    def test_float_coordinates_reach_win32_as_integers(self):
        overlay = self.make_overlay()
        frame = Image.new('RGBA', (16, 16))
        with patch.object(overlay_module, 'LayerSurface') as surface:
            overlay.present(1, frame, 123.7, 456.2)
        hwnd, size, x, y = surface.call_args.args
        self.assertEqual((hwnd, size), (1, (16, 16)))
        self.assertEqual((x, y), (124, 456))
        for value in (x, y):
            self.assertIsInstance(value, int)

    def test_a_float_rect_that_needs_clipping_still_crops_with_integers(self):
        overlay = self.make_overlay()
        frame = Image.new('RGBA', (16, 16))
        with patch.object(overlay_module, 'LayerSurface') as surface:
            overlay.present(1, frame, -8.5, -8.5)
        _hwnd, size, x, y = surface.call_args.args
        self.assertEqual((x, y), (0, 0))
        self.assertEqual(size, (8, 8))          # cropped to the visible corner
        for value in (x, y, *size):
            self.assertIsInstance(value, int)

    def test_fully_offscreen_float_rect_falls_back_to_the_empty_frame(self):
        overlay = self.make_overlay()
        frame = Image.new('RGBA', (16, 16))
        with patch.object(overlay_module, 'LayerSurface') as surface:
            overlay.present(1, frame, 5000.5, 5000.5)
        surface.assert_called_once()
        self.assertEqual(surface.call_args.args[2:], (0, 0))


if __name__ == '__main__':
    unittest.main()
