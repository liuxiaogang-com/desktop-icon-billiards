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

from . import card, diag, fonts, i18n
from .display import work_area
from .i18n import t
from .release_notes import note_blocks

BG = '#0e1a18'
CARD = '#152623'
TEXT = '#e8f5f0'
MUTED = '#8aa69e'
ACCENT = '#69ddb3'
WARN = '#f2b880'
GOLD = '#f2ca87'
NUMBERS = 'Segoe UI Semibold'
# Keep the wrapped bar a few pixels clear of the screen edge, so it never looks cut off.
EDGE_GUTTER = 8


def ui_font(size, bold=False):
    """A Tk font tuple in the interface family of the active language.

    Resolved per call, not at import, so switching language re-styles every window that
    is built afterwards as well as the toolbar that rebuilds its own labels.
    """
    family = fonts.ui_family()
    return (family, size, 'bold') if bold else (family, size)

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
        super().__init__(parent, text=text, font=ui_font(9, bold=bold), cursor='hand2',
                         padx=padx, pady=pady, bd=0)
        self.command = command
        self.kind = kind
        self.bold = bold
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

    def restyle(self):
        """Re-apply the interface font after a language change."""
        self.configure(font=ui_font(9, bold=self.bold))

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
    style.configure('Table.TLabel', background=BG, foreground=TEXT, font=ui_font(9))
    style.configure('Table.TButton', padding=(11, 6), font=ui_font(9), background=CARD, foreground=TEXT,
                    borderwidth=0, focusthickness=1, focuscolor=ACCENT)
    style.map('Table.TButton', background=[('active', '#1d3531'), ('pressed', '#24423c')])
    style.configure('Dark.Vertical.TScrollbar', background='#2b4a44', troughcolor=CARD, bordercolor=CARD,
                    lightcolor='#2b4a44', darkcolor='#2b4a44', arrowcolor=MUTED, relief='flat', gripcount=0)
    style.map('Dark.Vertical.TScrollbar', background=[('active', '#3a645b'), ('pressed', '#3a645b')])
    style.configure('Language.TCombobox', fieldbackground=CARD, background=CARD, foreground=TEXT,
                    bordercolor='#29443c', lightcolor=CARD, darkcolor=CARD, arrowcolor=MUTED,
                    selectbackground=CARD, selectforeground=TEXT, padding=(8, 3), arrowsize=11,
                    font=ui_font(9))
    style.map('Language.TCombobox',
              fieldbackground=[('readonly', CARD)], foreground=[('readonly', TEXT)],
              selectbackground=[('readonly', CARD)], selectforeground=[('readonly', TEXT)],
              arrowcolor=[('active', TEXT)], background=[('readonly', CARD), ('active', '#1d3531')])
    # The drop-down list is a classic Tk listbox, so it is themed through the option database.
    # The family needs Tk braces: an unquoted "Segoe UI 9" is read as a three-element list and
    # every attempt to post the drop-down fails with "expected integer but got UI". The family
    # covers Latin and CJK, because the list shows language names in their own scripts.
    for option, value in (('background', CARD), ('foreground', TEXT), ('selectBackground', '#1f5444'),
                          ('selectForeground', '#b6fbe0'), ('borderWidth', 0),
                          ('font', f'{{{fonts.DATA_FAMILY}}} 9')):
        root.option_add(f'*TCombobox*Listbox.{option}', value)


def default_look():
    """Text and look of the toolbar buttons before the first refresh."""
    return {
        'guide': {'text': t('panel.toolbar.guide_on'), 'kind': 'on'},
        'rank': {'text': t('panel.toolbar.rank'), 'kind': 'off'},
        'felt': {'text': t('panel.toolbar.felt_on'), 'kind': 'ghost'},
        'update': {'text': t('panel.toolbar.update'), 'kind': 'ghost'},
    }


