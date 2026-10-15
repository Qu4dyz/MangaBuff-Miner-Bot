"""Serialized Evening Cup client. Endpoints and grants come from server snapshots.

No mutation is retried on a transport error. A fresh GET must establish a new
revision before another action; secrets are deliberately disabled until their
server-side numerical rules are verified.
"""
import json
import math
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from arena_api import post_once,get_html

ORIGIN = 'https://mangabuff.ru'


def safe_url(value):
    url = urljoin(ORIGIN, str(value or ''))
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.netloc != 'mangabuff.ru' or not parsed.path.startswith('/battle/'):
        raise ValueError('Unexpected manual arena endpoint')
    return url


def choose_action(state, side, taunt_ids=(), hit_history=None):
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
        # Description says first attack per fight, not first attack per target.
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
        # Guaranteed base-damage kill takes precedence over probabilistic damage.
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
    return ('attack', {'from_card_user_id': int(attacker['card_user_id']), 'target_card_user_id': int(target['card_user_id'])})


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
        # Snapshots may omit immutable combat metadata already supplied by HTML.
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

    def fight(self, url, end, cancelled):
        response=get_html(self.bot.session,safe_url(url),timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        root = soup.select_one('[data-fight-id][data-manual-state-url]')
        if root is None:
            raise ValueError('Manual battle metadata missing')
        self.fight_id = str(root['data-fight-id'])
        self.active_fight = True
        self.save()
        self.state = None
        self.card_stats = {}
        self.hit_history = set()
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
                                               ('attack', 'crit_chance', 'max_hp', 'seal', 'seal_slug', 'seal_code')
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
        def finished():
            if self.path:
                history_path=self.path.with_name('manual_arena_history.json')
                try:history=json.loads(history_path.read_text(encoding='utf-8')).get('battles',[])
                except (OSError,ValueError):history=[]
                fields=('card_user_id','attack','max_hp','crit_chance','seal_code','buff_code','battle_card_energy')
                cards={s:[{k:self.card_stats.get(int(c['card_user_id']),{}).get(k,c.get(k)) for k in fields}
                          for c in self.state.get('final',{}).get(s,[])] for s in ('attacker','defender')}
                item={'id':self.fight_id,'observed_at':time.time(),'own_side':side,
                      'is_win':self.state.get('winner_user_id')==uid,'cards':cards,'source':'manual_opponent_cards'}
                history=[h for h in history if h.get('id')!=self.fight_id]+[item]
                temporary=history_path.with_suffix('.tmp')
                temporary.write_text(json.dumps({'battles':history[-40:]}),encoding='utf-8')
                temporary.replace(history_path)
            log('Кубок: бой завершён fight=' + self.fight_id + ' winner=' + str(self.state.get('winner_user_id')) +
                ' revision=' + str(self.state.get('revision')))
            return 'finished'
        while self.clock() < end and not cancelled():
            # An accepted fresh POST snapshot can grant another action immediately.
            # Otherwise GET recovers authoritative state before any mutation.
            if not fresh_post or self.clock() - self.received >= 2:
                data = self.unwrap(self.get(endpoints['state']))
                if not self.accept(data):
                    raise ValueError('Invalid manual state revision or fight identity')
            fresh_post = False
            if self.state.get('fight_status') == 'finished':
                return finished()
            revision = self.state['revision']
            blocked = self.pending and self.pending.get('fight_id') == self.fight_id and self.pending.get('revision') == revision
            if not blocked and int(self.state.get('current_turn_user_id', 0)) == uid and self.remaining() > 2:
                action = choose_action(self.state, side, taunts,
                                       self.hit_history if self.history_complete else None)
                if action:
                    kind, payload = action
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
                        # Includes409/timeouts: do not replay at this revision.
                        log('Кубок: действие ' + context + ' status=' + type(exc).__name__ + '; синхронизация')
                    continue
            self.sleep(min(2, max(0, end - self.clock())))
        return 'budget'

    def run(self, max_seconds=120, cancelled=None, allow_repeat_attempts=False):
        cancelled = cancelled or (lambda: False)
        end = self.clock() + max(0, max_seconds)
        response=get_html(self.bot.session,safe_url('/battle/tournament'),timeout=10)
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
            # The site offers unlimited new attempts after these terminal states.
            # Leave restarting to scheduler policy; never silently start over.
            if cup.get('attempt_status') == 'champion':
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
                # This is positive acknowledgement of registration, not an
                # ambiguous idle snapshot after a dropped POST response.
                self.registration = None
                self.save()
            if data.get('status') == 'matched':
                # A matching acknowledgement without its fight URL is not idle.
                self.sleep(min(2,max(0,end-self.clock())))
                continue
            if data.get('status') != 'searching' and not joined:
                if self.registration:
                    return 'registration_pending'
                prepare=getattr(self.bot,'prepare_arena_deck',None)
                if callable(prepare) and not prepare():return 'deck_unavailable'
                if self.clock()>=end:return 'budget'
                joined = True  # Never repeat ambiguous registration until next cycle.
                self.registration = {'signature': signature}
                self.save()
                try:
                    result = self.post(start_url, {}, getattr(self.bot,'csrf_token',None) or token_el['content'])
                    active = result.get('redirect_url') or (result.get('active_fight') or {}).get('url')
                    if active:
                        self.registration = None
                        self.save()
                        return self.fight(safe_url(active), end, cancelled)
                    if result.get('status') == 'searching':
                        self.registration = None
                        self.save()
                except Exception:
                    pass  # GET queue confirms whether registration succeeded.
            self.sleep(min(5, max(0, end - self.clock())))
        return 'budget'


def run_cup_cycle(bot, max_seconds=120, cancelled=None, state_path=None, allow_repeat_attempts=False):
    return ManualArenaController(bot, state_path=state_path).run(max_seconds, cancelled, allow_repeat_attempts)
