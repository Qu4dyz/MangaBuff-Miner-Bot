"""Bounded server replay observations for comparative arena evaluation.

No HTTP or game actions. Only JSON script elements are read; cookies/CSRF,
page HTML and chat/user text are never retained.
"""
import hashlib
import json
import math
import os
import tempfile
import time
from bs4 import BeautifulSoup

def replay_from_html(html):
    soup=BeautifulSoup(html,'html.parser')
    values={}
    for name in ('log','reward','seals','buffs'):
        node=soup.select_one(f'[data-battle-{name}-json]')
        if node:
            raw=node.get_text()
            if len(raw)>600000:return None
            try:values[name]=json.loads(raw)
            except (ValueError,TypeError):return None
    return observation_from_values(values)

def observation_from_values(values):
    log=values.get('log');reward=values.get('reward')
    if not isinstance(log,dict) or log.get('type')!='server_auto_battle' or not isinstance(reward,dict):return None
    own_attacker=reward.get('is_attacker_reward')
    if not isinstance(own_attacker,bool) or not isinstance(reward.get('is_win'),bool):return None
    sides=log.get('cards')
    if not isinstance(sides,dict) or any(not isinstance(sides.get(side),list) or len(sides[side])!=4 for side in ('attacker','defender')):return None
    cards={}
    fields=('card_user_id','attack','max_hp','crit_chance','seal_code','battle_card_energy','is_tired','buff_code')
    for side in ('attacker','defender'):
        cards[side]=[]
        for item in sides[side]:
            if not isinstance(item,dict):return None
            card={k:item.get(k) for k in fields}
            try:
                if any(not math.isfinite(float(card[k])) or float(card[k])<=0 for k in ('card_user_id','attack','max_hp')):return None
                if not 0<=float(card['crit_chance'])<=100:return None
            except (ValueError,TypeError):return None
            cards[side].append(card)
    steps=log.get('steps')
    if not isinstance(steps,list) or len(steps)>1000:return None
    safe_steps=[]
    # Retain primitive combat measurements, not display strings or arbitrary data.
    fields=('turn','type','from_side','to_side','from_card_user_id','to_card_user_id','damage','dealt_damage','is_crit','hp_before','hp_after','attacker_hp_before','attacker_hp_after','is_target_dead','target_effect')
    for item in steps:
        if not isinstance(item,dict):continue
        step={k:item[k] for k in fields if k in item and isinstance(item[k],(str,int,float,bool,type(None)))}
        for group in ('effects','extra_attacks'):
            step[group]=[{k:v for k,v in effect.items() if k in ('type','amount','damage','is_crit','card_user_id','target_card_user_id','to_card_user_id','hp_after','hp_before','real_side','target_effect') and isinstance(v,(str,int,float,bool,type(None)))}
                         for effect in item.get(group,[]) if isinstance(effect,dict)][:30]
        safe_steps.append(step)
    seals=values.get('seals')
    definitions={}
    if isinstance(seals,dict):
        for item in seals.values():
            if isinstance(item,dict) and isinstance(item.get('code'),str):
                definitions[item['code']]={k:item[k] for k in ('code','short','description') if isinstance(item.get(k),str)}
    identifier=hashlib.sha256(json.dumps({'cards':cards,'steps':safe_steps,'winner':log.get('winner')},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'id':identifier,'observed_at':time.time(),'cards':cards,'steps':safe_steps,
            'own_side':'attacker' if own_attacker else 'defender','is_win':reward['is_win'],
            'winner':log.get('winner'),'first_side':log.get('first_side'),'max_turns':log.get('max_turns'),
            'seal_definitions':definitions}

def load_observations(path):
    try:
        with open(path,encoding='utf-8') as f:data=json.load(f)
        if isinstance(data,dict) and isinstance(data.get('battles'),list):return data['battles'][-40:]
    except (OSError,ValueError):pass
    return []

def save_observation(path,observation):
    if observation is None:return
    battles=load_observations(path)
    if any(b.get('id')==observation['id'] for b in battles):return
    battles=(battles+[observation])[-40:]
    directory=os.path.dirname(os.path.abspath(path))
    temporary=None
    try:
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=directory,delete=False) as f:
            temporary=f.name;f.write(json.dumps({'schema':1,'battles':battles},ensure_ascii=False))
        os.replace(temporary,path)
    finally:
        if temporary and os.path.exists(temporary):os.unlink(temporary)

