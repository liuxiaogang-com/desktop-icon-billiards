"""The LAN-versus dialog: create a room, or join one found by broadcast.

Only the dialog lives here. Sockets belong to :mod:`dib.netplay`, imported inside the methods
that need it, so this module - and the toolbar that imports it - still load while the transport
module is unavailable.

Threading rule: ``Tk`` is only touched on the Tk thread. The background workers (join, discovery)
hand their result to ``self.results``; the 300 ms ``after`` poll drains it on the Tk thread and
calls back from there.
"""
from __future__ import annotations

import queue
import threading
import traceback

import tkinter as tk
from tkinter import messagebox

from .panel import ACCENT, BG, CARD, FONT, MUTED, NUMBERS, TEXT, WARN, Pill

CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
POLL_MS = 300
ROOM_ROWS = 6
HOVER = '#1d3531'
FIELD = 300  # wrapper width of the hint lines, in pixels


def _netplay():
    """The transport module, imported late so a missing module cannot break ``import dib.ui``."""
    from . import netplay
    return netplay


def clean_code(text):
    """Upper case, drop characters a room code can never contain, keep at most four."""
    if not text:
        return ''
    return ''.join(character for character in str(text).upper() if character in CODE_ALPHABET)[:4]


def describe_room(entry):
    """One line for a discovered room: ``PC-A · 房间 ABCD · 192.168.1.7``."""
    if not isinstance(entry, dict):
        return '未知房间'
    name = str(entry.get('name') or '').strip() or '未命名电脑'
    code = clean_code(entry.get('room') or '')
    host = str(entry.get('host') or '').strip()
    parts = [name.replace('\n', ' '), f'房间 {code}' if code else '房间未知']
    if host:
        parts.append(host)
    return ' · '.join(parts)


def pick_default(rooms):
    """The ``(host, code)`` of the first discovered room that carries both, else empty strings."""
    for entry in rooms or ():
        if not isinstance(entry, dict):
            continue
        host = str(entry.get('host') or '').strip()
        code = clean_code(entry.get('room') or '')
        if host and code:
            return host, code
    return '', ''


def format_addresses(addresses):
    """The local IPv4 list as one short line for the create tab."""
    values = []
    for address in addresses or ():
        text = str(address or '').strip()
        if text and text not in values:
            values.append(text)
    return ' / '.join(values) if values else '未找到局域网地址，请检查网络连接'


