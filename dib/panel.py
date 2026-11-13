"""Windows made of plain Tk widgets: the main panel, the shareable result window and the update dialog.

Only widgets live here. The game logic stays in :mod:`dib.ui`, which fills the string
variables and supplies the callbacks, so every window can also be previewed on its own.

The main panel is a single borderless toolbar with all actions visible.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

import win32api
import win32con
import win32gui

from PIL import Image, ImageTk

from . import card, diag

BG = '#0e1a18'
CARD = '#152623'
TEXT = '#e8f5f0'
MUTED = '#8aa69e'
ACCENT = '#69ddb3'
WARN = '#f2b880'
GOLD = '#f2ca87'
FONT = 'Microsoft YaHei UI'
NUMBERS = 'Segoe UI Semibold'

# kind -> (background, hover, foreground)
KINDS = {
    'primary': (ACCENT, '#86eac8', BG),
    'soft': ('#1f3733', '#294843', TEXT),
    'on': ('#1f5444', '#28695a', '#b6fbe0'),
    'off': ('#25322f', '#2f3f3b', MUTED),
    'ghost': (BG, '#18302c', MUTED),
    'gold': (BG, '#18302c', GOLD),
}


def format_duration(milliseconds):
    """``MM:SS.cc`` (``H:MM:SS.cc`` from one hour) for a round time in whole milliseconds."""
    centiseconds = max(0, int(milliseconds)) // 10
    seconds, centiseconds = divmod(centiseconds, 100)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    clock = f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'
    return f'{clock}.{centiseconds:02d}'


class Pill(tk.Label):
    """A flat button with hover feedback; ``set`` changes its text, look and availability."""

    def __init__(self, parent, text, command, kind='soft', *, bold=False, padx=12, pady=6):
        super().__init__(parent, text=text, font=(FONT, 9, 'bold' if bold else 'normal'), cursor='hand2',
                         padx=padx, pady=pady, bd=0)
        self.command = command
        self.kind = kind
        self.enabled = True
        self.hover = False
        self.bind('<Enter>', lambda event: self._hover(True))
        self.bind('<Leave>', lambda event: self._hover(False))
        self.bind('<ButtonRelease-1>', self._release)
        self._paint()

    def set(self, text=None, kind=None, enabled=None):
        if text is not None:
            self.configure(text=text)
        if kind is not None:
            self.kind = kind
        if enabled is not None:
            self.enabled = enabled
        self._paint()

    def _hover(self, value):
        self.hover = value
        self._paint()

    def _release(self, event):
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        if self.enabled and inside and self.command:
            self.command()

    def _paint(self):
        background, hover, foreground = KINDS[self.kind]
        if not self.enabled:
            self.configure(bg=KINDS['off'][0], fg='#5d726c', cursor='arrow')
            return
        self.configure(bg=hover if self.hover else background, fg=foreground, cursor='hand2')


def configure_styles(root):
    """ttk styles for the dialogs (the main window uses plain Tk widgets)."""
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('Panel.TFrame', background=BG)
    style.configure('Table.TLabel', background=BG, foreground=TEXT, font=(FONT, 9))
    style.configure('Table.TButton', padding=(11, 6), font=(FONT, 9), background=CARD, foreground=TEXT,
                    borderwidth=0, focusthickness=1, focuscolor=ACCENT)
    style.map('Table.TButton', background=[('active', '#1d3531'), ('pressed', '#24423c')])
    style.configure('Dark.Vertical.TScrollbar', background='#2b4a44', troughcolor=CARD, bordercolor=CARD,
                    lightcolor='#2b4a44', darkcolor='#2b4a44', arrowcolor=MUTED, relief='flat', gripcount=0)
    style.map('Dark.Vertical.TScrollbar', background=[('active', '#3a645b'), ('pressed', '#3a645b')])


class MainPanel:
    """A draggable, borderless row of round statistics and explicit actions."""

    DEFAULT_LOOK = {
        'guide': {'text': '辅助线 开', 'kind': 'on'},
        'rank': {'text': '排名', 'kind': 'off'},
        # 'hide': {'text': '隐藏', 'kind': 'soft'},
        'felt': {'text': '台面 开', 'kind': 'ghost'},
        'update': {'text': '检查更新', 'kind': 'ghost'},
    }

    def __init__(self, root, version, callbacks, credit=''):
        configure_styles(root)
        root.configure(bg=BG)
        root.overrideredirect(True)
        self.root = root
        self.pills = {}
        self.look = {key: dict(value) for key, value in self.DEFAULT_LOOK.items()}
        self.time_title = tk.StringVar(root, value='本局用时')
        self.time_value = tk.StringVar(root, value='00:00')
        self.count_title = tk.StringVar(root, value='图标入袋')
        self.count_value = tk.StringVar(root, value='—')
        self.remaining_title = tk.StringVar(root, value='剩余')
        self.remaining_value = tk.StringVar(root, value='—')
        self.shell = tk.Frame(root, bg=BG, padx=6, pady=5)
        self.shell.pack(fill='both', expand=True)
        grip = tk.Label(self.shell, text='⋮⋮', font=(FONT, 11), fg=MUTED, bg=BG, padx=6, cursor='fleur')
        grip.pack(side='left')
        self._draggable(grip)
        self._draggable(self.shell)
        for title, value in ((self.time_title, self.time_value), (self.count_title, self.count_value),
                             (self.remaining_title, self.remaining_value)):
            box = tk.Frame(self.shell, bg=BG, padx=7)
            box.pack(side='left')
            self._draggable(box)
            for variable, color, font in ((title, MUTED, (FONT, 8)), (value, TEXT, (NUMBERS, 11))):
                label = tk.Label(box, textvariable=variable, font=font, fg=color, bg=BG)
                label.pack(side='left', padx=(0, 4))
                self._draggable(label)
        for key, text, command, kind, bold in (
                ('restart', '重新开始', callbacks['restart'], 'primary', True),
                ('guide', '辅助线', callbacks['toggle_guide'], 'on', False),
                ('rank', '排名', callbacks['toggle_rank'], 'off', False),
                ('leaderboard', '排行榜', callbacks['leaderboard'], 'soft', False),
                # ('hide', '隐藏', None, 'soft', False),
                ('felt', '台面', callbacks['toggle_felt'], 'ghost', False),
                ('update', '检查更新', callbacks['check_updates'], 'ghost', False),
                ('donate', '打赏', callbacks['donate'], 'gold', False)):
            look = self.look.get(key, {})
            pill = Pill(self.shell, look.get('text', text), command, look.get('kind', kind),
                        bold=bold, padx=8, pady=4)
            pill.set(enabled=look.get('enabled', True))
            pill.pack(side='left', padx=(4, 0))
            self.pills[key] = pill
        if credit:
            link = tk.Label(self.shell, text=credit, font=(FONT, 8), fg=MUTED, bg=BG, padx=8, cursor='hand2')
            link.pack(side='left')
            link.bind('<ButtonRelease-1>', lambda event: callbacks['open_credit']())
        self.pills['quit'] = Pill(self.shell, '退出', callbacks['quit'], 'soft', padx=8, pady=4)
        self.pills['quit'].pack(side='left', padx=(4, 0))
        root.update_idletasks()

    def _draggable(self, widget):
        widget.bind('<ButtonPress-1>', self._drag_start)
        widget.bind('<B1-Motion>', self._drag_move)

    def _drag_start(self, event):
        self.drag_offset = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag_move(self, event):
        x, y = self.drag_offset
        self.root.geometry(f'+{event.x_root - x}+{event.y_root - y}')

    def set_look(self, key, text=None, kind=None, enabled=None):
        look = self.look.setdefault(key, {})
        for name, value in (('text', text), ('kind', kind), ('enabled', enabled)):
            if value is not None:
                look[name] = value
        pill = self.pills.get(key)
        if pill is not None and pill.winfo_exists():
            pill.set(text, kind, enabled)

    def is_enabled(self, key):
        return self.look.get(key, {}).get('enabled', True)


class ResultDialog:
    """The round-end window: a preview of the shareable result card and the buttons to share it.

    :meth:`update` takes a ``view`` dict and may be called every frame; the card is only redrawn
    when the view changes. Keys: ``time``, ``average``, ``balls``, ``shots``, ``note`` and
    ``rank`` (phase ``off`` / ``unavailable`` / ``skipped`` / ``pending`` / ``ok`` / ``error``).
    """

    PREVIEW = (600, 750)

    def __init__(self, root, callbacks, credit='', site=''):
        self.callbacks = callbacks
        self.credit = credit
        self.site = site
        self.image = None
        self.photo = None
        self.last = None
        self.folder = None
        self.root = root
        self.top = tk.Toplevel(root)
        top = self.top
        top.title('清台完成')
        for event in ('<Map>', '<Unmap>', '<Destroy>'):
            top.bind(event, lambda e, name=event: diag.log(f'result window event: {name}')
                     if e.widget is top else None, add='+')
        top.configure(bg=BG)
        top.transient(root)
        top.resizable(False, False)
        top.iconbitmap(str(card.LOGO.with_suffix('.ico')))
        shell = tk.Frame(top, bg=BG)
        shell.pack(fill='both', expand=True)

        self.photo = ImageTk.PhotoImage(Image.new('RGB', self.PREVIEW, CARD), master=top)
        self.preview = tk.Label(shell, image=self.photo, bg=BG, bd=0)
        self.preview.pack()

        if callbacks.get('leaderboard'):
            Pill(shell, '查看排行榜  ›', callbacks['leaderboard'], 'ghost', pady=8).pack(fill='x')

        share = tk.Frame(shell, bg=BG, padx=12, pady=12)
        share.pack(fill='x')
        for column, (text, command, kind) in enumerate((
                ('保存图片', self.save, 'soft'), ('复制图片', self.copy, 'soft'),
                ('再来一局', callbacks['again'], 'primary'))):
            share.columnconfigure(column, weight=1, uniform='actions')
            Pill(share, text, command, kind, bold=kind == 'primary', padx=8, pady=11).grid(
                row=0, column=column, sticky='ew', padx=(0 if column == 0 else 6, 0 if column == 2 else 6))
        status = self.status = tk.Frame(shell, bg=BG, height=30)
        status.pack_propagate(False)
        self.feedback_text = tk.StringVar(top)
        self.feedback = tk.Label(status, textvariable=self.feedback_text, font=(FONT, 8), fg=MUTED, bg=BG,
                                 anchor='w', justify='left')
        self.feedback.pack(side='left', fill='x', expand=True)
        self.feedback.bind('<ButtonRelease-1>', lambda event: self.open_folder())

        self.offer = Pill(status, '开启全球排名', callbacks['enable_rank'], 'ghost', padx=0, pady=0)
        top.bind('<Escape>', lambda event: self.close())
        top.protocol('WM_DELETE_WINDOW', self.close)
        top.update_idletasks()
        self._fit_screen(root)
        top.lift()

    def _fit_screen(self, root):
        # Use the toolbar's monitor and its work area (excluding the taskbar),
        # with measured native borders and DPI-scaled controls rather than a fixed height.
        monitor = win32api.MonitorFromPoint((root.winfo_rootx(), root.winfo_rooty()),
                                           win32con.MONITOR_DEFAULTTONEAREST)
        left, upper, right, bottom = win32api.GetMonitorInfo(monitor)['Work']
        top = self.top
        hwnd = win32gui.GetAncestor(top.winfo_id(), win32con.GA_ROOT)
        x0, y0, x1, y1 = win32gui.GetWindowRect(hwnd)
        border_w = x1 - x0 - top.winfo_width()
        border_h = y1 - y0 - top.winfo_height()
        extra_w = top.winfo_reqwidth() - self.photo.width() + border_w
        extra_h = top.winfo_reqheight() - self.photo.height() + border_h
        scale = min(1, (right - left - extra_w - 32) / self.PREVIEW[0],
                    (bottom - upper - extra_h - 32) / self.PREVIEW[1])
        self.preview_size = (max(1, int(self.PREVIEW[0] * scale)), max(1, int(self.PREVIEW[1] * scale)))
        frame = self.image.resize(self.preview_size, Image.Resampling.LANCZOS) if self.image else Image.new('RGB', self.preview_size, CARD)
        self.photo = ImageTk.PhotoImage(frame, master=top)
        self.preview.configure(image=self.photo)
        top.update_idletasks()
        width, height = top.winfo_reqwidth() + border_w, top.winfo_reqheight() + border_h
        x = left + max(0, (right - left - width) // 2)
        y = upper + max(0, (bottom - upper - height) // 2)
        top.geometry(f'+{x}+{y}')

    def alive(self):
        return self.top is not None and self.top.winfo_exists()

    def close(self):
        if self.alive():
            diag.log('result close requested')
            self.top.destroy()
        self.top = None

    def update(self, view):
        if not self.alive() or view == self.last:
            return
        self.last = view
        self.image = card.render_card(view, credit=self.credit, site=self.site)
        diag.log(f'result rendered: rank_phase={view["rank"]["phase"]} '
                 f'window_state={self.top.state()} visible={self.top.winfo_viewable()}')
        if view['rank']['phase'] == 'off':
            self.offer.pack(side='right')
        else:
            self.offer.pack_forget()
        self._layout_status()

    def _layout_status(self):
        if self.feedback_text.get() or (self.last and self.last['rank']['phase'] == 'off'):
            self.status.pack(fill='x', padx=12, pady=(0, 8))
        else:
            self.status.pack_forget()
        self.top.update_idletasks()
        self._fit_screen(self.root)

    def save(self):
        if self.image is None:
            return
        try:
            path = card.save_card(self.image)
        except OSError as error:
            self._say(f'保存失败：{error}', MUTED)
            return
        self.folder = path.parent
        self._say('已保存，点此打开所在文件夹', ACCENT)

    def copy(self):
        if self.image is None:
            return
        try:
            card.copy_card(self.image)
        except Exception:  # the clipboard can be locked by another program
            self._say('复制失败，请改用「保存图片」', WARN)
            return
        self._say('已复制，可直接粘贴到微信、QQ 或帖子里', ACCENT)

    def open_folder(self):
        if self.folder is not None:
            try:
                os.startfile(self.folder)
            except OSError:
                pass

    def _say(self, text, colour):
        self.feedback_text.set(text)
        self._layout_status()
        self.feedback.configure(fg=colour, cursor='hand2' if self.folder is not None and '点此' in text else 'arrow')


def show_update_dialog(root, current, info, entries, on_download):
    """A dark release-notes dialog. ``entries`` is the display list from ``release_entries``;
    ``on_download(dialog)`` opens the download and closes the dialog when it succeeds."""
    dialog = tk.Toplevel(root)
    dialog.title(f'发现新版本 {info["version"]}')
    dialog.configure(bg=BG)
    dialog.transient(root)
    dialog.resizable(False, False)
    shell = tk.Frame(dialog, bg=BG, padx=18, pady=16)
    shell.pack(fill='both', expand=True)

    tk.Label(shell, text='发现新版本', font=(FONT, 12, 'bold'), fg=TEXT, bg=BG).pack(anchor='w')
    versions = tk.Frame(shell, bg=BG)
    versions.pack(fill='x', pady=(4, 10))
    tk.Label(versions, text=f'v{current}', font=(FONT, 9), fg=MUTED, bg=BG).pack(side='left')
    tk.Label(versions, text='  →  ', font=(FONT, 9), fg=MUTED, bg=BG).pack(side='left')
    tk.Label(versions, text=f'v{info["version"]}', font=(NUMBERS, 11), fg=ACCENT, bg=BG).pack(side='left')

    card = tk.Frame(shell, bg=CARD)
    card.pack(fill='both', expand=True)
    notes = tk.Text(card, width=46, height=12, wrap='word', bg=CARD, fg=TEXT, relief='flat', bd=0,
                    highlightthickness=0, padx=14, pady=10, font=(FONT, 9), spacing3=3, cursor='arrow',
                    insertwidth=0, selectbackground='#2b4a44')
    scrollbar = ttk.Scrollbar(card, orient='vertical', command=notes.yview, style='Dark.Vertical.TScrollbar')
    notes.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side='right', fill='y')
    notes.pack(side='left', fill='both', expand=True)
    notes.tag_configure('latest', font=(FONT, 10, 'bold'), foreground=ACCENT, spacing1=0)
    notes.tag_configure('older', font=(FONT, 10, 'bold'), foreground=TEXT, spacing1=8)
    notes.tag_configure('body', foreground='#c4d9d2')
    for index, entry in enumerate(entries):
        notes.insert('end', f'{entry["version"]}\n', 'latest' if index == 0 else 'older')
        notes.insert('end', (entry['notes'] or '暂无更新说明') + '\n', 'body')
    notes.configure(state='disabled')

    buttons = tk.Frame(shell, bg=BG)
    buttons.pack(fill='x', pady=(14, 0))
    Pill(buttons, '去更新', lambda: on_download(dialog), 'primary', bold=True, padx=18).pack(side='right')
    Pill(buttons, '稍后', dialog.destroy, 'soft', padx=16).pack(side='right', padx=(0, 8))
    dialog.bind('<Escape>', lambda event: dialog.destroy())

    # Align the dialog's right edge with the main window, just below it, and keep it on screen.
    dialog.update_idletasks()
    x = max(0, root.winfo_x() + root.winfo_width() - dialog.winfo_reqwidth())
    dialog.geometry(f'+{x}+{root.winfo_y() + root.winfo_height() + 10}')
    return dialog
