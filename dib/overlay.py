"""Per-pixel-alpha desktop child windows that render the interactive table.

The Explorer icon layer is replaced by one layered child window per desktop icon
plus table layers for the rail, white ball, cue, and charge indicator. Icon artwork is extracted
through the Shell, so the game uses the user's real thumbnails and labels.
"""
from __future__ import annotations

import ctypes
import math
import os
import platform
import struct
import threading
import time
from collections import OrderedDict
from ctypes import wintypes as W

import comtypes
import win32api
import win32con
import win32gui
import win32process
from PIL import Image, ImageDraw, ImageFont

from . import diag
from .config import CREDIT
from .art import charge_art, cue_art, guide_art, pocket_particles, table_art, white_art
from .balls import MAX_BALLS, ball_summary, fill_sources, priority_order
from .display import dpi_context, monitors
from .shell import ShellDesktop
from .table import MIN_SHOT_CHARGE, BilliardsMotion
from .wallpaper import primary_wallpaper

BALL_CANVAS = 128
INTRO_DURATION = 1.25
INTRO_STAGGER = 0.025
RING_LEVELS = 6
MAX_IMPACT = .23  # the largest ``impact`` value the table produces
GUIDE_INTERVAL = 1 / 30  # the aim guide is redrawn at most this often
LUMA_CELL = 8  # wallpaper pixels per cell of the brightness map
WS_EX_LAYERED = 0x80000
WS_EX_NOACTIVATE = 0x08000000
SM_SWAPBUTTON = 23


class ImageFactory(comtypes.IUnknown):
    _iid_ = comtypes.GUID('{bcc18b79-ba16-442f-80c4-8a59c30c463b}')
    _methods_ = []


def _unpremultiply(image):
    """Shell bitmaps use premultiplied BGRA; Pillow needs straight alpha."""
    data = bytearray(image.tobytes())
    for index in range(3, len(data), 4):
        alpha = data[index]
        if 0 < alpha < 255:
            scale = 255 / alpha
            data[index - 3] = min(255, round(data[index - 3] * scale))
            data[index - 2] = min(255, round(data[index - 2] * scale))
            data[index - 1] = min(255, round(data[index - 1] * scale))
    return Image.frombytes('RGBA', image.size, bytes(data))


def artwork(identity, size):
    """Extract the Shell thumbnail for one desktop item as straight-alpha RGBA."""
    factory = ctypes.POINTER(ImageFactory)()
    create = ctypes.windll.shell32.SHCreateItemFromParsingName
    create.argtypes = [W.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(comtypes.GUID),
                       ctypes.POINTER(ctypes.POINTER(ImageFactory))]
    hr = create(identity, None, ctypes.byref(ImageFactory._iid_), ctypes.byref(factory))
    if hr < 0:
        raise OSError(f'Cannot load Shell artwork: {hr & 0xffffffff:08x}')
    bitmap = W.HBITMAP()
    ShellDesktop._invoke(factory, 3, [W.SIZE, W.UINT, ctypes.POINTER(W.HBITMAP)],
                         W.SIZE(size, size), 0, ctypes.byref(bitmap))
    try:
        return _unpremultiply(_bitmap_image(bitmap.value))
    finally:
        win32gui.DeleteObject(bitmap.value)


