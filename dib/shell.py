"""Documented Shell IFolderView position API, with a persistent recovery journal.

All icon movement goes through the documented Shell COM interfaces; there is no
Explorer injection, ListView message patching, screenshot replica or image
scaling.
"""
from __future__ import annotations

import ctypes
import json
import os
import time
import uuid
from pathlib import Path

# FWF_AUTOARRANGE | FWF_SNAPTOGRID: the two view flags that snap icons to a grid.
ARRANGE_MASK = 0x1 | 0x4


def _find_desktop(lookup):
    """Explorer may not have registered its desktop view yet during startup."""
    import win32gui

    for attempt in range(5):
        hwnd, dispatch = lookup()
        if hwnd and dispatch is not None and win32gui.IsWindow(hwnd):
            return hwnd, dispatch
        if attempt < 4:
            time.sleep(.15)
    raise RuntimeError('暂时无法连接 Windows 桌面。请等待桌面加载完成后重试；'
                       '如果使用了桌面替代软件，请先恢复 Windows 桌面。')


class PendingRecovery(RuntimeError):
    """A desktop layout snapshot is still active and must be recovered first."""


def atomic_json(path, data):
    """Write JSON atomically so a crash never leaves a partial journal."""
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    with tmp.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)


class LayoutSession:
    """Journal an icon layout, apply bounded offsets and restore exact positions."""

    def __init__(self, backend, journal):
        self.backend = backend
        self.journal = Path(journal)
        self.snapshot = None
        self.baseline = {}
        self.last = {}

    def begin(self, overlay_only=False):
        """Persist the layout, then disable snapping unless this is an overlay run."""
        if self.journal.exists():
            if json.loads(self.journal.read_text(encoding='utf-8')).get('active'):
                raise PendingRecovery('有未完成的桌面布局恢复记录，请先点“恢复原位置”。')
        items = self.backend.items()
        if not items:
            raise RuntimeError('没有找到真实桌面图标；请确认Explorer桌面已启用。')
        self.snapshot = {
            'overlay_only': overlay_only,
            'version': 2,
            'active': True,
            'owner_pid': os.getpid(),
            'session_id': uuid.uuid4().hex,
            'created': time.time(),
            'flags': self.backend.get_flags(),
            'geometry': list(self.backend.get_geometry()),
            'items': items,
        }
        self.baseline = {item['id']: (int(item['x']), int(item['y'])) for item in items}
        self.last = dict(self.baseline)
        atomic_json(self.journal, self.snapshot)
        if overlay_only:
            return items
        try:
            self.backend.set_flags(ARRANGE_MASK, 0)
            if self.backend.get_flags() & ARRANGE_MASK:
                raise RuntimeError('无法暂时关闭图标自动排列/网格对齐。')
        except Exception:
            self.restore()
            raise
        return items

    def apply(self, offsets):
        """Move icons by small offsets relative to the journaled baseline."""
        if not self.snapshot or not self.snapshot.get('active'):
            raise RuntimeError('No active layout session')
        if list(self.backend.get_geometry()) != self.snapshot['geometry']:
            raise PendingRecovery('显示器布局或分辨率已变化；已暂停，布局快照保留。')
        positions = {}
        for identity, (dx, dy) in offsets.items():
            if identity not in self.baseline:
                continue
            # Stay inside the displacement range validated during development.
            if not (-4 <= dx <= 4 and -4 <= dy <= 18):
                raise ValueError('Icon displacement outside safe test bounds')
            x, y = self.baseline[identity]
            point = (x + round(dx), y + round(dy))
            if y >= 0:
                point = (point[0], max(0, point[1]))
            if self.last.get(identity) != point:
                positions[identity] = point
        if positions:
            self.backend.move(positions)
            self.last.update(positions)
        return len(positions)

    def restore(self):
        """Restore the journaled layout and view flags, then close the session."""
        if self.snapshot is None:
            if not self.journal.exists():
                return {'success': True, 'restored': 0, 'missing': 0}
            self.snapshot = json.loads(self.journal.read_text(encoding='utf-8'))
        if not self.snapshot.get('active'):
            return {'success': True, 'restored': 0, 'missing': 0}
        hidden = self.snapshot.get('hidden_listview')
        if hidden:
            # Journals written by earlier releases may have hidden the list view.
            import win32con
            import win32gui
            if win32gui.IsWindow(hidden) and win32gui.GetClassName(hidden) == 'SysListView32':
                win32gui.ShowWindow(hidden, win32con.SW_SHOWNA)
        if self.snapshot.get('overlay_only'):
            self.snapshot.update(active=False, restored_at=time.time())
            atomic_json(self.journal, self.snapshot)
            return {'success': True, 'restored': 0, 'missing': 0}
        if list(self.backend.get_geometry()) != self.snapshot['geometry']:
            raise PendingRecovery('显示器布局已变化，保留原快照；请恢复显示器配置后再恢复图标。')
        current = {item['id']: item for item in self.backend.items()}
        target = {
            item['id']: (item['x'], item['y'])
            for item in self.snapshot['items']
            if item['id'] in current
        }
        self.backend.set_flags(ARRANGE_MASK, 0)
        try:
            self.backend.move(target)
        finally:
            self.backend.set_flags(ARRANGE_MASK, int(self.snapshot['flags']))
        actual = {item['id']: (item['x'], item['y']) for item in self.backend.items()}
        if any(actual.get(identity) != tuple(point) for identity, point in target.items()):
            raise PendingRecovery('桌面位置未完全恢复；快照仍保留，请重试恢复。')
        restored_flags = self.backend.get_flags() & ARRANGE_MASK
        if restored_flags != (int(self.snapshot['flags']) & ARRANGE_MASK):
            raise PendingRecovery('排列设置未完全恢复；快照仍保留。')
        self.snapshot.update(active=False, restored_at=time.time())
        atomic_json(self.journal, self.snapshot)
        self.last = dict(target)
        return {
            'success': True,
            'restored': len(target),
            'missing': len(self.snapshot['items']) - len(target),
        }


