"""Regression checks for arena tracking, notification delivery and club updates.

Run: python -m unittest discover -s tests -v
All sessions, Telegram calls and settings writes are isolated from real accounts.
"""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
from engine import MangaMinerBot
from seasonal import is_live_html
from tg_notifier import TelegramNotifier


def response(html='', status=200):
    return SimpleNamespace(status_code=status, text=html, headers={}, url='https://mangabuff.ru/notifications')


def rank(tier='Платина', index=3, division='Платина 4'):
    return dict(tier=tier, tier_index=index, icon='💎', division=division, full_name='💎 ' + tier)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.settings = {}
        self.get = patch('data_management.DataManager.get_setting', side_effect=lambda key, default=None: copy.deepcopy(self.settings.get(key, default)))
        self.put = patch('data_management.DataManager.set_setting', side_effect=lambda key, value: self.settings.update({key: copy.deepcopy(value)}))
        self.get.start()
        self.put.start()
        self.addCleanup(self.get.stop)
        self.addCleanup(self.put.stop)
        self.bot = object.__new__(MangaMinerBot)
        self.bot.log = Mock()
        self.bot.notifier = Mock()
        self.bot.session = Mock()
        self.bot.csrf_token = 'test'

    def test_rank_html_selects_current_tier(self):
        self.bot.session.get.return_value = response('''
            <div class="battle-home__rank-content"><strong>Бронза</strong></div>
            <div class="battle-home__rank-card--current">
              <span class="battle-home__rank-icon">💎</span>
              <div class="battle-home__rank-content"><strong>Платина</strong></div>
            </div><div class="battle-home__rank-top"><strong>Платина 4</strong></div>''')
        self.assertEqual(self.bot.get_current_arena_rank_info(), rank())

    def test_division_change_does_not_notify(self):
        self.bot.get_current_arena_rank_info = Mock(return_value=rank())
        self.bot.check_arena_rank_promotion()
        self.bot.get_current_arena_rank_info.return_value = rank(division='Платина 3')
        self.bot.check_arena_rank_promotion(3, 2, 1)
        self.bot.notifier.notify_rank_tier_promotion.assert_not_called()
        self.assertEqual(self.settings['arena_rank_tracker']['battles_in_tier'], 3)

    def test_first_batch_promotion_is_not_missed(self):
        self.bot.get_current_arena_rank_info = Mock(side_effect=[rank(), rank('Алмаз', 4, 'Алмаз 4')])
        self.bot.check_arena_rank_promotion()
        self.bot.check_arena_rank_promotion(3, 2, 1)
        call = self.bot.notifier.notify_rank_tier_promotion.call_args.kwargs
        self.assertEqual((call['battles'], call['wins'], call['losses']), (3, 2, 1))
        self.assertEqual(self.settings['arena_rank_tracker']['battles_in_tier'], 0)

    def test_rank_timeout_preserves_battle_counts(self):
        self.bot.get_current_arena_rank_info = Mock(side_effect=[rank(), None, rank('Алмаз', 4, 'Алмаз 4')])
        self.bot.check_arena_rank_promotion()
        self.bot.check_arena_rank_promotion(5, 3, 2)
        self.bot.check_arena_rank_promotion(2, 1, 1)
        call = self.bot.notifier.notify_rank_tier_promotion.call_args.kwargs
        self.assertEqual((call['battles'], call['wins'], call['losses']), (7, 4, 3))

    def test_initial_rank_timeout_preserves_counts(self):
        self.bot.get_current_arena_rank_info = Mock(side_effect=[None, rank()])
        self.bot.check_arena_rank_promotion(2, 1, 1)
        self.bot.check_arena_rank_promotion(3, 2, 1)
        self.assertEqual(self.settings['arena_rank_tracker']['battles_in_tier'], 5)

    def test_demotion_is_silent(self):
        self.bot.get_current_arena_rank_info = Mock(side_effect=[rank(), rank('Золото', 2, 'Золото 1')])
        self.bot.check_arena_rank_promotion()
        self.bot.check_arena_rank_promotion(2, 0, 2)
        self.bot.notifier.notify_rank_tier_promotion.assert_not_called()
        self.assertEqual(self.settings['arena_rank_tracker']['current_tier'], 'Золото')

    @patch('engine.time.sleep')
    def test_third_zero_reward_battle_is_counted(self, sleep):
        self.settings['arena_rank_mode'] = False
        self.bot._alive = Mock(return_value=True)
        self.bot._touch_heartbeat = Mock()
        self.bot._pause = Mock()
        self.bot.check_and_claim_quests = Mock()
        self.bot.check_arena_rank_promotion = Mock()
        self.bot.start_battle = Mock(return_value='battle-url')
        self.bot.check_battle_result = Mock(return_value=(0, True))
        self.bot.run_farm_loop(prepared=True)
        self.assertEqual(self.bot.check_battle_result.call_count, 3)
        self.assertEqual(self.bot.check_arena_rank_promotion.call_args.args, (3, 3, 0))
        self.assertEqual(self.bot.check_arena_rank_promotion.call_args_list[0].args, ())

    @patch('engine.time.sleep')
    def test_cleaner_preserves_chapters_comments_and_undelivered_trades(self, sleep):
        self.settings['notified_trade_ids'] = ['10']
        texts = {
            'chapter': 'Новая глава манги', 'comment': 'Ответил на ваш комментарий',
            'card': 'Вы получили новую карту', 'scroll': 'Свиток заточки',
            'animated': 'Добавлена анимированная карта', 'club': 'Достижение клуба: награда',
            'tower': 'Экспедиция в башне завершилась',
        }
        html = ''.join(f'<div class="notifications__item unread" data-id="{key}">{text}</div>' for key, text in texts.items())
        for key, trade in [('delivered', '10'), ('failed', '11')]:
            html += f'<div class="notifications__item unread" data-id="{key}">Обмен: новая карта<a href="/trades/{trade}">Обмен</a></div>'
        html += '<div class="notifications__item unread" data-id="unknown">Предложен обмен</div>'
        self.bot.session.get.return_value = response(html)
        self.bot.session.post.return_value = response()
        self.assertEqual(self.bot.clean_unwanted_notifications(active_trade_ids=['10', '11']), 6)
        read_ids = {call.args[0].split('/')[-2] for call in self.bot.session.post.call_args_list}
        self.assertEqual(read_ids, {'card', 'scroll', 'animated', 'club', 'tower', 'delivered'})

    def trade_setup(self, delivered):
        self.bot.session.get.side_effect = [
            response('<div class="notifications__item" data-id="1"><button data-trade-id="10"></button></div>'),
            response(), SimpleNamespace(status_code=200, json=lambda: {'other_user': {'name': 'User'}}),
        ]
        self.bot.notifier.notify_trade_offer.return_value = (delivered, 'test')

    def test_failed_telegram_trade_is_not_recorded_as_delivered(self):
        self.trade_setup(False)
        self.bot.check_trade_offers()
        self.assertEqual(self.settings.get('notified_trade_ids'), [])
        self.assertEqual(self.settings.get('tracked_active_trades'), {})

    def test_successful_telegram_trade_is_recorded(self):
        self.trade_setup(True)
        self.bot.check_trade_offers()
        self.assertEqual(self.settings['notified_trade_ids'], ['10'])
        self.assertTrue(self.bot.notifier.notify_trade_offer.call_args.kwargs['wait'])

    def test_club_limit_does_not_spend_crystals(self):
        soup = BeautifulSoup('<div class="club-crystals" data-contribute-url="/contribute"><div class="club-crystals__progress">Сегодня 10 / 10</div><button class="club-crystals__submit" data-item-id="1" data-version="1"></button></div>', 'html.parser')
        self.bot._process_crystal_contributions(soup, 'https://mangabuff.ru/clubs/test/boost')
        self.bot.session.post.assert_not_called()

    def test_unavailable_panel_does_not_overwrite_cache_with_zero(self):
        self.bot.validate_session = Mock(return_value=True)
        self.bot.session.get.return_value = response(status=403)
        self.bot.diamonds_balance = 0
        with patch('data_management.DataManager.save_reading_state') as save:
            self.assertFalse(self.bot.refresh_live_panel())
            save.assert_not_called()

    def test_partial_panel_does_not_replace_known_diamonds_with_zero(self):
        self.bot.validate_session = Mock(return_value=True)
        self.bot.session.get.side_effect = [response('<span class="main-mine__game-hits-left">15</span>'), response(status=403)]
        self.bot.diamonds_balance = 0
        self.bot._peek_tower_timer_str = Mock(return_value=None)
        with patch('data_management.DataManager.load_reading_state', return_value={'panel': {'diamonds': 123}}), patch('data_management.DataManager.save_reading_state') as save:
            self.assertTrue(self.bot.refresh_live_panel())
            self.assertEqual(save.call_args.args[0]['panel']['diamonds'], 123)

    def test_refresh_panel_import_does_not_start_network_activity(self):
        import importlib
        with patch('engine.MangaMinerBot') as constructor:
            import refresh_panel
            importlib.reload(refresh_panel)
            constructor.assert_not_called()

    def test_absent_club_card_does_not_notify(self):
        soup = BeautifulSoup('<div class="club-boost--content"><a href="/cards/123">Карта</a>У вас нет этой карты, чтобы пожертвовать</div>', 'html.parser')
        self.bot._process_card_contribution_alert(soup, 'https://mangabuff.ru/clubs/test/boost')
        self.bot.notifier.notify_club_card_available.assert_not_called()


