"""Collision artwork is reused without changing pixels or shared layers."""
from collections import OrderedDict
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from dib.overlay import BALL_CANVAS, RING_LEVELS, BilliardsOverlay


class CollisionCacheTests(unittest.TestCase):
    def test_colliding_sprites_reuse_layers_without_mutating_them(self):
        overlay = object.__new__(BilliardsOverlay)
        overlay.table = SimpleNamespace(radius=35)
        overlay.frame_cache = OrderedDict()
        overlay.frame_cache_bytes = 0
        overlay.ring_frames = {level: overlay.ring_layer(level) for level in range(1, RING_LEVELS + 1)}
        originals = {level: frame.tobytes() for level, frame in overlay.ring_frames.items()}
        source = Image.new('RGBA', (64, 64), (80, 140, 200, 180))
        with patch.object(overlay, 'ring_layer', side_effect=AssertionError('redrawing a cached ring')):
            for level in range(1, RING_LEVELS + 1):
                for identity in range(45):
                    frame = overlay.sprite_frame(source, identity, 64, 64, identity, ring=level)
                    plain = overlay.sprite_frame(source, identity, 64, 64, identity).copy()
                    plain.alpha_composite(overlay.ring_frames[level])
                    self.assertEqual(frame.tobytes(), plain.tobytes())
        self.assertEqual(originals, {level: frame.tobytes() for level, frame in overlay.ring_frames.items()})
        self.assertEqual(sum(frame.width * frame.height * 4 for frame in overlay.ring_frames.values()),
                         RING_LEVELS * BALL_CANVAS * BALL_CANVAS * 4)


class TableVisibilityTests(unittest.TestCase):
    def test_surface_toggle_switches_between_round_pockets_and_recycle_bins(self):
        overlay = object.__new__(BilliardsOverlay)
        overlay.table_windows = {'rail': 11, 'pockets': 12}
        with patch('dib.overlay.win32gui.ShowWindow') as show, \
                patch.object(overlay, 'position_layer'):
            overlay.set_felt(False)
            self.assertFalse(overlay.felt_visible)
            overlay.set_felt(True)
            self.assertTrue(overlay.felt_visible)
        self.assertEqual([call.args for call in show.call_args_list],
                         [(11, 0), (12, 4), (11, 4), (12, 0)])


if __name__ == '__main__':
    unittest.main()
