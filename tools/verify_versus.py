"""Play one LAN round on the real desktop: host and guest in a single process.

This is the check that the unit tests cannot do. It starts the actual application (the same
way ``main()`` does), then a second in-process client joins over loopback as the guest, asks
for a restart to take the break, and takes a shot. The host's animation thread integrates the
physics, so a broken coordinate transform, snapshot or table swap shows up immediately as a
crash or as a mirror that disagrees.

Run it from the repository root on an interactive desktop session; it takes the desktop over
for about half a minute and restores it through the app's own exit path:

    python tools\verify_versus.py

Use an interpreter that can write to ``%TEMP%`` and ``%APPDATA%``: a ``.venv`` created inside
a workspace that carries a Low integrity label runs at Low integrity and cannot write to the
user's profile, so pick the system interpreter in that case.

Artifacts: ``%TEMP%\\dib_live_shot.png`` and ``%TEMP%\\dib_live_report.json``.
Exit code 0 means the round was mirrored and no error log was written.
"""
from __future__ import annotations

import ctypes
import json
import os
import pathlib
import sys
import threading
import time
import tkinter as tk
import traceback

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from PIL import ImageGrab                             # noqa: E402

from dib import config, instance, netplay, ui          # noqa: E402
from dib.table import LOGICAL_BOUNDS, VERSUS_BALLS, BilliardsMotion   # noqa: E402
from dib.versus_sync import VersusLink                 # noqa: E402

SHOT = pathlib.Path(os.environ['TEMP']) / 'dib_live_shot.png'
REPORT = pathlib.Path(os.environ['TEMP']) / 'dib_live_report.json'
ERROR_LOG = pathlib.Path(os.environ['TEMP']) / 'DesktopIconBilliards' / 'error.log'

# ``publish_version`` stamps a window property used for multi-version handoff; a harness
# root created outside ui.main() has no parent window yet, and the check does not need it.
instance.publish_version = lambda *args, **kwargs: None

report = {'stages': [], 'errors': [], 'round': {}, 'guest': {}, 'shot': None}
deadline = time.monotonic() + 75


