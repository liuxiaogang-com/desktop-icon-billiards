"""Switching the interface language from the toolbar at runtime."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace

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
