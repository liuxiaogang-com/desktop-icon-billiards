"""Ranking client: padding, signing, HTTP behaviour and the round state machine."""
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dib.rank import (MAX_RANKED_BALLS, MIN_BALLS, RankApi, RankController, RankError, RankSettings, ERROR_TEXT,
                      average_seconds, fill_sources, is_rankable, machine_hash, normalize_base_url, sign)
from dib.table import BilliardsMotion

SECRET = 'b' * 64
DEVICE = '11111111-2222-3333-4444-555555555555'
ROUND = '99999999-2222-3333-4444-555555555555'


class FakeServer:
    """Local HTTP server that records requests and replies with queued (status, body) pairs."""

    def __init__(self):
        self.requests = []
        self.replies = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self):
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length)
                outer.requests.append((self.command, self.path, dict(self.headers), body))
                status, payload, extra = outer.replies.pop(0) if outer.replies else (200, {}, {})
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                for key, value in extra.items():
                    self.send_header(key, value)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = _handle

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def reply(self, status=200, payload=None, **headers):
        self.replies.append((status, {} if payload is None else payload, headers))

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def wait_until(condition, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class HelperTests(unittest.TestCase):
    def test_fill_sources_pads_to_the_minimum_by_cycling_real_icons(self):
        self.assertEqual(fill_sources(0), [])
        self.assertEqual(fill_sources(MIN_BALLS), [])
        self.assertEqual(fill_sources(40), [])
        self.assertEqual(fill_sources(1), [0] * (MIN_BALLS - 1))
        extra = fill_sources(4)
        self.assertEqual(len(extra), MIN_BALLS - 4)
        self.assertEqual(extra[:5], [0, 1, 2, 3, 0])
        self.assertTrue(all(0 <= index < 4 for index in extra))

    def test_rankable_range(self):
        self.assertFalse(is_rankable(MIN_BALLS - 1))
        self.assertTrue(is_rankable(MIN_BALLS))
        self.assertTrue(is_rankable(MAX_RANKED_BALLS))
        self.assertFalse(is_rankable(MAX_RANKED_BALLS + 1))

    def test_average_uses_padded_ball_count(self):
        self.assertAlmostEqual(average_seconds(52340, 22), 2.379090909, places=6)

    def test_base_url_validation(self):
        self.assertEqual(normalize_base_url('https://rank.example.com/'), 'https://rank.example.com')
        self.assertEqual(normalize_base_url('http://127.0.0.1:8787'), 'http://127.0.0.1:8787')
        for bad in ('', 'http://rank.example.com', 'ftp://x.com', 'https://x.com/api', 'https://u:p@x.com',
                    'https://x.com?a=1', 'rank.example.com'):
            with self.subTest(url=bad):
                with self.assertRaises(ValueError):
                    normalize_base_url(bad)

    def test_signature_matches_the_server_implementation(self):
        # Vectors produced by the Node server's sign() in desktop-icon-billiards-server.
        body = b'{"round_id":"x","total_ms":40000,"icon_count":20,"shots":12}'
        self.assertEqual(sign(SECRET, 'POST', '/v1/round/finish', '1700000000', body),
                         '105fea036b3ab676ae6a694fe56f4739c9d0e33c977c33c8b45340f5dac2c62c')
        self.assertEqual(sign('k', 'POST', '/v1/round/start', '1700000000', b''),
                         '6eb8784679f4889281c0747fb448fe65343548bafafd48268e47efb5f6326350')
        self.assertEqual(sign('k', 'POST', '/v1/device/register', '1700000000',
                              '{"machine_hash":"录"}'.encode('utf-8')),
                         'd264fb2b40958e7c1b64880573d2d655155ec1316fb981e5fed6c9a066c8c38d')

    def test_machine_hash_is_stable_salted_hex(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = RankSettings(Path(folder) / 'rank.json')
            first = machine_hash(settings)
            self.assertRegex(first, r'^[0-9a-f]{64}$')
            self.assertEqual(machine_hash(settings), first)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer()
        self.addCleanup(self.server.close)
        self.api = RankApi(self.server.url, '9.9.9', timeout=2)
        self.creds = {'device_id': DEVICE, 'secret': SECRET}

    def test_rankings_public_and_signed_with_personal_best_outside_top50(self):
        data = {'entries': [{'rank': 1, 'avg_ms': 1234, 'is_me': False}],
                'me': {'rank': 128, 'avg_ms': 9000, 'is_me': True},
                'generated_at': '2026-11-12T12:00:00Z'}
        for creds in (None, self.creds):
            self.server.reply(200, data)
            self.assertEqual(self.api.rankings(creds), data)
            method, path, headers, body = self.server.requests[-1]
            self.assertEqual((method, path, body), ('GET', '/v1/rankings', b''))
            if creds:
                self.assertEqual(headers['X-Signature'], sign(SECRET, method, path, headers['X-Timestamp'], body))
            else:
                self.assertNotIn('X-Device-Id', headers)

    def test_rankings_rejects_invalid_rows_and_timestamps(self):
        good = {'entries': [], 'me': None, 'generated_at': '2026-11-12T12:00:00Z'}
        for changes in ({'entries': [{'rank': 1, 'avg_ms': -1, 'is_me': False}]},
                        {'me': {'rank': 0, 'avg_ms': 1000, 'is_me': True}},
                        {'generated_at': 'not a date'}):
            self.server.reply(200, {**good, **changes})
            with self.assertRaises(RankError) as caught:
                self.api.rankings()
            self.assertEqual(caught.exception.code, 'bad_response')

    def test_register_posts_only_the_hash(self):
        self.server.reply(200, {'device_id': DEVICE, 'secret': SECRET})
        self.assertEqual(self.api.register('a' * 64), (DEVICE, SECRET))
        method, path, headers, body = self.server.requests[0]
        self.assertEqual((method, path), ('POST', '/v1/device/register'))
        self.assertEqual(json.loads(body), {'machine_hash': 'a' * 64})
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertNotIn('X-Signature', headers)
        self.assertEqual(headers['User-Agent'], 'DesktopIconBilliards/9.9.9')

    def test_signed_request_carries_a_valid_signature(self):
        self.server.reply(200, {'round_id': ROUND})
        self.assertEqual(self.api.start_round(self.creds), ROUND)
        _, path, headers, body = self.server.requests[0]
        self.assertEqual(path, '/v1/round/start')
        self.assertEqual(headers['X-Device-Id'], DEVICE)
        timestamp = headers['X-Timestamp']
        self.assertLess(abs(time.time() - int(timestamp)), 5)
        self.assertEqual(headers['X-Signature'], sign(SECRET, 'POST', '/v1/round/start', timestamp, body))

    def test_finish_sends_integers_only(self):
        self.server.reply(200, {'ok': True})
        self.api.finish_round(self.creds, ROUND, 40000.0, 20, 12)
        body = json.loads(self.server.requests[0][3])
        self.assertEqual(body, {'round_id': ROUND, 'total_ms': 40000, 'icon_count': 20, 'shots': 12})
        self.assertIsInstance(body['total_ms'], int)

    def test_error_codes_come_from_the_server(self):
        self.server.reply(422, {'error': 'too_fast'})
        with self.assertRaises(RankError) as caught:
            self.api.finish_round(self.creds, ROUND, 1, 20, 1)
        self.assertEqual((caught.exception.code, caught.exception.status), ('too_fast', 422))

    def test_bad_bodies_network_errors_and_redirects(self):
        self.server.reply(200, b'not json')
        with self.assertRaises(RankError) as caught:
            self.api.start_round(self.creds)
        self.assertEqual(caught.exception.code, 'bad_response')

        self.server.reply(200, {'device_id': 5})
        with self.assertRaises(RankError):
            self.api.register('a' * 64)

        self.server.reply(302, {}, Location='http://127.0.0.1:1/elsewhere')
        with self.assertRaises(RankError):
            self.api.start_round(self.creds)
        self.assertEqual(len(self.server.requests), 3)  # the redirect target was never contacted

        self.server.close()
        with self.assertRaises(RankError) as caught:
            self.api.start_round(self.creds)
        self.assertEqual(caught.exception.code, 'network')

    def test_oversized_response_is_rejected(self):
        self.server.reply(200, b'{"x":"' + b'a' * 70000 + b'"}')
        with self.assertRaises(RankError) as caught:
            self.api.start_round(self.creds)
        self.assertEqual(caught.exception.code, 'bad_response')


class FakeApi:
    base = 'https://rank.example.com'

    def __init__(self):
        self.calls = []
        self.start_error = None
        self.finish_error = None
        self.unauthorized_once = False
        self.registered = 0
        self.lock = threading.Lock()

    def register(self, machine):
        with self.lock:
            self.registered += 1
            self.calls.append(('register', machine))
            return DEVICE, f'secret-{self.registered}'

    def start_round(self, creds):
        with self.lock:
            self.calls.append(('start', creds['secret']))
            if self.unauthorized_once:
                self.unauthorized_once = False
                raise RankError('unauthorized', 401)
            if self.start_error:
                raise self.start_error
            return ROUND

    def finish_round(self, creds, round_id, total_ms, icon_count, shots):
        with self.lock:
            self.calls.append(('finish', round_id, total_ms, icon_count, shots))
            if self.finish_error:
                raise self.finish_error
            return {'ok': True, 'avg_ms': total_ms / icon_count, 'is_personal_best': True,
                    'round': {'rank': 1204, 'percentile': 87}, 'best': {'rank': 860, 'percentile': 91, 'avg_ms': 2000},
                    'total_players': 10532}

    def names(self):
        with self.lock:
            return [call[0] for call in self.calls]


def play_round(game, start=100.0, duration=40.0):
    """Take a shot and clear the table the way the real game would."""
    game.step(.01, start - 1)
    game.shoot(1, start)
    game.state = 'rolling'
    for icon, pocket in zip(game.icons, game.pockets * 30):
        icon.x, icon.y = pocket
    game.step(.01, start + duration)
    assert game.state == 'finished', game.state


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.settings = RankSettings(Path(self.folder.name) / 'rank.json')
        self.api = FakeApi()
        self.rank = RankController(self.api, self.settings, hash_function=lambda settings: 'h' * 64)
        self.addCleanup(self.rank.close)
        self.game = BilliardsMotion((0, 0, 1600, 1000), [(64, 64)] * MIN_BALLS)

    def observe_until(self, condition, game=None):
        game = game or self.game
        def check():
            self.rank.observe(game)
            return condition()
        self.assertTrue(wait_until(check), self.rank.status)

    def test_nothing_happens_while_ranking_is_off(self):
        play_round(self.game)
        self.rank.observe(self.game)
        time.sleep(.1)
        self.rank.observe(self.game)
        self.assertEqual(self.api.calls, [])
        self.assertEqual(self.rank.lines(self.game),
                         ['本局平均 2.67 秒/颗（15 颗）', '开启「全球排名」可与其他玩家比较'])

    def test_unconfigured_ranking_is_inert_but_still_shows_the_average(self):
        rank = RankController(None, None)
        play_round(self.game)
        rank.observe(self.game)
        self.assertFalse(rank.available or rank.enabled)
        self.assertEqual(rank.lines(self.game), ['本局平均 2.67 秒/颗（15 颗）'])
        rank.close()

    def test_full_round_registers_starts_and_submits_integer_milliseconds(self):
        self.rank.set_enabled(True)
        self.assertEqual(self.rank.lines(self.game), ['全球排名已开启 · 清台后提交成绩'])
        self.observe_until(lambda: self.rank.round_id == ROUND)
        self.assertEqual(self.api.names(), ['register', 'start'])
        self.assertEqual(self.settings.get('device_id'), DEVICE)
        self.assertEqual(self.settings.get('server'), 'https://rank.example.com')
        play_round(self.game, start=100.0, duration=40.0123)
        self.observe_until(lambda: len(self.rank.status) == 2)
        finish = [call for call in self.api.calls if call[0] == 'finish']
        self.assertEqual(finish, [('finish', ROUND, 40012, MIN_BALLS, 1)])
        self.assertEqual(self.rank.lines(self.game),
                         ['本局平均 2.67 秒/颗（15 颗）', '本局全球第 1,204 名 · 超过 87% 的玩家',
                          '新的个人最佳 · 个人最佳第 860 名'])
        # The result is submitted once, however often the UI polls.
        for _ in range(5):
            self.rank.observe(self.game)
        time.sleep(.1)
        self.assertEqual(len([call for call in self.api.calls if call[0] == 'finish']), 1)

    def test_credentials_are_reused_across_controllers(self):
        self.rank.set_enabled(True)
        self.observe_until(lambda: self.rank.round_id == ROUND)
        again = RankController(self.api, RankSettings(self.settings.path), hash_function=lambda s: 'h' * 64)
        self.addCleanup(again.close)
        self.assertTrue(again.enabled)
        game = BilliardsMotion((0, 0, 1600, 1000), [(64, 64)] * MIN_BALLS)
        self.assertTrue(wait_until(lambda: (again.observe(game), again.round_id)[1] == ROUND))
        self.assertEqual(self.api.registered, 1)

    def test_restart_starts_a_new_round_and_forgets_the_old_result(self):
        self.rank.set_enabled(True)
        self.observe_until(lambda: self.rank.round_id == ROUND)
        play_round(self.game)
        self.observe_until(lambda: self.rank.status and self.rank.status[0].startswith('本局全球'))
        self.game.reset()
        self.rank.observe(self.game)
        self.assertEqual(self.rank.status, [])
        self.assertEqual(self.rank.lines(self.game), ['全球排名已开启 · 清台后提交成绩'])
        self.observe_until(lambda: self.api.names().count('start') == 2 and self.rank.round_id == ROUND)

    def test_too_many_icons_are_played_but_not_ranked(self):
        game = BilliardsMotion((0, 0, 3200, 1800), [(64, 64)] * (MAX_RANKED_BALLS + 1))
        self.rank.set_enabled(True)
        self.rank.observe(game)
        self.assertEqual(self.rank.lines(game), [f'图标超过 {MAX_RANKED_BALLS} 个，本局不计入排名'])
        play_round(game)
        self.rank.observe(game)
        time.sleep(.1)
        self.assertEqual(self.api.calls, [])
        self.assertEqual(self.rank.lines(game)[0].startswith('本局平均'), True)

    def test_enabling_mid_round_skips_that_round(self):
        self.game.step(.01, 1)
        self.game.shoot(1, 2.0)
        self.rank.set_enabled(True)
        self.rank.observe(self.game)
        self.assertEqual(self.rank.lines(self.game), ['本局开始后才开启排名，下一局起计入'])
        self.game.state = 'rolling'
        for icon, pocket in zip(self.game.icons, self.game.pockets * 30):
            icon.x, icon.y = pocket
        self.game.step(.01, 30)
        self.rank.observe(self.game)
        time.sleep(.1)
        self.assertEqual(self.api.calls, [])
        self.game.reset()
        self.rank.observe(self.game)
        self.observe_until(lambda: self.rank.round_id == ROUND)

    def test_offline_start_means_no_submission(self):
        self.api.start_error = RankError('network')
        self.rank.set_enabled(True)
        self.rank.observe(self.game)
        time.sleep(.2)
        play_round(self.game)
        self.rank.observe(self.game)
        self.assertEqual(self.rank.status, ['未能连接排名服务，本局不计入排名'])
        self.assertNotIn('finish', self.api.names())

    def test_first_shot_retries_a_round_that_failed_to_start(self):
        self.api.start_error = RankError('network')
        self.rank.set_enabled(True)
        self.rank.observe(self.game)
        self.assertTrue(wait_until(lambda: self.api.names().count('start') == 1))
        self.rank.observe(self.game)
        self.api.start_error = None
        self.game.step(.01, 1)
        self.game.shoot(1, 2.0)
        self.observe_until(lambda: self.rank.round_id == ROUND)

    def test_server_rejection_and_failures_are_reported(self):
        self.rank.set_enabled(True)
        self.observe_until(lambda: self.rank.round_id == ROUND)
        self.api.finish_error = RankError('too_fast', 422)
        play_round(self.game)
        self.observe_until(lambda: self.rank.status == [ERROR_TEXT['too_fast']])

        self.game.reset()
        self.observe_until(lambda: self.rank.round_id == ROUND)
        self.api.finish_error = RankError('network')
        play_round(self.game)
        self.observe_until(lambda: self.rank.status == [ERROR_TEXT['network']])

    def test_unauthorized_secret_triggers_one_re_registration(self):
        self.settings.update(device_id=DEVICE, secret='stale', server=self.api.base)
        self.api.unauthorized_once = True
        self.rank.set_enabled(True)
        self.observe_until(lambda: self.rank.round_id == ROUND)
        self.assertEqual(self.api.names(), ['start', 'register', 'start'])
        self.assertEqual(self.settings.get('secret'), 'secret-1')

    def test_changing_the_server_registers_again(self):
        self.settings.update(device_id=DEVICE, secret='old', server='https://other.example.com')
        self.rank.set_enabled(True)
        self.observe_until(lambda: self.rank.round_id == ROUND)
        self.assertEqual(self.api.names()[0], 'register')

    def test_view_follows_the_round_for_the_result_dialog(self):
        self.assertEqual(self.rank.view(self.game), {'phase': 'off'})
        self.assertEqual(RankController(None, None).view(self.game), {'phase': 'unavailable'})
        self.rank.set_enabled(True)
        self.observe_until(lambda: self.rank.round_id == ROUND)
        self.assertEqual(self.rank.view(self.game), {'phase': 'pending'})
        play_round(self.game)
        self.observe_until(lambda: self.rank.outcome is not None)
        view = self.rank.view(self.game)
        self.assertEqual(view['phase'], 'ok')
        self.assertEqual(view['rank_text'], '第 1,204 名')
        self.assertEqual(view['percentile_text'], '超过 87% 的玩家')
        self.assertEqual(view['best_text'], '新的个人最佳 · 个人最佳第 860 名')
        self.assertTrue(view['new_best'])

    def test_view_reports_errors_skips_and_the_unrankable(self):
        self.rank.set_enabled(True)
        self.observe_until(lambda: self.rank.round_id == ROUND)
        self.api.finish_error = RankError('too_fast', 422)
        play_round(self.game)
        self.observe_until(lambda: self.rank.outcome is not None)
        self.assertEqual(self.rank.view(self.game), {'phase': 'error', 'message': ERROR_TEXT['too_fast']})
        self.game.reset()
        self.rank.observe(self.game)
        self.assertEqual(self.rank.view(self.game)['phase'], 'pending')  # a new round forgets the old outcome

        late = BilliardsMotion((0, 0, 1600, 1000), [(64, 64)] * MIN_BALLS)
        late.step(.01, 1)
        late.shoot(1, 2.0)
        self.rank.set_enabled(False)
        self.rank.set_enabled(True)
        self.rank.observe(late)
        self.assertEqual(self.rank.view(late)['phase'], 'skipped')

        crowded = BilliardsMotion((0, 0, 3200, 1800), [(64, 64)] * (MAX_RANKED_BALLS + 1))
        fresh = RankController(self.api, self.settings, hash_function=lambda settings: 'h' * 64)
        self.addCleanup(fresh.close)
        fresh.observe(crowded)
        self.assertEqual(fresh.view(crowded)['phase'], 'skipped')

    def test_the_player_is_asked_once_and_can_decline(self):
        self.assertTrue(self.rank.needs_prompt)
        self.rank.mark_prompted()
        self.assertFalse(self.rank.needs_prompt)
        self.assertFalse(self.rank.enabled)  # asking never enables anything by itself
        self.assertFalse(RankSettings(self.settings.path).get('enabled'))
        self.assertTrue(RankSettings(self.settings.path).get('prompted'))

    def test_enabling_by_hand_also_counts_as_asked(self):
        self.rank.set_enabled(True)
        self.assertFalse(self.rank.needs_prompt)

    def test_an_unconfigured_ranking_never_prompts(self):
        self.assertFalse(RankController(None, None).needs_prompt)

    def test_disabling_stops_all_activity(self):
        self.rank.set_enabled(True)
        self.observe_until(lambda: self.rank.round_id == ROUND)
        self.rank.set_enabled(False)
        play_round(self.game)
        self.rank.observe(self.game)
        time.sleep(.1)
        self.assertNotIn('finish', self.api.names())
        self.assertFalse(self.settings.get('enabled'))


if __name__ == '__main__':
    unittest.main()
