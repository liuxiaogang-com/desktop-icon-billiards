"""LAN versus rules on top of the shared logical table (see docs/NETPLAY.md)."""
import json
import unittest

from dib.table import LOGICAL_BOUNDS, VERSUS_BALLS, BilliardsMotion

# The fake desktop icons used by every test; versus physics must not depend on them.
SIZES = [(48, 48)] * 15


def versus_table(first=0, sizes=None):
    """A table built the way the overlay builds it for a versus match."""
    table = BilliardsMotion(LOGICAL_BOUNDS, list(sizes or SIZES), count=VERSUS_BALLS)
    table.start_versus(VERSUS_BALLS, first=first)
    return table


def stroke(table, icons=(), white=False, now=10.0, power=.8):
    """Fire a host stroke that pots ``icons`` (and optionally the white), then settle it.

    Placing the balls straight into pocket centres and zeroing the stroke delay keeps the
    test about the rules, not about the integrator.
    """
    table.aim = (1., 0.)
    if not table.shoot(power, now):
        raise AssertionError('the table must accept a shot in the ready state')
    table.state = 'rolling'
    for index in icons:
        table.icons[index].x, table.icons[index].y = table.pockets[index % len(table.pockets)]
    if white:
        table.white.x, table.white.y = table.pockets[5]
    table.step(.01, now)


class ForcedCountTests(unittest.TestCase):
    def test_forced_count_builds_the_logical_rack(self):
        table = BilliardsMotion(LOGICAL_BOUNDS, [(64, 64)] * 20, count=VERSUS_BALLS)
        self.assertEqual(len(table.icons), 15)
        self.assertEqual(len(table.bodies), 16)
        self.assertEqual(len(table.poses), 15)
        # The logical bounds get the fixed 6%/9% inset; everything else hangs off them.
        self.assertEqual(table.bounds, (96, 81, 1504, 819))
        self.assertEqual(len(table.pockets), 6)
        self.assertEqual(table.pockets[1], ((96 + 1504) / 2, 81))
        self.assertAlmostEqual(table.pocket_radius, min(49, max(18, table.height * .047)))
        self.assertAlmostEqual(table.radius, table.pitch * .46)
        self.assertLess(table.white_radius, table.radius)
        self.assertLess(table.tile, table.pitch)
        self.assertEqual(len(table.rack), 15)
        rows = {}
        for x, _ in table.rack:
            rows[round(x, 6)] = rows.get(round(x, 6), 0) + 1
        self.assertEqual(len(rows), 5)
        self.assertEqual(sorted(rows.values()), [1, 2, 3, 4, 5])

    def test_default_count_matches_the_icon_count(self):
        sizes = [(64, 64), (128, 76), (48, 96)]
        default = BilliardsMotion(LOGICAL_BOUNDS, sizes)
        forced = BilliardsMotion(LOGICAL_BOUNDS, sizes, count=len(sizes))
        for attribute in ('bounds', 'pockets', 'radius', 'tile', 'white_radius',
                          'pocket_radius', 'rack'):
            self.assertEqual(getattr(default, attribute), getattr(forced, attribute), attribute)
        self.assertEqual([(body.x, body.y) for body in default.bodies],
                         [(body.x, body.y) for body in forced.bodies])
        self.assertEqual(default.poses, forced.poses)

    def test_icon_sizes_do_not_change_the_logical_table(self):
        sizes_a = [(64, 64)] * 20
        sizes_b = [(33, 91), (120, 40), (48, 48), (80, 60), (17, 17), (96, 72)]
        host = BilliardsMotion(LOGICAL_BOUNDS, sizes_a, count=VERSUS_BALLS)
        guest = BilliardsMotion(LOGICAL_BOUNDS, sizes_b, count=VERSUS_BALLS)
        for attribute in ('bounds', 'pockets', 'radius', 'tile', 'white_radius',
                          'pocket_radius', 'rack', 'width', 'height'):
            self.assertEqual(getattr(host, attribute), getattr(guest, attribute), attribute)
        self.assertEqual([(body.x, body.y) for body in host.icons],
                         [(body.x, body.y) for body in guest.icons])
        self.assertEqual(len(host.icons), len(guest.icons))
        # Six icon sizes still produce a pose for all fifteen balls.
        self.assertEqual(len(guest.poses), 15)

    def test_start_versus_can_enlarge_a_single_player_rack(self):
        single = BilliardsMotion(LOGICAL_BOUNDS, [(64, 64)] * 3)
        self.assertEqual(len(single.icons), 3)
        single.start_versus()
        self.assertEqual(len(single.icons), 15)
        self.assertEqual(len(single.bodies), 16)
        self.assertEqual(len(single.poses), 15)
        self.assertEqual(single.turn, 0)
        self.assertEqual(single.scores, [0, 0])
        self.assertEqual(single.state, 'ready')