class MainPanel:
    """A draggable toolbar with functions above the current round and its actions."""

    # Pill key -> the neutral label it carries when no state text is set on it.
    LABELS = {
        'guide': 'panel.toolbar.guide',
        'felt': 'panel.toolbar.felt',
        'white': 'panel.toolbar.white_ball',
        'rank': 'panel.toolbar.rank',
        'leaderboard': 'panel.toolbar.leaderboard',
        'update': 'panel.toolbar.update',
        'donate': 'panel.toolbar.donate',
        'quit': 'panel.toolbar.quit',
        'restart': 'panel.toolbar.restart',
    }

    def __init__(self, root, version, callbacks, credit=''):
        configure_styles(root)
        root.configure(bg=BG)
        root.overrideredirect(True)
        self.root = root
        self.callbacks = callbacks
        self.pills = {}
        self.look = default_look()
        self.time_title = tk.StringVar(root, value=t('panel.stats.time'))
        self.time_value = tk.StringVar(root, value='00:00')
        self.count_title = tk.StringVar(root, value=t('panel.stats.count'))
        self.count_value = tk.StringVar(root, value='—')
        self.remaining_title = tk.StringVar(root, value=t('panel.stats.remaining'))
        self.remaining_value = tk.StringVar(root, value='—')
        self.shell = tk.Frame(root, bg=BG, padx=10, pady=8)
        self.shell.pack(fill='both', expand=True)
        self.shell.columnconfigure(1, weight=1)
        self.grip = tk.Label(self.shell, text='⋮⋮', font=ui_font(11), fg=MUTED, bg=BG, cursor='fleur')
        self.grip.grid(row=0, column=0, rowspan=3, padx=(0, 12), sticky='ns')
        self._draggable(self.grip)
        self._draggable(self.shell)
        self.functions = tk.Frame(self.shell, bg=BG)
        self.functions.grid(row=0, column=1, sticky='ew')
        self.groups = []
        for items in (
                (('guide', t('panel.toolbar.guide'), 'toggle_guide', 'on'),
                 ('felt', t('panel.toolbar.felt'), 'toggle_felt', 'ghost'),
                 ('white', t('panel.toolbar.white_ball'), 'white_ball', 'ghost')),
                (('rank', t('panel.toolbar.rank'), 'toggle_rank', 'off'),
                 ('leaderboard', t('panel.toolbar.leaderboard'), 'leaderboard', 'soft')),
                (('update', t('panel.toolbar.update'), 'check_updates', 'ghost'),)):
            self._add_group(items)
        # The picker sits beside "check updates"; "support" closes the group after it.
        self.language = self._add_language_picker(self.groups[-1])
        self._add_pill(self.groups[-1], ('donate', t('panel.toolbar.donate'), 'donate', 'gold'))
        self.credit_label = None
        if credit:
            self.credit_label = tk.Label(self.functions, text=credit, font=ui_font(8), fg=MUTED, bg=BG,
                                         cursor='hand2')
            self.credit_label.bind('<ButtonRelease-1>', lambda event: callbacks['open_credit']())
            self.groups.append(self.credit_label)
        separator = tk.Frame(self.shell, bg='#263d36', height=1)
        separator.grid(row=1, column=1, sticky='ew', pady=7)
        stats = tk.Frame(self.shell, bg=BG)
        stats.grid(row=2, column=1, sticky='ew')
        self._draggable(stats)
        self.stat_labels = []
        for title, value in ((self.time_title, self.time_value), (self.count_title, self.count_value),
                             (self.remaining_title, self.remaining_value)):
            box = tk.Frame(stats, bg=BG)
            box.pack(side='left', padx=(0, 14))
            self._draggable(box)
            for variable, colour, font in ((title, MUTED, ui_font(8)), (value, TEXT, (NUMBERS, 11))):
                label = tk.Label(box, textvariable=variable, font=font, fg=colour, bg=BG)
                label.pack(side='left', padx=(0, 5))
                self._draggable(label)
                if variable is title:
                    self.stat_labels.append(label)
        self.pills['quit'] = Pill(stats, t('panel.toolbar.quit'), callbacks['quit'], 'soft', padx=10, pady=4)
        self.pills['quit'].pack(side='right')
        self.pills['restart'] = Pill(stats, t('panel.toolbar.restart'), callbacks['restart'], 'primary',
                                     bold=True, padx=10, pady=4)
        self.pills['restart'].pack(side='right', padx=(0, 8))
        self._layout_groups()

    def _add_group(self, items):
        """Add a group of pills; groups wrap together, never inside."""
        group = tk.Frame(self.functions, bg=BG)
        self.groups.append(group)
        self._draggable(group)
        for item in items:
            self._add_pill(group, item)
        return group

    def _add_pill(self, group, item):
        key, text, command, kind = item
        look = self.look.get(key, {})
        pill = Pill(group, look.get('text', text), self.callbacks.get(command),
                    look.get('kind', kind), padx=8, pady=4)
        pill.set(enabled=look.get('enabled', True))
        pill.pack(side='left', padx=(0, 4))
        self.pills[key] = pill
        return pill

    def _add_language_picker(self, group):
        """The drop-down beside "check updates" that switches the interface language."""
        picker = ttk.Combobox(group, state='readonly', width=8, style='Language.TCombobox',
                              values=[i18n.name(code) for code in i18n.available()], font=ui_font(9))
        picker.set(i18n.name())
        picker.pack(side='left', padx=(2, 4), pady=1)
        picker.bind('<<ComboboxSelected>>', self._language_selected)
        return picker

    def _language_selected(self, event=None):
        picker = self.language
        picker.selection_clear()
        callback = self.callbacks.get('language')
        if callback is not None:
            callback(i18n.code_for(picker.get()))

    def _layout_groups(self):
        """Place the groups, wrapping onto a new row when the monitor is narrow."""
        for group in self.groups:
            group.grid_forget()
        self.root.update_idletasks()
        left, _, right, _ = work_area(self.root.winfo_rootx(), self.root.winfo_rooty())
        # The shell padding and the grip column grow with the monitor DPI, so the room the
        # groups get is the work area minus everything they never fill.
        chrome = 2 * int(self.shell.cget('padx')) + self.grip.winfo_reqwidth() + 12
        available = max(1, right - left - chrome - EDGE_GUTTER)
        row = column = used = 0
        for group in self.groups:
            width = group.winfo_reqwidth() + 12
            if used and used + width > available:
                row, column, used = row + 1, 0, 0
            group.grid(row=row, column=column, sticky='w', padx=(0, 12), pady=(0, 2))
            column += 1
            used += width

    def relayout(self):
        """Refit the bar once every label is final, so the wrap uses the widths drawn.

        Callers that replace state labels (``Guide on``, ``Rank on``, …) must call this
        afterwards: those labels are wider than the neutral ones the bar was built with.
        """
        self._layout_groups()

    def retranslate(self):
        """Re-read every label from the active catalog and re-font the widgets.

        Laying the bar out is left to :meth:`relayout`, because the state labels are only
        replaced afterwards, and those decide the final widths.
        """
        configure_styles(self.root)
        for key, catalog_key in self.LABELS.items():
            pill = self.pills.get(key)
            if pill is not None and pill.winfo_exists():
                pill.set(t(catalog_key))
                self.look.setdefault(key, {})['text'] = t(catalog_key)
        for pill in self.pills.values():
            pill.restyle()
        if self.credit_label is not None:
            self.credit_label.configure(text=t('product.credit'), font=ui_font(8))
        self.grip.configure(font=ui_font(11))
        for label in self.stat_labels:
            label.configure(font=ui_font(8))
        self.time_title.set(t('panel.stats.time'))
        self.count_title.set(t('panel.stats.count'))
        self.remaining_title.set(t('panel.stats.remaining'))
        self.language.configure(font=ui_font(9))
        self.language.set(i18n.name())

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
        try:
            self._build(root, callbacks)
        except Exception:
            self.close()
            raise

    def _build(self, root, callbacks):
        top = self.top
        top.title(t('panel.result.title'))
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
            self.leaderboard_pill = Pill(shell, t('panel.result.leaderboard'), callbacks['leaderboard'],
                                         'ghost', pady=8)
            self.leaderboard_pill.pack(fill='x')
        else:
            self.leaderboard_pill = None

        share = tk.Frame(shell, bg=BG, padx=12, pady=12)
        share.pack(fill='x')
        self.share_pills = []
        for column, (key, command, kind) in enumerate((('panel.result.save', self.save, 'soft'),
                                                       ('panel.result.copy', self.copy, 'soft'),
                                                       ('panel.result.again', callbacks['again'], 'primary'))):
            share.columnconfigure(column, weight=1, uniform='actions')
            pill = Pill(share, t(key), command, kind, bold=kind == 'primary', padx=8, pady=11)
            pill.grid(row=0, column=column, sticky='ew',
                      padx=(0 if column == 0 else 6, 0 if column == 2 else 6))
            self.share_pills.append((pill, key))
        status = self.status = tk.Frame(shell, bg=BG, height=30)
        status.pack_propagate(False)
        self.feedback_text = tk.StringVar(top)
        self.feedback = tk.Label(status, textvariable=self.feedback_text, font=ui_font(8), fg=MUTED, bg=BG,
                                 anchor='w', justify='left')
        self.feedback.pack(side='left', fill='x', expand=True)
        self.feedback.bind('<ButtonRelease-1>', lambda event: self.open_folder())

        self.offer = Pill(status, t('panel.result.enable_rank'), callbacks['enable_rank'], 'ghost', padx=0, pady=0)
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
        image = card.render_card(view, credit=self.credit, site=self.site or t('product.credit'))
        previous, previous_image = self.last, self.image
        self.last, self.image = view, image
        try:
            if view['rank']['phase'] == 'off':
                self.offer.pack(side='right')
            else:
                self.offer.pack_forget()
            self._layout_status()
        except Exception:
            self.last, self.image = previous, previous_image
            raise
        diag.log(f'result rendered: rank_phase={view["rank"]["phase"]} '
                 f'window_state={self.top.state()} visible={self.top.winfo_viewable()}')

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
            self._say('panel.result.save_failed', MUTED, error=error)
            return
        self.folder = path.parent
        self._say('panel.result.saved', ACCENT, link=True)

    def copy(self):
        if self.image is None:
            return
        try:
            card.copy_card(self.image)
        except Exception:  # the clipboard can be locked by another program
            self._say('panel.result.copy_failed', WARN)
            return
        self._say('panel.result.copied', ACCENT)

    def open_folder(self):
        if self.folder is not None:
            try:
                os.startfile(self.folder)
            except OSError:
                pass

    def _say(self, key, colour, link=False, **values):
        # Stored as a key so a language switch can re-render the same message.
        self.feedback_state = (key, values, colour, link)
        self._render_feedback()

    def _render_feedback(self):
        state = getattr(self, 'feedback_state', None)
        if state is None:
            self.feedback_text.set('')
            self.feedback.configure(cursor='arrow')
            return
        key, values, colour, link = state
        self.feedback_text.set(t(key, **values))
        self._layout_status()
        self.feedback.configure(fg=colour, cursor='hand2' if link and self.folder is not None else 'arrow')

    def retranslate(self):
        """Re-read every label, then redraw the card in the new language."""
        if not self.alive():
            return
        self.top.title(t('panel.result.title'))
        if self.leaderboard_pill is not None:
            self.leaderboard_pill.set(t('panel.result.leaderboard'))
            self.leaderboard_pill.restyle()
        for pill, key in self.share_pills:
            pill.set(t(key))
            pill.restyle()
        self.offer.set(t('panel.result.enable_rank'))
        self.offer.restyle()
        self.feedback.configure(font=ui_font(8))
        view, self.last = self.last, None
        if view is not None:
            self.update(view)
        self._render_feedback()


