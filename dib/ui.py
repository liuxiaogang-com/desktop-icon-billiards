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
from PIL import Image, ImageTk
from tkinter import messagebox, ttk

from . import config, diag, versus
from .display import dpi_context
from .hotkeys import Hotkeys
from .instance import acquire_instance, claim_instance_mutex, publish_version
from .overlay import BilliardsOverlay
from .panel import MainPanel, ResultDialog, format_duration, show_update_dialog
from .paths import runtime_directory
from .leaderboard import LeaderboardDialog
from .rank import RankApi, RankController, RankSettings, average_seconds, data_directory
from .runner import BilliardsRunner
from .shell import LayoutSession, ShellDesktop
from .update import fetch_update, release_entries


RANK_CONSENT = (
    '开启后，每局清台时会向排名服务器提交：\n'
    '  • 本局用时、图标数量、击球次数\n'
    '  • 一个匿名设备标识（由本机系统 ID 加盐哈希得到，无法还原）\n\n'
    '不会上传图标名称、桌面截图或任何个人文件信息。可随时在主窗口关闭。\n\n'
    '是否开启全球排名？'
)
RANK_INTRO = '现在可以和全球玩家比谁清台更快了。\n\n' + RANK_CONSENT


def _write_log(name, text):
    try:
        (runtime_directory() / name).write_text(text, encoding='utf-8')
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
    text = f'{error}\n\n出错位置：{where}' if where else str(error)
    return f'{text}\n\n详细信息见 %TEMP%\\DesktopIconBilliards\\error.log'


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
        root.title(config.WINDOW_TITLE)
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
        self.versus_dialog = None
        self.versus_session = None
        self.versus_pending = None
        self.versus_spoken = False
        self.versus_stuck = False
        self.update_results = queue.SimpleQueue()
        self.settings = self.make_settings()
        self.felt_enabled = bool(self.settings.get('felt', True))
        self.panel = panel = MainPanel(root, config.VERSION, {
            'restart': self.restart, 'toggle_guide': self.toggle_guide, 'toggle_rank': self.toggle_rank,
            'check_updates': lambda: self.check_updates(manual=True), 'donate': self.show_donation,
            'quit': self.close, 'toggle_felt': self.toggle_felt, 'versus': self.open_versus,
            'open_credit': self.open_credit,
        }, config.CREDIT)
        publish_version(root, config.VERSION)
        self.time_title, self.time_value = panel.time_title, panel.time_value
        self.count_title, self.count_value = panel.count_title, panel.count_value
        self.remaining_title = panel.remaining_title
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
        if self.versus_session is not None:
            # A LAN match never counts towards the global ranking.
            self.panel.set_look('rank', '排名 —', 'off', enabled=False)
            return
        if not self.rank.available:
            self.panel.set_look('rank', '排名 —', 'off', enabled=False)
            return
        on = self.rank.enabled
        self.panel.set_look('rank', '排名 开' if on else '排名 关', 'on' if on else 'off', enabled=True)

    def refresh_buttons(self):
        self.refresh_rank_button()
        self.panel.set_look('guide', '辅助线 开' if self.guide_enabled else '辅助线 关',
                            'on' if self.guide_enabled else 'off')
        self.panel.set_look('felt', '台面 开' if self.felt_enabled else '台面 关')

    # -- toolbar position --------------------------------------------------
    def place_window(self):
        root = self.root
        root.update_idletasks()
        width, height = root.winfo_reqwidth(), root.winfo_reqheight()
        monitor = win32api.MonitorFromPoint((0, 0), win32con.MONITOR_DEFAULTTOPRIMARY)
        left, top, right, bottom = win32api.GetMonitorInfo(monitor)['Work']
        x, y = self.settings.get('bar_x'), self.settings.get('bar_y')
        if not (isinstance(x, int) and isinstance(y, int)):
            x, y = left + max(0, (right - left - width) // 2), top + 2
        else:
            monitor = win32api.MonitorFromPoint((x, y), win32con.MONITOR_DEFAULTTONEAREST)
            left, top, right, bottom = win32api.GetMonitorInfo(monitor)['Work']
        x = max(left, min(x, right - width))
        y = max(top, min(y, bottom - height))
        root.geometry(f'+{x}+{y}')

    def remember_position(self):
        if self.root.state() == 'normal':
            self.settings.update(bar_x=self.root.winfo_x(), bar_y=self.root.winfo_y())

    def offer_rank(self):
        """Ask once, on the first run after the feature exists, whether to join the ranking."""
        if self.closing or not self.rank.needs_prompt:
            return
        self.rank.mark_prompted()
        if messagebox.askyesno('全球排名', RANK_INTRO, parent=self.root):
            self.rank.set_enabled(True)
        self.refresh_rank_button()

    def toggle_rank(self):
        if not self.rank.available:
            return
        if self.rank.enabled:
            self.rank.set_enabled(False)
        elif messagebox.askokcancel('全球排名', RANK_CONSENT, parent=self.root):
            self.rank.set_enabled(True)
        self.refresh_rank_button()

    def show_donation(self):
        if self.donation is not None and self.donation.winfo_exists():
            self.donation.lift()
            return
        try:
            with Image.open(config.ASSETS / 'donation.jpg') as source:
                image = source.convert('RGB')
            image.thumbnail((340, 340), Image.Resampling.LANCZOS)
        except OSError:
            messagebox.showwarning('打赏', '暂时无法打开赞赏图片。', parent=self.root)
            return
        self.donation = dialog = tk.Toplevel(self.root)
        dialog.title('打赏')
        dialog.transient(self.root)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=14, style='Panel.TFrame')
        body.pack()
        ttk.Label(body, text='微信扫码赞赏，感谢支持！', style='Table.TLabel').pack(pady=(0, 10))
        photo = ImageTk.PhotoImage(image, master=dialog)
        label = ttk.Label(body, image=photo)
        label.image = photo
        label.pack()
        ttk.Button(body, text='关闭', command=dialog.destroy, style='Table.TButton').pack(pady=(10, 0))
        dialog.bind('<Escape>', lambda event: dialog.destroy())

    def check_updates(self, manual=False):
        if self.closing or not self.panel.is_enabled('update'):
            return
        self.panel.set_look('update', '检查中…', enabled=False)

        def fetch():
            try:
                info = fetch_update(config.UPDATE_URL, config.VERSION)
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
        self.panel.set_look('update', '检查更新', enabled=True)
        if info:
            self.show_update(info)
        elif manual:
            messagebox.showinfo(config.PRODUCT_NAME, '已是最新版' if success else '检查失败，请稍后重试',
                                parent=self.root)

    def show_update(self, info):
        def download(dialog):
            if webbrowser.open(info['download_url']):
                dialog.destroy()
            else:
                messagebox.showerror('打开下载失败', '请检查默认浏览器设置。', parent=dialog)

        show_update_dialog(self.root, config.VERSION, info, release_entries(info), download)

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
                item['name'] = desktop.folder.GetDisplayNameOf(
                    [desktop.cache[item['id']]], desktop.shellcon.SHGDN_NORMAL)
            with dpi_context(-4):
                overlay = BilliardsOverlay(desktop, items)
                overlay.guide_enabled = self.guide_enabled
                overlay.want_felt = self.felt_enabled
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
                    raise RuntimeError('等待桌面恢复守护就绪超时。')
                ready = self.journal.with_name(self.journal.stem + '.watchdog.json')
                if ready.exists():
                    data = json.loads(ready.read_text(encoding='utf-8'))
                    if data.get('session_id') == self.session.snapshot['session_id'] and self.runner.prepared.is_set():
                        if not data.get('ready'):
                            raise RuntimeError('无法启动桌面恢复守护。')
                        self.runner.request_show(self.session.snapshot, self.journal)
            else:
                if not self.rank_offered:
                    self.rank_offered = True
                    self.root.after(800, self.offer_rank)
                overlay = self.runner.overlay
                table = overlay.table
                finished = table.state == 'finished'
                versus_active = False
                try:
                    self.watch_versus_switch(overlay)
                except Exception:
                    # A LAN switch must never interrupt a game or the desktop restore.
                    _write_log('versus.log', traceback.format_exc())
                try:
                    versus_active = self.show_versus_stats(overlay, table)
                except Exception:
                    # A LAN match must never interrupt a game or the desktop restore.
                    _write_log('versus.log', traceback.format_exc())
                if not versus_active:
                    minutes, seconds = divmod(table.round_seconds, 60)
                    self.time_value.set(f'{minutes:02d}:{seconds:02d}')
                    total = len(table.icons)
                    pocketed = table.pocketed_icons
                    self.time_title.set('总用时' if finished else '本局用时')
                    self.count_title.set('图标总数' if finished else '图标入袋')
                    self.count_value.set(str(total) if finished else f'{pocketed}/{total}')
                    self.remaining_title.set('剩余')
                    self.remaining_value.set(str(total - pocketed))
                try:
                    if not versus_active:
                        self.rank.observe(table)
                    self.update_result_dialog(overlay, table, finished, versus_active)
                except Exception:
                    # Ranking and dialogs must never interrupt a game or the desktop restore.
                    _write_log('rank.log', traceback.format_exc())
            self.root.after(50, self.poll)
        except Exception as exc:
            self.fail(exc)

    def restart(self):
        if self.runner and self.runner.shown.is_set():
            # The overlay knows the role: the host resets its own table, the guest asks it to.
            self.runner.overlay.request_restart()

    def toggle_guide(self):
        self.guide_enabled = not self.guide_enabled
        self.refresh_buttons()
        if self.runner:
            self.runner.overlay.guide_enabled = self.guide_enabled

    def toggle_felt(self):
        """Show or hide the table frame, pockets and wallpaper panel."""
        self.felt_enabled = not self.felt_enabled
        self.settings.update(felt=self.felt_enabled)
        self.refresh_buttons()
        if self.runner:
            self.runner.overlay.want_felt = self.felt_enabled

    # -- LAN versus --------------------------------------------------------
    def open_versus(self):
        """Show the create/join dialog, or raise it when it is already open."""
        if self.closing:
            return
        dialog = self.versus_dialog
        if dialog is not None and dialog.alive():
            dialog.lift()
            return
        self.versus_dialog = None  # the previous dialog finished or was cancelled
        if self.versus_session is not None:
            messagebox.showinfo('局域网联机', '正在联机对战中，请先结束当前对局。', parent=self.root)
            return
        if self.runner is None or not self.runner.shown.is_set():
            messagebox.showinfo('局域网联机', '台面还没有准备好，请稍后再试。', parent=self.root)
            return
        try:
            self.versus_dialog = versus.VersusDialog(self.root, on_ready=self.start_versus)
        except Exception as error:
            self.versus_dialog = None
            messagebox.showerror(config.PRODUCT_NAME, f'无法打开联机窗口：{error}', parent=self.root)

    def start_versus(self, session, role, peer_name):
        """Adopt the connected session and ask the animation thread for the versus table.

        The overlay owns every layered window, so the Tk thread only leaves a request; the
        animation thread swaps the table between two frames and reports back through
        ``versus_role`` / ``versus_error`` (see :meth:`poll`).
        """
        names = ('我', peer_name)
        self.versus_session = session
        self.versus_pending = (session, role, names)
        self.versus_spoken = False
        self.versus_stuck = False
        self.panel.set_look('versus', f'房间 {session.code}' if role == 'host' else '已连接', 'on')
        self.panel.set_look('rank', '排名 —', 'off', enabled=False)
        self.runner.overlay.request_versus(session, role, names)

    def watch_versus_switch(self, overlay):
        """Resolve a queued LAN table switch: report a failure, or note that it has landed."""
        if self.versus_pending is None:
            return
        error = getattr(overlay, 'versus_error', '')
        if error:
            self.versus_pending = None
            self.close_versus_session()
            self.panel.set_look('versus', '联机', 'soft')
            self.refresh_rank_button()
            messagebox.showerror('局域网联机', f'进入联机对战失败：{error}', parent=self.root)
        elif getattr(overlay, 'versus_role', None):
            self.versus_pending = None

    def close_versus_session(self):
        """Close the LAN socket once; safe from close() and from the disconnect path."""
        session, self.versus_session = self.versus_session, None
        if session is None:
            return
        try:
            session.close()
        except Exception:
            _write_log('versus.log', traceback.format_exc())

    def show_versus_stats(self, overlay, table):
        """Switch the toolbar to LAN-match wording; True while a match owns the table.

        Every read is defensive on purpose: the versus attributes arrive with the overlay work
        package, and the solo toolbar must survive reading them before they exist.
        """
        role = getattr(overlay, 'versus_role', None)
        if not role:
            return False
        reason = getattr(overlay, 'disconnect_reason', '')
        if reason:
            if not self.versus_stuck:
                self.finish_versus(str(reason))
                self.versus_stuck = bool(getattr(overlay, 'versus_role', None))
            return True
        total = len(getattr(table, 'icons', ()) or ())
        pocketed = getattr(table, 'pocketed_icons', 0)
        scores = list(getattr(table, 'scores', ()) or (0, 0))
        if len(scores) < 2:
            scores = [0, 0]
        local = getattr(table, 'local_side', 0 if role == 'host' else 1)
        local = local if local in (0, 1) else 0
        self.time_title.set('本局用时')
        self.count_title.set('入袋')
        self.count_value.set(f'{pocketed}/{total}')
        self.remaining_title.set('比分')
        self.remaining_value.set(f'我 {scores[local]} : {scores[1 - local]} 对手')
        your_turn = getattr(table, 'turn', local) == local
        self.panel.set_look('versus', '联机 · 轮到你' if your_turn else '联机 · 等对手',
                            'primary' if your_turn else 'soft')
        return True

    def finish_versus(self, reason):
        """Report a dropped match once, then hand the table and toolbar back to solo play."""
        if not self.versus_spoken:
            self.versus_spoken = True
            messagebox.showinfo('联机结束', reason, parent=self.root)
        try:
            if self.runner is not None:
                # The animation thread owns the windows: it drops the versus table between frames.
                self.runner.overlay.request_leave_versus(reason)
        except Exception:
            _write_log('versus.log', traceback.format_exc())
        finally:
            self.close_versus_session()
            self.panel.set_look('versus', '联机', 'soft')
            self.refresh_rank_button()

    # -- round result ------------------------------------------------------
    def update_result_dialog(self, overlay, table, finished, versus=False):
        """Open the result window once per cleared round and keep its numbers current."""
        if finished and self.result_serial != table.round_serial:
            self.result_serial = table.round_serial
            self.result_dialog = ResultDialog(self.root, {'again': self.play_again,
                                                          'enable_rank': self.enable_rank_from_dialog,
                                                          'leaderboard': self.show_leaderboard,
                                                          'open_credit': self.open_credit},
                                             config.CREDIT, config.SITE_TEXT)
        dialog = self.result_dialog
        if dialog is None:
            return
        if not dialog.alive():
            self.result_dialog = None
        elif not finished or table.round_serial != self.result_serial:
            dialog.close()  # a new round has begun
            self.result_dialog = None
        else:
            count = len(table.icons)
            skipped = {'phase': 'skipped', 'message': '联机对战不计入全球排名。'}
            dialog.update({
                'time': format_duration(table.play_ms),
                'average': f'{average_seconds(table.play_ms, count):.2f}',
                'balls': str(count),
                'shots': str(table.shots),
                'note': overlay.ball_info,
                'rank': skipped if versus else self.rank.view(table),
            })

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
        try:
            self.restore()
        except Exception as exc:
            messagebox.showerror(config.PRODUCT_NAME, f'{describe_error(error)}\n\n{exc}', parent=self.root)
            return
        _write_log('error.log', traceback.format_exc())
        messagebox.showerror(config.PRODUCT_NAME, describe_error(error), parent=self.root)
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
            messagebox.showwarning(config.PRODUCT_NAME, f'桌面恢复未完成：{exc}\n\n请重试退出。',
                                   parent=self.root)
            return
        self.closing = True
        self.rank.close()
        self.versus_pending = None
        self.close_versus_session()
        if self.versus_dialog is not None and self.versus_dialog.alive():
            self.versus_dialog.close()
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
            messagebox.showinfo(config.PRODUCT_NAME,
                                '检测到「桌面蹦迪」正在运行。\n请先退出它，再打开桌面图标台球。',
                                parent=root)
            root.destroy()
            return
        root = tk.Tk()
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
                messagebox.showerror(config.PRODUCT_NAME, f'启动失败：{describe_error(error)}')
            except Exception:
                pass
        raise
    finally:
        if desktop_guard:
            ctypes.windll.kernel32.CloseHandle(desktop_guard)