class SeasonalWatchTests(unittest.TestCase):
    def setUp(self):
        self.settings = {}
        self.get = patch(
            'seasonal.DataManager.get_setting',
            side_effect=lambda key, default=None: copy.deepcopy(self.settings.get(key, default)),
        )
        self.put = patch(
            'seasonal.DataManager.set_setting',
            side_effect=lambda key, value: self.settings.update({key: copy.deepcopy(value)}),
        )
        self.get.start()
        self.put.start()
        self.addCleanup(self.get.stop)
        self.addCleanup(self.put.stop)
        self.bot = object.__new__(MangaMinerBot)
        self.bot.log = Mock()
        self.bot.notifier = Mock()
        self.bot.session = Mock()

    def test_stub_json_404_is_not_live(self):
        self.assertFalse(is_live_html(404, '{     "message": "" }'))
        self.assertFalse(is_live_html(200, '{     "message": "" }'))
        self.assertTrue(is_live_html(200, '<!DOCTYPE html><html><title>Хэллоуин</title>' + ('x' * 500)))

    def test_notifies_once_when_halloween_goes_live(self):
        html = '<!DOCTYPE html><html><head><title>Halloween</title></head><body>' + ('y' * 600)
        def fake_get(url, **kwargs):
            if url.endswith('/halloween'):
                return response(html, 200)
            return response('{     "message": "" }', 404)

        self.bot.session.get.side_effect = fake_get
        first = self.bot.run_seasonal_watch(force=True)
        self.assertEqual(first, ['/halloween'])
        self.bot.notifier.notify_seasonal_event_live.assert_called_once()
        second = self.bot.run_seasonal_watch(force=True)
        self.assertEqual(second, [])
        self.assertEqual(self.bot.notifier.notify_seasonal_event_live.call_count, 1)


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.notifier = object.__new__(TelegramNotifier)
        self.notifier.send_message = Mock(return_value=(True, 'OK'))
        self.notifier.send_photo = Mock(return_value=(True, 'OK'))
        self.notifier.send_media_group = Mock(return_value=(True, 'OK'))

    def test_seasonal_alert_uses_force_sound(self):
        self.notifier.notify_seasonal_event_live(['/halloween'], {'/halloween': 'Хэллоуин'})
        kwargs = self.notifier.send_message.call_args.kwargs
        self.assertTrue(kwargs.get('force_sound'))
        self.assertIn('/halloween', self.notifier.send_message.call_args.args[0])

    def test_club_card_name_is_escaped(self):
        self.notifier.notify_club_card_available(123, card_name='A < B & C')
        self.assertIn('A &lt; B &amp; C', self.notifier.send_message.call_args.args[0])

    def test_trade_sides_are_separate_and_all_images_are_sent(self):
        received = [f'r{i}'.encode() for i in range(12)]
        given = [b'g1', b'g2']
        self.assertTrue(self.notifier.notify_trade_offer('10', 'A < B', ['Card & name'], receive_photos=received, give_photos=given, wait=True)[0])
        groups = self.notifier.send_media_group.call_args_list
        self.assertEqual([call.args[0] for call in groups], [received[:10], received[10:], given])
        self.assertTrue(all(call.kwargs['wait'] for call in groups))
        self.assertIn('A &lt; B', self.notifier.send_message.call_args.args[0])
        self.assertIn('Card &amp; name', self.notifier.send_message.call_args.args[0])

    def test_trade_text_failure_prevents_image_sends(self):
        self.notifier.send_message.return_value = (False, 'timeout')
        self.assertEqual(self.notifier.notify_trade_offer('10', receive_photos=[b'r'], wait=True), (False, 'timeout'))
        self.notifier.send_media_group.assert_not_called()

    def test_trade_album_failure_is_reported(self):
        self.notifier.send_media_group.return_value = (False, 'bad image')
        self.assertEqual(self.notifier.notify_trade_offer('10', receive_photos=[b'r'], wait=True), (False, 'bad image'))


