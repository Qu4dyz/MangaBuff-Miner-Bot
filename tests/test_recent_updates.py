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


if __name__ == '__main__':
    unittest.main()
