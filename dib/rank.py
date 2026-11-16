"""Global ranking client: device identity, signed requests and round submission.

Ranking is opt-in. A round is timed from the first shot to the last icon sinking
(whole milliseconds, see ``BilliardsMotion.play_ms``). The metric that is ranked is
the average time per ball, ``total_ms / ball_count``. Desktops with fewer than
``MIN_BALLS`` icons are padded by duplicating icons, so every ranked round has at
least that many balls; tables are trimmed to ``MAX_BALLS``, so every round is normally ranked.

All network access happens on one worker thread; the UI thread only calls
:meth:`RankController.observe` and reads :meth:`RankController.lines`. The server
contract lives in the separate ``desktop-icon-billiards-server`` repository.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
from datetime import datetime
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .balls import MAX_BALLS, MIN_BALLS, fill_sources  # noqa: F401  (re-exported for callers and tests)
from .i18n import t

# Every table is trimmed to MAX_BALLS, so in practice all rounds are ranked; the check stays as a guard.
MAX_RANKED_BALLS = MAX_BALLS
MAX_RESPONSE_BYTES = 64 * 1024
REQUEST_TIMEOUT = 8
# A round id older than this is replaced when the first shot is taken (server TTL is 6 h).
START_REFRESH_SECONDS = 30 * 60
HASH_SALT = b'dib-rank-v1|'

# Error codes the ranking server can answer with; each has a ``rank.error.<code>`` catalog entry.
ERROR_CODES = ('network', 'bad_response', 'rate_limited', 'busy', 'stale_timestamp',
               'too_fast', 'round_invalid', 'no_round', 'implausible_time')


def error_text(code):
    """The player-facing text for a ranking error code."""
    return t(f'rank.error.{code}' if code in ERROR_CODES else 'rank.error.unknown')


def is_rankable(ball_count):
    return MIN_BALLS <= ball_count <= MAX_RANKED_BALLS


def average_seconds(total_ms, ball_count):
    return total_ms / ball_count / 1000


def normalize_base_url(url):
    """Accept an HTTPS origin (plain HTTP only for localhost); reject anything with a path."""
    parts = urllib.parse.urlsplit((url or '').strip())
    host = parts.hostname
    if (not host or parts.username or parts.password or parts.query or parts.fragment
            or parts.path not in ('', '/')):
        raise ValueError('排名服务地址必须是不带路径的 https 地址')
    local = host in ('localhost', '127.0.0.1', '::1')
    if parts.scheme != 'https' and not (parts.scheme == 'http' and local):
        raise ValueError('排名服务地址必须使用 https')
    return f'{parts.scheme}://{parts.netloc}'


def sign(secret, method, path, timestamp, body):
    """HMAC-SHA256 over ``METHOD\\nPATH\\nTIMESTAMP\\nsha256(body)``; mirrors the server."""
    body_hash = hashlib.sha256(body).hexdigest()
    message = f'{method}\n{path}\n{timestamp}\n{body_hash}'
    return hmac.new(secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()


class RankError(Exception):
    def __init__(self, code, status=0):
        super().__init__(code)
        self.code = code
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: signed headers must not be replayed to another host."""

    def redirect_request(self, *args, **kwargs):
        return None


