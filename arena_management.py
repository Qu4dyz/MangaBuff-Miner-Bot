"""Whole-deck management, protection and bounded card awakening.

Essence research is a separate explicit operation; the daemon never rerolls
protected cards or spends essence automatically without a saved allowance.
"""
import os,re,time,json
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from path import BASE_DIR
from data_management import DataManager
from http_session import is_anti_bot_page
from arena_api import post_once
from arena_optimizer import optimize_deck,protected_card_ids
from arena_observations import load_observations,recent_battles,opponent_teams,calibrate_rules,supported_definitions,normalized_owned
from battle_effectiveness import SEALS,from_inventory,evaluate_buffs
from card_progression_policy import parse_roll_card,choose_test_card,TestLedger,plan_reroll,fingerprint

MANAGEMENT_FILE=os.path.join(BASE_DIR,'arena_management.json')
RESEARCH_FILE=os.path.join(BASE_DIR,'arena_research.json')
BUFF_NAMES={'Кровавая ярость':'blood_rage','Глаз хищника':'predator_eye','Безумный натиск':'mad_charge',
 'Жажда крови':'blood_thirst','Железная воля':'iron_will','Второе дыхание':'second_breath',
 'Стеклянная пушка':'glass_cannon','Палач':'executioner'}

def owned_buff_options(root):
    options={};active=None
    for item in root.select('[data-battle-buff-item]'):
        name=item.select_one('.battle-home__buff-info strong')
        inp=item.select_one('input[name="battle_buff_id"]')
        count=item.select_one('.battle-home__buff-count')
        code=BUFF_NAMES.get(name.get_text(strip=True)) if name else None
        if code is None or inp is None or count is None:continue
        value=int(re.sub(r'\D','',count.get_text()) or 0)
        if value<=0 or inp.has_attr('disabled'):continue
        options[code]={'id':inp['value'],'count':value}
        if 'battle-home__buff--active' in item.get('class',[]):active=code
    return options,active

def canonical_card(card):
    result=dict(card)
    if isinstance(result.get('seal'),dict):
        from arena_automation import seal_id
        sid=seal_id(result['seal'].get('name',''))
        result['seal_code']=result['seal'].get('code') or SEALS.get(sid)
        if sid:result['battle_seal_id']=sid
    elif result.get('battle_seal_id'):
        result['seal_code']=SEALS.get(int(result['battle_seal_id']))
    return result

def inventory_from_api_card(card,original):
    parsed=parse_roll_card(canonical_card(card))
    if parsed.get('seal_code') not in SEALS.values():raise ValueError('Unknown replacement seal')
    result=dict(original)
    result.update(battle_attack=parsed['attack'],battle_health=parsed['health'],
                  battle_crit_chance=parsed['crit_chance'],potential_rarity=parsed.get('potential_rarity'))
    if parsed.get('seal_code') in SEALS.values():result['battle_seal_id']=next(k for k,v in SEALS.items() if v==parsed['seal_code'])
    return result