class DonationDialog(tk.Toplevel):
    """The donation picture with its caption and close button."""

    def __init__(self, root, image):
        super().__init__(root)
        self.transient(root)
        self.resizable(False, False)
        body = ttk.Frame(self, padding=14, style='Panel.TFrame')
        body.pack()
        self.heading = ttk.Label(body, style='Table.TLabel')
        self.heading.pack(pady=(0, 10))
        self.photo = ImageTk.PhotoImage(image, master=self)
        ttk.Label(body, image=self.photo).pack()
        self.note = ttk.Label(body, style='Table.TLabel', wraplength=340, justify='center')
        self.note.pack(pady=(10, 0))
        self.close_button = ttk.Button(body, command=self.destroy, style='Table.TButton')
        self.close_button.pack(pady=(10, 0))
        self.bind('<Escape>', lambda event: self.destroy())
        self.retranslate()

    def alive(self):
        return bool(self.winfo_exists())

    def retranslate(self):
        """Re-read the copy without touching the picture or the window geometry."""
        if not self.alive():
            return
        self.title(t('ui.donate.title'))
        self.heading.configure(text=t('ui.donate.heading'))
        self.note.configure(text=t('ui.donate.note'))
        self.close_button.configure(text=t('ui.donate.close'))


class UpdateDialog(tk.Toplevel):
    """Native, display-only rich release notes with fixed header/footer.

    The notes come from the release feed, so they are never translated: :meth:`retranslate`
    refreshes the chrome and the note faces only, keeping the reader's scroll position.
    """

    def __init__(self, root, current, info, entries, on_download):
        super().__init__(root)
        self.current = current
        self.entries = entries
        self.on_download = on_download
        self.withdraw()
        self.configure(bg=BG)
        if root.state() != 'withdrawn':
            self.transient(root)
        self.resizable(True, True)
        self.scale = max(1., root.winfo_fpixels('1i') / 96)
        scale = self.scale
        pad = round(18 * scale)
        try:
            self.iconbitmap(str(card.LOGO.with_suffix('.ico')))
        except tk.TclError:
            pass
        self.shell = shell = tk.Frame(self, bg=BG, padx=pad, pady=pad)
        shell.pack(fill='both', expand=True)
        header = tk.Frame(shell, bg=BG)
        header.pack(fill='x', pady=(0, round(12 * scale)))
        self.heading = tk.Label(header, font=ui_font(13, bold=True), fg=TEXT, bg=BG)
        self.heading.pack(side='left')
        self.current_label = tk.Label(header, font=ui_font(9), fg=MUTED, bg=BG)
        self.current_label.pack(side='right')

        footer = tk.Frame(shell, bg=BG)
        footer.pack(side='bottom', fill='x', pady=(round(12 * scale), 0))
        self.download = Pill(footer, '', lambda: self.on_download(self), 'primary', bold=True,
                             padx=20, pady=10)
        self.download.pack(side='right')
        self.later = Pill(footer, '', self.destroy, 'soft', padx=18, pady=10)
        self.later.pack(side='right', padx=(0, 10))
        body = tk.Frame(shell, bg=CARD, highlightthickness=1, highlightbackground='#29443c')
        body.pack(fill='both', expand=True)
        notes = self.notes = tk.Text(body, width=38, height=10, wrap='word', bg=CARD, fg=TEXT,
                                     relief='flat', bd=0, highlightthickness=0,
                                     padx=round(18 * scale), pady=round(14 * scale),
                                     spacing1=2, cursor='arrow', insertwidth=0,
                                     selectbackground='#2b4a44')
        scrollbar = ttk.Scrollbar(body, orient='vertical', command=notes.yview,
                                  style='Dark.Vertical.TScrollbar')
        notes.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        notes.pack(side='left', fill='both', expand=True)
        self.retranslate()
        self.bind('<Escape>', lambda event: self.destroy())
        self.update_idletasks()
        monitor = win32api.MonitorFromPoint((root.winfo_rootx(), root.winfo_rooty()),
                                           win32con.MONITOR_DEFAULTTONEAREST)
        left, top, right, bottom = win32api.GetMonitorInfo(monitor)['Work']
        # Leave space for native borders and taskbar, including high-DPI small displays.
        width = max(1, min(round(620 * scale), right - left - round(32 * scale)))
        height = max(1, min(round(520 * scale), bottom - top - round(64 * scale)))
        self.minsize(min(width, round(400 * scale)), min(height, round(280 * scale)))
        self.maxsize(width, height)
        x = left + (right - left - width) // 2
        y = top + (bottom - top - height) // 2
        self.geometry(f'{width}x{height}+{x}+{y}')
        self.deiconify()
        self.lift()

    def alive(self):
        return bool(self.winfo_exists())

    def configure_tags(self):
        """The tag faces follow the interface language; the note text itself does not."""
        scale = self.scale
        notes = self.notes
        notes.configure(font=ui_font(10), spacing3=round(9 * scale))
        notes.tag_configure('body', foreground='#c4d9d2')
        notes.tag_configure('heading', font=ui_font(12, bold=True), foreground=TEXT,
                            spacing1=round(12 * scale), spacing3=round(8 * scale))
        notes.tag_configure('version', font=(NUMBERS, 13, 'bold'), foreground=ACCENT,
                            spacing1=round(18 * scale), spacing3=round(12 * scale))
        notes.tag_configure('first_version', spacing1=0)
        notes.tag_configure('date', font=ui_font(9), foreground=MUTED)
        notes.tag_configure('list', lmargin1=0, lmargin2=round(19 * scale), foreground='#c4d9d2')
        notes.tag_configure('quote', foreground=MUTED, lmargin1=round(12 * scale),
                            lmargin2=round(12 * scale))
        notes.tag_configure('bold', font=ui_font(10, bold=True), foreground=TEXT)
        notes.tag_configure('code', font=('Consolas', 10), foreground=ACCENT, background='#203a32')
        notes.tag_configure('space', font=ui_font(3), spacing1=0, spacing3=0)

    def render(self):
        notes = self.notes
        # Keep the reader's place by line: a new font re-wraps, so a fraction would drift.
        top = notes.index('@0,0')
        notes.configure(state='normal')
        notes.delete('1.0', 'end')
        for index, entry in enumerate(self.entries):
            tags = ('version', 'first_version') if index == 0 else ('version',)
            notes.insert('end', f'v{entry["version"]}' + (t('panel.update.latest_tag') if index == 0 else ''), tags)
            if entry.get('date'):
                notes.insert('end', f'    {entry["date"]}', (*tags, 'date'))
            notes.insert('end', '\n', tags)
            for kind, spans in note_blocks(entry['notes'] or t('panel.update.no_notes')):
                if kind == 'space':
                    notes.insert('end', '\n', 'space')
                    continue
                for text, style in spans:
                    tags = (kind, style) if style and not (kind == 'heading' and style == 'bold') else (kind,)
                    notes.insert('end', text, tags)
                notes.insert('end', '\n', kind)
        notes.configure(state='disabled')
        notes.yview(top)

    def retranslate(self):
        if not self.alive():
            return
        self.title(t('panel.update.title'))
        self.heading.configure(text=t('panel.update.heading'), font=ui_font(13, bold=True))
        self.current_label.configure(text=t('panel.update.current', version=self.current),
                                     font=ui_font(9))
        self.download.set(t('panel.update.download'))
        self.download.restyle()
        self.later.set(t('panel.update.later'))
        self.later.restyle()
        self.configure_tags()
        self.render()


def show_update_dialog(root, current, info, entries, on_download):
    """Native, display-only rich release notes with fixed header/footer."""
    return UpdateDialog(root, current, info, entries, on_download)
