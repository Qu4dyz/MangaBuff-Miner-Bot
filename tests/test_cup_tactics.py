"""Regression tests for actual Cup choices, independent of rollout planning."""
import copy
import unittest
import arena

def card(cid, attack=100, hp=200, max_hp=None, seal=None, crit=0):
    return dict(card_user_id=cid, attack=attack, hp=hp, max_hp=max_hp or hp,
                seal_code=seal, buff_code=None, crit_chance=crit, alive=True, is_alive=True)

def state(own, enemy, tactics=None):
    return dict(final={'attacker': own, 'defender': enemy},
                used_cards={'attacker': [], 'defender': []},
                tactics={'attacker': tactics or {'used_this_round': True},
                         'defender': {'used_this_round': True}})

class TacticalChoiceTests(unittest.TestCase):
    def test_real_kill_is_preferred_to_false_barrier_kill(self):
        s = state([card(1)], [card(2, attack=150, hp=50, seal='barrier'), card(3, hp=80)])
        self.assertEqual(arena.choose_action(s, 'attacker', hit_history=[])[1]['target_card_user_id'], 3)

    def test_consumed_barrier_is_no_longer_counted(self):
        s = state([card(1)], [card(2, attack=150, hp=50, seal='barrier'), card(3, hp=80)])
        self.assertEqual(arena.choose_action(s, 'attacker', hit_history=[(1, 2)])[1]['target_card_user_id'], 2)

    def test_immortality_is_not_counted_as_a_kill_before_consumption(self):
        s = state([card(1)], [card(2, attack=150, hp=50, seal='immortality'), card(3, hp=80)])
        self.assertEqual(arena.choose_action(s, 'attacker', hit_history=[])[1]['target_card_user_id'], 3)

    def test_high_crit_does_not_mean_guaranteed_kill(self):
        s = state([card(1, attack=100, crit=50)], [card(2, attack=150, hp=140), card(3, hp=80)])
        self.assertEqual(arena.choose_action(s, 'attacker', hit_history=[])[1]['target_card_user_id'], 3)

    def test_heal_saves_threatened_strong_card_instead_of_most_absolute_hp(self):
        s = state([card(1, attack=150, hp=50, max_hp=300), card(3, attack=10, hp=400, max_hp=1000)],
                  [card(2, attack=70, hp=400)],
                  {'used_this_round': False, 'blocked_kind': None})
        kind, payload = arena.choose_action(s, 'attacker', hit_history=[])
        self.assertEqual((kind, payload.get('kind'), payload.get('card_user_id')), ('tactic', 'heal', 1))

    def test_attack_does_not_choose_certain_self_death_by_thorns_when_safe_kill_exists(self):
        s = state([card(1, attack=100, hp=20)],
                  [card(2, attack=150, hp=80, seal='thorns'), card(3, attack=100, hp=80)])
        self.assertEqual(arena.choose_action(s, 'attacker', hit_history=[])[1]['target_card_user_id'], 3)

    def test_taunt_and_used_cards_remain_hard_constraints(self):
        s = state([card(1), card(4)], [card(2, hp=50, seal='taunt'), card(3, hp=30)])
        s['used_cards']['attacker'] = [1]
        action = arena.choose_action(s, 'attacker', hit_history=[])
        self.assertEqual(action, ('attack', {'from_card_user_id': 4, 'target_card_user_id': 2}))

    def test_missing_barrier_history_keeps_established_action(self):
        s = state([card(1)], [card(2, attack=150, hp=50, seal='barrier'), card(3, hp=80)])
        self.assertEqual(arena.choose_action(s, 'attacker', hit_history=None),
                         arena.choose_action_legacy(s, 'attacker', hit_history=None))

    def test_unknown_immortality_consumption_does_not_ignore_one_hp_enemy(self):
        s = state([card(1)], [card(2, attack=150, hp=1, max_hp=300, seal='immortality'),
                             card(3, hp=1000)])
        self.assertEqual(arena.choose_action(s, 'attacker', hit_history=None)[1]['target_card_user_id'], 2)

    def test_equal_kills_preserve_stronger_attacker_for_later(self):
        s = state([card(1, attack=195, crit=11), card(4, attack=222, crit=18)], [card(2, hp=64)])
        self.assertEqual(arena.choose_action(s, 'attacker', hit_history=[])[1]['from_card_user_id'], 1)

    def test_consumed_immortality_can_be_finished(self):
        s = state([card(1)], [card(2, attack=150, hp=50, seal='immortality'), card(3, hp=80)])
        s['battle_steps'] = [{'type': 'attack', 'effects': [{'type': 'immortal', 'card_user_id': 2}]}]
        self.assertEqual(arena.choose_action(s, 'attacker', hit_history=[])[1]['target_card_user_id'], 2)

    def test_nullable_server_history_is_safe(self):
        s = state([card(1)], [card(2, hp=50)])
        s['battle_steps'] = None
        self.assertIsNotNone(arena.choose_action(s, 'attacker', hit_history=[]))

    def test_state_is_never_mutated(self):
        s = state([card(1)], [card(2, hp=50)])
        old = copy.deepcopy(s)
        arena.choose_action(s, 'attacker', hit_history=[])
        self.assertEqual(s, old)

if __name__ == '__main__':
    unittest.main()
