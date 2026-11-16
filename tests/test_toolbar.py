"""The compact toolbar and hotkeys must survive a withdrawn Tk window."""
from types import SimpleNamespace

import tkinter as tk
import unittest

import win32con
import win32gui

from dib import config
from dib.hotkeys import Hotkeys
from dib.panel import MainPanel


class ToolbarTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)

    def test_only_exit_hotkey_is_enabled(self):
        hotkeys = Hotkeys()
        self.addCleanup(hotkeys.close)
        self.assertNotIn(config.HIDE_HOTKEY_ID, hotkeys.registered)
        self.assertIn(config.HOTKEY_ID, hotkeys.registered)
        self.root.withdraw()
        for identifier in (config.HIDE_HOTKEY_ID, config.HOTKEY_ID):
            win32gui.PostMessage(hotkeys.hwnd, win32con.WM_HOTKEY, identifier, 0)
        self.root.after(100, self.root.quit)
        self.root.mainloop()
        self.assertEqual(self.root.state(), 'withdrawn')
        self.assertEqual(hotkeys.pending.get_nowait(), config.HOTKEY_ID)
        self.assertTrue(hotkeys.pending.empty())

    def test_all_actions_are_visible_in_a_two_row_toolbar(self):
        calls = []
        names = ('restart', 'toggle_guide', 'toggle_rank', 'leaderboard', 'toggle_felt',
                 'check_updates', 'donate', 'quit', 'open_credit')
        callbacks = {name: lambda name=name: calls.append(name) for name in names}
        panel = MainPanel(self.root, config.VERSION, callbacks, config.CREDIT)
        self.root.update()
        self.assertTrue(self.root.overrideredirect())
        self.assertEqual(set(panel.pills), {'restart', 'guide', 'rank', 'leaderboard', 'felt', 'white',
                                            'update', 'donate', 'quit'})
        self.assertNotIn('hide', panel.pills)
        # The first row holds the functions and the author entry, the second the counters and actions.
        functions = {panel.pills[key].winfo_rooty()
                     for key in ('guide', 'felt', 'white', 'rank', 'leaderboard', 'update', 'donate')}
        self.assertEqual(len(functions), 1, 'the function buttons share one row')
        actions = {panel.pills['restart'].winfo_rooty(), panel.pills['quit'].winfo_rooty()}
        self.assertEqual(len(actions), 1, 'restart and quit share one row')
        self.assertLess(min(functions), min(actions), 'functions sit above the actions')
        credit = next(widget for widget in panel.functions.winfo_children()
                      if isinstance(widget, tk.Label) and widget.cget('text') == config.CREDIT)
        self.assertLess(credit.winfo_rooty(), min(actions), 'the author entry stays on the top row')
        self.assertLess(panel.pills['rank'].winfo_x(), panel.pills['leaderboard'].winfo_x())
        for pill in panel.pills.values():
            self.assertTrue(pill.winfo_ismapped())
            pill._release(SimpleNamespace(x=1, y=1))
        self.assertEqual(set(calls), {'toggle_guide', 'toggle_felt', 'toggle_rank', 'leaderboard',
                                      'check_updates', 'donate', 'quit', 'restart'})
        self.assertEqual(len(calls), 8, 'every enabled pill fires once')


if __name__ == '__main__':
    unittest.main()