class LoginBackoffTests(unittest.TestCase):
    def setUp(self):
        self.bot = object.__new__(MangaMinerBot)
        self.bot.running = True
        self.bot.log = Mock()
        self.bot.notifier = Mock()
        self.bot.auth_log_buffer = []
        self.bot.validate_session = Mock(return_value=False)
        self.bot._validate_session_with_server = Mock(return_value=False)
        self.bot._sleep_alive = Mock()

    def test_failed_login_waits_and_retries_instead_of_exiting(self):
        def login():
            self.bot.auth_log_buffer.append('❌ bad password')
            return self.bot.login_and_steal_keys.call_count >= 3
        self.bot.login_and_steal_keys = Mock(side_effect=login)

        self.assertTrue(self.bot._authorize_with_backoff())
        waits = [c.args[0] for c in self.bot._sleep_alive.call_args_list]
        self.assertEqual(len(waits), 2)
        self.assertGreaterEqual(waits[0], 5 * 60)
        self.assertGreaterEqual(waits[1], 10 * 60)
        titles = [c.args[0] for c in self.bot.notifier.notify_alert.call_args_list]
        self.assertEqual(titles, ['не удаётся войти на MangaBuff', 'вход на MangaBuff восстановлен'])
        self.assertIn('bad password', self.bot.notifier.notify_alert.call_args_list[0].args[1])

    @patch('engine.random.randint', return_value=30)
    def test_backoff_caps_at_an_hour_and_stops_with_daemon(self, _jitter):
        def login():
            if self.bot.login_and_steal_keys.call_count >= 8:
                self.bot.running = False
            return False
        self.bot.login_and_steal_keys = Mock(side_effect=login)

        self.assertFalse(self.bot._authorize_with_backoff())
        waits = [c.args[0] // 60 for c in self.bot._sleep_alive.call_args_list]
        self.assertEqual(waits[:5], [5, 10, 20, 40, 60])
        self.assertTrue(all(w <= 61 for w in waits))
        # Alerts on the first failure and then every sixth, not on every retry
        self.assertEqual(self.bot.notifier.notify_alert.call_count, 2)

    def test_valid_saved_session_skips_login(self):
        self.bot.validate_session.return_value = True
        self.bot._validate_session_with_server.return_value = True
        self.bot.login_and_steal_keys = Mock()
        self.assertTrue(self.bot._authorize_with_backoff())
        self.bot.login_and_steal_keys.assert_not_called()
        self.bot.notifier.notify_alert.assert_not_called()


class ArenaEnergyModelTests(unittest.TestCase):
    def test_latest_point_wins_and_fight_cost_is_applied(self):
        import arena
        battles = [
            {'observed_at': 1000, 'own_side': 'attacker', 'is_win': True,
             'cards': {'attacker': [{'card_user_id': 7, 'battle_card_energy': 11}]}},
            {'observed_at': 2000, 'own_side': 'defender', 'is_win': False,
             'cards': {'defender': [{'card_user_id': 8, 'battle_card_energy': 50}],
                       'attacker': [{'card_user_id': 99, 'battle_card_energy': 5}]}},
        ]
        points = arena.last_energy_points(battles, {'7': {'value': 40, 'at': 500}})
        self.assertEqual(points[7], (1000.0, 9))   # newer battle beats older read; win costs 2
        self.assertEqual(points[8], (2000.0, 49))  # loss costs 1
        self.assertNotIn(99, points)               # opponent cards are not ours

    def test_energy_is_full_after_a_4_hour_reset_mark(self):
        from datetime import datetime, timedelta, timezone

        import arena
        msk = timezone(timedelta(hours=3))
        mark = datetime(2026, 10, 18, 12, 0, tzinfo=msk).timestamp()  # 12:00 MSK reset
        points = {1: (mark - 600, 3), 2: (mark + 600, 7)}             # read at 11:50 and 12:10
        est = arena.projected_energy(points, now=mark + 3600)         # now 13:00
        self.assertEqual(est[1], arena.ENERGY_MAX)  # a reset passed since the read
        self.assertEqual(est[2], 7)                 # no reset since: still tired
        self.assertEqual(arena.last_energy_reset(mark + 3 * 3600), mark)


class DesktopLifecycleTests(unittest.TestCase):
    def test_stop_aborts_current_module(self):
        bot = object.__new__(MangaMinerBot)
        bot.running = True
        bot.log = Mock()
        bot._module_abort = __import__('threading').Event()
        bot.stop()
        self.assertFalse(bot.running)
        self.assertTrue(bot._module_cancelled())

    def _arena_bot(self):
        import threading

        bot = object.__new__(MangaMinerBot)
        bot.running = True
        bot.log = Mock()
        bot._module_abort = threading.Event()
        bot._heartbeat_lock = threading.Lock()
        bot.check_arena_rank_promotion = Mock()
        bot.check_and_claim_quests = Mock()
        return bot

    def test_pause_returns_as_soon_as_module_is_aborted(self):
        import threading
        import time

        bot = self._arena_bot()
        threading.Timer(0.3, bot._module_abort.set).start()
        started = time.monotonic()
        bot._pause(60)
        self.assertLess(time.monotonic() - started, 2)

    def test_farm_batch_stops_on_its_time_budget(self):
        bot = self._arena_bot()
        bot.start_battle = Mock(return_value=None)
        with patch('engine.ARENA_BATCH_SECONDS', 0):
            bot.run_farm_loop(prepared=True)
        bot.start_battle.assert_not_called()

    def test_panel_snapshot_keeps_last_known_values(self):
        state = {'panel': {'ore': 500, 'diamonds': 12, 'energy': 3}}
        bot = object.__new__(MangaMinerBot)
        with patch('engine.DataManager.load_reading_state', return_value=state), \
                patch('engine.DataManager.save_reading_state') as save:
            bot._persist_panel_state(next_action='Вечерний кубок', next_wait_sec=30, energy=0)
        panel = save.call_args.args[0]['panel']
        self.assertEqual(panel['next_action'], 'Вечерний кубок')
        self.assertEqual(panel['ore'], 500)
        self.assertEqual(panel['diamonds'], 12)
        self.assertEqual(panel['energy'], 0)

    def test_concurrent_setting_writes_are_not_lost(self):
        import os, tempfile, threading
        from data_management import DataManager
        path = os.path.join(tempfile.mkdtemp(), 'settings.json')

        def writer(prefix):
            for i in range(60):
                DataManager.save_json(path, {f'{prefix}{i}': i})

        threads = [threading.Thread(target=writer, args=(p,)) for p in ('a', 'b', 'c')]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(DataManager.load_json(path)), 180)