def _bitmap_image(bitmap):
    """Copy an unselected Shell bitmap into a top-down, 32-bit BGRA buffer."""
    get_bits = ctypes.WinDLL('gdi32', use_last_error=True).GetDIBits
    get_bits.argtypes = [W.HDC, W.HBITMAP, W.UINT, W.UINT, ctypes.c_void_p, ctypes.c_void_p, W.UINT]
    get_bits.restype = ctypes.c_int
    header_format = '<IiiHHIIiiII'
    header = ctypes.create_string_buffer(struct.calcsize(header_format))
    struct.pack_into('<I', header, 0, ctypes.sizeof(header))
    dc = win32gui.CreateCompatibleDC(0)
    try:
        if not get_bits(dc, bitmap, 0, 0, None, header, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        _, width, height, *_ = struct.unpack(header_format, header)
        height = abs(height)
        if width <= 0 or height <= 0:
            raise ValueError('Invalid Shell bitmap dimensions')
        pixels = ctypes.create_string_buffer(width * height * 4)
        struct.pack_into(header_format, header, 0, ctypes.sizeof(header),
                         width, -height, 1, 32, 0, ctypes.sizeof(pixels), 0, 0, 0, 0)
        if get_bits(dc, bitmap, 0, height, pixels, header, 0) != height:
            raise ctypes.WinError(ctypes.get_last_error())
        return Image.frombytes('RGBA', (width, height), pixels.raw, 'raw', 'BGRA')
    finally:
        win32gui.DeleteDC(dc)


def desktop_listview():
    """Choose a full-size icon list owned by the desktop's Explorer process."""
    user32 = ctypes.windll.user32
    user32.GetShellWindow.argtypes = []
    user32.GetShellWindow.restype = W.HWND
    desktop = user32.GetShellWindow()
    if not desktop:
        raise RuntimeError('找不到 Windows 桌面窗口，请等待桌面加载完成后重试。')
    shell_pid = win32process.GetWindowThreadProcessId(desktop)[1]
    primary = next(m[2] for m in monitors() if m[4])
    found = []

    def covers_primary(hwnd):
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        x0, y0 = win32gui.ClientToScreen(hwnd, (left, top))
        x1, y1 = win32gui.ClientToScreen(hwnd, (right, bottom))
        return x0 <= primary[0] < primary[2] <= x1 and y0 <= primary[1] < primary[3] <= y1

    def visit(hwnd, _):
        if win32gui.GetClassName(hwnd) == 'SHELLDLL_DefView' and covers_primary(hwnd):
            child = 0
            while True:
                child = win32gui.FindWindowEx(hwnd, child, 'SysListView32', None)
                if not child:
                    break
                if covers_primary(child):
                    found.append((hwnd, child))
        return True

    def top(hwnd, _):
        # Explorer's FolderBrowser also contains DefView/ListView controls, but is
        # not a desktop. Do not select it just because it contains more items.
        if (win32gui.GetClassName(hwnd) in ('Progman', 'WorkerW')
                and win32process.GetWindowThreadProcessId(hwnd)[1] == shell_pid):
            win32gui.EnumChildWindows(hwnd, visit, None)
        return True

    with dpi_context(-4):
        win32gui.EnumWindows(top, None)
    if not found:
        raise RuntimeError('找不到覆盖主屏幕的 Explorer 桌面图标窗口，请确认 Windows 桌面已加载。')

    def score(pair):
        try:
            return bool(win32gui.IsWindowVisible(pair[1])), listview_count(pair[1])
        except win32gui.error:
            return False, 0

    chosen = max(found, key=score)
    diag.log(f'desktop selection: shell_pid={shell_pid} candidates={found} chosen={chosen}')
    return chosen


def listview_count(hwnd):
    """LVM_GETITEMCOUNT of the desktop list view."""
    return int(win32gui.SendMessage(hwnd, 0x1004, 0, 0))


def item_rects(hwnd, indexes):
    """Read the icon and label rectangles for list-view ``indexes``.

    An entry is ``None`` when the list view cannot report that item (index beyond its
    count, a changed desktop, or a third-party desktop manager); the caller skips it
    instead of abandoning the whole game.
    """
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    kernel.VirtualAllocEx.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_size_t, W.DWORD, W.DWORD]
    kernel.VirtualAllocEx.restype = ctypes.c_void_p
    kernel.WriteProcessMemory.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
    kernel.ReadProcessMemory.argtypes = kernel.WriteProcessMemory.argtypes
    kernel.VirtualFreeEx.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_size_t, W.DWORD]
    kernel.CloseHandle.argtypes = [W.HANDLE]
    pid = win32process.GetWindowThreadProcessId(hwnd)[1]

    def failure(operation):
        code = ctypes.get_last_error()
        diag.log(f'icon bounds: {operation} failed winerror={code} target_pid={pid} hwnd={hwnd} access=0x38')
        message = f'读取桌面图标位置失败（{operation}，Windows 错误 {code}）。'
        if code == 5:
            message += 'Windows 拒绝访问桌面进程，请确认游戏与桌面在同一登录会话运行，并检查安全软件的拦截记录。'
        return ctypes.WinError(code, message)

    process = kernel.OpenProcess(0x38, False, pid)
    if not process:
        raise failure('OpenProcess')
    pointer = kernel.VirtualAllocEx(process, None, ctypes.sizeof(W.RECT), 0x3000, 4)
    try:
        if not pointer:
            raise failure('VirtualAllocEx')
        count = listview_count(hwnd)
        result = []
        for index in indexes:
            row = [] if 0 <= index < count else None
            for kind in (1, 2):
                if row is None:
                    break
                rect = W.RECT(kind, 0, 0, 0)
                if not kernel.WriteProcessMemory(process, pointer, ctypes.byref(rect), ctypes.sizeof(rect), None):
                    raise failure('WriteProcessMemory')
                if not win32gui.SendMessage(hwnd, 0x100E, index, pointer):
                    row = None
                    break
                if not kernel.ReadProcessMemory(process, pointer, ctypes.byref(rect), ctypes.sizeof(rect), None):
                    raise failure('ReadProcessMemory')
                row.append((rect.left, rect.top, rect.right, rect.bottom))
            if row is not None and not (row[0][2] > row[0][0] and row[0][3] > row[0][1]):
                row = None  # an empty icon rectangle cannot be drawn
            result.append(row)
        return result
    finally:
        if pointer:
            kernel.VirtualFreeEx(process, pointer, 0, 0x8000)
        kernel.CloseHandle(process)


def map_rect(rect, source_origin, target_origin):
    dx = source_origin[0] - target_origin[0]
    dy = source_origin[1] - target_origin[1]
    return (rect[0] + dx, rect[1] + dy, rect[2] + dx, rect[3] + dy)


def _load_font(size):
    try:
        return ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', size)
    except OSError:
        return ImageFont.load_default(size)


