"""Behaviour tests for the manual desktop billiards table."""
import math
import unittest

from dib.table import BilliardsMotion


class BilliardsMotionTests(unittest.TestCase):
    def test_flags_and_public_attributes_match_the_manual_contract(self):
        game = BilliardsMotion((0, 0, 1600, 1000), [(64, 64)] * 3)
        self.assertTrue(game.desktop)
        self.assertTrue(game.manual)
        self.assertEqual(len(game.pockets), 6)
        self.assertEqual(len(game.bodies), 1 + len(game.icons))
        self.assertIs(game.bodies[0], game.white)
        self.assertEqual(game.state, 'ready')
        self.assertIsNotNone(game.aim)
        self.assertEqual(game.poses, game._poses())
        for attribute in ('sizes', 'bounds', 'pocket_radius', 'icons', 'exits', 'charge'):
            self.assertTrue(hasattr(game, attribute), attribute)
        self.assertTrue(callable(game.reset))
        self.assertTrue(callable(game.step))

    def test_sinking_icon_slides_into_the_pocket_shrinking_and_fading(self):
        work = (-1920, -120, 0, 960)
        game = BilliardsMotion(work, [(64, 64)] * 2)
        game.state = 'rolling'
        px, py = game.pockets[0]
        game.icons[0].x, game.icons[0].y = px + 30, py + 20
        game.step(.01, 0)
        self.assertIn(1, game.sunk)
        self.assertNotIn(game.icons[0], game.active_bodies())
        x, y, fade, scale, spin = game.exit_visual(1)
        self.assertEqual((fade, scale, spin), (0, 1, 0))
        previous = (math.dist((x, y), (px, py)), 1.)
        for frame in range(1, 11):
            game.step(.05, frame * .05)
            visual = game.exit_visual(1)
            if visual is None:
                break
            x, y, fade, scale, spin = visual
            self.assertTrue(work[0] < x < work[2] and work[1] < y < work[3])
            distance = math.dist((x, y), (px, py))
            self.assertLessEqual(distance, previous[0] + 1e-9)   # only ever moves towards the pocket
            self.assertLessEqual(scale, previous[1] + 1e-9)      # only ever shrinks
            previous = (distance, scale)
        self.assertLess(previous[0], 1.0)
        self.assertLess(previous[1], .5)
        for frame in range(8):
            game.step(.05, .6 + frame * .05)
        self.assertIsNone(game.exit_visual(1))

    def test_cue_length_charge_and_stroke_approach(self):
        # A tall work area means the cue is long enough to reach its 440 px cap.
        game = BilliardsMotion((0, 0, 2400, 1400), [(64, 64)] * 2)
        game.aim_at(game.white.x, game.white.y + 200)
        tip, tail = game.cue()
        self.assertAlmostEqual(tip[1] - tail[1], min(440, game.height * .48), delta=1)
        self.assertAlmostEqual(tip[0], game.white.x, delta=.01)
        distance = game.white.y - tip[1]
        self.assertAlmostEqual(distance, game.white.radius + 12, delta=.5)
        game.charge = 1.
        charged_tip = game.cue()[0]
        self.assertAlmostEqual(game.white.y - charged_tip[1], game.white.radius + 46, delta=.5)

        game.charge = 0.
        game.state = 'stroke'
        game.stroke = .041
        drawn_tip = game.cue()[0]
        self.assertGreater(drawn_tip[1], tip[1])
        game.state = 'rolling'
        self.assertIsNone(game.cue())

    def test_manual_shoot_uses_desktop_power_multiplier(self):
        game = BilliardsMotion((0, 0, 1600, 1000), [(64, 64)] * 2)
        game.aim = (1., 0.)
        self.assertTrue(game.shoot(1))
        self.assertEqual(game.state, 'stroke')
        self.assertFalse(game.shoot(1))
        game.step(.05, 0.)
        self.assertEqual(game.white.vx, 0)
        game.step(.05, .05)
        expected = game.height * (.62 + 2.55) * 1.58
        self.assertAlmostEqual(game.white.vx, expected, delta=expected * .01)
        self.assertEqual(game.white.vy, 0)

    def test_manual_shoot_collision_chain_rebounds_and_rearms(self):
        game = BilliardsMotion((0, 0, 1600, 1000), [(64, 64)] * 2)
        # Put two balls in the same line so the first must pass its impulse on.
        for index, body in enumerate(game.icons):
            body.x = game.white.x + 250 + index * (game.radius * 2 + 8)
            body.y = game.white.y
        game.aim = game._aim()
        self.assertTrue(game.shoot(.9))
        tip_before = game.cue()[0][0]
        game.step(.04, .04)
        self.assertGreater(game.cue()[0][0], tip_before)
        self.assertEqual(game.white.vx, 0)
        second = game.icons[1]
        moved = False
        reflected = False
        for frame in range(460):
            game.step(.012, .45 + frame * .012)
            moved |= second.vx > 50
            reflected |= moved and second.vx < -30
        self.assertTrue(moved)
        self.assertTrue(reflected)
        self.assertEqual(game.state, 'ready')
        self.assertIsNotNone(game.cue())
        self.assertTrue(game.shoot(.7))

    def test_rack_and_moving_balls_stay_inside_main_table(self):
        for count in (1, 61, 300):
            with self.subTest(count=count):
                game = BilliardsMotion((-1600, -250, 0, 750), [(128, 76)] * count)
                self.assertTrue(game.shoot(1))
                for frame in range(100):
                    game.step(.016, frame * .016)
                    left, top, right, bottom = game.bounds
                    for body in game.active_bodies():
                        self.assertLessEqual(left + body.radius, body.x)
                        self.assertLessEqual(body.x, right - body.radius)
                        self.assertLessEqual(top + body.radius, body.y)
                        self.assertLessEqual(body.y, bottom - body.radius)

    def test_white_scratch_returns_ball_in_hand_and_can_be_replaced(self):
        game = BilliardsMotion((0, 0, 1600, 1000), [(64, 64)] * 3)
        self.assertEqual(len(game.pockets), 6)
        origin = (game.white.x, game.white.y)
        game.state = 'rolling'
        game.white.x, game.white.y = game.pockets[0]
        game.step(.01, 0)
        self.assertTrue(game.white_sunk)
        self.assertEqual(game.state, 'return_white')
        self.assertFalse(game.shoot(1))
        self.assertFalse(game.place_white(*game.pockets[0]))
        for frame in range(20):
            game.step(.05, frame * .05)
        # The white is returned automatically when a free spot exists.
        self.assertEqual(game.state, 'ready')
        self.assertFalse(game.white_sunk)
        self.assertEqual((game.white.x, game.white.y), origin)

        # A full table leaves the white in hand until the player places it.
        game._auto_place = lambda: False
        game.state = 'rolling'
        game.white.x, game.white.y = game.pockets[1]
        game.step(.01, 0)
        for frame in range(20):
            game.step(.05, frame * .05)
        self.assertEqual(game.state, 'ball_in_hand')
        self.assertFalse(game.shoot(1))
        left, top, right, bottom = game.bounds
        self.assertFalse(game.place_white(*game.pockets[1]))
        self.assertFalse(game.place_white(left - 10, top + game.height * .3))
        self.assertTrue(game.place_white(left + game.width * .25, top + game.height * .3))
        self.assertEqual(game.state, 'ready')
        self.assertTrue(game.shoot(.8))

    def test_round_counts_icons_and_freezes_time_until_restart(self):
        game = BilliardsMotion((0, 0, 1600, 1000), [(64, 64)] * 2)
        game.step(.01, 5)
        self.assertEqual(game.round_seconds, 0)  # aiming before the first shot is not timed
        self.assertTrue(game.shoot(1, 10))
        game.state = 'rolling'
        game.white.x, game.white.y = game.pockets[0]
        game.icons[0].x, game.icons[0].y = game.pockets[1]
        game.step(.01, 16)
        self.assertEqual(game.sunk, {0, 1})
        self.assertEqual(game.pocketed_icons, 1)
        self.assertEqual(game.round_seconds, 6)

        game.state = 'rolling'
        game.icons[1].x, game.icons[1].y = game.pockets[2]
        game.step(.01, 24)
        self.assertEqual(game.state, 'finished')
        self.assertEqual(game.pocketed_icons, 2)
        self.assertFalse(game.shoot(.8))
        game.step(.05, 60)
        self.assertEqual(game.state, 'finished')
        self.assertEqual(game.round_seconds, 14)

        game.reset()
        self.assertEqual(game.sunk, set())
        self.assertEqual(game.state, 'ready')
        self.assertEqual(game.round_seconds, 0)
        self.assertTrue(game.break_shot)
        self.assertEqual(len(game.bodies), 3)
        game.step(.01, 61)
        self.assertTrue(game.shoot(1, 61))
        game.step(.01, 63)
        self.assertEqual(game.round_seconds, 2)

    def test_round_timer_is_integer_milliseconds_from_first_shot_to_clear(self):
        game = BilliardsMotion((0, 0, 1600, 1000), [(64, 64)] * 2)
        serial = game.round_serial
        self.assertEqual((game.play_ms, game.shots), (0, 0))
        game.step(.01, 100.0)
        self.assertEqual(game.play_ms, 0)
        self.assertTrue(game.shoot(1, 100.0123))
        self.assertEqual(game.shots, 1)
        game.state = 'rolling'
        game.step(.01, 101.0)
        self.assertEqual(game.play_ms, 988)  # 101.0 - 100.0123 = 0.9877 s
        for icon, pocket in zip(game.icons, game.pockets[1:]):
            icon.x, icon.y = pocket
        game.state = 'rolling'
        game.step(.01, 102.5)
        self.assertEqual(game.state, 'finished')
        self.assertEqual(game.play_ms, 2488)
        self.assertIsInstance(game.play_ms, int)
        game.step(.05, 500.0)
        self.assertEqual(game.play_ms, 2488)  # frozen after the clear

        game.reset()
        self.assertEqual((game.play_ms, game.shots), (0, 0))
        self.assertEqual(game.round_serial, serial + 1)

    def test_hidden_time_does_not_count_towards_the_round(self):
        game = BilliardsMotion((0, 0, 1600, 1000), [(64, 64)] * 2)
        game.shoot(1, 10.0)
        game.state = 'rolling'
        game.step(.01, 12.0)
        self.assertEqual(game.play_ms, 2000)
        game.pause(12.0)
        game.pause(30.0)                      # a second pause call does not move the pause point
        game.resume(612.0)                    # ten minutes later
        game.step(.01, 613.0)
        self.assertEqual(game.play_ms, 3000)  # 2 s before + 1 s after the pause
        game.resume(700.0)                    # resuming without a pause is a no-op
        game.step(.01, 614.0)
        self.assertEqual(game.play_ms, 4000)

    def test_pausing_before_the_first_shot_or_after_the_clear_changes_nothing(self):
        game = BilliardsMotion((0, 0, 1600, 1000), [(64, 64)] * 2)
        game.pause(1.0)
        game.resume(50.0)
        self.assertEqual(game.play_ms, 0)
        game.shoot(1, 60.0)
        game.state = 'rolling'
        for icon, pocket in zip(game.icons, game.pockets[1:]):
            icon.x, icon.y = pocket
        game.step(.01, 65.0)
        self.assertEqual(game.state, 'finished')
        game.pause(66.0)
        game.resume(900.0)
        self.assertEqual(game.play_ms, 5000)

    def test_only_accepted_shots_are_counted(self):
        game = BilliardsMotion((0, 0, 1600, 1000), [(64, 64)] * 2)
        self.assertTrue(game.shoot(1, 1.0))
        self.assertFalse(game.shoot(1, 1.1))  # already in the stroke
        self.assertEqual(game.shots, 1)

    def test_aim_guide_predicts_both_directions_after_a_cut_shot(self):
        game = BilliardsMotion((0, 0, 1600, 1000), [(64, 64)] * 2)
        target, blocker = game.icons
        target.x, target.y = game.white.x + 240, game.white.y + 15
        contact_radius = target.radius + game.white.radius
        forward = math.sqrt(contact_radius ** 2 - 15 ** 2)
        direction = forward / contact_radius, 15 / contact_radius
        blocker.x = target.x + 220 * direction[0]
        blocker.y = target.y + 220 * direction[1]
        game.aim_at(target.x, game.white.y)
        incoming, white, outgoing = game.aim_guide()
        self.assertEqual([segment[0] for segment in (incoming, white, outgoing)], ['incoming', 'white', 'target'])
        self.assertAlmostEqual(incoming[2][0], target.x - forward)
        self.assertEqual(white[1], incoming[2])
        self.assertLess(white[2][1], game.white.y)
        self.assertEqual(outgoing[1], (target.x, target.y))
        self.assertAlmostEqual(math.dist(outgoing[2], (blocker.x, blocker.y)), target.radius + blocker.radius)
        game.shoot(.5)
        self.assertEqual(game.aim_guide(), ())

    def test_aim_guide_rebounds_from_the_rail_and_marks_the_stop(self):
        game = BilliardsMotion((0, 0, 4200, 700), [(64, 64)])
        left, _, right, _ = game.bounds
        game.white.x = right - 180
        game.icons[0].y = game.white.y - 150
        game.aim_at(right, game.white.y)
        incoming, rebound = game.aim_guide()
        self.assertAlmostEqual(incoming[2][0], right - game.white.radius)
        self.assertEqual(rebound[0], 'white')
        self.assertEqual(rebound[1], incoming[2])
        self.assertEqual(rebound[2][1], game.white.y)
        self.assertGreater(rebound[2][0], left + game.white.radius)
        self.assertLess(rebound[2][0], game.white.x)
        game.charge = 1
        self.assertAlmostEqual(game.aim_guide()[1][2][0], left + game.white.radius)

    def test_cut_shot_direction_matches_guide_at_different_frame_rates(self):
        for fps in (30, 60, 144):
            for offset in (15, 50, 65, 68):
                with self.subTest(fps=fps, offset=offset):
                    game = BilliardsMotion((0, 0, 1600, 1000), [(64, 64)])
                    target = game.icons[0]
                    target.x, target.y = game.white.x + 240, game.white.y + offset
                    game.aim, game.charge = (1., 0.), 1.
                    line = next(line for line in game.aim_guide() if line[0] == 'target')
                    expected = math.atan2(line[2][1] - line[1][1], line[2][0] - line[1][0])
                    game.shoot(1)
                    for frame in range(fps):
                        game.step(1 / fps, frame / fps)
                        if math.hypot(target.vx, target.vy) > 0:
                            break
                    self.assertGreater(math.hypot(target.vx, target.vy), 0)
                    self.assertAlmostEqual(math.atan2(target.vy, target.vx), expected, delta=.005)

    def test_all_corner_mouths_accept_paths_predicted_to_sink_on_small_screens(self):
        for work in ((0, 0, 1366, 728), (-1024, 0, 0, 728), (0, 0, 1920, 1080)):
            for corner in (0, 2, 3, 5):
                for fps in (30, 144):
                    with self.subTest(work=work, corner=corner, fps=fps):
                        game = BilliardsMotion(work, [(64, 64)] * 15)
                        game.sunk = set(range(2, 16))
                        ball = game.icons[0]
                        px, py = game.pockets[corner]
                        dx = 1 if corner in (0, 3) else -1
                        dy = 1 if corner in (0, 2) else -1
                        ball.x, ball.y = px + dx * 150, py + dy * 150
                        velocity = (-dx * 700, -dy * 700)
                        _, kind, _, _ = game._guide_leg((ball.x, ball.y), velocity, ball.radius, [])
                        self.assertEqual(kind, 'pocket')
                        ball.vx, ball.vy = velocity
                        game.state = 'rolling'
                        for frame in range(fps):
                            game.step(1 / fps, frame / fps)
                            if 1 in game.sunk:
                                break
                        self.assertIn(1, game.sunk)

    def test_reset_restores_the_original_rack(self):
        game = BilliardsMotion((0, 0, 1600, 1000), [(64, 64), (128, 76), (48, 96)])
        original = [(body.x, body.y) for body in game.bodies]
        original_poses = list(game.poses)
        game.shoot(1)
        for frame in range(30):
            game.step(.016, frame * .016)
        game.sunk.add(1)
        game.white_sunk = True
        game.reset()
        self.assertEqual(game.state, 'ready')
        self.assertEqual(game.sunk, set())
        self.assertFalse(game.white_sunk)
        self.assertTrue(game.break_shot)
        self.assertEqual([(body.x, body.y) for body in game.bodies], original)
        self.assertEqual(game.poses, original_poses)

    def test_aim_at_ignores_states_other_than_ready(self):
        game = BilliardsMotion((0, 0, 1600, 1000), [(64, 64)] * 2)
        game.aim_at(game.white.x + 100, game.white.y)
        self.assertAlmostEqual(game.aim[0], 1., delta=.01)
        self.assertAlmostEqual(game.aim[1], 0., delta=.01)
        game.state = 'rolling'
        game.aim_at(game.white.x - 100, game.white.y)
        self.assertAlmostEqual(game.aim[0], 1., delta=.01)


if __name__ == '__main__':
    unittest.main()