class StartVersusTests(unittest.TestCase):
    def test_start_versus_initialises_the_match(self):
        table = BilliardsMotion(LOGICAL_BOUNDS, SIZES, count=VERSUS_BALLS)
        self.assertFalse(table.versus)
        self.assertTrue(table.authoritative)
        self.assertEqual(table.local_side, 0)
        table.start_versus()
        self.assertTrue(table.versus)
        self.assertEqual((table.turn, table.scores, table.outcome), (0, [0, 0], 0))
        self.assertEqual(table.state, 'ready')
        self.assertEqual(table.round_serial, 2)   # construction plus the versus start
        self.assertTrue(table.can_shoot())

    def test_can_shoot_follows_the_local_side_then_the_turn(self):
        host = versus_table()
        self.assertTrue(host.can_shoot())                 # host side 0 has the first shot
        host.start_versus(first=1)
        self.assertFalse(host.can_shoot())                # guest side 0 starts: host must wait
        host.local_side = 1                               # pretend we are the guest
        self.assertTrue(host.can_shoot())
        host.state = 'rolling'
        self.assertFalse(host.can_shoot())

    def test_restart_with_a_new_first_side_keeps_the_roles(self):
        table = versus_table()
        table.authoritative = False
        table.local_side = 1
        table.start_versus(first=1)
        self.assertFalse(table.authoritative)             # role settings survive a restart
        self.assertEqual(table.local_side, 1)
        self.assertEqual(table.turn, 1)
        self.assertTrue(table.can_shoot())


