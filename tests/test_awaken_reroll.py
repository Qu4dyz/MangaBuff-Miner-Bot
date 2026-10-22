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
        self.sticky_awake = sticky_awake
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

    def management_post(self, url, data, mutation=False):
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
            assert not c['awake'], 'awakening an awakened card'
            self.energy -= 1
            c['awake'], c['stats'] = True, c['rolls'].pop(0)
            return {'success': True}
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

            def post(url, data, mutation=False):
                body = real(url, data, mutation)
                if url.endswith('/awaken'):
                    on_awaken(settings)
                return body
            self.management_post = post
        with patch('arena.DataManager.get_setting', side_effect=lambda k, d=None: settings.get(k, d)), \
                patch('arena.DataManager.set_setting', side_effect=settings.__setitem__), \
                patch('arena.time.sleep'):
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
        self.assertEqual(site.energy, 5)
        self.assertNotIn('awaken', site.calls)

    def test_stop_in_the_app_ends_the_series(self):
        site = FakeSite({CID: card(True, (90, 800, 10), [(100, 900, 5)] * 5)}, energy=10)

        def stop(settings):
            settings['awaken_reroll'] = {**settings['awaken_reroll'], 'active': False}  # «Стоп» pressed
        self.assertEqual(site.run(GOAL, on_awaken=stop), 1)
        self.assertEqual(site.energy, 9)
        self.assertFalse(site.settings['active'])

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
