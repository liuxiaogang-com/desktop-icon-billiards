"""Scrollable top 50 with a persistent personal best; network work stays off Tk."""
from datetime import datetime
import queue
import threading
import tkinter as tk
from tkinter import ttk

import win32api
import win32con
import win32gui

from . import card
from .i18n import t
from .panel import ACCENT, BG, CARD, GOLD, MUTED, TEXT, configure_styles, ui_font
from .rank import RankError


def round_details(entry):
    if entry is None:
        return ''
    parts = []
    if 'icon_count' in entry:
        parts.append(t('leaderboard.round.icons', count=entry['icon_count']))
    if 'shots' in entry:
        parts.append(t('leaderboard.round.shots', count=entry['shots']))
    return ' · '.join(parts)


class LeaderboardDialog:
    def __init__(self, root, load):
        self.load = load
        self.inbox = queue.SimpleQueue()
        self.busy = False
        self.top = top = tk.Toplevel(root)
        top.title(t('leaderboard.title'))
        top.configure(bg=BG)
        top.transient(root)
        top.iconbitmap(str(card.LOGO.with_suffix('.ico')))
        top.protocol('WM_DELETE_WINDOW', self.close)
        top.bind('<Escape>', lambda event: self.close())
        shell = tk.Frame(top, bg=BG, padx=26, pady=22)
        shell.pack(fill='both', expand=True)
        header = tk.Frame(shell, bg=BG)
        header.pack(fill='x')
        self.title_label = tk.Label(header, text=t('leaderboard.title'), font=ui_font(19, bold=True),
                                    bg=BG, fg=TEXT)
        self.title_label.pack(side='left')
        tk.Label(header, text='TOP 50', font=('Segoe UI', 10, 'bold'), bg=CARD, fg=ACCENT,
                 padx=12, pady=5).pack(side='right')
        self.subtitle = tk.Label(shell, text=t('leaderboard.subtitle'), font=ui_font(9), bg=BG, fg=MUTED)
        self.subtitle.pack(anchor='w', pady=(8, 20))

        self.status = tk.StringVar(top)
        self.status_label = tk.Label(shell, textvariable=self.status, font=ui_font(8), bg=BG, fg=MUTED,
                                     anchor='w')
        self.status_label.pack(side='bottom', fill='x', pady=(12, 0))
        self.status_label.bind('<ButtonRelease-1>', self._retry)
        self.failed = False
        self.me = None
        self.own_key = 'leaderboard.loading'
        self.own_canvas = tk.Canvas(shell, bg=CARD, bd=0, highlightthickness=0, height=104)
        self.own_canvas.pack(side='bottom', fill='x', pady=(20, 0))
        self.column_header = tk.Canvas(shell, bg=BG, bd=0, highlightthickness=0, height=42)
        self.column_header.pack(fill='x', pady=(0, 4))

        body = tk.Frame(shell, bg=BG)
        body.pack(fill='both', expand=True)
        configure_styles(top)
        style = ttk.Style(top)
        style.configure('Ranking.Vertical.TScrollbar', background='#29433c', troughcolor=BG,
                        bordercolor=BG, lightcolor='#29433c', darkcolor='#29433c',
                        borderwidth=0, arrowsize=9, width=7, gripcount=0, relief='flat')
        style.layout('Ranking.Vertical.TScrollbar', [
            ('Vertical.Scrollbar.trough', {'sticky': 'ns', 'children': [
                ('Vertical.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})]})])
        self.entries = []
        self.row_height = max(74, int(top.winfo_fpixels('1i') * .57))
        self.canvas = tk.Canvas(body, bg=BG, bd=0, highlightthickness=0, width=620, height=self.row_height * 10,
                                takefocus=True, yscrollincrement=self.row_height)
        scrollbar = ttk.Scrollbar(body, orient='vertical', command=self.canvas.yview,
                                  style='Ranking.Vertical.TScrollbar')
        scrollbar.pack(side='right', fill='y', padx=(8, 0))
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda event: self._draw_rows())
        top.bind('<MouseWheel>', self._wheel)
        for key, step in (('<Down>', 1), ('<Up>', -1), ('<Next>', 6), ('<Prior>', -6)):
            self.canvas.bind(key, lambda event, step=step: self.canvas.yview_scroll(step, 'units'))
        top.update_idletasks()
        monitor = win32api.MonitorFromPoint((root.winfo_rootx(), root.winfo_rooty()),
                                           win32con.MONITOR_DEFAULTTONEAREST)
        left, upper, right, bottom = win32api.GetMonitorInfo(monitor)['Work']
        hwnd = win32gui.GetAncestor(top.winfo_id(), win32con.GA_ROOT)
        x0, y0, x1, y1 = win32gui.GetWindowRect(hwnd)
        bw, bh = x1 - x0 - top.winfo_width(), y1 - y0 - top.winfo_height()
        width = min(max(700, top.winfo_reqwidth()), right - left - bw - 32)
        height = min(max(720, top.winfo_reqheight()), bottom - upper - bh - 32)
        x, y = left + (right - left - width - bw) // 2, upper + (bottom - upper - height - bh) // 2
        top.geometry(f'{width}x{height}+{x}+{y}')
        top.minsize(min(width, 540), min(height, 350))
        top.maxsize(right - left - bw - 32, bottom - upper - bh - 32)
        self.poll_id = top.after(100, self._poll)
        self.refresh()

    def _draw_rows(self):
        canvas = self.canvas
        canvas.delete('all')
        width = canvas.winfo_width()
        columns = (18, width * .43, width * .62, width - 18)
        self.column_header.delete('all')
        headers = (t('leaderboard.column.rank'), t('leaderboard.column.icons'),
                   t('leaderboard.column.shots'), t('leaderboard.column.average'))
        for x, text, anchor in zip(columns, headers, ('w', 'center', 'center', 'e')):
            self.column_header.create_text(x, 20, text=text, anchor=anchor, font=ui_font(9), fill=MUTED)
        h = self.row_height
        colors = {1: GOLD, 2: '#bbcbd5', 3: '#d5ae93'}
        for index, entry in enumerate(self.entries):
            y = index * h
            cy = y + h / 2
            mine = entry['is_me']
            colour = ACCENT if mine else colors.get(entry['rank'], MUTED)
            if mine:
                canvas.create_rectangle(0, y + 5, width, y + h - 5, fill='#183a30', outline='')
                canvas.create_rectangle(0, y + 20, 3, y + h - 20, fill=ACCENT, outline='')
            elif index:
                canvas.create_line(18, y, width - 18, y, fill='#1e302b')
            canvas.create_text(columns[0], cy, text=f'{entry["rank"]:02d}', anchor='w',
                               font=('Segoe UI', 17, 'bold'), fill=colour)
            if mine:
                canvas.create_text(72, cy, text=t('leaderboard.me'), anchor='w', font=ui_font(9), fill=ACCENT)
            for x, key in zip(columns[1:3], ('icon_count', 'shots')):
                canvas.create_text(x, cy, text=str(entry.get(key, '—')), font=('Segoe UI', 12), fill=MUTED)
            canvas.create_text(columns[3], cy, text=f'{entry["avg_ms"] / 1000:.3f}', anchor='e',
                               font=('Segoe UI', 17, 'bold'), fill=ACCENT if mine else TEXT)
        canvas.configure(scrollregion=(0, 0, width, max(1, len(self.entries) * h)))
        own = self.own_canvas
        own.delete('all')
        own.create_text(columns[0], 24, text=t('leaderboard.own_best'), anchor='w', font=ui_font(9), fill=MUTED)
        if self.me:
            own.create_text(columns[0], 67, text=t('leaderboard.own_rank', rank=f'{self.me["rank"]:,}'),
                            anchor='w', font=ui_font(12, bold=True), fill=ACCENT)
            for x, key in zip(columns[1:3], ('icon_count', 'shots')):
                own.create_text(x, 67, text=str(self.me.get(key, '—')), font=('Segoe UI', 12), fill=MUTED)
            own.create_text(columns[3], 67, text=f'{self.me["avg_ms"] / 1000:.3f}', anchor='e',
                            font=('Segoe UI', 17, 'bold'), fill=ACCENT)
        else:
            own.create_text(columns[0], 67, text=t(self.own_key), anchor='w', font=ui_font(11), fill=MUTED)

    def _wheel(self, event):
        if event.delta:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'

    def _retry(self, event):
        if self.failed:
            self.refresh()

    def alive(self):
        return self.top is not None and self.top.winfo_exists()

    def close(self):
        if self.alive():
            self.top.after_cancel(self.poll_id)
            self.top.destroy()
        self.top = None

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self.failed = False
        self.status_label.configure(cursor='arrow', fg=MUTED)
        self._set_status('refreshing')
        threading.Thread(target=self._load, name='Billiards-leaderboard', daemon=True).start()

    def _set_status(self, kind, **info):
        """Render the status line from its parts, so a language switch can redraw it."""
        self.status_state = (kind, info)
        self._apply_status()

    def _apply_status(self):
        kind, info = getattr(self, 'status_state', ('', {}))
        if kind == 'refreshing':
            text = t('leaderboard.refreshing')
        elif kind == 'updated':
            text = ((t('leaderboard.empty_prefix') if info['empty'] else '')
                    + t('leaderboard.updated', stamp=info['stamp']))
        elif kind == 'error':
            text = t(info['key']) + t('leaderboard.retry_hint')
        else:
            text = ''
        self.status.set(text)

    def retranslate(self):
        """Re-read every label from the active catalog and redraw the visible rows."""
        if not self.alive():
            return
        configure_styles(self.top)
        self.top.title(t('leaderboard.title'))
        self.title_label.configure(text=t('leaderboard.title'), font=ui_font(19, bold=True))
        self.subtitle.configure(text=t('leaderboard.subtitle'), font=ui_font(9))
        self.status_label.configure(font=ui_font(8))
        self._apply_status()
        self._draw_rows()

    def _load(self):
        try:
            self.inbox.put(('ok', self.load()))
        except RankError as error:
            self.inbox.put(('error', error))
        except Exception:
            self.inbox.put(('error', RankError('bad_response')))

    def _poll(self):
        if not self.alive():
            return
        try:
            kind, data = self.inbox.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            if kind == 'ok':
                self.entries = data['entries']
                self.me = data['me']
                self.own_key = 'leaderboard.empty'
                self._draw_rows()
                self.canvas.yview_moveto(0)
                stamp = datetime.fromisoformat(data['generated_at'].replace('Z', '+00:00')).astimezone()
                self._set_status('updated', stamp=f'{stamp:%m-%d %H:%M:%S}', empty=not data['entries'])
            else:
                key = ('leaderboard.error.unsupported' if data.status == 404 else
                       'leaderboard.error.rate_limited' if data.code == 'rate_limited'
                       else 'leaderboard.error.generic')
                self.failed = True
                self.status_label.configure(cursor='hand2', fg=ACCENT)
                self._set_status('error', key=key)
                if not self.entries:
                    self.own_key = 'leaderboard.unavailable'
                    self._draw_rows()
        self.poll_id = self.top.after(100, self._poll)