class TelegramTopicTests(unittest.TestCase):
    def setUp(self):
        self.notifier = object.__new__(TelegramNotifier)
        self.settings = {'tg_token': 't', 'tg_chat_id': '1', 'tg_enabled': True}
        getter = patch('tg_notifier.DataManager.get_setting', side_effect=lambda key, default=None: self.settings.get(key, default))
        getter.start()
        self.addCleanup(getter.stop)
        # Ignore TG_THREAD_ID from a real .env on the server
        env = patch.dict('os.environ', {'TG_THREAD_ID': ''})
        env.start()
        self.addCleanup(env.stop)

    @patch('tg_notifier.requests.post')
    def test_messages_go_to_configured_topic(self, post):
        post.return_value = SimpleNamespace(status_code=200, text='{"ok":true}')
        self.settings['tg_thread_id'] = '55'
        self.assertEqual(self.notifier._send_http('hi'), (True, 'OK'))
        self.assertEqual(post.call_args.kwargs['data']['message_thread_id'], 55)

    @patch('tg_notifier.requests.post')
    def test_no_topic_keeps_main_chat(self, post):
        post.return_value = SimpleNamespace(status_code=200, text='{"ok":true}')
        self.notifier._send_photo_http(b'\x89PNG')
        self.assertNotIn('message_thread_id', post.call_args.kwargs['data'])

    @patch('tg_notifier.requests.post')
    def test_deleted_topic_falls_back_to_main_chat(self, post):
        post.side_effect = [
            SimpleNamespace(status_code=400, text='{"description":"Bad Request: message thread not found"}'),
            SimpleNamespace(status_code=200, text='{"ok":true}'),
        ]
        self.settings['tg_thread_id'] = 55
        self.assertEqual(self.notifier._send_http('hi'), (True, 'OK'))
        self.assertEqual(post.call_count, 2)
        self.assertNotIn('message_thread_id', post.call_args.kwargs['data'])


