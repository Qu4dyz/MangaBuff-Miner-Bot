"""Pure, conservative research policy. No requests or automatic purchase/retries.

Rolls replace the previous result. The server's potential_rarity changed on
the same copy in live tests; it is not treated as an immutable stat ceiling.
Observations from another copy do not establish this copy's distribution.
The caller must persist the ledger reservation BEFORE performing a mutation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from math import isfinite, sqrt
from statistics import mean
from datetime import datetime, timezone
from typing import Callable

STAT_KEYS = ('attack', 'health', 'crit_chance')
SEAL_COST = 500


def _number(value):
    result = float(str(value).replace(' ', '').replace('\xa0', ''))
    if not isfinite(result) or result < 0:
        raise ValueError('Invalid stat')
    return result


def parse_roll_card(response: dict) -> dict:
    """Normalize API/inventory records; never manufacture missing statistics."""
    source = response.get('card', response)
    if not isinstance(source, dict):
        raise ValueError('Missing card')
    result = dict(source)
    for key, inventory_key in zip(STAT_KEYS, ('battle_attack', 'battle_health', 'battle_crit_chance')):
        raw = source.get(key, source.get(inventory_key))
        if raw is None:
            raise ValueError('Missing ' + key)
        result[key] = _number(raw)
    if result['health'] <= 0 or result['crit_chance'] > 100:
        raise ValueError('Invalid HP/crit')
    result['card_user_id'] = str(source.get('card_user_id', source.get('id', response.get('card_user_id', ''))))
    seal = source.get('seal')
    if isinstance(seal, dict):
        result['seal_code'] = seal.get('code') or source.get('seal_code')
    else:
        result['seal_code'] = source.get('seal_code', source.get('battle_seal_id'))
    return result


def fingerprint(card: dict) -> tuple:
    c = parse_roll_card(card)
    return tuple(c[k] for k in STAT_KEYS) + (c.get('seal_code'),)


def stat_lock_options():
    return [{'locked_stats': list(keys), 'cost': 300 * 2 ** len(keys)}
            for count in range(3) for keys in combinations(STAT_KEYS, count)]


def quantile(values, probability):
    values = sorted(_number(v) for v in values)
    if not values or not 0 <= probability <= 1:
        raise ValueError('Empty observations/invalid quantile')
    position = (len(values) - 1) * probability
    lo = int(position)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def empirical_thresholds(samples):
    cards = [parse_roll_card(sample) for sample in samples]
    return {key: {'median': quantile([c[key] for c in cards], .5),
                  'p90': quantile([c[key] for c in cards], .9)} for key in STAT_KEYS} if cards else {}


def _flag(value):
    return value is True or str(value).lower() in ('1', 'true', 'yes')


def _unsafe(card, protected):
    copy_id = str(card.get('card_user_id', card.get('id', '')))
    return (not copy_id or copy_id in {str(i) for i in protected}
            or any(_flag(card.get(k)) for k in ('is_locked', 'is_lock', 'in_trade', 'is_in_trade', 'is_active', 'is_best', 'protected')))


def _score(card):
    return card['attack'] * (1 + card['crit_chance'] / 100) * sqrt(card['health'])


def choose_test_card(cards, protected):
    """Weakest eligible copy; exclude base identities used by protected copies."""
    protected = {str(i) for i in protected}
    protected_bases = {str(c['card_id']) for c in cards
                       if str(c.get('card_user_id', c.get('id', ''))) in protected and c.get('card_id') is not None}
    eligible = []
    for card in cards:
        if _unsafe(card, protected) or str(card.get('card_id', '')) in protected_bases:
            continue
        try:
            eligible.append(parse_roll_card(card))
        except (ValueError, TypeError):
            continue
    return min(eligible, key=lambda c: (_score(c), c['card_user_id'])) if eligible else None


def plan_reroll(card, samples, budget, protectedIDs, evaluator: Callable | None = None):
    """Return one research action. Caller must re-protect newly strong results.

    Empirical expected gain uses only observed same-copy outcomes, without
    claiming a uniform RNG or known potential ceiling. A supplied evaluator
    should evaluate the changed card in its intended team against the meta.
    Without it seals are research only, never ranked by made-up effect weights.
    """
    if _unsafe(card, protectedIDs):
        return None
    current = parse_roll_card(card)
    observed = []
    for sample in samples:
        try:
            outcome = parse_roll_card(sample)
        except (ValueError, TypeError):
            continue
        if outcome['card_user_id'] == current['card_user_id']:
            observed.append((sample.get('type', sample.get('mode', 'stats')), outcome, sample.get('locked_stats', [])))
    if len(observed) < 3:
        # Bounded exploration, at most one mutation returned, budget remains external.
        kind = 'seal' if observed and not any(mode == 'seal' for mode, _, _ in observed) else 'stats'
        cost = SEAL_COST if kind == 'seal' else 300
        return {'type': kind, 'locked_stats': [], 'cost': cost, 'card_user_id': current['card_user_id'],
                'rationale': 'weak-copy research; insufficient same-copy observations', 'research': True} if budget >= cost else None
    evaluate = evaluator or _score
    baseline = evaluate(current)
    actions = []
    # Preserved statistics are not RNG observations. Use full fresh roll
    # vectors so measured correlations between stats survive the estimate.
    stats = [c for mode, c, locks in observed if mode == 'stats' and not locks]
    for option in stat_lock_options():
        if option['cost'] > budget or len(stats) < 3:
            continue
        gains = []
        for outcome in stats:
            candidate = dict(current)
            for key in STAT_KEYS:
                if key not in option['locked_stats']:
                    candidate[key] = outcome[key]
            gains.append(evaluate(candidate) - baseline)
        gain = mean(gains)
        if gain > 0:
            actions.append({**option, 'type': 'stats', 'expected_gain': gain, 'gain_per_essence': gain / option['cost']})
    seals = [c for mode, c, _ in observed if mode == 'seal']
    if evaluator and len(seals) >= 3 and budget >= SEAL_COST:
        gain = mean(evaluate({**current, 'seal_code': c.get('seal_code'), 'seal': c.get('seal')}) - baseline for c in seals)
        if gain > 0:
            actions.append({'type': 'seal', 'locked_stats': [], 'cost': SEAL_COST,
                            'expected_gain': gain, 'gain_per_essence': gain / SEAL_COST})
    if not actions:
        return None
    result = max(actions, key=lambda a: a['gain_per_essence'])
    result.update(card_user_id=current['card_user_id'], research=True,
                  rationale='empirical same-copy expected gain; replacement can worsen unlocked values')
    return result


@dataclass
class TestLedger:
    """One unresolved mutation at a time; uncertainty never refunds a reservation."""
    budget: int
    protected_ids: set = field(default_factory=set)
    spent: int = 0
    day: str = field(default_factory=lambda: datetime.now(timezone.utc).date().isoformat())
    pending: dict | None = None
    entries: list = field(default_factory=list)
    protected_fingerprints: dict = field(default_factory=dict)
    observed_balance: int | None = None

    @property
    def remaining(self):
        return max(0, self.budget - self.spent)

    def shield(self, cards):
        for card in cards:
            copy_id = str(card.get('card_user_id', card.get('id', '')))
            self.protected_ids.add(copy_id)
            self.protected_fingerprints[copy_id] = fingerprint(card)

    def assert_protected_unchanged(self, cards):
        actual = {str(c.get('card_user_id', c.get('id', ''))): fingerprint(c) for c in cards}
        if any(actual.get(cid) != value for cid, value in self.protected_fingerprints.items()):
            raise ValueError('Protected card missing or changed')

    def reserve(self, action, card, balance=None):
        if self.pending is not None:
            raise ValueError('Unresolved mutation; reconcile first, never replay')
        if _unsafe(card, self.protected_ids) or str(action.get('card_user_id')) != str(card.get('card_user_id', card.get('id', ''))):
            raise ValueError('Protected or mismatched card')
        kind = action.get('type')
        locks = action.get('locked_stats', [])
        if kind not in ('stats', 'seal') or len(set(locks)) != len(locks) or len(locks) > 2 or any(k not in STAT_KEYS for k in locks) or (kind == 'seal' and locks):
            raise ValueError('Invalid mutation')
        cost = 500 if kind == 'seal' else 300 * 2 ** len(locks)
        available = self.observed_balance if balance is None else balance
        if action.get('cost') != cost or cost > self.remaining or (available is not None and cost > available):
            raise ValueError('Budget/cost mismatch')
        self.spent += cost
        self.pending = {**action, 'timestamp': datetime.now(timezone.utc).isoformat(), 'day': self.day,
                        'balance_before': available, 'before': fingerprint(card)}
        self.entries.append(dict(self.pending))
        # Serialize this object now, before send. No network operation in this module.
        return dict(self.pending)

    def reconcile(self, balance_after, *, resolved=False):
        balance_after = int(balance_after)
        if balance_after < 0:
            raise ValueError('Invalid balance')
        self.observed_balance = balance_after
        if self.pending and self.pending.get('balance_before') is not None:
            actual_spend = max(0, self.pending['balance_before'] - balance_after)
            excess = max(0, actual_spend - self.pending['cost'])
            accounted = self.pending.get('excess_accounted', 0)
            self.spent += max(0, excess - accounted)
            self.pending['excess_accounted'] = max(accounted, excess)
        # Balance alone cannot identify which action succeeded. Caller needs card re-read.
        if resolved:
            self.pending = None

    def to_dict(self):
        return {**self.__dict__, 'protected_ids': sorted(str(i) for i in self.protected_ids),
                'protected_fingerprints': {k: list(v) for k, v in self.protected_fingerprints.items()}}

    @classmethod
    def from_dict(cls, value):
        data = dict(value)
        data['protected_ids'] = set(data.get('protected_ids', []))
        data['protected_fingerprints'] = {k: tuple(v) for k, v in data.get('protected_fingerprints', {}).items()}
        return cls(**data)

