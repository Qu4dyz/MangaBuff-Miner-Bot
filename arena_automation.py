"""Arena preflight and independent scheduling; no card purchases or rerolls."""
import math
import re
import time
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
from data_management import DataManager
from http_session import is_anti_bot_page
from utils import get_seconds_until_midnight_msk
from path import BASE_DIR
import os
from arena_observations import load_observations, recent_battles, opponent_teams, calibrate_rules, supported_definitions, normalized_owned
from battle_effectiveness import choose_replacement
from arena_management import ArenaManagementMixin
from arena_api import get_html

SEAL_STEMS = {1:'двойн',2:'ярост',3:'казн',4:'берсерк',5:'охот',6:'шип',
              7:'регенерац',8:'барьер',9:'бессмерт',10:'мест',11:'цепн',
              12:'вампир',13:'феникс',14:'уворот',15:'провокац'}

def seal_id(label):
    text=str(label or '').lower()
    return next((sid for sid,stem in SEAL_STEMS.items() if stem in text),None)

def arena_score(card):
    attack=float(card.get('battle_attack') or 0)
    health=float(card.get('battle_health') or 0)
    crit=max(0,min(100,float(card.get('battle_crit_chance') or 0)))
    return attack*(1+crit/100)*math.sqrt(max(0,health))

def legal_replacement(current_ids,cards,banned_id):
    """Keep legal slots; replace banned ones, with unique copy and base card IDs."""
    known={int(c['id']):c for c in cards if c.get('id') and c.get('card_id')}
    if len(current_ids)!=4 or any(i not in known for i in current_ids):
        return None
    chosen=[None]*4
    used=set()
    base=set()
    for index,cid in enumerate(current_ids):
        c=known[cid]
        if int(c.get('battle_seal_id') or 0)==banned_id:
            continue
        if int(c.get('in_trade') or 0) or arena_score(c)<=0:
            return None
        chosen[index]=cid;used.add(cid);base.add(int(c['card_id']))
    eligible=sorted((c for c in known.values()
                     if int(c.get('battle_seal_id') or 0)!=banned_id
                     and not int(c.get('in_trade') or 0) and arena_score(c)>0),
                    key=lambda c:(arena_score(c),int(c['id'])),reverse=True)
    for index,cid in enumerate(chosen):
        if cid is not None:continue
        candidate=next((c for c in eligible if int(c['id']) not in used and int(c['card_id']) not in base),None)
        if candidate is None:return None
        cid=int(candidate['id']);chosen[index]=cid;used.add(cid);base.add(int(candidate['card_id']))
    return chosen

def same_site_route(value,prefix):
    url=urljoin('https://mangabuff.ru/',str(value or ''))
    p=urlsplit(url)
    if p.scheme!='https' or p.netloc!='mangabuff.ru' or not p.path.startswith(prefix):
        raise ValueError('Unexpected arena route')
    return url

