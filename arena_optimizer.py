"""Pure, bounded whole-team search against sampled opponents.

This improves the best *found* lineup in the approximate combat model. It cannot
prove a global optimum or predict an unseen seasonal metagame.
"""
import itertools
import math
import time

from battle_effectiveness import (ASSUMPTIONS, MODEL_VERSION, SEALS,
                                 from_inventory, from_replay_card, heuristic,
                                 simulate)


def _normalized(cards, energy_map=None):
    result = {}
    for raw in cards:
        try:
            c = dict(raw) if 'seal' in raw else from_inventory(raw)
            if not c:
                continue
            c.setdefault('seal_id', next(k for k, v in SEALS.items() if v == c['seal']))
            c.setdefault('in_trade', False)
            c.setdefault('energy', None)
            if energy_map and c['id'] in energy_map:
                c['energy'] = energy_map[c['id']]
            if (int(c['id']) <= 0 or not c.get('base_id') or
                    c['seal'] not in SEALS.values() or
                    any(not math.isfinite(float(c[k])) for k in ('attack', 'max_hp', 'crit')) or
                    c['attack'] <= 0 or c['max_hp'] <= 0 or not 0 <= c['crit'] <= 100):
                continue
            result[int(c['id'])] = c
        except (ValueError, TypeError, KeyError, StopIteration):
            continue
    return result


def _frontier(cards):
    """Protect specialized cards even when their scalar stat score is lower."""
    return [c for c in cards if not any(
        d['id'] != c['id'] and all(d[k] >= c[k] for k in ('attack', 'max_hp', 'crit'))
        and any(d[k] > c[k] for k in ('attack', 'max_hp', 'crit')) for d in cards)]


def protected_card_ids(cards, current_ids=(), best_ids=(), bench_size=12,
                       per_seal=2, min_relative_score=.80, **kwargs):
    """Protect selected teams and competitive reserves, not every outlier stat.

    The reserve threshold is relative to the strongest owned stat combination.
    A weak seal-local Pareto point alone is never evidence of combat usefulness.
    Selected teams remain protected regardless of this shortlist threshold.
    """
    known = _normalized(cards)
    protected = set(current_ids) | set(best_ids)
    ordered = sorted(known.values(), key=heuristic, reverse=True)
    if not ordered:return protected
    def stat_quality(c):return c['attack']*(1+c['crit']/100)*math.sqrt(c['max_hp'])
    threshold=max(stat_quality(c) for c in ordered)*min_relative_score
    competitive=[c for c in ordered if stat_quality(c)>=threshold and not c['in_trade']]
    protected.update(c['id'] for c in competitive[:max(0, bench_size)])
    for sid in SEALS:
        group = [c for c in competitive if c['seal_id'] == sid]
        protected.update(c['id'] for c in group[:max(1, per_seal)])
        protected.update(c['id'] for c in _frontier(group))
    return protected


def _shortlist(legal, current_ids, limit):
    ordered = sorted(legal, key=lambda c: (heuristic(c), c['id']), reverse=True)
    chosen = {c['id']: c for c in ordered if c['id'] in current_ids}
    # Preserve one strong representative of each seal before filling scalar tops.
    for sid in SEALS:
        group = [c for c in ordered if c['seal_id'] == sid]
        if group and len(chosen) < limit:
            chosen[group[0]['id']] = group[0]
    for key in ('max_hp', 'attack', 'crit'):
        if ordered and len(chosen) < limit:
            c = max(ordered, key=lambda c: c[key])
            chosen[c['id']] = c
    for c in ordered:
        if len(chosen) >= limit:
            break
        chosen[c['id']] = c
    return sorted(chosen.values(), key=heuristic, reverse=True)


