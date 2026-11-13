"""LAN transport for the versus mode: framing, handshake, heartbeat, discovery.

The wire format, message types and thread rules are frozen in
``docs/NETPLAY.md`` section 3.  This module only moves frames around: it never
imports ``table``, never touches Tk and never raises for I/O failures.

Framing
-------
Every TCP frame is a four byte big-endian payload length followed by a UTF-8
encoded JSON object.  Lengths outside ``1 .. MAX_FRAME_BYTES`` are a protocol
violation: the connection is dropped and ``status`` becomes ``'error'``.

Threads
-------
Every background thread is a daemon and polls its socket with ``POLL_SECONDS``
timeouts, so :meth:`Session.close` can stop all of them well inside a second.

Player visible strings are Chinese; comments and docstrings are English.
"""
from __future__ import annotations

import json
import queue
import secrets
import socket
import struct
import threading
import time

PROTOCOL_VERSION = 1
DISCOVERY_PORT = 45871
GAME_PORT = 45872
LOGICAL_BOUNDS = (0.0, 0.0, 1600.0, 900.0)
VERSUS_BALLS = 15
STATE_HZ = 20.0
HEARTBEAT_SECONDS = 1.0
TIMEOUT_SECONDS = 5.0
MAX_FRAME_BYTES = 65536

# Room codes avoid the characters that are easy to mistake for each other.
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
CODE_LENGTH = 4
# Frames sent before the connection exists, and received frames nobody drained.
PENDING_LIMIT = 32
INBOX_LIMIT = 256
# Outbound bytes queued for a peer that stopped reading; beyond this the link is dead.
MAX_OUTBUF_BYTES = 262144
POLL_SECONDS = 0.1
HEADER = struct.Struct('>I')
DEFAULT_NAME = '电脑'
NAME_LIMIT = 32