class VersusDialog:
    """A two-tab dark dialog; ``on_ready(session, role, peer_name)`` takes over the session."""

    def __init__(self, root, on_ready=None):
        self.root = root
        self.on_ready = on_ready
        self.top = None
        self.session = None
        self.role = None
        self.tab = 'create'
        self.rooms = []
        self.joining = False
        self.scanning = False
        self.poll_id = None
        self.abandoned = False
        self.results = queue.SimpleQueue()
        self.handoff = threading.Lock()

        top = self.top = tk.Toplevel(root)
        top.title('局域网联机')
        top.configure(bg=BG)
        top.transient(root)
        top.resizable(False, False)
        top.protocol('WM_DELETE_WINDOW', self.close)
        top.bind('<Escape>', lambda event: self.close())

        shell = tk.Frame(top, bg=BG, padx=18, pady=16)
        shell.pack(fill='both', expand=True)
        heading = tk.Frame(shell, bg=BG)
        heading.pack(fill='x')
        tk.Label(heading, text='局域网联机', font=(FONT, 12, 'bold'), fg=TEXT, bg=BG).pack(side='left')
        tk.Label(heading, text='同一 Wi-Fi 下即可对打', font=(FONT, 8), fg=MUTED, bg=BG).pack(
            side='left', padx=(8, 0))

        self.tabs = {}
        tab_row = tk.Frame(shell, bg=BG)
        tab_row.pack(fill='x', pady=(12, 10))
        for key, text in (('create', '创建房间'), ('join', '加入房间')):
            pill = Pill(tab_row, text, lambda key=key: self.show_tab(key), 'soft', padx=14, pady=6)
            pill.pack(side='left', padx=(0, 8))
            self.tabs[key] = pill

        self.create_page = self.build_create_page(shell)
        self.join_page = self.build_join_page(shell)
        self.create_page.pack(fill='x')
        for name, pill in self.tabs.items():
            pill.set(kind='primary' if name == 'create' else 'soft')

        self.place_window()
        self.start_host()
        self.scan()  # prefill the join tab without making the player click first
        self.top.lift()
        self.poll_id = self.top.after(POLL_MS, self.poll)

    # -- widgets -----------------------------------------------------------
    def titled_row(self, parent, title):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill='x', pady=(2, 0))
        tk.Label(row, text=title, font=(FONT, 8), fg=MUTED, bg=BG, width=8, anchor='w').pack(side='left')
        return row

    def field(self, parent, variable, width):
        return tk.Entry(parent, textvariable=variable, width=width, font=(FONT, 10), bg=CARD, fg=TEXT,
                        insertbackground=TEXT, relief='flat', bd=0, highlightthickness=1,
                        highlightbackground='#24423c', highlightcolor=ACCENT)

    def build_create_page(self, parent):
        page = tk.Frame(parent, bg=BG)
        self.address_text = tk.StringVar(self.top)
        self.code_text = tk.StringVar(self.top, value='————')
        self.status_text = tk.StringVar(self.top)

        row = self.titled_row(page, '本机地址')
        tk.Label(row, textvariable=self.address_text, font=(FONT, 9), fg=TEXT, bg=BG, anchor='w',
                 justify='left', wraplength=FIELD).pack(side='left', fill='x', expand=True)
        row = self.titled_row(page, '房间码')
        tk.Label(row, textvariable=self.code_text, font=(NUMBERS, 22), fg=ACCENT, bg=BG).pack(
            side='left', padx=(0, 12))
        Pill(row, '换一个', self.new_code, 'ghost', padx=10, pady=5).pack(side='left')
        self.status_label = tk.Label(page, textvariable=self.status_text, font=(FONT, 9), fg=MUTED,
                                     bg=BG, anchor='w', justify='left', wraplength=FIELD)
        self.status_label.pack(fill='x', pady=(12, 0))

        buttons = tk.Frame(page, bg=BG)
        buttons.pack(fill='x', pady=(14, 0))
        Pill(buttons, '取消', self.close, 'soft', padx=16).pack(side='right')
        return page

    def build_join_page(self, parent):
        page = tk.Frame(parent, bg=BG)
        self.ip_text = tk.StringVar(self.top)
        self.join_code = tk.StringVar(self.top)
        self.join_status = tk.StringVar(self.top, value='点「扫描局域网」找房间，也可以直接填写主机地址。')

        row = self.titled_row(page, '主机地址')
        self.ip_entry = self.field(row, self.ip_text, 18)
        self.ip_entry.pack(side='left', ipady=3)
        row = self.titled_row(page, '房间码')
        self.code_entry = self.field(row, self.join_code, 6)
        self.code_entry.pack(side='left', ipady=3)
        self.code_entry.bind('<FocusOut>', lambda event: self.join_code.set(clean_code(self.join_code.get())))
        self.scan_pill = Pill(row, '扫描局域网', self.scan, 'ghost', padx=10, pady=5)
        self.scan_pill.pack(side='left', padx=(10, 0))

        self.rooms_frame = tk.Frame(page, bg=BG)
        self.rooms_frame.pack(fill='x', pady=(10, 0))
        self.render_rooms([])
        tk.Label(page, textvariable=self.join_status, font=(FONT, 8), fg=MUTED, bg=BG, anchor='w',
                 justify='left', wraplength=FIELD).pack(fill='x', pady=(10, 0))

        buttons = tk.Frame(page, bg=BG)
        buttons.pack(fill='x', pady=(14, 0))
        self.join_pill = Pill(buttons, '加入', self.request_join, 'primary', bold=True, padx=18)
        self.join_pill.pack(side='right')
        Pill(buttons, '取消', self.close, 'soft', padx=16).pack(side='right', padx=(0, 8))
        return page

    def render_rooms(self, rooms):
        for child in self.rooms_frame.winfo_children():
            child.destroy()
        if not rooms:
            tk.Label(self.rooms_frame, text='还没有发现房间', font=(FONT, 8), fg=MUTED, bg=BG,
                     anchor='w').pack(fill='x')
            return
        for entry in rooms[:ROOM_ROWS]:
            label = tk.Label(self.rooms_frame, text=describe_room(entry), font=(FONT, 9), fg=TEXT,
                             bg=CARD, anchor='w', padx=10, pady=6, cursor='hand2')
            label.pack(fill='x', pady=(0, 3))
            label.bind('<Enter>', lambda event, widget=label: widget.configure(bg=HOVER))
            label.bind('<Leave>', lambda event, widget=label: widget.configure(bg=CARD))
            label.bind('<ButtonRelease-1>', lambda event, item=entry: self.choose_room(item))

    def place_window(self):
        """Stay just below the toolbar, left-aligned with it, and fully on screen."""
        top = self.top
        if top is None:
            return
        top.update_idletasks()
        width, height = top.winfo_reqwidth(), top.winfo_reqheight()
        x = self.root.winfo_x()
        y = self.root.winfo_y() + self.root.winfo_height() + 10
        x = max(0, min(x, top.winfo_screenwidth() - width))
        y = max(0, min(y, top.winfo_screenheight() - height))
        top.geometry(f'+{x}+{y}')

    # -- tabs and rooms ----------------------------------------------------
    def show_tab(self, key):
        if self.joining or key == self.tab or not self.alive():
            return
        if key != 'create':
            self.stop_host()  # no hidden listening socket while the player browses the join tab
            self.code_text.set('————')
            self.status_text.set('')
            self.status_label.configure(fg=MUTED)
        self.tab = key
        for name, pill in self.tabs.items():
            pill.set(kind='primary' if name == key else 'soft')
        self.create_page.pack_forget()
        self.join_page.pack_forget()
        page = self.create_page if key == 'create' else self.join_page
        page.pack(fill='x')
        if key == 'create':
            self.start_host()
        elif not self.rooms and not self.scanning:
            self.scan()  # the first scan found nothing; retry when the join tab is opened
        self.place_window()

    def start_host(self, code=None):
        """Open a room now; a failure stays on the page as a warning instead of raising."""
        self.stop_host()
        try:
            transport = _netplay()
            code = code or transport.make_code()
            self.session = transport.start_host(code)
            self.address_text.set(format_addresses(transport.local_addresses()))
        except ImportError:
            self.fail_host('联机模块尚未就绪，请稍后再试。')
            return False
        except Exception as error:
            self.fail_host(f'无法创建房间：{error}')
            return False
        self.role = 'host'
        self.code_text.set(code)
        self.status_label.configure(fg=MUTED)
        self.status_text.set('等待客机接入…')
        return True

    def fail_host(self, message):
        self.stop_host()
        self.code_text.set('————')
        self.status_label.configure(fg=WARN)
        self.status_text.set(message)

    def new_code(self):
        if self.tab == 'create':
            self.start_host()

    def stop_host(self):
        """Close the room session, if any; idempotent and never raises."""
        session, self.session = self.session, None
        self.role = None
        if session is None:
            return
        try:
            session.close()
        except Exception:
            traceback.print_exc()

    def scan(self):
        """Discover rooms in the background; results arrive through ``self.results``."""
        if self.scanning or not self.alive():
            return
        self.scanning = True
        self.scan_pill.set('扫描中…', enabled=False)
        if self.tab == 'join':
            self.join_status.set('正在扫描局域网…')
        threading.Thread(target=self.scan_worker, name='Billiards-discover', daemon=True).start()

    def scan_worker(self):
        try:
            rooms = _netplay().discover()
        except Exception:
            rooms = []
        self.results.put(('rooms', rooms))

    def show_rooms(self, rooms):
        self.scanning = False
        if not self.alive():
            return
        self.scan_pill.set('扫描局域网', enabled=not self.joining)
        self.rooms = [entry for entry in rooms if isinstance(entry, dict)]
        host, code = pick_default(self.rooms)
        if host and not self.ip_text.get().strip():
            self.ip_text.set(host)
        if code and not self.join_code.get().strip():
            self.join_code.set(code)
        self.render_rooms(self.rooms)
        self.join_status.set(f'发现 {len(self.rooms)} 个房间，点一下就能填入。' if self.rooms
                             else '没找到房间，确认和主机在同一 Wi-Fi 下，或手动填写地址。')
        self.place_window()

    def choose_room(self, entry):
        host = str(entry.get('host') or '').strip()
        code = clean_code(entry.get('room') or '')
        if host:
            self.ip_text.set(host)
        if code:
            self.join_code.set(code)
        self.join_status.set(f'已填入 {describe_room(entry)}，点「加入」连接。')

    # -- joining -----------------------------------------------------------
    def request_join(self):
        if self.joining or not self.alive():
            return
        host = self.ip_text.get().strip()
        code = clean_code(self.join_code.get())
        self.join_code.set(code)
        if not host:
            messagebox.showwarning('加入房间', '请填写主机地址。', parent=self.top)
            self.ip_entry.focus_set()
            return
        if not code:
            messagebox.showwarning('加入房间', '请填写 4 位房间码。', parent=self.top)
            self.code_entry.focus_set()
            return
        self.joining = True
        self.join_pill.set('连接中…', enabled=False)
        self.scan_pill.set(enabled=False)
        self.join_status.set(f'正在连接 {host}…')
        threading.Thread(target=self.join_worker, args=(host, code), name='Billiards-join',
                         daemon=True).start()

    def join_worker(self, host, code):
        """Blocking handshake off the Tk thread; the Tk-thread poll calls back."""
        try:
            session = _netplay().join(host, code=code)
        except Exception as error:
            self.results.put(('join', None, error))
            return
        with self.handoff:
            # close() may run while the handshake is in flight: it takes the session instead.
            discard = self.abandoned
            if not discard:
                self.results.put(('join', session, None))
        if discard:
            try:
                session.close()
            except Exception:
                traceback.print_exc()

    def finish_join(self, session, error):
        self.joining = False
        if not self.alive():
            if session is not None:
                try:
                    session.close()
                except Exception:
                    traceback.print_exc()
            return
        self.join_pill.set('加入', kind='primary', enabled=True)
        self.scan_pill.set(enabled=True)
        if session is None:
            self.join_status.set('')
            message = str(error) if error else '无法连接房间，请确认主机地址与房间码。'
            messagebox.showerror('加入房间', message, parent=self.top)
            return
        if str(getattr(session, 'status', '') or '') != 'connected':
            try:
                session.close()
            except Exception:
                traceback.print_exc()
            self.join_status.set('')
            messagebox.showerror('加入房间', '握手没有完成，请重试。', parent=self.top)
            return
        self.finish(session, 'guest')

    # -- lifecycle ---------------------------------------------------------
    def finish(self, session, role):
        """Hand a connected session to the app and close without closing that session."""
        peer = str(getattr(session, 'peer_name', '') or '').strip() or '对手'
        self.session = None
        self.role = None
        self.destroy()
        if self.on_ready is None:
            try:
                session.close()
            except Exception:
                traceback.print_exc()
            return
        try:
            self.on_ready(session, role, peer)
        except Exception:
            # The app failed to adopt the session; never leave the socket dangling.
            try:
                session.close()
            except Exception:
                traceback.print_exc()
            raise

    def poll(self):
        self.poll_id = None
        if not self.alive():
            return
        try:
            self.drain()
            self.watch_session()
        except Exception:
            traceback.print_exc()
        if self.alive():
            try:
                self.poll_id = self.top.after(POLL_MS, self.poll)
            except tk.TclError:
                self.poll_id = None

    def drain(self):
        while True:
            try:
                item = self.results.get_nowait()
            except queue.Empty:
                return
            kind = item[0]
            if kind == 'rooms':
                self.show_rooms(item[1])
            elif kind == 'join':
                self.finish_join(item[1], item[2])

    def watch_session(self):
        session = self.session
        if session is None or self.role != 'host':
            return
        status = str(getattr(session, 'status', '') or '')
        if status == 'connected':
            self.finish(session, 'host')
        elif status in ('error', 'closed'):
            error = getattr(session, 'error', None)
            self.fail_host(f'房间已断开：{error}' if error else '房间已断开，请重新创建。')

    def close(self):
        """Cancel: close the room socket and the window. Safe to call twice."""
        if self.top is None:
            return
        with self.handoff:
            self.abandoned = True
            pending = []
            while True:
                try:
                    pending.append(self.results.get_nowait())
                except queue.Empty:
                    break
        for item in pending:
            if item[0] == 'join' and item[1] is not None:
                try:
                    item[1].close()
                except Exception:
                    traceback.print_exc()
        self.stop_host()
        self.destroy()

    def destroy(self):
        self.abandoned = True
        if self.poll_id is not None and self.top is not None:
            try:
                self.top.after_cancel(self.poll_id)
            except Exception:
                pass
            self.poll_id = None
        top, self.top = self.top, None
        if top is not None:
            try:
                top.destroy()
            except tk.TclError:
                pass

    def alive(self):
        top = self.top
        if top is None:
            return False
        try:
            return bool(top.winfo_exists())
        except tk.TclError:
            return False

    def lift(self):
        if self.alive():
            self.top.lift()
            try:
                self.top.focus_set()
            except tk.TclError:
                pass
