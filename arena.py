# -*- coding: utf-8 -*-
"""Arena subsystem: combat modeling, deck optimization, card protection,
awakening, research policy, and Evening Cup controller.
"""
from __future__ import annotations

import hashlib
from collections import Counter
import itertools
import json
import math
import os
import random
import re
import statistics
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import combinations
from math import isfinite, sqrt
from pathlib import Path
from statistics import mean
from typing import Callable
from urllib.parse import urljoin, urlparse, urlsplit

from bs4 import BeautifulSoup

import battle_store
from config import CONFIG
from data_management import DataManager
from http_session import is_anti_bot_page
from path import BASE_DIR
from utils import get_seconds_until_midnight_msk

# ==========================================
# CONSTANTS & CONFIGURATION
# ==========================================
MODEL_VERSION = 'arena-approx-2026-10-14-v2'
MANAGEMENT_FILE = os.path.join(BASE_DIR, 'arena_management.json')
RESEARCH_FILE = os.path.join(BASE_DIR, 'arena_research.json')
ORIGIN = 'https://mangabuff.ru'

BUFF_CODES = ('blood_rage', 'predator_eye', 'mad_charge', 'blood_thirst',
              'iron_will', 'second_breath', 'glass_cannon', 'executioner')

SEALS = dict(enumerate(('double_strike', 'rage', 'execution', 'berserk',
    'hunter', 'thorns', 'regeneration', 'barrier', 'immortality', 'revenge',
    'chain_attack', 'vampirism', 'phoenix', 'dodge', 'taunt'), 1))

SEAL_STEMS = {1: 'двойн', 2: 'ярост', 3: 'казн', 4: 'берсерк', 5: 'охот', 6: 'шип',
              7: 'регенерац', 8: 'барьер', 9: 'бессмерт', 10: 'мест', 11: 'цепн',
              12: 'вампир', 13: 'феникс', 14: 'уворот', 15: 'провокац'}

BUFF_NAMES = {
    'Кровавая ярость': 'blood_rage',
    'Глаз хищника': 'predator_eye',
    'Безумный натиск': 'mad_charge',
    'Жажда крови': 'blood_thirst',
    'Железная воля': 'iron_will',
    'Второе дыхание': 'second_breath',
    'Стеклянная пушка': 'glass_cannon',
    'Палач': 'executioner',
}

# Site rule: a deck may hold at most two cards with the same seal (HTTP 422 otherwise)
MAX_SAME_SEAL = 2

DEFAULT_RULES = {
    'crit_multiplier': 2.0, 'jitter': .10, 'max_turns': 100,
    'phoenix_once': True, 'own_buff': None,
    'hunter_multiplier': 1.75,
    'candidate_limit': 12, 'opponent_limit': 12,
    # Seal effects; checked against replays by arena_analysis.measure()
    'dodge_chance': .20, 'barrier_block': .70, 'regeneration_heal': .07,
    'vampirism_heal': .25, 'thorns_reflect': .30, 'double_strike_chance': .30,
    'chain_attack_chance': .25, 'rage_multiplier': 1.3, 'execution_multiplier': 1.35,
}

ASSUMPTIONS = [
    'Alternating full-team turns, living cards in slot order; inferred from replay.',
    'Random living target, prioritizing taunt; equal choice among multiple taunts.',
    'Critical damage 2x and uniform +/-10% jitter unless calibrated rules supplied.',
    'Phoenix attempted once; berserk adds 30% per kill; extra attacks never recurse.',
    'Unobserved effect ordering and trigger interactions remain approximate.',
    'Inventory stats can omit global boosts; opponent replay stats are effective.',
]

STAT_KEYS = ('attack', 'health', 'crit_chance')
SEAL_COST = 500


# ==========================================
# HTTP TRANSPORT HELPERS
# ==========================================
def get_html(session, url, **kwargs):
    """Page routes can return HTML fragments as JSON when XHR is inherited."""
    headers = getattr(session, 'headers', None)
    if headers is None:
        return session.get(url, **kwargs)
    xhr = headers.pop('X-Requested-With', None)
    accept = headers.pop('Accept', None)
    headers['Accept'] = 'text/html'
    try:
        return session.get(url, **kwargs)
    finally:
        headers.pop('Accept', None)
        if accept is not None:
            headers['Accept'] = accept
        if xhr is not None:
            headers['X-Requested-With'] = xhr


class SendCancelled(Exception):
    """before_send said no after the throttle wait: nothing was sent."""


def post_once(session, url, before_send=None, **kwargs):
    if hasattr(session, '_raw_request') and hasattr(session, '_throttle'):
        if session.should_abort and session.should_abort():
            raise RuntimeError('module aborted')
        session._throttle()
        if before_send and not before_send():  # checked after the wait, right before the request
            raise SendCancelled()
        response = session._raw_request('POST', url, **kwargs)
        reason = 'http_429' if response.status_code == 429 else is_anti_bot_page(response)
        if reason:
            session.last_block_reason = reason
            session._emit_block(reason, url)
        else:
            session._mark_ok('POST', url, response)
        return response
    if before_send and not before_send():
        raise SendCancelled()
    return session.post(url, **kwargs)


def safe_url(value):
    url = urljoin(ORIGIN, str(value or ''))
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.netloc != 'mangabuff.ru' or not parsed.path.startswith('/battle/'):
        raise ValueError('Unexpected manual arena endpoint')
    return url


def same_site_route(value, prefix):
    url = urljoin('https://mangabuff.ru/', str(value or ''))
    p = urlsplit(url)
    if p.scheme != 'https' or p.netloc != 'mangabuff.ru' or not p.path.startswith(prefix):
        raise ValueError('Unexpected arena route')
    return url


def seal_id(label):
    text = str(label or '').lower()
    return next((sid for sid, stem in SEAL_STEMS.items() if stem in text), None)


def arena_score(card):
    attack = float(card.get('battle_attack') or 0)
    health = float(card.get('battle_health') or 0)
    crit = max(0, min(100, float(card.get('battle_crit_chance') or 0)))
    return attack * (1 + crit / 100) * math.sqrt(max(0, health))


def _number(value):
    result = float(str(value).replace(' ', '').replace('\xa0', ''))
    if not math.isfinite(result):
        raise ValueError('Nonfinite stat')
    return result


def _round(value):
    return math.floor(value + .5)


# ==========================================
# CARD PROGRESSION & MUTATION LEDGER
# ==========================================
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
    if result['health'] <= 0 or result['crit_chance'] > 100 or result['attack'] < 0:
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


def _progression_score(card):
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
    return min(eligible, key=lambda c: (_progression_score(c), c['card_user_id'])) if eligible else None


def plan_reroll(card, samples, budget, protectedIDs, evaluator: Callable | None = None):
    """Return one research action. Caller must re-protect newly strong results."""
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
        kind = 'seal' if observed and not any(mode == 'seal' for mode, _, _ in observed) else 'stats'
        cost = SEAL_COST if kind == 'seal' else 300
        return {'type': kind, 'locked_stats': [], 'cost': cost, 'card_user_id': current['card_user_id'],
                'rationale': 'weak-copy research; insufficient same-copy observations', 'research': True} if budget >= cost else None
    evaluate = evaluator or _progression_score
    baseline = evaluate(current)
    actions = []
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


# ==========================================
# BATTLE EFFECTIVENESS & COMBAT SIMULATION
# ==========================================
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
        if buff == 'blood_rage':
            c['attack'] *= 1.2
        if buff == 'predator_eye':
            c['crit'] = min(100, c['crit'] + 25)
        if buff == 'iron_will':
            c['max_hp'] *= 1.2
        if buff == 'glass_cannon':
            c['attack'] *= 1.45
            c['max_hp'] *= .75
        c['hp'] = c['max_hp']
    return c


def unbuff_owned(card, baseline_crit=None):
    """Remove the explicitly observed static buff, preserving per-copy boosts."""
    c = dict(card)
    buff = c.get('buff')
    if buff == 'blood_rage':
        c['attack'] /= 1.2
    if buff == 'iron_will':
        c['max_hp'] /= 1.2
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
        else:
            c['crit'] = max(0, c['crit'] - 25)
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
        return c['attack'] * (1 + .3 * c['kills'] if c['seal'] == 'berserk' else 1) * (r['rage_multiplier'] if c['seal'] == 'rage' and c['hp'] < .5 * c['max_hp'] else 1)

    def hurt(victim, source, damage, side, depth=0, direct=True):
        if victim['hp'] <= 0 or depth > 8:
            return 0, False
        if direct and victim['seal'] == 'dodge' and rng.random() < r['dodge_chance']:
            return 0, False
        if direct and victim['first_hit'] and victim['seal'] == 'barrier':
            damage *= 1 - r['barrier_block']
        if direct:
            victim['first_hit'] = False
        before = victim['hp']
        victim['hp'] = max(0, before - max(0, _round(damage)))
        if victim['hp'] == 0 and victim['seal'] == 'immortality' and not victim['saved']:
            victim['saved'] = True
            victim['hp'] = 1
        dealt = max(0, before - victim['hp'])
        if victim['hp'] == 0:
            if victim['seal'] == 'phoenix' and (not r['phoenix_once'] or not victim['phoenix_used']):
                victim['phoenix_used'] = True
                if rng.random() < .35:
                    victim['hp'] = _round(.35 * victim['max_hp'])
            if victim['hp'] == 0 and victim['buff'] == 'second_breath' and not second_breath[side]:
                second_breath[side] = True
                victim['hp'] = _round(.30 * victim['max_hp'])
        killed = victim['hp'] <= 0
        if direct and victim['seal'] == 'thorns' and source['hp'] > 0:
            hurt(source, victim, r['thorns_reflect'] * dealt, 1 - side, depth + 1, False)
        if killed and victim['seal'] == 'revenge' and source['hp'] > 0:
            hurt(source, victim, power(victim), 1 - side, depth + 1, False)
        return dealt, killed

    def attack(actor, victim, side):
        damage = power(actor)
        if actor['first_attack']:
            if actor['seal'] == 'hunter':
                damage *= r['hunter_multiplier']
            if actor['buff'] == 'mad_charge':
                damage *= 1.55
        actor['first_attack'] = False
        if actor['seal'] == 'execution' and victim['hp'] < .35 * victim['max_hp']:
            damage *= r['execution_multiplier']
        if rng.random() < actor['crit'] / 100 or (actor['buff'] == 'executioner' and victim['hp'] < .30 * victim['max_hp']):
            damage *= r['crit_multiplier']
        damage *= rng.uniform(1 - r['jitter'], 1 + r['jitter'])
        dealt, killed = hurt(victim, actor, damage, 1 - side)
        if actor['hp'] > 0:
            if actor['seal'] == 'vampirism':
                actor['hp'] = min(actor['max_hp'], actor['hp'] + _round(r['vampirism_heal'] * dealt))
            if killed:
                actor['kills'] += 1
                if actor['buff'] == 'blood_thirst':
                    actor['hp'] = min(actor['max_hp'], actor['hp'] + _round(.15 * actor['max_hp']))

    for turn in range(int(r['max_turns'])):
        side = (first_side + turn) % 2
        for actor in sides[side]:
            if actor['hp'] <= 0:
                continue
            victim = target(1 - side)
            if victim is None:
                break
            attack(actor, victim, side)
            if actor['hp'] > 0 and actor['seal'] in ('double_strike', 'chain_attack'):
                chance = r['double_strike_chance'] if actor['seal'] == 'double_strike' else r['chain_attack_chance']
                if rng.random() < chance:
                    extra = target(1 - side, victim if actor['seal'] == 'chain_attack' else None)
                    if extra:
                        attack(actor, extra, side)
            if actor['hp'] > 0 and actor['seal'] == 'regeneration':
                actor['hp'] = min(actor['max_hp'], actor['hp'] + _round(r['regeneration_heal'] * actor['max_hp']))
        if not living(0) or not living(1):
            break
    alive = [bool(living(i)) for i in (0, 1)]
    score = 1.0 if alive == [True, False] else 0.0 if alive == [False, True] else .5
    fractions = [sum(c['hp'] for c in s) / sum(c['max_hp'] for c in s) for s in sides]
    return score, fractions[0] - fractions[1]


def evaluate_buffs(team, opponents, availablecodes, rules=None, samples=32,
                    seed=743, cancelled=None):
    """Paired comparison of owned buffs; no consumption, selection or purchases."""
    r = dict(DEFAULT_RULES, **(rules or {}))
    opposition = []
    for entry in (opponents or [])[-int(r['opponent_limit']):]:
        raw = entry.get('cards', []) if isinstance(entry, dict) else entry
        normalized = [c if 'seal' in c else from_replay_card(c) for c in raw]
        if len(normalized) == 4 and all(normalized):
            opposition.append((normalized, entry.get('buff') if isinstance(entry, dict) else None))
    if len(team) != 4 or not opposition:
        return []
    own = [unbuff_owned(c) if c.get('stats_source') == 'replay_effective' and not c.get('stats_unbuffed') else c for c in team]
    n = max(1, min(64, int(samples)))
    count = n * 2 * len(opposition)
    ranking = []
    codes = [None] + sorted(set(availablecodes or []) & set(BUFF_CODES))
    for code in codes:
        if cancelled and cancelled():
            return []
        wins = hp = 0.0
        for index, (enemy, buff) in enumerate(opposition):
            for sample in range(n):
                if cancelled and cancelled():
                    return []
                for initiative in (0, 1):
                    w, residual = simulate(own, enemy, seed + index * 10000 + sample,
                                           initiative, dict(r, own_buff=code), buff)
                    wins += w
                    hp += residual
        ranking.append({'code': code, 'modelled_win_rate': wins / count,
                        'hp_margin': hp / count, 'sample_count': count,
                        'model_version': MODEL_VERSION,
                        'uncertainty': 'Approximate buff and seal ordering; sampled opponent pool, not real win odds.'})
    ranking.sort(key=lambda c: (c['modelled_win_rate'], c['hp_margin']), reverse=True)
    return ranking


def heuristic(card):
    """Seal-aware shortlist proxy, not an effectiveness probability."""
    attack, hp = card['attack'], card['max_hp']
    damage = attack * (1 + card['crit'] / 100)
    seal = card['seal']
    damage *= {'double_strike': 1.3, 'chain_attack': 1.25, 'hunter': 1.13,
               'rage': 1.12, 'execution': 1.10, 'berserk': 1.18,
               'revenge': 1.15, 'thorns': 1.15}.get(seal, 1)
    hp *= {'regeneration': 1.45, 'vampirism': 1.25, 'phoenix': 1.1225,
           'immortality': 1.12, 'barrier': 1.10, 'dodge': 1.25}.get(seal, 1)
    return damage * math.sqrt(hp)


