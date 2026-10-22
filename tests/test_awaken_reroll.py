"""Awakening reroll against a fake site: no real requests, no real state files."""
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

from arena import AwakenRerollMixin, reroll_meets, reroll_odds

CID, CID2 = 679449399, 511408678


class FakeSite(AwakenRerollMixin):
    """cards: {id: {'awake': bool, 'stats': (atk, hp, crit), 'rolls': [...]}}"""

    def __init__(self, cards, energy, deck=(CID, 2, 3, 4), hour=12, sticky_awake=False):
        self.cards, self.energy, self.deck, self.hour = cards, energy, list(deck), hour
        self.sticky_awake, self.terse, self.stop_while_sending = sticky_awake, False, None
        self.state, self.calls, self.saved_decks = {}, [], []
        self.log, self.notifier = Mock(), Mock()
        self.settings = {}

    def _alive(self):
        return True

    def _msk_now(self):
        return datetime(2026, 10, 22, self.hour)

    def management_state(self):
        return dict(self.state)

    def management_save(self, values):
        self.state.update(values)

    def management_page(self, path, selector):
        html = {
            '/battle/unawaken': '<div class="battle-unawaken" data-unawaken-url="/battle/unawaken">'
                                '<form class="card-filter-form" data-url="/cards-filter/1"></form></div>',
            '/battle/awakening': f'<div class="battle-awakening" data-awakening-url="/battle/card/awaken">'
                                 f'<b data-awakening-free-left>0</b><b data-awakening-energy>{self.energy}</b></div>',
            '/battle/decks': '<div class="battle-decks" data-save-url="/battle/decks/save-cards">'
                             '<div class="battle-decks__deck--active" data-deck-id="7">'
                             + ''.join(f'<img data-card-user-id="{c}">' for c in self.deck) + '</div></div>',
        }[path]
        soup = BeautifulSoup(html, 'html.parser')
        return soup, soup.select_one(selector)

    def management_post(self, url, data, mutation=False, before_send=None):
        if before_send and self.stop_while_sending and url.endswith('/unawaken'):
            self.stop_while_sending()
        if before_send and not before_send():
            from arena import SendCancelled
            raise SendCancelled()
        if 'cards-filter' in url:
            self.calls.append('filter')
            rows = []
            for cid, c in self.cards.items():
                if c['awake'] == (data['is_battle'] == 1):
                    a, h, cr = c['stats']
                    rows.append({'id': cid, 'in_trade': 0, 'battle_attack': a, 'battle_health': h,
                                 'battle_crit_chance': cr, 'battle_seal_id': 5})
            return {'data': rows}
        if url.endswith('/unawaken'):
            self.calls.append('unawaken')
            # The site takes card_user_ids[]=<id>; check the form as curl_cffi will encode it
            from urllib.parse import parse_qsl, urlencode
            form = parse_qsl(urlencode(data))
            assert ('confirmed', '1') in form, form
            cid = int(dict(form)['card_user_ids[]'])
            if not self.sticky_awake:
                self.cards[cid]['awake'] = False
                self.deck = [c for c in self.deck if c != cid]
            return {'success': True}
        if url.endswith('/awaken'):
            self.calls.append('awaken')
            c = self.cards[data['card_user_id']]
            if c['awake']:
                raise ValueError('Arena API HTTP 422: card is already awakened')
            self.energy -= 1
            c['awake'], c['stats'] = True, c['rolls'].pop(0)
            if self.terse:
                return {'success': True}
            a, h, cr = c['stats']
            return {'success': True, 'free_left': 0, 'awakening_energy': str(self.energy),
                    'card': {'card_user_id': data['card_user_id'], 'attack': a, 'health': h, 'crit_chance': cr,
                             'seal': {'id': 14, 'name': 'Печать уворота'}}}
        raise AssertionError(url)

    def _arena_post(self, url, data):
        self.saved_decks.append([data[f'card_user_ids[{i}]'] for i in range(4)])
        self.deck = self.saved_decks[-1]
        return True

    def run(self, goal, ids=(CID,), on_awaken=None):
        settings = {'awaken_reroll': {'active': True, **goal,
                                      'cards': [{'card_user_id': i, 'name': 'Карта'} for i in ids]}}
        if on_awaken:
            real = self.management_post

            def post(url, data, mutation=False, before_send=None):
                body = real(url, data, mutation, before_send)
                if url.endswith('/awaken'):
                    on_awaken(settings)
                return body
            self.management_post = post
        self.live_settings = settings
        with patch('arena.DataManager.get_setting', side_effect=lambda k, d=None: settings.get(k, d)), \
                patch('arena.DataManager.set_setting', side_effect=settings.__setitem__), \
                patch('arena.REROLL_HOLD_SEC', 0), patch('arena.time.sleep'):
            done = self.run_awaken_reroll()
        self.settings = settings['awaken_reroll']
        return done