class ArenaManagementMixin:
    def management_state(self):return DataManager.load_json(MANAGEMENT_FILE)
    def management_save(self,values):
        DataManager.save_json(MANAGEMENT_FILE,values)
        stored=self.management_state()
        if any(stored.get(k)!=v for k,v in values.items()):raise OSError('Arena state not persisted')

    def management_page(self,path,selector):
        soup=self._arena_get_soup(path)
        root=soup.select_one(selector) if soup else None
        if root is None:raise ValueError('Required arena page unavailable: '+path)
        return soup,root

    def management_post(self,url,data,mutation=False):
        from arena_automation import same_site_route
        url=same_site_route(url,'/')
        headers={'X-CSRF-TOKEN':self.csrf_token,'X-Requested-With':'XMLHttpRequest','Accept':'application/json','Referer':'https://mangabuff.ru/battle'}
        send=post_once if mutation else lambda s,u,**kw:s.post(u,**kw)
        response=send(self.session,url,data=data,headers=headers,timeout=15)
        if response.status_code!=200 or is_anti_bot_page(response):
            detail=''
            if response.status_code==422:
                try:detail=': '+str(response.json().get('message',''))[:180]
                except (ValueError,AttributeError):pass
            raise ValueError('Arena API HTTP '+str(response.status_code)+detail)
        body=response.json()
        if not isinstance(body,dict) or body.get('success') is False or body.get('status') is False:raise ValueError('Arena API rejected operation')
        return body

    def load_arena_inventory(self,form,awake=1,max_pages=30):
        url=urljoin('https://mangabuff.ru',form['data-url'])
        rows=[];seen=set()
        for page in range(1,max_pages+1):
            if not self._alive():raise RuntimeError('module aborted')
            data=self.management_post(url,{'is_battle':awake,'per_page':70,'page':page})
            batch=data.get('data')
            if not isinstance(batch,list):raise ValueError('Inventory shape changed')
            ids=tuple(int(c['id']) for c in batch)
            if ids and ids in seen:raise ValueError('Repeated inventory page')
            seen.add(ids);rows.extend(batch)
            if not data.get('next_page_url'):return rows
            if page==max_pages:raise ValueError('Inventory incomplete; refuse optimizing partial scan')
        return rows

    def protect_arena_cards(self,cards,current,best):
        state=self.management_state()
        protected=protected_card_ids(cards,current,best,bench_size=24,per_seal=3)
        recommended=sorted(protected)
        # Never silently unlock an earlier protected asset when meta changes.
        protected.update(int(i) for i in state.get('protected_ids',[]))
        self.management_save({'protected_ids':sorted(protected),'recommended_protected_ids':recommended,
                              'legacy_protected_ids':sorted(protected-set(recommended))})
        byid={int(c['id']):c for c in cards}
        unlocked=[cid for cid in protected if cid in byid and not int(byid[cid].get('is_lock') or 0)]
        for start in range(0,len(unlocked),20):
            if not self._alive():raise RuntimeError('module aborted')
            ids=unlocked[start:start+20]
            data={'is_lock':1};data.update({f'card_ids[{i}]':cid for i,cid in enumerate(ids)})
            self.management_post('https://mangabuff.ru/trades/lockCards',data,mutation=True)
            for cid in ids:byid[cid]['is_lock']=1
        if unlocked:
            _,root=self.management_page('/battle/decks','.battle-decks')
            fresh=self.load_arena_inventory(root.select_one('.card-filter-form[data-url]'))
            confirmed={int(c['id']) for c in fresh if int(c.get('is_lock') or 0)}
            if not set(unlocked)<=confirmed:raise ValueError('Card protection not confirmed by server')
            self.management_save({'inventory':cards})
            self.management_save({'auto_locked_ids':sorted(set(state.get('auto_locked_ids',[]))|set(unlocked))})
            self.log(f'🔒 Арена: закрыто {len(unlocked)} карт; актуальный боевой резерв {len(recommended)}, всего сохранённых замков {len(protected)}.')
        return protected

    def refresh_arena_meta(self,banned):
        state=self.management_state()
        if time.time()-float(state.get('meta_at',0))<6*3600 and state.get('meta_banned')==banned:return state.get('meta_teams',[])
        soup=self._arena_get_soup('/battle/leaderboard')
        if soup is None:return state.get('meta_teams',[])
        leaders=[]
        for el in soup.select('.battle-leaderboard__row[href]'):
            m=re.search(r'/users/(\d+)',el['href'])
            if m and m[1] not in leaders:leaders.append(m[1])
        teams=[]
        from battle_effectiveness import heuristic
        for uid in leaders[:3]:
            if not self._alive():break
            try:
                # Public filter route was checked live. It exposes owned cards,
                # not the player's current active deck: label derived teams clearly.
                page=self._arena_get_soup('/users/'+uid+'/cards')
                if page is None:continue
                data=self.management_post('https://mangabuff.ru/cards-filter/'+uid,{'is_battle':1,'battle_sort':'attack','per_page':12,'page':1})
                pool=[from_inventory(c) for c in data.get('data',[])]
                pool=[c for c in pool if c and c['seal_id']!=banned]
                pool.sort(key=heuristic,reverse=True)
                selected=[];bases=set()
                for c in pool:
                    if c['base_id'] in bases:continue
                    bases.add(c['base_id']);selected.append(c)
                    if len(selected)==4:break
                if len(selected)==4:teams.append({'cards':selected,'source':'derived_from_public_leader_cards','user_id':uid})
            except Exception as exc:
                self.log('ℹ️ Арена: публичный мета-профиль недоступен ('+type(exc).__name__+').')
        self.management_save({'meta_at':time.time(),'meta_teams':teams,'meta_leaders':leaders[:10],'meta_banned':banned})
        return teams

    def prepare_best_arena_deck(self):
        from arena_automation import seal_id,same_site_route
        soup,home=self.management_page('/battle','.battle-home')
        ban=home.select_one('.battle-home__banned-seal strong');banned=seal_id(ban.get_text()) if ban else None
        fighters=home.select('.battle-home__fighter')
        if len(fighters)!=4 or (ban and banned is None):raise ValueError('Unknown arena composition/ban')
        deck_soup,decks=self.management_page('/battle/decks','.battle-decks')
        active=decks.select_one('.battle-decks__deck--active[data-deck-id]')
        form=decks.select_one('.card-filter-form[data-url]')
        if active is None or form is None:raise ValueError('Active deck missing')
        current=[int(i['data-card-user-id']) for i in active.select('img[data-card-user-id]')]
        if len(current)!=4:raise ValueError('Incomplete active deck')
        energy={}
        for cid,f in zip(current,fighters):
            el=f.select_one('.battle-home__energy b');m=re.search(r'(\d+)\s*/\s*(\d+)',el.get_text() if el else '')
            if m is None:raise ValueError('Unknown fighter energy')
            energy[cid]=int(m[1])
        state=self.management_state()
        # Recently verified low energy on a reserve remains a hard exclusion;
        # expiry only makes it unknown and it is verified after installation.
        for cid,observation in state.get('energy_observations',{}).items():
            if int(cid) not in energy and time.time()-float(observation.get('at',0))<1800:
                energy[int(cid)]=int(observation['value'])
        cards=state.get('inventory',[])
        # Manual awakening/trades can add strong copies between scheduler cycles.
        # Every optimization uses a full fresh scan, including every page.
        cards=self.load_arena_inventory(form)
        self.management_save({'inventory':cards,'inventory_at':time.time()})
        protected=self.protect_arena_cards(cards,current,current)
        battles=recent_battles(load_observations(os.path.join(BASE_DIR,'arena_observations.json')))
        result=None
        buff_code=None;buff_ranking=[]
        buff_options,active_buff=owned_buff_options(home)
        if supported_definitions(battles):
            meta=self.refresh_arena_meta(banned)
            manual=DataManager.load_json(os.path.join(BASE_DIR,'manual_arena_history.json')).get('battles',[])
            manual_teams=[]
            for observed in manual[-2:]:
                if time.time()-float(observed.get('observed_at',0))>7*86400:continue
                side='defender' if observed.get('own_side')=='attacker' else 'attacker'
                rows=observed.get('cards',{}).get(side,[])
                if len(rows)==4:manual_teams.append({'cards':rows,'source':'manual_opponent_cards'})
            # Card snapshots from Cup are an additional opponent pool; Cup
            # outcomes are never used to calibrate automatic battle rules.
            opponents=opponent_teams(battles,limit=3)+manual_teams+meta[:2]
            def currently_legal_opponent(entry):
                rows=entry.get('cards',[]) if isinstance(entry,dict) else entry
                return not any(c.get('seal_id')==banned or c.get('seal_code',c.get('seal'))==SEALS.get(banned) for c in rows)
            opponents=[entry for entry in opponents if currently_legal_opponent(entry)]
            normalized=normalized_owned(cards,battles,energy)
            known={int(c['id']):c for c in normalized}
            rules=calibrate_rules(battles)
            buff_ranking=evaluate_buffs([known[i] for i in current],opponents,buff_options,rules,samples=24,cancelled=self._module_cancelled)
            if buff_ranking:buff_code=buff_ranking[0]['code']
            result=optimize_deck(current,normalized,banned,opponents,
                rules=dict(rules,own_buff=buff_code),energy_map=energy,cancelled=self._module_cancelled,budget_seconds=12)
            if result:
                ranking=evaluate_buffs([known[i] for i in result[0]],opponents,buff_options,rules,samples=24,cancelled=self._module_cancelled)
                if ranking and ranking[0]['code']!=buff_code:
                    buff_code=ranking[0]['code'];buff_ranking=ranking
                    result=optimize_deck(current,normalized,banned,opponents,
                        rules=dict(rules,own_buff=buff_code),energy_map=energy,cancelled=self._module_cancelled,budget_seconds=12)
        if result is None:
            # Existing legal deck can keep farming while the model awaits evidence.
            byid={int(c['id']):c for c in cards}
            if any(cid not in byid or int(byid[cid].get('battle_seal_id') or 0)==banned for cid in current):return False
            chosen=current;diagnostics={'reason':'no_valid_model_keep_current'}
        else:chosen,diagnostics=result
        diagnostics['buff_ranking']=buff_ranking
        diagnostics['selected_buff']=buff_code
        self.management_save({'best_ids':chosen,'optimizer':diagnostics,'optimized_at':time.time(),'banned_seal':banned,'inventory':cards})
        self.protect_arena_cards(cards,current,chosen)
        if chosen!=current:
            endpoint=same_site_route(decks.get('data-save-url'),'/battle/decks/save-cards')
            data={'deck_id':active['data-deck-id']};data.update({f'card_user_ids[{i}]':cid for i,cid in enumerate(chosen)})
            self.management_post(endpoint,data,mutation=True)
            fresh,roots=self.management_page('/battle/decks','.battle-decks')
            stored=roots.select_one('.battle-decks__deck--active')
            ids=[int(i['data-card-user-id']) for i in stored.select('img[data-card-user-id]')] if stored else []
            if ids!=chosen:raise ValueError('Deck save not confirmed')
            self.log('🧮 Арена: установлен лучший найденный состав с учётом синергий и наблюдавшихся соперников.')
        if result and buff_ranking and buff_code!=active_buff:
            endpoint=same_site_route(home.get('data-battle-buff-select-url'),'/battle/buff/select')
            self.management_post(endpoint,{'battle_buff_id':buff_options[buff_code]['id'] if buff_code else ''},mutation=True)
            _,confirmed=self.management_page('/battle','.battle-home')
            _,confirmed_code=owned_buff_options(confirmed)
            if confirmed_code!=buff_code:raise ValueError('Buff selection not confirmed')
            self.log('✦ Арена: выбран лучший найденный доступный баф '+str(buff_code)+'.')
        self._arena_buff_remaining=buff_options[buff_code]['count'] if buff_code else None
        self._arena_buff_depleted=False
        fresh,home=self.management_page('/battle','.battle-home')
        check=home.select('.battle-home__fighter')
        if len(check)!=4 or any(seal_id(f.select_one('.battle-home__seal').get_text())==banned for f in check):return False
        energies=[]
        for f in check:
            el=f.select_one('.battle-home__energy b');m=re.search(r'(\d+)\s*/\s*(\d+)',el.get_text() if el else '')
            if m is None:return False
            energies.append(int(m[1]))
        self._arena_low_energy=min(energies)<10
        self._arena_min_energy=min(energies)
        energy_observations=dict(state.get('energy_observations',{}))
        for cid,value in zip(chosen,energies):energy_observations[str(cid)]={'value':value,'at':time.time()}
        self.management_save({'energy_observations':energy_observations})
        self._arena_start_url=same_site_route(home.get('data-battle-start-url'),'/battle/fight/start')
        self._arena_inventory=cards
        self._arena_protected_ids=protected
        return True

    def run_card_awakening(self,max_cards=3):
        pending=self.management_state().get('awakening_pending')
        if pending:
            _,decks=self.management_page('/battle/decks','.battle-decks')
            fresh=self.load_arena_inventory(decks.select_one('.card-filter-form[data-url]'))
            if not any(int(c['id'])==int(pending['id']) for c in fresh):
                self.log('⚠️ Неподтверждённое пробуждение: повтор не отправляется.')
                return 0
            best=self.management_state().get('best_ids',[])
            self.protect_arena_cards(fresh,best,best)
            self._arena_inventory=fresh
            self.management_save({'inventory':fresh,'inventory_at':time.time(),'awakening_pending':None})
            self.log('✦ Ранее выполненное пробуждение подтверждено по коллекции.')
        if not DataManager.get_setting('arena_auto_awaken',True):return 0
        soup,root=self.management_page('/battle/awakening','.battle-awakening')
        def number(selector):
            el=root.select_one(selector)
            if el is None:raise ValueError('Unknown awakening balance')
            return int(re.sub(r'\D','',el.get_text()))
        free=number('[data-awakening-free-left]');energy=number('[data-awakening-energy]')
        count=min(max_cards,free+energy)
        if count<=0:return 0
        form=root.select_one('.card-filter-form[data-url]')
        # Reading just the first candidate page is sufficient; never claim a full unawake scan.
        data=self.management_post(urljoin('https://mangabuff.ru',form['data-url']),{'is_battle':-1,'per_page':70,'page':1})
        candidates=[c for c in data.get('data',[]) if not int(c.get('in_trade') or 0)]
        # No evidence of rank-specific stat ceilings: diversify base identities.
        existing={int(c['card_id']) for c in getattr(self,'_arena_inventory',[])}
        candidates.sort(key=lambda c:(int(c['card_id']) in existing,int(c.get('copy_number') or 1000000)<1000,int(c['id'])))
        done=0
        for original in candidates:
            if done>=count or not self._alive():break
            state=self.management_state()
            if state.get('awakening_pending'):break
            cid=int(original['id'])
            self.management_save({'awakening_pending':{'id':cid,'at':time.time()}})
            try:
                response=self.management_post(root['data-awakening-url'],{'card_user_id':cid},mutation=True)
                card=inventory_from_api_card(response.get('card') or response,original)
            except Exception:
                # An ambiguous mutation is never repeated automatically.
                self.log('⚠️ Пробуждение: результат не подтверждён; сохранено ожидание для сверки.')
                break
            done+=1
            cards=list(getattr(self,'_arena_inventory',[]));cards=[c for c in cards if int(c['id'])!=cid]+[card]
            self._arena_inventory=cards
            current=state.get('best_ids',[])
            self.management_save({'inventory':cards,'inventory_at':0,'awakening_pending':None})
            # Read authoritative inventory/token before locking the new copy.
            _,decks=self.management_page('/battle/decks','.battle-decks')
            cards=self.load_arena_inventory(decks.select_one('.card-filter-form[data-url]'))
            self.protect_arena_cards(cards,current,current)
            self._arena_inventory=cards
            self.log(f'✦ Пробуждена карта #{cid}: ATK {card["battle_attack"]}, HP {card["battle_health"]}, CR {card["battle_crit_chance"]}%.')
        return done

    def run_reroll_research(self,max_rolls=11,budget=None,plans=None):
        """Explicit research only. Never called by the ordinary daemon scheduler."""
        soup,root=self.management_page('/battle/reroll','.battle-reroll')
        balance_el=root.select_one('[data-reroll-essence]')
        if balance_el is None:raise ValueError('Unknown essence balance')
        balance=int(re.sub(r'\D','',balance_el.get_text()))
        _,decks=self.management_page('/battle/decks','.battle-decks')
        active=decks.select_one('.battle-decks__deck--active')
        current=[int(i['data-card-user-id']) for i in active.select('img[data-card-user-id]')] if active else []
        if len(current)!=4:raise ValueError('Confirm current deck before research')
        cards=self.load_arena_inventory(decks.select_one('.card-filter-form[data-url]'))
        state=self.management_state()
        protected=self.protect_arena_cards(cards,current,state.get('best_ids',[]))
        self.management_save({'inventory':cards,'inventory_at':time.time()})
        canonical=[canonical_card(c) for c in cards]
        weak=choose_test_card(canonical,protected)
        if weak is None:return {'rolls':0,'reason':'no_safe_test_copy'}
        cid=int(weak['card_user_id'])
        stored=DataManager.load_json(RESEARCH_FILE)
        if stored.get('ledger',{}).get('pending'):raise ValueError('Unresolved earlier research mutation')
        ledger=TestLedger(budget=min(balance,int(budget if budget is not None else balance)))
        ledger.shield([c for c in canonical if int(c['id']) in protected])
        ledger.observed_balance=balance
        samples=stored.get('samples',[])
        plans=plans or [('stats',[]),('stats',[]),('stats',[]),('stats',['attack']),('stats',['health']),
               ('stats',['crit_chance']),('stats',['attack','health']),('stats',['attack','crit_chance']),
               ('stats',['health','crit_chance']),('seal',[]),('seal',[])]
        original=next(c for c in cards if int(c['id'])==cid)
        response=self.management_post(root['data-reroll-card-url'],{'card_user_id':cid})
        api=dict(response['card'],card_user_id=cid)
        working=canonical_card(api)
        if fingerprint(working)!=fingerprint(canonical_card(original)):
            raise ValueError('Test copy changed after inventory scan; refusing roll')
        results=[]
        for kind,locks in plans[:max_rolls]:
            if not self._alive() or cid in protected:break
            cost=500 if kind=='seal' else 300*2**len(locks)
            if cost>ledger.remaining or cost>balance:break
            action={'type':kind,'card_user_id':str(cid),'locked_stats':locks,'cost':cost,'rationale':'bounded research of replacement and lock mechanics on a weak copy'}
            before=parse_roll_card(working)
            ledger.reserve(action,working,balance)
            DataManager.save_json(RESEARCH_FILE,{'ledger':ledger.to_dict(),'samples':samples,'test_copy':cid,'pending_response':None})
            if json.dumps(DataManager.load_json(RESEARCH_FILE).get('ledger',{}).get('pending'),sort_keys=True)!=json.dumps(ledger.to_dict()['pending'],sort_keys=True):
                raise ValueError('Research reservation not persisted; refusing mutation')
            payload={'card_user_id':cid}
            payload.update({f'locked_stats[{i}]':key for i,key in enumerate(locks)})
            endpoint=root['data-reroll-'+kind+'-url']
            result=self.management_post(endpoint,payload,mutation=True)
            DataManager.save_json(RESEARCH_FILE,{'pending_response':{'essence':result.get('essence'),'card':result.get('card')}})
            # Reread the specific copy; do not infer success solely from HTTP200.
            verified=self.management_post(root['data-reroll-card-url'],{'card_user_id':cid})
            working=canonical_card(dict(verified['card'],card_user_id=cid))
            after=parse_roll_card(working)
            if any(after[key]!=before[key] for key in locks):raise ValueError('Server failed to preserve locked stat')
            if result.get('essence') is not None:balance=int(re.sub(r'\s','',str(result['essence'])))
            else:
                _,fresh_root=self.management_page('/battle/reroll','.battle-reroll')
                balance=int(re.sub(r'\D','',fresh_root.select_one('[data-reroll-essence]').get_text()))
            ledger.reconcile(balance,resolved=True)
            updated=inventory_from_api_card(working,original)
            original=updated
            cards=[c for c in cards if int(c['id'])!=cid]+[updated]
            newly=protected_card_ids(cards,state.get('best_ids',[]),state.get('best_ids',[]),bench_size=24,per_seal=3)
            protected.update(newly)
            sample={'type':kind,'locked_stats':locks,'card':working,'before':before,'cost':cost,'at':time.time()}
            samples.append(sample);samples=samples[-500:]
            ledger.protected_ids.update(str(i) for i in protected)
            ledger.shield([canonical_card(c) for c in cards if int(c['id']) in protected and str(c['id']) not in ledger.protected_fingerprints])
            DataManager.save_json(RESEARCH_FILE,{'ledger':ledger.to_dict(),'samples':samples,'test_copy':cid,'pending_response':None})
            self.management_save({'inventory':cards,'inventory_at':0,'protected_ids':sorted(protected)})
            results.append({'type':kind,'locks':locks,'cost':cost,'after':after})
            self.log(f'🧪 Перековка: слабый экземпляр #{cid}, {kind}, локов {len(locks)}, потрачено {cost}.')
            if cid in protected:
                self.protect_arena_cards(cards,state.get('best_ids',[]),state.get('best_ids',[]))
                self.log('🔒 Тестовая карта стала перспективной — роллы остановлены, карта защищена.')
                weak=choose_test_card([canonical_card(c) for c in cards],protected)
                if weak is None:break
                cid=int(weak['card_user_id'])
                original=next(c for c in cards if int(c['id'])==cid)
                response=self.management_post(root['data-reroll-card-url'],{'card_user_id':cid})
                working=canonical_card(dict(response['card'],card_user_id=cid))
                if fingerprint(working)!=fingerprint(canonical_card(original)):
                    raise ValueError('Next test copy changed; refusing roll')
        # Full inventory reread proves protected statistics unchanged by research.
        fresh=self.load_arena_inventory(root.select_one('.card-filter-form[data-url]'))
        ledger.assert_protected_unchanged([canonical_card(c) for c in fresh if str(c['id']) in ledger.protected_fingerprints])
        self.management_save({'inventory':fresh,'inventory_at':0})
        return {'rolls':len(results),'copy_id':cid,'spent':ledger.spent,'balance':balance,'protected_unchanged':True,'results':results}

    def reconcile_research_pending(self,confirmed_balance_after=None):
        """Resolve a charged operation by fresh balance/copy reads, never resend."""
        stored=DataManager.load_json(RESEARCH_FILE)
        ledger=TestLedger.from_dict(stored.get('ledger',{}))
        pending=ledger.pending
        if not pending:return False
        _,root=self.management_page('/battle/reroll','.battle-reroll')
        balance=int(re.sub(r'\D','',root.select_one('[data-reroll-essence]').get_text()))
        receipt=(stored.get('pending_response') or {}).get('essence')
        charged_balance=int(re.sub(r'\s','',str(receipt))) if receipt is not None else (balance if confirmed_balance_after is None else int(confirmed_balance_after))
        if charged_balance!=pending['balance_before']-pending['cost']:
            raise ValueError('Ambiguous research charge; refusing another roll')
        cid=int(pending['card_user_id'])
        response=self.management_post(root['data-reroll-card-url'],{'card_user_id':cid})
        working=canonical_card(dict(response['card'],card_user_id=cid))
        after=parse_roll_card(working)
        for key in pending.get('locked_stats',[]):
            if after[key]!=pending['before'][('attack','health','crit_chance').index(key)]:
                raise ValueError('Recovered roll changed locked stat')
        fresh=self.load_arena_inventory(root.select_one('.card-filter-form[data-url]'))
        ledger.assert_protected_unchanged([canonical_card(c) for c in fresh if str(c['id']) in ledger.protected_fingerprints])
        ledger.reconcile(charged_balance,resolved=True)
        ledger.observed_balance=balance
        samples=stored.get('samples',[])+[{'type':pending['type'],'locked_stats':pending.get('locked_stats',[]),'card':working,'cost':pending['cost'],'recovered':True,'at':time.time()}]
        DataManager.save_json(RESEARCH_FILE,{'ledger':ledger.to_dict(),'samples':samples[-500:],'pending_response':None})
        self.management_save({'inventory':fresh,'inventory_at':time.time()})
        self.protect_arena_cards(fresh,self.management_state().get('best_ids',[]),self.management_state().get('best_ids',[]))
        self.log(f'🧪 Подтверждён ранее выполненный ролл #{cid}: {pending["cost"]} эссенции; повтор не отправлялся.')
        return True
