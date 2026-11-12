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
from .panel import ACCENT, BG, CARD, FONT, GOLD, MUTED, TEXT, configure_styles
from .rank import RankError


class LeaderboardDialog:
    def __init__(self, root, load):
        self.load = load
        self.inbox = queue.SimpleQueue()
        self.busy = False
        self.top = top = tk.Toplevel(root)
        top.title('全球排行榜')
        top.configure(bg=BG)
        top.transient(root)
        top.iconbitmap(str(card.LOGO.with_suffix('.ico')))
        top.protocol('WM_DELETE_WINDOW', self.close)
        top.bind('<Escape>', lambda event: self.close())
        shell = tk.Frame(top, bg=BG, padx=26, pady=22)
        shell.pack(fill='both', expand=True)
        header = tk.Frame(shell, bg=BG)
        header.pack(fill='x')
        tk.Label(header, text='全球排行榜', font=(FONT, 19, 'bold'), bg=BG, fg=TEXT).pack(side='left')
        tk.Label(header, text='TOP 50', font=('Segoe UI', 10, 'bold'), bg=CARD, fg=ACCENT,
                 padx=12, pady=5).pack(side='right')
        tk.Label(shell, text='个人最佳 · 平均每颗用时', font=(FONT, 9),
                 bg=BG, fg=MUTED).pack(anchor='w', pady=(8, 20))

        self.status = tk.StringVar(top)
        self.status_label = tk.Label(shell, textvariable=self.status, font=(FONT, 8), bg=BG, fg=MUTED,
                                     anchor='w')
        self.status_label.pack(side='bottom', fill='x', pady=(12, 0))
        self.status_label.bind('<ButtonRelease-1>', self._retry)
        self.failed = False
        own = tk.Frame(shell, bg=CARD, padx=16, pady=14)
        own.pack(side='bottom', fill='x', pady=(16, 0))
        own_left = tk.Frame(own, bg=CARD)
        own_left.pack(side='left')
        tk.Label(own_left, text='我的最佳', font=(FONT, 9), fg=MUTED, bg=CARD).pack(anchor='w')
        self.personal = tk.StringVar(top, value='正在查询…')
        tk.Label(own_left, textvariable=self.personal, font=(FONT, 12, 'bold'), fg=ACCENT,
                 bg=CARD).pack(anchor='w', pady=(5, 0))
        self.personal_score = tk.StringVar(top)
        tk.Label(own, textvariable=self.personal_score, font=('Segoe UI', 15, 'bold'),
                 fg=TEXT, bg=CARD).pack(side='right')

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
        self.row_height = max(64, int(top.winfo_fpixels('1i') * .53))
        self.canvas = tk.Canvas(body, bg=BG, bd=0, highlightthickness=0, width=440, height=430,
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
        width = min(max(540, top.winfo_reqwidth()), right - left - bw - 32)
        height = min(max(720, top.winfo_reqheight()), bottom - upper - bh - 32)
        x, y = left + (right - left - width - bw) // 2, upper + (bottom - upper - height - bh) // 2
        top.geometry(f'{width}x{height}+{x}+{y}')
        top.minsize(min(width, 420), min(height, 350))
        top.maxsize(right - left - bw - 32, bottom - upper - bh - 32)
        self.poll_id = top.after(100, self._poll)
        self.refresh()

    def _draw_rows(self):
        canvas = self.canvas
        canvas.delete('all')
        width = canvas.winfo_width()
        h = self.row_height
        colors = {1: GOLD, 2: '#bbcbd5', 3: '#d5ae93'}
        for index, entry in enumerate(self.entries):
            y = index * h
            cy = y + h / 2
            mine = entry['is_me']
            colour = ACCENT if mine else colors.get(entry['rank'], MUTED)
            if mine:
                canvas.create_rectangle(0, y + 3, width, y + h - 3, fill='#183a30', outline='')
                canvas.create_rectangle(0, y + 12, 3, y + h - 12, fill=ACCENT, outline='')
            elif index:
                canvas.create_line(12, y, width - 12, y, fill='#1e302b')
            canvas.create_text(14, cy, text='TOP', anchor='w', font=('Segoe UI', 8, 'bold'), fill=colour)
            canvas.create_text(64, cy, text=str(entry['rank']), anchor='w',
                               font=('Segoe UI', 16, 'bold'), fill=colour)
            if mine:
                canvas.create_text(112, cy, text='我', anchor='w', font=(FONT, 9), fill=ACCENT)
            canvas.create_text(width - 64, cy, text=f'{entry["avg_ms"] / 1000:.2f}', anchor='e',
                               font=('Segoe UI', 16, 'bold'), fill=ACCENT if mine else TEXT)
            canvas.create_text(width - 10, cy + 2, text='秒/颗', anchor='e', font=(FONT, 8), fill=MUTED)
        canvas.configure(scrollregion=(0, 0, width, max(1, len(self.entries) * h)))

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
        self.status.set('正在获取最新排名…')
        threading.Thread(target=self._load, name='Billiards-leaderboard', daemon=True).start()

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
                self._draw_rows()
                self.canvas.yview_moveto(0)
                me = data['me']
                self.personal.set(f'第 {me["rank"]:,} 名' if me else '暂无上榜成绩')
                self.personal_score.set(f'{me["avg_ms"] / 1000:.2f} 秒/颗' if me else '')
                stamp = datetime.fromisoformat(data['generated_at'].replace('Z', '+00:00')).astimezone()
                self.status.set(('榜单暂无成绩 · ' if not data['entries'] else '') + f'更新于 {stamp:%m-%d %H:%M:%S}')
            else:
                message = ('排名服务尚未支持新榜单' if data.status == 404 else
                           '刷新过于频繁，请稍后重试' if data.code == 'rate_limited' else '暂时无法获取排名，请重试')
                self.failed = True
                self.status_label.configure(cursor='hand2', fg=ACCENT)
                self.status.set(message + ' · 点击重试')
                if not self.entries:
                    self.personal.set('暂时无法查询')
        self.poll_id = self.top.after(100, self._poll)