def optimize_deck(current_ids, cards, banned_id, opponent_teams, rules=None,
                  energy_map=None, cancelled=None, budget_seconds=12):
    """Return (ordered four IDs, diagnostics) or None if unsafe/unavailable.

    Unknown energy is allowed but must be checked by integration before saving.
    Known energy below 10 is unavailable. Uses paired seeds and both initiatives;
    hysteresis only applies when the current deck is legal. No live side effects.
    """
    start = time.monotonic()
    deadline = start + max(0, float(budget_seconds))
    search_deadline = start + max(0, float(budget_seconds)) * .55
    rules = dict(rules or {})
    if cancelled and cancelled():
        return None
    known = _normalized(cards, energy_map)
    try:
        current = tuple(int(i) for i in current_ids)
    except (ValueError, TypeError):
        return None
    def available(c):
        try:
            return (not c['in_trade'] and c['seal_id'] != banned_id and
                    (c['energy'] is None or math.isfinite(float(c['energy'])) and float(c['energy']) >= 10))
        except (ValueError, TypeError):
            return False
    def valid(ids):
        return (len(ids) == 4 and len(set(ids)) == 4 and
                all(i in known and available(known[i]) for i in ids) and
                len({known[i]['base_id'] for i in ids}) == 4)
    legal = [c for c in known.values() if available(c)]
    if len({c['base_id'] for c in legal}) < 4:
        return None
    opponents = []
    for entry in opponent_teams or []:
        raw = entry.get('cards', []) if isinstance(entry, dict) else entry
        team = [c if 'seal' in c else from_replay_card(c) for c in raw]
        if len(team) == 4 and all(team):
            opponents.append((team, entry.get('buff') if isinstance(entry, dict) else None,
                              entry.get('source', 'sample') if isinstance(entry, dict) else 'sample'))
    if not opponents:
        return None  # No invented opponents or automatic heuristic-only swaps.
    opponents = opponents[-max(1, min(8, int(rules.get('opponent_limit', 6)))):]
    pool = _shortlist(legal, current, max(18, min(24, int(rules.get('pool_limit', 24)))))
    pool_ids = [c['id'] for c in pool]
    cache = {}
    stopped = False
    def expired():
        return time.monotonic() >= deadline
    def evaluate(ids, samples):
        nonlocal stopped
        key = (tuple(ids), samples)
        if key in cache:
            return cache[key]
        values, wins, hps = [], [], []
        for n in range(samples):
            for team, buff, _ in opponents:
                for side in (0, 1):
                    if cancelled and cancelled():
                        stopped = True
                        return None
                    if expired():
                        return None
                    win, hp = simulate([known[i] for i in ids], team,
                                       seed=431 + n * 7919, first_side=side,
                                       rules=rules, opponent_buff=buff)
                    values.append(win + .03 * hp)
                    wins.append(win)
                    hps.append(hp)
        result = {'utility': sum(values)/len(values), 'win_rate': sum(wins)/len(wins),
                  'hp_margin': sum(hps)/len(hps), 'values': values,
                  'battles': len(values)}
        cache[key] = result
        return result
    def seed_team(order):
        ids, bases = [], set()
        for cid in order:
            if known[cid]['base_id'] not in bases:
                ids.append(cid)
                bases.add(known[cid]['base_id'])
            if len(ids) == 4:
                return tuple(ids)
    seeds = []
    if valid(current):
        seeds.append(current)
    seeds.append(seed_team(pool_ids))
    for key in ('max_hp', 'attack'):
        seeds.append(seed_team([c['id'] for c in sorted(pool, key=lambda c: c[key], reverse=True)]))
    # Multistart ensures a lower-stat tank / sustain team can enter the beam.
    for seal in ('taunt', 'regeneration', 'vampirism'):
        front = [c['id'] for c in pool if c['seal'] == seal]
        if front:
            seeds.append(seed_team(front + pool_ids))
    coarse = max(1, min(8, int(rules.get('search_samples', 2))))
    scored = {}
    for ids in dict.fromkeys(seeds):
        score = evaluate(ids, coarse)
        if score:
            scored[ids] = score
    if not scored or stopped:
        return None
    # Search combinations and orders jointly; retain diverse starts, not one path.
    for _ in range(5):
        beam = sorted(scored, key=lambda ids: scored[ids]['utility'], reverse=True)[:4]
        previous = beam[0]
        for ids in beam:
            candidates = list(itertools.permutations(ids))
            for slot in range(4):
                for cid in pool_ids:
                    trial = ids[:slot] + (cid,) + ids[slot+1:]
                    if valid(trial):
                        candidates.append(trial)
            for trial in dict.fromkeys(candidates):
                if trial not in scored:
                    score = evaluate(trial, coarse)
                    if score:
                        scored[trial] = score
                if time.monotonic() >= search_deadline or stopped:
                    break
            if time.monotonic() >= search_deadline or stopped:
                break
        if time.monotonic() >= search_deadline or stopped or max(scored, key=lambda ids: scored[ids]['utility']) == previous:
            break
    if stopped:
        return None
    finalists = sorted(scored, key=lambda ids: scored[ids]['utility'], reverse=True)[:4]
    baseline_legal = valid(current)
    if baseline_legal and current not in finalists:
        finalists.append(current)
    refined = {}
    samples = max(coarse, min(128, int(rules.get('refine_samples', 24))))
    # Current is measured first to make every accepted swap a paired comparison.
    if baseline_legal:
        finalists = [current] + [ids for ids in finalists if ids != current]
    for ids in finalists:
        score = evaluate(ids, samples)
        if score:
            refined[ids] = score
        if stopped:
            return None
    # If budget exhausted, retain legal current rather than accept a coarse gain.
    selected = max(refined, key=lambda ids: refined[ids]['utility']) if refined else (
        current if baseline_legal else max(scored, key=lambda ids: scored[ids]['utility']))
    candidate = selected
    paired_se = None
    gain = None
    hysteresis = False
    if baseline_legal and selected != current:
        if current not in refined or selected not in refined:
            selected = current
            hysteresis = True
        else:
            diffs = [a-b for a,b in zip(refined[selected]['values'], refined[current]['values'])]
            gain = sum(diffs)/len(diffs)
            paired_se = (math.sqrt(sum((d-gain)**2 for d in diffs)/(len(diffs)-1)/len(diffs))
                         if len(diffs) > 1 else 1)
            if gain <= max(float(rules.get('swap_margin', .025)), 1.5*paired_se):
                selected = current
                hysteresis = True
    def public(score):
        return {k:v for k,v in score.items() if k != 'values'} if score else None
    return list(selected), {
        'method': 'bounded_whole_deck_search', 'model_version': MODEL_VERSION,
        'baseline': public(refined.get(current) or scored.get(current)),
        'best': public(refined.get(selected) or scored.get(selected)),
        'candidate_ids': list(candidate),
        'candidate_score': public(refined.get(candidate) or scored.get(candidate)),
        'refined_lineups': len(refined),
        'pool_ids': pool_ids, 'evaluated_lineups': len(scored),
        'opponent_count': len(opponents), 'opponent_sources': [s for _,_,s in opponents],
        'paired_gain': gain, 'paired_standard_error': paired_se,
        'hysteresis_retained': hysteresis, 'budget_exhausted': expired(),
        'elapsed_seconds': time.monotonic()-start,
        'uncertainty': 'Sampling error only; hidden rules and opponent selection remain uncertain.',
        'assumptions': list(ASSUMPTIONS), 'unknown_energy_ids': [i for i in selected if known[i]['energy'] is None],
    }
