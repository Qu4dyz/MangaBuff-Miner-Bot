#!/usr/bin/env python3
"""Live-refresh MangaBuff panel fields into reading_state.json (no claims/mining)."""
from engine import MangaMinerBot

from data_management import DataManager


def main():
    bot = MangaMinerBot(
        log_callback=lambda *a, **k: None,
        progress_callback=lambda p: None,
        stats_callback=lambda **kw: None,
        headless=True,
    )
    if not bot.refresh_live_panel():
        print("FAIL")
        return 1
    panel = (DataManager.load_reading_state() or {}).get("panel") or {}
    print(
        "OK",
        f"dia={panel.get('diamonds')}",
        f"ore={panel.get('ore')}",
        f"total={panel.get('diamonds_total')}",
        f"energy={panel.get('energy')}",
        f"ads={panel.get('ads_count')}",
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