class UpdaterTests(unittest.TestCase):
    def test_version_compare(self):
        from buffy.updater import is_newer

        self.assertTrue(is_newer('v11.1.0', '11.0.0'))
        self.assertTrue(is_newer('11.0.10', '11.0.9'))
        self.assertFalse(is_newer('v11.0.0', '11.0.0'))
        self.assertFalse(is_newer('v10.9.9', '11.0.0'))
        self.assertFalse(is_newer('', '11.0.0'))

    def test_fetch_latest_picks_the_installer(self):
        from buffy import updater

        release = {
            'tag_name': 'v11.1.0',
            'html_url': 'https://github.com/x/y/releases/tag/v11.1.0',
            'assets': [
                {'name': 'Buffy-11.1.0-portable.zip', 'browser_download_url': 'zip', 'size': 5},
                {'name': 'Buffy-Setup-11.1.0.exe', 'browser_download_url': 'exe', 'size': 7},
            ],
        }
        resp = Mock()
        resp.json.return_value = release
        with patch.object(updater.requests, 'get', return_value=resp):
            info = updater.fetch_latest()
        self.assertEqual(info['version'], '11.1.0')
        self.assertEqual(info['url'], 'exe')
        self.assertEqual(info['size'], 7)

    def test_latest_404_falls_back_to_the_newest_tag(self):
        # GitHub outage: no release list for logged-out users, yet each release opens by its tag
        from buffy import updater

        release = {'tag_name': 'v11.1.0', 'html_url': 'page',
                   'assets': [{'name': 'Buffy-Setup-11.1.0.exe', 'browser_download_url': 'exe', 'size': 7}]}
        tags = [{'name': 'v11.0.9'}, {'name': 'v11.1.0'}, {'name': 'v11.0.12'}, {'name': 'nightly'}]
        asked = []

        def get(url, **kw):
            asked.append(url.rsplit('/', 1)[-1])
            if url.endswith('/releases/latest'):
                return Mock(status_code=404)
            if '/tags?' in url:
                return Mock(status_code=200, json=Mock(return_value=tags))
            return Mock(status_code=200, json=Mock(return_value=release))
        with patch.object(updater.requests, 'get', side_effect=get):
            info = updater.fetch_latest()
        self.assertEqual(asked[-1], 'v11.1.0')  # the highest version, not the first tag listed
        self.assertEqual(info['url'], 'exe')


class FriendlyJournalTests(unittest.TestCase):
    def test_technical_lines_become_plain_or_hidden(self):
        from buffy.humanize import humanize

        ctx = {}
        self.assertIsNone(humanize('▶️ Модуль «reading» (лимит ~20 мин)', ctx))
        self.assertEqual(ctx['module'], 'reading')
        self.assertIsNone(humanize('✅ Session valid!', ctx))
        self.assertEqual(
            humanize('🃏 ВЫПАЛА КАРТА: «Рем» (№12 — «Re:Zero»)! (6/10 сегодня)', ctx),
            '🃏 Выпала карта «Рем» (6/10 за сегодня)',
        )
        self.assertEqual(
            humanize('⚔️ Battles: 5 | W: 4 | L: 1 | +20 essence', ctx),
            '⚔️ Бои на арене: 5, побед 4, поражений 1, +20 эссенции',
        )
        self.assertEqual(humanize('⚠️ что-то непонятное', ctx), '⚠️ Чтение глав: небольшая заминка, Buffy повторит сам')

    def test_problem_report_masks_secrets_and_fits_url(self):
        from urllib.parse import unquote

        from buffy.api import ISSUE_URL_LIMIT, build_issue_url

        lines = ['[12:00:00] вход me@mail.ru пароль hunter22 прокси http://u:p@1.2.3.4:80'] * 500
        url = build_issue_url('Арена не бьётся', lines, secrets=['hunter22'])
        text = unquote(url)
        self.assertLessEqual(len(url), ISSUE_URL_LIMIT)
        self.assertIn('Арена не бьётся', text)
        for secret in ('me@mail.ru', 'hunter22', 'u:p@'):
            self.assertNotIn(secret, text)


