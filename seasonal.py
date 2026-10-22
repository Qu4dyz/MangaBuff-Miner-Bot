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
    ("/event/pack", "Ивент — праздничные активности"),
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
SITE_WATCH_PAGES = ("/", "/balance", "/battle", "/halloween/quest", "/event/pack")
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

    # ---------------------------------------------------------------- Halloween opening
    HALLOWEEN_STATE_URL = "https://mangabuff.ru/halloween/quest/state"
    HALLOWEEN_MILESTONES = (90, 95, 99)

    def run_halloween_watch(self) -> List[str]:
        """Owner-only: report the global candy goal, the holiday start and claim the personal reward."""
        try:
            res = self.session.get(self.HALLOWEEN_STATE_URL, timeout=15,
                                   headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"})
            data = res.json() if res.status_code == 200 else None
        except Exception:
            data = None
        if not isinstance(data, dict) or not isinstance(data.get("event"), dict):
            return []
        ev, inv = data["event"], data.get("investigation") or {}
        now = time.time()
        prev = DataManager.get_setting("halloween_watch_state") or {}
        cur = {"at": now, "returned": ev.get("returned_candies"), "percent": ev.get("percent"),
               "remaining": ev.get("remaining"), "completed": bool(ev.get("completed")), "holiday": bool(ev.get("holiday_started")),
               "locks": ev.get("locks"), "keys": sorted(set(ev) | set(data))}
        DataManager.set_setting("halloween_watch_state", cur)
        if not prev:
            return []  # first look: only remember

        events = halloween_changes(prev, cur, self.HALLOWEEN_MILESTONES)
        if inv.get("reward_available") and not inv.get("reward_claimed"):
            events.append("🍬 Личная награда: " + self._claim_halloween_reward())
        if cur["holiday"] and not prev.get("holiday"):
            # The moment it opens: look at everything new at once instead of in 6 h
            DataManager.set_setting("site_watch_last_at", 0)
            new = self.run_site_watch() or {}
            found = new.get("scripts", []) + new.get("achievements", [])
            events.append("🧩 Новое на сайте: " + (", ".join(found) if found else "новых скриптов пока не видно"))
        if events:
            self.notifier.notify_alert("Хэллоуин", "\n".join(events) + "\n<a href=\"https://mangabuff.ru/halloween/quest\">Открыть</a>")
            self.log("🎃 " + " | ".join(events))
        return events

    def _claim_halloween_reward(self) -> str:
        try:
            page = self.session.get("https://mangabuff.ru/halloween/quest", timeout=15).text
            m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
            r = self.session.post("https://mangabuff.ru/halloween/quest/reward", json={}, timeout=15, headers={
                "Accept": "application/json", "X-Requested-With": "XMLHttpRequest",
                "X-CSRF-TOKEN": m.group(1) if m else getattr(self, "csrf_token", "")})
            return "забрал 10 конфет" if r.status_code == 200 else f"забрать не получилось (HTTP {r.status_code})"
        except Exception as e:
            return f"забрать не получилось ({e})"


def halloween_changes(prev: Dict, cur: Dict, milestones=(90, 95, 99)) -> List[str]:
    """Human lines for what changed between two snapshots of the Halloween event."""
    out = []
    pct, old_pct = float(cur.get("percent") or 0), float(prev.get("percent") or 0)
    eta = ""
    try:
        rate = (cur["returned"] - prev["returned"]) / max(1.0, cur["at"] - prev["at"])
        if rate > 0 and pct < 100:
            mins = int(cur["remaining"] / rate / 60)
            eta = f" — при нынешнем темпе 100% примерно через {mins // 60} ч {mins % 60} мин" if mins >= 60 else f" — 100% примерно через {mins} мин"
    except (KeyError, TypeError, ZeroDivisionError):
        pass
    for m in milestones:
        if old_pct < m <= pct:
            out.append(f"📈 Собрано {pct:g}% конфет{eta}")
            break
    if cur.get("locks") != prev.get("locks") and prev.get("locks") is not None:
        out.append(f"🔓 Замки изменились: {prev.get('locks')} → {cur.get('locks')}")
    if cur.get("completed") and not prev.get("completed"):
        out.append("🎃 Все конфеты собраны — Хэллоуин вот-вот начнётся!")
    if cur.get("holiday") and not prev.get("holiday"):
        out.append("🎉 ХЭЛЛОУИН НАЧАЛСЯ! Праздничные активности: https://mangabuff.ru/event/pack")
    new_keys = sorted(set(cur.get("keys") or []) - set(prev.get("keys") or []))
    if new_keys and prev.get("keys"):
        out.append("🆕 В состоянии ивента появились поля: " + ", ".join(new_keys))
    return out


def parse_event_pack(html: str) -> Dict:
    """Candy balance, today's drops and cauldron state from /event/pack."""
    def num(rx):
        m = re.search(rx, html, re.S)
        return int(re.sub(r"\D", "", m.group(1)) or 0) if m else None
    take = re.search(r'<button[^>]*js-summer-heat-take[^>]*>', html)
    return {
        "balance": num(r'js-event-balance">\s*([\d\s]+)<'),
        "today": num(r'Сегодня выпало</div>\s*<div class="event-page__stat-value">\s*([\d\s]+)<'),
        "temperature": num(r'js-summer-heat-temp">\s*(\d+)°'),
        "can_take": bool(take) and "disabled" not in take.group(0),
    }


def _balance_after(res, old, gained):
    """Balance the server reports after a claim, else the old one plus the known reward."""
    try:
        value = (res.json() or {}).get("balance")
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
            return int(value)
    except Exception:
        pass
    return old + gained if isinstance(old, int) else old


def sync_candy_day(info: Dict) -> None:
    """Take the site's "Сегодня выпало" when it is ahead of our own count: drops from reading on the
    phone or before a restart count too. Our count decides whether to keep reading for candies."""
    site = info.get("today")
    if site is None:
        return
    today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 3 * 3600))  # MSK day, as event_candy_day
    day = DataManager.get_setting("halloween_candy_day") or {}
    if day.get("date") != today:
        day = {"date": today, "n": 0, "misses": 0}
    if site > day.get("n", 0):
        DataManager.set_setting("halloween_candy_day", {**day, "n": site})