class RankApi:
    """Thin signed HTTP client. Raises :class:`RankError` with the server's error code."""

    def __init__(self, base_url, version, *, timeout=REQUEST_TIMEOUT):
        self.base = normalize_base_url(base_url)
        self.version = version
        self.timeout = timeout
        self.opener = urllib.request.build_opener(_NoRedirect)

    def _request(self, method, path, payload=None, creds=None):
        body = b'' if payload is None else json.dumps(payload, separators=(',', ':')).encode('utf-8')
        headers = {'User-Agent': f'DesktopIconBilliards/{self.version}', 'Accept': 'application/json'}
        if method == 'POST':
            headers['Content-Type'] = 'application/json'
        if creds:
            timestamp = str(int(time.time()))
            headers['X-Device-Id'] = creds['device_id']
            headers['X-Timestamp'] = timestamp
            headers['X-Signature'] = sign(creds['secret'], method, path, timestamp, body)
        request = urllib.request.Request(self.base + path, data=body if method == 'POST' else None,
                                         headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                status, raw = response.status, response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            status, raw = error.code, error.read(MAX_RESPONSE_BYTES + 1)
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise RankError('network') from error
        if len(raw) > MAX_RESPONSE_BYTES:
            raise RankError('bad_response', status)
        try:
            data = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            raise RankError('bad_response', status) from None
        if not isinstance(data, dict):
            raise RankError('bad_response', status)
        if status != 200:
            code = data.get('error')
            raise RankError(code if isinstance(code, str) else 'error', status)
        return data

    def register(self, machine_hash):
        data = self._request('POST', '/v1/device/register', {'machine_hash': machine_hash})
        device_id, secret = data.get('device_id'), data.get('secret')
        if not isinstance(device_id, str) or not isinstance(secret, str):
            raise RankError('bad_response')
        return device_id, secret

    def start_round(self, creds):
        data = self._request('POST', '/v1/round/start', {}, creds)
        if not isinstance(data.get('round_id'), str):
            raise RankError('bad_response')
        return data['round_id']

    def finish_round(self, creds, round_id, total_ms, icon_count, shots):
        return self._request('POST', '/v1/round/finish', {
            'round_id': round_id, 'total_ms': int(total_ms), 'icon_count': int(icon_count), 'shots': int(shots),
        }, creds)

    def leaderboard(self, limit=50):
        return self._request('GET', f'/v1/leaderboard?limit={int(limit)}')

    def rankings(self, creds=None):
        data = self._request('GET', '/v1/rankings', creds=creds)
        def valid_entry(entry):
            return (isinstance(entry, dict) and type(entry.get('rank')) is int and entry['rank'] > 0
                    and type(entry.get('avg_ms')) in (int, float) and math.isfinite(entry['avg_ms'])
                    and entry['avg_ms'] > 0 and type(entry.get('is_me')) is bool
                    and all(key not in entry or (type(entry[key]) is int and entry[key] > 0)
                            for key in ('icon_count', 'shots')))
        entries = data.get('entries')
        if (not isinstance(entries, list) or len(entries) > 50 or not all(map(valid_entry, entries))
                or 'me' not in data or (data['me'] is not None and not valid_entry(data['me']))):
            raise RankError('bad_response')
        try:
            stamp = datetime.fromisoformat(data['generated_at'].replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('missing timezone')
        except (KeyError, TypeError, ValueError, AttributeError):
            raise RankError('bad_response') from None
        return data


def data_directory():
    """Persistent per-user settings (unlike the Temp-based runtime directory)."""
    root = os.environ.get('APPDATA') or str(Path.home())
    directory = Path(root) / 'DesktopIconBilliards'
    directory.mkdir(parents=True, exist_ok=True)
    return directory


class RankSettings:
    """Small JSON file holding the opt-in flag and the server-issued device credentials."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            self.data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.data = {}

    def get(self, key, default=None):
        with self.lock:
            return self.data.get(key, default)

    def update(self, **values):
        with self.lock:
            self.data.update(values)
            snapshot = json.dumps(self.data, ensure_ascii=False, indent=1)
        try:
            temporary = self.path.with_name(self.path.name + '.tmp')
            temporary.write_text(snapshot, encoding='utf-8')
            os.replace(temporary, self.path)
        except OSError:
            pass  # not persisted; the in-memory value still works for this session


def machine_hash(settings=None):
    """Salted SHA-256 of the Windows MachineGuid. The raw value never leaves the PC."""
    guid = None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography', 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            guid = winreg.QueryValueEx(key, 'MachineGuid')[0]
    except (ImportError, OSError):
        pass
    if not isinstance(guid, str) or not guid.strip():
        # No readable MachineGuid: fall back to a random id that survives in the settings file.
        guid = settings.get('fallback_id') if settings else None
        if not guid:
            guid = uuid.uuid4().hex
            if settings:
                settings.update(fallback_id=guid)
    return hashlib.sha256(HASH_SALT + guid.strip().lower().encode('utf-8')).hexdigest()


class RankController:
    """Follow the table, start/submit rounds in the background and describe the outcome."""

    def __init__(self, api, settings, *, hash_function=machine_hash, clock=time.monotonic):
        self.api = api
        self.settings = settings
        self.hash_function = hash_function
        self.clock = clock
        self.jobs = queue.Queue()
        self.inbox = queue.SimpleQueue()
        self.thread = None
        self.serial = None
        self.round_id = None
        self.round_sent = 0.
        self.start_token = 0
        self.shot_seen = False
        self.skip = False
        self.submitted = None
        self.status = []
        self.outcome = None  # ('ok', server data) or ('error', code) once the round was answered

    # -- state -------------------------------------------------------------
    @property
    def available(self):
        return self.api is not None

    @property
    def enabled(self):
        return self.available and bool(self.settings.get('enabled'))

    @property
    def needs_prompt(self):
        """True until the player has been asked once whether to join the ranking."""
        return self.available and not self.settings.get('prompted')

    def mark_prompted(self):
        self.settings.update(prompted=True)

    def set_enabled(self, value):
        self.settings.update(enabled=bool(value), prompted=True)
        self.serial = None  # re-evaluate the current round on the next observe()
        self.status = []

    def close(self):
        if self.thread is not None:
            self.jobs.put(None)

    def fetch_rankings(self):
        """Called by the leaderboard worker, never by Tk's UI thread."""
        if not self.available:
            raise RankError('unavailable')
        if self.enabled:
            return self._call(self.api.rankings)
        return self.api.rankings()

    # -- UI thread ---------------------------------------------------------
    def observe(self, table):
        self._drain()
        if not self.enabled:
            return
        if table.round_serial != self.serial:
            self._begin(table)
        if self.skip or not is_rankable(table.icon_count):
            return
        if table.first_shot_at is not None and not self.shot_seen:
            self.shot_seen = True
            stale = self.round_id is not None and self.clock() - self.round_sent > START_REFRESH_SECONDS
            if self.round_id is None or stale:
                self._request_start()
        if table.state == 'finished' and self.submitted != self.serial:
            self._finish(table)

    def lines(self, table):
        """Up to three short lines for the summary label under the stats."""
        count = table.icon_count
        if table.state == 'finished':
            lines = [t('rank.lines.average', average=f'{average_seconds(table.play_ms, count):.2f}', balls=count)]
            if self.enabled:
                lines.extend(self.status or [''])
            elif self.available:
                lines.append(t('rank.lines.enable_hint'))
            return lines
        if not self.enabled:
            return []
        if self.skip:
            return [t('rank.lines.skip')]
        if not is_rankable(count):
            return [t('rank.lines.too_many', max=MAX_RANKED_BALLS)]
        return [t('rank.lines.ready')]

    def view(self, table):
        """The ranking outcome of the current round as plain data for the result dialog.

        ``phase`` is ``unavailable`` (no server configured), ``off`` (not joined), ``skipped``,
        ``pending``, ``ok`` (with formatted ``rank_text`` / ``percentile_text`` / ``best_text``)
        or ``error`` (with a ``message``).
        """
        if not self.available:
            return {'phase': 'unavailable'}
        if not self.enabled:
            return {'phase': 'off'}
        if self.skip:
            return {'phase': 'skipped', 'message': t('rank.view.skip')}
        if not is_rankable(table.icon_count):
            return {'phase': 'skipped', 'message': t('rank.view.too_many', max=MAX_RANKED_BALLS)}
        if self.outcome is None:
            return {'phase': 'pending'}
        kind, payload = self.outcome
        if kind == 'ok':
            try:
                this, best = payload['round'], payload['best']
                return {
                    'phase': 'ok',
                    'rank': int(this['rank']),
                    'percentile': float(this['percentile']),
                    'best_rank': int(best['rank']),
                    'rank_text': t('rank.text.rank', rank=f'{this["rank"]:,}'),
                    'percentile_text': t('rank.text.percentile', percentile=this['percentile']),
                    'best_text': (t('rank.text.new_best') if payload.get('is_personal_best') else '')
                                 + t('rank.text.best', rank=f'{best["rank"]:,}'),
                    'new_best': bool(payload.get('is_personal_best')),
                }
            except (KeyError, TypeError, ValueError):
                payload = 'bad_response'
        return {'phase': 'error', 'message': error_text(payload)}

    def _begin(self, table):
        self.serial = table.round_serial
        self.round_id = None
        self.round_sent = 0.
        self.shot_seen = False
        self.submitted = None
        self.status = []
        self.outcome = None
        self.start_token += 1  # invalidate answers that belong to the previous round
        # Enabled in the middle of a round: it cannot be verified, so it is not ranked.
        self.skip = table.first_shot_at is not None
        if not self.skip and is_rankable(table.icon_count):
            self._request_start()

    def _request_start(self):
        self.start_token += 1
        token, serial, sent = self.start_token, self.serial, self.clock()

        def job():
            try:
                round_id = self._call(self.api.start_round)
            except RankError as error:
                self.inbox.put(('start_failed', token, serial, error.code))
            else:
                self.inbox.put(('started', token, serial, round_id, sent))

        self._submit_job(job)

    def _finish(self, table):
        self.submitted = self.serial
        if self.round_id is None:
            self.status = [error_text('no_round')]
            self.outcome = ('error', 'no_round')
            return
        self.status = [t('rank.status.submitting')]
        serial, round_id = self.serial, self.round_id
        total_ms, count, shots = table.play_ms, table.icon_count, table.shots

        def job():
            try:
                data = self._call(lambda creds: self.api.finish_round(creds, round_id, total_ms, count, shots))
            except RankError as error:
                self.inbox.put(('failed', serial, error.code))
            else:
                self.inbox.put(('result', serial, data))

        self._submit_job(job)

    def _drain(self):
        while True:
            try:
                event = self.inbox.get_nowait()
            except queue.Empty:
                return
            kind = event[0]
            if kind == 'started':
                _, token, serial, round_id, sent = event
                if token == self.start_token and serial == self.serial:
                    self.round_id, self.round_sent = round_id, sent
            elif kind == 'start_failed':
                pass  # reported only if the round finishes without an id
            elif event[1] == self.serial:
                if kind == 'failed':
                    self.status = [error_text(event[2])]
                    self.outcome = ('error', event[2])
                else:
                    self.status = self._describe(event[2])
                    self.outcome = ('ok', event[2])

    @staticmethod
    def _describe(data):
        try:
            this, best = data['round'], data['best']
            first = t('rank.status.first', rank=f'{this["rank"]:,}', percentile=this['percentile'])
            prefix = t('rank.text.new_best') if data.get('is_personal_best') else ''
            second = t('rank.status.second', prefix=prefix, rank=f'{best["rank"]:,}')
        except (KeyError, TypeError, ValueError):
            return [error_text('bad_response')]
        return [first, second]

    # -- worker thread -----------------------------------------------------
    def _submit_job(self, job):
        if self.thread is None:
            self.thread = threading.Thread(target=self._work, name='Billiards-rank', daemon=True)
            self.thread.start()
        self.jobs.put(job)

    def _work(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            try:
                job()
            except Exception:
                pass  # every job reports its own outcome through the inbox

    def _credentials(self, force=False):
        settings = self.settings
        device_id, secret = settings.get('device_id'), settings.get('secret')
        if not force and device_id and secret and settings.get('server') == self.api.base:
            return {'device_id': device_id, 'secret': secret}
        device_id, secret = self.api.register(self.hash_function(settings))
        settings.update(device_id=device_id, secret=secret, server=self.api.base)
        return {'device_id': device_id, 'secret': secret}

    def _call(self, function):
        """Run ``function(creds)``; register again once if the server no longer knows the secret."""
        try:
            return function(self._credentials())
        except RankError as error:
            if error.status != 401 or error.code != 'unauthorized':
                raise
        return function(self._credentials(force=True))
