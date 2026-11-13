"""Only readable icon images are selected and duplicated for a game."""
import unittest
from unittest.mock import patch

from PIL import Image, ImageFont

from dib.overlay import BilliardsOverlay


class IconLoadingTests(unittest.TestCase):
    def overlay(self, count):
        overlay = object.__new__(BilliardsOverlay)
        overlay.items = [{'id': f'icon-{i}', 'name': ''} for i in range(count)]
        overlay.rects = [((0, 0, 32, 32), (0, 32, 32, 48))] * count
        overlay.fonts = list(range(count))
        overlay.desktop_total = count
        overlay.skipped = 0
        return overlay

    @patch('dib.overlay.diag.log')
    def test_failed_and_empty_images_are_skipped_before_padding(self, log):
        overlay = self.overlay(48)
        original_items = overlay.items.copy()

        def load(identity, size):
            index = int(identity.split('-')[1])
            if index < 45:
                raise OSError('cannot read icon')
            if index == 45:
                return Image.new('RGBA', (32, 32))
            return Image.new('RGBA', (32, 32), (index, 0, 0, 255))

        with patch('dib.overlay.artwork', side_effect=load):
            overlay.load_icon_sources()
        self.assertEqual(overlay.real_count, 2)
        self.assertEqual(overlay.padded, 13)
        self.assertEqual(overlay.skipped, 46)
        self.assertEqual(len(overlay.sources), 15)
        self.assertEqual({item['id'] for item in overlay.items}, {'icon-46', 'icon-47'})
        for item, source, font in zip(overlay.items, overlay.sources, overlay.fonts):
            index = int(item['id'].split('-')[1])
            self.assertEqual(source.getpixel((0, 0))[0], index)
            self.assertEqual(font, index)
        self.assertEqual(len(original_items), 48)
        self.assertIsNot(overlay.sources[0], overlay.sources[2])

    @patch('dib.overlay.diag.log')
    def test_no_usable_images_reports_no_icons(self, log):
        overlay = self.overlay(2)
        with patch('dib.overlay.artwork', side_effect=lambda identity, size: Image.new('RGBA', (size, size))):
            with self.assertRaisesRegex(RuntimeError, '没有获取到图标'):
                overlay.load_icon_sources()

    @patch('dib.overlay.diag.log')
    def test_high_resolution_source_keeps_desktop_pose_and_bounds_game_texture(self, log):
        overlay = self.overlay(1)
        with patch('dib.overlay.artwork', return_value=Image.new('RGBA', (128, 128), 'red')) as load:
            overlay.load_icon_sources()
        load.assert_called_once_with('icon-0', 128)
        self.assertEqual(overlay.sources[0].size, (128, 128))
        overlay.fonts = [ImageFont.load_default()] * len(overlay.items)
        overlay.templates, overlay.anchors, overlay.drawn = [], [], []
        overlay.primary_bounds = (0, 0, 1920, 1080)
        overlay.build_templates()
        self.assertEqual(overlay.poses[0][:4], (16, 16, 32, 32))
        self.assertEqual(overlay.intro_sources[0].size, (128, 128))
        self.assertLessEqual(max(overlay.sources[0].size), 64)

    @patch('dib.overlay.diag.log')
    def test_high_resolution_failure_falls_back_to_desktop_size(self, log):
        for failure in (OSError('large image unavailable'), Image.new('RGBA', (128, 128))):
            with self.subTest(failure=type(failure).__name__):
                overlay = self.overlay(1)
                with patch('dib.overlay.artwork', side_effect=[failure, Image.new('RGBA', (32, 32), 'red')]) as load:
                    overlay.load_icon_sources()
                self.assertEqual([call.args[1] for call in load.call_args_list], [128, 32])
                self.assertEqual(overlay.real_count, 1)
                self.assertEqual(overlay.skipped, 0)
                self.assertEqual(overlay.sources[0].size, (32, 32))
