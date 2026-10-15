"""Versioned approximate arena comparison, never an exact server battle solver.

Only banned slots are optimized. Inventory stats omit some server boosts; replay
cards contain effective stats. Win rates describe this model and sampled opponents,
not the site's real probability. All code is offline and uses the standard library.
"""
import math
import random

MODEL_VERSION = 'arena-approx-2026-10-14-v2'
BUFF_CODES = ('blood_rage', 'predator_eye', 'mad_charge', 'blood_thirst',
              'iron_will', 'second_breath', 'glass_cannon', 'executioner')
SEALS = dict(enumerate(('double_strike', 'rage', 'execution', 'berserk',
    'hunter', 'thorns', 'regeneration', 'barrier', 'immortality', 'revenge',
    'chain_attack', 'vampirism', 'phoenix', 'dodge', 'taunt'), 1))
DEFAULT_RULES = {'crit_multiplier': 2.0, 'jitter': .10, 'max_turns': 100,
                 'phoenix_once': True, 'own_buff': None,
                 'hunter_multiplier': 1.75,
                 'candidate_limit': 12, 'opponent_limit': 6}
ASSUMPTIONS = [
    'Alternating full-team turns, living cards in slot order; inferred from replay.',
    'Random living target, prioritizing taunt; equal choice among multiple taunts.',
    'Critical damage 2x and uniform +/-10% jitter unless calibrated rules supplied.',
    'Phoenix attempted once; berserk adds 30% per kill; extra attacks never recurse.',
    'Unobserved effect ordering and trigger interactions remain approximate.',
    'Inventory stats can omit global boosts; opponent replay stats are effective.',
]