def opponent_teams(battles,limit=6):
    """Most recent distinct observed opposition; each card uses effective server stats."""
    teams=[];seen=set()
    for battle in reversed(battles):
        side='defender' if battle.get('own_side')=='attacker' else 'attacker'
        team=(battle.get('cards') or {}).get(side)
        if not isinstance(team,list) or len(team)!=4:continue
        identity=tuple(c.get('card_user_id') for c in team)
        if identity in seen:continue
        seen.add(identity);teams.append(team)
        if len(teams)>=limit:break
    return teams

def calibrate_rules(battles):
    """Estimate ordinary critical damage; report first-Hunter uncertainty separately."""
    import statistics
    ordinary=[];crit=[];hunter=[]
    for battle in battles:
        by_id={c['card_user_id']:c for side in (battle.get('cards') or {}).values() for c in side}
        acted=set()
        for step in battle.get('steps',[]):
            cid=step.get('from_card_user_id');actor=by_id.get(cid);target=by_id.get(step.get('to_card_user_id'))
            first=cid not in acted;acted.add(cid)
            if not actor or not target or step.get('type')!='attack':continue
            damage=step.get('damage');attack=actor.get('attack')
            if not isinstance(damage,(int,float)) or not attack or step.get('target_effect'):continue
            if actor.get('seal_code') in ('rage','execution','berserker','berserk'):continue
            ratio=damage/attack
            if first and actor.get('seal_code')=='hunter':
                hunter.append(ratio/(2 if step.get('is_crit') else 1));continue
            (crit if step.get('is_crit') else ordinary).append(ratio)
    result={'crit_multiplier':2.0,'jitter':.10,'calibration_samples':len(ordinary)+len(crit),'hunter_samples':len(hunter)}
    if len(ordinary)>=8 and len(crit)>=5:
        result['crit_multiplier']=max(1.5,min(2.5,statistics.median(crit)/statistics.median(ordinary)))
    if len(hunter)>=3:
        result['hunter_multiplier']=max(1.0,min(2.0,statistics.median(hunter)))
        result['hunter_source']='observed_first_attacks'
    else:
        result['hunter_source']='live_description_unverified'
    result['uncertainties']=['Target selection uniformity and all seal interaction orders are not proven.',
        'Live seal descriptions can differ from server behavior; non-observed effects remain approximate.']
    return result

def recent_battles(battles,max_age=7*24*3600):
    now=time.time()
    return [b for b in battles if isinstance(b,dict) and 0<=now-float(b.get('observed_at',0))<=max_age]

def supported_definitions(battles):
    """Stop trusting fixed seal coefficients when fresh descriptions change."""
    expected={'double_strike':[30], 'rage':[50,30], 'execution':[35,35],
        'berserk':[30], 'hunter':[75], 'thorns':[30], 'regeneration':[7],
        'barrier':[70], 'immortality':[1], 'revenge':[100], 'chain_attack':[25],
        'vampirism':[25], 'phoenix':[35,35], 'dodge':[20]}
    if not battles:return False
    import re
    descriptions=battles[-1].get('seal_definitions') or {}
    if set(expected)-set(descriptions) or 'taunt' not in descriptions:return False
    for code,numbers in expected.items():
        text=descriptions[code].get('description','')
        observed=[int(v) for v in re.findall(r'\d+',text)]
        if observed!=numbers:return False
    return True

def normalized_owned(cards,battles,current_energy=None):
    """Use recent measured per-copy stats, never extrapolate boosts to other copies."""
    from battle_effectiveness import from_inventory,from_replay_card,unbuff_owned
    measured={}
    for battle in recent_battles(battles,max_age=4*3600):
        for c in (battle.get('cards') or {}).get(battle.get('own_side'),[]):
            observed=dict(c)
            if isinstance(c.get('battle_card_energy'),(int,float)):
                observed['energy_after']=max(0,c['battle_card_energy']-(2 if battle.get('is_win') else 1))
            measured[c['card_user_id']]=observed
    result=[]
    for raw in cards:
        cid=int(raw.get('id') or 0)
        normal=from_inventory(raw,(current_energy or {}).get(cid))
        if normal is None:continue
        old=measured.get(cid)
        effective=from_replay_card(old) if old else None
        if normal['energy'] is None and old and 'energy_after' in old:normal['energy']=old['energy_after']
        if effective:
            effective=unbuff_owned(effective,baseline_crit=normal['crit'])
        if effective and effective['seal_id']==normal['seal_id'] and abs(effective['crit']-normal['crit'])<.01:
            energy=normal['energy']
            # Never reuse an old un-tired stat line for a now-exhausted card.
            if energy is None or energy>0:
                normal.update(attack=effective['attack'],max_hp=effective['max_hp'],
                              stats_source='replay_effective',stats_unbuffed=True,buff=None)
        result.append(normal)
    return result