def choose_replacement(current_ids, cards, banned_id, opponent_teams, rules=None,
                       seed=431, samples=32, cancelled=None):
    """Return (IDs, diagnostics); None means unsafe/unavailable/cancelled."""
    r = dict(DEFAULT_RULES, **(rules or {}))
    known = {}
    for raw in cards:
        c = raw if 'seal' in raw else from_inventory(raw)
        if c:
            known[c['id']] = c
    try:
        ids = [int(i) for i in current_ids]
    except (TypeError, ValueError):
        return None
    if len(ids) != 4 or len(set(ids)) != 4 or any(i not in known for i in ids):
        return None
    slots = [i for i, cid in enumerate(ids) if known[cid]['seal_id'] == banned_id]

    def legal(c):
        return not c['in_trade'] and c['seal_id'] != banned_id and (c['energy'] is None or c['energy'] > 0)

    preserved = [known[cid] for i, cid in enumerate(ids) if i not in slots]
    if any(not legal(c) for c in preserved) or len({c['base_id'] for c in preserved}) != len(preserved):
        return None
    opponents = []
    for entry in (opponent_teams or [])[-int(r['opponent_limit']):]:
        raw = entry.get('cards', []) if isinstance(entry, dict) else entry
        team = [c if 'seal' in c else from_replay_card(c) for c in raw]
        if len(team) == 4 and all(team):
            opponents.append((team, entry.get('buff') if isinstance(entry, dict) else None))
    pool = sorted((c for c in known.values() if legal(c)), key=lambda c: (heuristic(c), c['id']), reverse=True)
    for slot in slots:
        others = [known[cid] for i, cid in enumerate(ids) if i not in slots or i < slot]
        c = next((c for c in pool if all(c['id'] != o['id'] and c['base_id'] != o['base_id'] for o in others)), None)
        if c is None:
            return None
        ids[slot] = c['id']
    selections = []
    n = max(1, min(64, int(samples)))
    for slot in slots:
        others = [known[cid] for i, cid in enumerate(ids) if i != slot]
        candidates = [c for c in pool if all(c['id'] != o['id'] and c['base_id'] != o['base_id'] for o in others)][:max(1, min(12, int(r['candidate_limit'])))]
        scores = []
        for c in candidates:
            if cancelled and cancelled():
                return None
            trial = list(ids)
            trial[slot] = c['id']
            wins = residual = 0.0
            count = n * 2 * len(opponents)
            for opponent_index, (opp, buff) in enumerate(opponents):
                for sample in range(n):
                    if cancelled and cancelled():
                        return None
                    for initiative in (0, 1):
                        w, hp = simulate([known[i] for i in trial], opp,
                                         seed + opponent_index * 10000 + sample, initiative, r, buff)
                        wins += w
                        residual += hp
            scores.append({'id': c['id'], 'modelled_win_rate': wins / count if count else None,
                           'hp_margin': residual / count if count else None,
                           'heuristic': heuristic(c), 'sample_count': count})
        scores.sort(key=lambda s: (s['modelled_win_rate'] if s['modelled_win_rate'] is not None else 0,
                                  s['hp_margin'] if s['hp_margin'] is not None else 0, s['heuristic']), reverse=True)
        best = scores[0]
        ids[slot] = best['id']
        margin = (best['modelled_win_rate'] - scores[1]['modelled_win_rate']) if len(scores) > 1 and opponents else None
        selections.append({'slot': slot, 'best': best, 'runner_up': scores[1] if len(scores) > 1 else None,
                           'win_rate_margin': margin, 'candidate_count': len(scores)})
    return ids, {'model_version': MODEL_VERSION, 'method': 'team_monte_carlo' if opponents else 'seal_heuristic',
                 'opponent_count': len(opponents), 'selections': selections,
                 'assumptions': list(ASSUMPTIONS), 'rules': r,
                 'uncertainty': 'Model parameter and opponent sampling uncertainty unquantified; no real win probability claim.',
                 'near_tie': any(s['win_rate_margin'] is not None and s['win_rate_margin'] < .05 for s in selections)}


# ==========================================
# OBSERVATIONS & REPLAY ANALYSIS
# ==========================================
def replay_from_html(html):
    soup = BeautifulSoup(html, 'html.parser')
    values = {}
    for name in ('log', 'reward', 'seals', 'buffs'):
        node = soup.select_one(f'[data-battle-{name}-json]')
        if node:
            raw = node.get_text()
            if len(raw) > 600000:
                return None
            try:
                values[name] = json.loads(raw)
            except (ValueError, TypeError):
                return None
    return observation_from_values(values)


def observation_from_values(values):
    log = values.get('log')
    reward = values.get('reward')
    if not isinstance(log, dict) or log.get('type') != 'server_auto_battle' or not isinstance(reward, dict):
        return None
    own_attacker = reward.get('is_attacker_reward')
    if not isinstance(own_attacker, bool) or not isinstance(reward.get('is_win'), bool):
        return None
    sides = log.get('cards')
    if not isinstance(sides, dict) or any(not isinstance(sides.get(side), list) or len(sides[side]) != 4 for side in ('attacker', 'defender')):
        return None
    cards = {}
    fields = ('card_user_id', 'attack', 'max_hp', 'crit_chance', 'seal_code', 'battle_card_energy', 'is_tired', 'buff_code')
    for side in ('attacker', 'defender'):
        cards[side] = []
        for item in sides[side]:
            if not isinstance(item, dict):
                return None
            card = {k: item.get(k) for k in fields}
            try:
                if any(not math.isfinite(float(card[k])) or float(card[k]) <= 0 for k in ('card_user_id', 'attack', 'max_hp')):
                    return None
                if not 0 <= float(card['crit_chance']) <= 100:
                    return None
            except (ValueError, TypeError):
                return None
            cards[side].append(card)
    steps = log.get('steps')
    if not isinstance(steps, list) or len(steps) > 1000:
        return None
    safe_steps = []
    fields = ('turn', 'type', 'from_side', 'to_side', 'from_card_user_id', 'to_card_user_id', 'damage', 'dealt_damage', 'is_crit', 'hp_before', 'hp_after', 'attacker_hp_before', 'attacker_hp_after', 'is_target_dead', 'target_effect')
    for item in steps:
        if not isinstance(item, dict):
            continue
        step = {k: item[k] for k in fields if k in item and isinstance(item[k], (str, int, float, bool, type(None)))}
        for group in ('effects', 'extra_attacks'):
            step[group] = [{k: v for k, v in effect.items() if k in ('type', 'amount', 'damage', 'is_crit', 'card_user_id', 'target_card_user_id', 'to_card_user_id', 'hp_after', 'hp_before', 'real_side', 'target_effect') and isinstance(v, (str, int, float, bool, type(None)))}
                         for effect in item.get(group, []) if isinstance(effect, dict)][:30]
        safe_steps.append(step)
    seals = values.get('seals')
    definitions = {}
    if isinstance(seals, dict):
        for item in seals.values():
            if isinstance(item, dict) and isinstance(item.get('code'), str):
                definitions[item['code']] = {k: item[k] for k in ('code', 'short', 'description') if isinstance(item.get(k), str)}
    identifier = hashlib.sha256(json.dumps({'cards': cards, 'steps': safe_steps, 'winner': log.get('winner')}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'id': identifier, 'observed_at': time.time(), 'cards': cards, 'steps': safe_steps,
            'own_side': 'attacker' if own_attacker else 'defender', 'is_win': reward['is_win'],
            'winner': log.get('winner'), 'first_side': log.get('first_side'), 'max_turns': log.get('max_turns'),
            'seal_definitions': definitions}


def load_observations(path):
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get('battles'), list):
            return data['battles'][-40:]
    except (OSError, ValueError):
        pass
    return []


def save_observation(path, observation):
    if observation is None:
        return
    # Full history goes to the long-term archive; this JSON stays the short live window
    base_dir = os.path.dirname(os.path.abspath(path))
    if not os.path.exists(battle_store.db_path(base_dir)):
        battle_store.backfill(base_dir)
    battle_store.record(battle_store.db_path(base_dir), observation, 'arena')
    battles = load_observations(path)
    if any(b.get('id') == observation['id'] for b in battles):
        return
    battles = (battles + [observation])[-40:]
    directory = os.path.dirname(os.path.abspath(path))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=directory, delete=False) as f:
            temporary = f.name
            f.write(json.dumps({'schema': 1, 'battles': battles}, ensure_ascii=False))
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def opponent_teams(battles, limit=6):
    """Most recent distinct observed opposition; each card uses effective server stats."""
    teams = []
    seen = set()
    for battle in reversed(battles):
        side = 'defender' if battle.get('own_side') == 'attacker' else 'attacker'
        team = (battle.get('cards') or {}).get(side)
        if not isinstance(team, list) or len(team) != 4:
            continue
        identity = tuple(c.get('card_user_id') for c in team)
        if identity in seen:
            continue
        seen.add(identity)
        teams.append(team)
        if len(teams) >= limit:
            break
    return teams


def archive_opponent_teams(battles, limit=8):
    """Distinct opponent teams, the most often met first, then the most recent."""
    seen = {}
    for order, battle in enumerate(battles):
        side = 'defender' if battle.get('own_side') == 'attacker' else 'attacker'
        team = (battle.get('cards') or {}).get(side)
        if not isinstance(team, list) or len(team) != 4:
            continue
        identity = tuple(sorted(c.get('card_user_id') or 0 for c in team))
        entry = seen.setdefault(identity, {'team': team, 'count': 0, 'last': 0})
        entry['count'] += 1
        if order >= entry['last']:
            entry['team'], entry['last'] = team, order
    ranked = sorted(seen.values(), key=lambda e: (e['count'], e['last']), reverse=True)
    return [e['team'] for e in ranked[:limit]]


def calibrate_rules(battles):
    """Estimate ordinary critical damage; report first-Hunter uncertainty separately."""
    ordinary = []
    crit = []
    hunter = []
    for battle in battles:
        by_id = {c['card_user_id']: c for side in (battle.get('cards') or {}).values() for c in side}
        acted = set()
        for step in battle.get('steps', []):
            cid = step.get('from_card_user_id')
            actor = by_id.get(cid)
            target = by_id.get(step.get('to_card_user_id'))
            first = cid not in acted
            acted.add(cid)
            if not actor or not target or step.get('type') != 'attack':
                continue
            damage = step.get('damage')
            attack = actor.get('attack')
            if not isinstance(damage, (int, float)) or not attack or step.get('target_effect'):
                continue
            if actor.get('seal_code') in ('rage', 'execution', 'berserker', 'berserk'):
                continue
            ratio = damage / attack
            if first and actor.get('seal_code') == 'hunter':
                hunter.append(ratio / (2 if step.get('is_crit') else 1))
                continue
            (crit if step.get('is_crit') else ordinary).append(ratio)
    result = {'crit_multiplier': 2.0, 'jitter': .10, 'calibration_samples': len(ordinary) + len(crit), 'hunter_samples': len(hunter)}
    if len(ordinary) >= 8 and len(crit) >= 5:
        result['crit_multiplier'] = max(1.5, min(2.5, statistics.median(crit) / statistics.median(ordinary)))
    if len(hunter) >= 3:
        result['hunter_multiplier'] = max(1.0, min(2.0, statistics.median(hunter)))
        result['hunter_source'] = 'observed_first_attacks'
    else:
        result['hunter_source'] = 'live_description_unverified'
    result['uncertainties'] = [
        'Target selection uniformity and all seal interaction orders are not proven.',
        'Live seal descriptions can differ from server behavior; non-observed effects remain approximate.',
    ]
    return result


def recent_battles(battles, max_age=7 * 24 * 3600):
    now = time.time()
    return [b for b in battles if isinstance(b, dict) and 0 <= now - float(b.get('observed_at', 0)) <= max_age]


def supported_definitions(battles):
    """Stop trusting fixed seal coefficients when fresh descriptions change."""
    expected = {
        'double_strike': [30], 'rage': [50, 30], 'execution': [35, 35],
        'berserk': [30], 'hunter': [75], 'thorns': [30], 'regeneration': [7],
        'barrier': [70], 'immortality': [1], 'revenge': [100], 'chain_attack': [25],
        'vampirism': [25], 'phoenix': [35, 35], 'dodge': [20],
    }
    if not battles:
        return False
    descriptions = battles[-1].get('seal_definitions') or {}
    if set(expected) - set(descriptions) or 'taunt' not in descriptions:
        return False
    for code, numbers in expected.items():
        text = descriptions[code].get('description', '')
        observed = [int(v) for v in re.findall(r'\d+', text)]
        if observed != numbers:
            return False
    return True


def normalized_owned(cards, battles, current_energy=None):
    """Use recent measured per-copy stats, never extrapolate boosts to other copies."""
    measured = {}
    for battle in recent_battles(battles, max_age=4 * 3600):
        for c in (battle.get('cards') or {}).get(battle.get('own_side'), []):
            observed = dict(c)
            if isinstance(c.get('battle_card_energy'), (int, float)):
                observed['energy_after'] = max(0, c['battle_card_energy'] - (2 if battle.get('is_win') else 1))
            measured[c['card_user_id']] = observed
    result = []
    for raw in cards:
        cid = int(raw.get('id') or 0)
        normal = from_inventory(raw, (current_energy or {}).get(cid))
        if normal is None:
            continue
        old = measured.get(cid)
        effective = from_replay_card(old) if old else None
        if normal['energy'] is None and old and 'energy_after' in old:
            normal['energy'] = old['energy_after']
        if effective:
            effective = unbuff_owned(effective, baseline_crit=normal['crit'])
        if effective and effective['seal_id'] == normal['seal_id'] and abs(effective['crit'] - normal['crit']) < .01:
            energy = normal['energy']
            if energy is None or energy > 0:
                normal.update(attack=effective['attack'], max_hp=effective['max_hp'],
                              stats_source='replay_effective', stats_unbuffed=True, buff=None)
        result.append(normal)
    return result


# ==========================================
# CARD ENERGY MODEL
# ==========================================
# Card energy refills gradually over time. Only the four active fighters show
# their energy on the site, so bench cards are estimated from the last time we
# saw them, plus regeneration learned from the account's own observations.
ENERGY_MAX = 100
ENERGY_TO_FIGHT = 10


