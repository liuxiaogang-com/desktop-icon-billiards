"""Animation thread that drives the layered windows outside Tk's event loop."""
from __future__ import annotations

import threading
import time
from collections import deque

import win32gui

from .display import dpi_context
from .i18n import t

FRAME_SECONDS = 1 / 60


class BilliardsRunner:
    """Own the overlay windows, tablet input polling and repaint loop."""

    def __init__(self, overlay):
        self.overlay = overlay
        self.prepared = threading.Event()
        self.shown = threading.Event()
        self.stopping = threading.Event()
        self.error = None
        self.show_request = None
        self.snapshot_lock = threading.Lock()
        self.latest_snapshot = None
        self.round_events = deque()
        self.thread = threading.Thread(target=self._run, name='DesktopIconBilliards', daemon=True)

    def start(self):
        self.thread.start()

    def request_show(self, snapshot, journal):
        self.show_request = (snapshot, journal)

    def publish_snapshot(self, snapshot):
        """Coalesce ordinary frames but retain round/first-shot/finish transitions."""
        with self.snapshot_lock:
            previous = self.latest_snapshot
            if (previous is None or snapshot.round_serial != previous.round_serial
                    or (snapshot.first_shot_at is not None) != (previous.first_shot_at is not None)
                    or (snapshot.state == 'finished') != (previous.state == 'finished')):
                self.round_events.append(snapshot)
            self.latest_snapshot = snapshot

    def take_snapshots(self):
        """Transfer a consistent ordered batch; never hold the lock during UI or I/O."""
        with self.snapshot_lock:
            snapshots = list(self.round_events)
            self.round_events.clear()
            if self.latest_snapshot is not None and (not snapshots or snapshots[-1] != self.latest_snapshot):
                snapshots.append(self.latest_snapshot)
            return snapshots

    def stop(self):
        self.stopping.set()
        self.thread.join(5)
        if self.thread.is_alive():
            raise RuntimeError(t('runner.error.thread_alive'))

    def _run(self):
        with dpi_context(-4):
            self._run_with_dpi()

    def _run_with_dpi(self):
        try:
            # Tk may change the UI thread DPI context; match native desktop coordinates.
            self.overlay.prepare()
            self.overlay.table.snapshot_sink = self.publish_snapshot
            self.publish_snapshot(self.overlay.table.snapshot())
            self.prepared.set()
            last = time.perf_counter()
            while not self.stopping.is_set():
                now = time.perf_counter()
                if self.show_request and not self.shown.is_set():
                    if self.overlay.show(*self.show_request) is not False:
                        self.shown.set()
                        last = now
                if self.shown.is_set():
                    self.overlay.draw(now, min(0.05, now - last))
                    self.publish_snapshot(self.overlay.table.snapshot())
                last = now
                win32gui.PumpWaitingMessages()
                self.stopping.wait(max(0.001, FRAME_SECONDS - (time.perf_counter() - now)))
        except Exception as exc:  # surfaced to the Tk thread through .error
            self.error = exc
        finally:
            try:
                self.overlay.close()
            except Exception as exc:
                if self.error is None:
                    self.error = exc
