"""Switching the interface language from the toolbar at runtime."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import win32api

from dib import config, i18n
from dib.i18n import t
from dib.panel import MainPanel
from dib.rank import RankSettings
from dib.ui import DesktopBilliardsApp

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
        app = SimpleNamespace(
            settings=self.settings,
            panel=SimpleNamespace(retranslate=lambda: calls.append('panel')),
            refresh_buttons=lambda: calls.append('buttons'),
            result_dialog=SimpleNamespace(alive=lambda: True, retranslate=lambda: calls.append('result')),
            leaderboard_dialog=SimpleNamespace(alive=lambda: True, retranslate=lambda: calls.append('board')),
        )
        DesktopBilliardsApp.set_language(app, 'en')
        self.assertEqual(i18n.language(), 'en')
        self.assertEqual(calls, ['panel', 'buttons', 'result', 'board'])
        self.assertEqual(self.settings.get('language'), 'en')

    def test_a_closed_window_is_not_touched(self):
        calls = []
        app = SimpleNamespace(
            settings=self.settings,
            panel=SimpleNamespace(retranslate=lambda: calls.append('panel')),
            refresh_buttons=lambda: calls.append('buttons'),
            result_dialog=None,
            leaderboard_dialog=SimpleNamespace(alive=lambda: False, retranslate=lambda: calls.append('board')),
        )
        DesktopBilliardsApp.set_language(app, 'zh-CN')
        self.assertEqual(calls, ['panel', 'buttons'])


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
