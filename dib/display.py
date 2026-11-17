"""Explicit DPI contexts and physical monitor layout for desktop artwork."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from contextlib import contextmanager

import win32api
import win32con


def work_area(x, y):
    """The work rect of the monitor nearest a screen point, as ``(left, top, right, bottom)``."""
    handle = win32api.MonitorFromPoint((x, y), win32con.MONITOR_DEFAULTTONEAREST)
    left, top, right, bottom = win32api.GetMonitorInfo(handle)['Work']
    return left, top, right, bottom


@contextmanager
def dpi_context(value):
    """Run the block under a specific thread DPI awareness context."""
    user32 = ctypes.windll.user32
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(value))
    try:
        yield
    finally:
        if previous:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))


def monitors():
    """Return a sorted tuple of device, monitor rect, work rect, DPI and primary flag."""
    result = []
    get_dpi = ctypes.windll.shcore.GetDpiForMonitor
    get_dpi.argtypes = [wintypes.HANDLE, ctypes.c_int,
                       ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
    get_dpi.restype = ctypes.c_long
    with dpi_context(-4):
        for handle, _, _ in win32api.EnumDisplayMonitors():
            info = win32api.GetMonitorInfo(handle)
            dpi_x = ctypes.c_uint()
            dpi_y = ctypes.c_uint()
            hresult = get_dpi(
                int(handle), 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
            )
            if hresult:
                raise OSError('Cannot read monitor DPI')
            result.append(
                (
                    info['Device'],
                    info['Monitor'],
                    info['Work'],
                    dpi_x.value,
                    bool(info['Flags'] & 1),
                )
            )
    return tuple(sorted(result))


def window_dpi(hwnd):
    """Use a pointer-sized HWND, including on 64-bit Windows."""
    get_dpi = ctypes.windll.user32.GetDpiForWindow
    get_dpi.argtypes = [wintypes.HWND]
    get_dpi.restype = ctypes.c_uint
    dpi = get_dpi(hwnd)
    if not dpi:
        raise OSError('Cannot read window DPI')
    return dpi