class NetError(Exception):
    """Handshake, version, room code or timeout failure.  Text is player facing."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def make_code() -> str:
    """Return a four character room code, e.g. ``'K7QM'``."""
    return ''.join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def _machine_name() -> str:
    try:
        name = socket.gethostname().strip()
    except OSError:
        name = ''
    return name[:NAME_LIMIT] or DEFAULT_NAME


def _route_local_ip(peer_host: str) -> str:
    """Local interface address the OS would use to reach ``peer_host``.

    A connected UDP socket only performs a route lookup here, no packet is sent.
    """
    probe = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect((peer_host, 9))
        return probe.getsockname()[0]
    except OSError:
        return ''
    finally:
        if probe is not None:
            probe.close()


def local_addresses() -> list[str]:
    """IPv4 addresses of this machine that the LAN can reach; loopback excluded."""
    found = []

    def add(value: str) -> None:
        if not value or value in found:
            return
        if value.startswith('127.') or value == '0.0.0.0':
            return
        found.append(value)

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    add(_route_local_ip('8.8.8.8'))
    return found


def _encode_json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _parse_json(data: bytes):
    try:
        value = json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _encode_frame(message: dict) -> bytes:
    """Encode one message; refuse frames outside ``1 .. MAX_FRAME_BYTES``."""
    payload = _encode_json(message)
    if not 1 <= len(payload) <= MAX_FRAME_BYTES:
        raise NetError('消息有 %d 字节，超过 %d 字节上限，无法发送。'
                       % (len(payload), MAX_FRAME_BYTES))
    return HEADER.pack(len(payload)) + payload


def _close_socket(sock) -> None:
    if sock is None:
        return
    try:
        sock.close()
    except OSError:
        pass


class _FrameReader:
    """Incremental frame decoder for a socket in timeout mode.

    Only the handshake and then the reader thread touch it, never both at once,
    so a plain list is enough for the decoded queue.
    """

    def __init__(self):
        self._buffer = bytearray()
        self.messages = []

    def feed(self, chunk: bytes) -> None:
        """Append raw bytes and decode every complete frame into ``messages``."""
        self._buffer += chunk
        while len(self._buffer) >= HEADER.size:
            length = HEADER.unpack(self._buffer[:HEADER.size])[0]
            if not 1 <= length <= MAX_FRAME_BYTES:
                raise NetError('收到的数据帧长度不合法（%d 字节），连接已断开。' % length)
            if len(self._buffer) < HEADER.size + length:
                return
            payload = bytes(self._buffer[HEADER.size:HEADER.size + length])
            del self._buffer[:HEADER.size + length]
            try:
                message = json.loads(payload.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise NetError('收到的数据帧不是合法的 UTF-8 JSON，连接已断开。') from exc
            if not isinstance(message, dict) or not isinstance(message.get('t'), str):
                raise NetError('收到的数据帧缺少 t 字段，连接已断开。')
            self.messages.append(message)

    def pop(self):
        return self.messages.pop(0) if self.messages else None


def _send_frame(sock, message: dict, deadline: float | None = None) -> None:
    """Write one complete frame; used by the handshake before threads exist."""
    data = _encode_frame(message)
    if deadline is None:
        sock.sendall(data)
        return
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise NetError('发送握手消息超时。')
    sock.settimeout(max(0.05, remaining))
    sock.sendall(data)


def _read_message(sock, reader: _FrameReader, deadline: float | None,
                  stopping: threading.Event | None = None,
                  what: str = '对方的响应') -> dict:
    """Block (polling) until one frame arrives or ``deadline`` passes."""
    while True:
        message = reader.pop()
        if message is not None:
            return message
        if stopping is not None and stopping.is_set():
            raise NetError('连接已关闭。')
        if deadline is None:
            wait = POLL_SECONDS
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NetError('等待%s超时。' % what)
            wait = min(POLL_SECONDS, remaining)
        sock.settimeout(max(0.005, wait))
        try:
            chunk = sock.recv(MAX_FRAME_BYTES + HEADER.size)
        except socket.timeout:
            continue
        except OSError as exc:
            raise NetError('与对方的连接已中断。') from exc
        if not chunk:
            raise NetError('对方关闭了连接。')
        reader.feed(chunk)


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------

class Session:
    """One side of a LAN versus connection.

    ``status`` is ``'waiting'`` (host, no guest yet), ``'connected'``,
    ``'closed'`` or ``'error'``.  ``error`` carries a :class:`NetError` with a
    Chinese message whenever the link ended for a reason worth showing.
    """

    def __init__(self, role: str, code: str = '', name: str | None = None,
                 heartbeat: float | None = None, timeout: float | None = None):
        if role not in ('host', 'guest'):
            raise ValueError('role 必须是 host 或 guest。')
        self.role = role
        self.code = str(code or '').strip().upper()
        self.name = (name or '').strip()[:NAME_LIMIT] or _machine_name()
        self.port = 0
        # A host sits in 'waiting' until a guest arrives; a guest starts there
        # too but turns 'connected' before :func:`join` returns.
        self.status = 'waiting'
        self.error = None
        self.peer_name = ''
        # The heartbeat/timeout knobs are read from the module constants at
        # construction time so tests can monkeypatch them before creating a room.
        self._heartbeat = HEARTBEAT_SECONDS if heartbeat is None else float(heartbeat)
        self._timeout = TIMEOUT_SECONDS if timeout is None else float(timeout)
        # Captured here so a patched module constant cannot be reverted behind
        # the discovery thread's back before it binds.
        self._discovery_port = int(DISCOVERY_PORT)
        self._sock = None
        self._listener = None
        self._discovery_sock = None
        self._reader = _FrameReader()
        # Frames written before the connection exists, capped at PENDING_LIMIT.
        self._pending = queue.Queue(maxsize=PENDING_LIMIT)
        self._outbuf = bytearray()
        # Messages received but not drained yet, capped at INBOX_LIMIT.
        self._inbox = queue.Queue(maxsize=INBOX_LIMIT)
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._close_lock = threading.Lock()
        self._threads = []
        self._last_recv = time.monotonic()

    # -- public API ---------------------------------------------------------

    def send(self, message: dict) -> None:
        """Send one message.  Thread safe; I/O failures never raise."""
        frame = self._frame(message)
        with self._lock:
            if self._closed.is_set() or self.status == 'error':
                return
            if self.status != 'connected' or self._sock is None:
                # Not connected yet: hold the frame, dropping the oldest when full.
                self._queue_pending(frame)
                return
            if not self._pending.empty() or self._outbuf:
                # Keep wire order: never jump ahead of buffered frames.
                self._queue_pending(frame)
                pending = self._take_pending()
                while pending is not None:
                    self._outbuf += pending
                    pending = self._take_pending()
                if len(self._outbuf) > MAX_OUTBUF_BYTES:
                    self._fail('发送缓冲已满，对方的网络太慢，连接已断开。')
                    return
            else:
                self._outbuf += frame
            self._flush_locked()

    def drain(self) -> list[dict]:
        """Return every message received so far, in arrival order.  Never blocks."""
        items = []
        while True:
            try:
                items.append(self._inbox.get_nowait())
            except queue.Empty:
                return items

    def close(self, reason: str = '') -> None:
        """Close the session.  Idempotent; stops all threads in under a second.

        ``reason`` is only sent to the peer inside the ``bye`` frame: a local
        close is not an error, so ``self.error`` stays untouched.
        """
        with self._close_lock:
            first = not self._closed.is_set()
            self._closed.set()
        if first:
            self._send_farewell(reason)
        # Teardown and the thread join are safe to repeat: a session that already
        # failed still needs its workers reaped.
        self._teardown()
        self._join_threads()
        if not first:
            return
        with self._lock:
            if self.status != 'error':
                self.status = 'closed'

    def __repr__(self) -> str:
        return '<Session %s %s %s:%s %s>' % (
            self.role, self.code or '-', self.name, self.port, self.status)

    # -- outbound -----------------------------------------------------------

    def _frame(self, message: dict) -> bytes:
        if not isinstance(message, dict):
            raise ValueError('消息必须是 dict。')
        frame = dict(message)
        kind = frame.get('t')
        if not isinstance(kind, str) or not kind:
            raise ValueError('消息必须带有字符串类型的 t 字段。')
        # The transport owns the protocol version so callers cannot forget it.
        frame['v'] = PROTOCOL_VERSION
        try:
            return _encode_frame(frame)
        except NetError as exc:
            self._fail(str(exc))
            return b''

    def _queue_pending(self, frame: bytes) -> None:
        """Bounded pending queue: the oldest frame falls off when it is full."""
        while True:
            try:
                self._pending.put_nowait(frame)
                return
            except queue.Full:
                try:
                    self._pending.get_nowait()
                except queue.Empty:
                    return

    def _take_pending(self):
        try:
            return self._pending.get_nowait()
        except queue.Empty:
            return None

    def _queue_inbox(self, message: dict) -> None:
        """Bounded inbox: the oldest message falls off if nobody drains it."""
        while True:
            try:
                self._inbox.put_nowait(message)
                return
            except queue.Full:
                try:
                    self._inbox.get_nowait()
                except queue.Empty:
                    return

    def _flush_locked(self) -> None:
        """Write as many buffered bytes as the socket accepts right now."""
        while self._outbuf and self._sock is not None and not self._closed.is_set():
            try:
                sent = self._sock.send(self._outbuf)
            except socket.timeout:
                return
            except OSError:
                self._fail('发送数据失败，连接已断开。')
                return
            if sent <= 0:
                self._fail('对方已断开连接。')
                return
            del self._outbuf[:sent]

    def _send_farewell(self, reason: str) -> None:
        """Best effort farewell; a broken stream here is harmless."""
        with self._lock:
            sock = self._sock if self.status == 'connected' else None
            leftover = bytes(self._outbuf)
            self._outbuf.clear()
        if sock is None:
            return
        message = {'t': 'bye', 'v': PROTOCOL_VERSION, 'why': reason}
        try:
            sock.settimeout(0.2)
            if leftover:
                sock.sendall(leftover)
            sock.sendall(_encode_frame(message))
        except (OSError, NetError):
            pass

    def _send_control(self, message: dict) -> None:
        try:
            self.send(message)
        except ValueError:
            pass

    # -- inbound ------------------------------------------------------------

    def _dispatch(self, message: dict) -> None:
        kind = message['t']
        self._last_recv = time.monotonic()
        if kind == 'ping':
            self._send_control({'t': 'pong'})
            return
        if kind == 'pong':
            return
        version = message.get('v')
        if version is not None and version != PROTOCOL_VERSION:
            self._fail('对方的协议版本不一致（需要 v%d）。' % PROTOCOL_VERSION)
            return
        if kind == 'bye':
            self._expire(message.get('why') or '对方断开了连接。')
            return
        self._queue_inbox(message)

    def _read_loop(self) -> None:
        while not self._closed.is_set():
            sock = self._sock
            if sock is None:
                return
            try:
                chunk = sock.recv(MAX_FRAME_BYTES + HEADER.size)
            except socket.timeout:
                continue
            except OSError:
                if not self._closed.is_set():
                    self._expire('与对方的连接已断开。')
                return
            if not chunk:
                if not self._closed.is_set():
                    self._expire('对方关闭了连接。')
                return
            try:
                self._reader.feed(chunk)
            except NetError as exc:
                self._fail(str(exc))
                return
            while not self._closed.is_set():
                message = self._reader.pop()
                if message is None:
                    break
                self._dispatch(message)

    def _heartbeat_loop(self) -> None:
        tick = max(0.02, min(POLL_SECONDS, self._heartbeat * 0.5))
        last_ping = 0.0
        while not self._closed.is_set():
            self._closed.wait(tick)
            if self._closed.is_set():
                return
            if self.status != 'connected':
                continue
            now = time.monotonic()
            if now - self._last_recv > self._timeout:
                self._expire('与对方的连接超时：超过 %.3g 秒没有收到任何数据，已断开。'
                             % self._timeout)
                return
            if now - last_ping >= self._heartbeat:
                last_ping = now
                self._send_control({'t': 'ping'})
            with self._lock:
                self._flush_locked()

    # -- lifecycle ----------------------------------------------------------

    def _adopt(self, sock) -> None:
        """Take ownership of a handshaken socket and start the worker threads."""
        sock.settimeout(POLL_SECONDS)
        with self._lock:
            self._sock = sock
            self._last_recv = time.monotonic()
            self.status = 'connected'
            pending = self._take_pending()
            while pending is not None:
                self._outbuf += pending
                pending = self._take_pending()
        self._start_thread('read', self._read_loop)
        self._start_thread('heartbeat', self._heartbeat_loop)
        with self._lock:
            self._flush_locked()

    def _start_thread(self, suffix: str, target) -> None:
        thread = threading.Thread(
            target=target, name='dib-netplay-%s-%s' % (self.role, suffix), daemon=True)
        with self._lock:
            self._threads.append(thread)
        thread.start()

    def _teardown(self) -> None:
        with self._lock:
            sock, self._sock = self._sock, None
            listener, self._listener = self._listener, None
            udp, self._discovery_sock = self._discovery_sock, None
            self._outbuf.clear()
            pending = self._take_pending()
            while pending is not None:
                pending = self._take_pending()
        for item in (sock, listener, udp):
            if item is None:
                continue
            try:
                item.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            _close_socket(item)

    def _join_threads(self) -> None:
        current = threading.current_thread()
        deadline = time.monotonic() + 0.9
        for thread in list(self._threads):
            if thread is current:
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            thread.join(remaining)

    def _fail(self, message: str) -> None:
        """Protocol or I/O failure: ``status='error'`` and drop the link."""
        with self._lock:
            if self.error is None:
                self.error = NetError(message)
            self.status = 'error'
            self._closed.set()
        self._teardown()

    def _expire(self, message: str) -> None:
        """Peer disconnects, timeout or a ``bye`` frame: ``status='closed'``."""
        with self._lock:
            if self._closed.is_set():
                return
            if self.error is None:
                self.error = NetError(message)
            if self.status != 'error':
                self.status = 'closed'
            self._closed.set()
        self._teardown()

    # -- host side ----------------------------------------------------------

    def _serve_handshake(self, conn) -> None:
        """Validate the greeting.  Raises NetError when the guest is refused."""
        conn.settimeout(POLL_SECONDS)
        reader = _FrameReader()
        deadline = time.monotonic() + max(1.0, self._timeout)
        hello = _read_message(conn, reader, deadline, self._closed, '客机的握手消息')
        if hello.get('t') != 'hello':
            self._reject(conn, '握手消息不正确')
            raise NetError('握手消息不正确')
        if hello.get('v') != PROTOCOL_VERSION:
            self._reject(conn, '协议版本不一致')
            raise NetError('协议版本不一致')
        if str(hello.get('code') or '').strip().upper() != self.code:
            self._reject(conn, '房间码不对')
            raise NetError('房间码不对')
        peer_name = str(hello.get('name') or '').strip()[:NAME_LIMIT]
        self.peer_name = peer_name or '对手'
        _send_frame(conn, {'t': 'welcome', 'v': PROTOCOL_VERSION, 'name': self.name},
                    deadline=time.monotonic() + 2.0)
        # Hand any bytes that already arrived in the same packet to the reader.
        self._reader = reader
        self._adopt(conn)

    def _reject(self, conn, why: str) -> None:
        try:
            _send_frame(conn, {'t': 'reject', 'v': PROTOCOL_VERSION, 'why': why},
                        deadline=time.monotonic() + 1.0)
        except (OSError, NetError):
            pass
        _close_socket(conn)

    def _accept_loop(self) -> None:
        while not self._closed.is_set():
            listener = self._listener
            if listener is None:
                return
            try:
                conn, _addr = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self._closed.is_set():
                    self._fail('房间监听失败，无法接受新的连接。')
                return
            if self._closed.is_set():
                _close_socket(conn)
                return
            try:
                self._serve_handshake(conn)
            except (NetError, OSError):
                # A mistyped room code must not burn the room: keep listening.
                _close_socket(conn)
                continue
            return

    def _discovery_loop(self) -> None:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(('', self._discovery_port))
            sock.settimeout(POLL_SECONDS)
        except OSError:
            # Discovery is a convenience; the room still works without it.
            _close_socket(sock)
            return
        with self._lock:
            self._discovery_sock = sock
        try:
            while not self._closed.is_set() and self.status == 'waiting':
                try:
                    data, addr = sock.recvfrom(MAX_FRAME_BYTES)
                except socket.timeout:
                    continue
                except OSError:
                    return
                probe = _parse_json(data)
                if not probe or probe.get('t') != 'discover':
                    continue
                if probe.get('v') != PROTOCOL_VERSION:
                    continue
                reply = {'t': 'room', 'v': PROTOCOL_VERSION,
                         'host': _route_local_ip(addr[0]) or addr[0],
                         'port': self.port, 'room': self.code, 'name': self.name}
                try:
                    sock.sendto(_encode_json(reply), addr)
                except OSError:
                    continue
        finally:
            with self._lock:
                if self._discovery_sock is sock:
                    self._discovery_sock = None
            _close_socket(sock)


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------

def start_host(code: str, port: int = GAME_PORT, discovery: bool = True) -> Session:
    """Open a room and return immediately.  The guest is accepted in the background."""
    session = Session('host', code=code)
    listener = None
    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('', int(port)))
        listener.listen(4)
        listener.settimeout(POLL_SECONDS)
    except OSError as exc:
        _close_socket(listener)
        if int(port) == 0:
            raise NetError('无法创建房间：系统没有分配到可用端口。') from exc
        raise NetError('端口 %d 已被占用，无法创建房间。' % int(port)) from exc
    session._listener = listener
    session.port = listener.getsockname()[1]
    if discovery:
        session._start_thread('discovery', session._discovery_loop)
    session._start_thread('accept', session._accept_loop)
    return session


def join(host: str, port: int = GAME_PORT, code: str = '', timeout: float = 6.0) -> Session:
    """Join a room; blocks until the handshake finishes and raises NetError on failure."""
    if not host:
        raise NetError('房间地址不正确。')
    session = Session('guest', code=code)
    deadline = time.monotonic() + max(0.5, float(timeout))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(max(0.05, min(2.0, deadline - time.monotonic())))
        sock.connect((host, int(port)))
        sock.settimeout(POLL_SECONDS)
        reader = _FrameReader()
        _send_frame(sock, {'t': 'hello', 'v': PROTOCOL_VERSION,
                           'code': session.code, 'name': session.name}, deadline=deadline)
        reply = _read_message(sock, reader, deadline, None, '主机的响应')
        kind = reply.get('t')
        if kind == 'reject':
            why = str(reply.get('why') or '房间码不对')
            raise NetError(why if why.endswith('。') else why + '。')
        if kind != 'welcome':
            raise NetError('主机的响应不正确，无法加入房间。')
        if reply.get('v') != PROTOCOL_VERSION:
            raise NetError('协议版本不一致，无法加入房间。')
    except NetError:
        _close_socket(sock)
        raise
    except OSError as exc:
        _close_socket(sock)
        raise NetError('连接不到房间，请确认主机已开好房间，并且两台电脑在同一个局域网。') from exc
    except BaseException:
        _close_socket(sock)
        raise
    session.port = int(port)
    session.peer_name = str(reply.get('name') or '').strip()[:NAME_LIMIT] or '对手'
    session._reader = reader
    session._adopt(sock)
    return session


def discover(timeout: float = 1.5, address: str = '255.255.255.255',
             port: int = DISCOVERY_PORT) -> list[dict]:
    """Ask the LAN for rooms; returns them in arrival order, deduplicated.

    ``address`` may be a broadcast address or a concrete one such as
    ``'127.0.0.1'``, which is what the loopback tests use.
    """
    sock = None
    rooms = []
    probe = _encode_json({'t': 'discover', 'v': PROTOCOL_VERSION})
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(('', 0))
        sock.settimeout(POLL_SECONDS)
        try:
            sock.sendto(probe, (address, int(port)))
        except OSError:
            return rooms
        started = time.monotonic()
        deadline = started + max(0.0, float(timeout))
        # A single broadcast is easy to lose on Wi-Fi, so probe three times while
        # collecting; answers are deduplicated below.
        retries = (0.0, 0.3, 0.6)
        next_probe = 1
        seen = set()
        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                break
            if next_probe < len(retries) and now - started >= retries[next_probe]:
                try:
                    sock.sendto(probe, (address, int(port)))
                except OSError:
                    pass
                next_probe += 1
            sock.settimeout(min(POLL_SECONDS, max(0.005, remaining)))
            try:
                data, addr = sock.recvfrom(MAX_FRAME_BYTES)
            except socket.timeout:
                continue
            except OSError:
                break
            reply = _parse_json(data)
            if not reply or reply.get('t') != 'room':
                continue
            if reply.get('v') != PROTOCOL_VERSION:
                continue
            try:
                room_port = int(reply.get('port') or 0)
            except (TypeError, ValueError):
                continue
            entry = {'host': str(reply.get('host') or addr[0]),
                     'port': room_port,
                     'room': str(reply.get('room') or ''),
                     'name': str(reply.get('name') or ''),
                     'version': PROTOCOL_VERSION}
            key = (entry['host'], entry['port'], entry['room'])
            if room_port <= 0 or key in seen:
                continue
            seen.add(key)
            rooms.append(entry)
    except OSError:
        return rooms
    finally:
        _close_socket(sock)
    return rooms
