"""Hidden toolbar onboarding and recovery behaviour."""
import unittest
from unittest.mock import Mock, patch

from dib.ui import DesktopBilliardsApp


class StealthToolbarTests(unittest.TestCase):
    def app(self):
        app = object.__new__(DesktopBilliardsApp)
        app.root = Mock()
        app.root.grab_current.return_value = None
        app.root.winfo_children.return_value = []
        app.root.winfo_rootx.return_value = 100
        app.root.winfo_rooty.return_value = 20
        app.root.winfo_width.return_value = 800
        app.root.winfo_height.return_value = 40
        app.root.winfo_pointerxy.return_value = (1000, 400)
        app.felt_enabled = False
        app.stealth_announced = False
        app.toolbar_hidden = False
        app.toolbar_region = None
        app.toolbar_hover_since = app.toolbar_leave_since = None
        app.remember_position = Mock()
        return app

    @patch('dib.ui.win32api.GetAsyncKeyState', return_value=0)
    @patch('dib.ui.messagebox.showinfo')
    @patch('dib.ui.time.monotonic')
    def test_prompt_precedes_hiding_and_hover_restores_toolbar(self, clock, prompt, keys):
        app = self.app()
        clock.return_value = 10
        app.update_toolbar()
        prompt.assert_called_once()
        self.assertIn('Shift', prompt.call_args.args[1])
        app.root.withdraw.assert_not_called()
        clock.return_value = 11.1
        app.update_toolbar()
        self.assertTrue(app.toolbar_hidden)
        app.root.winfo_pointerxy.return_value = (200, 30)
        app.update_toolbar()
        clock.return_value = 11.5
        app.update_toolbar()
        self.assertFalse(app.toolbar_hidden)
        app.root.deiconify.assert_called_once()
        prompt.assert_called_once()

    @patch('dib.ui.win32api.GetAsyncKeyState', return_value=0x8000)
    @patch('dib.ui.time.monotonic', return_value=20)
    def test_shooting_does_not_reveal_toolbar_and_normal_mode_restores_it(self, clock, keys):
        app = self.app()
        app.stealth_announced = True
        app.toolbar_hidden = True
        app.toolbar_region = (100, 20, 900, 60)
        app.root.winfo_pointerxy.return_value = (200, 30)
        app.toolbar_hover_since = 1
        app.update_toolbar()
        app.root.deiconify.assert_not_called()
        self.assertIsNone(app.toolbar_hover_since)
        app.felt_enabled = True
        app.update_toolbar()
        self.assertFalse(app.toolbar_hidden)
        app.root.deiconify.assert_called_once()
