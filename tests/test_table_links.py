"""Table credit links share their drawing geometry and require a full click."""
import queue
import unittest

from dib.art import credit_layout
from dib.config import CREDIT, DOUYIN_CREDIT, DOUYIN_URL, REPOSITORY_URL
from dib.overlay import BilliardsOverlay
from dib.table import BilliardsMotion


class TableLinkTests(unittest.TestCase):
    def test_two_rows_fit_below_table_including_offset_screens(self):
        for area in ((0, 0, 640, 480), (-1920, -120, 0, 960), (0, 0, 2560, 1528)):
            game = BilliardsMotion(area, [(48, 48)] * 15)
            _, rows = credit_layout(game, CREDIT + '\n' + DOUYIN_CREDIT)
            self.assertEqual([row[0] for row in rows], [CREDIT, DOUYIN_CREDIT])
            self.assertLess(rows[0][3][3], rows[1][3][1])
            for _, _, _, (l, t, r, b) in rows:
                self.assertGreaterEqual(l, area[0])
                self.assertLessEqual(r, area[2])
                self.assertGreaterEqual(t, game.bounds[3])
                self.assertLessEqual(b, area[3])

    def test_only_same_visible_link_press_and_release_opens(self):
        overlay = object.__new__(BilliardsOverlay)
        overlay.credit_links = [((10, 10, 100, 25), REPOSITORY_URL),
                                ((10, 30, 100, 45), DOUYIN_URL)]
        overlay.credit_was_down = False
        overlay.credit_pressed = None
        overlay.felt_visible = True
        overlay.link_requests = queue.SimpleQueue()
        for y, url in ((15, REPOSITORY_URL), (35, DOUYIN_URL)):
            overlay.update_credit_pointer(20, y, True, True)
            overlay.update_credit_pointer(20, y, False, True)
            self.assertEqual(overlay.link_requests.get_nowait(), url)
        overlay.update_credit_pointer(20, 15, True, True)
        overlay.update_credit_pointer(20, 35, False, True)
        overlay.update_credit_pointer(20, 15, True, False)
        overlay.update_credit_pointer(20, 15, False, False)
        overlay.felt_visible = False
        overlay.update_credit_pointer(20, 15, True, True)
        overlay.update_credit_pointer(20, 15, False, True)
        self.assertTrue(overlay.link_requests.empty())
