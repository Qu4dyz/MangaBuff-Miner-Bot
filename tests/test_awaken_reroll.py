"""Awakening reroll against a fake site: no real requests, no real state files."""
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

from arena import AwakenRerollMixin, reroll_meets, reroll_odds

CID = 679449399


class FakeSite(AwakenRerollMixin):
    def __init__(self, awake, stats, rolls, energy, deck=(CID, 2, 3, 4), hour=12, sticky_awake=False):
        self.awake, self.stats, self.rolls, self.energy = awake, stats, list(rolls), energy
        self.deck, self.hour, self.sticky_awake = list(deck), hour, sticky_awake
        self.state, self.calls, self.saved_decks = {}, [], []
        self.log, self.notifier = Mock(), Mock()
        self.settings = {}

    # --- what the bot's mixins provide
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
        self.calls.append(url.rsplit('/', 1)[-1] if 'cards-filter' not in url else 'filter')
        if 'cards-filter' in url:
            mine = self.awake == (data['is_battle'] == 1)
            card = {'id': CID, 'in_trade': 0, 'battle_attack': self.stats[0], 'battle_health': self.stats[1],
                    'battle_crit_chance': self.stats[2], 'battle_seal_id': 5}
            return {'data': [card] if mine else []}
        if url.endswith('/unawaken'):
            if not self.sticky_awake:
                self.awake = False
                self.deck = [c for c in self.deck if c != CID]
            return {'success': True}
        if url.endswith('/awaken'):
            assert not self.awake, 'awakening an awakened card'
            self.energy -= 1
            self.awake, self.stats = True, self.rolls.pop(0)
            return {'success': True}
        raise AssertionError(url)

    def _arena_post(self, url, data):
        self.saved_decks.append([data[f'card_user_ids[{i}]'] for i in range(4)])
        self.deck = self.saved_decks[-1]
        return True

    def run(self, goal):
        settings = {'awaken_reroll': {'card_user_id': CID, 'name': 'Цунаде', 'active': True, **goal}}
        with patch('arena.DataManager.get_setting', side_effect=lambda k, d=None: settings.get(k, d)), \
                patch('arena.DataManager.set_setting', side_effect=settings.__setitem__), \
                patch('arena.time.sleep'):
            done = self.run_awaken_reroll()
        self.settings = settings['awaken_reroll']
        return done


GOAL = {'min_attack': 150, 'min_health': 1200, 'min_crit': 0}


class AwakenRerollTests(unittest.TestCase):
    def test_rerolls_until_goal_then_keeps_card_and_restores_deck(self):
        site = FakeSite(awake=True, stats=(90, 800, 10), energy=10,
                        rolls=[(140, 1300, 20), (160, 1100, 5), (155, 1250, 9), (200, 1500, 30)])
        self.assertEqual(site.run(GOAL), 3)
        self.assertEqual(site.stats, (155, 1250, 9))  # the first roll that meets every minimum stays
        self.assertEqual(site.energy, 7)
        self.assertEqual(site.deck, [CID, 2, 3, 4])  # card is back in the same deck
        self.assertEqual(site.settings['status'], 'reached')
        self.assertFalse(site.settings['active'])
        self.assertEqual(site.settings['attempts'], 3)
        site.notifier.notify_alert.assert_called_once()

    def test_no_energy_touches_nothing(self):
        site = FakeSite(awake=True, stats=(90, 800, 10), energy=0, rolls=[])
        self.assertEqual(site.run(GOAL), 0)
        self.assertNotIn('unawaken', site.calls)
        self.assertTrue(site.awake)

    def test_energy_running_out_leaves_card_awakened_in_its_deck(self):
        site = FakeSite(awake=True, stats=(90, 800, 10), energy=2, rolls=[(100, 900, 5), (110, 950, 5)])
        self.assertEqual(site.run(GOAL), 2)
        self.assertTrue(site.awake)
        self.assertEqual(site.deck, [CID, 2, 3, 4])
        self.assertTrue(site.settings['active'])  # goes on when fights refill energy

    def test_plain_card_is_awakened_without_unawaken(self):
        site = FakeSite(awake=False, stats=(0, 0, 0), energy=5, rolls=[(170, 1300, 15)], deck=(2, 3, 4, 5))
        self.assertEqual(site.run(GOAL), 1)
        self.assertNotIn('unawaken', site.calls)
        self.assertEqual(site.saved_decks, [])  # it was never in the deck

    def test_already_good_card_is_kept_without_spending(self):
        site = FakeSite(awake=True, stats=(180, 1400, 20), energy=5, rolls=[])
        self.assertEqual(site.run(GOAL), 0)
        self.assertEqual(site.energy, 5)
        self.assertEqual(site.settings['status'], 'reached')

    def test_unconfirmed_unawaken_never_spends_energy(self):
        site = FakeSite(awake=True, stats=(90, 800, 10), energy=5, rolls=[(200, 1500, 30)], sticky_awake=True)
        with self.assertRaises(ValueError):
            site.run(GOAL)
        self.assertEqual(site.energy, 5)
        self.assertNotIn('awaken', site.calls)

    def test_stop_in_the_app_ends_the_series(self):
        site = FakeSite(awake=True, stats=(90, 800, 10), energy=10, rolls=[(100, 900, 5)] * 5)
        settings = {'awaken_reroll': {'card_user_id': CID, 'name': 'Цунаде', 'active': True, **GOAL}}
        real_post = site.management_post

        def post(url, data, mutation=False):
            body = real_post(url, data, mutation)
            if url.endswith('/awaken'):
                settings['awaken_reroll'] = {**settings['awaken_reroll'], 'active': False}  # «Стоп» pressed
            return body
        site.management_post = post
        with patch('arena.DataManager.get_setting', side_effect=lambda k, d=None: settings.get(k, d)),                 patch('arena.DataManager.set_setting', side_effect=settings.__setitem__), patch('arena.time.sleep'):
            self.assertEqual(site.run_awaken_reroll(), 1)
        self.assertEqual(site.energy, 9)

    def test_quiet_during_the_evening_cup(self):
        site = FakeSite(awake=True, stats=(90, 800, 10), energy=5, rolls=[], hour=20)
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