GOAL = {'min_attack': 150, 'min_health': 1200, 'min_crit': 0}


def card(awake, stats, rolls=()):
    return {'awake': awake, 'stats': stats, 'rolls': list(rolls)}


class AwakenRerollTests(unittest.TestCase):
    def test_rerolls_until_goal_then_keeps_card_and_restores_deck(self):
        site = FakeSite({CID: card(True, (90, 800, 10), [(140, 1300, 20), (160, 1100, 5), (155, 1250, 9), (200, 1500, 30)])},
                        energy=10)
        self.assertEqual(site.run(GOAL), 3)
        self.assertEqual(site.cards[CID]['stats'], (155, 1250, 9))  # the first roll that meets every minimum stays
        self.assertEqual(site.energy, 7)
        self.assertEqual(site.deck, [CID, 2, 3, 4])  # card is back in the same deck
        self.assertEqual(site.live_settings['awaken_energy']['left'], 7)  # shown on the Awakening page
        self.assertEqual(site.settings['cards'][0]['status'], 'reached')
        self.assertEqual(site.settings['cards'][0]['attempts'], 3)
        self.assertFalse(site.settings['active'])
        site.notifier.notify_alert.assert_called_once()

    def test_several_cards_in_one_run(self):
        site = FakeSite({CID: card(True, (90, 800, 10), [(160, 1300, 5)]),
                         CID2: card(False, (0, 0, 0), [(100, 900, 5), (170, 1250, 9)])},
                        energy=10, deck=(CID, CID2, 3, 4))
        self.assertEqual(site.run(GOAL, ids=(CID, CID2)), 3)
        self.assertEqual([c['status'] for c in site.settings['cards']], ['reached', 'reached'])
        self.assertFalse(site.settings['active'])
        self.assertEqual(site.deck, [CID, CID2, 3, 4])

    def test_no_energy_touches_nothing(self):
        site = FakeSite({CID: card(True, (90, 800, 10))}, energy=0)
        self.assertEqual(site.run(GOAL), 0)
        self.assertNotIn('unawaken', site.calls)
        self.assertTrue(site.cards[CID]['awake'])

    def test_energy_running_out_leaves_card_awakened_in_its_deck(self):
        site = FakeSite({CID: card(True, (90, 800, 10), [(100, 900, 5), (110, 950, 5)])}, energy=2)
        self.assertEqual(site.run(GOAL), 2)
        self.assertTrue(site.cards[CID]['awake'])
        self.assertEqual(site.deck, [CID, 2, 3, 4])
        self.assertTrue(site.settings['active'])  # goes on when fights refill energy

    def test_plain_card_is_awakened_without_unawaken(self):
        site = FakeSite({CID: card(False, (0, 0, 0), [(170, 1300, 15)])}, energy=5, deck=(2, 3, 4, 5))
        self.assertEqual(site.run(GOAL), 1)
        self.assertNotIn('unawaken', site.calls)
        self.assertEqual(site.saved_decks, [])  # it was never in the deck

    def test_already_good_card_is_kept_without_spending(self):
        site = FakeSite({CID: card(True, (180, 1400, 20))}, energy=5)
        self.assertEqual(site.run(GOAL), 0)
        self.assertEqual(site.energy, 5)
        self.assertEqual(site.settings['cards'][0]['status'], 'reached')

    def test_missing_card_is_skipped_and_the_next_one_rolled(self):
        site = FakeSite({CID2: card(False, (0, 0, 0), [(170, 1300, 15)])}, energy=5, deck=(2, 3, 4, 5))
        self.assertEqual(site.run(GOAL, ids=(CID, CID2)), 1)
        self.assertEqual([c['status'] for c in site.settings['cards']], ['missing', 'reached'])

    def test_unconfirmed_unawaken_never_spends_energy(self):
        site = FakeSite({CID: card(True, (90, 800, 10), [(200, 1500, 30)])}, energy=5, sticky_awake=True)
        with self.assertRaises(ValueError):
            site.run(GOAL)
        self.assertEqual(site.energy, 5)  # the site refused to awaken a still awakened card

    def test_two_requests_per_roll(self):
        site = FakeSite({CID: card(True, (90, 800, 10), [(100, 900, 5), (110, 950, 5), (160, 1300, 9)])}, energy=10)
        site.run(GOAL)
        rolls = site.calls[site.calls.index('unawaken'):]
        self.assertEqual(rolls, ['unawaken', 'awaken'] * 3)  # no collection reads between rolls

    def test_terse_answer_falls_back_to_the_collection(self):
        site = FakeSite({CID: card(True, (90, 800, 10), [(100, 900, 5), (160, 1300, 9)])}, energy=10)
        site.terse = True
        self.assertEqual(site.run(GOAL), 2)
        self.assertEqual(site.settings['cards'][0]['status'], 'reached')

    def test_stop_in_the_app_ends_the_series(self):
        site = FakeSite({CID: card(True, (90, 800, 10), [(100, 900, 5)] * 5)}, energy=10)

        def stop(settings):
            settings['awaken_reroll'] = {**settings['awaken_reroll'], 'active': False}  # «Стоп» pressed
        self.assertEqual(site.run(GOAL, on_awaken=stop), 1)
        self.assertEqual(site.energy, 9)
        self.assertFalse(site.settings['active'])

    def test_stop_during_request_spacing_keeps_the_last_roll(self):
        site = FakeSite({CID: card(True, (90, 800, 10), [(172, 920, 5), (145, 1270, 9)])}, energy=10)
        rolls = []

        def stop_after_first_roll():
            rolls.append(1)
            if len(rolls) == 2:  # the second unawaken is about to go: «Остановить» pressed just now
                s = site.live_settings['awaken_reroll']
                site.live_settings['awaken_reroll'] = {**s, 'active': False}
        site.stop_while_sending = stop_after_first_roll
        self.assertEqual(site.run(GOAL), 1)
        self.assertEqual(site.cards[CID]['stats'], (172, 920, 5))  # kept, not rerolled to 145/1270
        self.assertTrue(site.cards[CID]['awake'])
        self.assertIsNone(site.state.get('reroll_pending'))

    def test_quiet_during_the_evening_cup(self):
        site = FakeSite({CID: card(True, (90, 800, 10))}, energy=5, hour=20)
        self.assertEqual(site.run(GOAL), 0)
        self.assertEqual(site.calls, [])

    def test_odds_from_past_awakenings(self):
        cards = [{'battle_attack': a, 'battle_health': h, 'battle_crit_chance': c}
                 for a, h, c in ((100, 900, 10), (160, 1300, 20), (170, 1100, 25), (90, 1400, 5))]
        self.assertEqual(reroll_odds(cards, GOAL), 0.25)
        self.assertIsNone(reroll_odds([], GOAL))
        self.assertTrue(reroll_meets(cards[1], {'min_attack': 160, 'min_health': 1300, 'min_crit': 20}))