def _number(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Nonfinite stat')
    return result


def _round(value):
    return math.floor(value + .5)


def from_inventory(card, energy=None):
    """Return a normalized card or None for unknown/invalid stats or seals."""
    try:
        sid = int(card['battle_seal_id'])
        result = {'id': int(card['id']), 'base_id': int(card['card_id']),
                  'attack': _number(card['battle_attack']),
                  'max_hp': _number(card['battle_health']),
                  'crit': _number(card['battle_crit_chance']),
                  'seal': SEALS[sid], 'seal_id': sid,
                  'in_trade': bool(int(card.get('in_trade') or 0)),
                  'energy': energy if energy is not None else card.get('battle_card_energy'),
                  'stats_source': 'inventory'}
        if result['id'] <= 0 or result['base_id'] <= 0 or result['attack'] <= 0 or result['max_hp'] <= 0 or not 0 <= result['crit'] <= 100:
            return None
        if result['energy'] is not None:
            result['energy'] = _number(result['energy'])
        return result
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def from_replay_card(card):
    """Replay hp is final HP, so reset to max_hp for a new comparison."""
    try:
        seal = card['seal_code']
        if seal not in SEALS.values():
            return None
        result = {'id': int(card['card_user_id']), 'base_id': None,
                  'attack': _number(card['attack']), 'max_hp': _number(card['max_hp']),
                  'crit': _number(card['crit_chance']), 'seal': seal,
                  'seal_id': next(k for k, v in SEALS.items() if v == seal),
                  'energy': card.get('battle_card_energy'), 'in_trade': False,
                  'stats_source': 'replay_effective', 'buff': card.get('buff_code')}
        if result['attack'] <= 0 or result['max_hp'] <= 0 or not 0 <= result['crit'] <= 100:
            return None
        return result
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def _state(card, buff, effective=False):
    c = dict(card, hp=card['max_hp'], first_attack=True, first_hit=True,
             saved=False, phoenix_used=False, kills=0, buff=buff)
    if not effective:
        if buff == 'blood_rage': c['attack'] *= 1.2
        if buff == 'predator_eye': c['crit'] = min(100, c['crit'] + 25)
        if buff == 'iron_will': c['max_hp'] *= 1.2
        if buff == 'glass_cannon':
            c['attack'] *= 1.45
            c['max_hp'] *= .75
        c['hp'] = c['max_hp']
    return c


def unbuff_owned(card, baseline_crit=None):
    """Remove the explicitly observed static buff, preserving per-copy boosts.

    Capped predator crit cannot be inverted from a replay alone; use a compatible
    inventory baseline when available, otherwise conservative 75 with disclosure.
    """
    c = dict(card)
    buff = c.get('buff')
    if buff == 'blood_rage': c['attack'] /= 1.2
    if buff == 'iron_will': c['max_hp'] /= 1.2
    if buff == 'glass_cannon':
        c['attack'] /= 1.45
        c['max_hp'] /= .75
    if buff == 'predator_eye':
        if c['crit'] >= 100:
            if baseline_crit is not None and 75 <= baseline_crit <= 100:
                c['crit'] = baseline_crit
            else:
                c['crit'] = 75
                c['crit_uncertain'] = 'Capped predator_eye cannot be uniquely inverted.'
        else: c['crit'] = max(0, c['crit'] - 25)
    c['buff'] = None
    c['stats_unbuffed'] = True
    return c


def simulate(team, opponent, seed=0, first_side=0, rules=None, opponent_buff=None):
    """Return (model score 0/0.5/1, surviving HP fraction difference)."""
    r = dict(DEFAULT_RULES, **(rules or {}))
    rng = random.Random(seed)
    sides = [[_state(c, r['own_buff'], c.get('stats_source') == 'replay_effective' and not c.get('stats_unbuffed')) for c in team],
             [_state(c, opponent_buff or c.get('buff'), c.get('stats_source') == 'replay_effective') for c in opponent]]
    second_breath = [False, False]

    def living(side):
        return [c for c in sides[side] if c['hp'] > 0]

    def target(side, exclude=None):
        pool = [c for c in living(side) if c is not exclude]
        taunts = [c for c in pool if c['seal'] == 'taunt']
        return rng.choice(taunts or pool) if pool else None

    def power(c):
        return c['attack'] * (1 + .3*c['kills'] if c['seal'] == 'berserk' else 1) * (1.3 if c['seal'] == 'rage' and c['hp'] < .5*c['max_hp'] else 1)

    def hurt(victim, source, damage, side, depth=0, direct=True):
        if victim['hp'] <= 0 or depth > 8:
            return 0, False
        if direct and victim['seal'] == 'dodge' and rng.random() < .20:
            return 0, False
        if direct and victim['first_hit'] and victim['seal'] == 'barrier':
            damage *= .30
        if direct: victim['first_hit'] = False
        before = victim['hp']
        victim['hp'] = max(0, before - max(0, _round(damage)))
        if victim['hp'] == 0 and victim['seal'] == 'immortality' and not victim['saved']:
            victim['saved'] = True
            victim['hp'] = 1
        dealt = max(0, before - victim['hp'])
        if victim['hp'] == 0:
            if victim['seal'] == 'phoenix' and (not r['phoenix_once'] or not victim['phoenix_used']):
                victim['phoenix_used'] = True
                if rng.random() < .35: victim['hp'] = _round(.35*victim['max_hp'])
            if victim['hp'] == 0 and victim['buff'] == 'second_breath' and not second_breath[side]:
                second_breath[side] = True
                victim['hp'] = _round(.30*victim['max_hp'])
        killed = victim['hp'] <= 0
        if direct and victim['seal'] == 'thorns' and source['hp'] > 0:
            hurt(source, victim, .30*dealt, 1-side, depth+1, False)
        if killed and victim['seal'] == 'revenge' and source['hp'] > 0:
            hurt(source, victim, power(victim), 1-side, depth+1, False)
        return dealt, killed

    def attack(actor, victim, side):
        damage = power(actor)
        if actor['first_attack']:
            if actor['seal'] == 'hunter': damage *= r['hunter_multiplier']
            if actor['buff'] == 'mad_charge': damage *= 1.55
        actor['first_attack'] = False
        if actor['seal'] == 'execution' and victim['hp'] < .35*victim['max_hp']: damage *= 1.35
        if rng.random() < actor['crit']/100 or (actor['buff'] == 'executioner' and victim['hp'] < .30*victim['max_hp']):
            damage *= r['crit_multiplier']
        damage *= rng.uniform(1-r['jitter'], 1+r['jitter'])
        dealt, killed = hurt(victim, actor, damage, 1-side)
        if actor['hp'] > 0:
            if actor['seal'] == 'vampirism': actor['hp'] = min(actor['max_hp'], actor['hp'] + _round(.25*dealt))
            if killed:
                actor['kills'] += 1
                if actor['buff'] == 'blood_thirst': actor['hp'] = min(actor['max_hp'], actor['hp'] + _round(.15*actor['max_hp']))

    for turn in range(int(r['max_turns'])):
        side = (first_side + turn) % 2
        for actor in sides[side]:
            if actor['hp'] <= 0: continue
            victim = target(1-side)
            if victim is None: break
            attack(actor, victim, side)
            if actor['hp'] > 0 and actor['seal'] in ('double_strike', 'chain_attack'):
                chance = .30 if actor['seal'] == 'double_strike' else .25
                if rng.random() < chance:
                    extra = target(1-side, victim if actor['seal'] == 'chain_attack' else None)
                    if extra: attack(actor, extra, side)
            if actor['hp'] > 0 and actor['seal'] == 'regeneration':
                actor['hp'] = min(actor['max_hp'], actor['hp'] + _round(.07*actor['max_hp']))
        if not living(0) or not living(1): break
    alive = [bool(living(i)) for i in (0, 1)]
    score = 1.0 if alive == [True, False] else 0.0 if alive == [False, True] else .5
    fractions = [sum(c['hp'] for c in s)/sum(c['max_hp'] for c in s) for s in sides]
    return score, fractions[0]-fractions[1]


def evaluate_buffs(team, opponents, availablecodes, rules=None, samples=32,
                   seed=743, cancelled=None):
    """Paired comparison of owned buffs; no consumption, selection or purchases.

    Return a ranked list. None always participates as a no-consumable baseline.
    Scores represent simulations against observed teams, not real win odds.
    """
    r = dict(DEFAULT_RULES, **(rules or {}))
    opposition = []
    for entry in (opponents or [])[-int(r['opponent_limit']):]:
        raw = entry.get('cards', []) if isinstance(entry, dict) else entry
        normalized = [c if 'seal' in c else from_replay_card(c) for c in raw]
        if len(normalized) == 4 and all(normalized):
            opposition.append((normalized, entry.get('buff') if isinstance(entry, dict) else None))
    if len(team) != 4 or not opposition: return []
    own = [unbuff_owned(c) if c.get('stats_source') == 'replay_effective' and not c.get('stats_unbuffed') else c for c in team]
    n = max(1, min(64, int(samples)))
    count = n * 2 * len(opposition)
    ranking = []
    codes = [None] + sorted(set(availablecodes or []) & set(BUFF_CODES))
    for code in codes:
        if cancelled and cancelled(): return []
        wins = hp = 0.0
        for index, (enemy, buff) in enumerate(opposition):
            for sample in range(n):
                if cancelled and cancelled(): return []
                for initiative in (0, 1):
                    w, residual = simulate(own, enemy, seed+index*10000+sample,
                                           initiative, dict(r, own_buff=code), buff)
                    wins += w; hp += residual
        ranking.append({'code': code, 'modelled_win_rate': wins/count,
                        'hp_margin': hp/count, 'sample_count': count,
                        'model_version': MODEL_VERSION,
                        'uncertainty': 'Approximate buff and seal ordering; sampled opponent pool, not real win odds.'})
    ranking.sort(key=lambda c: (c['modelled_win_rate'], c['hp_margin']), reverse=True)
    return ranking


def heuristic(card):
    """Seal-aware shortlist proxy, not an effectiveness probability."""
    attack, hp = card['attack'], card['max_hp']
    damage = attack * (1+card['crit']/100)
    seal = card['seal']
    damage *= {'double_strike': 1.3, 'chain_attack': 1.25, 'hunter': 1.13,
               'rage': 1.12, 'execution': 1.10, 'berserk': 1.18,
               'revenge': 1.15, 'thorns': 1.15}.get(seal, 1)
    hp *= {'regeneration': 1.45, 'vampirism': 1.25, 'phoenix': 1.1225,
           'immortality': 1.12, 'barrier': 1.10, 'dodge': 1.25}.get(seal, 1)
    return damage * math.sqrt(hp)


def choose_replacement(current_ids, cards, banned_id, opponent_teams, rules=None,
                       seed=431, samples=32, cancelled=None):
    """Return (IDs, diagnostics); None means unsafe/unavailable/cancelled.

    Opponents may be normalized lists or {'cards': list, 'buff': code}.
    Multiple banned slots use bounded coordinate selection, not exhaustive search.
    """
    r = dict(DEFAULT_RULES, **(rules or {}))
    known = {}
    for raw in cards:
        c = raw if 'seal' in raw else from_inventory(raw)
        if c: known[c['id']] = c
    try: ids = [int(i) for i in current_ids]
    except (TypeError, ValueError): return None
    if len(ids) != 4 or len(set(ids)) != 4 or any(i not in known for i in ids): return None
    slots = [i for i, cid in enumerate(ids) if known[cid]['seal_id'] == banned_id]
    def legal(c):
        return not c['in_trade'] and c['seal_id'] != banned_id and (c['energy'] is None or c['energy'] > 0)
    preserved = [known[cid] for i, cid in enumerate(ids) if i not in slots]
    if any(not legal(c) for c in preserved) or len({c['base_id'] for c in preserved}) != len(preserved): return None
    opponents = []
    for entry in (opponent_teams or [])[-int(r['opponent_limit']):]:
        raw = entry.get('cards', []) if isinstance(entry, dict) else entry
        team = [c if 'seal' in c else from_replay_card(c) for c in raw]
        if len(team) == 4 and all(team):
            opponents.append((team, entry.get('buff') if isinstance(entry, dict) else None))
    pool = sorted((c for c in known.values() if legal(c)), key=lambda c: (heuristic(c), c['id']), reverse=True)
    # Seed every missing slot legally before team simulations.
    for slot in slots:
        others = [known[cid] for i, cid in enumerate(ids) if i not in slots or i < slot]
        c = next((c for c in pool if all(c['id'] != o['id'] and c['base_id'] != o['base_id'] for o in others)), None)
        if c is None: return None
        ids[slot] = c['id']
    selections = []
    n = max(1, min(64, int(samples)))
    for slot in slots:
        others = [known[cid] for i, cid in enumerate(ids) if i != slot]
        candidates = [c for c in pool if all(c['id'] != o['id'] and c['base_id'] != o['base_id'] for o in others)][:max(1, min(12, int(r['candidate_limit'])))]
        scores = []
        for c in candidates:
            if cancelled and cancelled(): return None
            trial = list(ids); trial[slot] = c['id']
            wins = residual = 0.0
            count = n*2*len(opponents)
            for opponent_index, (opp, buff) in enumerate(opponents):
                for sample in range(n):
                    if cancelled and cancelled(): return None
                    for initiative in (0, 1):
                        w, hp = simulate([known[i] for i in trial], opp,
                                         seed+opponent_index*10000+sample, initiative, r, buff)
                        wins += w; residual += hp
            scores.append({'id': c['id'], 'modelled_win_rate': wins/count if count else None,
                           'hp_margin': residual/count if count else None,
                           'heuristic': heuristic(c), 'sample_count': count})
        scores.sort(key=lambda s: (s['modelled_win_rate'] if s['modelled_win_rate'] is not None else 0,
                                  s['hp_margin'] if s['hp_margin'] is not None else 0, s['heuristic']), reverse=True)
        best = scores[0]; ids[slot] = best['id']
        margin = (best['modelled_win_rate']-scores[1]['modelled_win_rate']) if len(scores)>1 and opponents else None
        selections.append({'slot': slot, 'best': best, 'runner_up': scores[1] if len(scores)>1 else None,
                           'win_rate_margin': margin, 'candidate_count': len(scores)})
    return ids, {'model_version': MODEL_VERSION, 'method': 'team_monte_carlo' if opponents else 'seal_heuristic',
                 'opponent_count': len(opponents), 'selections': selections,
                 'assumptions': list(ASSUMPTIONS), 'rules': r,
                 'uncertainty': 'Model parameter and opponent sampling uncertainty unquantified; no real win probability claim.',
                 'near_tie': any(s['win_rate_margin'] is not None and s['win_rate_margin'] < .05 for s in selections)}
