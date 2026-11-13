"""Transport layer tests: loopback only, temporary ports, no external network.

Every test uses ``127.0.0.1`` plus ports the OS hands out, so the suite is
hermetic and finishes in a couple of seconds.
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time
import unittest
from unittest import mock

from dib import netplay

LOOPBACK = '127.0.0.1'
ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


# ---------------------------------------------------------------------------
# raw wire helpers: independent of the module under test
# ---------------------------------------------------------------------------

def _recv_exact(sock, count):
    buffer = bytearray()
    while len(buffer) < count:
        chunk = sock.recv(count - len(buffer))
        if not chunk:
            raise AssertionError('socket closed after %d/%d bytes' % (len(buffer), count))
        buffer += chunk
    return bytes(buffer)


def _raw_send(sock, message):
    payload = json.dumps(message, ensure_ascii=False).encode('utf-8')
    sock.sendall(struct.pack('>I', len(payload)) + payload)


def _raw_recv(sock):
    length = struct.unpack('>I', _recv_exact(sock, 4))[0]
    return json.loads(_recv_exact(sock, length).decode('utf-8'))


def _free_udp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind((LOOPBACK, 0))
        return probe.getsockname()[1]


def _free_tcp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((LOOPBACK, 0))
        return probe.getsockname()[1]


class NetplayTestCase(unittest.TestCase):
    """Shared fixtures: track sessions, wait for status changes, collect frames."""

    def setUp(self):
        self.sessions = []

    def tearDown(self):
        for session in self.sessions:
            try:
                session.close()
            except Exception:      # teardown must never mask the real failure
                pass

    def _keep(self, session):
        self.sessions.append(session)
        return session

    def _host(self, code='ABCD', port=0, discovery=False):
        return self._keep(netplay.start_host(code=code, port=port, discovery=discovery))

    def _guest(self, host, code='ABCD', timeout=3.0):
        return self._keep(netplay.join(LOOPBACK, host.port, code=code, timeout=timeout))

    def _pair(self, code='ABCD'):
        host = self._host(code=code)
        guest = self._guest(host, code=code)
        self._wait_status(host, 'connected')
        return host, guest

    def _wait_status(self, session, expected, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if session.status == expected:
                return
            time.sleep(0.01)
        self.fail('%s %s status is %r, expected %r (error=%r)'
                  % (session.role, session.code, session.status, expected, session.error))

    def _collect(self, session, count, timeout=5.0):
        deadline = time.monotonic() + timeout
        received = []
        while len(received) < count and time.monotonic() < deadline:
            received.extend(session.drain())
            if len(received) >= count:
                break
            time.sleep(0.005)
        self.assertGreaterEqual(len(received), count,
                                'received %d of %d frames' % (len(received), count))
        return received[:count]

    def _wait_threads_gone(self, needle, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            alive = [t for t in threading.enumerate() if needle in t.name]
            if not alive:
                return
            time.sleep(0.02)
        self.fail('worker threads still alive: %r'
                  % [t.name for t in threading.enumerate() if needle in t.name])


# ---------------------------------------------------------------------------
# constants and helpers
# ---------------------------------------------------------------------------

class ConstantsTest(NetplayTestCase):
    def test_constants_match_contract(self):
        self.assertEqual(netplay.PROTOCOL_VERSION, 1)
        self.assertEqual(netplay.DISCOVERY_PORT, 45871)
        self.assertEqual(netplay.GAME_PORT, 45872)
        self.assertEqual(netplay.LOGICAL_BOUNDS, (0.0, 0.0, 1600.0, 900.0))
        self.assertEqual(netplay.VERSUS_BALLS, 15)
        self.assertEqual(netplay.STATE_HZ, 20.0)
        self.assertEqual(netplay.HEARTBEAT_SECONDS, 1.0)
        self.assertEqual(netplay.TIMEOUT_SECONDS, 5.0)
        self.assertEqual(netplay.MAX_FRAME_BYTES, 65536)
        self.assertTrue(issubclass(netplay.NetError, Exception))

    def test_make_code_uses_safe_alphabet(self):
        codes = [netplay.make_code() for _ in range(200)]
        for code in codes:
            self.assertEqual(len(code), 4)
            self.assertTrue(set(code) <= set(ALPHABET), code)
        self.assertGreaterEqual(len(set(codes)), 100)

    def test_local_addresses_excludes_loopback(self):
        addresses = netplay.local_addresses()
        self.assertIsInstance(addresses, list)
        for address in addresses:
            self.assertIsInstance(address, str)
            self.assertFalse(address.startswith('127.'), address)
        self.assertNotIn('127.0.0.1', addresses)


# ---------------------------------------------------------------------------
# handshake
# ---------------------------------------------------------------------------

class HandshakeTest(NetplayTestCase):
    def test_handshake_connects_and_exchanges_names(self):
        host = self._host(code='ABCD')
        self.assertEqual(host.role, 'host')
        self.assertGreater(host.port, 0)               # port=0 asked the OS
        self.assertEqual(host.status, 'waiting')
        guest = self._guest(host, code='abcd')         # lower case is normalised
        self.assertEqual(guest.role, 'guest')
        self.assertEqual(guest.status, 'connected')
        self.assertEqual(guest.port, host.port)
        self.assertEqual(guest.code, 'ABCD')
        self._wait_status(host, 'connected')
        self.assertTrue(host.peer_name)
        self.assertTrue(guest.peer_name)
        self.assertEqual(host.peer_name, guest.name)   # hello carried the guest name
        self.assertEqual(guest.peer_name, host.name)   # welcome carried the host name

    def test_wrong_code_is_rejected_and_room_stays_open(self):
        host = self._host(code='ABCD')
        with self.assertRaises(netplay.NetError) as caught:
            netplay.join(LOOPBACK, host.port, code='ZZZZ', timeout=2.0)
        self.assertIn('房间码', str(caught.exception))
        self.assertEqual(host.status, 'waiting')
        # A typo must not burn the room: the same host still accepts the real guest.
        guest = self._guest(host, code='ABCD')
        self._wait_status(host, 'connected')
        self.assertEqual(guest.status, 'connected')

    def test_wrong_protocol_version_is_rejected(self):
        host = self._host(code='ABCD')
        peer = socket.create_connection((LOOPBACK, host.port), timeout=3.0)
        try:
            peer.settimeout(3.0)
            _raw_send(peer, {'t': 'hello', 'v': 99, 'code': 'ABCD', 'name': 'PEER'})
            reply = _raw_recv(peer)
        finally:
            peer.close()
        self.assertEqual(reply['t'], 'reject')
        self.assertIn('版本', reply['why'])
        self.assertEqual(host.status, 'waiting')

    def test_join_unreachable_room_raises_net_error(self):
        port = _free_tcp_port()
        started = time.monotonic()
        with self.assertRaises(netplay.NetError):
            netplay.join(LOOPBACK, port, code='ABCD', timeout=1.0)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_failed_join_leaks_no_threads(self):
        host = self._host(code='ABCD')
        with self.assertRaises(netplay.NetError):
            netplay.join(LOOPBACK, host.port, code='WRNG', timeout=2.0)
        self.assertFalse([t for t in threading.enumerate()
                          if 'dib-netplay-guest' in t.name])


# ---------------------------------------------------------------------------
# data path
# ---------------------------------------------------------------------------

class DataPathTest(NetplayTestCase):
    def test_bidirectional_frames_keep_order(self):
        host, guest = self._pair()
        blob = 'x' * 60000                              # near the 64 KB frame limit
        host_out = [{'t': 'state', 'seq': index, 'state': {'turn': index % 2}}
                    for index in range(40)]
        guest_out = [{'t': 'shot', 'seq': index, 'power': index / 40}
                     for index in range(40)]
        for message in host_out:
            host.send(message)
        for message in guest_out:
            guest.send(message)
        host.send({'t': 'state', 'seq': 40, 'state': {'blob': blob}})

        seen_by_guest = self._collect(guest, 41)
        seen_by_host = self._collect(host, 40)

        self.assertEqual([m['seq'] for m in seen_by_guest], list(range(41)))
        self.assertEqual(seen_by_guest[-1]['state']['blob'], blob)
        self.assertEqual(len(seen_by_guest[-1]['state']['blob'].encode('utf-8')), 60000)
        self.assertEqual([m['seq'] for m in seen_by_host], list(range(40)))
        self.assertEqual(seen_by_host[7]['power'], guest_out[7]['power'])
        for message in seen_by_guest + seen_by_host:
            self.assertEqual(message['v'], netplay.PROTOCOL_VERSION)
        self.assertEqual(guest.drain(), [])
        self.assertEqual(host.drain(), [])

    def test_send_before_connect_is_queued_and_capped(self):
        host = self._host(code='ABCD')
        for index in range(40):                         # queue limit is 32 frames
            host.send({'t': 'state', 'seq': index, 'state': {}})
        guest = self._guest(host, code='ABCD')
        self._wait_status(host, 'connected')
        seen = self._collect(guest, 32)
        self.assertEqual([m['seq'] for m in seen], list(range(8, 40)))
        self.assertEqual(guest.drain(), [])

    def test_wire_format_is_length_prefixed_utf8_json(self):
        host = self._host(code='ABCD')
        peer = socket.create_connection((LOOPBACK, host.port), timeout=3.0)
        try:
            peer.settimeout(3.0)
            _raw_send(peer, {'t': 'hello', 'v': 1, 'code': 'ABCD', 'name': 'PEER'})
            welcome = _raw_recv(peer)
            self.assertEqual(welcome['t'], 'welcome')
            self.assertEqual(welcome['v'], netplay.PROTOCOL_VERSION)
            self.assertEqual(welcome['name'], host.name)
            self._wait_status(host, 'connected')

            host.send({'t': 'nack', 'seq': 7, 'why': '还没轮到你'})
            length = struct.unpack('>I', _recv_exact(peer, 4))[0]
            payload = _recv_exact(peer, length)
        finally:
            peer.close()
        self.assertEqual(length, len(payload))
        self.assertLessEqual(length, netplay.MAX_FRAME_BYTES)
        self.assertIn('还没轮到你'.encode('utf-8'), payload)   # UTF-8, not \u escapes
        message = json.loads(payload.decode('utf-8'))
        self.assertEqual(message['t'], 'nack')
        self.assertEqual(message['seq'], 7)
        self.assertEqual(message['why'], '还没轮到你')
        self.assertEqual(message['v'], netplay.PROTOCOL_VERSION)

    def test_frames_can_be_pipelined_and_split(self):
        host = self._host(code='ABCD')
        peer = socket.create_connection((LOOPBACK, host.port), timeout=3.0)
        try:
            peer.settimeout(3.0)
            _raw_send(peer, {'t': 'hello', 'v': 1, 'code': 'ABCD', 'name': 'PEER'})
            self.assertEqual(_raw_recv(peer)['t'], 'welcome')
            first = json.dumps({'t': 'restart', 'v': 1}).encode('utf-8')
            second = json.dumps({'t': 'shot', 'v': 1, 'seq': 2}).encode('utf-8')
            # two complete frames inside one write
            peer.sendall(struct.pack('>I', len(first)) + first
                         + struct.pack('>I', len(second)) + second)
            third = json.dumps({'t': 'state', 'v': 1, 'ms': 1}).encode('utf-8')
            frame = struct.pack('>I', len(third)) + third
            # ... and one frame torn in half by the network
            peer.sendall(frame[:3])
            time.sleep(0.05)
            peer.sendall(frame[3:])
            seen = self._collect(host, 3)
        finally:
            peer.close()
        self.assertEqual([m['t'] for m in seen], ['restart', 'shot', 'state'])
        self.assertEqual(seen[1]['seq'], 2)

    def test_oversize_frame_marks_error_and_disconnects(self):
        host = self._host(code='ABCD')
        peer = socket.create_connection((LOOPBACK, host.port), timeout=3.0)
        try:
            peer.settimeout(3.0)
            _raw_send(peer, {'t': 'hello', 'v': 1, 'code': 'ABCD', 'name': 'PEER'})
            self.assertEqual(_raw_recv(peer)['t'], 'welcome')
            self._wait_status(host, 'connected')
            peer.sendall(struct.pack('>I', netplay.MAX_FRAME_BYTES + 1))
            self._wait_status(host, 'error')
            self.assertIsInstance(host.error, netplay.NetError)
            self.assertIn('长度', str(host.error))
        finally:
            peer.close()
        self._wait_threads_gone('dib-netplay-host')


# ---------------------------------------------------------------------------
# heartbeat, disconnects, close
# ---------------------------------------------------------------------------

class LifecycleTest(NetplayTestCase):
    def test_silent_peer_times_out_to_closed(self):
        with mock.patch.object(netplay, 'HEARTBEAT_SECONDS', 0.05), \
                mock.patch.object(netplay, 'TIMEOUT_SECONDS', 0.3):
            host = self._host(code='ABCD')
            peer = socket.create_connection((LOOPBACK, host.port), timeout=3.0)
            try:
                peer.settimeout(3.0)
                _raw_send(peer, {'t': 'hello', 'v': 1, 'code': 'ABCD', 'name': 'PEER'})
                self.assertEqual(_raw_recv(peer)['t'], 'welcome')
                self.assertEqual(host.peer_name, 'PEER')
                self._wait_status(host, 'closed', timeout=3.0)
                self.assertIsInstance(host.error, netplay.NetError)
                self.assertIn('超时', str(host.error))
            finally:
                peer.close()

    def test_peer_close_marks_session_closed(self):
        host, guest = self._pair()
        guest.close('先走了')
        self._wait_status(host, 'closed')
        self.assertIsInstance(host.error, netplay.NetError)
        self.assertIn('先走了', str(host.error))
        # A deliberate local close is not an error for the side that left.
        self.assertIsNone(guest.error)

    def test_close_is_idempotent_and_stops_threads(self):
        host, guest = self._pair()
        started = time.monotonic()
        host.close()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.0, 'close() took %.3fs' % elapsed)
        self.assertEqual(host.status, 'closed')
        self.assertIsNone(host.error)
        self._wait_threads_gone('dib-netplay-host', timeout=0.2)

        host.close()
        host.close(reason='再来一局')
        self.assertEqual(host.status, 'closed')

        guest.close()
        guest.close()
        self._wait_threads_gone('dib-netplay-guest', timeout=0.2)
        self.assertEqual(guest.status, 'closed')

    def test_send_after_close_is_quietly_ignored(self):
        session = netplay.Session('host', code='ABCD')
        self.assertEqual(session.status, 'waiting')
        self.assertEqual(session.drain(), [])
        session.close()
        self.assertEqual(session.status, 'closed')
        session.send({'t': 'state', 'state': {}})
        session.send({'t': 'ping'})
        self.assertEqual(session.drain(), [])


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

class DiscoveryTest(NetplayTestCase):
    def test_discover_finds_listening_host_on_loopback(self):
        discovery_port = _free_udp_port()
        with mock.patch.object(netplay, 'DISCOVERY_PORT', discovery_port):
            host = self._host(code='ABCD', discovery=True)
            rooms = netplay.discover(timeout=0.6, address=LOOPBACK, port=discovery_port)
        self.assertTrue(rooms, 'discover() found no room')
        matches = [room for room in rooms if room['port'] == host.port]
        self.assertEqual(len(matches), 1, rooms)
        room = matches[0]
        self.assertEqual(room['host'], LOOPBACK)
        self.assertEqual(room['room'], 'ABCD')
        self.assertEqual(room['name'], host.name)
        self.assertEqual(room['version'], netplay.PROTOCOL_VERSION)
        # The answer is directly usable by the join dialog.
        guest = self._guest(host, code=room['room'])
        self._wait_status(host, 'connected')
        self.assertEqual(guest.status, 'connected')

    def test_discover_without_room_returns_empty(self):
        started = time.monotonic()
        rooms = netplay.discover(timeout=0.3, address=LOOPBACK, port=_free_udp_port())
        self.assertEqual(rooms, [])
        self.assertLess(time.monotonic() - started, 2.0)


if __name__ == '__main__':
    unittest.main()