class TurnAndScoreTests(unittest.TestCase):
    def test_potting_a_ball_scores_and_keeps_the_turn(self):
        table = versus_table()
        stroke(table, icons=[0], now=10.0)
        self.assertEqual(table.scores, [1, 0])
        self.assertEqual(table.turn, 0)                   # a pot lets the shooter continue
        self.assertEqual(table.state, 'ready')
        self.assertEqual(table.pocketed_icons, 1)
        self.assertEqual(table.shots, 1)
        self.assertTrue(table.can_shoot())

    def test_the_score_follows_the_shooter_not_the_host(self):
        table = versus_table(first=1)
        table.local_side = 1
        stroke(table, icons=[2], now=10.0)
        self.assertEqual(table.scores, [0, 1])
        self.assertEqual(table.turn, 1)
        self.assertEqual(table.pocketed_icons, 1)

    def test_two_pots_in_one_stroke_score_two(self):
        table = versus_table()
        stroke(table, icons=[0, 4], now=10.0)
        self.assertEqual(table.scores, [2, 0])
        self.assertEqual(table.turn, 0)
        self.assertEqual(table.pocketed_icons, 2)

    def test_missing_passes_the_turn_without_scoring(self):
        table = versus_table()
        stroke(table, now=10.0)
        self.assertEqual(table.scores, [0, 0])
        self.assertEqual(table.turn, 1)
        self.assertEqual(table.state, 'ready')
        self.assertFalse(table.can_shoot())               # the host must wait now

    def test_scratch_returns_the_white_and_passes_the_turn(self):
        table = versus_table()
        table._auto_place = lambda: False                 # keep the ball-in-hand state visible
        stroke(table, white=True, now=10.0)
        self.assertEqual(table.state, 'return_white')
        self.assertTrue(table.white_sunk)
        self.assertIn(0, table.sunk)
        self.assertEqual(table.pocketed_icons, 0)
        self.assertEqual(table.scores, [0, 0])            # the white does not score
        self.assertEqual(table.turn, 1)                   # a scratch hands the table over
        for frame in range(20):
            table.step(.05, 10.05 + frame * .05)
        self.assertEqual(table.state, 'ball_in_hand')
        self.assertEqual(table.turn, 1)
        self.assertFalse(table.can_shoot())
        left, top, _, _ = table.bounds
        self.assertFalse(table.place_white(*table.pockets[0]))           # inside a pocket
        self.assertFalse(table.place_white(left - 10, top + table.height * .3))
        self.assertTrue(table.place_white(left + table.width * .3, top + table.height * .4))
        self.assertEqual(table.state, 'ready')
        self.assertFalse(table.white_sunk)
        self.assertEqual(table.turn, 1)

    def test_scratch_with_a_pot_scores_but_still_passes_the_turn(self):
        table = versus_table()
        table._auto_place = lambda: False
        stroke(table, icons=[1], white=True, now=10.0)
        self.assertEqual(table.scores, [1, 0])
        self.assertEqual(table.turn, 1)
        self.assertEqual(table.state, 'return_white')

    def test_clearing_the_table_finishes_with_the_score_outcome(self):
        for scores, outcome in (([0, 0], 1), ([0, 18], 2), ([0, 15], 3)):
            with self.subTest(scores=scores, outcome=outcome):
                table = versus_table()
                table.scores = list(scores)
                stroke(table, icons=range(len(table.icons)), now=10.0)
                self.assertEqual(table.state, 'finished')
                self.assertEqual(table.outcome, outcome)
                self.assertEqual(table.pocketed_icons, 15)
                self.assertEqual(table.scores, [scores[0] + 15, scores[1]])
                self.assertFalse(table.can_shoot())
                self.assertFalse(table.shoot(1, 11.0))
                # The round timer is frozen once the table is cleared.
                frozen = table.play_ms
                table.step(.05, 60.0)
                self.assertEqual(table.play_ms, frozen)
                self.assertEqual(table.state, 'finished')