class ProxyRetryTests(unittest.TestCase):
    def _session(self, outcomes):
        from http_session import GuardedSession

        session = object.__new__(GuardedSession)
        session.should_abort = None
        session.on_heartbeat = None
        session._raw_request_once = Mock(side_effect=outcomes)
        return session

    @patch('http_session.random.uniform', return_value=0)
    def test_tunnel_failure_is_retried(self, _):
        session = self._session([RuntimeError('curl: (7) CONNECT tunnel failed, response 502'), 'ok'])
        self.assertEqual(session._raw_request('POST', 'https://mangabuff.ru/battle/fight/start'), 'ok')
        self.assertEqual(session._raw_request_once.call_count, 2)

    @patch('http_session.random.uniform', return_value=0)
    def test_connect_timeout_is_retried_but_read_timeout_is_not(self, _):
        session = self._session([RuntimeError('curl: (28) Connection timed out after 10002 milliseconds'), 'ok'])
        self.assertEqual(session._raw_request('POST', 'https://mangabuff.ru/battle/cup/action'), 'ok')
        session = self._session([RuntimeError('curl: (28) Operation timed out after 10002 milliseconds with 0 bytes received')])
        with self.assertRaises(RuntimeError):
            session._raw_request('POST', 'https://mangabuff.ru/battle/cup/action')
        self.assertEqual(session._raw_request_once.call_count, 1)

    @patch('http_session.random.uniform', return_value=0)
    def test_other_errors_are_not_retried(self, _):
        session = self._session([RuntimeError('HTTP 500')])
        with self.assertRaises(RuntimeError):
            session._raw_request('GET', 'https://mangabuff.ru/')
        self.assertEqual(session._raw_request_once.call_count, 1)


class BattleArchiveTests(unittest.TestCase):
    def test_archive_keeps_every_battle_once_and_filters(self):
        import tempfile

        import battle_store

        path = battle_store.db_path(tempfile.mkdtemp())
        sides = {'attacker': [{'card_user_id': i} for i in range(4)],
                 'defender': [{'card_user_id': 10 + i} for i in range(4)]}
        for n in range(60):
            battle = {'id': f'b{n}', 'observed_at': 1000 + n, 'own_side': 'attacker',
                      'is_win': n % 2 == 0, 'cards': sides, 'steps': [{'type': 'attack'}]}
            self.assertTrue(battle_store.record(path, battle, 'arena'))
        self.assertFalse(battle_store.record(path, {'id': 'b0', 'observed_at': 1}, 'arena'))
        battle_store.record(path, {'id': 'cup1', 'observed_at': 2000, 'own_side': 'defender',
                                   'is_win': False, 'cards': sides}, 'cup')
        self.assertEqual(len(battle_store.load(path)), 61)
        self.assertEqual([b['id'] for b in battle_store.load(path, source='arena', limit=2)], ['b58', 'b59'])
        self.assertEqual(len(battle_store.load(path, with_steps_only=True)), 60)
        self.assertEqual(battle_store.stats(path)['arena']['wins'], 30)

    def test_broken_archive_never_raises(self):
        import battle_store

        self.assertFalse(battle_store.record('/nonexistent/dir/x.sqlite3', {'id': 'x'}, 'arena'))


class ArenaAnalysisTests(unittest.TestCase):
    @staticmethod
    def _battle(steps, defender_seal='barrier'):
        attacker = [{'card_user_id': 1, 'attack': 200, 'max_hp': 1000, 'seal_code': 'vampirism'}] +                    [{'card_user_id': i, 'attack': 100, 'max_hp': 1000, 'seal_code': 'hunter'} for i in (2, 3, 4)]
        defender = [{'card_user_id': 11, 'attack': 100, 'max_hp': 1000, 'seal_code': defender_seal}] +                    [{'card_user_id': i, 'attack': 100, 'max_hp': 1000, 'seal_code': 'hunter'} for i in (12, 13, 14)]
        return {'own_side': 'attacker', 'is_win': True, 'cards': {'attacker': attacker, 'defender': defender},
                'steps': steps}

    def test_barrier_share_uses_the_full_hit(self):
        import arena_analysis

        step = {'type': 'attack', 'from_side': 'attacker', 'to_side': 'defender', 'from_card_user_id': 1,
                'to_card_user_id': 11, 'damage': 60, 'dealt_damage': 60, 'hp_before': 100, 'hp_after': 94,
                'effects': [{'type': 'shield', 'amount': 140, 'card_user_id': 11}]}
        est = arena_analysis.measure([self._battle([step])])
        self.assertAlmostEqual(est['barrier_block'].value, 0.7)

    def test_dodge_counts_zero_damage_hits(self):
        import arena_analysis

        hit = {'type': 'attack', 'from_card_user_id': 2, 'to_card_user_id': 11, 'damage': 100, 'dealt_damage': 100}
        miss = dict(hit, damage=0, dealt_damage=0)
        est = arena_analysis.measure([self._battle([hit, hit, hit, miss], defender_seal='dodge')])
        self.assertEqual(est['dodge_chance'].value, 0.25)
        self.assertEqual(est['dodge_chance'].samples, 4)

    def test_opponent_pool_prefers_teams_met_most(self):
        import arena

        def battle(ids):
            return {'own_side': 'attacker', 'cards': {'defender': [{'card_user_id': i} for i in ids]}}
        battles = [battle([1, 2, 3, 4]), battle([5, 6, 7, 8]), battle([4, 3, 2, 1]), battle([9, 10, 11, 12])]
        teams = arena.archive_opponent_teams(battles, limit=2)
        self.assertEqual(sorted(c['card_user_id'] for c in teams[0]), [1, 2, 3, 4])
        self.assertEqual(len(teams), 2)


