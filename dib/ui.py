"""Tk window, game lifecycle, and single-instance handling."""
from __future__ import annotations

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import webbrowser

import tkinter as tk
import win32api
import win32con
from PIL import Image
from tkinter import messagebox

from . import config, diag, i18n
from .display import dpi_context, work_area
from .hotkeys import Hotkeys
from .i18n import t
from .instance import acquire_instance, claim_instance_mutex, publish_version
from .overlay import BilliardsOverlay
from .panel import DonationDialog, MainPanel, ResultDialog, format_duration, show_update_dialog
from .paths import runtime_directory
from .leaderboard import LeaderboardDialog
from .rank import RankApi, RankController, RankSettings, average_seconds, data_directory
from .runner import BilliardsRunner
from .shell import LayoutSession, ShellDesktop
from .update import fetch_update, release_entries
from .white_ball import WhiteBallDialog, read_texture


_last_log = {}


def _write_log(name, text):
    text = text.encode('utf-8')[:diag.MAX_BYTES].decode('utf-8', errors='ignore')
    now = time.monotonic()
    previous = _last_log.get(name)
    if previous is not None and previous[0] == text and now - previous[1] < 60:
        return
    try:
        (runtime_directory() / name).write_text(text, encoding='utf-8')
        _last_log[name] = (text, now)
    except OSError:
        pass


def describe_error(error):
    """The error text plus where it was raised (``file.py:line in function``).

    A bare message such as "'NoneType' object is not iterable" says nothing on its own; the
    location lets a player's screenshot pin the bug down. The last frame inside this package wins.
    """
    frames = traceback.extract_tb(error.__traceback__)
    frame = next((f for f in reversed(frames) if 'dib' in f.filename.replace('\\', '/').split('/')), None)
    frame = frame or (frames[-1] if frames else None)
    where = f'{os.path.basename(frame.filename)}:{frame.lineno} in {frame.name}' if frame else ''
    text = t('ui.error.location', error=error, where=where) if where else str(error)
    return text + t('ui.error.log_hint')