class LayerSurface:
    """Reuse one top-down BGRA DIB and DC for each desktop child."""

    def __init__(self, hwnd, size, x, y):
        self.hwnd = hwnd
        self.size = size
        self.bitmap = None
        self.old = None
        self.last_frame = None
        self.dc = win32gui.CreateCompatibleDC(0)
        self.bits = ctypes.c_void_p()
        try:
            width, height = size
            header = ctypes.create_string_buffer(struct.pack('<IiiHHIIiiII', 40, width, -height, 1, 32, 0,
                                                             width * height * 4, 0, 0, 0, 0))
            create = ctypes.windll.gdi32.CreateDIBSection
            create.argtypes = [W.HDC, ctypes.c_void_p, W.UINT, ctypes.POINTER(ctypes.c_void_p), W.HANDLE, W.DWORD]
            create.restype = W.HBITMAP
            self.bitmap = create(self.dc, header, 0, ctypes.byref(self.bits), None, 0)
            if not self.bitmap:
                raise ctypes.WinError()
            self.old = win32gui.SelectObject(self.dc, self.bitmap)
            self.point = (x, y)
        except Exception:
            self.close()
            raise

    def present(self, frame, x=None, y=None):
        if x is not None:
            # Layered child windows take coordinates relative to their parent.
            self.point = (x, y)
        if frame is not self.last_frame:
            data = frame.convert('RGBa').tobytes('raw', 'BGRa')
            ctypes.memmove(self.bits, data, len(data))
            self.last_frame = frame
        win32gui.UpdateLayeredWindow(self.hwnd, 0, self.point, self.size, self.dc, (0, 0), 0, (0, 0, 255, 1), 2)

    def close(self):
        if self.old:
            win32gui.SelectObject(self.dc, self.old)
            self.old = None
        if self.bitmap:
            win32gui.DeleteObject(self.bitmap)
            self.bitmap = None
        if self.dc:
            win32gui.DeleteDC(self.dc)
            self.dc = None


class TablePointer:
    """Turn the held left mouse button into aim, charge and a shot."""

    def __init__(self):
        self.was_down = True
        self.held_since = None
        self.charge_origin = None

    def update(self, game, now, x, y, down, over_table):
        if over_table:
            game.aim_at(x, y)
        if down and not self.was_down and over_table:
            if game.state == 'ball_in_hand':
                game.place_white(x, y)
            elif game.state == 'ready':
                self.held_since = now
                self.charge_origin = (x, y)
        if self.held_since is not None:
            power = min(1, (now - self.held_since) / 0.9)
            game.charge = power
            if not down:
                self.held_since = None
                self.charge_origin = None
                if over_table:
                    game.shoot(max(MIN_SHOT_CHARGE, power), now)
                else:
                    game.charge = 0.0
        self.was_down = down


