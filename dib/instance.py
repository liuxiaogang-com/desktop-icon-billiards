"""Version-aware single instance ownership for Desktop Icon Billiards."""
from __future__ import annotations

import ctypes
import re
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import win32api
import win32con
import win32gui

from . import fonts
from .config import EXE_PATTERN, MUTEX, PRODUCT_NAMES, VERSION_PROPERTY
from .i18n import t

ERROR_ALREADY_EXISTS = 183


def version_code(version: str) -> int:
    """Encode a semantic version into a single window property value."""
    parts = tuple(int(part) for part in version.split('.'))
    if len(parts) != 3 or any(not 0 <= part <= 65535 for part in parts):
        raise ValueError('Invalid application version')
    return ((parts[0] << 32) | (parts[1] << 16) | parts[2]) + 1


def publish_version(root, version: str) -> None:
    """Store the running version on the top-level window for other instances."""
    root.update_idletasks()
    hwnd = win32gui.GetParent(root.winfo_id()) or root.winfo_id()
    user32 = ctypes.windll.user32
    user32.SetPropW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p]
    if not user32.SetPropW(hwnd, VERSION_PROPERTY, version_code(version)):
        raise ctypes.WinError()


def _process_image_name(pid: int) -> str | None:
    """Return the full executable path of a process id, if it is readable."""
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        size = ctypes.c_uint32(32768)
        name = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
            return None
        return name.value
    finally:
        kernel32.CloseHandle(handle)


def running_version(hwnd: int) -> str | None:
    """Return the version of the process owning *hwnd*, or None if unknown."""
    user32 = ctypes.windll.user32
    user32.GetPropW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    user32.GetPropW.restype = ctypes.c_void_p
    value = user32.GetPropW(hwnd, VERSION_PROPERTY)
    if value:
        value -= 1
        return f'{value >> 32}.{(value >> 16) & 65535}.{value & 65535}'
    # Earlier builds do not publish a version property; read their executable name.
    pid = ctypes.c_uint32()
    user32.GetWindowThreadProcessId.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    image = _process_image_name(pid.value)
    if not image:
        return None
    match = re.fullmatch(EXE_PATTERN, Path(image).name, re.IGNORECASE)
    return match.group(1) if match else None


def _is_product_window(hwnd: int) -> bool:
    """Check whether *hwnd* is a top-level window of this product."""
    if win32gui.GetClassName(hwnd) != 'TkTopLevel':
        return False
    user32 = ctypes.windll.user32
    user32.GetPropW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    user32.GetPropW.restype = ctypes.c_void_p
    if user32.GetPropW(hwnd, VERSION_PROPERTY):
        return True
    return win32gui.GetWindowText(hwnd).startswith(PRODUCT_NAMES)


def find_existing_window() -> int:
    """Return the first running Desktop Icon Billiards window, or 0."""
    matches = []

    def visit(hwnd, _):
        if _is_product_window(hwnd):
            matches.append(hwnd)

    win32gui.EnumWindows(visit, None)
    return matches[0] if matches else 0


def activate(hwnd: int) -> None:
    """Bring an existing window to the foreground, restoring it if minimized."""
    if not win32gui.IsWindow(hwnd):
        return
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except win32gui.error:
        win32gui.FlashWindow(hwnd, True)


def confirm_switch(hwnd: int, current: str | None, requested: str) -> bool:
    """Ask whether to replace the running version; returns True to switch."""
    root = tk.Tk()
    root.withdraw()
    root.title(t('instance.title'))
    root.resizable(False, False)
    body = ttk.Frame(root, padding=20)
    body.pack(fill='both', expand=True)
    label = current or t('instance.version_unknown')
    ttk.Label(
        body,
        text=t('instance.body', product=t('product.name'), running=label, requested=requested),
        font=(fonts.ui_family(), 10),
    ).pack(anchor='w')
    actions = ttk.Frame(body)
    actions.pack(fill='x', pady=(18, 0))
    answer = [False]

    def choose(value: bool) -> None:
        answer[0] = value
        root.destroy()

    ttk.Button(actions, text=t('instance.keep'), command=lambda: choose(False)).pack(side='left')
    ttk.Button(actions, text=t('instance.switch', version=requested), command=lambda: choose(True)).pack(
        side='right', padx=(12, 0)
    )
    root.protocol('WM_DELETE_WINDOW', lambda: choose(False))
    root.deiconify()
    root.update_idletasks()
    if win32gui.IsWindow(hwnd):
        rect = win32gui.GetWindowRect(hwnd)
        monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        work = win32api.GetMonitorInfo(monitor)['Work']
        own = win32gui.GetParent(root.winfo_id())
        bounds = win32gui.GetWindowRect(own)
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        x = max(work[0], min((rect[0] + rect[2] - width) // 2, work[2] - width))
        y = max(work[1], min((rect[1] + rect[3] - height) // 2, work[3] - height))
        win32gui.SetWindowPos(own, win32con.HWND_TOP, x, y, 0, 0, win32con.SWP_NOSIZE)
    root.mainloop()
    return answer[0]


def claim_instance_mutex(name=MUTEX):
    """Create the named mutex; return its handle, or None if another instance has it."""
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateMutexW(None, False, name)
    error = kernel32.GetLastError()
    if not handle:
        raise ctypes.WinError(error)
    if error == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return handle


def acquire_instance(version: str):
    """Return the instance mutex handle, or exit after activating/switching the running app."""
    handle = claim_instance_mutex()
    if handle:
        return handle
    deadline = time.monotonic() + 4
    hwnd = 0
    while time.monotonic() < deadline:
        hwnd = find_existing_window()
        if hwnd:
            break
        handle = claim_instance_mutex()
        if handle:
            return handle
        time.sleep(0.05)
    if not hwnd:
        messagebox.showinfo(t('instance.starting_title'), t('instance.starting_body'))
        raise SystemExit(0)
    current = running_version(hwnd)
    if current == version:
        activate(hwnd)
        raise SystemExit(0)
    if not confirm_switch(hwnd, current, version):
        activate(hwnd)
        raise SystemExit(0)
    if win32gui.IsWindow(hwnd):
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        handle = claim_instance_mutex()
        if handle:
            return handle
        time.sleep(0.1)
    activate(hwnd)
    messagebox.showinfo(
        t('instance.error_title'),
        t('instance.error_body'),
    )
    raise SystemExit(0)
