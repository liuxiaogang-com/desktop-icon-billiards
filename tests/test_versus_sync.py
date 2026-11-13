"""The two halves of LAN versus, exercised headlessly.

Nothing here touches the desktop: the test builds the same pair of tables the two game
clients build, wires them through real loopback sockets, and runs the pump in
:mod:`dib.versus_sync` frame by frame. That covers the parts that are easy to get wrong
(turn ownership, snapshot mirroring, score attribution) without needing two monitors.
"""
import time
import unittest

from dib import netplay
from dib.overlay import Viewport
from dib.table import LOGICAL_BOUNDS, VERSUS_BALLS, BilliardsMotion
from dib.versus_sync import VersusLink

CODE = 'ABCD'
FRAME = 1 / 60


def wait_for(predicate, timeout=6.0, interval=0.01):
    """Poll ``predicate`` until it is truthy, or fail the test after ``timeout``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def make_table(sizes, role):
    table = BilliardsMotion(LOGICAL_BOUNDS, sizes, count=VERSUS_BALLS)
    table.authoritative = role == 'host'
    table.local_side = 0 if role == 'host' else 1
    table.start_versus(VERSUS_BALLS, first=0)
    return table


class LayoutAgreementTests(unittest.TestCase):
    """Both machines must build one identical table from one logical rectangle."""

    def test_icon_sizes_never_change_the_physics_geometry(self):
        small = BilliardsMotion(LOGICAL_BOUNDS, [(48, 48)] * VERSUS_BALLS, count=VERSUS_BALLS)
        large = BilliardsMotion(LOGICAL_BOUNDS, [(256, 150)] * VERSUS_BALLS, count=VERSUS_BALLS)
        for attribute in ('bounds', 'pockets', 'radius', 'white_radius', 'pocket_radius', 'tile', 'rack'):
            self.assertEqual(getattr(small, attribute), getattr(large, attribute), attribute)
        self.assertEqual([(body.x, body.y) for body in small.icons],
                         [(body.x, body.y) for body in large.icons])
        self.assertEqual(len(small.icons), VERSUS_BALLS)
        columns = {}
        for body in small.icons:
            columns[round(body.x, 3)] = columns.get(round(body.x, 3), 0) + 1
        self.assertEqual(sorted(columns.values()), [1, 2, 3, 4, 5])  # a clean triangular rack


class SnapshotTests(unittest.TestCase):
    def test_guest_mirrors_the_host_snapshot_exactly(self):
        host = make_table([(48, 48)] * VERSUS_BALLS, 'host')
        guest = make_table([(64, 64)] * VERSUS_BALLS, 'guest')
        host.white.x, host.white.y = 321.5, 456.25
        host.icons[3].angle = 12.5
        host.icons[3].impact = .11
        guest.apply_snapshot(host.snapshot())
        self.assertEqual((guest.white.x, guest.white.y), (321.5, 456.25))
        self.assertEqual(round(guest.icons[3].angle, 2), 12.5)
        self.assertEqual(round(guest.icons[3].impact, 2), .11)
        self.assertEqual(guest.snapshot()['balls'], host.snapshot()['balls'])

    def test_snapshot_round_trip_is_idempotent(self):
        import json
        host = make_table([(48, 48)] * VERSUS_BALLS, 'host')
        guest = make_table([(48, 48)] * VERSUS_BALLS, 'guest')
        first = json.loads(json.dumps(host.snapshot()))
        guest.apply_snapshot(first)
        self.assertEqual(json.loads(json.dumps(guest.snapshot())), first)

    def test_guest_adopts_a_new_serial_without_resetting_locally(self):
        host = make_table([(48, 48)] * VERSUS_BALLS, 'host')
        guest = make_table([(48, 48)] * VERSUS_BALLS, 'guest')
        before = guest.round_serial
        host.start_versus(VERSUS_BALLS, first=1)
        guest.apply_snapshot(host.snapshot())
        self.assertNotEqual(guest.round_serial, before)
        self.assertEqual(guest.round_serial, host.round_serial)

    def test_guest_records_intents_instead_of_moving_the_balls(self):
        guest = make_table([(48, 48)] * VERSUS_BALLS, 'guest')
        position = (guest.white.x, guest.white.y)
        self.assertFalse(guest.shoot(.8))
        self.assertIsNotNone(guest.pending_shot)
        self.assertEqual((guest.white.x, guest.white.y), position)
        self.assertFalse(guest.place_white(400, 400))
        self.assertIsNotNone(guest.pending_place)


class ViewportTests(unittest.TestCase):
    """The letterbox: one logical table, two screens, identical proportions."""

    def test_scales_uniformly_and_centres_inside_a_wider_screen(self):
        # 2000x1500 work area for a 16:9 table: the width is the limiting axis (1.25).
        view = Viewport.fit(LOGICAL_BOUNDS, (100, 50, 2100, 1550))
        self.assertAlmostEqual(view.scale, 2000 / 1600)
        # Same scale both axes is what keeps balls round.
        self.assertAlmostEqual(view.length(100), 100 * view.scale)
        left, top = view.to_parent(LOGICAL_BOUNDS[0], LOGICAL_BOUNDS[1])
        right, bottom = view.to_parent(LOGICAL_BOUNDS[2], LOGICAL_BOUNDS[3])
        self.assertAlmostEqual(left, 100)                   # flush with the left edge
        self.assertAlmostEqual(right, 2100)                 # and the right edge
        self.assertAlmostEqual(top - 50, 1550 - bottom)     # centred vertically
        self.assertGreater(top, 50)                         # letterboxed top and bottom
        self.assertLess(bottom, 1550)

    def test_never_stretches_the_table(self):
        for target in ((0, 0, 1920, 1080), (0, 0, 1280, 1024), (0, 0, 3840, 2160),
                       (0, 0, 800, 600), (17, 31, 1600, 1200)):
            view = Viewport.fit(LOGICAL_BOUNDS, target)
            width = view.length(LOGICAL_BOUNDS[2] - LOGICAL_BOUNDS[0])
            height = view.length(LOGICAL_BOUNDS[3] - LOGICAL_BOUNDS[1])
            self.assertAlmostEqual(width / height, 16 / 9, places=6, msg=target)

    def test_table_coordinates_round_trip(self):
        view = Viewport.fit(LOGICAL_BOUNDS, (0, 0, 1920, 1080))
        for point in ((0.0, 0.0), (1600.0, 900.0), (321.5, 456.25), (96.0, 81.0)):
            back = view.to_table(*view.to_parent(*point))
            self.assertAlmostEqual(back[0], point[0], places=6)
            self.assertAlmostEqual(back[1], point[1], places=6)

    def test_identity_view_keeps_solo_pixels_untouched(self):
        view = Viewport.identity()
        self.assertTrue(view.is_identity)
        self.assertEqual(view.scale, 1.0)
        self.assertEqual(view.to_parent(321.5, 456.25), (321.5, 456.25))
        self.assertEqual(view.to_table(321.5, 456.25), (321.5, 456.25))
        self.assertEqual(view.length(43.24), 43.24)


class LiveMatchTests(unittest.TestCase):
    """Two tables, two real sessions over loopback, one pump per side."""

    def setUp(self):
        self.host_session = netplay.start_host(CODE, port=0, discovery=False)
        self.addCleanup(self.host_session.close)
        self.guest_session = netplay.join('127.0.0.1', self.host_session.port, CODE, timeout=6.0)
        self.addCleanup(self.guest_session.close)
        self.assertTrue(wait_for(lambda: self.host_session.status == 'connected'),
                        'host never saw the guest')
        self.host = make_table([(48, 48)] * VERSUS_BALLS, 'host')
        self.guest = make_table([(64, 64)] * VERSUS_BALLS, 'guest')
        self.host_link = VersusLink(self.host, self.host_session, 'host')
        self.guest_link = VersusLink(self.guest, self.guest_session, 'guest')
        self.now = 1000.0

    def pump(self, frames=1):
        """Advance both sides; the host integrates the physics, the guest mirrors it."""
        for _ in range(frames):
            self.now += FRAME
            self.host_link.pump(self.now)
            self.host.step(FRAME, self.now)
            self.guest_link.pump(self.now)
            time.sleep(0.001)

    def pump_until(self, predicate, timeout=8.0, frames=1):
        """Pump real frames until ``predicate`` holds, so socket traffic keeps flowing."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.pump(frames)
        return predicate()

    def world_snapshot(self, table):
        return table.snapshot()['balls']

    def test_host_stroke_reaches_the_guest_and_the_turn_is_mirrored(self):
        self.host.aim = (1.0, 0.0)
        self.host.shoot(0.9, self.now)
        self.assertTrue(self.pump_until(lambda: self.host.state not in ('stroke', 'rolling')),
                        'the host stroke never settled')
        self.assertTrue(self.pump_until(
            lambda: self.guest.state == self.host.state and self.guest.turn == self.host.turn),
            'the guest never mirrored the settled table')
        self.assertEqual(self.guest.scores, self.host.scores)
        for left, right in zip(self.world_snapshot(self.host), self.world_snapshot(self.guest)):
            self.assertAlmostEqual(left[0], right[0], places=1)
            self.assertAlmostEqual(left[1], right[1], places=1)

    def test_guest_shot_is_honoured_only_on_the_guest_turn(self):
        # Host holds the table first: a guest shot must be refused.
        self.guest.aim = (-1.0, 0.0)
        self.guest.shoot(0.7)
        self.assertTrue(self.pump_until(lambda: self.guest_link.nack_note),
                        'host accepted an out-of-turn shot')
        self.assertEqual(self.host.state, 'ready')

        # Now hand the table to the guest and try again.
        self.host.turn = 1
        self.guest.turn = 1
        self.guest.aim = (-1.0, 0.0)
        self.guest.shoot(0.7)
        self.assertTrue(self.pump_until(lambda: self.host.state in ('stroke', 'rolling')),
                        'the honoured guest shot never started on the host')

    def test_restart_from_the_guest_restarts_the_host(self):
        serial = self.host.round_serial
        self.guest_link.restart()
        self.pump(20)
        self.assertTrue(wait_for(lambda: self.host.round_serial != serial), 'host ignored the restart')
        self.assertTrue(wait_for(lambda: self.guest.round_serial == self.host.round_serial),
                        'guest did not adopt the new round')

    def test_closing_the_guest_is_reported_to_the_host(self):
        self.guest_session.close('对手退出了')
        self.pump(10)
        self.assertTrue(wait_for(lambda: self.host_link.disconnect_reason),
                        'host never noticed the connection closing')


if __name__ == '__main__':
    unittest.main()