if __name__ == '__main__':
    unittest.main()


class RerollApiTests(unittest.TestCase):
    def setUp(self):
        from buffy.api import BuffyApi
        self.api = BuffyApi()
        self.store = {'awaken_reroll': {'active': False, 'min_attack': 156, 'min_health': 1100, 'min_crit': 0,
                                        'cards': [{'card_user_id': CID, 'name': 'Девушка', 'status': 'running', 'attempts': 6}]}}
        for p in (patch('buffy.api.DataManager.get_setting', side_effect=lambda k, d=None: self.store.get(k, d)),
                  patch('buffy.api.DataManager.set_setting', side_effect=self.store.__setitem__),
                  patch.object(self.api, '_log')):
            p.start()
            self.addCleanup(p.stop)

    def test_resume_keeps_cards_and_progress_and_starts_now(self):
        with patch.object(self.api, 'run_module', return_value={'ok': True, 'queued': False}) as run:
            self.assertTrue(self.api.reroll_resume()['ok'])
        run.assert_called_once_with('awaken')
        goal = self.store['awaken_reroll']
        self.assertTrue(goal['active'])
        self.assertEqual(goal['cards'][0]['attempts'], 6)

    def test_lowering_minimums_keeps_the_cards(self):
        self.assertTrue(self.api.reroll_set_mins({'min_attack': '120', 'min_health': 900, 'min_crit': ''})['ok'])
        goal = self.store['awaken_reroll']
        self.assertEqual((goal['min_attack'], goal['min_health'], goal['min_crit']), (120, 900, 0))
        self.assertEqual(goal['cards'][0]['attempts'], 6)
        self.assertFalse(self.api.reroll_set_mins({'min_attack': 0})['ok'])


