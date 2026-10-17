# -*- coding: utf-8 -*-
"""Measure how the arena really works from stored battle replays.

Every number the simulator uses for seals and damage started as a guess from
the in-game descriptions. Replays log each hit with its damage, crit flag,
target HP (in percent) and the side effects (heals, shields, reflected
damage, extra attacks), so most of those numbers can be measured directly.

measure(battles) -> {param: Estimate}; learned_rules(battles) returns only the
parameters with enough samples, ready to merge into the simulator rules.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field

# Simulator defaults (arena.simulate) — the guesses being checked
DEFAULTS = {
    "crit_multiplier": 2.0,
    "jitter": 0.10,
    "dodge_chance": 0.20,
    "barrier_block": 0.70,        # share of the first hit absorbed
    "regeneration_heal": 0.07,    # of max HP per own attack
    "vampirism_heal": 0.25,       # of damage dealt
    "thorns_reflect": 0.30,       # of damage taken
    "double_strike_chance": 0.30,
    "chain_attack_chance": 0.25,
    "rage_multiplier": 1.30,
    "execution_multiplier": 1.35,
}

# Minimum samples before a measurement may replace a default
MIN_SAMPLES = {
    "crit_multiplier": 20, "jitter": 40, "dodge_chance": 40, "barrier_block": 10,
    "regeneration_heal": 30, "vampirism_heal": 30, "thorns_reflect": 15,
    "double_strike_chance": 60, "chain_attack_chance": 60,
    "rage_multiplier": 15, "execution_multiplier": 15,
}

# Seals that change outgoing damage; ordinary hits exclude them
DAMAGE_SEALS = {"rage", "execution", "berserk", "berserker", "hunter"}


@dataclass
class Estimate:
    value: float | None
    samples: int
    low: float | None = None   # ~95% interval
    high: float | None = None
    note: str = ""
    default: float | None = None

    def usable(self, name):
        return self.value is not None and self.samples >= MIN_SAMPLES.get(name, 30)

    def as_dict(self):
        return {"value": self.value, "samples": self.samples, "low": self.low, "high": self.high,
                "default": self.default, "note": self.note}


def _wilson(successes, n, z=1.96):
    if n <= 0:
        return None, None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _ratio_estimate(values, default, note=""):
    values = [v for v in values if v is not None and math.isfinite(v)]
    if not values:
        return Estimate(None, 0, default=default, note=note)
    med = statistics.median(values)
    if len(values) >= 4:
        qs = statistics.quantiles(values, n=40)
        low, high = qs[0], qs[-1]
    else:
        low, high = min(values), max(values)
    return Estimate(round(med, 4), len(values), round(low, 4), round(high, 4), note, default)


def _rate_estimate(successes, n, default, note=""):
    if n <= 0:
        return Estimate(None, 0, default=default, note=note)
    low, high = _wilson(successes, n)
    return Estimate(round(successes / n, 4), n, round(low, 4), round(high, 4), note, default)


def _cards(battle):
    return {c.get("card_user_id"): c for side in (battle.get("cards") or {}).values() for c in side or []}


def _num(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def measure(battles):
    """Estimates for every simulator parameter that the replays can show."""
    ordinary, crits = [], []
    dodge_hits = dodge_misses = 0
    barrier = []
    regen = []
    vamp = []
    thorns = []
    extra = Counter()
    attacks_by_seal = Counter()
    rage, execution = [], []

    for battle in battles:
        cards = _cards(battle)
        hp_pct = {cid: 100.0 for cid in cards}
        first_hit_taken = set()
        acted = set()
        for step in battle.get("steps") or []:
            if step.get("type") != "attack":
                continue
            actor = cards.get(step.get("from_card_user_id"))
            target = cards.get(step.get("to_card_user_id"))
            if not actor or not target:
                continue
            aid, tid = step.get("from_card_user_id"), step.get("to_card_user_id")
            attack = _num(actor.get("attack"))
            damage = _num(step.get("damage"))
            dealt = _num(step.get("dealt_damage"))
            seal = actor.get("seal_code")
            tseal = target.get("seal_code")
            first_attack = aid not in acted
            acted.add(aid)
            attacks_by_seal[seal] += 1
            before = _num(step.get("hp_before"))
            if before is not None:
                hp_pct[tid] = before

            # Dodge: a dodging card takes 0 from a direct hit
            # (the server logs a dodged hit as damage 0, dealt 0)
            if tseal == "dodge" and dealt is not None:
                if dealt == 0:
                    dodge_misses += 1
                else:
                    dodge_hits += 1

            # Barrier: shield effect on the target's first hit
            for eff in step.get("effects") or []:
                owner = cards.get(eff.get("card_user_id"))
                amount = _num(eff.get("amount"))
                if amount is None or not owner:
                    continue
                kind = eff.get("type")
                # "damage" is what got through the shield, so the full hit is amount + damage
                if kind == "shield" and owner.get("seal_code") == "barrier" and damage is not None:
                    barrier.append(amount / (amount + damage))
                elif kind == "heal" and owner.get("seal_code") == "regeneration" and eff.get("card_user_id") == aid:
                    hp_b, hp_a = _num(eff.get("hp_before")), _num(eff.get("hp_after"))
                    # Only heals that were not capped at full HP measure the real amount
                    if amount > 0 and hp_a is not None and hp_a < 100:
                        regen.append(amount / _num(owner.get("max_hp")))
                elif kind == "heal" and owner.get("seal_code") == "vampirism" and eff.get("card_user_id") == aid and dealt:
                    hp_a = _num(eff.get("hp_after"))
                    if amount > 0 and hp_a is not None and hp_a < 100:
                        vamp.append(amount / dealt)
                elif kind == "damage" and tseal == "thorns" and eff.get("card_user_id") == aid and dealt:
                    thorns.append(amount / dealt)

            if tseal == "barrier":
                first_hit_taken.add(tid)

            if seal in ("double_strike", "chain_attack"):
                if step.get("extra_attacks"):
                    extra[seal] += 1

            if not attack or damage is None or damage <= 0:
                continue
            ratio = damage / attack
            shielded = any(e.get("type") == "shield" for e in step.get("effects") or [])
            if shielded:
                continue
            target_low = before is not None and before < 35
            if seal == "rage":
                own_hp = _num(step.get("attacker_hp_before"))
                if own_hp is not None and own_hp < 50 and not step.get("is_crit"):
                    rage.append(ratio)
                continue
            if seal == "execution":
                if target_low and not step.get("is_crit"):
                    execution.append(ratio)
                continue
            if seal in DAMAGE_SEALS:
                continue
            if actor.get("buff_code") in ("executioner", "mad_charge"):
                continue
            (crits if step.get("is_crit") else ordinary).append(ratio)

    est = {}
    est["jitter"] = _ratio_estimate([abs(r - 1) for r in ordinary], DEFAULTS["jitter"],
                                    "spread of ordinary hits around attack (median |ratio-1|)")
    if ordinary:
        spread = max(abs(min(ordinary) - 1), abs(max(ordinary) - 1))
        est["jitter"].note += f"; observed range {min(ordinary):.3f}..{max(ordinary):.3f}"
        est["jitter_range"] = Estimate(round(spread, 4), len(ordinary), default=DEFAULTS["jitter"],
                                       note="half-width of the ordinary-hit range")
    base = statistics.median(ordinary) if len(ordinary) >= 5 else 1.0
    est["crit_multiplier"] = _ratio_estimate([c / base for c in crits], DEFAULTS["crit_multiplier"],
                                             "crit damage / ordinary damage")
    est["dodge_chance"] = _rate_estimate(dodge_misses, dodge_hits + dodge_misses, DEFAULTS["dodge_chance"],
                                         "share of direct hits a dodge card took for 0")
    est["barrier_block"] = _ratio_estimate(barrier, DEFAULTS["barrier_block"], "share of the first hit absorbed")
    est["regeneration_heal"] = _ratio_estimate(regen, DEFAULTS["regeneration_heal"], "heal / max HP, uncapped heals")
    est["vampirism_heal"] = _ratio_estimate(vamp, DEFAULTS["vampirism_heal"], "heal / damage dealt, uncapped heals")
    est["thorns_reflect"] = _ratio_estimate(thorns, DEFAULTS["thorns_reflect"], "reflected / damage dealt to thorns")
    est["double_strike_chance"] = _rate_estimate(extra["double_strike"], attacks_by_seal["double_strike"],
                                                 DEFAULTS["double_strike_chance"], "extra attack per attack")
    est["chain_attack_chance"] = _rate_estimate(extra["chain_attack"], attacks_by_seal["chain_attack"],
                                                DEFAULTS["chain_attack_chance"], "extra attack per attack")
    est["rage_multiplier"] = _ratio_estimate([r / base for r in rage], DEFAULTS["rage_multiplier"],
                                             "damage below 50% own HP / ordinary")
    est["execution_multiplier"] = _ratio_estimate([r / base for r in execution], DEFAULTS["execution_multiplier"],
                                                  "damage on targets below 35% / ordinary")
    return est


def measure_targeting(battles):
    """How the server picks targets: uniform random, lowest HP, or highest attack?"""
    picks = Counter()
    total = 0
    expected_lowest = 0.0
    for battle in battles:
        cards = _cards(battle)
        sides = {side: [c.get("card_user_id") for c in team or []] for side, team in (battle.get("cards") or {}).items()}
        hp = {cid: 100.0 for cid in cards}
        for step in battle.get("steps") or []:
            if step.get("type") != "attack":
                continue
            to_side = step.get("to_side")
            tid = step.get("to_card_user_id")
            alive = [cid for cid in sides.get(to_side, []) if hp.get(cid, 0) > 0]
            if tid in alive and len(alive) > 1:
                taunts = [cid for cid in alive if (cards.get(cid) or {}).get("seal_code") == "taunt"]
                pool = taunts or alive
                if tid in pool and len(pool) > 1:
                    total += 1
                    lowest = min(pool, key=lambda c: hp.get(c, 100))
                    strongest = max(pool, key=lambda c: _num((cards.get(c) or {}).get("attack")) or 0)
                    first = pool[0]
                    picks["lowest_hp"] += tid == lowest
                    picks["highest_attack"] += tid == strongest
                    picks["first_in_line"] += tid == first
                    expected_lowest += 1 / len(pool)
            after = _num(step.get("hp_after"))
            if after is not None and tid in hp:
                hp[tid] = after
            for extra in step.get("extra_attacks") or []:
                if extra.get("to_card_user_id") in hp and _num(extra.get("hp_after")) is not None:
                    hp[extra["to_card_user_id"]] = _num(extra["hp_after"])
            for eff in step.get("effects") or []:
                if eff.get("card_user_id") in hp and _num(eff.get("hp_after")) is not None:
                    hp[eff["card_user_id"]] = _num(eff["hp_after"])
    return {
        "decisions": total,
        "if_uniform_random": round(expected_lowest / total, 3) if total else None,
        **({k: round(v / total, 3) for k, v in picks.items()} if total else {}),
    }


def learned_rules(battles):
    """Simulator rule overrides backed by enough samples (others stay at defaults)."""
    est = measure(battles)
    rules = {}
    for name, e in est.items():
        if name in DEFAULTS and e.usable(name):
            rules[name] = e.value
    if "jitter_range" in est and est["jitter_range"].samples >= MIN_SAMPLES["jitter"]:
        rules["jitter"] = min(0.3, est["jitter_range"].value)
    return rules


def report(battles):
    """Plain-text summary for logs and Telegram."""
    est = measure(battles)
    lines = [f"Боёв в выборке: {len(battles)}"]
    for name, e in est.items():
        if name not in DEFAULTS:
            continue
        mark = "✅" if e.usable(name) else "…"
        val = "—" if e.value is None else f"{e.value:g}"
        ci = "" if e.low is None else f" [{e.low:g}…{e.high:g}]"
        lines.append(f"{mark} {name}: {val}{ci} (n={e.samples}, было {e.default:g})")
    t = measure_targeting(battles)
    if t.get("decisions"):
        lines.append(f"Цели: решений {t['decisions']}, самая раненая {t.get('lowest_hp')} "
                     f"(при случайном выборе было бы {t['if_uniform_random']}), "
                     f"самая сильная {t.get('highest_attack')}, первая в ряду {t.get('first_in_line')}")
    return "\n".join(lines)
