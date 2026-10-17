# -*- coding: utf-8 -*-
"""Long-term archive of arena and cup battles (SQLite).

arena_observations.json keeps only the last 40 replays for the live deck logic;
this archive keeps every battle so effects, targeting and the model can be
measured on thousands of fights. Writing never raises: a broken archive must
not stop the bot from fighting.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import zlib

DB_NAME = "arena_battles.sqlite3"
_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS battles (
    id          TEXT PRIMARY KEY,
    source      TEXT NOT NULL,          -- arena | cup
    observed_at REAL NOT NULL,
    own_side    TEXT,
    is_win      INTEGER,
    first_side  TEXT,
    own_ids     TEXT,                   -- JSON list of card_user_id
    opp_ids     TEXT,
    own_buff    TEXT,
    opp_buff    TEXT,
    step_count  INTEGER,
    payload     BLOB NOT NULL           -- zlib(JSON observation)
);
CREATE INDEX IF NOT EXISTS battles_observed ON battles(observed_at);
CREATE INDEX IF NOT EXISTS battles_source ON battles(source, observed_at);
"""


def db_path(base_dir):
    return os.path.join(base_dir, DB_NAME)


def _connect(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.executescript(_SCHEMA)
    return conn


def _side_summary(observation, side):
    cards = (observation.get("cards") or {}).get(side) or []
    ids = [c.get("card_user_id") for c in cards]
    buffs = {c.get("buff_code") for c in cards if c.get("buff_code")}
    return json.dumps(ids), (sorted(buffs)[0] if len(buffs) == 1 else None)


def record(path, observation, source="arena"):
    """Store one battle; True when it was new. Never raises."""
    if not isinstance(observation, dict) or not observation.get("id"):
        return False
    try:
        own = observation.get("own_side")
        opp = "defender" if own == "attacker" else "attacker"
        own_ids, own_buff = _side_summary(observation, own)
        opp_ids, opp_buff = _side_summary(observation, opp)
        payload = zlib.compress(json.dumps(observation, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6)
        row = (
            str(observation["id"]), source, float(observation.get("observed_at") or time.time()), own,
            None if observation.get("is_win") is None else int(bool(observation["is_win"])),
            observation.get("first_side"), own_ids, opp_ids, own_buff, opp_buff,
            len(observation.get("steps") or []), payload,
        )
        with _LOCK:
            conn = _connect(path)
            try:
                with conn:
                    cur = conn.execute("INSERT OR IGNORE INTO battles VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row)
                return cur.rowcount > 0
            finally:
                conn.close()
    except Exception:
        return False


def load(path, source=None, since=None, limit=None, with_steps_only=False):
    """Battles oldest first, as the original observation dicts."""
    if not os.path.exists(path):
        return []
    query = "SELECT payload FROM battles WHERE 1=1"
    args = []
    if source:
        query += " AND source = ?"
        args.append(source)
    if since is not None:
        query += " AND observed_at >= ?"
        args.append(float(since))
    if with_steps_only:
        query += " AND step_count > 0"
    query += " ORDER BY observed_at DESC"
    if limit:
        query += " LIMIT ?"
        args.append(int(limit))
    with _LOCK:
        conn = _connect(path)
        try:
            rows = conn.execute(query, args).fetchall()
        finally:
            conn.close()
    out = []
    for (blob,) in reversed(rows):
        try:
            out.append(json.loads(zlib.decompress(blob).decode("utf-8")))
        except Exception:
            continue
    return out


def stats(path):
    """Counts per source plus the time span, for reports."""
    if not os.path.exists(path):
        return {}
    with _LOCK:
        conn = _connect(path)
        try:
            rows = conn.execute(
                "SELECT source, COUNT(*), SUM(is_win), MIN(observed_at), MAX(observed_at) FROM battles GROUP BY source"
            ).fetchall()
        finally:
            conn.close()
    return {s: {"battles": n, "wins": int(w or 0), "first": a, "last": b} for s, n, w, a, b in rows}


def import_json(path, json_path, source):
    """Copy battles from one of the legacy 40-item JSON files; returns how many were new."""
    try:
        with open(json_path, encoding="utf-8") as f:
            battles = json.load(f).get("battles") or []
    except (OSError, ValueError, AttributeError):
        return 0
    return sum(1 for b in battles if record(path, b, source))


def backfill(base_dir):
    """One-time import of what the JSON files still hold (idempotent)."""
    path = db_path(base_dir)
    new = import_json(path, os.path.join(base_dir, "arena_observations.json"), "arena")
    new += import_json(path, os.path.join(base_dir, "manual_arena_history.json"), "cup")
    return new