def last_energy_points(battles, observations):
    """{card_id: (timestamp, energy)} — the latest known energy of every card.

    Battles give the energy each own card started with; the fight then costs
    2 on a win and 1 on a loss. Page observations are exact reads.
    """
    points = {}

    def keep(cid, at, value):
        try:
            cid, at, value = int(cid), float(at), int(value)
        except (TypeError, ValueError):
            return
        if cid not in points or at >= points[cid][0]:
            points[cid] = (at, max(0, min(ENERGY_MAX, value)))

    for battle in battles or []:
        at = battle.get('observed_at')
        for c in (battle.get('cards') or {}).get(battle.get('own_side'), []):
            if isinstance(c.get('battle_card_energy'), (int, float)):
                keep(c.get('card_user_id'), at, c['battle_card_energy'] - (2 if battle.get('is_win') else 1))
    for cid, obs in (observations or {}).items():
        keep(cid, obs.get('at'), obs.get('value'))
    return points


def last_energy_reset(now=None):
    """Most recent full-energy moment: the site resets energy to 100 at 00/04/08/12/16/20 MSK."""
    now = time.time() if now is None else now
    msk = datetime.fromtimestamp(now, timezone(timedelta(hours=3)))
    return msk.replace(hour=msk.hour - msk.hour % 4, minute=0, second=0, microsecond=0).timestamp()


def projected_energy(points, now=None):
    """Current energy from the last known points: full if a reset mark passed since then."""
    reset = last_energy_reset(now)
    return {cid: ENERGY_MAX if at < reset else int(value) for cid, (at, value) in points.items()}


# ==========================================
# DECK OPTIMIZER
# ==========================================
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
    """Protect selected teams and competitive reserves, not every outlier stat."""
    known = _normalized(cards)
    protected = set(current_ids) | set(best_ids)
    ordered = sorted(known.values(), key=heuristic, reverse=True)
    if not ordered:
        return protected

    def stat_quality(c):
        return c['attack'] * (1 + c['crit'] / 100) * math.sqrt(c['max_hp'])

    threshold = max(stat_quality(c) for c in ordered) * min_relative_score
    competitive = [c for c in ordered if stat_quality(c) >= threshold and not c['in_trade']]
    protected.update(c['id'] for c in competitive[:max(0, bench_size)])
    for sid in SEALS:
        group = [c for c in competitive if c['seal_id'] == sid]
        protected.update(c['id'] for c in group[:max(1, per_seal)])
        protected.update(c['id'] for c in _frontier(group))
    return protected


def _shortlist(legal, current_ids, limit):
    ordered = sorted(legal, key=lambda c: (heuristic(c), c['id']), reverse=True)
    chosen = {c['id']: c for c in ordered if c['id'] in current_ids}
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
    """Return (ordered four IDs, diagnostics) or None if unsafe/unavailable."""
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
                len({known[i]['base_id'] for i in ids}) == 4 and
                max(Counter(known[i]['seal'] for i in ids).values()) <= MAX_SAME_SEAL)

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
        return None
    opponents = opponents[-max(1, min(12, int(rules.get('opponent_limit', 12)))):]
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
        result = {'utility': sum(values) / len(values), 'win_rate': sum(wins) / len(wins),
                  'hp_margin': sum(hps) / len(hps), 'values': values,
                  'battles': len(values)}
        cache[key] = result
        return result

    def seed_team(order):
        ids, bases, seals = [], set(), Counter()
        for cid in order:
            if known[cid]['base_id'] not in bases and seals[known[cid]['seal']] < MAX_SAME_SEAL:
                ids.append(cid)
                bases.add(known[cid]['base_id'])
                seals[known[cid]['seal']] += 1
            if len(ids) == 4:
                return tuple(ids)

    seeds = []
    if valid(current):
        seeds.append(current)
    seeds.append(seed_team(pool_ids))
    for key in ('max_hp', 'attack'):
        seeds.append(seed_team([c['id'] for c in sorted(pool, key=lambda c: c[key], reverse=True)]))
    for seal in ('taunt', 'regeneration', 'vampirism'):
        front = [c['id'] for c in pool if c['seal'] == seal]
        if front:
            seeds.append(seed_team(front + pool_ids))
    coarse = max(1, min(8, int(rules.get('search_samples', 2))))
    scored = {}
    for ids in dict.fromkeys(s for s in seeds if s and valid(s)):
        score = evaluate(ids, coarse)
        if score:
            scored[ids] = score
    if not scored or stopped:
        return None

    for _ in range(5):
        beam = sorted(scored, key=lambda ids: scored[ids]['utility'], reverse=True)[:4]
        previous = beam[0]
        for ids in beam:
            candidates = list(itertools.permutations(ids))
            for slot in range(4):
                for cid in pool_ids:
                    trial = ids[:slot] + (cid,) + ids[slot + 1:]
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
    if baseline_legal:
        finalists = [current] + [ids for ids in finalists if ids != current]
    for ids in finalists:
        score = evaluate(ids, samples)
        if score:
            refined[ids] = score
        if stopped:
            return None

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
            diffs = [a - b for a, b in zip(refined[selected]['values'], refined[current]['values'])]
            gain = sum(diffs) / len(diffs)
            paired_se = (math.sqrt(sum((d - gain) ** 2 for d in diffs) / (len(diffs) - 1) / len(diffs))
                         if len(diffs) > 1 else 1)
            if gain <= max(float(rules.get('swap_margin', .025)), 1.5 * paired_se):
                selected = current
                hysteresis = True

    def public(score):
        return {k: v for k, v in score.items() if k != 'values'} if score else None

    return list(selected), {
        'method': 'bounded_whole_deck_search', 'model_version': MODEL_VERSION,
        'baseline': public(refined.get(current) or scored.get(current)),
        'best': public(refined.get(selected) or scored.get(selected)),
        'candidate_ids': list(candidate),
        'candidate_score': public(refined.get(candidate) or scored.get(candidate)),
        'refined_lineups': len(refined),
        'pool_ids': pool_ids, 'evaluated_lineups': len(scored),
        'opponent_count': len(opponents), 'opponent_sources': [s for _, _, s in opponents],
        'paired_gain': gain, 'paired_standard_error': paired_se,
        'hysteresis_retained': hysteresis, 'budget_exhausted': expired(),
        'elapsed_seconds': time.monotonic() - start,
        'uncertainty': 'Sampling error only; hidden rules and opponent selection remain uncertain.',
        'assumptions': list(ASSUMPTIONS), 'unknown_energy_ids': [i for i in selected if known[i]['energy'] is None],
    }


# ==========================================
# MANUAL ARENA (EVENING CUP)
# ==========================================
# Everything the cup page offered on 2026-10-17; anything else is reported once as new
CUP_KNOWN = {
    'tactic': {'heal', 'boost', 'secret'},
    'secret': {'trap', 'mirror', 'mend'},
    'state': {'battle_steps', 'current_turn_user_id', 'fight_id', 'fight_status', 'final', 'finished_reason',
              'private_tactics', 'revision', 'server_time', 'steps', 'tactics', 'tournament_result',
              'tournament_url', 'turn', 'turn_base_seconds', 'turn_deadline_at', 'turn_limits', 'turn_seconds',
              'used_cards', 'winner_user_id'},
    'url': {'data-manual-attack-url', 'data-manual-state-url', 'data-manual-surrender-url',
            'data-manual-tactic-url', 'data-manual-turn-state'},
    'tactic_field': {'used_this_round', 'blocked_kind', 'secret_active', 'boost'},
}


def choose_action_legacy(state, side, taunt_ids=(), hit_history=None, allow_secret=True):
    """Conservative legal one-step policy, not a full battle simulator."""
    other = 'defender' if side == 'attacker' else 'attacker'
    alive = lambda c: c.get('alive', True) and c.get('is_alive', True) and float(c.get('hp', 0)) > 0
    own = [c for c in state.get('final', {}).get(side, []) if alive(c)]
    enemy = [c for c in state.get('final', {}).get(other, []) if alive(c)]
    used = set(map(int, state.get('used_cards', {}).get(side, [])))
    ready = [c for c in own if int(c['card_user_id']) not in used]
    taunts = [c for c in enemy if int(c['card_user_id']) in set(taunt_ids) or c.get('taunt_active') is True
              or c.get('seal_code') == 'taunt']
    targets = taunts or enemy
    if not ready or not targets:
        return None

    def attack_value(card):
        atk = float(card.get('attack', 0))
        if card.get('seal_code') == 'hunter' and hit_history is not None and not any(
                actor == int(card['card_user_id']) for actor, target in hit_history):
            atk *= 1.75
        boost = state.get('tactics', {}).get(side, {}).get('boost')
        if boost and int(boost.get('card_user_id', 0)) == int(card['card_user_id']):
            atk = round(atk * 1.3)
        return atk

    def score(pair):
        a, t = pair
        atk = attack_value(a)
        crit = max(0, min(100, float(a.get('crit_chance', 0)))) / 100
        damage = atk * (1 + crit)
        hp = float(t['hp'])
        return (atk >= hp, damage >= hp, float(t.get('attack', 0)) if atk >= hp else 0,
                min(damage, hp) / math.sqrt(max(1, hp)), -int(a['card_user_id']), -int(t['card_user_id']))

    attacker, target = max(((a, t) for a in ready for t in targets), key=score)
    tactic = state.get('tactics', {}).get(side, {})
    if tactic and not tactic.get('used_this_round'):
        wounded = [c for c in own if float(c.get('max_hp', 0)) > float(c['hp'])]
        if tactic.get('blocked_kind') != 'heal' and wounded:
            card = max(wounded, key=lambda c: min(float(c['max_hp']) * .15, float(c['max_hp']) - float(c['hp'])))
            recovered = min(float(card['max_hp']) * .15, float(card['max_hp']) - float(card['hp']))
            if recovered >= float(card['max_hp']) * .12 and float(card['hp']) <= float(card['max_hp']) * .5:
                return ('tactic', {'kind': 'heal', 'card_user_id': int(card['card_user_id'])})
        if tactic.get('blocked_kind') != 'boost' and not tactic.get('boost'):
            atk, hp = attack_value(attacker), float(target['hp'])
            if atk < hp <= round(atk * 1.3):
                return ('tactic', {'kind': 'boost', 'card_user_id': int(attacker['card_user_id'])})
        # Otherwise the round's tactic would be wasted: a trap costs the next attacker 20% of its max HP.
        # It goes on our most wounded card, the one players usually finish off.
        if (allow_secret and 'secret_active' in tactic and not tactic.get('secret_active')
                and tactic.get('blocked_kind') != 'secret'):
            bait = min(own, key=lambda c: (float(c['hp']) / max(1.0, float(c.get('max_hp', 0))), int(c['card_user_id'])))
            return ('tactic', {'kind': 'secret', 'secret_type': 'trap', 'card_user_id': int(bait['card_user_id'])})
    return ('attack', {'from_card_user_id': int(attacker['card_user_id']), 'target_card_user_id': int(target['card_user_id'])})


def choose_action(state, side, taunt_ids=(), hit_history=None, allow_secret=True):
    baseline = choose_action_legacy(state, side, taunt_ids, hit_history, allow_secret)
    from cup_tactics import choose
    try:
        return choose(state, side, baseline, hit_history, set(taunt_ids), DEFAULT_RULES)
    except (KeyError, TypeError, ValueError, OverflowError):
        return baseline