class ArenaAutomationMixin(ArenaManagementMixin):
    def evaluate_arena_replacement(self,current,cards,banned,current_energy=None):
        battles=recent_battles(load_observations(os.path.join(BASE_DIR,'arena_observations.json')))
        if battles and not supported_definitions(battles):
            self.log('⚠️ Арена: описания печатей изменились — модель отключена, подбор только по характеристикам.')
            return legal_replacement(current,cards,banned)
        rules=calibrate_rules(battles)
        deadline=time.monotonic()+10
        result=choose_replacement(current,normalized_owned(cards,battles,current_energy),banned,
            opponent_teams(battles),rules=rules,samples=32,
            cancelled=lambda:self._module_cancelled() or time.monotonic()>deadline)
        if result is None:
            self.log('⚠️ Арена: расчёт состава недоступен или прерван — замена отложена.')
            return None
        ids,diagnostics=result
        self._arena_model_diagnostics=diagnostics
        self.log(f"🧮 Арена: подбор {diagnostics['method']}, соперников {diagnostics['opponent_count']}; "
                 'это оценка модели, не гарантированная вероятность победы.')
        if diagnostics.get('near_tie'):
            self.log('ℹ️ Арена: кандидаты имеют близкие модельные оценки; учтён запас HP состава.')
        return ids

    def _arena_get_soup(self,path):
        response=get_html(self.session,'https://mangabuff.ru'+path,timeout=15)
        if response.status_code!=200 or is_anti_bot_page(response):return None
        soup=BeautifulSoup(response.text,'html.parser')
        token=soup.select_one('meta[name="csrf-token"]')
        if token and token.get('content'):self.csrf_token=token['content']
        return soup

    def _arena_post(self,url,data):
        response=self.session.post(url,data=data,headers={
            'X-CSRF-TOKEN':self.csrf_token,'X-Requested-With':'XMLHttpRequest',
            'Accept':'application/json','Referer':'https://mangabuff.ru/battle/decks'},timeout=15)
        if response.status_code!=200:return False
        try:
            body=response.json()
            return isinstance(body,dict) and body.get('success') is not False and body.get('status') is not False
        except (ValueError,TypeError):return False

    def prepare_arena_deck(self):
        """Validate four cards and replace the week's prohibited seal before fighting."""
        if DataManager.get_setting('arena_auto_optimize',True):
            return self.prepare_best_arena_deck()
        soup=self._arena_get_soup('/battle')
        root=soup.select_one('.battle-home') if soup else None
        if root is None:
            self.log('⚠️ Арена: разметка недоступна — бои отложены.')
            return False
        ban=root.select_one('.battle-home__banned-seal strong')
        banned=seal_id(ban.get_text(' ',strip=True)) if ban else None
        fighters=root.select('.battle-home__fighter')
        if len(fighters)!=4 or (ban and banned is None):
            self.log('⚠️ Арена: не удалось проверить состав или запрет недели.')
            return False
        energies=[]
        for fighter in fighters:
            energy=fighter.select_one('.battle-home__energy b')
            match=re.search(r'(\d+)\s*/\s*(\d+)',energy.get_text() if energy else '')
            if match is None:return False
            energies.append(int(match.group(1)))
        self._arena_low_energy=min(energies)<10
        self._arena_start_url=same_site_route(root.get('data-battle-start-url'),'/battle/fight/start')
        prohibited=any(seal_id(f.select_one('.battle-home__seal').get_text(' ',strip=True))==banned
                       for f in fighters if f.select_one('.battle-home__seal')) if banned else False
        if not prohibited:return True
        if not DataManager.get_setting('arena_auto_swap_banned',True):
            self.log('⚠️ Арена: в колоде запрещённая печать; автоматическая замена выключена.')
            return False
        decks_soup=self._arena_get_soup('/battle/decks')
        decks=decks_soup.select_one('.battle-decks') if decks_soup else None
        active=decks.select_one('.battle-decks__deck--active[data-deck-id]') if decks else None
        inventory=decks.select_one('.card-filter-form[data-url]') if decks else None
        if active is None or inventory is None:return False
        current=[int(i['data-card-user-id']) for i in active.select('img[data-card-user-id]')]
        url=same_site_route(inventory['data-url'],'/cards-filter/')
        cards={}
        # Candidate pool from both attack and HP ordering, plus the active deck.
        for sort in ('attack','health'):
            if not self._alive():return False
            response=self.session.post(url,data={'is_battle':1,'battle_sort':sort,'per_page':70,'page':1},
                headers={'X-CSRF-TOKEN':self.csrf_token,'X-Requested-With':'XMLHttpRequest','Accept':'application/json'},timeout=15)
            if response.status_code!=200:return False
            try:
                rows=response.json().get('data')
                if not isinstance(rows,list):return False
                cards.update({int(c['id']):c for c in rows if c.get('id')})
            except (ValueError,TypeError,AttributeError):return False
        # Active deck IDs/base IDs and stats are visible on the two pages. The
        # collection's deck_id filter is a different kind of deck, so don't use it.
        if any(i not in cards for i in current):
            images=active.select('img[data-card-user-id][data-card-id]')
            if len(images)!=4:return False
            for image,fighter in zip(images,fighters):
                cid=int(image['data-card-user-id'])
                if cid in cards:continue
                stats=fighter.select_one('.battle-home__fighter-stats')
                label=fighter.select_one('.battle-home__seal')
                text=stats.get_text(' ',strip=True) if stats else ''
                values=[re.search(r'\b'+key+r'\s+(\d+)',text) for key in ('ATK','HP','CR')]
                sid=seal_id(label.get_text()) if label else None
                if sid is None or not all(values):return False
                cards[cid]={'id':cid,'card_id':int(image['data-card-id']),'battle_seal_id':sid,
                    'battle_attack':int(values[0].group(1)),'battle_health':int(values[1].group(1)),
                    'battle_crit_chance':int(values[2].group(1)),'in_trade':0}
        current_energy={cid:energy for cid,energy in zip(current,energies)}
        replacement=self.evaluate_arena_replacement(current,list(cards.values()),banned,current_energy)
        if replacement is None:
            self.log('⚠️ Арена: не найден безопасный состав без запрещённой печати — бои отложены.')
            return False
        endpoint=same_site_route(decks.get('data-save-url'),'/battle/decks/save-cards')
        payload={'deck_id':active['data-deck-id']}
        payload.update({f'card_user_ids[{i}]':cid for i,cid in enumerate(replacement)})
        if not self._arena_post(endpoint,payload):return False
        # Re-read both stored IDs and battle labels: a 200 is not confirmation.
        fresh=self._arena_get_soup('/battle/decks')
        stored=fresh.select_one('.battle-decks__deck--active') if fresh else None
        ids=[int(i['data-card-user-id']) for i in stored.select('img[data-card-user-id]')] if stored else []
        battle=self._arena_get_soup('/battle')
        fresh_root=battle.select_one('.battle-home') if battle else None
        safe_fighters=fresh_root.select('.battle-home__fighter') if fresh_root else []
        safe=len(safe_fighters)==4 and all(f.select_one('.battle-home__seal') and
            seal_id(f.select_one('.battle-home__seal').get_text()) not in (None,banned) for f in safe_fighters)
        if ids!=replacement or not safe:return False
        self.log(f'🃏 Арена: запрещённая печать заменена, колода #{active["data-deck-id"]} проверена.')
        self.notifier.send_message('🃏 Арена: заменил карту с запрещённой печатью и проверил состав колоды.')
        verified_energy=[]
        for fighter in safe_fighters:
            node=fighter.select_one('.battle-home__energy b')
            match=re.search(r'(\d+)\s*/\s*(\d+)',node.get_text() if node else '')
            if match is None:return False
            verified_energy.append(int(match.group(1)))
        self._arena_low_energy=min(verified_energy)<10
        return True

    def run_arena_cycle(self):
        self.claim_battle_chests()
        self.check_and_claim_quests()
        if not DataManager.get_setting('battles_enabled',True):return
        today=self._msk_now().date()
        rank_mode=DataManager.get_setting('arena_rank_mode',True)
        if getattr(self,'_arena_cap_date',None)==today and not rank_mode:return
        if not self.prepare_arena_deck():return
        if time.time()>=getattr(self,'_awakening_next_check_at',0):
            self._awakening_next_check_at=time.time()+6*3600
            if self.run_card_awakening(max_cards=3):
                if not self.prepare_arena_deck():return
        if self._arena_low_energy:
            self.log('⏳ Арена: энергия карты ниже 10 — ждём восстановления.')
            self._arena_next_check_at=time.time()+4*3600
            return
        self.run_farm_loop(prepared=True)
        if (getattr(self,'_arena_min_energy',100) or 0)<10 or getattr(self,'_arena_buff_depleted',False):self._arena_next_check_at=time.time()+60

    def run_evening_cup(self):
        from manual_arena import run_cup_cycle
        from path import BASE_DIR
        import os
        now=self._msk_now()
        if not DataManager.get_setting('arena_cup_enabled',True):return False
        pending=getattr(self,'_cup_active',False) or bool(DataManager.load_json(os.path.join(BASE_DIR,'manual_arena_state.json')).get('active_fight'))
        in_window=19<=now.hour<22
        if not in_window and not pending:return False
        completed=self.management_state().get('cup_completed_date')==now.date().isoformat()
        if completed and not pending:return False
        if time.time()<getattr(self,'_cup_next_check_at',0):return pending or in_window
        if not pending and not self.prepare_arena_deck():return False
        result=run_cup_cycle(self,max_seconds=180,cancelled=self._module_cancelled,
            state_path=os.path.join(BASE_DIR,'manual_arena_state.json'),allow_repeat_attempts=True)
        self._cup_active=result in ('budget','finished')
        self._cup_next_check_at=time.time()+(1 if self._cup_active else 300)
        if result in ('champion','daily_reward_claimed'):
            self.management_save({'cup_completed_date':now.date().isoformat(),'inventory_at':0})
        self.log('🏆 Вечерний кубок: '+result)
        return self._cup_active or (in_window and result not in ('champion','daily_reward_claimed','closed'))
        if self._alive():
            self.check_and_claim_quests()
            self.claim_battle_chests()
        if getattr(self,'_arena_cap_date',None)==today and not rank_mode:
            self._arena_next_check_at=time.time()+get_seconds_until_midnight_msk()
