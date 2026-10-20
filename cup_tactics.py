"""Fast, seal-aware Cup choices. No I/O and no stochastic planning."""
import math

def alive(c):
    return c.get('alive', True) and c.get('is_alive', True) and float(c.get('hp', 0)) > 0

def profile(actor, target, state, side, history, rules, boost=False):
    """Discrete expected hit estimate; kill probability includes survival effects."""
    attack = float(actor['attack'])
    seal = actor.get('seal_code')
    first = history is not None and not any(a == int(actor['card_user_id']) for a, _ in history)
    if first and seal == 'hunter':
        attack *= rules['hunter_multiplier']
    if first and actor.get('buff_code') == 'mad_charge':
        attack *= 1.55
    if seal == 'rage' and actor['hp'] < actor['max_hp'] * .5:
        attack *= rules['rage_multiplier']
    if seal == 'execution' and target['hp'] < target['max_hp'] * .35:
        attack *= rules['execution_multiplier']
    active_boost = state.get('tactics', {}).get(side, {}).get('boost')
    if boost or (active_boost and int(active_boost.get('card_user_id', 0)) == int(actor['card_user_id'])):
        attack *= 1.3
    crit = max(0, min(1, float(actor.get('crit_chance', 0)) / 100))
    if actor.get('buff_code') == 'executioner' and target['hp'] < target['max_hp'] * .3:
        crit = 1
    tseal = target.get('seal_code')
    effects = [e for s in (state.get('battle_steps') or []) for e in s.get('effects', [])]
    struck = (history is not None and any(t == int(target['card_user_id']) for _, t in history)) or any(
        e.get('type') == 'shield' and e.get('card_user_id') == target['card_user_id'] for e in effects)
    if tseal == 'barrier' and not struck:
        attack *= 1 - rules['barrier_block']
    dodge = rules['dodge_chance'] if tseal == 'dodge' else 0
    immortal_spent = any(e.get('type') == 'immortal' and e.get('card_user_id') == target['card_user_id'] for e in effects)
    survival = 1 if tseal == 'immortality' and not immortal_spent else .35 if tseal == 'phoenix' else 0
    if target.get('buff_code') == 'second_breath':
        survival = 1  # Consumption is not public in every captured state.
    expected = kill = suicide = 0.0
    for is_crit, chance in ((False, 1 - crit), (True, crit)):
        for i in range(9):
            damage = math.floor(attack * (rules['crit_multiplier'] if is_crit else 1)
                                * (1 - rules['jitter'] + 2 * rules['jitter'] * (i + .5) / 9) + .5)
            weight = chance * (1 - dodge) / 9
            dealt = min(float(target['hp']), max(0, damage))
            lethal = damage >= float(target['hp'])
            expected += weight * dealt
            kill += weight * lethal * (1 - survival)
            reflected = rules['thorns_reflect'] * dealt if tseal == 'thorns' else 0
            revenge = float(target['attack']) if tseal == 'revenge' and lethal else 0
            suicide += weight * (reflected + revenge >= float(actor['hp']))
    return {'damage': round(expected, 8), 'kill': round(kill, 8), 'suicide': round(suicide, 8), 'attack': attack}

def choose(state, side, baseline, history, taunt_ids, rules):
    if baseline is None:
        return None
    other = 'defender' if side == 'attacker' else 'attacker'
    # Opponent secret target/effect is hidden. Keep the established action there.
    if state.get('tactics', {}).get(other, {}).get('secret_active'):
        return baseline
    known_seals = {None, '', 'hunter', 'rage', 'execution', 'barrier', 'dodge', 'immortality',
                   'phoenix', 'thorns', 'revenge', 'regeneration', 'vampirism',
                   'taunt', 'double_strike', 'chain_attack'}
    known_buffs = {None, '', 'blood_rage', 'predator_eye', 'mad_charge', 'blood_thirst',
                   'iron_will', 'glass_cannon', 'executioner', 'second_breath'}
    if any(c.get('seal_code') not in known_seals or c.get('buff_code') not in known_buffs
           for team in state.get('final', {}).values() for c in team if alive(c)):
        return baseline
    own = [c for c in state.get('final', {}).get(side, []) if alive(c)]
    enemy = [c for c in state.get('final', {}).get(other, []) if alive(c)]
    used = set(map(int, state.get('used_cards', {}).get(side, [])))
    enemy_used = set(map(int, state.get('used_cards', {}).get(other, [])))
    ready = [c for c in own if int(c['card_user_id']) not in used]
    forced = [c for c in enemy if int(c['card_user_id']) in taunt_ids or c.get('taunt_active')
              or c.get('seal_code') == 'taunt']
    targets = forced or enemy
    if history is None and any(t.get('seal_code') in ('barrier', 'immortality', 'phoenix') for t in targets):
        return baseline
    if any(t.get('buff_code') == 'second_breath' for t in targets):
        return baseline
    if not ready or not targets:
        return baseline

    def attack_score(a, t, boosted=False):
        p = profile(a, t, state, side, history, rules, boosted)
        threat = float(t['attack']) * (1 if int(t['card_user_id']) not in enemy_used else .25)
        return (p['kill'] >= .95 and p['suicide'] < .05,
                p['kill'] * threat - p['suicide'] * float(a['attack']),
                p['kill'], p['damage'] / math.sqrt(max(1, float(t['hp']))),
                -p['attack'], -int(a['card_user_id']), -int(t['card_user_id']))

    attacker, target = max(((a, t) for a in ready for t in targets), key=lambda pair: attack_score(*pair))
    tactic = state.get('tactics', {}).get(side, {})
    if tactic and not tactic.get('used_this_round'):
        if tactic.get('blocked_kind') != 'heal':
            rescues = []
            for c in own:
                recovered = min(math.floor(.15 * float(c['max_hp']) + .5), float(c['max_hp']) - float(c['hp']))
                if recovered <= 0:
                    continue
                healed = dict(c, hp=float(c['hp']) + recovered)
                risks = [(profile(e, c, state, other, history, rules)['kill'],
                          profile(e, healed, state, other, history, rules)['kill'])
                         for e in enemy if int(e['card_user_id']) not in enemy_used]
                gain = max((before - after for before, after in risks), default=0)
                if gain >= .5:
                    rescues.append((gain * float(c['attack']), recovered, -int(c['card_user_id']), c))
            if rescues:
                c = max(rescues, key=lambda r: r[:3])[3]
                return ('tactic', {'kind': 'heal', 'card_user_id': int(c['card_user_id'])})
        if tactic.get('blocked_kind') != 'boost' and not tactic.get('boost'):
            boosts = []
            for a in ready:
                for t in targets:
                    before = profile(a, t, state, side, history, rules)
                    after = profile(a, t, state, side, history, rules, True)
                    if after['kill'] >= .95 and after['kill'] - before['kill'] >= .35 and after['suicide'] < .05:
                        boosts.append((attack_score(a, t, True), a))
            if boosts:
                a = max(boosts, key=lambda row: row[0])[1]
                return ('tactic', {'kind': 'boost', 'card_user_id': int(a['card_user_id'])})
        # Preserve existing secret placement and non-urgent healing behavior.
        if baseline[0] == 'tactic' and baseline[1].get('kind') != 'boost':
            return baseline
    return ('attack', {'from_card_user_id': int(attacker['card_user_id']),
                       'target_card_user_id': int(target['card_user_id'])})
