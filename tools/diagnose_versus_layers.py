"""Enter a versus table on a real desktop and dump the layer state that the eye cannot see.

The felt layer is drawn from the wallpaper: when ``primary_wallpaper()`` returns an image,
``table_art`` pastes a blurred copy and the rail becomes opaque. A harness desktop without a
readable wallpaper leaves it transparent, which is exactly why the "felt covers the cue ball"
bug did not reproduce in the earlier checks. This tool pins a bright wallpaper first, then
reports the z-order and captures the desktop layer.
"""
import json
import os
import pathlib
import sys
import threading
import time
import tkinter as tk

REPO = pathlib.Path(r'C:\Users\Lenovo\Documents\deepseek-harness\default-workspace\desktop-icon-billiards')
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import ctypes                                            # noqa: E402
import win32gui                                          # noqa: E402
from PIL import Image                                    # noqa: E402

import dib.overlay as overlay_module                     # noqa: E402

# Make the table surface opaque so the layer order actually matters.
overlay_module.primary_wallpaper = lambda *args, **kwargs: Image.new('RGB', (1920, 1080), (243, 246, 250))

from dib import config, instance, netplay, ui             # noqa: E402
from dib.table import LOGICAL_BOUNDS, VERSUS_BALLS, BilliardsMotion   # noqa: E402
from dib.versus_sync import VersusLink                   # noqa: E402

instance.publish_version = lambda *a, **k: None
OUT = pathlib.Path(os.environ['TEMP']) / 'dib_diag.json'
DUMP = pathlib.Path(os.environ['TEMP']) / 'dib_diag_layer.png'
report = {'stages': [], 'errors': []}
deadline = time.monotonic() + 60


def note(stage, **extra):
    report['stages'].append({'stage': stage, **extra})
    print(json.dumps({'stage': stage, **extra}, ensure_ascii=False), flush=True)


def child_zorder(parent):
    """Map child hwnd -> index in the z-order (0 = topmost)."""
    order, hwnd, index = {}, ctypes.windll.user32.GetWindow(parent, 5), 0
    while hwnd and index < 500:
        order[hwnd] = index
        hwnd = ctypes.windll.user32.GetWindow(hwnd, 2)
        index += 1
    return order


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except OSError:
        pass
    handle = ui.acquire_instance(config.VERSION)
    guard = ui.claim_instance_mutex(config.DESKTOP_GUARD)
    root = tk.Tk()
    app = ui.DesktopBilliardsApp(root, handle)
    state = {'session': None, 'guest': None}

    def guest_pump():
        table = BilliardsMotion(LOGICAL_BOUNDS, [(64, 64)] * VERSUS_BALLS, count=VERSUS_BALLS)
        table.authoritative = False
        table.local_side = 1
        table.start_versus(VERSUS_BALLS, first=0)
        link = VersusLink(table, state['guest'], 'guest')
        now = time.monotonic()
        while state['guest'].status == 'connected' and time.monotonic() < deadline:
            now += 1 / 60
            link.pump(now)
            time.sleep(1 / 90)

    def step_connect():
        try:
            overlay = app.runner.overlay
            note('shown', windows=len(overlay.windows), state=overlay.table.state,
                 solo_wallpaper=overlay.wallpaper is not None,
                 solo_rail_opaque=None if overlay.rail_frame is None else overlay.rail_frame[0].size)
            session = netplay.start_host('DIAG', port=0, discovery=False)
            state['session'] = session
            time.sleep(0.2)
            state['guest'] = netplay.join('127.0.0.1', session.port, 'DIAG', timeout=6.0)
            app.start_versus(session, 'host', '诊断')
            threading.Thread(target=guest_pump, daemon=True).start()
            root.after(2600, step_dump)
        except Exception as error:
            report['errors'].append(f'connect: {error!r}')
            root.after(200, finish)

    def step_dump():
        try:
            overlay = app.runner.overlay
            table = overlay.table
            order = child_zorder(overlay.parent)
            windows = {}
            for part in ('guide', 'rail', 'white', 'cue', 'charge'):
                hwnd = overlay.table_windows.get(part)
                windows[part] = None if hwnd is None else {
                    'hwnd': hwnd,
                    'visible': bool(win32gui.IsWindowVisible(hwnd)),
                    'rect': list(win32gui.GetWindowRect(hwnd)),
                    'z': order.get(hwnd),
                }
            cue = table.cue()
            data = {
                'role': overlay.versus_role,
                'view': {'scale': overlay.view.scale, 'offset': list(overlay.view.offset)},
                'wallpaper_readable': overlay.wallpaper is not None,
                'logical_wallpaper_size': None if overlay.logical_wallpaper is None
                                          else list(overlay.logical_wallpaper.size),
                'table': {'state': table.state, 'turn': table.turn, 'local_side': table.local_side,
                          'white_sunk': table.white_sunk, 'white': [round(table.white.x, 2), round(table.white.y, 2)],
                          'aim': list(table.aim), 'bounds': list(table.bounds)},
                'derive': {'cue': None if cue is None else [[round(v, 1) for v in p] for p in cue],
                           'cue_visible_gate': overlay.cue_visible(),
                           'pointer_active': overlay.pointer_active(),
                           'white_position_cache': None if overlay.white_position is None
                                                   else list(overlay.white_position),
                           'zorder_dirty': overlay.zorder_dirty},
                'windows': windows,
            }
            report['dump'] = data
            note('dump', **{k: v for k, v in data.items() if k != 'windows'})
            for part, value in windows.items():
                note('layer', part=part, **(value or {}))
            try:
                from tools.verify_versus import capture_desktop_layer
                size = capture_desktop_layer(overlay.parent, DUMP)
                note('capture', size=size, path=str(DUMP))
            except Exception as error:
                note('capture_failed', error=repr(error))
        except Exception as error:
            report['errors'].append(f'dump: {error!r}')
        root.after(300, finish)

    def finish():
        try:
            app.close()
        except Exception as error:
            report['errors'].append(f'close: {error!r}')
        finally:
            try:
                root.quit()
            except tk.TclError:
                pass

    root.after(9000, step_connect)
    root.after(int((deadline - time.monotonic()) * 1000), finish)
    try:
        root.mainloop()
    finally:
        for key in ('guest', 'session'):
            session = state.get(key)
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
        try:
            ctypes.windll.kernel32.CloseHandle(handle)
        except OSError:
            pass
        if guard:
            ctypes.windll.kernel32.CloseHandle(guard)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('REPORT', OUT, flush=True)


if __name__ == '__main__':
    main()
