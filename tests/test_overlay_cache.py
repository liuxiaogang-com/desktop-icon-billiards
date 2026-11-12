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


if __name__ == '__main__':
    unittest.main()