class ManualArenaController:
    def __init__(self, bot, state_path=None, clock=time.monotonic, sleep=time.sleep):
        self.bot, self.clock, self.sleep = bot, clock, sleep
        self.path = Path(state_path) if state_path else None
        self.pending = None
        self.registration = None
        self.fight_id = None
        self.active_fight = False
        self.last_result = None
        if self.path and self.path.exists():
            try:
                stored = json.loads(self.path.read_text(encoding='utf-8'))
                self.pending = stored.get('pending')
                self.registration = stored.get('registration')
                self.fight_id = stored.get('fight_id')
                self.active_fight = bool(stored.get('active_fight', False))
                self.last_result = stored.get('result')
            except (ValueError, OSError):
                pass
        self.state = None
        self.stamp = 0
        self.received = self.clock()
        self.card_stats = {}
        self.hit_history = set()
        self.history_complete = False
        self.history_revision = None

    def record_hits(self, steps):
        for step in steps:
            if step.get('type') == 'attack' and step.get('from_card_user_id') and step.get('to_card_user_id'):
                self.hit_history.add((int(step['from_card_user_id']), int(step['to_card_user_id'])))

    def save(self):
        if self.path:
            payload = {'fight_id': self.fight_id, 'revision': (self.state or {}).get('revision'),
                       'pending': self.pending, 'registration': self.registration,
                       'active_fight': self.active_fight, 'result': self.last_result}
            temp = self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(payload), encoding='utf-8')
            temp.replace(self.path)

    def get(self, url):
        response = self.bot.session.get(safe_url(url), timeout=10)
        response.raise_for_status()
        return response

    @staticmethod
    def unwrap(response):
        data = response.json()
        return data.get('data', data)

    def accept(self, data):
        if str(data.get('fight_id')) != self.fight_id:
            return False
        revision = data.get('revision')
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            return False
        if self.state and revision < self.state['revision']:
            return False
        old_revision = self.state.get('revision') if self.state else self.history_revision
        if isinstance(data.get('battle_steps'), list):
            self.hit_history = set()
            self.record_hits(data['battle_steps'])
            self.history_complete = True
        elif old_revision is not None and revision > old_revision:
            if revision > old_revision + 1 or not isinstance(data.get('steps'), list):
                self.history_complete = False
            self.record_hits(data.get('steps', []))
        elif old_revision is None:
            self.history_complete = False
        self.history_revision = revision
        now = self.clock()
        old_now = self.stamp + max(0, now - self.received) if self.stamp else 0
        self.stamp = max(old_now, float(data.get('server_time', 0)))
        self.received = now
        data = dict(data)
        data['final'] = {side: [dict(self.card_stats.get(int(card['card_user_id']), {}), **card)
                                for card in cards]
                         for side, cards in data.get('final', {}).items()}
        self.state = data
        self.active_fight = data.get('fight_status') != 'finished'
        if not self.active_fight:
            self.last_result = {'fight_id': self.fight_id, 'revision': revision,
                                'winner_user_id': data.get('winner_user_id'),
                                'reason': data.get('finish_reason') or data.get('reason')}
        if self.pending and (self.pending.get('fight_id') != self.fight_id or revision > self.pending.get('revision', -1) or data.get('fight_status') == 'finished'):
            self.pending = None
        self.save()
        return True

    def remaining(self):
        return float((self.state or {}).get('turn_deadline_at', 0)) - self.stamp - max(0, self.clock() - self.received)

    def post(self, url, payload, token):
        response = post_once(self.bot.session, safe_url(url), data=dict(payload, _token=token), timeout=10)
        response.raise_for_status()
        return self.unwrap(response)

    def probe_controls(self, root):
        """Record which actions the cup page offers, to spot new buttons/tactics. Never raises."""
        try:
            if not self.path:
                return
            path = self.path.with_name('cup_controls_probe.json')
            state = self.state or {}
            buttons = sorted({(b.get('data-action') or b.get('data-kind') or b.get('data-tactic') or
                               ' '.join(b.get('class', [])))[:60]
                              for b in root.select('button, [data-action], [data-kind], [data-tactic]')})
            entry = {
                'at': time.time(), 'fight_id': self.fight_id,
                'manual_urls': sorted(k for k in root.attrs if k.startswith('data-manual-')),
                'buttons': buttons[:80],
                'state_keys': sorted(state.keys()),
                'tactics': state.get('tactics'),
                # one full card per side, to see where the cup keeps buffs and other per-card fields
                'sample_cards': {side: (team or [None])[0] for side, team in (state.get('final') or {}).items()},
                'buff_keys': sorted(k for k in state if 'buff' in k),
                'extra': {k: state[k] for k in state if any(w in k for w in ('tactic', 'abilit', 'skill', 'heal', 'action', 'buff'))
                          and k != 'tactics'},
            }
            try:
                saved = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                saved = {}
            current = {
                'tactic': {el.get('data-tactic-kind') for el in root.select('[data-tactic-kind]')},
                'secret': {el.get('data-tactic-secret') for el in root.select('[data-tactic-secret]')},
                'state': set(state.keys()), 'url': set(entry['manual_urls']),
                'tactic_field': {k for t in (state.get('tactics') or {}).values() if isinstance(t, dict) for k in t},
            }
            alerted = set(saved.get('alerted', []))
            new = sorted(f'{group}:{name}' for group, names in current.items()
                         for name in names - CUP_KNOWN.get(group, set()) if f'{group}:{name}' not in alerted)
            if new:
                getattr(self.bot, 'log', print)('⚠️ Кубок: на странице появилось новое — ' + ', '.join(new))
                try:
                    self.bot.notifier.notify_alert('в кубке появилось что-то новое',
                                                   'Бот этого не знает: ' + ', '.join(new))
                except Exception:
                    pass
            path.write_text(json.dumps({'fights': (saved.get('fights', []) + [entry])[-5:],
                                        'alerted': sorted(alerted | set(new))},
                                       ensure_ascii=False, default=str), encoding='utf-8')
        except Exception:
            pass

    def dump_raw(self, state_response):
        """Keep the latest cup page and first state response, to see every field the cup sends. Never raises."""
        try:
            if self.path:
                self.path.with_name('cup_page_dump.html').write_text(getattr(self, '_page_html', ''), encoding='utf-8')
                self.path.with_name('cup_state_dump.json').write_text(
                    json.dumps(state_response, ensure_ascii=False, default=str), encoding='utf-8')
        except Exception:
            pass

    def fight(self, url, end, cancelled):
        response = get_html(self.bot.session, safe_url(url), timeout=10)
        response.raise_for_status()
        self._page_html = response.text
        soup = BeautifulSoup(response.text, 'html.parser')
        root = soup.select_one('[data-fight-id][data-manual-state-url]')
        self._page_blocks = {}
        for node in soup.select('[data-battle-buffs-json], [data-battle-seals-json], [data-battle-reward-json]'):
            name = next(k for k in node.attrs if k.endswith('-json'))
            try:
                self._page_blocks[name] = json.loads(node.get_text())
            except ValueError:
                pass
        if root is None:
            raise ValueError('Manual battle metadata missing')
        self.fight_id = str(root['data-fight-id'])
        self.active_fight = True
        self.save()
        self.state = None
        self.card_stats = {}
        self.hit_history = set()
        self.decisions = []  # every move with the full state it was made in, to replay policies offline
        self.secret_disabled = False
        self.history_complete = False
        self.history_revision = None
        for selector in ('[data-battle-log-json]', '[data-manual-state-json]'):
            script = root.select_one(selector)
            if script:
                document = json.loads(script.get_text())
                if selector == '[data-manual-state-json]' and isinstance(document.get('revision'), int):
                    self.history_revision = document['revision']
                if selector == '[data-battle-log-json]' and isinstance(document.get('steps'), list):
                    self.record_hits(document['steps'])
                    self.history_complete = True
                collections = []
                for key in ('cards', 'final'):
                    value = document.get(key, {})
                    collections.extend(value.values() if isinstance(value, dict) else [value])
                for cards in collections:
                    for card in cards if isinstance(cards, list) else []:
                        cid = int(card['card_user_id'])
                        self.card_stats.setdefault(cid, {}).update({key: card[key] for key in
                                               ('attack', 'crit_chance', 'max_hp', 'seal', 'seal_slug', 'seal_code', 'buff_code', 'buff')
                                               if key in card})
        side = root.get('data-bottom-real-side')
        uid = int(root.get('data-current-user-id') or getattr(self.bot, 'user_id', 0) or 0)
        if side not in ('attacker', 'defender') or uid <= 0:
            raise ValueError('Cannot establish manual player identity')
        token_el = soup.select_one('meta[name="csrf-token"]')
        if token_el is None:
            raise ValueError('Missing CSRF token')
        token = token_el['content']
        endpoints = {kind: safe_url(root.get('data-manual-' + kind + '-url')) for kind in ('state', 'attack', 'tactic')}
        taunts = {int(c['data-card-user-id']) for c in root.select('.battle-card--taunt[data-card-user-id]') if c.get('data-real-side') != side}
        fresh_post = False
        log = getattr(self.bot, 'log', lambda _: None)
        probed = False

        def finished():
            if self.path:
                history_path = self.path.with_name('manual_arena_history.json')
                try:
                    history = json.loads(history_path.read_text(encoding='utf-8')).get('battles', [])
                except (OSError, ValueError):
                    history = []
                fields = ('card_user_id', 'attack', 'max_hp', 'crit_chance', 'seal_code', 'buff_code', 'battle_card_energy')
                cards = {s: [{k: self.card_stats.get(int(c['card_user_id']), {}).get(k, c.get(k)) for k in fields}
                          for c in self.state.get('final', {}).get(s, [])] for s in ('attacker', 'defender')}
                item = {'id': self.fight_id, 'observed_at': time.time(), 'own_side': side,
                      'is_win': self.state.get('winner_user_id') == uid, 'cards': cards, 'source': 'manual_opponent_cards'}
                from combat_replay import capture_cup_trace
                archived = dict(item, decisions=self.decisions[-300:], taunts=sorted(taunts),
                                page_blocks=getattr(self, '_page_blocks', {}),
                                policy_version='cup-tactics-v2', **capture_cup_trace(self.state))
                battle_store.record(battle_store.db_path(str(history_path.parent)), archived, 'cup')
                history = [h for h in history if h.get('id') != self.fight_id] + [item]
                temporary = history_path.with_suffix('.tmp')
                temporary.write_text(json.dumps({'battles': history[-40:]}), encoding='utf-8')
                temporary.replace(history_path)
            log('Кубок: бой завершён fight=' + self.fight_id + ' winner=' + str(self.state.get('winner_user_id')) +
                ' revision=' + str(self.state.get('revision')))
            return 'finished'

        while self.clock() < end and not cancelled():
            if not fresh_post or self.clock() - self.received >= 2:
                data = self.unwrap(self.get(endpoints['state']))
                if not self.accept(data):
                    raise ValueError('Invalid manual state revision or fight identity')
                if not probed:
                    probed = True
                    self.probe_controls(root)
                    self.dump_raw(data)
            fresh_post = False
            if self.state.get('fight_status') == 'finished':
                return finished()
            revision = self.state['revision']
            blocked = self.pending and self.pending.get('fight_id') == self.fight_id and self.pending.get('revision') == revision
            if not blocked and int(self.state.get('current_turn_user_id', 0)) == uid and self.remaining() > 2:
                action = choose_action(self.state, side, taunts,
                                       self.hit_history if self.history_complete else None,
                                       allow_secret=not self.secret_disabled)
                if action:
                    kind, payload = action
                    self.decisions.append({'state': self.state, 'kind': kind, 'payload': payload, 'policy_version': 'cup-tactics-v2'})
                    self.pending = {'fight_id': self.fight_id, 'revision': revision, 'kind': kind, 'payload': payload}
                    self.save()
                    context = 'fight=' + self.fight_id + ' turn=' + str(self.state.get('turn')) + ' revision=' + str(revision) + ' kind=' + kind + ' cards=' + str(payload)
                    log('Кубок: действие ' + context + ' status=send')
                    try:
                        result = self.post(endpoints[kind], dict(payload, expected_revision=revision), token)
                        if not self.accept(result):
                            raise ValueError('Invalid manual action response')
                        log('Кубок: действие ' + context + ' status=accepted revision=' + str(self.state['revision']))
                        if self.state.get('fight_status') == 'finished':
                            return finished()
                        fresh_post = (not self.pending and int(self.state.get('current_turn_user_id', 0)) == uid
                                      and self.clock() - self.received < 2 and self.remaining() > 2)
                    except Exception as exc:
                        log('Кубок: действие ' + context + ' status=' + type(exc).__name__ + '; синхронизация')
                        if kind == 'tactic' and payload.get('kind') == 'secret':
                            self.secret_disabled = True
                    continue
            self.sleep(min(2, max(0, end - self.clock())))
        return 'budget'

    def run(self, max_seconds=120, cancelled=None, allow_repeat_attempts=False):
        cancelled = cancelled or (lambda: False)
        end = self.clock() + max(0, max_seconds)
        response = get_html(self.bot.session, safe_url('/battle/tournament'), timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        root = soup.select_one('.battle-tournament')
        if root is None:
            raise ValueError('Cup page unavailable')
        token_el = soup.select_one('meta[name="csrf-token"]')
        if token_el is None:
            raise ValueError('Missing CSRF token')
        queue_url, start_url = safe_url(root.get('data-cup-queue-url')), safe_url(root.get('data-cup-start-url'))
        joined = False
        while self.clock() < end and not cancelled():
            data = self.unwrap(self.get(queue_url))
            cup = data.get('tournament', {})
            active = data.get('redirect_url') or (data.get('active_fight') or cup.get('active_fight') or {}).get('url')
            if active:
                self.registration = None
                self.save()
                result = self.fight(safe_url(active), end, cancelled)
                if result != 'finished':
                    return result
                joined = False
                continue
            resolved_round = any(str(round_.get('fight_id')) == str(self.fight_id) and
                                 round_.get('status') in ('won', 'lost') for round_ in cup.get('rounds', []))
            if self.active_fight and (cup.get('attempt_status') in ('champion', 'eliminated') or resolved_round):
                self.active_fight = False
                self.pending = None
                self.last_result = {'fight_id': self.fight_id, 'source': 'cup_queue',
                                    'attempt_status': cup.get('attempt_status')}
                self.save()
            now = float(data.get('server_time') or cup.get('server_time') or 0)
            if not now or not float(cup.get('opens_at', 0)) <= now < float(cup.get('closes_at', 0)):
                return 'closed'
            # Winners may start another attempt (the winners list counts cups per player)
            champion_again = cup.get('attempt_status') == 'champion'
            if champion_again and not allow_repeat_attempts:
                return 'champion'
            if cup.get('attempt_status') == 'eliminated' and not allow_repeat_attempts:
                return 'eliminated'
            if cup.get('reward_limit_daily') and cup.get('reward_claimed'):
                return 'daily_reward_claimed'
            signature = [cup.get('date'), cup.get('opens_at'), cup.get('attempt_number'), cup.get('stage')]
            if self.registration and self.registration.get('signature') != signature:
                self.registration = None
                self.save()
            if data.get('status') == 'searching':
                self.registration = None
                self.save()
            if data.get('status') == 'matched':
                self.sleep(min(2, max(0, end - self.clock())))
                continue
            if data.get('status') != 'searching' and not joined:
                # A registration the server never confirmed must not block the cup for the rest of the evening
                if self.registration and 0 <= time.time() - float(self.registration.get('at', 0)) < 90:
                    return 'registration_pending'
                prepare = getattr(self.bot, 'prepare_arena_deck', None)
                if callable(prepare) and not prepare():
                    return 'deck_unavailable'
                if self.clock() >= end:
                    return 'budget'
                joined = True
                self.registration = {'signature': signature, 'at': time.time()}
                self.save()
                try:
                    # The page's token is fresh; the bot's may be stale after a relogin (HTTP 419/422)
                    result = self.post(start_url, {}, token_el['content'] or getattr(self.bot, 'csrf_token', None))
                    active = result.get('redirect_url') or (result.get('active_fight') or {}).get('url')
                    if active:
                        self.registration = None
                        self.save()
                        return self.fight(safe_url(active), end, cancelled)
                    if result.get('status') == 'searching':
                        self.registration = None
                        self.save()
                    elif champion_again:
                        return 'champion'  # no new attempt after the win: done for today
                except Exception:
                    self.registration = None  # the start was refused: nothing is pending
                    self.save()
                    if champion_again:
                        return 'champion'

            self.sleep(min(5, max(0, end - self.clock())))
        return 'budget'


def run_cup_cycle(bot, max_seconds=120, cancelled=None, state_path=None, allow_repeat_attempts=False):
    return ManualArenaController(bot, state_path=state_path).run(max_seconds, cancelled, allow_repeat_attempts)


# ==========================================
# ARENA MANAGEMENT & AWAKENING
# ==========================================
def owned_buff_options(root):
    options = {}
    active = None
    for item in root.select('[data-battle-buff-item]'):
        name = item.select_one('.battle-home__buff-info strong')
        inp = item.select_one('input[name="battle_buff_id"]')
        count = item.select_one('.battle-home__buff-count')
        code = BUFF_NAMES.get(name.get_text(strip=True)) if name else None
        if code is None or inp is None or count is None:
            continue
        value = int(re.sub(r'\D', '', count.get_text()) or 0)
        if value <= 0 or inp.has_attr('disabled'):
            continue
        options[code] = {'id': inp['value'], 'count': value}
        if 'battle-home__buff--active' in item.get('class', []):
            active = code
    return options, active


def canonical_card(card):
    result = dict(card)
    if isinstance(result.get('seal'), dict):
        sid = seal_id(result['seal'].get('name', ''))
        result['seal_code'] = result['seal'].get('code') or SEALS.get(sid)
        if sid:
            result['battle_seal_id'] = sid
    elif result.get('battle_seal_id'):
        result['seal_code'] = SEALS.get(int(result['battle_seal_id']))
    return result


def inventory_from_api_card(card, original):
    parsed = parse_roll_card(canonical_card(card))
    if parsed.get('seal_code') not in SEALS.values():
        raise ValueError('Unknown replacement seal')
    result = dict(original)
    result.update(battle_attack=parsed['attack'], battle_health=parsed['health'],
                  battle_crit_chance=parsed['crit_chance'], potential_rarity=parsed.get('potential_rarity'))
    if parsed.get('seal_code') in SEALS.values():
        result['battle_seal_id'] = next(k for k, v in SEALS.items() if v == parsed['seal_code'])
    return result


REROLL_SETTING = 'awaken_reroll'  # {active, min_attack, min_health, min_crit, cards: [{card_user_id, name, status, ...}]}
REROLL_DONE = ('reached', 'missing', 'refused')
# Seal names and short effects as the site shows them (data-battle-seals-json)
SEAL_INFO = {
    1: ('Печать двойного удара', '30% шанс повторной атаки', 'rare'),
    2: ('Печать ярости', '+30% атаки ниже 50% HP', 'common'),
    3: ('Печать казни', '+35% урона по слабым', 'common'),
    4: ('Печать берсерка', '+30% атаки после убийства', 'rare'),
    5: ('Печать охотника', '+75% первый удар', 'common'),
    6: ('Печать шипов', '30% урона обратно', 'rare'),
    7: ('Печать регенерации', '7% HP после хода', 'common'),
    8: ('Печать барьера', '-70% первый входящий урон', 'rare'),
    9: ('Печать бессмертия', 'Выживает с 1 HP', 'epic'),
    10: ('Печать мести', 'Урон после смерти', 'rare'),
    11: ('Печать цепной атаки', '25% ударить ещё врага', 'epic'),
    12: ('Печать вампиризма', '25% лечения от урона', 'rare'),
    13: ('Печать феникса', '35% воскреснуть', 'legendary'),
    14: ('Печать уворота', '20% шанс уклониться', 'epic'),
    15: ('Печать провокации', '100% шанс стать целью', 'epic'),
}
REROLL_HOLD_SEC = 3  # a roll stays at least this long before it is discarded, so «Остановить» can keep it


def card_stats(card):
    return {'attack': int(float(card['battle_attack'])), 'health': int(float(card['battle_health'])),
            'crit': int(float(card['battle_crit_chance'])), 'seal': card.get('battle_seal_id')}


def stats_text(card):
    s = card_stats(card)
    seal = SEAL_INFO.get(s['seal'], ('',))[0]
    return f"ATK {s['attack']}, HP {s['health']}, CR {s['crit']}%" + (f", {seal.lower()}" if seal else '')


def reroll_meets(card, goal):
    """Inventory card (battle_* stats) reaches every minimum the user set."""
    return (float(card.get('battle_attack') or 0) >= float(goal.get('min_attack') or 0)
            and float(card.get('battle_health') or 0) >= float(goal.get('min_health') or 0)
            and float(card.get('battle_crit_chance') or 0) >= float(goal.get('min_crit') or 0))


def reroll_odds(samples, goal):
    """Share of past awakenings (each one an independent roll) that would have met the goal."""
    rolls = [c for c in samples if c.get('battle_attack')]
    if not rolls:
        return None
    return sum(reroll_meets(c, goal) for c in rolls) / len(rolls)


def reroll_card_from_response(response, cid):
    """Stats of the awakened card from the site answer, or None to fall back to the collection."""
    api = response.get('card') if isinstance(response, dict) else None
    if not isinstance(api, dict) or str(api.get('card_user_id')) != str(cid):
        return None
    try:
        parsed = parse_roll_card(canonical_card(api))
    except ValueError:
        return None
    return {'id': cid, 'battle_attack': parsed['attack'], 'battle_health': parsed['health'],
            'battle_crit_chance': parsed['crit_chance'], 'battle_seal_id': canonical_card(api).get('battle_seal_id')}


def note_awaken_energy(left):
    """Remember how many awakenings are left, for the app's Awakening page."""
    DataManager.set_setting('awaken_energy', {'left': int(left), 'at': time.time()})


def reroll_energy_left(response):
    """Awakenings left after this one, or None when the answer does not say (then the page is read)."""
    try:
        return int(response['awakening_energy']) + int(response.get('free_left') or 0)
    except (KeyError, TypeError, ValueError):
        return None


class AwakenRerollMixin:
    """Awaken the chosen cards again and again until each one's stats reach the user's minimums.

    Unawakening is free on the site; every awakening costs awakening energy, which arena
    fights refill. The collection is the only truth: after every request the card is looked up
    again, so a crash between request and answer never spends energy twice."""

    def _reroll_card(self, route, item, awake, max_pages=10):
        for page in range(1, max_pages + 1):
            data = self.management_post(route, {'search': item['name'], 'is_battle': awake, 'per_page': 70, 'page': page})
            rows = data.get('data')
            if not isinstance(rows, list):
                raise ValueError('Inventory shape changed')
            found = [c for c in rows if int(c['id']) == int(item['card_user_id'])]
            if found:
                if int(found[0].get('in_trade') or 0):
                    raise ValueError('Card is in a trade')
                return found[0]
            if not data.get('next_page_url'):
                return None
        return None

    def _reroll_energy(self):
        _, root = self.management_page('/battle/awakening', '.battle-awakening')

        def count(selector):
            return int(re.sub(r'\D', '', root.select_one(selector).get_text()) or 0)
        left = count('[data-awakening-free-left]') + count('[data-awakening-energy]')
        note_awaken_energy(left)
        return root['data-awakening-url'], left

    def _reroll_refresh_cache(self, cid, card):
        """Put the latest roll into the cached collection the app lists cards from."""
        cards = self.management_state().get('inventory') or []
        for c in cards:
            if int(c['id']) == int(cid):
                c.update({k: card[k] for k in ('battle_attack', 'battle_health', 'battle_crit_chance', 'battle_seal_id')
                          if card.get(k) is not None})
                self.management_save({'inventory': cards})
                return

    def _reroll_update(self, cid, **values):
        """Change one card in what is stored now: «Стоп» or a new list chosen in the app must not be overwritten."""
        latest = DataManager.get_setting(REROLL_SETTING) or {}
        cards = latest.get('cards') or []
        for item in cards:
            if int(item['card_user_id']) == int(cid):
                item.update(values)
                DataManager.set_setting(REROLL_SETTING, latest)
                return item
        return None

    def _reroll_restore_deck(self):
        """Unawakening drops a card from the active deck: put the remembered deck back."""
        saved = self.management_state().get('reroll_deck')
        if not saved:
            return
        _, decks = self.management_page('/battle/decks', '.battle-decks')
        payload = {'deck_id': saved['deck_id']}
        payload.update({f'card_user_ids[{i}]': cid for i, cid in enumerate(saved['ids'])})
        endpoint = same_site_route(decks.get('data-save-url'), '/battle/decks/save-cards')
        if self._arena_post(endpoint, payload):
            self.management_save({'reroll_deck': None})
        else:
            self.log('⚠️ Перебор пробуждения: не удалось вернуть карты в колоду — проверь колоду на сайте.')

    def _reroll_remember_deck(self, cid):
        if self.management_state().get('reroll_deck'):
            return
        _, decks = self.management_page('/battle/decks', '.battle-decks')
        active = decks.select_one('.battle-decks__deck--active[data-deck-id]')
        ids = [int(i['data-card-user-id']) for i in active.select('img[data-card-user-id]')] if active else []
        if cid in ids:
            self.management_save({'reroll_deck': {'deck_id': active['data-deck-id'], 'ids': ids}})

    def _reroll_still_wanted(self, cid):
        latest = DataManager.get_setting(REROLL_SETTING) or {}
        item = next((c for c in latest.get('cards') or [] if int(c['card_user_id']) == cid), None)
        if not latest.get('active') or item is None or item.get('status') in REROLL_DONE:
            return None  # stopped or the card was dropped from the list in the app meanwhile
        return latest

    def _reroll_one(self, route, unawaken_url, item, budget):
        """Roll one card. Returns (rolls spent, out_of_energy_or_stopped)."""
        cid = int(item['card_user_id'])
        card = self._reroll_card(route, item, 1)
        if card is None and self._reroll_card(route, item, -1) is None:
            self._reroll_update(cid, status='missing')
            self.log(f"⚠️ Перебор пробуждения: карта «{item['name']}» #{cid} не найдена в коллекции — пропускаю.")
            return 0, False
        rolls, energy, awaken_url, rolled_at = 0, None, None, None
        while rolls < budget and self._alive():
            goal = self._reroll_still_wanted(cid)
            if goal is None:
                return rolls, True
            if card is not None and reroll_meets(card, goal):
                self._reroll_update(cid, status='reached', result=card_stats(card))
                reached = getattr(self, 'on_reroll_reached', None)  # Buffy: Windows notification
                if reached:
                    reached(item['name'], stats_text(card))
                self.log(f"🎲 Перебор пробуждения: «{item['name']}» {stats_text(card)} — цель достигнута, оставил.")
                self.notifier.notify_alert('перебор пробуждения',
                                           f"«{item['name']}» выбила {stats_text(card)}. Цель достигнута, карта оставлена.")
                return rolls, False
            if energy is None:
                awaken_url, energy = self._reroll_energy()
            if energy <= 0:  # checked before unawakening: never leave a card plain
                return rolls, True
            if card is not None:
                self._reroll_remember_deck(cid)
                # Give the person time to see this roll and press «Остановить» before it is discarded
                while rolled_at is not None and time.monotonic() - rolled_at < REROLL_HOLD_SEC:
                    if self._reroll_still_wanted(cid) is None:
                        return rolls, True
                    time.sleep(0.25)
                self.management_save({'reroll_pending': 'unawaken'})
                try:
                    # Pairs, not {'card_user_ids[]': [cid]}: curl_cffi urlencodes a dict without doseq and sends "[cid]" (HTTP 422)
                    self.management_post(unawaken_url, [('card_user_ids[]', cid), ('confirmed', 1)], mutation=True,
                                         before_send=lambda: self._reroll_still_wanted(cid) is not None)
                except SendCancelled:  # «Остановить» came during the request spacing: the card stays as it is
                    self.management_save({'reroll_pending': None})
                    return rolls, True
                except ValueError as e:
                    if 'HTTP 422' not in str(e):
                        raise
                    # The site refuses this card (locked, in a squad it guards, ...): skip it, go on with the rest
                    self.management_save({'reroll_pending': None})
                    reason = str(e).split(':', 1)[-1].strip() or 'сайт отказал'
                    self._reroll_update(cid, status='refused', reason=reason[:160])
                    self.log(f"⚠️ Перебор пробуждения: сайт не даёт снять пробуждение с «{item['name']}» ({reason}) — пропускаю.")
                    return rolls, False
                # No collection check here: if unawakening silently failed, the site refuses to
                # awaken an awakened card and the series stops on that error
            self.management_save({'reroll_pending': 'awaken'})
            response = self.management_post(awaken_url, {'card_user_id': cid}, mutation=True)
            # The answer carries the new stats and the energy left: two requests per roll instead of six
            card = reroll_card_from_response(response, cid) or self._reroll_card(route, item, 1)
            if card is None:
                raise ValueError('Awakening not confirmed by the collection')
            energy = reroll_energy_left(response)
            if energy is not None:
                note_awaken_energy(energy)
            rolled_at = time.monotonic()
            self.management_save({'reroll_pending': None})
            rolls += 1
            done = int(item.get('attempts') or 0) + 1
            item = self._reroll_update(cid, attempts=done, status='running',
                                       history=(item.get('history') or [])[-29:] + [card_stats(card)]) or item
            self.log(f"🎲 Перебор пробуждения «{item['name']}» #{done}: {stats_text(card)}")
        return rolls, rolls >= budget

    def run_awaken_reroll(self, max_rolls=20):
        goal = DataManager.get_setting(REROLL_SETTING) or {}
        if not goal.get('active') or 19 <= self._msk_now().hour < 22:  # never touch the deck during the cup
            return 0
        _, root = self.management_page('/battle/unawaken', '.battle-unawaken')
        route = urljoin('https://mangabuff.ru', root.select_one('.card-filter-form[data-url]')['data-url'])
        unawaken_url = root['data-unawaken-url']
        self.management_save({'reroll_pending': None})  # each card is re-read from the collection below
        rolls = 0
        try:
            for item in goal.get('cards') or []:
                if item.get('status') in REROLL_DONE:
                    continue
                spent, paused = self._reroll_one(route, unawaken_url, item, max_rolls - rolls)
                rolls += spent
                if paused:
                    break
            latest = DataManager.get_setting(REROLL_SETTING) or {}
            cards = latest.get('cards') or []
            if latest.get('active') and cards and all(c.get('status') in REROLL_DONE for c in cards):
                DataManager.set_setting(REROLL_SETTING, {**latest, 'active': False})
                self.log('🎲 Перебор пробуждения: все выбранные карты готовы.')
            elif rolls and latest.get('active'):
                self.log('🎲 Перебор пробуждения: энергия кончилась — продолжу, когда бои её накопят.')
            return rolls
        finally:
            try:
                self._reroll_restore_deck()
            except Exception as e:  # must not hide the error that got us here
                self.log(f'⚠️ Перебор пробуждения: колоду проверю в следующем цикле ({e}).')
            for c in (DataManager.get_setting(REROLL_SETTING) or {}).get('cards') or []:
                last = c.get('result') or (c.get('history') or [None])[-1]
                if last:
                    self._reroll_refresh_cache(c['card_user_id'], {
                        'battle_attack': last['attack'], 'battle_health': last['health'],
                        'battle_crit_chance': last['crit'], 'battle_seal_id': last.get('seal')})


# Seal meta from 3555 arena and cup fights (Oct 2026): share of opponent cards with the seal, and how much
# it moved their win rate beyond what deck strength predicts. "" = within noise. Deck strength matters far more.
SEAL_META = {
    'regeneration': (25.5, 2.3, 'сильнее'), 'vampirism': (21.6, 0.9, ''), 'rage': (8.5, -1.1, ''),
    'hunter': (7.3, -2.8, ''), 'execution': (6.4, -3.7, 'слабее'), 'berserk': (4.5, -6.6, 'слабее'),
    'thorns': (4.2, 3.6, ''), 'dodge': (4.2, 1.5, ''), 'double_strike': (4.1, -1.7, ''), 'revenge': (3.9, -3.3, ''),
    'barrier': (3.8, -1.0, ''), 'immortality': (2.7, -0.8, ''), 'phoenix': (1.3, -3.6, ''),
    'taunt': (1.1, -9.6, 'слабее'), 'chain_attack': (1.0, -5.2, ''),
}
SEAL_SETTING = 'seal_reroll'  # {active, seals: [ids], max_essence, spent, cards: [{card_user_id, name, status, ...}]}
SEAL_REROLL_COST = 500


def note_essence(left):
    DataManager.set_setting('reroll_essence', {'left': int(left), 'at': time.time()})


def _digits(text):
    return int(re.sub(r'\D', '', str(text)) or 0)


class SealRerollMixin:
    """Reforge the seal of chosen awakened cards until it is one of the wanted seals.

    Each reforge costs essence (500) and keeps attack, HP and crit; the bot checks that and stops
    on any change. Never spends past the user's essence limit."""

    def _seal_wanted(self, cid):
        latest = DataManager.get_setting(SEAL_SETTING) or {}
        item = next((c for c in latest.get('cards') or [] if int(c['card_user_id']) == cid), None)
        if not latest.get('active') or item is None or item.get('status') in REROLL_DONE:
            return None
        return latest

    def _seal_update(self, cid, **values):
        latest = DataManager.get_setting(SEAL_SETTING) or {}
        for item in latest.get('cards') or []:
            if int(item['card_user_id']) == int(cid):
                item.update(values)
                DataManager.set_setting(SEAL_SETTING, latest)
                return item
        return None

    def run_seal_reroll(self, max_rolls=40):
        goal = DataManager.get_setting(SEAL_SETTING) or {}
        if not goal.get('active') or 19 <= self._msk_now().hour < 22:
            return 0
        _, root = self.management_page('/battle/reroll', '.battle-reroll')
        seal_url, card_url = root['data-reroll-seal-url'], root['data-reroll-card-url']
        essence = _digits(root.select_one('[data-reroll-essence]').get_text())
        note_essence(essence)
        self.management_save({'seal_pending': None})
        rolls = 0
        for item in goal.get('cards') or []:
            if item.get('status') in REROLL_DONE:
                continue
            cid = int(item['card_user_id'])
            stats_goal = DataManager.get_setting(REROLL_SETTING) or {}
            if stats_goal.get('active') and any(int(c['card_user_id']) == cid and c.get('status') not in REROLL_DONE
                                                for c in stats_goal.get('cards') or []):
                continue  # finishes the stats reroll first; the seal is reforged afterwards
            try:
                card = self.management_post(card_url, {'card_user_id': cid}).get('card') or {}
            except ValueError:
                card = {}
            if str(card.get('card_user_id')) != str(cid):
                self._seal_update(cid, status='missing')
                self.log(f"⚠️ Перековка печати: «{item['name']}» #{cid} не найдена среди пробуждённых — пропускаю.")
                continue
            rolled_at, paused = None, False
            while rolls < max_rolls and self._alive():
                latest = self._seal_wanted(cid)
                if latest is None:
                    return rolls
                seal = seal_id((card.get('seal') or {}).get('name')) if isinstance(card.get('seal'), dict) \
                    else card.get('battle_seal_id')
                stats = {'attack': int(float(card['attack'])), 'health': int(float(card['health'])),
                         'crit': int(float(card['crit_chance'])), 'seal': seal}
                if seal in {int(s) for s in latest.get('seals') or []}:
                    self._seal_update(cid, status='reached', result=stats)
                    text = f"«{item['name']}» получила {SEAL_INFO.get(seal, ('печать',))[0].lower()}"
                    self.log(f'🔮 Перековка печати: {text} — оставил.')
                    self.notifier.notify_alert('перековка печати', text + '.')
                    reached = getattr(self, 'on_reroll_reached', None)
                    if reached:
                        reached(item['name'], SEAL_INFO.get(seal, ('печать',))[0].lower())
                    break
                limit = int(latest.get('max_essence') or 0)
                if essence < SEAL_REROLL_COST or (limit and int(latest.get('spent') or 0) + SEAL_REROLL_COST > limit):
                    self.log('🔮 Перековка печати: эссенция кончилась или достигнут лимит — жду.')
                    return rolls
                while rolled_at is not None and time.monotonic() - rolled_at < REROLL_HOLD_SEC:
                    if self._seal_wanted(cid) is None:
                        return rolls
                    time.sleep(0.25)
                self.management_save({'seal_pending': cid})
                try:
                    response = self.management_post(seal_url, {'card_user_id': cid}, mutation=True,
                                                    before_send=lambda: self._seal_wanted(cid) is not None)
                except SendCancelled:
                    self.management_save({'seal_pending': None})
                    return rolls
                except ValueError as e:
                    if 'HTTP 422' not in str(e):
                        raise
                    self.management_save({'seal_pending': None})
                    reason = str(e).split(':', 1)[-1].strip() or 'сайт отказал'
                    self._seal_update(cid, status='refused', reason=reason[:160])
                    self.log(f"⚠️ Перековка печати: сайт не даёт перековать «{item['name']}» ({reason}) — пропускаю.")
                    break
                new = response.get('card') or {}
                if str(new.get('card_user_id')) != str(cid):
                    raise ValueError('Seal reforge answered for another card')
                if any(int(float(new[k])) != stats[s] for k, s in (('attack', 'attack'), ('health', 'health'),
                                                                    ('crit_chance', 'crit'))):
                    raise ValueError('Seal reforge changed attack, HP or crit — stopped')
                cost = _digits(response.get('cost') or SEAL_REROLL_COST)
                essence = _digits(response.get('essence')) if response.get('essence') is not None else essence - cost
                note_essence(essence)
                self.management_save({'seal_pending': None})
                card, rolls, rolled_at = new, rolls + 1, time.monotonic()
                new_seal = seal_id((new.get('seal') or {}).get('name'))
                item = self._seal_update(cid, attempts=int(item.get('attempts') or 0) + 1, status='running',
                                         history=(item.get('history') or [])[-29:] + [new_seal]) or item
                spent = int((DataManager.get_setting(SEAL_SETTING) or {}).get('spent') or 0) + cost
                DataManager.set_setting(SEAL_SETTING, {**(DataManager.get_setting(SEAL_SETTING) or {}), 'spent': spent})
                self.log(f"🔮 Перековка печати «{item['name']}» #{item['attempts']}: "
                         f"{SEAL_INFO.get(new_seal, ('?',))[0]} (эссенции осталось {essence})")
            else:
                paused = True
            if paused:
                break
        latest = DataManager.get_setting(SEAL_SETTING) or {}
        cards = latest.get('cards') or []
        if latest.get('active') and cards and all(c.get('status') in REROLL_DONE for c in cards):
            DataManager.set_setting(SEAL_SETTING, {**latest, 'active': False})
            self.log('🔮 Перековка печати: все выбранные карты готовы.')
        return rolls


class ArenaManagementMixin:
    def management_state(self):
        return DataManager.load_json(MANAGEMENT_FILE)

    def management_save(self, values):
        DataManager.save_json(MANAGEMENT_FILE, values)
        stored = self.management_state()
        if any(stored.get(k) != v for k, v in values.items()):
            raise OSError('Arena state not persisted')

    def management_page(self, path, selector):
        soup = self._arena_get_soup(path)
        root = soup.select_one(selector) if soup else None
        if root is None:
            raise ValueError('Required arena page unavailable: ' + path)
        return soup, root

    def management_post(self, url, data, mutation=False, before_send=None):
        url = same_site_route(url, '/')
        headers = {'X-CSRF-TOKEN': self.csrf_token, 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json', 'Referer': 'https://mangabuff.ru/battle'}
        send = (lambda s, u, **kw: post_once(s, u, before_send=before_send, **kw)) if mutation \
            else lambda s, u, **kw: s.post(u, **kw)
        response = send(self.session, url, data=data, headers=headers, timeout=15)
        if response.status_code != 200 or is_anti_bot_page(response):
            detail = ''
            if response.status_code == 422:
                try:
                    detail = ': ' + str(response.json().get('message', ''))[:180]
                except (ValueError, AttributeError):
                    pass
            raise ValueError('Arena API HTTP ' + str(response.status_code) + detail)
        body = response.json()
        if not isinstance(body, dict) or body.get('success') is False or body.get('status') is False:
            raise ValueError('Arena API rejected operation')
        return body

    def load_arena_inventory(self, form, awake=1, max_pages=30):
        url = urljoin('https://mangabuff.ru', form['data-url'])
        rows = []
        seen = set()
        for page in range(1, max_pages + 1):
            if not self._alive():
                raise RuntimeError('module aborted')
            data = self.management_post(url, {'is_battle': awake, 'per_page': 70, 'page': page})
            batch = data.get('data')
            if not isinstance(batch, list):
                raise ValueError('Inventory shape changed')
            ids = tuple(int(c['id']) for c in batch)
            if ids and ids in seen:
                raise ValueError('Repeated inventory page')
            seen.add(ids)
            rows.extend(batch)
            if not data.get('next_page_url'):
                return rows
            if page == max_pages:
                raise ValueError('Inventory incomplete; refuse optimizing partial scan')
        return rows

    def protect_arena_cards(self, cards, current, best):
        state = self.management_state()
        protected = protected_card_ids(cards, current, best, bench_size=24, per_seal=3)
        recommended = sorted(protected)
        protected.update(int(i) for i in state.get('protected_ids', []))
        self.management_save({'protected_ids': sorted(protected), 'recommended_protected_ids': recommended,
                              'legacy_protected_ids': sorted(protected - set(recommended))})
        byid = {int(c['id']): c for c in cards}
        unlocked = [cid for cid in protected if cid in byid and not int(byid[cid].get('is_lock') or 0)]
        for start in range(0, len(unlocked), 20):
            if not self._alive():
                raise RuntimeError('module aborted')
            ids = unlocked[start:start + 20]
            data = {'is_lock': 1}
            data.update({f'card_ids[{i}]': cid for i, cid in enumerate(ids)})
            self.management_post('https://mangabuff.ru/trades/lockCards', data, mutation=True)
            for cid in ids:
                byid[cid]['is_lock'] = 1
        if unlocked:
            _, root = self.management_page('/battle/decks', '.battle-decks')
            fresh = self.load_arena_inventory(root.select_one('.card-filter-form[data-url]'))
            confirmed = {int(c['id']) for c in fresh if int(c.get('is_lock') or 0)}
            if not set(unlocked) <= confirmed:
                raise ValueError('Card protection not confirmed by server')
            self.management_save({'inventory': cards})
            self.management_save({'auto_locked_ids': sorted(set(state.get('auto_locked_ids', [])) | set(unlocked))})
            self.log(f'🔒 Арена: закрыто {len(unlocked)} карт; актуальный боевой резерв {len(recommended)}, всего сохранённых замков {len(protected)}.')
        return protected

    def refresh_arena_meta(self, banned):
        state = self.management_state()
        if time.time() - float(state.get('meta_at', 0)) < 6 * 3600 and state.get('meta_banned') == banned:
            return state.get('meta_teams', [])
        soup = self._arena_get_soup('/battle/leaderboard')
        if soup is None:
            return state.get('meta_teams', [])
        leaders = []
        for el in soup.select('.battle-leaderboard__row[href]'):
            m = re.search(r'/users/(\d+)', el['href'])
            if m and m[1] not in leaders:
                leaders.append(m[1])
        teams = []
        for uid in leaders[:3]:
            if not self._alive():
                break
            try:
                page = self._arena_get_soup('/users/' + uid + '/cards')
                if page is None:
                    continue
                data = self.management_post('https://mangabuff.ru/cards-filter/' + uid, {'is_battle': 1, 'battle_sort': 'attack', 'per_page': 12, 'page': 1})
                pool = [from_inventory(c) for c in data.get('data', [])]
                pool = [c for c in pool if c and c['seal_id'] != banned]
                pool.sort(key=heuristic, reverse=True)
                selected = []
                bases = set()
                for c in pool:
                    if c['base_id'] in bases:
                        continue
                    bases.add(c['base_id'])
                    selected.append(c)
                    if len(selected) == 4:
                        break
                if len(selected) == 4:
                    teams.append({'cards': selected, 'source': 'derived_from_public_leader_cards', 'user_id': uid})
            except Exception as exc:
                self.log('ℹ️ Арена: публичный мета-профиль недоступен (' + type(exc).__name__ + ').')
        self.management_save({'meta_at': time.time(), 'meta_teams': teams, 'meta_leaders': leaders[:10], 'meta_banned': banned})
        return teams

    def prepare_best_arena_deck(self, _retry=False):
        soup, home = self.management_page('/battle', '.battle-home')
        ban = home.select_one('.battle-home__banned-seal strong')
        banned = seal_id(ban.get_text()) if ban else None
        fighters = home.select('.battle-home__fighter')
        if len(fighters) < 4 and not (ban and banned is None):
            # New accounts have no arena deck yet: say what to do and look again in 6 hours
            self.log('🃏 Арена: на аккаунте ещё нет колоды из 4 карт. Пробуди 4 карты в «Пробуждении» (первые 4 бесплатно) '
                     'и собери из них колоду: Карточные бои → Колоды.')
            DataManager.set_setting('arena_no_deck', True)
            self._arena_next_check_at = time.time() + 6 * 3600
            return False
        if len(fighters) != 4 or (ban and banned is None):
            raise ValueError('Unknown arena composition/ban')
        if DataManager.get_setting('arena_no_deck'):
            DataManager.set_setting('arena_no_deck', False)
        deck_soup, decks = self.management_page('/battle/decks', '.battle-decks')
        active = decks.select_one('.battle-decks__deck--active[data-deck-id]')
        form = decks.select_one('.card-filter-form[data-url]')
        if active is None or form is None:
            raise ValueError('Active deck missing')
        current = [int(i['data-card-user-id']) for i in active.select('img[data-card-user-id]')]
        if len(current) != 4:
            raise ValueError('Incomplete active deck')
        energy = {}
        for cid, f in zip(current, fighters):
            el = f.select_one('.battle-home__energy b')
            m = re.search(r'(\d+)\s*/\s*(\d+)', el.get_text() if el else '')
            if m is None:
                raise ValueError('Unknown fighter energy')
            energy[cid] = int(m[1])
        state = self.management_state()
        # Bench cards: last seen energy, or full if a 4-hour reset mark has passed since
        points = last_energy_points(load_observations(os.path.join(BASE_DIR, 'arena_observations.json')),
                                    state.get('energy_observations', {}))
        for cid, value in projected_energy(points).items():
            energy.setdefault(cid, value)
        cards = self.load_arena_inventory(form)
        self.management_save({'inventory': cards, 'inventory_at': time.time()})
        protected = self.protect_arena_cards(cards, current, current)
        battles = recent_battles(load_observations(os.path.join(BASE_DIR, 'arena_observations.json')))
        result = None
        buff_code = None
        buff_ranking = []
        buff_options, active_buff = owned_buff_options(home)
        if supported_definitions(battles):
            meta = self.refresh_arena_meta(banned)
            manual = DataManager.load_json(os.path.join(BASE_DIR, 'manual_arena_history.json')).get('battles', [])
            manual_teams = []
            for observed in manual[-2:]:
                if time.time() - float(observed.get('observed_at', 0)) > 7 * 86400:
                    continue
                side = 'defender' if observed.get('own_side') == 'attacker' else 'attacker'
                rows = observed.get('cards', {}).get(side, [])
                if len(rows) == 4:
                    manual_teams.append({'cards': rows, 'source': 'manual_opponent_cards'})
            # A wide pool of real opponents from the archive instead of the last three
            archive = battle_store.load(battle_store.db_path(BASE_DIR), source='arena',
                                        since=time.time() - 3 * 86400, limit=600, with_steps_only=True)
            pool = archive_opponent_teams(archive, limit=8) if len(archive) >= 20 else opponent_teams(battles, limit=3)
            opponents = pool + manual_teams + meta[:2]

            def currently_legal_opponent(entry):
                rows = entry.get('cards', []) if isinstance(entry, dict) else entry
                return not any(c.get('seal_id') == banned or c.get('seal_code', c.get('seal')) == SEALS.get(banned) for c in rows)

            opponents = [entry for entry in opponents if currently_legal_opponent(entry)]
            normalized = normalized_owned(cards, battles, energy)
            known = {int(c['id']): c for c in normalized}
            rules = calibrate_rules(battles)
            if len(archive) >= 50:
                import arena_analysis
                rules.update(arena_analysis.learned_rules(archive))
            buff_ranking = evaluate_buffs([known[i] for i in current], opponents, buff_options, rules, samples=24, cancelled=self._module_cancelled)
            if buff_ranking:
                buff_code = buff_ranking[0]['code']
            result = optimize_deck(current, normalized, banned, opponents,
                rules=dict(rules, own_buff=buff_code), energy_map=energy, cancelled=self._module_cancelled, budget_seconds=20)
            if result:
                ranking = evaluate_buffs([known[i] for i in result[0]], opponents, buff_options, rules, samples=24, cancelled=self._module_cancelled)
                if ranking and ranking[0]['code'] != buff_code:
                    buff_code = ranking[0]['code']
                    buff_ranking = ranking
                    result = optimize_deck(current, normalized, banned, opponents,
                        rules=dict(rules, own_buff=buff_code), energy_map=energy, cancelled=self._module_cancelled, budget_seconds=20)
        if result is None:
            byid = {int(c['id']): c for c in cards}
            if any(cid not in byid or int(byid[cid].get('battle_seal_id') or 0) == banned for cid in current):
                return False
            chosen = current
            diagnostics = {'reason': 'no_valid_model_keep_current'}
        else:
            chosen, diagnostics = result
        diagnostics['buff_ranking'] = buff_ranking
        diagnostics['selected_buff'] = buff_code
        self.management_save({'best_ids': chosen, 'optimizer': diagnostics, 'optimized_at': time.time(), 'banned_seal': banned, 'inventory': cards})
        self.protect_arena_cards(cards, current, chosen)
        if chosen != current:
            endpoint = same_site_route(decks.get('data-save-url'), '/battle/decks/save-cards')
            data = {'deck_id': active['data-deck-id']}
            data.update({f'card_user_ids[{i}]': cid for i, cid in enumerate(chosen)})
            self.management_post(endpoint, data, mutation=True)
            fresh, roots = self.management_page('/battle/decks', '.battle-decks')
            stored = roots.select_one('.battle-decks__deck--active')
            ids = [int(i['data-card-user-id']) for i in stored.select('img[data-card-user-id]')] if stored else []
            if ids != chosen:
                raise ValueError('Deck save not confirmed')
            self.log('🧮 Арена: установлен лучший найденный состав с учётом синергий и наблюдавшихся соперников.')
        if result and buff_ranking and buff_code != active_buff:
            endpoint = same_site_route(home.get('data-battle-buff-select-url'), '/battle/buff/select')
            self.management_post(endpoint, {'battle_buff_id': buff_options[buff_code]['id'] if buff_code else ''}, mutation=True)
            _, confirmed = self.management_page('/battle', '.battle-home')
            _, confirmed_code = owned_buff_options(confirmed)
            if confirmed_code != buff_code:
                raise ValueError('Buff selection not confirmed')
            self.log('✦ Арена: выбран лучший найденный доступный баф ' + str(buff_code) + '.')
        self._arena_buff_remaining = buff_options[buff_code]['count'] if buff_code else None
        self._arena_buff_depleted = False
        fresh, home = self.management_page('/battle', '.battle-home')
        check = home.select('.battle-home__fighter')
        if len(check) != 4 or any(seal_id(f.select_one('.battle-home__seal').get_text()) == banned for f in check):
            return False
        energies = []
        for f in check:
            el = f.select_one('.battle-home__energy b')
            m = re.search(r'(\d+)\s*/\s*(\d+)', el.get_text() if el else '')
            if m is None:
                return False
            energies.append(int(m[1]))
        self._arena_low_energy = min(energies) < ENERGY_TO_FIGHT
        self._arena_min_energy = min(energies)
        energy_observations = dict(state.get('energy_observations', {}))
        for cid, value in zip(chosen, energies):
            energy_observations[str(cid)] = {'value': value, 'at': time.time()}
        self.management_save({'energy_observations': energy_observations})
        if self._arena_low_energy and chosen != current and not _retry:
            # A bench card's energy was estimated too high: now it's an exact read,
            # so pick again right away instead of idling for hours.
            self.log('🔁 Арена: карта в новом составе ещё не восстановилась — пересобираю колоду.')
            return self.prepare_best_arena_deck(_retry=True)
        self._arena_start_url = same_site_route(home.get('data-battle-start-url'), '/battle/fight/start')
        self._arena_inventory = cards
        self._arena_protected_ids = protected
        return True

    def run_card_awakening(self, max_cards=3):
        pending = self.management_state().get('awakening_pending')
        if pending:
            _, decks = self.management_page('/battle/decks', '.battle-decks')
            fresh = self.load_arena_inventory(decks.select_one('.card-filter-form[data-url]'))
            if not any(int(c['id']) == int(pending['id']) for c in fresh):
                self.log('⚠️ Неподтверждённое пробуждение: повтор не отправляется.')
                return 0
            best = self.management_state().get('best_ids', [])
            self.protect_arena_cards(fresh, best, best)
            self._arena_inventory = fresh
            self.management_save({'inventory': fresh, 'inventory_at': time.time(), 'awakening_pending': None})
            self.log('✦ Ранее выполненное пробуждение подтверждено по коллекции.')
        if not DataManager.get_setting('arena_auto_awaken', False):
            return 0
        soup, root = self.management_page('/battle/awakening', '.battle-awakening')

        def number(selector):
            el = root.select_one(selector)
            if el is None:
                raise ValueError('Unknown awakening balance')
            return int(re.sub(r'\D', '', el.get_text()))

        free = number('[data-awakening-free-left]')
        energy = number('[data-awakening-energy]')
        count = min(max_cards, free + energy)
        if count <= 0:
            return 0
        form = root.select_one('.card-filter-form[data-url]')
        data = self.management_post(urljoin('https://mangabuff.ru', form['data-url']), {'is_battle': -1, 'per_page': 70, 'page': 1})
        candidates = [c for c in data.get('data', []) if not int(c.get('in_trade') or 0)]
        existing = {int(c['card_id']) for c in getattr(self, '_arena_inventory', [])}
        candidates.sort(key=lambda c: (int(c['card_id']) in existing, int(c.get('copy_number') or 1000000) < 1000, int(c['id'])))
        done = 0
        for original in candidates:
            if done >= count or not self._alive():
                break
            state = self.management_state()
            if state.get('awakening_pending'):
                break
            cid = int(original['id'])
            self.management_save({'awakening_pending': {'id': cid, 'at': time.time()}})
            try:
                response = self.management_post(root['data-awakening-url'], {'card_user_id': cid}, mutation=True)
                card = inventory_from_api_card(response.get('card') or response, original)
            except Exception:
                self.log('⚠️ Пробуждение: результат не подтверждён; сохранено ожидание для сверки.')
                break
            done += 1
            cards = list(getattr(self, '_arena_inventory', []))
            cards = [c for c in cards if int(c['id']) != cid] + [card]
            self._arena_inventory = cards
            current = state.get('best_ids', [])
            self.management_save({'inventory': cards, 'inventory_at': 0, 'awakening_pending': None})
            _, decks = self.management_page('/battle/decks', '.battle-decks')
            cards = self.load_arena_inventory(decks.select_one('.card-filter-form[data-url]'))
            self.protect_arena_cards(cards, current, current)
            self._arena_inventory = cards
            self.log(f'✦ Пробуждена карта #{cid}: ATK {card["battle_attack"]}, HP {card["battle_health"]}, CR {card["battle_crit_chance"]}%.')
        return done

    def run_reroll_research(self, max_rolls=11, budget=None, plans=None):
        """Explicit research only. Never called by the ordinary daemon scheduler."""
        soup, root = self.management_page('/battle/reroll', '.battle-reroll')
        balance_el = root.select_one('[data-reroll-essence]')
        if balance_el is None:
            raise ValueError('Unknown essence balance')
        balance = int(re.sub(r'\D', '', balance_el.get_text()))
        _, decks = self.management_page('/battle/decks', '.battle-decks')
        active = decks.select_one('.battle-decks__deck--active')
        current = [int(i['data-card-user-id']) for i in active.select('img[data-card-user-id]')] if active else []
        if len(current) != 4:
            raise ValueError('Confirm current deck before research')
        cards = self.load_arena_inventory(decks.select_one('.card-filter-form[data-url]'))
        state = self.management_state()
        protected = self.protect_arena_cards(cards, current, state.get('best_ids', []))
        self.management_save({'inventory': cards, 'inventory_at': time.time()})
        canonical = [canonical_card(c) for c in cards]
        weak = choose_test_card(canonical, protected)
        if weak is None:
            return {'rolls': 0, 'reason': 'no_safe_test_copy'}
        cid = int(weak['card_user_id'])
        stored = DataManager.load_json(RESEARCH_FILE)
        if stored.get('ledger', {}).get('pending'):
            raise ValueError('Unresolved earlier research mutation')
        ledger = TestLedger(budget=min(balance, int(budget if budget is not None else balance)))
        ledger.shield([c for c in canonical if int(c['id']) in protected])
        ledger.observed_balance = balance
        samples = stored.get('samples', [])
        plans = plans or [('stats', []), ('stats', []), ('stats', []), ('stats', ['attack']), ('stats', ['health']),
               ('stats', ['crit_chance']), ('stats', ['attack', 'health']), ('stats', ['attack', 'crit_chance']),
               ('stats', ['health', 'crit_chance']), ('seal', []), ('seal', [])]
        original = next(c for c in cards if int(c['id']) == cid)
        response = self.management_post(root['data-reroll-card-url'], {'card_user_id': cid})
        api = dict(response['card'], card_user_id=cid)
        working = canonical_card(api)
        if fingerprint(working) != fingerprint(canonical_card(original)):
            raise ValueError('Test copy changed after inventory scan; refusing roll')
        results = []
        for kind, locks in plans[:max_rolls]:
            if not self._alive() or cid in protected:
                break
            cost = 500 if kind == 'seal' else 300 * 2 ** len(locks)
            if cost > ledger.remaining or cost > balance:
                break
            action = {'type': kind, 'card_user_id': str(cid), 'locked_stats': locks, 'cost': cost, 'rationale': 'bounded research of replacement and lock mechanics on a weak copy'}
            before = parse_roll_card(working)
            ledger.reserve(action, working, balance)
            DataManager.save_json(RESEARCH_FILE, {'ledger': ledger.to_dict(), 'samples': samples, 'test_copy': cid, 'pending_response': None})
            if json.dumps(DataManager.load_json(RESEARCH_FILE).get('ledger', {}).get('pending'), sort_keys=True) != json.dumps(ledger.to_dict()['pending'], sort_keys=True):
                raise ValueError('Research reservation not persisted; refusing mutation')
            payload = {'card_user_id': cid}
            payload.update({f'locked_stats[{i}]': key for i, key in enumerate(locks)})
            endpoint = root['data-reroll-' + kind + '-url']
            result = self.management_post(endpoint, payload, mutation=True)
            DataManager.save_json(RESEARCH_FILE, {'pending_response': {'essence': result.get('essence'), 'card': result.get('card')}})
            verified = self.management_post(root['data-reroll-card-url'], {'card_user_id': cid})
            working = canonical_card(dict(verified['card'], card_user_id=cid))
            after = parse_roll_card(working)
            if any(after[key] != before[key] for key in locks):
                raise ValueError('Server failed to preserve locked stat')
            if result.get('essence') is not None:
                balance = int(re.sub(r'\s', '', str(result['essence'])))
            else:
                _, fresh_root = self.management_page('/battle/reroll', '.battle-reroll')
                balance = int(re.sub(r'\D', '', fresh_root.select_one('[data-reroll-essence]').get_text()))
            ledger.reconcile(balance, resolved=True)
            updated = inventory_from_api_card(working, original)
            original = updated
            cards = [c for c in cards if int(c['id']) != cid] + [updated]
            newly = protected_card_ids(cards, state.get('best_ids', []), state.get('best_ids', []), bench_size=24, per_seal=3)
            protected.update(newly)
            sample = {'type': kind, 'locked_stats': locks, 'card': working, 'before': before, 'cost': cost, 'at': time.time()}
            samples.append(sample)
            samples = samples[-500:]
            ledger.protected_ids.update(str(i) for i in protected)
            ledger.shield([canonical_card(c) for c in cards if int(c['id']) in protected and str(c['id']) not in ledger.protected_fingerprints])
            DataManager.save_json(RESEARCH_FILE, {'ledger': ledger.to_dict(), 'samples': samples, 'test_copy': cid, 'pending_response': None})
            self.management_save({'inventory': cards, 'inventory_at': 0, 'protected_ids': sorted(protected)})
            results.append({'type': kind, 'locks': locks, 'cost': cost, 'after': after})
            self.log(f'🧪 Перековка: слабый экземпляр #{cid}, {kind}, локов {len(locks)}, потрачено {cost}.')
            if cid in protected:
                self.protect_arena_cards(cards, state.get('best_ids', []), state.get('best_ids', []))
                self.log('🔒 Тестовая карта стала перспективной — роллы остановлены, карта защищена.')
                weak = choose_test_card([canonical_card(c) for c in cards], protected)
                if weak is None:
                    break
                cid = int(weak['card_user_id'])
                original = next(c for c in cards if int(c['id']) == cid)
                response = self.management_post(root['data-reroll-card-url'], {'card_user_id': cid})
                working = canonical_card(dict(response['card'], card_user_id=cid))
                if fingerprint(working) != fingerprint(canonical_card(original)):
                    raise ValueError('Next test copy changed; refusing roll')
        fresh = self.load_arena_inventory(root.select_one('.card-filter-form[data-url]'))
        ledger.assert_protected_unchanged([canonical_card(c) for c in fresh if str(c['id']) in ledger.protected_fingerprints])
        self.management_save({'inventory': fresh, 'inventory_at': 0})
        return {'rolls': len(results), 'copy_id': cid, 'spent': ledger.spent, 'balance': balance, 'protected_unchanged': True, 'results': results}

    def reconcile_research_pending(self, confirmed_balance_after=None):
        stored = DataManager.load_json(RESEARCH_FILE)
        ledger = TestLedger.from_dict(stored.get('ledger', {}))
        pending = ledger.pending
        if not pending:
            return False
        _, root = self.management_page('/battle/reroll', '.battle-reroll')
        balance = int(re.sub(r'\D', '', root.select_one('[data-reroll-essence]').get_text()))
        receipt = (stored.get('pending_response') or {}).get('essence')
        charged_balance = int(re.sub(r'\s', '', str(receipt))) if receipt is not None else (balance if confirmed_balance_after is None else int(confirmed_balance_after))
        if charged_balance != pending['balance_before'] - pending['cost']:
            raise ValueError('Ambiguous research charge; refusing another roll')
        cid = int(pending['card_user_id'])
        response = self.management_post(root['data-reroll-card-url'], {'card_user_id': cid})
        working = canonical_card(dict(response['card'], card_user_id=cid))
        after = parse_roll_card(working)
        for key in pending.get('locked_stats', []):
            if after[key] != pending['before'][('attack', 'health', 'crit_chance').index(key)]:
                raise ValueError('Recovered roll changed locked stat')
        fresh = self.load_arena_inventory(root.select_one('.card-filter-form[data-url]'))
        ledger.assert_protected_unchanged([canonical_card(c) for c in fresh if str(c['id']) in ledger.protected_fingerprints])
        ledger.reconcile(charged_balance, resolved=True)
        ledger.observed_balance = balance
        samples = stored.get('samples', []) + [{'type': pending['type'], 'locked_stats': pending.get('locked_stats', []), 'card': working, 'cost': pending['cost'], 'recovered': True, 'at': time.time()}]
        DataManager.save_json(RESEARCH_FILE, {'ledger': ledger.to_dict(), 'samples': samples[-500:], 'pending_response': None})
        self.management_save({'inventory': fresh, 'inventory_at': time.time()})
        self.protect_arena_cards(fresh, self.management_state().get('best_ids', []), self.management_state().get('best_ids', []))
        self.log(f'🧪 Подтверждён ранее выполненный ролл #{cid}: {pending["cost"]} эссенции; повтор не отправлялся.')
        return True


# ==========================================
# ARENA AUTOMATION MIXIN
# ==========================================
def legal_replacement(current_ids, cards, banned_id):
    """Keep legal slots; replace banned ones, with unique copy and base card IDs."""
    known = {int(c['id']): c for c in cards if c.get('id') and c.get('card_id')}
    if len(current_ids) != 4 or any(i not in known for i in current_ids):
        return None
    chosen = [None] * 4
    used = set()
    base = set()
    for index, cid in enumerate(current_ids):
        c = known[cid]
        if int(c.get('battle_seal_id') or 0) == banned_id:
            continue
        if int(c.get('in_trade') or 0) or arena_score(c) <= 0:
            return None
        chosen[index] = cid
        used.add(cid)
        base.add(int(c['card_id']))
    eligible = sorted((c for c in known.values()
                     if int(c.get('battle_seal_id') or 0) != banned_id
                     and not int(c.get('in_trade') or 0) and arena_score(c) > 0),
                    key=lambda c: (arena_score(c), int(c['id'])), reverse=True)
    for index, cid in enumerate(chosen):
        if cid is not None:
            continue
        seals = Counter(int(known[i].get('battle_seal_id') or 0) for i in chosen if i is not None)
        candidate = next((c for c in eligible if int(c['id']) not in used and int(c['card_id']) not in base
                          and seals[int(c.get('battle_seal_id') or 0)] < MAX_SAME_SEAL), None)
        if candidate is None:
            return None
        cid = int(candidate['id'])
        chosen[index] = cid
        used.add(cid)
        base.add(int(candidate['card_id']))
    return chosen


class ArenaAutomationMixin(ArenaManagementMixin, AwakenRerollMixin, SealRerollMixin):
    def evaluate_arena_replacement(self, current, cards, banned, current_energy=None):
        battles = recent_battles(load_observations(os.path.join(BASE_DIR, 'arena_observations.json')))
        if battles and not supported_definitions(battles):
            self.log('⚠️ Арена: описания печатей изменились — модель отключена, подбор только по характеристикам.')
            return legal_replacement(current, cards, banned)
        rules = calibrate_rules(battles)
        deadline = time.monotonic() + 10
        result = choose_replacement(current, normalized_owned(cards, battles, current_energy), banned,
            opponent_teams(battles), rules=rules, samples=32,
            cancelled=lambda: self._module_cancelled() or time.monotonic() > deadline)
        if result is None:
            self.log('⚠️ Арена: расчёт состава недоступен или прерван — замена отложена.')
            return None
        ids, diagnostics = result
        self._arena_model_diagnostics = diagnostics
        self.log(f"🧮 Арена: подбор {diagnostics['method']}, соперников {diagnostics['opponent_count']}; "
                 'это оценка модели, не гарантированная вероятность победы.')
        if diagnostics.get('near_tie'):
            self.log('ℹ️ Арена: кандидаты имеют близкие модельные оценки; учтён запас HP состава.')
        return ids

    def _arena_get_soup(self, path):
        response = get_html(self.session, 'https://mangabuff.ru' + path, timeout=15)
        if response.status_code != 200 or is_anti_bot_page(response):
            return None
        soup = BeautifulSoup(response.text, 'html.parser')
        token = soup.select_one('meta[name="csrf-token"]')
        if token and token.get('content'):
            self.csrf_token = token['content']
        return soup

    def _arena_post(self, url, data):
        response = self.session.post(url, data=data, headers={
            'X-CSRF-TOKEN': self.csrf_token, 'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'application/json', 'Referer': 'https://mangabuff.ru/battle/decks'}, timeout=15)
        if response.status_code != 200:
            return False
        try:
            body = response.json()
            return isinstance(body, dict) and body.get('success') is not False and body.get('status') is not False
        except (ValueError, TypeError):
            return False

    def prepare_arena_deck(self):
        """Validate four cards and replace the week's prohibited seal before fighting."""
        if DataManager.get_setting('arena_auto_optimize', True):
            return self.prepare_best_arena_deck()
        soup = self._arena_get_soup('/battle')
        root = soup.select_one('.battle-home') if soup else None
        if root is None:
            self.log('⚠️ Арена: разметка недоступна — бои отложены.')
            return False
        ban = root.select_one('.battle-home__banned-seal strong')
        banned = seal_id(ban.get_text(' ', strip=True)) if ban else None
        fighters = root.select('.battle-home__fighter')
        if len(fighters) != 4 or (ban and banned is None):
            self.log('⚠️ Арена: не удалось проверить состав или запрет недели.')
            return False
        energies = []
        for fighter in fighters:
            energy = fighter.select_one('.battle-home__energy b')
            match = re.search(r'(\d+)\s*/\s*(\d+)', energy.get_text() if energy else '')
            if match is None:
                return False
            energies.append(int(match.group(1)))
        self._arena_low_energy = min(energies) < 10
        self._arena_start_url = same_site_route(root.get('data-battle-start-url'), '/battle/fight/start')
        prohibited = any(seal_id(f.select_one('.battle-home__seal').get_text(' ', strip=True)) == banned
                       for f in fighters if f.select_one('.battle-home__seal')) if banned else False
        if not prohibited:
            return True
        if not DataManager.get_setting('arena_auto_swap_banned', True):
            self.log('⚠️ Арена: в колоде запрещённая печать; автоматическая замена выключена.')
            return False
        decks_soup = self._arena_get_soup('/battle/decks')
        decks = decks_soup.select_one('.battle-decks') if decks_soup else None
        active = decks.select_one('.battle-decks__deck--active[data-deck-id]') if decks else None
        inventory = decks.select_one('.card-filter-form[data-url]') if decks else None
        if active is None or inventory is None:
            return False
        current = [int(i['data-card-user-id']) for i in active.select('img[data-card-user-id]')]
        url = same_site_route(inventory['data-url'], '/cards-filter/')
        cards = {}
        for sort in ('attack', 'health'):
            if not self._alive():
                return False
            response = self.session.post(url, data={'is_battle': 1, 'battle_sort': sort, 'per_page': 70, 'page': 1},
                headers={'X-CSRF-TOKEN': self.csrf_token, 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'}, timeout=15)
            if response.status_code != 200:
                return False
            try:
                rows = response.json().get('data')
                if not isinstance(rows, list):
                    return False
                cards.update({int(c['id']): c for c in rows if c.get('id')})
            except (ValueError, TypeError, AttributeError):
                return False
        if any(i not in cards for i in current):
            images = active.select('img[data-card-user-id][data-card-id]')
            if len(images) != 4:
                return False
            for image, fighter in zip(images, fighters):
                cid = int(image['data-card-user-id'])
                if cid in cards:
                    continue
                stats = fighter.select_one('.battle-home__fighter-stats')
                label = fighter.select_one('.battle-home__seal')
                text = stats.get_text(' ', strip=True) if stats else ''
                values = [re.search(r'\b' + key + r'\s+(\d+)', text) for key in ('ATK', 'HP', 'CR')]
                sid = seal_id(label.get_text()) if label else None
                if sid is None or not all(values):
                    return False
                cards[cid] = {'id': cid, 'card_id': int(image['data-card-id']), 'battle_seal_id': sid,
                    'battle_attack': int(values[0].group(1)), 'battle_health': int(values[1].group(1)),
                    'battle_crit_chance': int(values[2].group(1)), 'in_trade': 0}
        current_energy = {cid: energy for cid, energy in zip(current, energies)}
        replacement = self.evaluate_arena_replacement(current, list(cards.values()), banned, current_energy)
        if replacement is None:
            self.log('⚠️ Арена: не найден безопасный состав без запрещённой печати — бои отложены.')
            return False
        endpoint = same_site_route(decks.get('data-save-url'), '/battle/decks/save-cards')
        payload = {'deck_id': active['data-deck-id']}
        payload.update({f'card_user_ids[{i}]': cid for i, cid in enumerate(replacement)})
        if not self._arena_post(endpoint, payload):
            return False
        fresh = self._arena_get_soup('/battle/decks')
        stored = fresh.select_one('.battle-decks__deck--active') if fresh else None
        ids = [int(i['data-card-user-id']) for i in stored.select('img[data-card-user-id]')] if stored else []
        battle = self._arena_get_soup('/battle')
        fresh_root = battle.select_one('.battle-home') if battle else None
        safe_fighters = fresh_root.select('.battle-home__fighter') if fresh_root else []
        safe = len(safe_fighters) == 4 and all(f.select_one('.battle-home__seal') and
            seal_id(f.select_one('.battle-home__seal').get_text()) not in (None, banned) for f in safe_fighters)
        if ids != replacement or not safe:
            return False
        self.log(f'🃏 Арена: запрещённая печать заменена, колода #{active["data-deck-id"]} проверена.')
        self.notifier.send_message('🃏 Арена: заменил карту с запрещённой печатью и проверил состав колоды.')
        verified_energy = []
        for fighter in safe_fighters:
            node = fighter.select_one('.battle-home__energy b')
            match = re.search(r'(\d+)\s*/\s*(\d+)', node.get_text() if node else '')
            if match is None:
                return False
            verified_energy.append(int(match.group(1)))
        self._arena_low_energy = min(verified_energy) < 10
        return True

    def run_arena_cycle(self):
        self.claim_battle_chests()
        self.check_and_claim_quests()
        try:
            self.run_awaken_reroll()
        except Exception as e:  # never let it stop the fights
            self.log(f'⚠️ Перебор пробуждения остановлен на этом цикле: {e}')
        try:
            self.run_seal_reroll()
        except Exception as e:
            self.log(f'⚠️ Перековка печати остановлена на этом цикле: {e}')
        if not DataManager.get_setting('battles_enabled', True):
            return
        today = self._msk_now().date()
        rank_mode = DataManager.get_setting('arena_rank_mode', True)
        if getattr(self, '_arena_cap_date', None) == today and not rank_mode:
            return
        if not self.prepare_arena_deck():
            return
        if time.time() >= getattr(self, '_awakening_next_check_at', 0):
            self._awakening_next_check_at = time.time() + 6 * 3600
            if self.run_card_awakening(max_cards=3):
                if not self.prepare_arena_deck():
                    return
        if self._arena_low_energy:
            self.log('⏳ Арена: энергия карты ниже 10 — ждём восстановления.')
            self._arena_next_check_at = last_energy_reset() + 4 * 3600 + 120  # just after the next full reset
            return
        self.run_farm_loop(prepared=True)
        if (getattr(self, '_arena_min_energy', 100) or 0) < 10 or getattr(self, '_arena_buff_depleted', False):
            self._arena_next_check_at = time.time() + 60

    def run_evening_cup(self):
        now = self._msk_now()
        if not DataManager.get_setting('arena_cup_enabled', True):
            return False
        if not (19 <= now.hour < 22):
            self._cup_active = False
            return False
        pending = getattr(self, '_cup_active', False) or bool(DataManager.load_json(os.path.join(BASE_DIR, 'manual_arena_state.json')).get('active_fight'))
        completed = self.management_state().get('cup_completed_date') == now.date().isoformat()
        if completed and not pending:
            # Done for today: keep the scheduler from waking every few minutes until 22:00
            self._cup_retry_at = now.replace(hour=22, minute=0, second=0, microsecond=0).timestamp()
            return False
        if not pending and time.time() < getattr(self, '_cup_retry_at', 0):
            return False  # not "busy": the other modules keep running meanwhile
        if time.time() < getattr(self, '_cup_next_check_at', 0):
            return pending or True

        if not pending and not self.prepare_arena_deck():
            self._cup_retry_at = time.time() + 30 * 60  # no deck to play with yet
            return False
        result = run_cup_cycle(self, max_seconds=180, cancelled=self._module_cancelled,
            state_path=os.path.join(BASE_DIR, 'manual_arena_state.json'), allow_repeat_attempts=True)
        self._cup_active = result in ('budget', 'finished')
        self._cup_next_check_at = time.time() + (1 if self._cup_active else 300)
        if result in ('champion', 'daily_reward_claimed'):
            self.management_save({'cup_completed_date': now.date().isoformat(), 'inventory_at': 0})
        self.log('🏆 Вечерний кубок: ' + result)
        in_window = 19 <= now.hour < 22
        return self._cup_active or (in_window and result not in ('champion', 'daily_reward_claimed', 'closed'))
