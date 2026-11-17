"""Switching the interface language from the toolbar at runtime."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import win32api
from PIL import Image

from dib import config, fonts, i18n
from dib.i18n import t
from dib.overlay import BilliardsOverlay
from dib.panel import DonationDialog, MainPanel, show_update_dialog
from dib.rank import RankSettings
from dib.table import BilliardsMotion
from dib.ui import DesktopBilliardsApp
from dib.white_ball import CropDialog, WhiteBallDialog

CALLBACKS = ('restart', 'toggle_guide', 'toggle_rank', 'leaderboard', 'toggle_felt',
             'check_updates', 'donate', 'quit', 'white_ball', 'open_credit')


class LanguagePickerTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(i18n.set_language, None)
        i18n.set_language('zh-CN')
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.chosen = []
        callbacks = {name: (lambda: None) for name in CALLBACKS}
        callbacks['language'] = self.chosen.append
        self.panel = MainPanel(self.root, config.VERSION, callbacks, t('product.credit'))

    def choose(self, label):
        self.panel.language.set(label)
        self.panel.language.event_generate('<<ComboboxSelected>>')
        self.root.update()

    def test_the_picker_lists_every_language_by_its_own_name(self):
        self.assertEqual(set(self.panel.language.cget('values')),
                         {i18n.name(code) for code in i18n.available()})
        self.assertEqual(self.panel.language.get(), '中文')

    def test_the_drop_down_actually_opens(self):
        """A malformed listbox font option makes Tk throw while posting the pop-down."""
        picker = self.panel.language
        popdown = self.root.tk.call('ttk::combobox::PopdownWindow', picker)
        errors = []
        self.root.report_callback_exception = lambda *info: errors.append(info)
        self.root.tk.call('ttk::combobox::Post', picker)
        self.root.update()
        self.assertEqual(errors, [], 'posting the drop-down must not raise')
        self.assertTrue(self.root.tk.call('winfo', 'ismapped', popdown), 'the drop-down is visible')
        self.root.tk.call('ttk::combobox::Unpost', picker)

    def test_choosing_a_language_reports_its_code(self):
        self.choose('English')
        self.choose('中文')
        self.assertEqual(self.chosen, ['en', 'zh-CN'])

    def test_retranslate_switches_every_label_and_the_font(self):
        self.assertIn('YaHei', self.panel.pills['quit'].cget('font'))
        i18n.set_language('en')
        self.panel.retranslate()
        for key, catalog_key in MainPanel.LABELS.items():
            self.assertEqual(self.panel.pills[key].cget('text'), t(catalog_key), key)
        self.assertEqual(self.panel.time_title.get(), 'Round time')
        self.assertEqual(self.panel.count_title.get(), 'Potted')
        self.assertEqual(self.panel.remaining_title.get(), 'Left')
        self.assertEqual(self.panel.credit_label.cget('text'), 'Douyin @LLLLLLLLLL')
        self.assertIn('Segoe UI', self.panel.pills['quit'].cget('font'))

    def test_the_picker_follows_the_active_language(self):
        i18n.set_language('en')
        self.panel.retranslate()
        self.assertEqual(self.panel.language.get(), 'English')


class AppSwitchTests(unittest.TestCase):
    """``DesktopBilliardsApp.set_language`` retranslates every open window and persists the choice."""

    def setUp(self):
        self.addCleanup(i18n.set_language, None)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.settings = RankSettings(Path(folder.name) / 'ui.json')

    def test_every_open_window_follows_the_switch(self):
        calls = []

        def window(name):
            return SimpleNamespace(alive=lambda: True, retranslate=lambda: calls.append(name))

        app = SimpleNamespace(
            settings=self.settings,
            panel=SimpleNamespace(retranslate=lambda: calls.append('panel')),
            refresh_buttons=lambda: calls.append('buttons'),
            result_dialog=window('result'),
            leaderboard_dialog=window('board'),
            white_ball_dialog=window('white'),
            donation=window('donation'),
            update_dialog=window('update'),
        )
        DesktopBilliardsApp.set_language(app, 'en')
        self.assertEqual(i18n.language(), 'en')
        self.assertEqual(calls, ['panel', 'buttons', 'result', 'board', 'white', 'donation', 'update'])
        self.assertEqual(self.settings.get('language'), 'en')

    def test_a_closed_window_is_not_touched(self):
        calls = []
        app = SimpleNamespace(
            settings=self.settings,
            panel=SimpleNamespace(retranslate=lambda: calls.append('panel')),
            refresh_buttons=lambda: calls.append('buttons'),
            result_dialog=None,
            leaderboard_dialog=SimpleNamespace(alive=lambda: False, retranslate=lambda: calls.append('board')),
            white_ball_dialog=SimpleNamespace(alive=lambda: False, retranslate=lambda: calls.append('white')),
            donation=SimpleNamespace(alive=lambda: True, retranslate=lambda: calls.append('donation')),
            update_dialog=None,
        )
        DesktopBilliardsApp.set_language(app, 'zh-CN')
        self.assertEqual(calls, ['panel', 'buttons', 'donation'])


class OverlayCopyTests(unittest.TestCase):
    """The layered table caches its own faces, so a switch has to rebuild and repaint them."""

    def setUp(self):
        self.addCleanup(i18n.set_language, None)
        i18n.set_language('en')

    def make_overlay(self):
        game = BilliardsMotion((0, 0, 1920, 1080), [(64, 64)] * 15)
        game.charge = .6
        overlay = object.__new__(BilliardsOverlay)
        overlay.table = game
        overlay.pointer = SimpleNamespace(charge_origin=(400, 400), charge_falling=False)
        overlay.copy_language = i18n.language()
        overlay.charge_size = 20
        overlay.charge_font = fonts.text(20)
        overlay.charge_key = None
        overlay.round_actions_key = None
        overlay.round_action_hover = overlay.round_action_pressed = None
        overlay.primary_bounds = game.bounds
        overlay.table_windows = {'charge': 7, 'round_actions': 8}
        overlay.present = mock.Mock()
        overlay.position_layer = mock.Mock()
        return overlay

    @staticmethod
    def renders_its_script(font, text):
        """A face without the script draws the same empty box for every character."""
        return bytes(font.getmask(text[0])) != bytes(font.getmask(text[1]))

    def test_a_switch_mid_round_repaints_the_meter_in_a_face_with_the_new_script(self):
        overlay = self.make_overlay()
        overlay.draw_charge()
        self.assertEqual(overlay.present.call_count, 1)
        i18n.set_language('zh-CN')
        overlay.draw_charge()
        self.assertEqual(overlay.present.call_count, 2, 'the frame cached in the old face must be redrawn')
        self.assertIs(overlay.charge_font, overlay.copy_face())
        self.assertTrue(self.renders_its_script(overlay.charge_font, t('art.charge')),
                        'the Chinese label needs a face that covers the script')

    def test_nothing_is_repainted_while_the_language_stays_the_same(self):
        overlay = self.make_overlay()
        overlay.draw_charge()
        overlay.draw_charge()
        self.assertEqual(overlay.present.call_count, 1)

    def test_the_end_of_round_buttons_redraw_in_the_new_language(self):
        overlay = self.make_overlay()
        overlay.table.state = 'finished'
        overlay.draw_round_actions()
        i18n.set_language('zh-CN')
        overlay.draw_round_actions()
        self.assertEqual(overlay.present.call_count, 2)


class DialogSwitchTests(unittest.TestCase):
    """The dialogs that can stay open across a switch re-read their copy, not their state."""

    def setUp(self):
        self.addCleanup(i18n.set_language, None)
        i18n.set_language('zh-CN')
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def test_the_white_ball_window_keeps_its_texture_and_reset_state(self):
        texture = Image.new('RGBA', (256, 256), 'blue')
        dialog = WhiteBallDialog(self.root, self.folder / 'white-ball.png', texture, lambda value: None)
        self.addCleanup(dialog.window.destroy)
        self.root.update()
        corner = (dialog.window.winfo_rootx(), dialog.window.winfo_rooty())
        i18n.set_language('en')
        dialog.retranslate()
        self.root.update()
        self.assertEqual(dialog.window.title(), t('white_ball.title'))
        self.assertEqual(dialog.title_label.cget('text'), t('white_ball.title'))
        self.assertEqual(dialog.intro.cget('text'), t('white_ball.intro'))
        self.assertEqual(dialog.hint.cget('text'), t('white_ball.hint'))
        self.assertEqual(dialog.choose_button.cget('text'), t('white_ball.choose'))
        self.assertEqual(dialog.reset_button.cget('text'), t('white_ball.reset'))
        self.assertEqual(dialog.status.cget('text'), t('white_ball.status_custom'))
        self.assertIs(dialog.texture, texture, 'the chosen ball must survive the switch')
        self.assertTrue(dialog.reset_button.enabled)
        self.assertEqual((dialog.window.winfo_rootx(), dialog.window.winfo_rooty()), corner)

    def test_the_crop_window_keeps_the_zoom_and_the_centre(self):
        source = Image.new('RGB', (300, 100), 'red')
        editor = CropDialog(self.root, source, lambda texture: None)
        self.addCleanup(editor.close)
        editor.scale('2.5')
        editor.render()
        zoom, centre = editor.zoom, editor.center
        i18n.set_language('en')
        editor.retranslate()
        self.assertEqual(editor.window.title(), t('white_ball.crop_title'))
        self.assertEqual(editor.hint.cget('text'), t('white_ball.crop_hint'))
        self.assertEqual(editor.cancel_button.cget('text'), t('white_ball.cancel'))
        self.assertEqual(editor.apply_button.cget('text'), t('white_ball.apply'))
        self.assertEqual(editor.zoom, zoom)
        self.assertEqual(editor.center, centre)

    def test_the_white_ball_window_forwards_the_switch_to_an_open_crop_window(self):
        dialog = WhiteBallDialog(self.root, self.folder / 'white-ball.png', None, lambda value: None)
        self.addCleanup(dialog.window.destroy)
        editor = CropDialog(dialog.window, Image.new('RGB', (300, 100), 'red'), lambda texture: None)
        self.addCleanup(editor.close)
        dialog.crop = editor
        i18n.set_language('en')
        dialog.retranslate()
        self.assertEqual(editor.window.title(), t('white_ball.crop_title'))
        self.assertEqual(editor.apply_button.cget('text'), t('white_ball.apply'))

    def test_the_donation_window_follows_the_switch(self):
        dialog = DonationDialog(self.root, Image.new('RGB', (40, 40), 'gold'))
        self.addCleanup(dialog.destroy)
        self.assertEqual(dialog.title(), t('ui.donate.title'))
        i18n.set_language('en')
        dialog.retranslate()
        self.assertEqual(dialog.title(), t('ui.donate.title'))
        self.assertEqual(dialog.heading.cget('text'), t('ui.donate.heading'))
        self.assertEqual(dialog.note.cget('text'), t('ui.donate.note'))
        self.assertEqual(dialog.close_button.cget('text'), t('ui.donate.close'))
        self.assertTrue(dialog.alive())

    def test_the_update_window_keeps_the_server_notes_and_the_scroll(self):
        remote = '# 更新重点\n- 服务器给的 **原文**\n' + '说明行\n' * 40
        entries = [{'version': '0.2.4', 'date': '2026-11-17 10:00', 'notes': remote},
                   {'version': '0.2.3', 'notes': '旧版本说明'}]
        with mock.patch('dib.panel.win32api.GetMonitorInfo', return_value={'Work': (0, 0, 800, 600)}):
            dialog = show_update_dialog(self.root, '0.2.3', entries[0], entries, lambda window: None)
        self.addCleanup(dialog.destroy)
        self.root.update()
        dialog.notes.yview_moveto(.5)
        self.root.update()
        top = dialog.notes.index('@0,0')
        self.assertNotEqual(top, '1.0', 'the notes must be scrollable for this check')
        i18n.set_language('en')
        dialog.retranslate()
        self.root.update()
        self.assertEqual(dialog.title(), t('panel.update.title'))
        self.assertEqual(dialog.heading.cget('text'), t('panel.update.heading'))
        self.assertEqual(dialog.download.cget('text'), t('panel.update.download'))
        self.assertEqual(dialog.later.cget('text'), t('panel.update.later'))
        body = dialog.notes.get('1.0', 'end')
        self.assertIn('服务器给的', body, 'notes from the server are not translated')
        self.assertIn('原文', body)
        self.assertIn('旧版本说明', body)
        self.assertNotIn('**', body, 'the markup is rendered, not translated')
        self.assertEqual(dialog.notes.index('@0,0'), top, 'the reader keeps their place')
        self.assertTrue(dialog.alive())


class ToolbarFitTests(unittest.TestCase):
    """The bar wraps around its final labels and then stays inside one work area.

    The state labels (``Guide on``, ``Rank unavailable``, …) are wider than the neutral ones
    the bar is built with, so laying it out before them leaves the wrap short of the truth.
    The work area is measured from the bar itself, so these checks hold at any DPI.
    """

    HEIGHT = 640

    def setUp(self):
        self.addCleanup(i18n.set_language, None)
        i18n.set_language('en')
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.settings = RankSettings(Path(folder.name) / 'ui.json')
        monitor = mock.patch.object(win32api, 'MonitorFromPoint', return_value=7)
        self.monitor = monitor.start()
        self.addCleanup(monitor.stop)
        info = mock.patch.object(win32api, 'GetMonitorInfo',
                                 return_value={'Work': (0, 0, 4000, self.HEIGHT)})
        self.info = info.start()
        self.addCleanup(info.stop)
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.root.overrideredirect(True)
        callbacks = {name: (lambda: None) for name in CALLBACKS}
        self.panel = MainPanel(self.root, config.VERSION, callbacks, t('product.credit'))
        self.app = SimpleNamespace(root=self.root, panel=self.panel, settings=self.settings,
                                   guide_enabled=True, felt_enabled=True,
                                   rank=SimpleNamespace(available=False), toolbar_ready=False)
        self.app.refresh_rank_button = lambda: DesktopBilliardsApp.refresh_rank_button(self.app)
        self.app.refresh_buttons = lambda: DesktopBilliardsApp.refresh_buttons(self.app)
        self.app.fit_toolbar = lambda: DesktopBilliardsApp.fit_toolbar(self.app)
        self.app.place_window = lambda **options: DesktopBilliardsApp.place_window(self.app, **options)
        self.app.refresh_buttons()
        self.root.update()
        stats = self.panel.pills['quit'].master.winfo_reqwidth()  # the counters row cannot wrap
        # Narrow enough to force a second row, never narrower than the counters need.
        # Measured from the bar itself, so the checks hold at any DPI.
        self.narrow = max(stats + 80, self.root.winfo_reqwidth() - 220)
        self.info.return_value = {'Work': (0, 0, self.narrow, self.HEIGHT)}
        self.settings.update(bar_x=10 ** 6, bar_y=10 ** 6)  # a corner from a larger desktop
        self.app.place_window()
        self.root.update()

    def switch(self, code):
        i18n.set_language(code)
        self.panel.retranslate()
        self.app.refresh_buttons()
        self.root.update()

    def assert_fits(self):
        left, top, right, bottom = 0, 0, self.narrow, self.HEIGHT
        self.assertGreaterEqual(self.root.winfo_rootx(), left)
        self.assertGreaterEqual(self.root.winfo_rooty(), top)
        self.assertLessEqual(self.root.winfo_rootx() + self.root.winfo_width(), right,
                             'the bar hangs past the right edge')
        self.assertLessEqual(self.root.winfo_rooty() + self.root.winfo_height(), bottom,
                             'the bar hangs under the taskbar')
        for key, pill in self.panel.pills.items():
            self.assertTrue(pill.winfo_ismapped(), key)
            self.assertGreaterEqual(pill.winfo_rootx(), left, key)
            self.assertLessEqual(pill.winfo_rootx() + pill.winfo_width(), right, key)
        credit = self.panel.credit_label
        self.assertEqual(credit.winfo_width(), credit.winfo_reqwidth(), 'the author credit is clipped')
        self.assertLessEqual(credit.winfo_rootx() + credit.winfo_width(), right)
        self.assertNotEqual(self.panel.pills['guide'].cget('text'), t('panel.toolbar.guide'),
                            'the state label, not the neutral one, must be laid out')

    def test_the_bar_stays_inside_the_work_area_in_both_directions(self):
        for code in ('zh-CN', 'en', 'zh-CN'):
            self.switch(code)
            self.assert_fits()

    def test_every_state_label_row_fits_inside_the_bar(self):
        """A row laid out with the neutral labels overflows once the state labels arrive."""
        for code in ('en', 'zh-CN', 'en'):
            self.switch(code)
            rows = {}
            for group in self.panel.groups:
                rows.setdefault(int(group.grid_info()['row']), []).append(group)
            self.assertGreater(len(rows), 1, f'the groups must wrap in {code}')
            right = self.root.winfo_rootx() + self.root.winfo_width()
            for row, groups in rows.items():
                self.assertLessEqual(groups[-1].winfo_rootx() + groups[-1].winfo_width(), right,
                                     f'row {row} overflows the bar in {code}')
            self.assert_fits()

    def test_a_longer_update_label_refits_the_bar_too(self):
        self.switch('en')
        for label, enabled in ((t('panel.toolbar.update_checking'), False),
                               (t('panel.toolbar.update'), True)):
            self.panel.set_look('update', label, enabled=enabled)
            self.app.fit_toolbar()
            self.root.update()
            self.assert_fits()

    def test_a_refit_reads_the_work_area_of_the_bar_itself(self):
        corner = (self.root.winfo_rootx(), self.root.winfo_rooty())
        self.monitor.reset_mock()
        self.switch('zh-CN')
        self.assertEqual(self.monitor.call_args.args[0], corner,
                         'the bar must be clamped against its own monitor, not the primary one')

    def test_a_refit_does_not_move_a_bar_that_already_fits(self):
        self.settings.update(bar_x=20, bar_y=20)
        self.app.place_window()
        self.root.update()
        corner = (self.root.winfo_rootx(), self.root.winfo_rooty())
        self.switch('zh-CN')
        self.assertEqual((self.root.winfo_rootx(), self.root.winfo_rooty()), corner,
                         'a bar with room to spare must not be re-centred or moved')
        self.assert_fits()
