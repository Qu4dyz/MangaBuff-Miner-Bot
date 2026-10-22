"""Seal reforge against a fake site: no real requests, no real state files."""
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

from arena import SEAL_INFO, SealRerollMixin, SendCancelled

CID = 679449399
NAME = {sid: info[0] for sid, info in SEAL_INFO.items()}


class FakeForge(SealRerollMixin):
    def __init__(self, seal, rolls, essence=5000, changes_stats=False, hour=12):
        self.seal, self.rolls, self.essence = seal, list(rolls), essence
        self.changes_stats, self.hour = changes_stats, hour
        self.state, self.calls = {}, []
        self.log, self.notifier = Mock(), Mock()
        self.on_send = None

    def _alive(self):
        return True

    def _msk_now(self):
        return datetime(2026, 10, 22, self.hour)

    def management_state(self):
        return dict(self.state)

    def management_save(self, values):
        self.state.update(values)

    def management_page(self, path, selector):
        soup = BeautifulSoup(f'<div class="battle-reroll" data-reroll-card-url="/battle/card/reroll/card" '
                             f'data-reroll-seal-url="/battle/card/reroll-seal"><b data-reroll-essence>{self.essence}</b></div>',
                             'html.parser')
        return soup, soup.select_one(selector)

    def card(self):
        return {'card_user_id': CID, 'attack': 173, 'health': 1470 + (5 if self.changes_stats and self.calls.count('seal') else 0),
                'crit_chance': 21, 'seal': {'name': NAME[self.seal]}}

    def management_post(self, url, data, mutation=False, before_send=None):
        if url.endswith('/reroll/card'):
            self.calls.append('card')
            return {'card': self.card()}
        if self.on_send:
            self.on_send()
        if before_send and not before_send():
            raise SendCancelled()
        self.calls.append('seal')
        self.essence -= 500
        self.seal = self.rolls.pop(0)
        return {'success': True, 'cost': 500, 'essence': f'{self.essence:,}'.replace(',', ' '), 'card': self.card()}

    def run(self, wanted, max_essence=0, spent=0):
        self.settings = settings = {'seal_reroll': {'active': True, 'seals': wanted, 'max_essence': max_essence, 'spent': spent,
                                                    'cards': [{'card_user_id': CID, 'name': 'Цунаде'}]}}
        with patch('arena.DataManager.get_setting', side_effect=lambda k, d=None: settings.get(k, d)), \
                patch('arena.DataManager.set_setting', side_effect=settings.__setitem__), \
                patch('arena.REROLL_HOLD_SEC', 0), patch('arena.time.sleep'):
            return self.run_seal_reroll()


class SealRerollTests(unittest.TestCase):
    def test_reforges_until_a_wanted_seal(self):
        forge = FakeForge(seal=2, rolls=[5, 4, 14, 12])
        self.assertEqual(forge.run([14, 12]), 3)
        self.assertEqual(forge.seal, 14)
        goal = forge.settings['seal_reroll']
        self.assertEqual(goal['cards'][0]['status'], 'reached')
        self.assertEqual(goal['cards'][0]['history'], [5, 4, 14])
        self.assertEqual(goal['spent'], 1500)
        self.assertFalse(goal['active'])
        self.assertEqual(forge.settings['reroll_essence']['left'], 3500)

    def test_wanted_seal_already_there_spends_nothing(self):
        forge = FakeForge(seal=14, rolls=[])
        self.assertEqual(forge.run([14]), 0)
        self.assertNotIn('seal', forge.calls)

    def test_never_spends_past_the_limit(self):
        forge = FakeForge(seal=2, rolls=[5, 4, 3, 1])
        self.assertEqual(forge.run([14], max_essence=1000), 2)
        self.assertEqual(forge.settings['seal_reroll']['spent'], 1000)
        self.assertTrue(forge.settings['seal_reroll']['active'])  # waits for a higher limit

    def test_not_enough_essence(self):
        forge = FakeForge(seal=2, rolls=[14], essence=300)
        self.assertEqual(forge.run([14]), 0)
        self.assertNotIn('seal', forge.calls)

    def test_changed_stats_stop_the_series(self):
        forge = FakeForge(seal=2, rolls=[5, 14], changes_stats=True)
        with self.assertRaises(ValueError):
            forge.run([14])
        self.assertEqual(forge.calls.count('seal'), 1)

    def test_stop_right_before_sending_keeps_the_seal(self):
        forge = FakeForge(seal=2, rolls=[5, 14])

        def stop():
            if forge.calls.count('seal') == 1:  # «Остановить» pressed while the second reforge waits
                forge.settings['seal_reroll'] = {**forge.settings['seal_reroll'], 'active': False}
        forge.on_send = stop
        self.assertEqual(forge.run([14]), 1)
        self.assertEqual(forge.seal, 5)
        self.assertIsNone(forge.state.get('seal_pending'))

    def test_quiet_during_the_cup(self):
        forge = FakeForge(seal=2, rolls=[14], hour=20)
        self.assertEqual(forge.run([14]), 0)
        self.assertEqual(forge.calls, [])


if __name__ == '__main__':
    unittest.main()


class SealWaitsForStatsTests(unittest.TestCase):
    def test_card_in_an_unfinished_stats_reroll_is_not_reforged(self):
        forge = FakeForge(seal=2, rolls=[14])
        stats = {'active': True, 'cards': [{'card_user_id': CID, 'status': 'running'}]}
        settings = {'seal_reroll': {'active': True, 'seals': [14], 'max_essence': 0, 'spent': 0,
                                    'cards': [{'card_user_id': CID, 'name': 'Цунаде'}]}, 'awaken_reroll': stats}
        with patch('arena.DataManager.get_setting', side_effect=lambda k, d=None: settings.get(k, d)), \
                patch('arena.DataManager.set_setting', side_effect=settings.__setitem__), patch('arena.time.sleep'):
            self.assertEqual(forge.run_seal_reroll(), 0)
        self.assertNotIn('seal', forge.calls)
        self.assertEqual(settings['seal_reroll']['cards'][0].get('status'), None)  # waits, not «missing»