class DesktopBilliardsApp:
    """Own the Tk window, the layout journal and the animation runner."""

    def __init__(self, root, instance_handle):
        self.root = root
        self.instance_handle = instance_handle
        self.runner = None
        self.session = None
        self.watchdog = None
        self.donation = None
        self.closing = False
        self.deadline = 0.0
        self.journal = runtime_directory() / 'layout.json'
        root.title(config.window_title())
        root.resizable(False, False)
        try:
            root.iconbitmap(str(config.ICON_PATH))
        except tk.TclError:
            pass
        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-2))
        self.rank = self.make_rank()
        self.rank_offered = False
        self.guide_enabled = True
        self.leaderboard_dialog = None
        self.result_dialog = None
        self.result_serial = None
        self.result_attempt_serial = None
        self.result_attempts = 0
        self.result_retry_at = 0.
        self.restart_after_serial = None
        self.round_snapshot = None
        self.result_observation = None
        self.update_results = queue.SimpleQueue()
        self.settings = self.make_settings()
        # A language chosen in the toolbar wins over the Windows interface language.
        i18n.set_language(self.settings.get('language'))
        self.white_ball_dialog = None
        self.update_dialog = None
        self.white_texture = None
        self.white_texture_path = self.settings.path.parent / 'white-ball.png'
        if self.white_texture_path.exists():
            try:
                self.white_texture = read_texture(self.white_texture_path)
            except (OSError, ValueError, Image.DecompressionBombError):
                _write_log('white-ball.log', traceback.format_exc())
        self.felt_enabled = bool(self.settings.get('felt', True))
        self.stealth_announced = False
        self.toolbar_hidden = False
        self.toolbar_ready = False
        self.toolbar_eye = None
        self.toolbar_region = None
        self.toolbar_hover_since = None
        self.toolbar_leave_since = None
        self.panel = panel = MainPanel(root, config.VERSION, {
            'restart': self.restart, 'toggle_guide': self.toggle_guide, 'toggle_rank': self.toggle_rank,
            'check_updates': lambda: self.check_updates(manual=True), 'donate': self.show_donation,
            'quit': self.close, 'toggle_felt': self.toggle_felt,
            'leaderboard': self.show_leaderboard,
            'white_ball': self.show_white_ball,
            'open_credit': lambda: webbrowser.open(config.DOUYIN_URL),
            'language': self.set_language,
        }, t('product.credit'))
        publish_version(root, config.VERSION)
        self.time_title, self.time_value = panel.time_title, panel.time_value
        self.count_title, self.count_value = panel.count_title, panel.count_value
        self.remaining_value = panel.remaining_value
        self.refresh_buttons()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<Escape>', lambda event: self.close())
        self.place_window()
        self.hotkeys = Hotkeys()
        root.after(20, self.start)
        root.after(1500, self.check_updates)

    @staticmethod
    def make_rank():
        """Ranking is optional: a missing or invalid server address just disables it."""
        if config.RANK_URL:
            try:
                api = RankApi(config.RANK_URL, config.VERSION)
                return RankController(api, RankSettings(data_directory() / 'rank.json'))
            except (ValueError, OSError):
                _write_log('rank.log', traceback.format_exc())
        return RankController(None, None)

    @staticmethod
    def make_settings():
        """Window layout and position, and the table-frame switch (kept across runs)."""
        try:
            return RankSettings(data_directory() / 'ui.json')
        except OSError:
            return RankSettings(runtime_directory() / 'ui.json')

    def refresh_rank_button(self):
        if not self.rank.available:
            self.panel.set_look('rank', t('panel.toolbar.rank_unavailable'), 'off', enabled=False)
            return
        on = self.rank.enabled
        self.panel.set_look('rank', t('panel.toolbar.rank_on') if on else t('panel.toolbar.rank_off'),
                            'on' if on else 'off', enabled=True)

    def fit_toolbar(self):
        """Wrap the bar around its final labels, then keep it inside its monitor."""
        self.panel.relayout()
        if self.toolbar_ready:
            self.place_window(keep=True)

    def refresh_buttons(self):
        self.refresh_rank_button()
        self.panel.set_look('guide', t('panel.toolbar.guide_on') if self.guide_enabled
                            else t('panel.toolbar.guide_off'),
                            'on' if self.guide_enabled else 'off')
        self.panel.set_look('felt', t('panel.toolbar.felt_on') if self.felt_enabled
                            else t('panel.toolbar.felt_off'))
        # Every state label is set now, and they are wider than the neutral ones, so the
        # wrap and the position are only correct once the bar has been refitted here.
        self.fit_toolbar()

    def set_language(self, code):
        """Switch every open window to another bundled language, and remember the choice."""
        i18n.set_language(code)
        self.settings.update(language=i18n.language())
        self.panel.retranslate()
        self.refresh_buttons()
        for window in (self.result_dialog, self.leaderboard_dialog, self.white_ball_dialog,
                       self.donation, self.update_dialog):
            if window is not None and window.alive():
                window.retranslate()
        diag.log(f'language changed: {i18n.language()}')

    # -- toolbar position --------------------------------------------------
    def place_window(self, keep=False):
        """Keep the bar inside the work area of the monitor it belongs to.

        The first run centres it above the taskbar; later runs restore the saved corner.
        ``keep`` refits the bar where it already is, so a language change or a longer label
        cannot push it off the edge or onto another monitor.
        """
        root = self.root
        root.update_idletasks()
        width, height = root.winfo_reqwidth(), root.winfo_reqheight()
        if keep:
            x, y = root.winfo_rootx(), root.winfo_rooty()
        else:
            monitor = win32api.MonitorFromPoint((0, 0), win32con.MONITOR_DEFAULTTOPRIMARY)
            left, top, right, bottom = win32api.GetMonitorInfo(monitor)['Work']
            x, y = self.settings.get('bar_x'), self.settings.get('bar_y')
            if not (isinstance(x, int) and isinstance(y, int)):
                x, y = left + max(0, (right - left - width) // 2), top + 2
        left, top, right, bottom = work_area(x, y)
        x = max(left, min(x, right - width))
        y = max(top, min(y, bottom - height))
        root.geometry(f'+{x}+{y}')
        self.toolbar_ready = True

    def remember_position(self):
        if self.root.state() == 'normal':
            self.settings.update(bar_x=self.root.winfo_x(), bar_y=self.root.winfo_y())

    def offer_rank(self):
        """Ask once, on the first run after the feature exists, whether to join the ranking."""
        if self.closing or not self.rank.needs_prompt:
            return
        self.rank.mark_prompted()
        if messagebox.askyesno(t('ui.rank.dialog_title'),
                               t('ui.rank.intro', consent=t('ui.rank.consent')), parent=self.root):
            self.rank.set_enabled(True)
        self.refresh_rank_button()

    def toggle_rank(self):
        if not self.rank.available:
            return
        if self.rank.enabled:
            self.rank.set_enabled(False)
        elif messagebox.askokcancel(t('ui.rank.dialog_title'), t('ui.rank.consent'), parent=self.root):
            self.rank.set_enabled(True)
        self.refresh_rank_button()

    def show_donation(self):
        if self.donation is not None and self.donation.alive():
            self.donation.lift()
            return
        try:
            with Image.open(config.ASSETS / 'donation.jpg') as source:
                image = source.convert('RGB')
            image.thumbnail((340, 340), Image.Resampling.LANCZOS)
        except OSError:
            messagebox.showwarning(t('ui.donate.title'), t('ui.donate.image_error'), parent=self.root)
            return
        self.donation = DonationDialog(self.root, image)

    def show_white_ball(self):
        if self.white_ball_dialog is not None and self.white_ball_dialog.alive():
            self.white_ball_dialog.window.lift()
            return
        self.white_ball_dialog = WhiteBallDialog(self.root, self.white_texture_path,
                                                self.white_texture, self.set_white_texture)

    def set_white_texture(self, texture):
        self.white_texture = texture
        if self.runner is not None:
            self.runner.overlay.request_white_texture(texture)

    def check_updates(self, manual=False):
        if self.closing or not self.panel.is_enabled('update'):
            return
        self.panel.set_look('update', t('panel.toolbar.update_checking'), enabled=False)
        self.fit_toolbar()

        def fetch():
            try:
                info = fetch_update(config.UPDATE_URL, config.VERSION, source='manual' if manual else 'startup')
            except Exception:
                _write_log('update.log', traceback.format_exc())
                self.update_results.put((False, None))
            else:
                self.update_results.put((True, info))

        threading.Thread(target=fetch, name='Billiards-update', daemon=True).start()
        self.root.after(100, self.finish_update_check, manual)

    def finish_update_check(self, manual):
        if self.closing:
            return
        try:
            success, info = self.update_results.get_nowait()
        except queue.Empty:
            self.root.after(100, self.finish_update_check, manual)
            return
        self.panel.set_look('update', t('panel.toolbar.update'), enabled=True)
        self.fit_toolbar()
        if info:
            self.show_update(info)
        elif manual:
            messagebox.showinfo(t('product.name'),
                                t('ui.update.up_to_date') if success else t('ui.update.check_failed'),
                                parent=self.root)

    def show_update(self, info):
        def download(dialog):
            if webbrowser.open(info['download_url']):
                dialog.destroy()
            else:
                messagebox.showerror(t('ui.update.open_failed_title'), t('ui.update.open_failed_body'),
                                     parent=dialog)

        self.update_dialog = show_update_dialog(self.root, config.VERSION, info,
                                                release_entries(info), download)

    # -- game lifecycle ----------------------------------------------------
    def start(self):
        if self.closing:
            return
        diag.start_session(config.VERSION)
        try:
            desktop = ShellDesktop()
            # Recover a journal left behind by an earlier crash before starting.
            self.session = LayoutSession(desktop, self.journal)
            self.session.restore()
            self.session = LayoutSession(desktop, self.journal)
            items = self.session.begin(overlay_only=True)
            for item in items:
                item['name'] = desktop.display_name(item['id'])
            with dpi_context(-4):
                overlay = BilliardsOverlay(desktop, items)
                overlay.guide_enabled = self.guide_enabled
                overlay.want_felt = self.felt_enabled
                overlay.request_white_texture(self.white_texture)
            self.watchdog = self.spawn_watchdog()
            self.runner = BilliardsRunner(overlay)
            self.runner.start()
            self.deadline = time.monotonic() + 30
            self.poll()
        except Exception as exc:
            self.fail(exc)

    def spawn_watchdog(self):
        frozen = bool(getattr(sys, 'frozen', False))
        command = [sys.executable]
        if not frozen:
            command.append(str(config.BASE / 'app.pyw'))
        command.extend(['--watchdog', str(self.journal), self.session.snapshot['session_id'], str(os.getpid())])
        environment = {**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'}
        return subprocess.Popen(command, env=environment,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
                                cwd=str(runtime_directory()))

    def poll(self):
        if self.closing:
            return
        try:
            while True:
                try:
                    identifier = self.hotkeys.pending.get_nowait()
                except queue.Empty:
                    break
                if identifier == config.HOTKEY_ID:
                    self.close()
                    return
            if self.runner.error:
                raise self.runner.error
            if not self.runner.shown.is_set():
                if time.monotonic() > self.deadline:
                    raise RuntimeError(t('ui.error.guard_timeout'))
                ready = self.journal.with_name(self.journal.stem + '.watchdog.json')
                if ready.exists():
                    data = json.loads(ready.read_text(encoding='utf-8'))
                    if data.get('session_id') == self.session.snapshot['session_id'] and self.runner.prepared.is_set():
                        if not data.get('ready'):
                            raise RuntimeError(t('ui.error.guard_failed'))
                        self.runner.request_show(self.session.snapshot, self.journal)
            else:
                if not self.rank_offered:
                    self.rank_offered = True
                    self.root.after(800, self.offer_rank)
                if not self.runner.overlay.input_suspended:
                    self.update_toolbar()
                overlay = self.runner.overlay
                try:
                    while True:
                        url = overlay.link_requests.get_nowait()
                        webbrowser.open(url)
                except queue.Empty:
                    pass
                except Exception:
                    _write_log('links.log', traceback.format_exc())
                snapshots = self.runner.take_snapshots()
                if snapshots:
                    self.observe_snapshots(overlay, snapshots)
                    if self.handle_round_actions(overlay, snapshots[-1]):
                        return
            self.root.after(50, self.poll)
        except Exception as exc:
            self.fail(exc)

    def handle_round_actions(self, overlay, snapshot):
        while True:
            try:
                serial, action = overlay.round_actions.get_nowait()
            except queue.Empty:
                return False
            if snapshot.state != 'finished' or serial != snapshot.round_serial:
                continue
            if self.restart_after_serial is not None and serial <= self.restart_after_serial:
                continue
            if action == 'again':
                self.play_again()
                return False
            if action == 'quit':
                self.close()
                return self.closing

    def observe_snapshots(self, overlay, snapshots):
        """Use one batch for ranking and its latest immutable state for the UI."""
        table = snapshots[-1]
        self.round_snapshot = table
        # Enabling ranking mid-round must not replay pre-consent start events.
        ranked = snapshots if self.rank.serial is not None else [table]
        rank_failed = False
        for snapshot in ranked:
            rank_failed = False
            try:
                self.rank.observe(snapshot)
            except Exception:
                rank_failed = True
                _write_log('rank.log', traceback.format_exc())
        minutes, seconds = divmod(table.round_seconds, 60)
        self.time_value.set(f'{minutes:02d}:{seconds:02d}')
        total, pocketed = table.icon_count, table.pocketed_icons
        finished = table.state == 'finished'
        observation = (table.round_serial, finished)
        if observation != self.result_observation:
            self.result_observation = observation
            diag.log(f'round observed: serial={table.round_serial} finished={finished} '
                     f'pocketed={pocketed}/{total} toolbar_hidden={self.toolbar_hidden}')
        self.time_title.set(t('panel.stats.time_total') if finished else t('panel.stats.time'))
        self.count_title.set(t('panel.stats.count_total') if finished else t('panel.stats.count'))
        self.count_value.set(str(total) if finished else f'{pocketed}/{total}')
        self.remaining_value.set(str(total - pocketed))
        try:
            self.update_result_dialog(overlay, table, rank_failed=rank_failed)
        except Exception:
            _write_log('result.log', traceback.format_exc())

    def restart(self):
        if self.runner and self.runner.shown.is_set():
            if self.round_snapshot is not None:
                self.restart_after_serial = self.round_snapshot.round_serial
            self.runner.overlay.request_restart()

    def toggle_guide(self):
        self.guide_enabled = not self.guide_enabled
        self.refresh_buttons()
        if self.runner:
            self.runner.overlay.guide_enabled = self.guide_enabled

    def toggle_felt(self):
        """Show or hide the table frame and wallpaper panel, keeping the wastebaskets."""
        self.felt_enabled = not self.felt_enabled
        self.stealth_announced = False
        self.settings.update(felt=self.felt_enabled)
        self.refresh_buttons()
        if self.runner:
            self.runner.overlay.want_felt = self.felt_enabled

    def reveal_toolbar(self, event=None):
        button = win32con.VK_RBUTTON if win32api.GetSystemMetrics(23) else win32con.VK_LBUTTON
        if not win32api.GetAsyncKeyState(button) & 0x8000:
            self.show_toolbar()

    def show_toolbar_eye(self):
        if self.toolbar_eye is None:
            eye = self.toolbar_eye = tk.Toplevel(self.root)
            eye.withdraw()
            eye.overrideredirect(True)
            eye.attributes('-topmost', True)
            eye.attributes('-transparentcolor', '#010203')
            eye.attributes('-alpha', .75)
            canvas = tk.Canvas(eye, width=36, height=28, bg='#010203',
                               highlightthickness=0, bd=0)
            canvas.pack()
            # Outlined eye with a pupil, readable on both light and dark wallpapers.
            shape = (5, 14, 11, 7, 18, 5, 25, 7, 31, 14, 25, 21, 18, 23, 11, 21)
            canvas.create_polygon(*shape, smooth=True, fill='#263d45', outline='#e1e9eb', width=2)
            canvas.create_oval(14, 10, 22, 18, fill='#e1e9eb', outline='')
            canvas.bind('<Enter>', self.reveal_toolbar)
        left, top, _, bottom = self.toolbar_region
        self.toolbar_eye.geometry(f'36x28+{left}+{top + max(0, (bottom - top - 28) // 2)}')
        self.toolbar_eye.deiconify()

    def show_toolbar(self):
        if self.toolbar_eye is not None:
            self.toolbar_eye.withdraw()
        if self.toolbar_hidden:
            self.root.deiconify()
            self.toolbar_hidden = False
        self.toolbar_hover_since = self.toolbar_leave_since = None

    def grab_held(self):
        """Whether any window holds the input grab, including menu and pop-down lists.

        ``grab_current`` maps the grab window back to a tkinter widget, but a combobox
        pop-down is created in Tcl, so that lookup raises KeyError('popdown').
        """
        try:
            return bool(self.root.tk.call('grab', 'current'))
        except tk.TclError:
            return False

    def update_toolbar(self):
        """Keep the original toolbar area discoverable without hiding game windows."""
        if self.felt_enabled:
            self.show_toolbar()
            return
        if not self.stealth_announced:
            self.stealth_announced = True
            self.show_toolbar()
            messagebox.showinfo(t('ui.stealth.title'), t('ui.stealth.body'), parent=self.root)
            self.toolbar_leave_since = time.monotonic()
        # Keep dialogs and toolbar button interactions accessible.
        if self.grab_held() or any(
                isinstance(child, tk.Toplevel) and child is not self.toolbar_eye and child.winfo_viewable()
                for child in self.root.winfo_children()):
            self.show_toolbar()
            return
        now = time.monotonic()
        if not self.toolbar_hidden:
            x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
            self.toolbar_region = (x, y, x + self.root.winfo_width(), y + self.root.winfo_height())
        if self.toolbar_region is None:
            return
        x, y = self.root.winfo_pointerxy()
        left, top, right, bottom = self.toolbar_region
        inside = left <= x < right and top <= y < bottom
        button = win32con.VK_RBUTTON if win32api.GetSystemMetrics(23) else win32con.VK_LBUTTON
        if win32api.GetAsyncKeyState(button) & 0x8000:
            self.toolbar_hover_since = self.toolbar_leave_since = None
            return
        if inside:
            self.toolbar_leave_since = None
            if self.toolbar_hidden:
                if self.toolbar_hover_since is None:
                    self.toolbar_hover_since = now
                elif now - self.toolbar_hover_since >= .3:
                    self.show_toolbar()
        else:
            self.toolbar_hover_since = None
            if not self.toolbar_hidden:
                if self.toolbar_leave_since is None:
                    self.toolbar_leave_since = now
                elif now - self.toolbar_leave_since >= 1:
                    self.remember_position()
                    self.root.withdraw()
                    self.toolbar_hidden = True
                    self.show_toolbar_eye()

    # -- round result ------------------------------------------------------
    def update_result_dialog(self, overlay, table, *, rank_failed=False):
        """Acknowledge a round only after its result window renders successfully."""
        serial = table.round_serial
        finished = table.state == 'finished'
        restarting = self.restart_after_serial is not None and serial <= self.restart_after_serial
        if self.restart_after_serial is not None and serial > self.restart_after_serial:
            self.restart_after_serial = None
        dialog = self.result_dialog
        if dialog is not None:
            if not dialog.alive():
                self.result_dialog = None  # User dismissal stays acknowledged for this round.
            elif restarting or not finished or serial != self.result_serial:
                diag.log(f'result closing for round change: serial={serial} result_serial={self.result_serial}')
                dialog.close()
                self.result_dialog = None
        if restarting or not finished:
            return
        if self.result_attempt_serial != serial:
            self.result_attempt_serial = serial
            self.result_attempts = 0
            self.result_retry_at = 0.
        creating = self.result_serial != serial
        if not creating and self.result_dialog is None:
            return  # An explicitly closed window must not reopen on the next poll.
        now = time.monotonic()
        if self.result_attempts >= 2 or now < self.result_retry_at:
            return
        try:
            rank_view = ({'phase': 'error', 'message': t('ui.rank.unavailable_note')}
                         if rank_failed else self.rank.view(table))
        except Exception:
            _write_log('rank.log', traceback.format_exc())
            rank_view = {'phase': 'error', 'message': t('ui.rank.unavailable_note')}
        view = {
            'time': format_duration(table.play_ms),
            'average': f'{average_seconds(table.play_ms, table.icon_count):.2f}',
            'balls': str(table.icon_count), 'shots': str(table.shots),
            'note': overlay.ball_info, 'rank': rank_view,
        }
        dialog = self.result_dialog
        try:
            if creating:
                diag.log(f'result opening: serial={serial} root_state={self.root.state()}')
                dialog = ResultDialog(self.root, {'again': self.play_again,
                                                 'enable_rank': self.enable_rank_from_dialog,
                                                 'leaderboard': self.show_leaderboard,
                                                 'open_credit': self.open_credit},
                                      config.CREDIT)
            dialog.update(view)
        except Exception:
            self.result_attempts += 1
            self.result_retry_at = now + 1.
            if creating and dialog is not None:
                dialog.close()
            raise
        else:
            if creating:
                self.result_dialog = dialog
                self.result_serial = serial
                diag.log(f'result created and rendered: serial={serial}')
            self.result_attempts = 0

    @staticmethod
    def open_credit():
        webbrowser.open(config.CREDIT_URL)

    def play_again(self):
        if self.result_dialog is not None:
            self.result_dialog.close()
            self.result_dialog = None
        self.restart()

    def show_leaderboard(self):
        if self.leaderboard_dialog is not None and self.leaderboard_dialog.alive():
            self.leaderboard_dialog.top.lift()
            self.leaderboard_dialog.top.focus_set()
            return
        self.leaderboard_dialog = LeaderboardDialog(self.root, self.rank.fetch_rankings)

    def enable_rank_from_dialog(self):
        if self.rank.available and not self.rank.enabled:
            self.toggle_rank()

    def fail(self, error):
        self.show_toolbar()
        try:
            self.restore()
        except Exception as exc:
            messagebox.showerror(t('product.name'), f'{describe_error(error)}\n\n{exc}', parent=self.root)
            return
        _write_log('error.log', traceback.format_exc())
        messagebox.showerror(t('product.name'), describe_error(error), parent=self.root)
        self.close()

    def restore(self):
        if self.runner:
            self.runner.stop()
            self.runner = None
        if self.session:
            self.session.restore()
            self.session = None

    def close(self):
        if self.closing:
            return
        try:
            self.restore()
        except Exception as exc:
            messagebox.showwarning(t('product.name'), t('ui.error.restore_incomplete', error=exc),
                                   parent=self.root)
            return
        self.closing = True
        self.rank.close()
        self.remember_position()
        self.hotkeys.close()
        ctypes.windll.kernel32.CloseHandle(self.instance_handle)
        self.root.destroy()


def main():
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    app = None
    desktop_guard = None
    try:
        handle = acquire_instance(config.VERSION)
        desktop_guard = claim_instance_mutex(config.DESKTOP_GUARD)
        if desktop_guard is None:
            ctypes.windll.kernel32.CloseHandle(handle)
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(t('product.name'), t('ui.error.stop_conflict'), parent=root)
            root.destroy()
            return
        root = tk.Tk()
        def callback_error(kind, error, trace):
            text = ''.join(traceback.format_exception(kind, error, trace))
            _write_log('callback.log', text)
            diag.log('Tk callback failed:', text)
        root.report_callback_exception = callback_error
        app = DesktopBilliardsApp(root, handle)
        root.mainloop()
    except SystemExit:
        raise
    except Exception as error:
        _write_log('error.log', traceback.format_exc())
        if app is not None:
            try:
                app.close()
            except Exception:
                pass
        else:
            try:
                messagebox.showerror(t('product.name'), t('ui.error.start_failed', error=describe_error(error)))
            except Exception:
                pass
        raise
    finally:
        if desktop_guard:
            ctypes.windll.kernel32.CloseHandle(desktop_guard)