class CupSecretTests(unittest.TestCase):
    def _state(self, tactic):
        def team(base, hp):
            return [{'card_user_id': base + i, 'hp': h, 'max_hp': 2000, 'attack': 250, 'crit_chance': 20}
                    for i, h in enumerate(hp)]
        return {'final': {'attacker': team(1, [2000, 1500, 2000, 1900]), 'defender': team(11, [2000] * 4)},
                'used_cards': {'attacker': [], 'defender': []},
                'tactics': {'attacker': tactic, 'defender': {}}}

    def test_unused_tactic_becomes_a_trap_on_the_most_wounded_card(self):
        from arena import choose_action

        state = self._state({'used_this_round': False, 'blocked_kind': None, 'secret_active': False, 'boost': None})
        self.assertEqual(choose_action(state, 'attacker'),
                         ('tactic', {'kind': 'secret', 'secret_type': 'trap', 'card_user_id': 2}))

    def test_no_secret_when_set_blocked_disabled_or_unsupported(self):
        from arena import choose_action

        for tactic, allow in ((dict(used_this_round=False, secret_active=True), True),
                              (dict(used_this_round=False, secret_active=False, blocked_kind='secret'), True),
                              (dict(used_this_round=False, secret_active=False), False),
                              (dict(used_this_round=False), True)):
            self.assertEqual(choose_action(self._state(tactic), 'attacker', allow_secret=allow)[0], 'attack')


class DeckSealLimitTests(unittest.TestCase):
    def test_optimizer_never_puts_three_same_seals_in_a_deck(self):
        from collections import Counter

        import arena

        def card(i, seal, attack):
            return {'id': i, 'base_id': 100 + i, 'attack': attack, 'max_hp': 2500, 'crit': 20, 'seal': seal,
                    'seal_id': 1, 'in_trade': False, 'energy': 60, 'stats_source': 'inventory'}
        cards = [card(i, 'regeneration', 400) for i in range(1, 6)] + [card(i, 'hunter', 150) for i in range(6, 10)]
        opponents = [{'cards': [{'card_user_id': 50 + j, 'attack': 250, 'max_hp': 1800, 'crit_chance': 20,
                                 'seal_code': 'hunter', 'buff_code': None} for j in range(4)]}]
        ids, _ = arena.optimize_deck([6, 7, 8, 9], cards, 999, opponents, budget_seconds=3)
        seals = Counter(c['seal'] for c in cards if c['id'] in ids)
        self.assertLessEqual(max(seals.values()), arena.MAX_SAME_SEAL)
        self.assertEqual(seals['regeneration'], 2)


class TowerChoiceTests(unittest.TestCase):
    def _plan(self, priority):
        html = ('<div id="t" data-preview-url="https://mangabuff.ru/tower/preview">'
                '<button data-tower-option="difficulty" data-value="normal"></button>'
                '<button data-tower-option="difficulty" data-value="nightmare"></button></div>')
        soup = BeautifulSoup(html, 'html.parser')
        root = soup.select_one('#t')
        forecasts = {'normal': (0.90 * 12, 4800), 'nightmare': (0.87 * 12, 7900)}

        def post(url, json=None, **kw):
            progress, crystals = forecasts[json['difficulty_type']]
            return Mock(status_code=200, json=lambda: {'data': {
                'power_ratio': 1.5, 'estimated_progress': progress, 'warning': None,
                'rewards': {'dark_crystals': {'min': crystals, 'max': crystals}}}})
        bot = object.__new__(MangaMinerBot)
        bot.log = Mock()
        bot.session = Mock(post=post)
        bot.csrf_token = 't'
        bot._module_cancelled = lambda: False
        payload = {'card_user_ids': [1, 2, 3, 4], 'duration_minutes': 720, 'route_type': 'frontier',
                   'difficulty_type': 'normal', 'order_type': 'gathering'}
        with patch('engine.DataManager.get_setting', side_effect=lambda k, d=None: priority if k == 'tower_priority' else d):
            return bot._plan_tower_expedition(root, soup, payload)['difficulty_type']

    def test_rewards_priority_takes_nightmare_when_progress_is_close(self):
        self.assertEqual(self._plan('rewards'), 'nightmare')

    def test_progress_priority_keeps_the_old_choice(self):
        self.assertEqual(self._plan('progress'), 'normal')


class ClubScheduleTests(unittest.TestCase):
    def setUp(self):
        self.settings = {}
        get = patch('club.DataManager.get_setting', side_effect=lambda k, d=None: self.settings.get(k, d))
        put = patch('club.DataManager.set_setting', side_effect=lambda k, v: self.settings.__setitem__(k, v))
        get.start(); put.start()
        self.addCleanup(get.stop); self.addCleanup(put.stop)
        self.bot = object.__new__(MangaMinerBot)
        self.bot.log = Mock()
        self.bot.session = Mock()

    def test_no_club_is_looked_up_once_a_day(self):
        self.bot.get_club_slug = Mock(return_value=None)
        self.bot.run_club_cycle()
        self.bot.run_club_cycle()
        self.assertEqual(self.bot.get_club_slug.call_count, 1)
        self.assertEqual(self.bot.club_check_interval(), 6 * 3600)

    def test_full_crystals_switch_to_rare_checks(self):
        self.assertEqual(self.bot.club_check_interval(), 30 * 60)
        html = '<div class="club-crystals" data-contribute-url="/c"><div class="club-crystals__progress">10 / 10</div></div>'
        self.bot.get_club_slug = Mock(return_value='my-club')
        self.bot.session.get.return_value = Mock(status_code=200, text=html)
        self.bot._process_card_contribution_alert = Mock()
        self.bot.run_club_cycle()
        self.assertEqual(self.bot.club_check_interval(), 3 * 3600)
        self.bot.session.post.assert_not_called()


