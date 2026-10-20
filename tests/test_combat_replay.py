import unittest
from combat_replay import cup_samples, chronological_split, capture_cup_trace

class ReplayTests(unittest.TestCase):
    def test_terminal_trace_preserves_cumulative_events_and_excludes_private_fields(self):
        state = {'battle_steps': [{'turn': 1}, {'turn': 2}], 'steps': [{'turn': 2}],
                 'fight_status': 'finished', 'private_tactics': {'secret': 'private'},
                 'final': {'attacker': []}}
        result = capture_cup_trace(state)
        self.assertEqual(len(result['steps']), 2)
        self.assertNotIn('private_tactics', result['final_state'])
        self.assertFalse(result['trace_complete'])
        result['steps'].append({})
        self.assertEqual(len(state['battle_steps']), 2)

    def test_deduplicates_revision_and_keeps_action_context(self):
        state = {'fight_id': 'f', 'revision': 3, 'final': {'attacker': [], 'defender': []}}
        d = {'state': state, 'kind': 'attack', 'payload': {'from_card_user_id': 1, 'target_card_user_id': 2}}
        battles = [{'id': 'f', 'own_side': 'attacker', 'is_win': True, 'observed_at': 10, 'decisions': [d, d]}]
        samples = cup_samples(battles)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]['action'], ('attack', d['payload']))
        self.assertFalse(samples[0]['complete_trace'])

    def test_future_steps_do_not_enter_earlier_decision(self):
        earlier = {'fight_id': 'f', 'revision': 1, 'battle_steps': [{'turn': 1}]}
        later = {'fight_id': 'f', 'revision': 3, 'battle_steps': [{'turn': 1}, {'turn': 2}]}
        b = {'id': 'f', 'own_side': 'attacker', 'decisions': [
            {'state': earlier, 'kind': 'attack', 'payload': {}},
            {'state': later, 'kind': 'attack', 'payload': {}}]}
        self.assertEqual(len(cup_samples([b])[0]['history']), 1)

    def test_split_does_not_share_battle_or_timestamp_boundary(self):
        b = [{'id': str(i), 'observed_at': i // 2} for i in range(10)]
        train, holdout = chronological_split(b, .7)
        self.assertLess(max(x['observed_at'] for x in train), min(x['observed_at'] for x in holdout))
        self.assertFalse({x['id'] for x in train} & {x['id'] for x in holdout})

if __name__ == '__main__':
    unittest.main()
