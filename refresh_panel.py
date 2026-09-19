#!/usr/bin/env python3
"""Live-refresh MangaBuff panel fields into reading_state.json (no side effects)."""
import sys
sys.path.insert(0, "/root/mangabuff-bot")
from engine import MangaMinerBot

bot = MangaMinerBot(
    log_callback=lambda *a, **k: None,
    progress_callback=lambda p: None,
    stats_callback=lambda **kw: None,
    headless=True,
)
ok = bot.refresh_live_panel()
if not ok:
    print("FAIL")
    raise SystemExit(1)
from data_management import DataManager
p = (DataManager.load_reading_state() or {}).get("panel") or {}
print(
    "OK",
    f"dia={p.get('diamonds')}",
    f"ore={p.get('ore')}",
    f"total={p.get('diamonds_total')}",
    f"energy={p.get('energy')}",
    f"ads={p.get('ads_count')}",
)