class CupScheduleTests(unittest.TestCase):
    def test_cup_without_a_deck_waits_without_blocking_other_modules(self):
        from datetime import datetime, timedelta, timezone

        bot = object.__new__(MangaMinerBot)
        bot.log = Mock()
        bot._msk_now = lambda: datetime(2026, 10, 18, 20, 0, tzinfo=timezone(timedelta(hours=3)))
        bot.management_state = lambda: {}
        bot.prepare_arena_deck = Mock(return_value=False)
        with patch('arena.DataManager.get_setting', return_value=True),                 patch('arena.DataManager.load_json', return_value={}):
            self.assertFalse(bot.run_evening_cup())
            self.assertFalse(bot.run_evening_cup())
        bot.prepare_arena_deck.assert_called_once()
        self.assertGreater(bot._cup_retry_at, __import__('time').time() + 25 * 60)


class BonusCardAccessTests(unittest.TestCase):
    def test_reading_for_cards_stops_when_ready_cards_never_drop(self):
        settings = {}
        bot = object.__new__(MangaMinerBot)
        bot.log = Mock()
        bot.get_reading_stats = Mock(return_value={'chapters_read': 75, 'chapters_max': 75, 'cards_found': 0,
                                                   'cards_max': 10, 'card_ready': True, 'card_cooldown': ''})
        bot.read_manga_chapters = Mock(side_effect=lambda **_: setattr(bot, '_last_read_count', 10))
        out = {'card_wait_sec': 0, 'chapters_wait_sec': 0, 'ch_read': 0, 'ch_max': 75, 'cards_found': 0, 'cards_max': 10}
        with patch('engine.DataManager.get_setting', side_effect=lambda k, d=None: settings.get(k, d)),                 patch('engine.DataManager.set_setting', side_effect=settings.__setitem__):
            for _ in range(4):
                bot._reading_step(dict(out))
        self.assertEqual(bot.read_manga_chapters.call_count, 3)
        self.assertIn('bonus_cards_off_date', settings)


class ManualPreemptTests(unittest.TestCase):
    def _bot(self):
        import collections
        import threading

        bot = object.__new__(MangaMinerBot)
        bot.running = True
        bot.log = Mock()
        bot.notifier = Mock()
        bot._module_abort = threading.Event()
        bot._heartbeat_lock = threading.Lock()
        bot._manual_queue = collections.deque()
        bot.manual_callback = None
        bot.fatal_exit = Mock()
        return bot

    def test_manual_run_cuts_the_current_step_and_goes_first(self):
        bot = self._bot()
        order = []

        def long_reading():
            bot.request_manual_module('club')       # user clicks while reading
            order.append('reading cut' if bot._module_abort.is_set() else 'reading kept')

        bot._manual_step = lambda name: order.append('manual ' + name) or 'ok'
        bot.refresh_live_panel = Mock()
        bot._run_module('reading', long_reading, 60)
        bot._run_module('ads', lambda: order.append('ads'), 60)
        self.assertEqual(order, ['reading cut', 'manual club', 'ads'])

    def test_live_cup_fight_is_never_cut(self):
        bot = self._bot()
        seen = []
        bot._run_module('evening_cup', lambda: (bot.request_manual_module('club'),
                                                seen.append(bot._module_abort.is_set())), 60)
        self.assertEqual(seen, [False])


class AppWordingTests(unittest.TestCase):
    def test_telegram_errors_say_what_to_do(self):
        from buffy.api import _tg_error

        self.assertIn('Токен не подошёл', _tg_error('Unauthorized'))
        self.assertIn('Найти Chat ID', _tg_error('Bad Request: chat not found'))
        self.assertIn('Разблокируй', _tg_error('Forbidden: bot was blocked by the user'))

    def test_switched_off_module_cannot_be_run_now(self):
        from buffy.api import BuffyApi

        with patch('buffy.api.DataManager.get_setting', side_effect=lambda k, d=None: False if k == 'club_enabled' else d),                 patch('buffy.api.DataManager.get_credentials', return_value=('a@b.c', 'x')):
            result = BuffyApi().run_module('club')
        self.assertFalse(result['ok'])
        self.assertIn('выключен', result['error'])


class PcSleepTests(unittest.TestCase):
    def test_wait_ends_at_once_when_the_clock_jumped_past_it(self):
        import threading

        bot = object.__new__(MangaMinerBot)
        bot.running = True
        bot._heartbeat_lock = threading.Lock()
        clock = [1000.0]
        with patch('engine.time.time', side_effect=lambda: clock[0]),                 patch('engine.time.sleep', side_effect=lambda s: clock.__setitem__(0, clock[0] + 8 * 3600)):
            bot._sleep_alive(3600)        # the PC sleeps 8 h during the first second
        self.assertGreaterEqual(clock[0], 1000 + 3600)
        self.assertLess(clock[0], 1000 + 9 * 3600)  # one slice, not 3600 of them


if __name__ == '__main__':
    unittest.main()
