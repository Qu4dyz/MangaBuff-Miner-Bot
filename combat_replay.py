"""Offline, read-only battle datasets. Never imports the bot or opens a session."""
import copy
import json
import sqlite3
import zlib
from pathlib import Path

def load_archive(path, source=None):
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as conn:
        query = 'SELECT payload FROM battles'
        args = ()
        if source:
            query += ' WHERE source=?'
            args = (source,)
        rows = conn.execute(query + ' ORDER BY observed_at,id', args).fetchall()
    return [json.loads(zlib.decompress(blob)) for (blob,) in rows]

def chronological_split(battles, share=.8):
    if not 0 < share < 1:
        raise ValueError('Training share must be between zero and one')
    ordered = sorted(battles, key=lambda b: (b.get('observed_at', 0), str(b['id'])))
    if len({str(b['id']) for b in ordered}) != len(ordered):
        raise ValueError('Duplicate battle IDs')
    cut = int(len(ordered) * share)
    if not cut or cut == len(ordered):
        raise ValueError('Not enough battles for a holdout')
    boundary = ordered[cut]['observed_at']
    while cut > 0 and ordered[cut - 1]['observed_at'] == boundary:
        cut -= 1
    if not cut:
        raise ValueError('Not enough distinct timestamps for a holdout')
    return ordered[:cut], ordered[cut:]

def cup_samples(battles):
    samples = []
    seen = set()
    for battle in battles:
        for decision in battle.get('decisions') or []:
            state = decision.get('state')
            if not isinstance(state, dict) or not isinstance(state.get('revision'), int):
                continue
            identity = (str(battle['id']), state['revision'])
            if identity in seen:
                continue
            if str(state.get('fight_id')) != str(battle['id']):
                continue
            kind = decision.get('kind')
            if kind not in ('attack', 'tactic') or not isinstance(decision.get('payload'), dict):
                continue
            seen.add(identity)
            # Only history available at this decision. Never append later frames.
            history = state.get('battle_steps') or []
            if not isinstance(history, list):
                history = []
            samples.append({
                'fight_id': str(battle['id']), 'observed_at': battle.get('observed_at', 0),
                'side': battle['own_side'], 'is_win': battle.get('is_win'),
                'state': copy.deepcopy(state),
                'history': copy.deepcopy(history),
                'action': (kind, copy.deepcopy(decision['payload'])),
                'taunts': list(battle.get('taunts') or []),
                'complete_trace': False,
            })
    return samples


def capture_cup_trace(state):
    """Keep the server's final frame without claiming partial logs are complete."""
    cumulative = state.get('battle_steps')
    source = 'server_cumulative' if isinstance(cumulative, list) else 'server_incremental'
    steps = cumulative if isinstance(cumulative, list) else state.get('steps')
    keys = ('fight_id', 'fight_status', 'winner_user_id', 'turn', 'revision',
            'used_cards', 'final', 'tactics', 'finished_reason')
    return {'steps': copy.deepcopy(steps) if isinstance(steps, list) else [],
            'trace_source': source, 'trace_complete': False,
            'final_state': {k: copy.deepcopy(state[k]) for k in keys if k in state}}