class RerollAgainTests(unittest.TestCase):
    def test_raising_minimums_requeues_finished_cards_and_resumes(self):
        from buffy.api import BuffyApi
        api = BuffyApi()
        store = {'awaken_reroll': {'active': False, 'min_attack': 130, 'min_health': 1100, 'min_crit': 0, 'cards': [
            {'card_user_id': CID, 'name': 'A', 'status': 'reached', 'result': {'attack': 140, 'health': 1150, 'crit': 9}},
            {'card_user_id': CID2, 'name': 'B', 'status': 'reached', 'result': {'attack': 200, 'health': 1500, 'crit': 9}}]}}
        with patch('buffy.api.DataManager.get_setting', side_effect=lambda k, d=None: store.get(k, d)), \
                patch('buffy.api.DataManager.set_setting', side_effect=store.__setitem__), \
                patch.object(api, '_log'), patch.object(api, 'run_module', return_value={'ok': True}) as run:
            self.assertTrue(api.reroll_set_mins({'min_attack': 150, 'min_health': 1200, 'min_crit': 0})['resumed'])
        self.assertEqual([c['status'] for c in store['awaken_reroll']['cards']], ['waiting', 'reached'])
        self.assertTrue(store['awaken_reroll']['active'])
        run.assert_called_once_with('awaken')

    def test_reached_card_raises_a_windows_notification(self):
        site = FakeSite({CID: card(True, (90, 800, 10), [(160, 1300, 5)])}, energy=5)
        site.on_reroll_reached = Mock()
        site.run(GOAL)
        name, stats = site.on_reroll_reached.call_args.args
        self.assertIn('ATK 160', stats)