class ShellDesktop:
    """Live desktop icon layout exposed through documented Shell COM interfaces."""

    def __init__(self):
        # Match the GUI and the independent recovery worker's coordinate/DPI context.
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        import comtypes
        import comtypes.client
        import pythoncom
        import pywintypes
        import win32com.client
        from win32com.shell import shell, shellcon

        self.shell = shell
        self.shellcon = shellcon
        self.cache = {}
        def lookup():
            self.windows = win32com.client.Dispatch('Shell.Application').Windows()
            hwnd = win32com.client.VARIANT(pythoncom.VT_I4 | pythoncom.VT_BYREF, 0)
            dispatch = self.windows.FindWindowSW(
                0, 0, shellcon.SWC_DESKTOP, hwnd, shellcon.SWFO_NEEDDISPATCH)
            return int(hwnd.value), dispatch

        self.hwnd, dispatch = _find_desktop(lookup)
        provider = dispatch._oleobj_.QueryInterface(pythoncom.IID_IServiceProvider)
        browser = provider.QueryService(
            pywintypes.IID('{4C96BE40-915C-11CF-99D3-00AA004AE837}'),
            shell.IID_IShellBrowser,
        )
        self.shellview = browser.QueryActiveShellView()
        self.view = self.shellview.QueryInterface(shell.IID_IFolderView)
        self.folder = self.view.GetFolder(shell.IID_IShellFolder)
        # comtypes provides a real COM pointer for the documented IFolderView2
        # flag methods. Slot indices are verified against ShObjIdl_core.h.

        class IShellBrowser(comtypes.IUnknown):
            _iid_ = comtypes.GUID('{000214E2-0000-0000-C000-000000000046}')
            _methods_ = []

        class IFolderView2(comtypes.IUnknown):
            _iid_ = comtypes.GUID('{1AF3A467-214F-4298-908E-06B03E0B39F9}')
            _methods_ = []

        def ct_lookup():
            self.ct_windows = comtypes.client.CreateObject('Shell.Application').Windows()
            hwnd, dispatch = self.ct_windows.FindWindowSW(0, 0, 8, 1)
            return int(hwnd), dispatch if dispatch else None

        ct_hwnd, ct_dispatch = _find_desktop(ct_lookup)
        if ct_hwnd != self.hwnd:
            raise RuntimeError('Windows 桌面在连接过程中发生变化，请重新启动游戏。')
        ct_browser = ct_dispatch.QueryInterface(comtypes.IServiceProvider).QueryService(
            comtypes.GUID('{4C96BE40-915C-11CF-99D3-00AA004AE837}'), IShellBrowser
        )
        out = ctypes.c_void_p()
        self._invoke(ct_browser, 15, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(out))
        if not out.value:
            raise RuntimeError('Windows 未返回可用的桌面视图，请等待桌面加载完成后重试。')
        self.ct_shellview = ctypes.cast(out, ctypes.POINTER(comtypes.IUnknown))
        self.ct_view = self.ct_shellview.QueryInterface(IFolderView2)
        self.items()

    @staticmethod
    def _invoke(obj, index, argtypes, *args):
        """Call a raw COM vtable slot, raising when the HRESULT reports failure."""
        address = ctypes.cast(obj, ctypes.c_void_p)
        vtable = ctypes.cast(address, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        function = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[index])
        hresult = function(address, *args)
        if hresult < 0:
            raise OSError(f'Shell COM HRESULT {hresult & 0xffffffff:08x}')
        return hresult

    def get_flags(self):
        """Return the current IFolderView2 view flags."""
        value = ctypes.c_uint32()
        self._invoke(self.ct_view, 25, [ctypes.POINTER(ctypes.c_uint32)], ctypes.byref(value))
        return value.value

    def set_flags(self, mask, value):
        """Set *mask* bits of the IFolderView2 view flags to *value*."""
        self._invoke(
            self.ct_view, 24, [ctypes.c_uint32, ctypes.c_uint32], mask, value
        )

    def get_geometry(self):
        """Return the virtual screen rectangle plus monitor count."""
        user32 = ctypes.windll.user32
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-2))
        try:
            # SM_XVIRTUALSCREEN .. SM_CMONITORS: bounds, size and monitor count.
            return tuple(user32.GetSystemMetrics(index) for index in (76, 77, 78, 79, 80))
        finally:
            if previous:
                user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))

    def items(self):
        """Return every desktop item as ``{'id', 'x', 'y'}`` dictionaries."""
        items = []
        cache = {}
        for index in range(self.view.ItemCount(self.shellcon.SVGIO_ALLVIEW)):
            child = self.view.Item(index)
            identity = self.folder.GetDisplayNameOf([child], self.shellcon.SHGDN_FORPARSING)
            x, y = self.view.GetItemPosition(child)
            if identity in cache:
                raise RuntimeError('Duplicate Shell item identity; refuse animating ambiguous items')
            cache[identity] = child
            items.append({'id': identity, 'x': int(x), 'y': int(y)})
        self.cache = cache
        return items

    def move(self, positions):
        """Move all requested identities in one documented SelectAndPositionItems call."""
        if not positions:
            return
        if set(positions) - set(self.cache):
            self.items()
        identities = [identity for identity in positions if identity in self.cache]
        if not identities:
            return
        from ctypes import wintypes

        # PyWin32's plural SelectAndPositionItems wrapper is intentionally
        # unimplemented, so call that documented COM method directly and batch
        # every icon into a single Shell call.
        buffers = [
            ctypes.create_string_buffer(self.shell.PIDLAsString([self.cache[identity]]))
            for identity in identities
        ]
        pointers = (ctypes.c_void_p * len(buffers))(
            *[ctypes.addressof(buffer) for buffer in buffers]
        )
        points = (wintypes.POINT * len(identities))(
            *[wintypes.POINT(*map(int, positions[identity])) for identity in identities]
        )
        self._invoke(
            self.ct_view,
            16,
            [
                ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(wintypes.POINT),
                ctypes.c_uint32,
            ],
            len(identities),
            pointers,
            points,
            self.shellcon.SVSI_POSITIONITEM,
        )
