# -*- coding: utf-8 -*-
"""Watch for seasonal MangaBuff pages coming online and alert via Telegram."""
from __future__ import annotations

import re
import time
from typing import Dict, List, Tuple

from data_management import DataManager

# Paths that are currently 404 and should trigger an alert when they return real HTML.
SEASONAL_PROBE_PATHS: Tuple[Tuple[str, str], ...] = (
    ("/halloween", "Хэллоуин — главная"),
    ("/halloween/quest", "Хэллоуин — квест"),
    ("/halloween/game", "Хэллоуин — мини-игра"),
    ("/halloween/pack", "Хэллоуин — паки"),
    ("/event", "Ивент — главная"),
    ("/event/boost", "Ивент — boost"),
)

SETTING_ALERTED = "seasonal_alerted_paths"
SETTING_ALERTED_AT = "seasonal_alerted_at"
SETTING_LAST_CHECK = "seasonal_last_check_at"
DEFAULT_CHECK_INTERVAL_SEC = 60 * 60  # once per hour
# After the season ends (all probes dead), allow a fresh alert next year.
RESET_AFTER_OFFLINE_SEC = 14 * 24 * 60 * 60


def is_live_html(status_code: int, body: str) -> bool:
    """True when the response looks like a real site page, not a stub 404 JSON."""
    if status_code != 200:
        return False
    text = body or ""
    if len(text) < 500:
        return False
    low = text.lower()
    if '"message"' in low and "<html" not in low and "<!doctype" not in low:
        return False
    return "<html" in low or "<!doctype" in low or "<title" in low


class SeasonalWatchMixin:
    """Mixin: poll seasonal URLs and TG-notify once when they go live."""

    def run_seasonal_watch(self, force: bool = False) -> List[str]:
        """
        Probe seasonal endpoints. On first transition to live for any path,
        send a Telegram alert (once per path) and return newly live paths.
        """
        if not DataManager.get_setting("seasonal_watch_enabled", True):
            return []

        now = time.time()
        last = float(DataManager.get_setting(SETTING_LAST_CHECK, 0) or 0)
        interval = int(
            DataManager.get_setting("seasonal_watch_interval_sec", DEFAULT_CHECK_INTERVAL_SEC)
            or DEFAULT_CHECK_INTERVAL_SEC
        )
        if not force and last and now - last < interval:
            return []

        DataManager.set_setting(SETTING_LAST_CHECK, now)

        alerted = set(DataManager.get_setting(SETTING_ALERTED, []) or [])
        newly_live: List[str] = []
        live_labels: Dict[str, str] = {}
        any_live = False

        for path, label in SEASONAL_PROBE_PATHS:
            try:
                res = self.session.get(
                    f"https://mangabuff.ru{path}",
                    timeout=12,
                    allow_redirects=True,
                )
                body = getattr(res, "text", "") or ""
                status = getattr(res, "status_code", 0)
            except Exception as e:
                self.log(f"⚠️ Seasonal watch {path}: {e}")
                continue

            if not is_live_html(status, body):
                continue

            any_live = True
            if path in alerted:
                continue

            newly_live.append(path)
            live_labels[path] = label
            self.log(f"🎃 Seasonal: {path} уже доступен ({label})")

        # Season fully offline for 2+ weeks → allow alerts again next year
        if not any_live and alerted:
            alerted_at = float(DataManager.get_setting(SETTING_ALERTED_AT, 0) or 0)
            if alerted_at and now - alerted_at >= RESET_AFTER_OFFLINE_SEC:
                DataManager.set_setting(SETTING_ALERTED, [])
                DataManager.set_setting(SETTING_ALERTED_AT, 0)
                self.log("🎃 Seasonal watch: сброс прошлых алертов (ивент давно оффлайн)")

        if not newly_live:
            return []

        alerted.update(newly_live)
        DataManager.set_setting(SETTING_ALERTED, sorted(alerted))
        DataManager.set_setting(SETTING_ALERTED_AT, now)

        self.notifier.notify_seasonal_event_live(
            paths=newly_live,
            labels=live_labels,
        )
        self.log(
            "🎃 Сезонное обновление обнаружено — уведомление отправлено в Telegram: "
            + ", ".join(newly_live)
        )
        return newly_live


# Pages whose scripts are scanned for new site features (achievement easter eggs, event scripts).
SITE_WATCH_PAGES = ("/", "/balance", "/battle", "/halloween/quest")
SITE_WATCH_INTERVAL_SEC = 6 * 3600
ACHIEVEMENT_RE = re.compile(r"/achievements/[\w-]+")


def site_features(scripts: Dict[str, str]) -> Dict[str, List[str]]:
    """Script names (without cache-busting query) and achievement endpoints found in them."""
    return {
        "scripts": sorted({name.split("?", 1)[0] for name in scripts}),
        "achievements": sorted({hit for body in scripts.values() for hit in ACHIEVEMENT_RE.findall(body)}),
    }


class SiteWatchMixin:
    """Owner-only: report new site scripts and achievement endpoints (off unless site_watch_enabled)."""

    def run_site_watch(self) -> Dict[str, List[str]]:
        now = time.time()
        if now - float(DataManager.get_setting("site_watch_last_at", 0) or 0) < SITE_WATCH_INTERVAL_SEC:
            return {}
        DataManager.set_setting("site_watch_last_at", now)
        scripts: Dict[str, str] = {}
        for page in SITE_WATCH_PAGES:
            try:
                html = self.session.get("https://mangabuff.ru" + page, timeout=20).text
            except Exception:
                continue
            for src in re.findall(r'<script[^>]+src="(/js/[^"]+)"', html):
                if src not in scripts and not any(x in src for x in ("jquery", "libs.js")):
                    try:
                        scripts[src] = self.session.get("https://mangabuff.ru" + src, timeout=40).text
                    except Exception:
                        pass
        if not scripts:
            return {}
        found = site_features(scripts)
        known = DataManager.get_setting("site_watch_known") or {}
        DataManager.set_setting("site_watch_known", {k: sorted(set(known.get(k, [])) | set(v)) for k, v in found.items()})
        if not known:  # first run only records what exists today
            return {}
        new = {k: [x for x in v if x not in set(known.get(k, []))] for k, v in found.items()}
        new = {k: v for k, v in new.items() if v}
        if new:
            lines = [f"🏆 Новая ачивка-пасхалка: <code>{a}</code>" for a in new.get("achievements", [])]
            lines += [f"🧩 Новый скрипт: <code>{s}</code>" for s in new.get("scripts", [])]
            self.notifier.notify_alert("на сайте появилось новое", "\n".join(lines))
            self.log("🔭 На сайте новое: " + ", ".join(new.get("achievements", []) + new.get("scripts", [])))
        return new
