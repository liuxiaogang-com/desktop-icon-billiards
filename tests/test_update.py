import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from dib.update import (MAX_FEED_BYTES, fetch_update, parse_update, release_entries, version_tuple)


def feed(**overrides):
    info = {
        'version': '0.2.0',
        'download_url': 'https://example.com/DesktopIconBilliards-0.2.0-win-x64.exe',
        'notes': '新版本说明',
    }
    info.update(overrides)
    return info


def encoded(info):
    return json.dumps(info, ensure_ascii=False).encode('utf-8')


class VersionTupleTests(unittest.TestCase):
    def test_accepts_three_part_versions(self):
        self.assertEqual(version_tuple('0.2.10'), (0, 2, 10))
        self.assertEqual(version_tuple('65535.0.1'), (65535, 0, 1))

    def test_rejects_invalid_versions(self):
        for value in ('1.2', '1.2.3.4', 'v1.2.3', '1.-2.3', '', None, 123):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    version_tuple(value)


class ParseUpdateTests(unittest.TestCase):
    def test_returns_release_when_newer(self):
        result = parse_update(encoded(feed()), '0.1.0')
        self.assertEqual(result['version'], '0.2.0')
        self.assertEqual(result['download_url'], feed()['download_url'])
        self.assertEqual(result['notes'], '新版本说明')
        self.assertNotIn('history', result)

    def test_returns_none_when_current_or_newer(self):
        self.assertIsNone(parse_update(encoded(feed()), '0.2.0'))
        self.assertIsNone(parse_update(encoded(feed()), '0.3.0'))

    def test_accepts_utf8_bom(self):
        data = b'\xef\xbb\xbf' + encoded(feed())
        self.assertEqual(parse_update(data, '0.1.0')['version'], '0.2.0')

    def test_rejects_unsafe_download_urls(self):
        for url in ('http://example.com/app.exe', 'javascript:alert(1)', 'file:///tmp/a.exe',
                    'https://user:pass@example.com/app.exe', 'https:///app.exe', ''):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    parse_update(encoded(feed(download_url=url)), '0.1.0')

    def test_rejects_invalid_payloads(self):
        with self.assertRaises(ValueError):
            parse_update(b'[]', '0.1.0')
        with self.assertRaises(ValueError):
            parse_update(encoded(feed(version='1.2')), '0.1.0')
        with self.assertRaises(ValueError):
            parse_update(encoded(feed(notes='x' * 4001)), '0.1.0')
        with self.assertRaises(ValueError):
            parse_update(encoded(feed(notes=None)), '0.1.0')

    def test_history_is_validated_sorted_and_capped(self):
        history = [
            {'version': '0.1.2', 'notes': 'older'},
            {'version': '0.2.0', 'notes': 'duplicate of latest'},
            {'version': '0.3.0', 'notes': 'newer than latest'},
            {'version': 'bad', 'notes': 'invalid'},
            {'version': '0.1.2', 'notes': 'duplicate'},
            {'version': '0.1.9', 'notes': 'x' * 4001},
            {'version': '0.1.5', 'notes': 'newest history'},
        ]
        result = parse_update(encoded(feed(history=history)), '0.1.0')
        self.assertEqual([row['version'] for row in result['history']], ['0.1.5', '0.1.2'])
        self.assertEqual(result['history'][0]['notes'], 'newest history')

    def test_history_cannot_override_latest_download(self):
        info = feed(history=[{'version': '0.1.9', 'notes': 'old', 'download_url': 'https://evil.example/x'}])
        result = parse_update(encoded(info), '0.1.0')
        self.assertEqual(result['download_url'], feed()['download_url'])


class ReleaseEntriesTests(unittest.TestCase):
    def test_latest_first_then_numeric_order(self):
        info = feed(history=[
            {'version': '0.1.9', 'notes': 'a'},
            {'version': '0.1.10', 'notes': 'b'},
        ])
        entries = release_entries(info)
        self.assertEqual([row['version'] for row in entries], ['0.2.0', '0.1.10', '0.1.9'])

    def test_caps_display_entries_at_21(self):
        history = [{'version': f'0.0.{index}', 'notes': 'n'} for index in range(50)]
        self.assertEqual(len(release_entries(feed(history=history))), 21)


class _FeedHandler(BaseHTTPRequestHandler):
    payload = b'{}'

    def do_GET(self):  # noqa: N802 - http.server API
        self.server.request_path = self.path
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *args):
        pass


class FetchUpdateTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), _FeedHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/update.json'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def test_fetches_and_parses_over_local_http_for_tests(self):
        _FeedHandler.payload = encoded(feed())
        result = fetch_update(self.url, '0.1.0', allow_insecure=True)
        self.assertEqual(result['version'], '0.2.0')

    def test_http_is_rejected_in_production_mode(self):
        _FeedHandler.payload = encoded(feed())
        with self.assertRaises(ValueError):
            fetch_update(self.url, '0.1.0')

    def test_startup_request_includes_app_and_installed_version(self):
        _FeedHandler.payload = encoded(feed())
        fetch_update(self.url, '0.1.0', allow_insecure=True)
        self.assertEqual(parse_qs(urlsplit(self.server.request_path).query), {
            'app': ['DesktopIconBilliards'], 'version': ['0.1.0'], 'source': ['startup'],
        })

    def test_manual_request_preserves_other_parameters_and_replaces_tracking(self):
        _FeedHandler.payload = encoded(feed())
        url = self.url + '?channel=preview&empty=&source=startup&version=old&app=old'
        fetch_update(url, '0.1.5', source='manual', allow_insecure=True)
        self.assertEqual(parse_qs(urlsplit(self.server.request_path).query, keep_blank_values=True), {
            'channel': ['preview'], 'empty': [''], 'app': ['DesktopIconBilliards'],
            'version': ['0.1.5'], 'source': ['manual'],
        })

    def test_rejects_oversized_response(self):
        _FeedHandler.payload = b'x' * (MAX_FEED_BYTES + 1)
        with self.assertRaises(ValueError):
            fetch_update(self.url, '0.1.0', allow_insecure=True)

    def test_empty_url_is_a_noop(self):
        self.assertIsNone(fetch_update('', '0.1.0'))


if __name__ == '__main__':
    unittest.main()
