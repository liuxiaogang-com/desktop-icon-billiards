"""Drawing tests for the table, the balls, the particles and the cue."""
import math
import unittest

from PIL import Image, ImageFont, ImageStat

from dib.art import (GUIDE_DARK, GUIDE_LIGHT, charge_art, cue_art, guide_art, guide_palette, pocket_art, pocket_particles,
                     table_art, white_art)
from dib.table import BilliardsMotion


class TableArtTests(unittest.TestCase):
    def test_frame_covers_the_work_area_with_transparent_felt_and_dark_pockets(self):
        work = (-1920, -120, 0, 960)
        game = BilliardsMotion(work, [(64, 64)] * 2)
        frame, origin_x, origin_y = table_art(game)
        self.assertEqual(frame.mode, 'RGBA')
        self.assertEqual(frame.size, (work[2] - work[0], work[3] - work[1]))
        left, top, right, bottom = game.bounds
        center = ((left + right) // 2 - origin_x, (top + bottom) // 2 - origin_y)
        self.assertEqual(frame.getpixel(center)[3], 0)
        # The desktop area around the rails keeps a faint tint so the table reads.
        self.assertGreater(frame.getpixel((1, 1))[3], 0)
        for px, py in game.pockets:
            # Light pockets: clearly there, but translucent rather than solid black.
            alpha = frame.getpixel((px - origin_x, py - origin_y))[3]
            self.assertGreater(alpha, 90)
            self.assertLess(alpha, 200)

    def test_wastebaskets_remain_on_a_transparent_layer_at_all_six_pockets(self):
        game = BilliardsMotion((-1920, -120, 0, 960), [(64, 64)] * 15)
        source = Image.new('RGBA', (32, 40), (80, 150, 220, 180))
        original = source.tobytes()
        frame, ox, oy = pocket_art(game, source)
        self.assertEqual(source.tobytes(), original)
        self.assertEqual(len(game.pockets), 6)
        for x, y in game.pockets:
            self.assertEqual(frame.getpixel((round(x - ox), round(y - oy))), (80, 150, 220, 180))
        self.assertEqual(frame.getpixel((frame.width // 2, frame.height // 2))[3], 0)
        self.assertEqual(frame.getpixel((0, 0))[3], 0)

    def test_credit_is_drawn_faintly_in_the_bottom_margin_between_pockets(self):
        game = BilliardsMotion((0, 0, 1600, 1000), [(64, 64)] * 2)
        plain, ox, oy = table_art(game)
        credited, _, _ = table_art(game, None, 'GitHub@liuxiaogang-com')
        left, top, right, bottom = game.bounds
        # The text lives in the margin below the table, around three quarters of the way across.
        x0, x1 = round(left + game.width * .55 - ox), round(left + game.width * .95 - ox)
        y0, y1 = round(bottom - oy), round(game.work_area[3] - oy)
        region = (x0, y0, x1, y1)
        changed = [(x, y) for x in range(x0, x1) for y in range(y0, y1)
                   if plain.getpixel((x, y)) != credited.getpixel((x, y))]
        self.assertGreater(len(changed), 40)
        # Faint: nothing is painted fully opaque, and the table itself is untouched.
        self.assertTrue(all(credited.getpixel(point)[3] < 200 for point in changed))
        self.assertEqual(plain.crop((0, 0, plain.width, y0 - 1)).tobytes(),
                         credited.crop((0, 0, credited.width, y0 - 1)).tobytes())
        self.assertEqual(plain.getpixel((x0, y0)), table_art(game, None, '')[0].getpixel((x0, y0)))
        self.assertTrue(region)

    def test_frame_origin_is_the_work_area_origin(self):
        game = BilliardsMotion((100, 50, 1700, 1050), [(64, 64)] * 2)
        frame, origin_x, origin_y = table_art(game)
        self.assertEqual((origin_x, origin_y), (100, 50))
        self.assertEqual(frame.size, (1600, 1000))

    def test_wallpaper_blurs_inside_the_table_only(self):
        game = BilliardsMotion((0, 0, 640, 480), [(32, 32)])
        wallpaper = Image.new('RGB', (640, 480), 'black')
        wallpaper.paste('white', (320, 0, 640, 480))
        frame, _, _ = table_art(game, wallpaper)
        edge = frame.getpixel((319, 240))
        self.assertEqual(edge[3], 255)
        self.assertGreater(edge[0], 60)
        self.assertLess(edge[0], 195)
        self.assertLess(frame.getpixel((1, 1))[3], 255)


class ChargeArtTests(unittest.TestCase):
    def test_bar_fills_from_left_to_right(self):
        font = ImageFont.load_default(size=16)
        empty, half, full = [charge_art(percent, font) for percent in (0, 50, 100)]
        self.assertEqual(empty.size, full.size)
        for x, half_is_filled in ((60, True), (105, False)):
            point = x, full.height // 2
            expected = full if half_is_filled else empty
            self.assertNotEqual(empty.getpixel(point), full.getpixel(point))
            self.assertEqual(half.getpixel(point), expected.getpixel(point))


class WhiteArtTests(unittest.TestCase):
    def test_shaded_ball_is_round_and_transparent_at_the_corners(self):
        ball = white_art(25, shaded=True)
        self.assertEqual(ball.mode, 'RGBA')
        expected = max(3, math.ceil(25 * 2) + 4)
        self.assertEqual(ball.size, (expected, expected))
        self.assertEqual(ball.getpixel((0, 0))[3], 0)
        self.assertGreater(ball.getpixel((expected // 2, expected // 2))[3], 200)

    def test_unshaded_ball_is_drawn_as_an_opaque_disc(self):
        ball = white_art(12)
        expected = max(3, math.ceil(12 * 2) + 4)
        self.assertEqual(ball.size, (expected, expected))
        self.assertEqual(ball.getpixel((0, 0))[3], 0)
        center = ball.getpixel((expected // 2, expected // 2))
        self.assertEqual(center[3], 255)
        self.assertGreater(center[0], 200)


class ParticleArtTests(unittest.TestCase):
    def test_fading_particles_only_paint_inside_the_frame(self):
        base = Image.new('RGBA', (64, 64))
        halo = pocket_particles(base, 1, .5)
        self.assertEqual(halo.size, base.size)
        self.assertGreater(halo.getchannel('A').getextrema()[1], 0)
        self.assertEqual(base.getchannel('A').getextrema()[1], 0)
        invisible = pocket_particles(base, 0, 0.)
        self.assertEqual(invisible.getchannel('A').getextrema()[1], 0)
        done = pocket_particles(base, 0, 1.)
        self.assertEqual(done.getchannel('A').getextrema()[1], 0)

    def test_ripple_grows_while_the_icon_sinks(self):
        base = Image.new('RGBA', (128, 128))

        def ripple_radius(fade):
            frame = pocket_particles(base, 0, fade)
            return max(x for x in range(64, 128) if frame.getpixel((x, 64))[3] > 40) - 64

        self.assertGreater(ripple_radius(.7), ripple_radius(.3))


class CueArtTests(unittest.TestCase):
    def test_cue_frame_contains_the_tip_and_tail(self):
        cue = ((110., 60.), (10., 55.))
        frame, x, y = cue_art(cue)
        side = math.ceil(math.dist(cue[0], cue[1])) + 18
        self.assertEqual(frame.mode, 'RGBA')
        self.assertEqual(frame.size, (side, side))
        self.assertEqual((x, y), (round((110 + 10 - side) / 2), round((60 + 55 - side) / 2)))
        tip = (round(cue[0][0] - x), round(cue[0][1] - y))
        self.assertEqual(frame.getpixel(tip), (126, 207, 228, 255))
        self.assertEqual(frame.getpixel((0, 0))[3], 0)

    def test_thicker_cue_draws_a_bigger_tip(self):
        cue = ((100., 100.), (20., 100.))
        thin = cue_art(cue, 1)[0]
        thick = cue_art(cue, 2)[0]
        self.assertEqual(thin.size, thick.size)
        thin_alpha = ImageStat.Stat(thin.getchannel('A')).sum[0]
        thick_alpha = ImageStat.Stat(thick.getchannel('A')).sum[0]
        self.assertGreater(thick_alpha, thin_alpha)


class FormatDurationTests(unittest.TestCase):
    def test_formats_whole_milliseconds(self):
        from dib.panel import format_duration
        self.assertEqual(format_duration(0), '00:00.00')
        self.assertEqual(format_duration(372_340), '06:12.34')
        self.assertEqual(format_duration(59_999), '00:59.99')
        self.assertEqual(format_duration(3_600_000), '1:00:00.00')
        self.assertEqual(format_duration(-5), '00:00.00')


class GuideArtTests(unittest.TestCase):
    LINES = (('incoming', (100., 100.), (300., 100.)), ('target', (300., 100.), (360., 180.)))

    @staticmethod
    def coverage(frame):
        return ImageStat.Stat(frame.getchannel('A')).sum[0]

    def test_frame_covers_lines_and_circles_on_a_64_pixel_grid(self):
        circles = (('ghost', (300., 100.), 30.), ('target', (330., 140.), 30.))
        frame, left, top = guide_art(self.LINES, circles)
        self.assertEqual((frame.width % 64, frame.height % 64), (0, 0))
        self.assertLessEqual(left, 100 - 9)
        self.assertLessEqual(top, 70 - 9)  # the ghost circle reaches above the lines
        self.assertGreaterEqual(left + frame.width, 360 + 9)
        self.assertGreaterEqual(top + frame.height, 180 + 9)

    def test_palette_follows_the_wallpaper_brightness(self):
        self.assertIs(guide_palette(None, (0, 0), (10, 0)), GUIDE_DARK)
        self.assertIs(guide_palette(lambda x, y: 40, (0, 0), (10, 0)), GUIDE_DARK)
        self.assertIs(guide_palette(lambda x, y: 230, (0, 0), (10, 0)), GUIDE_LIGHT)
        self.assertIs(guide_palette(lambda x, y: None, (0, 0), (10, 0)), GUIDE_DARK)

    def test_dashes_stay_visible_on_dark_and_bright_backgrounds(self):
        dark, bright = guide_art(self.LINES, (), lambda x, y: 20)[0], guide_art(self.LINES, (), lambda x, y: 240)[0]
        for frame, core, casing in ((dark, GUIDE_DARK['incoming'], GUIDE_DARK['casing']),
                                    (bright, GUIDE_LIGHT['incoming'], GUIDE_LIGHT['casing'])):
            row = 100 - 91  # the incoming line's y inside the frame (top = 100 - 9)
            pixels = [frame.getpixel((x, row)) for x in range(12, 200)]
            self.assertTrue(any(abs(p[0] - core[0]) < 30 and p[3] > 200 for p in pixels), 'core colour missing')
            edge = [frame.getpixel((x, row - 2)) for x in range(12, 200)]
            self.assertTrue(any(p[3] > 120 and abs(p[0] - casing[0]) < 40 for p in edge), 'casing missing')

    def test_dashes_have_gaps(self):
        frame = guide_art((('incoming', (100., 100.), (300., 100.)),), (), None)[0]
        row = [frame.getpixel((x, 100 - 91))[3] for x in range(10, 200)]
        self.assertTrue(any(value < 20 for value in row) and any(value > 200 for value in row))

    def test_circles_add_faint_marks_without_hiding_the_lines(self):
        plain = guide_art(self.LINES)[0]
        ringed = guide_art(self.LINES, (('ghost', (300., 100.), 20.),))[0]
        self.assertGreater(self.coverage(ringed), self.coverage(plain) * .99)


class AimPreviewTests(unittest.TestCase):
    def test_a_ball_hit_reports_the_ghost_and_target_circles(self):
        game = BilliardsMotion((0, 0, 1600, 1000), [(64, 64)] * 2)
        target = game.icons[0]
        target.x, target.y = game.white.x + 240, game.white.y
        game.icons[1].y = game.white.y - 300
        game.aim_at(target.x, game.white.y)
        lines, circles = game.aim_preview()
        self.assertEqual(game.aim_guide(), lines)
        roles = {role: (centre, radius) for role, centre, radius in circles}
        self.assertAlmostEqual(roles['ghost'][0][0], target.x - (target.radius + game.white.radius))
        self.assertEqual(roles['ghost'][1], game.white.radius)
        self.assertEqual(roles['target'], ((target.x, target.y), target.radius))
        # The collision circle is larger than the drawn icon, which is what the faint ring reveals.
        self.assertGreater(target.radius, game.tile / 2)

    def test_a_rail_hit_only_shows_the_ghost_circle(self):
        game = BilliardsMotion((0, 0, 4200, 700), [(64, 64)])
        game.white.x = game.bounds[2] - 180
        game.icons[0].y = game.white.y - 150
        game.aim_at(game.bounds[2], game.white.y)
        _, circles = game.aim_preview()
        self.assertEqual([role for role, _, _ in circles], ['ghost'])

    def test_nothing_is_previewed_while_balls_move(self):
        game = BilliardsMotion((0, 0, 1600, 1000), [(64, 64)] * 2)
        game.shoot(.5)
        self.assertEqual(game.aim_preview(), ((), ()))


if __name__ == '__main__':
    unittest.main()