class BilliardsOverlay:
    """Own the layered windows and the printable-level state of the table."""

    def __init__(self, desktop, items):
        self.desktop = desktop
        # Own list: the chosen icons and the padding clones are collected here and must never
        # reach the layout snapshot that the desktop restore reads (``items`` is that list).
        self.items = []
        self.desktop_total = len(items)
        self.real_count = 0
        self.padded = 0
        self.skipped = 0
        self.ball_info = ''
        self.blocked_by = None
        self.last_blocked = None
        self.luma_map = None
        # Set from the UI thread, applied by the animation thread inside draw().
        self.want_hidden = False
        self.hidden = False
        self.want_felt = True
        self.felt_visible = True
        self.table = None
        self.windows = []
        self.surfaces = {}
        self.sources = []
        self.templates = []
        self.anchors = []
        self.drawn = []
        self.poses = []
        self.transition = None
        self.restart_requested = threading.Event()
        self.pointer = TablePointer()
        self.table_windows = {}
        self.guide_enabled = True
        self.guide_key = None
        self.guide_drawn = 0.0
        self.cue_key = None
        self.charge_key = None
        self.white_frame = None
        self.white_position = None
        self.parent, self.listview = desktop_listview()
        self.geometry = desktop.get_geometry()
        self.monitor_state = monitors()
        self.monitor_checked = 0.0
        self.fonts = []
        list_origin = win32gui.ClientToScreen(self.listview, (0, 0))
        parent_origin = win32gui.ClientToScreen(self.parent, (0, 0))
        self.log_environment(list_origin, parent_origin)
        chosen = self.choose_icons(items)
        self.real_count = len(chosen)
        self.items = [items[index] for index, _ in chosen]
        self.rects = [[map_rect(rect, list_origin, parent_origin) for rect in pair] for _, pair in chosen]
        primary = next(m for m in self.monitor_state if m[4])
        self.primary_screen = primary[1]
        self.primary_work = primary[2]
        self.primary_bounds = map_rect(primary[2], (0, 0), parent_origin)
        self.charge_font = _load_font(max(12, round(14 * primary[3] / 96)))
        left, top, right, bottom = win32gui.GetClientRect(self.parent)
        x0, y0, x1, y1 = self.primary_bounds
        if not (left <= x0 < x1 <= right and top <= y0 < y1 <= bottom):
            raise RuntimeError('桌面绘制窗口未覆盖主屏幕，已保留原桌面图标。')
        self.empty_frame = Image.new('RGBA', (1, 1))
        for icon, _ in self.rects:
            center = (round((icon[0] + icon[2]) / 2), round((icon[1] + icon[3]) / 2))
            screen = win32gui.ClientToScreen(self.parent, center)
            monitor = win32api.MonitorFromPoint(screen, win32con.MONITOR_DEFAULTTONEAREST)
            info = win32api.GetMonitorInfo(monitor)
            dpi = next(m[3] for m in self.monitor_state if m[0] == info['Device'])
            self.fonts.append(_load_font(max(9, round(12 * dpi / 96))))
        # Sparse desktops are padded to the minimum ball count by cloning icons; the clones
        # are overlay-only and start their intro flight from the icon they copy.
        real = list(self.items)
        extra = fill_sources(len(real))
        for source in extra:
            self.items.append(dict(real[source]))
            self.rects.append(self.rects[source])
            self.fonts.append(self.fonts[source])
        self.padded = len(extra)
        self.ball_info = ball_summary(self.desktop_total, self.real_count, self.padded)
        diag.log(f'balls: {self.ball_info}', f'primary work area (parent coords): {self.primary_bounds}')
        self.was_visible = win32gui.IsWindowVisible(self.listview)
        if not self.was_visible:
            raise RuntimeError('桌面图标当前已隐藏，请先在桌面菜单中显示图标。')
        self.font = _load_font(max(12, round(12 * ctypes.windll.user32.GetDpiForWindow(self.listview) / 96)))
        self.classname = 'DesktopIconBilliardsOverlay'
        window_class = win32gui.WNDCLASS()
        window_class.hInstance = win32api.GetModuleHandle(None)
        window_class.lpszClassName = self.classname
        window_class.lpfnWndProc = win32gui.DefWindowProc
        try:
            win32gui.RegisterClass(window_class)
        except win32gui.error as exc:
            if exc.winerror != 1410:
                raise

    def choose_icons(self, items):
        """Pick up to MAX_BALLS desktop icons by priority; skip ones the list view cannot report.

        Returns ``[(shell_index, (icon_rect, label_rect)), ...]`` in desktop order.
        """
        order = priority_order(items)
        chosen, skipped, position = [], [], 0
        while len(chosen) < MAX_BALLS and position < len(order):
            batch = order[position:position + MAX_BALLS - len(chosen)]
            position += len(batch)
            for index, pair in zip(batch, item_rects(self.listview, batch)):
                if pair is None:
                    skipped.append(index)
                else:
                    chosen.append((index, pair))
        self.skipped = len(skipped)
        diag.log(f'icons: shell={len(items)} listview={listview_count(self.listview)} '
                 f'chosen={len(chosen)} unreadable={skipped[:20]}')
        if not chosen:
            raise RuntimeError('无法读取桌面图标的位置。请确认桌面图标已显示，并且没有被桌面整理软件接管。')
        chosen.sort(key=lambda row: row[0])
        return chosen

    def log_environment(self, list_origin, parent_origin):
        """Record the display and Explorer facts that multi-monitor bugs depend on."""
        try:
            user32 = ctypes.windll.user32
            user32.GetWindowDpiAwarenessContext.argtypes = [W.HWND]
            user32.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
            user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            user32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int

            def describe(name, hwnd, origin):
                awareness = user32.GetAwarenessFromDpiAwarenessContext(user32.GetWindowDpiAwarenessContext(hwnd))
                return (f'{name}: hwnd={hwnd} class={win32gui.GetClassName(hwnd)} visible={win32gui.IsWindowVisible(hwnd)} '
                        f'client={win32gui.GetClientRect(hwnd)} window={win32gui.GetWindowRect(hwnd)} '
                        f'screen-origin={origin} dpi-awareness(0=unaware,1=system,2=per-monitor)={awareness}')

            lines = [f'windows: {platform.platform()}',
                     f'virtual screen / monitors: {self.geometry}']
            lines += [f'monitor device={m[0]} rect={m[1]} work={m[2]} dpi={m[3]} primary={m[4]}'
                      for m in self.monitor_state]
            lines += [describe('desktop parent', self.parent, parent_origin),
                      describe('desktop listview', self.listview, list_origin),
                      f'swapped mouse buttons: {bool(win32api.GetSystemMetrics(SM_SWAPBUTTON))}']
            diag.log(*lines)
        except Exception as exc:  # diagnostics only
            diag.log(f'environment logging failed: {exc!r}')

    # -- preparation -------------------------------------------------------
    def prepare(self):
        # Run in a worker: thumbnail extraction can access disk / Shell extensions.
        comtypes.CoInitialize()
        try:
            extracted = {}
            for item, (icon, label) in zip(self.items, self.rects):
                size = min(icon[2] - icon[0], icon[3] - icon[1])
                key = (item['id'], size)
                if key not in extracted:  # padding balls reuse the artwork of the icon they copy
                    source = artwork(item['id'], size)
                    source.thumbnail((size, size), Image.Resampling.LANCZOS)
                    extracted[key] = source
                self.sources.append(extracted[key].copy())
            self.build_templates()
            screens = [monitor[1] for monitor in self.monitor_state]
            desktop_bounds = (min(rect[0] for rect in screens), min(rect[1] for rect in screens),
                              max(rect[2] for rect in screens), max(rect[3] for rect in screens))
            wallpaper = primary_wallpaper(self.primary_screen, desktop_bounds)
            if wallpaper is not None:
                wallpaper = wallpaper.crop(map_rect(self.primary_work, (0, 0), self.primary_screen[:2]))
                # Coarse brightness map: the aim guide picks a palette that contrasts with it.
                self.luma_map = wallpaper.convert('L').resize(
                    (max(1, wallpaper.width // LUMA_CELL), max(1, wallpaper.height // LUMA_CELL)),
                    Image.Resampling.BOX)
            self.rail_frame = table_art(self.table, wallpaper, CREDIT)
        finally:
            comtypes.CoUninitialize()

    def build_templates(self):
        """Build label canvases and the resting pose each icon starts from."""
        self.frame_cache = OrderedDict()
        self.frame_cache_bytes = 0
        for index, (source, (icon, label)) in enumerate(zip(self.sources, self.rects)):
            left = min(icon[0], label[0]) - 40
            top = icon[1] - 48
            frame = Image.new('RGBA', (max(icon[2], label[2]) - left + 40, max(icon[3], label[3]) - top + 12))
            font = self.fonts[index] if index < len(self.fonts) else self.font
            text = self.items[index]['name']
            draw = ImageDraw.Draw(frame)
            max_width = max(20, label[2] - label[0])
            lines = []
            line = ''
            for char in text:
                if draw.textlength(line + char, font=font) > max_width and line:
                    lines.append(line)
                    line = ''
                line += char
            if line:
                lines.append(line)
            line_count = max(1, min(2, (label[3] - label[1]) // max(1, font.size + 2)))
            for line_index, line in enumerate(lines[:line_count]):
                if line_index == line_count - 1 and len(lines) > line_count:
                    while line and draw.textlength(line + '…', font=font) > max_width:
                        line = line[:-1]
                    line += '…'
                tx = (label[0] + label[2]) / 2 - left
                ty = label[1] - top + line_index * (font.size + 2)
                draw.text((tx + 1, ty + 1), line, font=font, anchor='mt', fill=(0, 0, 0, 210), stroke_width=1)
                draw.text((tx, ty), line, font=font, anchor='mt', fill='white')
            bounds = source.getchannel('A').getbbox()
            if not bounds:
                raise RuntimeError('系统返回了空图标')
            center = (icon[0] + icon[2]) / 2 - left - source.width / 2 + (bounds[0] + bounds[2]) / 2
            bottom = icon[3] - top - source.height + bounds[3]
            self.sources[index] = source.crop(bounds)
            self.templates.append(frame)
            self.anchors.append((left, top, center, bottom))
            self.drawn.append(None)
        self.poses = []
        for source, (left, top, center, bottom) in zip(self.sources, self.anchors):
            # Resting pose at the real desktop position, before the rack is built.
            self.poses.append((left + center, top + bottom - source.height / 2,
                               source.width, source.height, 0, 0, 1.0))
        self.table = BilliardsMotion(self.primary_bounds, [source.size for source in self.sources])
        # All icons share the collision radius. Prepare the six immutable layers
        # once so simultaneous impacts never resize the same artwork per ball.
        self.ring_frames = {level: self.ring_layer(level) for level in range(1, RING_LEVELS + 1)}

    # -- lifecycle ---------------------------------------------------------
    def show(self, snapshot, journal):
        from .shell import atomic_json

        try:
            for _ in self.items:
                hwnd = win32gui.CreateWindowEx(
                    WS_EX_LAYERED | 0x20 | WS_EX_NOACTIVATE, self.classname, '', win32con.WS_CHILD,
                    0, 0, 1, 1, self.parent, 0, win32api.GetModuleHandle(None), None)
                self.windows.append(hwnd)
            self.transition = {'elapsed': 0.0, 'origins': [list(pose) for pose in self.poses]}
            self.draw(0.0, 0.016)
            snapshot['hidden_listview'] = self.listview
            atomic_json(journal, snapshot)
            win32gui.ShowWindow(self.listview, win32con.SW_HIDE)
            for hwnd in self.windows:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
            self.stack_table_windows()
        except Exception:
            self.close()
            raise

    def request_restart(self):
        self.restart_requested.set()

    def set_hidden(self, hidden, now):
        """Hide every game window and show the real icons again (or the reverse), pausing the round."""
        shown = win32con.SW_HIDE if hidden else win32con.SW_SHOWNOACTIVATE
        if hidden:
            for hwnd in [*self.windows, *self.table_windows.values()]:
                win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
            win32gui.ShowWindow(self.listview, win32con.SW_SHOWNA)
            self.table.pause(now)
        else:
            win32gui.ShowWindow(self.listview, win32con.SW_HIDE)
            for hwnd in self.windows:
                win32gui.ShowWindow(hwnd, shown)
            rail = self.table_windows.get('rail')
            if rail and self.want_felt:
                win32gui.ShowWindow(rail, shown)
            # Forget what each layer last showed so the next frames re-show cue, guide and white ball.
            self.cue_key = self.guide_key = self.charge_key = self.white_position = None
            self.drawn = [None] * len(self.items)
            self.pointer = TablePointer()
            self.stack_table_windows()
            self.table.resume(now)
        self.hidden = hidden
        diag.log(f'game {"hidden" if hidden else "shown again"}')

    def set_felt(self, visible):
        """Show or hide the table frame, pockets and wallpaper panel; the balls keep rolling on the desktop."""
        rail = self.table_windows.get('rail')
        if rail is None:
            return
        if visible:
            win32gui.ShowWindow(rail, win32con.SW_SHOWNOACTIVATE)
            self.position_layer(rail, win32con.HWND_BOTTOM)
        else:
            win32gui.ShowWindow(rail, win32con.SW_HIDE)
        self.felt_visible = visible

    def draw(self, now, dt):
        if self.desktop.get_geometry() != self.geometry:
            raise RuntimeError('显示器布局变化，已停止台球并恢复桌面')
        if now - self.monitor_checked >= 0.5:
            self.monitor_checked = now
            if monitors() != self.monitor_state:
                raise RuntimeError('屏幕布局或 DPI 缩放变化，已恢复桌面，请重新开始。')
        if self.transition is None and self.want_hidden != self.hidden:
            self.set_hidden(self.want_hidden, now)
        if self.hidden:
            return  # the real desktop is showing: draw and simulate nothing
        if self.transition is None and self.want_felt != self.felt_visible:
            self.set_felt(self.want_felt)
        if self.transition is not None:
            self.draw_intro(now, dt)
        else:
            if self.restart_requested.is_set():
                self.restart_requested.clear()
                self.table.reset()
                self.pointer = TablePointer()
                self.drawn = [None] * len(self.items)
            self.update_pointer(now)
            self.table.step(dt, now)
            self.draw_balls()
        self.draw_table()
        self.draw_guide()
        self.draw_charge()

    def update_pointer(self, now):
        screen = win32gui.GetCursorPos()
        x, y = win32gui.ScreenToClient(self.parent, screen)
        left, top, right, bottom = self.table.bounds
        on_desktop = False
        blocked = None
        if left <= x <= right and top <= y <= bottom:
            hwnd = win32gui.WindowFromPoint(screen)
            top_window = win32gui.GetAncestor(hwnd, win32con.GA_ROOT) if hwnd else 0
            if top_window:
                name = win32gui.GetClassName(top_window)
                on_desktop = name in ('Progman', 'WorkerW')
                if not on_desktop and win32process.GetWindowThreadProcessId(top_window)[1] != os.getpid():
                    title = win32gui.GetWindowText(top_window)[:30]
                    blocked = (title or name, name)  # the player sees the title, the log keeps the class
        self.blocked_by = blocked[0] if blocked else None
        if blocked != self.last_blocked:
            self.last_blocked = blocked
            if blocked:
                diag.log(f'mouse over the table is blocked by another window: {blocked[0]} (class {blocked[1]})')
        # GetAsyncKeyState reports the physical buttons; with swapped buttons the player's
        # primary (logical left) button is the physical right one.
        button = win32con.VK_RBUTTON if win32api.GetSystemMetrics(SM_SWAPBUTTON) else win32con.VK_LBUTTON
        down = bool(win32api.GetAsyncKeyState(button) & 0x8000)
        self.pointer.update(self.table, now, x, y, down, on_desktop)

    # -- drawing -----------------------------------------------------------
    def draw_intro(self, now, dt):
        """Fly the real icons from their desktop positions into the rack."""
        transition = self.transition
        transition['elapsed'] += max(0, min(0.05, dt))
        for index, (hwnd, origin) in enumerate(zip(self.windows, transition['origins'])):
            target = self.table.poses[index]
            t = max(0, min(1, (transition['elapsed'] - (index % 7) * INTRO_STAGGER) / INTRO_DURATION))
            ease = t * t * t * (10 + t * (-15 + 6 * t))
            values = [start + (end - start) * ease for start, end in zip(origin, target)]
            dx = target[0] - origin[0]
            dy = target[1] - origin[1]
            distance = max(1, math.hypot(dx, dy))
            arc = math.sin(math.pi * t) ** 2 * min(65, distance * 0.10) * (1 if index % 2 else -1)
            values[0] -= dy / distance * arc
            values[1] += dx / distance * arc
            self.poses[index] = tuple(values)
            cx, cy, width, height, angle, _shift, label = values
            width = max(1, round(width))
            height = max(1, round(height))
            template = self.templates[index]
            source = self.sources[index]
            left, top, center, bottom = self.anchors[index]
            sprite_extent = math.ceil(math.hypot(width, height)) + 4
            frame = Image.new('RGBA', (max(math.ceil(template.width * 2), sprite_extent),
                                       max(math.ceil(template.height * 2), sprite_extent)))
            mx, my = frame.width // 2, frame.height // 2
            if label > 0:
                labels = template.copy()
                labels.putalpha(labels.getchannel('A').point(lambda value: round(value * label)))
                frame.alpha_composite(labels, (round(mx - center), round(my - bottom + height / 2)))
            picture = source.resize((width, height), Image.Resampling.BICUBIC)
            picture = picture.rotate(angle, Image.Resampling.BICUBIC, expand=True)
            frame.alpha_composite(picture, (round(mx - picture.width / 2), round(my - picture.height / 2)))
            self.present(hwnd, frame, round(cx - mx), round(cy - my))
        if transition['elapsed'] >= INTRO_DURATION + 6 * INTRO_STAGGER:
            self.transition = None
            self.drawn = [None] * len(self.items)

    def draw_balls(self):
        for index, (hwnd, values) in enumerate(zip(self.windows, self.table.poses)):
            self.poses[index] = values
            cx, cy, width, height, angle, _shift, _label = values
            angle = round(angle)
            alpha = 0 if index + 1 in self.table.sunk else 255
            effect = self.table.exit_visual(index + 1) if not alpha else None
            fade = 0.0
            if effect:
                cx, cy, fade, scale, spin = effect
                width = max(1, round(width * scale))
                height = max(1, round(height * scale))
                angle = round(angle + spin)
                alpha = round(255 * (1 - fade) ** 1.35)
                self.poses[index] = (cx, cy, width, height, angle, 0, 0.0)
            # A faint ring flashes at the collision radius while a ball is being hit.
            impact = self.table.icons[index].impact
            ring = min(RING_LEVELS, math.ceil(impact / MAX_IMPACT * RING_LEVELS)) if alpha == 255 and impact > .012 else 0
            key = ((round(cx), round(cy), width, height, angle, alpha, round(fade * 40), ring)
                   if alpha or effect else (0,))
            if self.drawn[index] == key:
                continue
            frame = self.sprite_frame(self.sources[index], index, width, height, angle, alpha, ring)
            if fade:
                frame = pocket_particles(frame, index, fade)
            self.present(hwnd, frame, round(cx - BALL_CANVAS / 2), round(cy - BALL_CANVAS / 2))
            self.drawn[index] = key

    def sprite_frame(self, source, identity, width, height, angle, alpha=255, ring=0):
        key = (identity, width, height, angle, BALL_CANVAS, alpha, ring)
        if key in self.frame_cache:
            self.frame_cache.move_to_end(key)
            return self.frame_cache[key]
        frame = Image.new('RGBA', (BALL_CANVAS, BALL_CANVAS))
        if alpha:
            picture = source.resize((width, height), Image.Resampling.BICUBIC)
            picture = picture.rotate(angle, Image.Resampling.BICUBIC, expand=True)
            if alpha < 255:
                picture.putalpha(picture.getchannel('A').point([value * alpha // 255 for value in range(256)]))
            frame.alpha_composite(picture, ((BALL_CANVAS - picture.width) // 2,
                                            (BALL_CANVAS - picture.height) // 2))
            if ring:
                frame.alpha_composite(self.ring_frames[ring])
        self.frame_cache[key] = frame
        self.frame_cache_bytes += BALL_CANVAS * BALL_CANVAS * 4
        while len(self.frame_cache) > 256 or self.frame_cache_bytes > 16 * 1024 * 1024:
            _, old = self.frame_cache.popitem(last=False)
            self.frame_cache_bytes -= old.width * old.height * 4
        return frame

    def ring_layer(self, level):
        """The collision circle of one ball, drawn very faintly (``level`` 1..RING_LEVELS)."""
        scale = 3
        layer = Image.new('RGBA', (BALL_CANVAS * scale, BALL_CANVAS * scale))
        radius = self.table.radius * scale
        centre = BALL_CANVAS * scale / 2
        box = (centre - radius, centre - radius, centre + radius, centre + radius)
        alpha = round(28 + 12 * level)
        draw = ImageDraw.Draw(layer)
        draw.ellipse(box, fill=(190, 255, 232, alpha // 5), outline=(190, 255, 232, alpha), width=2 * scale)
        return layer.resize((BALL_CANVAS, BALL_CANVAS), Image.Resampling.LANCZOS)

    def table_window(self, part):
        hwnd = win32gui.CreateWindowEx(
            WS_EX_LAYERED | 0x20 | WS_EX_NOACTIVATE, self.classname, '', win32con.WS_CHILD,
            0, 0, 1, 1, self.parent, 0, win32api.GetModuleHandle(None), None)
        self.table_windows[part] = hwnd
        return hwnd

    @staticmethod
    def position_layer(hwnd, after, flags=0):
        win32gui.SetWindowPos(hwnd, after, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE | flags)

    def stack_table_windows(self):
        """Keep the wallpaper behind the icons and the shot controls above them."""
        self.position_layer(self.table_windows['guide'], win32con.HWND_BOTTOM)
        self.position_layer(self.table_windows['rail'], win32con.HWND_BOTTOM)
        for part in ('white', 'cue', 'charge'):
            if part in self.table_windows:
                self.position_layer(self.table_windows[part], win32con.HWND_TOP)

    def draw_table(self):
        table = self.table
        if 'rail' not in self.table_windows:
            hwnd = self.table_window('rail')
            self.present(hwnd, *self.rail_frame)
            self.rail_frame = None
            self.position_layer(hwnd, win32con.HWND_BOTTOM, win32con.SWP_SHOWWINDOW)
            self.table_window('guide')
        white = table.white
        if 'white' not in self.table_windows:
            self.table_window('white')
            self.white_frame = white_art(white.radius, shaded=True)
            self.white_position = None
        hwnd = self.table_windows['white']
        if table.white_sunk:
            if self.white_position is not None:
                win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
            self.white_position = None
        else:
            position = (round(white.x - self.white_frame.width / 2), round(white.y - self.white_frame.height / 2))
            if self.white_position != position:
                self.present(hwnd, self.white_frame, *position)
                if self.white_position is None:
                    self.position_layer(hwnd, win32con.HWND_TOP,
                                        win32con.SWP_SHOWWINDOW | win32con.SWP_NOZORDER)
                self.white_position = position
        cue = table.cue()
        if cue is None:
            if self.cue_key is not None:
                win32gui.ShowWindow(self.table_windows['cue'], win32con.SW_HIDE)
                self.cue_key = None
            return
        key = tuple(round(value) for point in cue for value in point)
        if key == self.cue_key:
            return
        hwnd = self.table_windows.get('cue') or self.table_window('cue')
        self.present(hwnd, *cue_art(cue, thickness=1.65))
        if self.cue_key is None:
            self.position_layer(hwnd, win32con.HWND_TOP,
                                win32con.SWP_SHOWWINDOW | win32con.SWP_NOZORDER)
        self.cue_key = key

    def background_luma(self, x, y):
        """Wallpaper brightness (0-255) under a table point, or None without a readable wallpaper."""
        if self.luma_map is None:
            return None
        origin_x, origin_y = self.table.work_area[:2]
        px, py = int((x - origin_x) // LUMA_CELL), int((y - origin_y) // LUMA_CELL)
        width, height = self.luma_map.size
        return self.luma_map.getpixel((px, py)) if 0 <= px < width and 0 <= py < height else None

    def draw_guide(self):
        lines, circles = self.table.aim_preview() if self.guide_enabled and self.transition is None else ((), ())
        rounded = [(role, (round(x0), round(y0)), (round(x1), round(y1)))
                   for role, (x0, y0), (x1, y1) in lines]
        lines_key = tuple((role, start, end) for role, start, end in rounded if start != end)
        key = (lines_key, tuple((role, (round(x), round(y)), round(radius)) for role, (x, y), radius in circles))             if lines_key else None
        if key == self.guide_key:
            return
        clock = time.perf_counter()
        if key is not None and clock - self.guide_drawn < GUIDE_INTERVAL:
            return  # redrawing the guide is the costliest step: cap it, the next frame catches up
        hwnd = self.table_windows['guide']
        if key is None:
            win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
        else:
            self.present(hwnd, *guide_art(key[0], key[1], self.background_luma))
            self.guide_drawn = clock
            if self.guide_key is None:
                self.position_layer(hwnd, win32con.HWND_TOP, win32con.SWP_SHOWWINDOW | win32con.SWP_NOZORDER)
        self.guide_key = key

    def draw_charge(self):
        origin = self.pointer.charge_origin
        if origin is None or self.table.state != 'ready':
            if self.charge_key is not None:
                win32gui.ShowWindow(self.table_windows['charge'], win32con.SW_HIDE)
                self.charge_key = None
            return

        percent = round(self.table.charge * 100)
        key = (origin, percent)
        if key == self.charge_key:
            return
        frame = charge_art(percent, self.charge_font)
        width, height = frame.size

        left, top, right, bottom = self.primary_bounds
        x, y = origin
        gap = self.charge_font.size
        x = x + gap if x + gap + width <= right else x - gap - width
        y = y + gap if y + gap + height <= bottom else y - gap - height
        hwnd = self.table_windows.get('charge') or self.table_window('charge')
        self.present(hwnd, frame, max(left, x), max(top, y))
        if self.charge_key is None:
            self.position_layer(hwnd, win32con.HWND_TOP, win32con.SWP_SHOWWINDOW)
        self.charge_key = key

    def clear_table(self):
        for hwnd in self.table_windows.values():
            surface = self.surfaces.pop(hwnd, None)
            if surface:
                surface.close()
            if win32gui.IsWindow(hwnd):
                win32gui.DestroyWindow(hwnd)
        self.table_windows.clear()
        self.guide_key = None
        self.guide_drawn = 0.0
        self.cue_key = None
        self.charge_key = None
        self.white_position = None

    def present(self, hwnd, frame, x, y):
        left, top, right, bottom = self.primary_bounds
        x0, y0 = max(left, x), max(top, y)
        x1, y1 = min(right, x + frame.width), min(bottom, y + frame.height)
        if x0 >= x1 or y0 >= y1:
            frame, x, y = self.empty_frame, left, top
        elif (x0, y0, x1, y1) != (x, y, x + frame.width, y + frame.height):
            frame = frame.crop((x0 - x, y0 - y, x1 - x, y1 - y))
            x, y = x0, y0
        if hwnd in self.surfaces and self.surfaces[hwnd].size != frame.size:
            self.surfaces.pop(hwnd).close()
        if hwnd not in self.surfaces:
            self.surfaces[hwnd] = LayerSurface(hwnd, frame.size, x, y)
        self.surfaces[hwnd].present(frame, x, y)

    def close(self):
        if self.was_visible and win32gui.IsWindow(self.listview):
            win32gui.ShowWindow(self.listview, win32con.SW_SHOWNA)
        self.clear_table()
        for surface in self.surfaces.values():
            surface.close()
        self.surfaces = {}
        for hwnd in self.windows:
            if win32gui.IsWindow(hwnd):
                win32gui.DestroyWindow(hwnd)
        self.windows = []
        if hasattr(self, 'frame_cache'):
            self.frame_cache.clear()
            self.frame_cache_bytes = 0
        if hasattr(self, 'ring_frames'):
            self.ring_frames.clear()