class KeepAsIsTests(unittest.TestCase):
    def test_keep_takes_the_last_roll_and_leaves_the_queue(self):
        from buffy.api import BuffyApi
        api = BuffyApi()
        last = {'attack': 202, 'health': 980, 'crit': 23, 'seal': 4}
        store = {'awaken_reroll': {'active': True, 'min_attack': 170, 'min_health': 1000, 'min_crit': 0, 'cards': [
            {'card_user_id': CID, 'name': 'Акено', 'status': 'running', 'history': [{'attack': 135, 'health': 770, 'crit': 16}, last]},
            {'card_user_id': CID2, 'name': 'B', 'status': 'waiting', 'history': []}]}}
        with patch('buffy.api.DataManager.get_setting', side_effect=lambda k, d=None: store.get(k, d)), \
                patch('buffy.api.DataManager.set_setting', side_effect=store.__setitem__):
            self.assertTrue(api.reroll_keep(CID)['ok'])
        goal = store['awaken_reroll']
        self.assertEqual(goal['cards'][0]['status'], 'reached')
        self.assertEqual(goal['cards'][0]['result'], last)
        self.assertTrue(goal['active'])  # the other card still goes on
        with patch('buffy.api.DataManager.get_setting', side_effect=lambda k, d=None: store.get(k, d)), \
                patch('buffy.api.DataManager.set_setting', side_effect=store.__setitem__):
            api.reroll_keep(CID2)  # nothing rolled yet: stays in the queue
        self.assertEqual(store['awaken_reroll']['cards'][1]['status'], 'waiting')


class ReviewFixTests(unittest.TestCase):
    def test_card_on_a_later_page_is_found(self):
        site = FakeSite({}, energy=0)
        pages = {1: {'data': [{'id': 1, 'in_trade': 0}], 'next_page_url': '/p2'},
                 2: {'data': [{'id': CID, 'in_trade': 0, 'battle_attack': 1}], 'next_page_url': None}}
        site.management_post = lambda url, data, mutation=False, before_send=None: pages[data['page']]
        self.assertEqual(site._reroll_card('/cards-filter/1', {'name': 'Девушка', 'card_user_id': CID}, 1)['id'], CID)
        self.assertIsNone(site._reroll_card('/cards-filter/1', {'name': 'Девушка', 'card_user_id': 999}, 1))

    def test_cached_collection_gets_the_latest_roll(self):
        site = FakeSite({CID: card(True, (90, 800, 10), [(100, 900, 5)])}, energy=1)
        site.state['inventory'] = [{'id': CID, 'battle_attack': 90, 'battle_health': 800, 'battle_crit_chance': 10}]
        site.run(GOAL)
        self.assertEqual(site.state['inventory'][0]['battle_attack'], 100)

    def test_tower_squad_comes_back_after_a_reroll(self):
        from engine import rerolled_out_of_squad
        goal = {'awaken_reroll': {'cards': [{'card_user_id': CID}]}}
        with patch('engine.DataManager.get_setting', side_effect=lambda k, d=None: goal.get(k, d)):
            self.assertTrue(rerolled_out_of_squad([2, 3, 4], [2, 3, 4, CID]))       # only the rerolled card is gone
            self.assertFalse(rerolled_out_of_squad([2, 3, 4], [2, 3, 4, 5]))        # the person changed the squad
            self.assertFalse(rerolled_out_of_squad([2, 3, 9], [2, 3, 4, CID]))      # a new card on the site wins
            self.assertFalse(rerolled_out_of_squad([2, 3, 4, CID], [2, 3, 4, CID]))  # nothing missing


class RefusedCardTests(unittest.TestCase):
    def test_site_refusal_skips_the_card_and_goes_on(self):
        site = FakeSite({CID: card(True, (90, 800, 10), [(170, 1300, 9)]),
                         CID2: card(False, (0, 0, 0), [(170, 1300, 9)])}, energy=5, deck=(2, 3, 4, 5))
        real = site.management_post

        def post(url, data, mutation=False, before_send=None):
            if url.endswith('/unawaken'):
                raise ValueError('Arena API HTTP 422: Карта заблокирована')
            return real(url, data, mutation, before_send)
        site.management_post = post
        self.assertEqual(site.run(GOAL, ids=(CID, CID2)), 1)
        first, second = site.settings['cards']
        self.assertEqual((first['status'], first['reason']), ('refused', 'Карта заблокирована'))
        self.assertEqual(second['status'], 'reached')
        self.assertTrue(site.cards[CID]['awake'])  # never left plain