def note(stage, **extra):
    entry = {'stage': stage, 'at': round(time.monotonic(), 2), **extra}
    report['stages'].append(entry)
    print(json.dumps(entry, ensure_ascii=False), flush=True)


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # same as ui.main()
    except OSError:
        pass
    handle = ui.acquire_instance(config.VERSION)
    guard = ui.claim_instance_mutex(config.DESKTOP_GUARD)
    if guard is None:
        raise SystemExit('another instance is already guarding the desktop')
    root = tk.Tk()
    app = ui.DesktopBilliardsApp(root, handle)
    state = {'session': None, 'guest': None, 'link': None, 'shot_sent': False}

    def guest_pump():
        """Act as the second player: mirror the host and take one shot."""
        guest_table = BilliardsMotion(LOGICAL_BOUNDS, [(64, 64)] * VERSUS_BALLS,
                                      count=VERSUS_BALLS)
        guest_table.authoritative = False
        guest_table.local_side = 1
        # first=0, like the real overlay: the guest may not shoot until a host snapshot says
        # the table is its.
        guest_table.start_versus(VERSUS_BALLS, first=0)
        link = VersusLink(guest_table, state['guest'], 'guest')
        state['link'] = link
        restarted = False
        now = time.monotonic()
        while state['guest'].status == 'connected' and time.monotonic() < deadline:
            now += 1 / 60
            link.pump(now)
            if not restarted:
                link.restart()
                restarted = True
            elif (not state['shot_sent'] and guest_table.remote_ms is not None
                  and guest_table.can_shoot()):
                guest_table.aim = (-1.0, -0.02)
                guest_table.shoot(0.92)
                state['shot_sent'] = True
                note('guest_shot', turn=guest_table.turn, state=guest_table.state)
            time.sleep(1 / 90)
        report['guest'] = {
            'turn': guest_table.turn, 'state': guest_table.state,
            'scores': list(guest_table.scores), 'shots': guest_table.shots,
            'white': [round(guest_table.white.x, 1), round(guest_table.white.y, 1)],
            'nack': link.nack_note, 'disconnect': link.disconnect_reason,
        }
        note('guest_done', **report['guest'])

    def step_connect():
        try:
            overlay = app.runner.overlay
            note('shown', windows=len(overlay.windows), table_state=overlay.table.state)
            session = netplay.start_host('TEST', port=0, discovery=False)
            state['session'] = session
            time.sleep(0.2)
            state['guest'] = netplay.join('127.0.0.1', session.port, 'TEST', timeout=6.0)
            note('guest_joined', port=session.port)
            app.start_versus(session, 'host', '本机测试')
            note('versus_requested')
            threading.Thread(target=guest_pump, name='guest', daemon=True).start()
            root.after(2500, step_check_versus)
        except Exception as error:
            report['errors'].append(f'connect: {error!r}')
            note('connect_failed', error=repr(error))
            root.after(200, step_finish)

    def step_check_versus():
        try:
            if app.runner is None:
                report['errors'].append('runner died: see error_log')
                note('runner_died')
                root.after(200, step_finish)
                return
            overlay = app.runner.overlay
            note('versus_applied', role=overlay.versus_role,
                 scale=round(overlay.view.scale, 4), error=overlay.versus_error,
                 balls=len(overlay.table.icons), link=overlay.link is not None)
            report['round'].update(role=overlay.versus_role, scale=overlay.view.scale,
                                   versus_error=overlay.versus_error)
            root.after(2200, step_capture)
        except Exception as error:
            report['errors'].append(f'check: {error!r}')
            root.after(200, step_finish)

    def step_capture():
        try:
            image = ImageGrab.grab()
            image.save(SHOT)
            report['shot'] = {'path': str(SHOT), 'size': image.size}
            note('screenshot', size=image.size, path=str(SHOT))
        except Exception as error:
            report['errors'].append(f'capture: {error!r}')
        root.after(1500, step_round_state)

    def step_round_state():
        try:
            table = app.runner.overlay.table
            report['round'].update({
                'state': table.state, 'turn': table.turn, 'scores': list(table.scores),
                'shots': table.shots, 'serial': table.round_serial,
                'white': [round(table.white.x, 1), round(table.white.y, 1)],
                'pocketed': table.pocketed_icons, 'play_ms': table.play_ms,
            })
            note('round_state', **report['round'])
        except Exception as error:
            report['errors'].append(f'round: {error!r}')
        root.after(600, step_finish)

    def step_finish():
        try:
            note('closing')
            app.close()
        except Exception as error:
            report['errors'].append(f'close: {error!r}')
            traceback.print_exc()
        finally:
            try:
                root.quit()
            except tk.TclError:
                pass

    def watchdog_timeout():
        report['errors'].append('harness deadline hit')
        try:
            root.quit()
        except tk.TclError:
            pass

    root.after(9000, step_connect)
    root.after(int((deadline - time.monotonic()) * 1000), watchdog_timeout)
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
        report['error_log'] = None
        if ERROR_LOG.exists():
            report['error_log'] = ERROR_LOG.read_text(encoding='utf-8', errors='replace')[-1500:]
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('REPORT', REPORT, flush=True)

    compared = ('state', 'turn', 'scores', 'shots', 'white')
    host = report['round']
    guest = report['guest']
    mismatched = [key for key in compared if host.get(key) != guest.get(key)]
    ok = (host.get('role') == 'host' and host.get('shots', 0) >= 1
          and not mismatched and not report['errors'] and not report['error_log'])
    print('VERIFY', 'OK' if ok else 'FAILED',
          f'(host={ {k: host.get(k) for k in compared} }, '
          f'guest={ {k: guest.get(k) for k in compared} }, '
          f'mismatched={mismatched}, errors={report["errors"]})', flush=True)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