class SnapshotTests(unittest.TestCase):
    def test_snapshot_is_json_safe_and_round_trips_through_apply_snapshot(self):
        host = versus_table()
        stroke(host, icons=[0], now=10.0)                 # one potted icon with an exit animation
        host.charge = .42                                 # a snapshot taken mid charge
        state = host.snapshot()
        payload = json.dumps(state)                       # must not raise
        self.assertEqual(json.loads(payload), state)
        self.assertEqual(len(state['balls']), 15)
        self.assertEqual(len(state['sunk']), 16)          # the white is bodies[0]
        self.assertEqual(state['exits'], {str(index): list(values)
                                          for index, values in host.exits.items()})
        self.assertIsInstance(state['ms'], int)
        self.assertEqual(state['charge'], .42)

        guest = BilliardsMotion(LOGICAL_BOUNDS, SIZES, count=VERSUS_BALLS)
        guest.authoritative = False
        guest.local_side = 1
        guest.start_versus()
        guest.apply_snapshot(json.loads(payload))
        self.assertEqual(guest.snapshot(), state)
        self.assertEqual(guest.round_serial, host.round_serial)
        self.assertEqual(guest.state, 'ready')
        self.assertEqual(guest.scores, [1, 0])
        self.assertEqual(guest.sunk, host.sunk)
        self.assertEqual(guest.turn, host.turn)
        self.assertEqual(len(guest.exits), 1)
        self.assertAlmostEqual(guest.icons[0].x, state['balls'][0][0])
        self.assertEqual(guest.poses, guest._poses())

    def test_apply_snapshot_mirrors_the_whole_table(self):
        host = versus_table(first=1)
        host.scores = [3, 4]
        stroke(host, icons=[0, 1], now=10.0)
        host.turn = 0
        guest = versus_table(first=1)
        guest.authoritative = False
        guest.apply_snapshot(host.snapshot())
        self.assertEqual(guest.state, host.state)
        self.assertEqual(guest.scores, [3, 6])            # side 1 shot, so side 1 scored
        self.assertEqual(guest.outcome, host.outcome)
        self.assertEqual(guest.turn, 0)
        self.assertEqual(guest.sunk, host.sunk)
        self.assertEqual([(round(body.x, 2), round(body.y, 2), round(body.angle, 2),
                           round(body.impact, 2)) for body in guest.icons],
                         [(round(body.x, 2), round(body.y, 2), round(body.angle, 2),
                           round(body.impact, 2)) for body in host.icons])
        self.assertEqual((round(guest.white.x, 2), round(guest.white.y, 2)),
                         (round(host.white.x, 2), round(host.white.y, 2)))
        self.assertEqual(guest.remote_ms, host.play_ms)
        self.assertEqual(guest.charge, round(host.charge, 2))

    def test_apply_snapshot_keeps_the_local_serial_until_the_host_restarts(self):
        host = versus_table()
        guest = versus_table()
        guest.authoritative = False
        serial = guest.round_serial
        self.assertEqual(serial, host.round_serial)
        guest.apply_snapshot(host.snapshot())
        self.assertEqual(guest.round_serial, serial)      # a plain mirror never restarts us
        host.reset()                                      # the host starts a new round
        guest.apply_snapshot(host.snapshot())
        self.assertEqual(guest.round_serial, host.round_serial)
        self.assertEqual(guest.round_serial, serial + 1)

    def test_remote_ms_is_the_host_clock(self):
        host = versus_table()
        self.assertTrue(host.shoot(.5, 10.0))
        host.state = 'rolling'
        host.step(.01, 15.0)                              # five seconds after the first shot
        self.assertEqual(host.play_ms, 5000)
        guest = versus_table()
        guest.authoritative = False
        guest.local_side = 1
        self.assertEqual(guest.play_ms, 0)
        guest.apply_snapshot(host.snapshot())
        self.assertEqual(guest.remote_ms, 5000)
        self.assertEqual(guest.play_ms, 5000)
        self.assertEqual(guest.round_seconds, 5)
        self.assertEqual(guest.snapshot()['ms'], 5000)

    def test_a_mirroring_table_stops_counting_and_placing_for_itself(self):
        guest = versus_table(first=1)
        guest.authoritative = False
        guest.local_side = 1
        self.assertTrue(guest.can_shoot())
        guest.aim = (-1., -.02)
        white = (guest.white.x, guest.white.y)
        positions = [(body.x, body.y) for body in guest.bodies]
        self.assertFalse(guest.shoot(.73, 10.0))
        self.assertEqual(guest.pending_shot, ((-1., -.02), .73))
        self.assertEqual(guest.state, 'ready')
        self.assertEqual(guest.shots, 0)
        self.assertEqual((guest.white.x, guest.white.y), white)
        guest.state = 'ball_in_hand'
        self.assertFalse(guest.place_white(400., 300.))
        self.assertEqual(guest.pending_place, (400., 300.))
        self.assertEqual(guest.state, 'ball_in_hand')
        self.assertEqual([(body.x, body.y) for body in guest.bodies], positions)
        # Even a stroke forced into the table does not integrate on the mirroring side.
        guest.icons[0].x, guest.icons[0].y = guest.pockets[0]
        guest.step(.05, 20.0)
        self.assertEqual(guest.sunk, set())
        self.assertEqual(guest.pocketed_icons, 0)


if __name__ == '__main__':
    unittest.main()