class HalloweenEventMixin:
    """Free Halloween rewards that need no choice: the cauldron treat. Spending candies stays manual."""

    EVENT_PACK_URL = "https://mangabuff.ru/event/pack"

    def refresh_halloween_info(self) -> None:
        """Candy balance and cauldron heat for the app right at start: run_halloween_event comes
        at the end of a long cycle, so the cards stayed empty for up to an hour. Reads only."""
        if not DataManager.get_setting("halloween_event_enabled", True):
            return
        try:
            res = self.session.get(self.EVENT_PACK_URL, timeout=15)
            info = parse_event_pack(res.text) if res.status_code == 200 else {}
        except Exception:
            return
        if info.get("balance") is not None:  # not the event page (event over, logged out): keep what we had
            DataManager.set_setting("halloween_event_info",
                                    {**(DataManager.get_setting("halloween_event_info") or {}), **info})
            sync_candy_day(info)

    def run_halloween_event(self) -> Dict:
        res = self.session.get(self.EVENT_PACK_URL, timeout=15)
        if res.status_code != 200:
            return {}
        info = parse_event_pack(res.text)
        sync_candy_day(info)
        if info["can_take"]:
            m = re.search(r'<meta name="csrf-token" content="([^"]+)"', res.text)
            r = self.session.post("https://mangabuff.ru/event/heat/claim", data={"_token": m.group(1) if m else ""},
                                  timeout=15, headers={"X-Requested-With": "XMLHttpRequest", "Referer": self.EVENT_PACK_URL})
            info["claimed"] = r.status_code == 200
            self.log("🍬 Котёл кошмаров: " + ("забрал 5 конфет" if info["claimed"] else f"не получилось забрать (HTTP {r.status_code})"))
            if info["claimed"]:
                info["balance"] = _balance_after(r, info.get("balance"), 5)
        today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 3 * 3600))  # MSK day: the cauldron resets at midnight
        if info.get("claimed"):
            DataManager.set_setting("halloween_heat_claimed_day", today)
        elif ((info.get("temperature") or 0) >= 100 and not info["can_take"]
              and DataManager.get_setting("halloween_heat_claimed_day") != today
              and DataManager.get_setting("halloween_heat_locked_alert_day") != today):
            # The site may want a burned card before it lets you take the treat: ask once a day, never burn
            DataManager.set_setting("halloween_heat_locked_alert_day", today)
            self.log("🔥 Котёл разогрет до 100°, но сайт не даёт забрать 5 🍬 — похоже, нужно сжечь карту")
            self.notifier.notify_alert("котёл кошмаров", "Котёл разогрет до 100°, но сайт не даёт забрать 5 🍬. "
                                       "Похоже, сначала нужно сжечь карту. Бот ничего не сжигает — решай сам.\n"
                                       "<a href=\"https://mangabuff.ru/event/pack\">Открыть котёл</a>")
        info["claimed_today"] = DataManager.get_setting("halloween_heat_claimed_day") == today
        reward = self._quest_reward_ready()
        if reward:
            msg = SiteWatchMixin._claim_halloween_reward(self)  # same claim as the owner-only watcher
            self.log("🍬 Личная награда за расследование: " + msg)
            if msg.startswith("забрал"):
                info["balance"] = (info.get("balance") or 0) + 10
        DataManager.set_setting("halloween_event_info", info)
        return info

    def _quest_reward_ready(self) -> bool:
        try:
            r = self.session.get("https://mangabuff.ru/halloween/quest/state", timeout=15,
                                 headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"})
            inv = (r.json() or {}).get("investigation") or {} if r.status_code == 200 else {}
        except Exception:
            return False
        return bool(inv.get("reward_available") and not inv.get("reward_claimed"))
