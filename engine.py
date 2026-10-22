from data_management import DataManager
from languages import tr
from bs4 import BeautifulSoup
from config import CONFIG, TARGET_RARITIES, BASE_DIR
import re
import json
import uuid
from html import unescape
from urllib.parse import urljoin
from profile_theme import skin_classes, skin_variables, stylesheet_urls, theme_from_variables
from diagnostics import mask_secrets, reading_response_details
from utils import (
    parse_smart_number,
    safe_cookies_to_dict,
    normalize_proxy_url,
    mask_proxy_url,
    parse_card_cooldown_seconds,
    get_seconds_until_midnight_msk,
    classify_card_copy_number
)
from http_session import (
    CHROME_UA,
    GuardedSession,
    create_raw_session,
    has_curl_cffi,
    is_anti_bot_page,
    load_cookies_into,
    try_refresh_ddos_guard,
)
import os
import collections
import random
import threading
import time
from tg_notifier import get_notifier
from arena import ArenaAutomationMixin, replay_from_html, save_observation
from club import ClubAutomationMixin
from seasonal import HalloweenEventMixin, SeasonalWatchMixin, SiteWatchMixin


ARENA_TIERS = [
    "Бронза",
    "Серебро",
    "Золото",
    "Платина",
    "Алмаз",
    "Мастер",
    "Легенда",
]

TIER_ICONS = {
    "Бронза": "🛡️",
    "Серебро": "⚔️",
    "Золото": "🏅",
    "Платина": "💎",
    "Алмаз": "🔷",
    "Мастер": "👑",
    "Легенда": "🔥",
}

# Arena module limit, and how long one batch of battles may run inside it.
# A 30-battle batch with human-like pauses takes ~15 min, so it is cut by time.
ARENA_MODULE_SECONDS = 20 * 60
ARENA_BATCH_SECONDS = 12 * 60


# ==========================================
# BOT ENGINE
# ==========================================
EVENT_CANDY_DROPS = 35  # "до 35 раз в сутки, раз в 3 минуты" on /event/pack


def event_candy_day(today):
    day = DataManager.get_setting("halloween_candy_day") or {}
    return day if day.get("date") == str(today) else {"date": str(today), "n": 0, "misses": 0}


def rerolled_out_of_squad(page_ids, saved_ids):
    """The site's squad lacks cards that only the awakening reroll removed (unawakening drops them)."""
    missing = set(saved_ids) - set(page_ids)
    if not missing or not set(page_ids) <= set(saved_ids):
        return False
    rerolled = {int(c["card_user_id"]) for c in (DataManager.get_setting("awaken_reroll") or {}).get("cards") or []}
    return missing <= rerolled


def tower_downtime_hours(now_msk, hours, cup=True):
    """Expected wait after the squad returns until the bot sends it again.

    Normally minutes (the bot wakes for the tower); during the evening cup (19-22 MSK) the bot only fights,
    so a squad back at 20:30 waits until about 22:05.
    """
    from datetime import timedelta

    back = now_msk + timedelta(hours=hours)
    wait = 0.15
    if cup and 19 <= back.hour < 22:
        wait = max(wait, (back.replace(hour=22, minute=5, second=0, microsecond=0) - back).total_seconds() / 3600)
    return wait


class ManualFailed(str):
    """A manual-run summary that reports a failure (shown as an error, not a green tick)."""


class MangaMinerBot(ArenaAutomationMixin, ClubAutomationMixin, SeasonalWatchMixin, SiteWatchMixin, HalloweenEventMixin):

    def __init__(self, log_callback, progress_callback, stats_callback, proxy=None):
        self.log = log_callback       
        self.auth_log_buffer = []
        self.update_progress = progress_callback
        self.update_stats = stats_callback
        self.running = False
        self.API_URL = "https://mangabuff.ru/mine/hit"
        self.notifier = get_notifier()
        self._anti_bot_paused_until = 0.0
        self._anti_bot_down_since = 0.0
        self._anti_bot_fail_streak = 0
        self._anti_bot_user_notified = False
        self._last_hard_alert_at = 0.0
        # Module isolation: abort current step without stopping the whole daemon.
        self._module_abort = threading.Event()
        self._heartbeat_at = time.monotonic()
        self._heartbeat_lock = threading.Lock()
        self._watchdog_started = False
        # Hung-daemon escape hatch. On a server systemd restarts the process;
        # the desktop app swaps this for an in-app restart of the bot.
        self.fatal_exit = lambda: os._exit(1)

        # Configure proxy if provided or found in settings/environment
        raw_proxy = proxy or os.getenv("MANGABUFF_PROXY") or os.getenv("PROXY") or DataManager.get_setting("proxy")
        self.proxy = normalize_proxy_url(raw_proxy)

        raw = create_raw_session(proxy=self.proxy, impersonate="chrome131")
        self.session = GuardedSession(
            raw,
            on_block=self._on_anti_bot_block,
            on_recovered=self._on_anti_bot_recovered,
            persist_cookies=self._persist_session_cookies,
            should_abort=self._module_cancelled,
            on_heartbeat=self._touch_heartbeat,
            on_csrf_expired=self._relogin_after_419,
            proxy=self.proxy,
            impersonate="chrome131",
        )
        if self.proxy:
            masked = mask_proxy_url(self.proxy)
            self.auth_log_buffer.append(f"🌐 Прокси подключен: {masked}")
        engine = "curl_cffi/chrome131" if has_curl_cffi() else "requests"
        self.auth_log_buffer.append(f"🛡️ HTTP-клиент: {engine} (обход DDoS-Guard fingerprint)")

        # Default modern browser UA (overridden after login if a saved one exists)
        self.session.headers.update({"User-Agent": CHROME_UA})
        self.email, self.password = DataManager.get_credentials()
        self.csrf_token = None
        self.user_agent = CHROME_UA
        self.user_id = None
        self.current_balance = 0
        self.diamonds_balance = 0
        self.daily_reward_claimed = False
        self._last_claimed_date = None
        self._visit_ping_done_day = None
        self._tower_next_check_at = 0.0
        self._tower_ends_at = 0.0
        self._last_notif_poll_at = 0.0
        # One-off module runs requested from the desktop app
        self._manual_queue = collections.deque()
        self.manual_callback = None

    def _persist_session_cookies(self):
        """Save current cookies/csrf/ua so DDoS-Guard tokens survive restarts."""
        if not self.csrf_token:
            return
        try:
            cookies_dict = safe_cookies_to_dict(self.session)
            if not cookies_dict:
                return
            DataManager.save_session(
                cookies_dict,
                self.csrf_token,
                self.user_agent or CHROME_UA,
            )
        except Exception:
            pass

    def _touch_heartbeat(self):
        with self._heartbeat_lock:
            self._heartbeat_at = time.monotonic()

    def _module_cancelled(self):
        return self._module_abort.is_set()

    def _alive(self):
        """True while daemon runs and current module is not aborted."""
        return bool(self.running) and not self._module_abort.is_set()

    def _start_watchdog(self):
        """Force-exit if the main loop stops heartbeating (hung curl/sleep)."""
        if self._watchdog_started:
            return
        self._watchdog_started = True
        # Monotonic clock: NTP jumps must never disable the watchdog.
        limit = 8 * 60

        def _watch():
            try:
                # Stops with the daemon so a desktop "Stop" doesn't trip it later
                while self.running:
                    time.sleep(15)
                    with self._heartbeat_lock:
                        age = time.monotonic() - self._heartbeat_at
                    if self.running and age > limit:
                        try:
                            self.log(
                                f"🛑 Watchdog: нет heartbeat {int(age)}с — "
                                f"принудительный рестарт процесса"
                            )
                        except Exception:
                            pass
                        self.fatal_exit()
                        return
            finally:
                self._watchdog_started = False

        threading.Thread(target=_watch, name="mb-watchdog", daemon=True).start()

    def _run_module(self, name, fn, timeout_sec):
        """
        Run one daemon step with a hard deadline.
        Cooperative abort via _module_abort; if still stuck after grace → process exit
        (systemd restarts the service). Session stays on the main thread.
        """
        if not self.running:
            return  # stopped: no new step, even one already scheduled in this cycle
        # A manual run requested meanwhile goes first (and may have cut the previous step short)
        if self._manual_queue and not getattr(self, "_draining", False):
            self._drain_manual_queue()
            if not self.running:
                return  # Stop came while the manual run was going
        self._module_abort.clear()
        self._touch_heartbeat()
        self._current_module = name
        self.log(f"▶️ Модуль «{name}» (лимит ~{max(1, timeout_sec // 60)} мин)")

        done = threading.Event()

        def _killer():
            if done.wait(timeout_sec):
                return
            try:
                self.log(f"⏱️ Модуль «{name}» превысил лимит — abort")
            except Exception:
                pass
            self._module_abort.set()
            try:
                self.notifier.notify_alert(
                    f"Модуль «{name}» завис",
                    f"Лимит {timeout_sec // 60} мин. Прерываю; если не отпустит — рестарт.",
                )
            except Exception:
                pass
            if done.wait(30):
                return
            try:
                self.log(f"🛑 Модуль «{name}» не остановился — рестарт процесса")
            except Exception:
                pass
            self.fatal_exit()

        killer = threading.Thread(target=_killer, name=f"mb-kill-{name}", daemon=True)
        killer.start()
        try:
            return fn()
        except Exception as e:
            self.log(f"⚠️ Модуль «{name}» ошибка: {e}")
            return None
        finally:
            done.set()
            self._module_abort.clear()
            self._touch_heartbeat()
            self._current_module = None

    def _on_anti_bot_block(self, reason, url):
        now = time.time()
        is_rate_limit = reason == "http_429"
        if not self._anti_bot_down_since:
            self._anti_bot_down_since = now
        self._anti_bot_fail_streak += 1
        # Back off hard so we don't provoke more challenges: 3 → 6 → 12 → 20 мин.
        # 429 gets a slightly softer first pause but still stops the burst.
        if is_rate_limit:
            base = 180 * (2 ** min(self._anti_bot_fail_streak - 1, 3))
            pause = min(12 * 60, base)
        else:
            pause = min(20 * 60, 180 * (2 ** min(self._anti_bot_fail_streak - 1, 3)))
        pause += random.randint(20, 60)
        self._anti_bot_paused_until = max(self._anti_bot_paused_until, now + pause)
        down_for = now - self._anti_bot_down_since
        label = "Rate-limit 429" if is_rate_limit else f"Anti-bot ({reason})"
        self.log(
            f"🛡️ {label} на {url} — пауза ~{pause // 60} мин "
            f"(подряд: {self._anti_bot_fail_streak})."
        )
        # 429 = temporary throttle, NOT a DDoS interstitial. Never spam TG for it.
        # Real challenges: alert only when actually stuck (3+ fails or ≥8 min down).
        if is_rate_limit:
            return
        stuck = self._anti_bot_fail_streak >= 3 or down_for >= 8 * 60
        if stuck and now - self._last_hard_alert_at >= 3 * 3600:
            self._last_hard_alert_at = now
            self._anti_bot_user_notified = True
            try:
                self.notifier.notify_ddos_stuck(reason, down_for / 60.0)
            except Exception:
                pass

    def _clear_anti_bot_state(self, *, silent_log=False, reason="ok"):
        told_user = self._anti_bot_user_notified
        had_block = bool(self._anti_bot_down_since or self._anti_bot_fail_streak)
        self._anti_bot_paused_until = 0.0
        self._anti_bot_down_since = 0.0
        self._anti_bot_fail_streak = 0
        self._anti_bot_user_notified = False
        # Keep hard-alert cooldown so a flapping challenge doesn't re-ping every recovery.
        if not silent_log and had_block and reason not in ("http_429", "ok", "probe_ok"):
            self.log(f"✅ Anti-bot обойдён ({reason})")
        elif not silent_log and had_block and reason == "http_429":
            self.log("✅ Rate-limit 429 снят, продолжаю.")
        if told_user:
            self._last_hard_alert_at = 0.0
            try:
                self.notifier.notify_ddos_recovered()
            except Exception:
                pass

    def _on_anti_bot_recovered(self, reason, url):
        self._clear_anti_bot_state(reason=reason or "ok")

    def _wait_if_anti_bot_paused(self):
        """Sleep out the backoff in short slices so the daemon can still stop."""
        while self.running and time.time() < self._anti_bot_paused_until:
            left = int(self._anti_bot_paused_until - time.time())
            if left <= 0:
                break
            self.log(f"⏳ Anti-bot пауза, ещё ~{max(1, left // 60)} мин. Запросы не шлю.")
            end = time.monotonic() + min(left, 30)
            while self.running and time.monotonic() < end:
                self._touch_heartbeat()
                time.sleep(0.5)

    def ensure_site_access(self):
        """Probe homepage; refresh DDoS-Guard cookies if interstitial is shown."""
        try:
            res = self.session.get("https://mangabuff.ru/", timeout=20)
            reason = is_anti_bot_page(res)
            if reason is None and getattr(res, "status_code", 0) == 200:
                self._persist_session_cookies()
                # Homepage OK must clear stale 429/challenge flags (they used to stick for days).
                if self._anti_bot_down_since or self._anti_bot_fail_streak:
                    self._clear_anti_bot_state(silent_log=True, reason="probe_ok")
                return True
            # Force an explicit refresh even if GuardedSession already tried
            if try_refresh_ddos_guard(self.session._s if isinstance(self.session, GuardedSession) else self.session):
                self._persist_session_cookies()
                self.log("✅ Доступ к сайту восстановлен (DDoS-Guard cookies).")
                if self._anti_bot_down_since or self._anti_bot_fail_streak:
                    self._clear_anti_bot_state(reason="ddos_refresh")
                return True
            self.log(f"⚠️ Нет доступа к сайту ({reason or res.status_code}). Нужен рабочий прокси.")
            return False
        except Exception as e:
            self.log(f"⚠️ ensure_site_access: {e}")
            return False

    def validate_session(self):
        data = DataManager.load_session()
        if not data or "cookies" not in data or "csrf" not in data:
            return False

        load_cookies_into(self.session, data.get("cookies") or {})
        self.csrf_token = data["csrf"]
        self.user_agent = data.get("agent") or CHROME_UA
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "X-CSRF-TOKEN": self.csrf_token,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://mangabuff.ru/mine",
            "Origin": "https://mangabuff.ru"
        })
        self.session.headers.pop("Content-Type", None)
        self.auth_log_buffer.append(tr("log_session_valid"))
        return True

    def _keep_logged_in(self, force=False):
        """The site sometimes drops our session and then serves guest pages (empty stats, dashes in the app).
        Check at most every 10 min (or when a page looked logged out) and log in again if needed."""
        if time.time() - getattr(self, "_session_checked_at", 0) < (2 * 60 if force else 10 * 60):
            return True
        self._session_checked_at = time.time()
        try:
            res = self.session.get(CONFIG["urls"]["game"], timeout=10)
        except Exception:
            return True  # network trouble is not a logout
        logged_out = res.status_code in (401, 419) or (
            res.status_code == 200 and ("/login" in str(res.url).lower() or "auth-form" in res.text.lower()))
        if not logged_out:
            return True  # fine, or the site itself is down (5xx): logging in again would not help
        self.log("♻️ Сайт сбросил вход в аккаунт — вхожу заново")
        return self.login_and_steal_keys()

    def _relogin_after_419(self):
        """The site answered 419: our session or CSRF token expired. Log in again (at most once a minute)."""
        if time.time() - getattr(self, "_last_419_relogin", 0) < 60:
            return self.csrf_token
        self._last_419_relogin = time.time()
        self.log("♻️ Сайт ответил 419 (сессия устарела) — вхожу заново и повторяю действие")
        return self.csrf_token if self.login_and_steal_keys() else None

    def adopt_cookie_session(self):
        """Check the cookies already in self.session on /balance; on success take its CSRF token
        and user id and save the session. True = logged in, False = guest, None = site unreachable."""
        res = self.session.get("https://mangabuff.ru/balance", timeout=10)
        if res.status_code in (401, 419) or (res.status_code == 200 and (
                "/login" in str(res.url).lower() or "auth-form" in res.text.lower())):
            return False
        meta = BeautifulSoup(res.text, "html.parser").find("meta", attrs={"name": "csrf-token"})
        if res.status_code != 200 or not meta or not meta.get("content"):
            return None
        self.csrf_token = meta.get("content")
        self.session.headers.update({"X-CSRF-TOKEN": self.csrf_token, "X-Requested-With": "XMLHttpRequest",
                                     "Referer": "https://mangabuff.ru/mine", "Origin": "https://mangabuff.ru"})
        user = re.search(r"/users/(\d+)", res.text)
        if user:
            self.user_id = user.group(1)
        self._persist_session_cookies()
        return True

    def refresh_profile(self):
        """Save nickname, avatar and cover from the site profile for Buffy's account card (cosmetic: never raises)."""
        try:
            if not self.user_id:
                found = re.search(r"/users/(\d+)", self.session.get("https://mangabuff.ru/balance", timeout=10).text)
                if not found:
                    return None
                self.user_id = found.group(1)
            res = self.session.get(f"https://mangabuff.ru/users/{self.user_id}", timeout=15)
            name = re.search(r'class="profile__name" data-name="([^"]*)"', res.text) if res.status_code == 200 else None
            if not name:
                return None

            def media(pattern):
                found = re.search(pattern, res.text)
                return urljoin("https://mangabuff.ru/", found.group(1)) if found else None

            profile = {
                **self._profile_theme(res.text),
                "id": self.user_id,
                "name": unescape(name.group(1)),
                "avatar": media(r'class="profile__avatar">\s*<img src="([^"]+)"'),  # often an animated gif
                "cover": media(r"class=\"profile__background\" style=\"background-image: url\('([^']+)'\)"),
                "cover_video": media(r'class="profile__cover"[^>]*>\s*<source src="([^"]+)"'),
            }
            DataManager.set_setting("account_profile", profile)
            return profile
        except Exception:
            return None

    def _profile_theme(self, page_html):
        """App colours from the profile skin. The site CSS is ~3 MB, so it is read again
        only when the skin or the site's stylesheet version changes."""
        skins, sheets = skin_classes(page_html), stylesheet_urls(page_html)
        key = "|".join(skins + sheets)
        old = DataManager.get_setting("account_profile") or {}
        if old.get("theme_key") == key:
            return {"theme": old.get("theme") or {}, "theme_key": key}
        try:
            css = [self.session.get(urljoin("https://mangabuff.ru/", url), timeout=30).text
                   for url in sheets] if skins else []
            return {"theme": theme_from_variables(skin_variables(css, skins)), "theme_key": key}
        except Exception:
            return {"theme": old.get("theme") or {}, "theme_key": old.get("theme_key")}

    def use_cookies(self, cookies, user_agent=None):
        load_cookies_into(self.session, cookies)
        if user_agent:
            self.user_agent = user_agent
            self.session.headers["User-Agent"] = user_agent

    def _restore_social_session(self):
        """VK/Discord/Yandex/Google accounts have no password: reuse the cookies from Buffy's sign-in window,
        and when they died, ask Buffy to sign in again through that window (silently if the provider allows)."""
        self.login_rejected = False
        try:
            alive = self.adopt_cookie_session() if self.validate_session() else False
            relogin = getattr(self, "social_relogin", None)  # set by Buffy; the CLI has no window
            if alive is False and relogin:
                fresh = relogin()
                if fresh:
                    self.use_cookies(*fresh)
                    alive = self.adopt_cookie_session()
        except Exception as e:
            self.auth_log_buffer.append(tr("log_login_crash", e=e))
            return False
        if alive:
            self.auth_log_buffer.append(tr("log_login_ok"))
            return True
        if alive is None:
            self.auth_log_buffer.append("⚠️ MangaBuff не ответил, вход через соцсеть проверю позже")
            return False
        self.login_rejected = True
        self.auth_log_buffer.append(
            "❌ Вход через соцсеть устарел: открой Buffy → Настройки → «Сменить аккаунт» и войди заново")
        return False

    def login_and_steal_keys(self):
        if DataManager.get_setting("login_method") == "social":
            return self._restore_social_session()
        self.auth_log_buffer.append("Logging in via API...")
        self.login_rejected = False  # True when the site answered and refused the email/password
        try:
            self.session.cookies.clear()
        except Exception:
            pass
        # Warm DDoS-Guard cookies before login (critical on datacenter IPs / flagged proxies)
        try:
            try_refresh_ddos_guard(self.session._s if isinstance(self.session, GuardedSession) else self.session)
        except Exception:
            pass
        try:
            self.user_agent = CHROME_UA
            self.session.headers.update({"User-Agent": self.user_agent})

            # 1. Initial GET to retrieve the login page (and session cookie)
            res = self.session.get(CONFIG["urls"]["login"], timeout=10)
            soup = BeautifulSoup(res.text, "html.parser")

            # Extract CSRF token from the meta tag
            meta = soup.find("meta", attrs={"name": "csrf-token"})
            if not meta or not meta.get("content"):
                self.auth_log_buffer.append(tr("log_login_fail_csrf"))
                return False
            self.csrf_token = meta.get("content")

            # 2. POST login (standard form submit)
            post_res = self.session.post(
                CONFIG["urls"]["login"],
                data={"_token": self.csrf_token, "email": self.email, "password": self.password},
                headers={
                    "Referer": "https://mangabuff.ru/login",
                    "Origin": "https://mangabuff.ru",
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                timeout=10,
                allow_redirects=True
            )

            # 3. Validation: parse JSON response ({"status": true} on success)
            try:
                response_data = post_res.json()
            except ValueError:
                self.auth_log_buffer.append(tr("log_login_fail_page"))
                return False
            if not isinstance(response_data, dict):
                response_data = {}

            if response_data.get("status") is True:
                # 4. Configure API headers for subsequent requests
                self.session.headers.update({
                    "User-Agent": self.user_agent,
                    "X-CSRF-TOKEN": self.csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://mangabuff.ru/mine",
                    "Origin": "https://mangabuff.ru"
                })
                self.session.headers.pop("Content-Type", None)

                cookies_dict = safe_cookies_to_dict(self.session)
                DataManager.save_session(cookies_dict, self.csrf_token, self.user_agent)
                self.auth_log_buffer.append(tr("log_login_ok"))
                return True
            # The site answered: a 422/401 or "status": false is a refused email/password
            self.login_rejected = True
            errors = response_data.get("errors") or {}
            first = next((v[0] if isinstance(v, list) and v else v for v in errors.values()), None) if isinstance(errors, dict) else None
            reason = first or response_data.get("message") or "Неверная почта или пароль"
            self.auth_log_buffer.append(f"❌ Сайт не принял вход: {reason}")
            return False

        except Exception as e:
            self.auth_log_buffer.append(tr("log_login_crash", e=e))
            return False

    def claim_daily_reward(self):
        """Claim the daily login reward from /balance page (monthly calendar, resets on the 1st)."""
        from datetime import datetime, timezone, timedelta
        msk_today = datetime.now(timezone(timedelta(hours=3))).date()
        if self._last_claimed_date != msk_today:
            self.daily_reward_claimed = False
        if self.daily_reward_claimed:
            return

        try:
            claim_url = self._find_active_claim_url()
            if not claim_url:
                return

            def _log_reward(txt):
                if self.running:
                    self.log(txt)
                else:
                    self.auth_log_buffer.append(txt)

            _log_reward(f"🎁 Найдена ежедневная награда: {claim_url}. Забираем...")
            claim_res = self.session.post(
                claim_url,
                data="",
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-CSRF-TOKEN": self.csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://mangabuff.ru/balance"
                },
                timeout=10
            )

            if claim_res.status_code == 422:               
                _log_reward("ℹ️ Ежедневная награда уже была забрана сегодня.")
                self.daily_reward_claimed = True
                self._last_claimed_date = msk_today
            elif claim_res.status_code == 200:                    
                msg = "✅ Ежедневная награда успешно получена!"
                self.daily_reward_claimed = True
                self._last_claimed_date = msk_today
                try:
                    res_json = claim_res.json()
                    if isinstance(res_json, dict) and res_json.get("message"):
                        msg = f"✅ Награда получена: {res_json['message']}"
                except Exception:
                    pass
                _log_reward(msg)
                self.notifier.notify_daily_reward(msg)
            else:
                _log_reward(f"⚠️ Ошибка забора ежедневной награды: статус {claim_res.status_code}")

        except Exception as e:
            err = f"⚠️ Daily reward error: {e}"
            if self.running:
                self.log(err)
            else:
                self.auth_log_buffer.append(err)

    def ping_daily_visit(self):
        """
        Maintain profile login streak («Дней подряд») via POST /visit/ping.

        MangaBuff frontend sends this once per day when opening /, /users/{id}
        or /manga/* outside 00:00–02:00 local time. Pure HTTP scraping never
        runs that JS, so the streak resets unless we ping explicitly.
        """
        from datetime import datetime, timezone, timedelta

        msk = datetime.now(timezone(timedelta(hours=3)))
        # Same quiet window as site JS (account is MSK-oriented).
        if 0 <= msk.hour < 2:
            return False

        msk_day = msk.strftime("%Y-%m-%d")
        if self._visit_ping_done_day == msk_day:
            return False
        if DataManager.get_setting("visit_ping_day") == msk_day:
            self._visit_ping_done_day = msk_day
            return False

        try:
            # Homepage is an allowed visit path; also refreshes CSRF.
            home = self.session.get("https://mangabuff.ru/", timeout=12)
            if home.status_code != 200:
                self.log(f"⚠️ Visit ping: главная HTTP {home.status_code}")
                return False
            soup = BeautifulSoup(home.text, "html.parser")
            meta = soup.find("meta", attrs={"name": "csrf-token"})
            if meta and meta.get("content"):
                self.csrf_token = meta.get("content")

            resp = self.session.post(
                "https://mangabuff.ru/visit/ping",
                data={},
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-CSRF-TOKEN": self.csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://mangabuff.ru/",
                    "Origin": "https://mangabuff.ru",
                },
                timeout=10,
            )
            if resp.status_code != 200:
                self.log(f"⚠️ Visit ping HTTP {resp.status_code}")
                return False

            status = ""
            try:
                status = str((resp.json() or {}).get("status") or "")
            except Exception:
                status = ""

            if status in ("recorded", "already"):
                self._visit_ping_done_day = msk_day
                DataManager.set_setting("visit_ping_day", msk_day)
                if status == "recorded":
                    self.log("📅 Логин-стрик: визит засчитан (/visit/ping).")
                else:
                    self.log("📅 Логин-стрик: визит за сегодня уже учтён.")
                return True

            self.log(f"⚠️ Visit ping unexpected: {resp.text[:160]}")
            return False
        except Exception as e:
            self.log(f"⚠️ Visit ping error: {e}")
            return False

    def watch_daily_ads(self):
        """Watch up to 3 daily ads on /balance page for +7 diamonds each (+21 diamonds total)."""
        if not DataManager.get_setting("ads_enabled", True):
            return
        self.log(tr("log_ads_check"))
        try:
            res = self.session.get("https://mangabuff.ru/balance", timeout=10)
            if res.status_code != 200:
                self.log(f"⚠️ Failed to open /balance for ads: {res.status_code}")
                return

            soup = BeautifulSoup(res.text, "html.parser")
            meta = soup.find("meta", attrs={"name": "csrf-token"})
            if meta and meta.get("content"):
                self.csrf_token = meta.get("content")

            btn = soup.find(class_=lambda c: c and "user-quest__watch-ads-btn" in c)
            if not btn:
                self.log("ℹ️ Daily ads button not found on /balance.")
                return

            count = int(btn.get("data-count", 0))
            if count >= 3:
                self.log(tr("log_ads_already"))
                return

            needed = 3 - count
            watched = 0
            diamonds = 0
            for i in range(needed):
                if not self.running:
                    break

                # If this is not the first ad in this session, wait for video ad duration (cooldown ~20s)
                if i > 0:
                    delay = random.uniform(21.0, 25.0)  # the site's ad cooldown is ~20 s; 18 s got 429s
                    for _ in range(int(delay * 4)):
                        if not self.running:
                            break
                        time.sleep(0.25)
                else:
                    time.sleep(random.uniform(1.0, 2.0))

                if not self.running:
                    break

                ad_res = self.session.post(
                    "https://mangabuff.ru/balance/ads",
                    data="",
                    headers={
                        "Accept": "*/*",
                        "X-CSRF-TOKEN": self.csrf_token,
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": "https://mangabuff.ru/balance"
                    },
                    timeout=10
                )

                # Handle 429 rate limit cooldown
                if ad_res.status_code == 429:
                    for _ in range(int(20 * 4)):
                        if not self.running:
                            break
                        time.sleep(0.25)
                    if not self.running:
                        break
                    ad_res = self.session.post(
                        "https://mangabuff.ru/balance/ads",
                        data="",
                        headers={
                            "Accept": "*/*",
                            "X-CSRF-TOKEN": self.csrf_token,
                            "X-Requested-With": "XMLHttpRequest",
                            "Referer": "https://mangabuff.ru/balance"
                        },
                        timeout=10
                    )

                if ad_res.status_code == 200:
                    watched += 1
                    diamonds += 7
                else:
                    self.log(f"⚠️ Ad request failed with status {ad_res.status_code}")
                    break

            if watched > 0:
                self.log(tr("log_ads_watched", count=(count + watched), diamonds=diamonds))
                self.notifier.notify_ads_watched(count + watched, diamonds)
        except Exception as e:
            self.log(f"⚠️ watch_daily_ads error: {e}")

    def get_reading_stats(self, soup=None):
        """Parse current daily chapter reading progress and card drops from /balance."""
        try:
            if soup is None:
                res = self.session.get("https://mangabuff.ru/balance", timeout=10)
                if res.status_code != 200:
                    return None
                soup = BeautifulSoup(res.text, "html.parser")

            dia_el = soup.find(class_="menu__balance")
            if dia_el:
                m_dia = re.search(r"([\d\s]+)", dia_el.get_text())
                if m_dia:
                    self.diamonds_balance = parse_smart_number(m_dia.group(1))

            chapters_read = None
            chapters_max = None
            for span in soup.find_all(string=lambda t: t and "Главы" in t):
                parent = span.parent
                grand = parent.parent if parent else None
                text = grand.get_text(strip=True) if grand else ""
                m = re.search(r"(\d+)\s*/\s*(\d+)", text)
                if m:
                    chapters_read = int(m.group(1))
                    chapters_max = int(m.group(2))
                    break

            cards_found = None
            cards_max = None
            card_ready = False
            card_cooldown = None

            drop_container = soup.find(class_="wallet-panel__drop")
            if drop_container:
                card_ready = True
                tooltip = drop_container.get("data-tooltip", "")
                m_cd = re.search(r"через\s*([^.]+)", tooltip)
                if m_cd:
                    card_cooldown = m_cd.group(1).strip()

                wait_state = drop_container.find(class_=lambda c: c and "drop-state--wait" in c)
                if wait_state or card_cooldown:
                    card_ready = False

            drop_div = soup.find(class_="wallet-panel__drop-text")
            if drop_div:
                m = re.search(r"(\d+)\s*из\s*(\d+)", drop_div.get_text())
                if m:
                    cards_found = int(m.group(1))
                    cards_max = int(m.group(2))

            # An auth/interstitial page or changed wallet markup is unavailable,
            # not evidence of an empty daily counter or a ready card.
            if (chapters_read is None or chapters_max is None or
                    cards_found is None or cards_max is None or not drop_container or
                    chapters_max <= 0 or cards_max <= 0 or
                    chapters_read > chapters_max or cards_found > cards_max):
                return None

            stats = {
                "chapters_read": chapters_read,
                "chapters_max": chapters_max,
                "cards_found": cards_found,
                "cards_max": cards_max,
                "card_ready": card_ready,
                "card_cooldown": card_cooldown,
                "card_cooldown_sec": parse_card_cooldown_seconds(card_cooldown) if card_cooldown else 0,
            }

            # Persist daily stats for Telegram /manga panel (mono_bot reads reading_state.json)
            try:
                self._persist_reading_stats(stats)
            except Exception:
                pass

            return stats
        except Exception as e:
            print(f"get_reading_stats exception: {e}")
            return None

    def _msk_now(self):
        from datetime import datetime, timezone, timedelta
        return datetime.now(timezone(timedelta(hours=3)))

    def _persist_reading_stats(self, stats):
        """Write reading/card counters + cooldown into reading_state for /manga panel."""
        if not stats:
            return
        now = self._msk_now()
        today = now.strftime("%Y-%m-%d")
        state = DataManager.load_reading_state() or {}
        prev = state.get("stats") or {}
        state["stats"] = {
            "date": today,
            "chapters_read": int(stats.get("chapters_read") or 0),
            "chapters_max": int(stats.get("chapters_max") or 75),
            "cards_found": int(stats.get("cards_found") or 0),
            "cards_max": int(stats.get("cards_max") or 10),
            "card_ready": bool(stats.get("card_ready", False)),
            "card_cooldown": stats.get("card_cooldown"),
            "card_cooldown_sec": int(stats.get("card_cooldown_sec") or 0),
            "updated_at": now.isoformat(),
            # keep current manga slug if known
            "current_manga": state.get("current_manga") or prev.get("current_manga"),
        }
        # Drop obsolete local grind field if present
        state["stats"].pop("chapters_grinded_today", None)
        DataManager.save_reading_state(state)

    def _persist_panel_state(
        self,
        *,
        next_action,
        next_wait_sec,
        energy=None,
        ads_count=None,
        tower_str=None,
        ore=None,
        diamonds=None,
        diamonds_total=None,
        chapters_str=None,
        cards_str=None,
    ):
        """Write daemon schedule snapshot for Telegram /manga panel."""
        try:
            now = self._msk_now()
            from datetime import timedelta
            wait_sec = max(0, int(next_wait_sec or 0))
            wake_at = now + timedelta(seconds=wait_sec)
            state = DataManager.load_reading_state() or {}
            prev = state.get("panel") or {}
            values = {
                "energy": energy,
                "ads_count": ads_count,
                "tower_str": tower_str,
                "ore": ore,
                "diamonds": diamonds,
                "diamonds_total": diamonds_total,
                "chapters_str": chapters_str,
                "cards_str": cards_str,
            }
            state["panel"] = {
                "next_action": next_action if next_action is not None else prev.get("next_action"),
                "next_wait_sec": wait_sec,
                "next_wake_at": wake_at.isoformat(),
                # Keep the last known value when this snapshot doesn't carry one
                **{k: (v if v is not None else prev.get(k)) for k, v in values.items()},
                "updated_at": now.isoformat(),
            }
            DataManager.save_reading_state(state)
        except Exception:
            pass

    def _peek_tower_timer_str(self):
        """Read-only tower remaining time (no claim/start)."""
        try:
            res = self.session.get("https://mangabuff.ru/tower", timeout=12)
            if res.status_code != 200:
                return None
            soup = BeautifulSoup(res.text, "html.parser")
            root = soup.find(class_="mbf-abyss-tower") or soup.find(attrs={"data-tower-root": True})
            if not root:
                root = soup.find(attrs={"data-claim-url": True})
            timer_el = soup.find(attrs={"data-tower-timer": True}) or soup.find(class_="mbf-abyss-tower__timer-value")
            if timer_el and timer_el.get("data-remaining-seconds"):
                remaining_seconds = int(timer_el.get("data-remaining-seconds"))
            elif root:
                remaining_seconds = int(root.get("data-remaining-seconds", 0) or 0)
            else:
                return None
            if remaining_seconds <= 0:
                is_complete = str((root or {}).get("data-expedition-complete", "0") if root else "0") == "1"
                return "Готово к сбору" if is_complete else "—"
            hours = remaining_seconds // 3600
            minutes = (remaining_seconds % 3600) // 60
            return f"{hours}ч {minutes}м" if hours > 0 else f"{minutes}м"
        except Exception:
            return None

    def refresh_live_panel(self):
        """
        Fetch live wallet/mine/reading/tower stats from MangaBuff and update
        reading_state for the Telegram /manga panel. No side effects (no claim/mine).
        """
        if not self.validate_session():
            if not self.login_and_steal_keys():
                return False

        energy = None
        ore = None
        ads_cnt = None
        tower_str = None
        diamonds = None
        reading_stats = None

        # Mine: ore + energy
        try:
            res = self.session.get(CONFIG["urls"]["game"], timeout=10)
            if res.status_code == 200:
                html = res.text
                b_cls = CONFIG["selectors"]["balance_class"]
                e_cls = CONFIG["selectors"]["energy_class"]
                ore_match = re.search(f'class="[^"]*{b_cls}[^"]*">\\s*([\\d\\.\\s,kKmM]+)\\s*<', html)
                hits_match = re.search(f'class="[^"]*{e_cls}[^"]*">\\s*([\\d\\s]+)\\s*<', html)
                if ore_match:
                    ore = parse_smart_number(ore_match.group(1))
                    self.current_balance = ore
                if hits_match:
                    energy = parse_smart_number(hits_match.group(1))
        except Exception:
            pass

        # Balance: diamonds, ads, reading stats
        try:
            res_bal = self.session.get("https://mangabuff.ru/balance", timeout=12)
            if res_bal.status_code == 200:
                soup_bal = BeautifulSoup(res_bal.text, "html.parser")
                ads_btn = soup_bal.find(class_=lambda c: c and "user-quest__watch-ads-btn" in c)
                if ads_btn:
                    try:
                        ads_cnt = int(ads_btn.get("data-count", 0))
                    except Exception:
                        ads_cnt = 0
                reading_stats = self.get_reading_stats(soup=soup_bal)
                dia_el = soup_bal.select_one('.menu__balance')
                if dia_el:
                    match = re.search(r'([\d\s]+)', dia_el.get_text())
                    if match:
                        diamonds = parse_smart_number(match.group(1))
        except Exception:
            pass

        if all(value is None for value in (energy, ore, ads_cnt, diamonds, reading_stats)):
            return False

        tower_str = self._peek_tower_timer_str()

        # Merge into existing panel (keep next_action / wake schedule from daemon)
        try:
            now = self._msk_now()
            state = DataManager.load_reading_state() or {}
            panel = dict(state.get("panel") or {})
            stats = state.get("stats") or {}

            if energy is not None:
                panel["energy"] = energy
            if ore is not None:
                panel["ore"] = int(ore)
            if diamonds is not None:
                panel["diamonds"] = int(diamonds)
            if ads_cnt is not None:
                panel["ads_count"] = ads_cnt
            if tower_str:
                panel["tower_str"] = tower_str

            ore_i = int(panel.get("ore") or 0)
            dia_i = int(panel.get("diamonds") or 0)
            panel["diamonds_total"] = dia_i + ore_i // 100

            if stats:
                panel["chapters_str"] = f"{stats.get('chapters_read', 0)}/{stats.get('chapters_max', 75)}"
                panel["cards_str"] = f"{stats.get('cards_found', 0)}/{stats.get('cards_max', 10)}"

            panel["updated_at"] = now.isoformat()
            if not panel.get("next_action"):
                panel["next_action"] = "—"
            state["panel"] = panel
            DataManager.save_reading_state(state)
            return True
        except Exception:
            return False

    def _download_image_bytes(self, img_path_or_url, referer="https://mangabuff.ru/", retries=3):
        """Download card/image bytes via raw session; validate magic header."""
        if not img_path_or_url:
            return None
        full_img_url = (
            f"https://mangabuff.ru{img_path_or_url}"
            if str(img_path_or_url).startswith("/")
            else img_path_or_url
        )
        raw = self.session._s if hasattr(self.session, "_s") else self.session
        last_err = None
        for attempt in range(retries):
            try:
                r_img = raw.get(
                    full_img_url,
                    timeout=20,
                    headers={
                        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                        "Referer": referer,
                    },
                )
                body = bytes(r_img.content or b"")
                if r_img.status_code == 200 and len(body) > 256:
                    head = body[:16]
                    if head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF")):
                        return body
                    if attempt == 0:
                        try:
                            try_refresh_ddos_guard(raw)
                        except Exception:
                            pass
                last_err = f"status={getattr(r_img, 'status_code', '?')} len={len(body)}"
            except Exception as e:
                last_err = str(e)
            time.sleep(0.35 * (attempt + 1))
        if last_err:
            self.log(f"⚠️ Не удалось скачать картинку: {last_err}")
        return None

    def check_trade_offers(self):
        """
        Check for incoming trade proposals on MangaBuff.
        1. Checks /notifications?type=trade and /trades.
        2. Retrieves trade preview JSON (initiator, give cards, receive cards).
        3. Sends Telegram alert with details and direct link.
        """
        try:
            trades_found = []

            # Check /notifications?type=trade
            res_notif = self.session.get("https://mangabuff.ru/notifications?type=trade", timeout=10)
            if res_notif.status_code == 200:
                soup_n = BeautifulSoup(res_notif.text, "html.parser")
                for item in soup_n.select(".notifications__item"):
                    trade_btn = item.select_one(".js-notification-trade-open") or item.select_one("[data-trade-id]")
                    trade_id = trade_btn.get("data-trade-id") if trade_btn else None
                    if not trade_id:
                        link = item.find("a", href=re.compile(r"/trades/(\d+)"))
                        if link:
                            m = re.search(r"/trades/(\d+)", link.get("href", ""))
                            if m:
                                trade_id = m.group(1)
                    if trade_id and str(trade_id) not in trades_found:
                        trades_found.append(str(trade_id))

            # Also check /trades page directly
            try:
                res_trades = self.session.get("https://mangabuff.ru/trades", timeout=10)
                if res_trades.status_code == 200 and "Предложений нет" not in res_trades.text:
                    soup_t = BeautifulSoup(res_trades.text, "html.parser")
                    for a in soup_t.find_all("a", href=re.compile(r"/trades/(\d+)")):
                        m = re.search(r"/trades/(\d+)", a.get("href", ""))
                        if m:
                            tid = m.group(1)
                            if tid not in trades_found:
                                trades_found.append(tid)
            except Exception:
                pass

            notified_list = DataManager.get_setting("notified_trade_ids", [])
            notified_set = set(str(x) for x in notified_list)
            new_trades = [tid for tid in trades_found if tid not in notified_set]

            tracked_active_trades = DataManager.get_setting("tracked_active_trades", {})

            for tid in new_trades:
                try:
                    preview_res = self.session.get(f"https://mangabuff.ru/trades/{tid}/preview", timeout=10)
                    if preview_res.status_code == 200:
                        pdata = preview_res.json()
                        user_name = pdata.get("other_user", {}).get("name", "Пользователь")

                        def _card_label(c):
                            c_name = c.get("name", "Карта")
                            c_num = c.get("copy_number")
                            if c_num and int(c_num) < 1000000:
                                num_str = f" (#{int(c_num):06d})"
                            elif c_num:
                                num_str = f" (#{c_num})"
                            else:
                                num_str = ""
                            return f"«{c_name}»{num_str}"

                        def _download_card_image(c):
                            return self._download_image_bytes(
                                c.get("image"),
                                referer="https://mangabuff.ru/trades",
                            )

                        recv_list = pdata.get("receive_cards", []) or []
                        give_list = pdata.get("give_cards", []) or []
                        recv_cards = [_card_label(c) for c in recv_list]
                        give_cards = [_card_label(c) for c in give_list]
                        recv_photos = [p for p in (_download_card_image(c) for c in recv_list) if p]
                        give_photos = [p for p in (_download_card_image(c) for c in give_list) if p]
                        if (recv_list and not recv_photos) or (give_list and not give_photos):
                            self.log(
                                f"⚠️ Обмен #{tid}: картинки частично не загрузились "
                                f"(recv {len(recv_photos)}/{len(recv_list)}, give {len(give_photos)}/{len(give_list)})"
                            )

                        delivered, detail = self.notifier.notify_trade_offer(
                            tid,
                            user_name,
                            recv_cards,
                            give_cards,
                            receive_photos=recv_photos,
                            give_photos=give_photos,
                            wait=True,
                        )
                        if not delivered:
                            self.log(f"⚠️ Обмен #{tid}: Telegram не доставлен: {detail}")
                            continue
                        self.log(f"🤝 Обнаружено предложение обмена #{tid} от {user_name}! Отправлено в Telegram.")
                        tracked_active_trades[str(tid)] = {"user_name": user_name, "last_status": pdata.get("status", "pending")}
                    else:
                        delivered, detail = self.notifier.notify_trade_offer(tid, "Пользователь", wait=True)
                        if not delivered:
                            self.log(f"⚠️ Обмен #{tid}: Telegram не доставлен: {detail}")
                            continue
                        self.log(f"🤝 Обнаружено предложение обмена #{tid}! Отправлено в Telegram.")
                        tracked_active_trades[str(tid)] = {"user_name": "Пользователь", "last_status": "pending"}
                    notified_set.add(tid)
                except Exception as ex:
                    self.log(f"⚠️ Ошибка получения информации об обмене #{tid}: {ex}")

            # Monitor tracked active trades for cancellation / acceptance
            for active_tid, t_info in list(tracked_active_trades.items()):
                if active_tid in new_trades:
                    continue
                try:
                    p_res = self.session.get(f"https://mangabuff.ru/trades/{active_tid}/preview", timeout=8)
                    if p_res.status_code == 200:
                        p_data = p_res.json()
                        st = p_data.get("status")
                        u_name = t_info.get("user_name", "Пользователь")
                        if st in ("canceled", "accepted", "rejected"):
                            self.notifier.notify_trade_status(active_tid, u_name, st)
                            self.log(f"ℹ️ Статус обмена #{active_tid} ({u_name}): {st}")
                            del tracked_active_trades[active_tid]
                    elif p_res.status_code in (404, 410):
                        del tracked_active_trades[active_tid]
                except Exception:
                    pass

            DataManager.set_setting("tracked_active_trades", tracked_active_trades)
            if new_trades:
                DataManager.set_setting("notified_trade_ids", list(notified_set))

            return trades_found
        except Exception as e:
            self.log(f"⚠️ Ошибка проверки предложений обмена: {e}")
            return []

    def clean_unwanted_notifications(self, active_trade_ids=None):
        """
        Mark as read: card drops, sharpening scrolls, promo banners, club rewards, and trade offers.
        Leaves manga chapter alerts and user comments strictly UNREAD.
        """
        if not DataManager.get_setting("notifs_cleaner_enabled", True):
            return 0

        try:
            unwanted_notif_ids = set()
            delivered_trade_ids = {str(tid) for tid in DataManager.get_setting("notified_trade_ids", [])}
            urls_to_check = [
                "https://mangabuff.ru/notifications",
                "https://mangabuff.ru/notifications?type=other",
            ]

            for url in urls_to_check:
                res = self.session.get(url, timeout=10)
                if res.status_code != 200:
                    continue
                blocked = is_anti_bot_page(res)
                if blocked:
                    self.log(f"🛡️ Уведомления недоступны — anti-bot ({blocked})")
                    return 0

                soup = BeautifulSoup(res.text, "html.parser")
                items = soup.select(".notifications__item")
                if not items:
                    continue

                for item in items:
                    notif_id = item.get("data-id")
                    if not notif_id or notif_id in unwanted_notif_ids:
                        continue

                    # Skip already read items
                    classes = item.get("class", [])
                    if not any("not-read" in c or "unread" in c for c in classes):
                        continue

                    item_text = item.get_text(strip=True, separator=" ").lower()

                    # 1. PRESERVE CHAPTERS: manga chapter alerts stay unread.
                    if any(w in item_text for w in ["глава", "главы", "главу", "глав"]):
                        continue

                    # 2. PRESERVE COMMENTS: user comments & replies stay unread.
                    if any(w in item_text for w in ["комментар", "ответил на ваш", "ответил вам"]):
                        continue

                    # Trades: mark read after Telegram alert (handled upstream).
                    is_trade = bool(
                        item.select_one(".js-notification-trade-open") or
                        item.select_one("[data-trade-id]") or
                        item.find("a", href=re.compile(r"/trades/\d+")) or
                        "обмен" in item_text or
                        "trade" in item_text
                    )
                    if is_trade:
                        trade_button = item.select_one("[data-trade-id]")
                        trade_id = trade_button.get("data-trade-id") if trade_button else None
                        if not trade_id:
                            link = item.find("a", href=re.compile(r"/trades/\d+"))
                            match = re.search(r"/trades/(\d+)", link.get("href", "")) if link else None
                            trade_id = match.group(1) if match else None
                        if trade_id and str(trade_id) in delivered_trade_ids:
                            unwanted_notif_ids.add(notif_id)
                        continue

                    # Clutter: card drops, scrolls, animated promo, club card rewards, finished tower expeditions.
                    is_card_or_scroll = any(k in item_text for k in [
                        "карту", "карта", "карты",
                        "свиток", "свитки", "заточка", "заточки",
                        "анимированн",
                    ]) and any(v in item_text for v in [
                        "получили", "выпал", "выпала", "достижен", "бесплатн", "наград", "нов"
                    ])

                    is_clutter = is_card_or_scroll or any(k in item_text for k in [
                        "получили новую карту",
                        "вы получили карту",
                        "получили бесплатную карту",
                        "бесплатную карту",
                        "достижение клуба",
                        "достижения клуба",
                        "вам выпала карта",
                        "выпала карта",
                        "получена карта",
                        "новая x карта",
                        "новая карта",
                        "новую карту",
                        "новые карты",
                        "свиток заточки",
                        "свитки заточки",
                        "благословенный свиток",
                        "заточки",
                        "заточка",
                        "анимированн",
                        "анимированная",
                        "добавлена анимированная",
                    ])

                    # Tower expedition finished — claim is handled by check_tower_expedition.
                    is_tower_done = (
                        ("экспедиц" in item_text and "башн" in item_text)
                        or "экспедиция завершилась" in item_text
                        or ("экспедиция в башне" in item_text and "наград" in item_text)
                    )

                    if is_clutter or is_tower_done:
                        unwanted_notif_ids.add(notif_id)

            read_count = 0
            headers = {
                "X-Requested-With": "XMLHttpRequest",
                "X-CSRF-TOKEN": getattr(self, "csrf_token", "")
            }
            for uid in unwanted_notif_ids:
                try:
                    read_res = self.session.post(
                        f"https://mangabuff.ru/notifications/{uid}/read",
                        data={},
                        headers=headers,
                        timeout=5
                    )
                    if read_res.status_code == 200:
                        read_count += 1
                    time.sleep(0.1)
                except Exception:
                    pass

            if read_count > 0:
                self.log(
                    f"🧹 Прочитано уведомлений MangaBuff: {read_count} шт. "
                    f"(главы манги и комментарии оставлены непрочитанными)"
                )
            return read_count

        except Exception as e:
            self.log(f"⚠️ Ошибка обработки уведомлений MangaBuff: {e}")
            return 0

    def process_mangabuff_notifications(self):
        """Parse notifications tab for trade offers and clean unwanted site notifications."""
        active_trades = self.check_trade_offers()
        return self.clean_unwanted_notifications(active_trade_ids=active_trades)

    def get_popular_manga_slugs(self):
        """Fetch top manga slugs dynamically from /manga/top with verified fallback."""
        verified = [
            "elised",
            "svinarnik",
            "mech-razyashchego-groma",
            "geroi-vernulsya",
            "slabeishii-geroi",
            "plamya-beschislennyh-nevzgod",
            "ona-moya",
            "garem-iz-muzhchin",
            "temnyi-demon",
            "vetrolom-2013",
            "ohotnik-sss-urovnya",
            "borba-v-pryamom-efire",
            "karti-istinnoe-obrazovanie",
            "karti-odnazhdy-ya-stala-princessoi",
            "ya-stal-grafskim-ublyudkom",
            "klinok-rassekayushchii-demonov",
            "vsevedushchii-chitatel",
            "tenkaichi-turnir-silneishih-masterov-boevyh-iskusstv-yaponii",
            "belaya-krov",
            "nesravnennaya-chu-e-su",
            "plach-nochnoi-vorony",
            "kovarnyi-plan-muzhchiny-ili-kak-zavoevat-serdce",
            "ya-podruzhilsya-so-vtoroi-samoi-krasivoi-devushkoi-v-klasse",
            "operaciya-nastoyashchaya-lyubov",
            "zlodeika-perevernuvshaya-pesochnye-chasy",
            "studiya-kabana",
            "sistema-vsemogushchego-dizainera",
            "plan-pererozhdennogo-naemnika",
            "vyberi-menya",
            "rycar-zhivushchii-odnim-dnem"
        ]
        try:
            res = self.session.get("https://mangabuff.ru/manga/top", timeout=10)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                slugs = [
                    re.sub(r"https?://mangabuff\.ru/manga/", "", a["href"]).split("/")[0]
                    for a in soup.find_all("a", href=True)
                    if "/manga/" in a["href"] and not a["href"].endswith("/top")
                ]
                slugs = list(dict.fromkeys(s for s in slugs if s and not s.startswith("top") and "/" not in s))
                if "elised" in slugs:
                    slugs.remove("elised")
                    slugs.insert(0, "elised")
                elif verified:
                    slugs.insert(0, "elised")
                if len(slugs) >= 15:
                    return slugs
        except Exception:
            pass
        return verified

    def read_manga_chapters(self, target_count=None, for_event=False):
        """Read manga chapters to farm bonus cards, sharpening scrolls, and 75-chapter daily quest."""
        # Below the daily cap count only growth confirmed by /balance.
        # At the cap it cannot grow; retain submitted reads for card/event pacing.
        self._last_read_count = 0
        if not DataManager.get_setting("reading_enabled", True):
            return

        if not self._alive():
            return

        self.log(tr("log_reading_check"))
        self._touch_heartbeat()
        stats = self.get_reading_stats()
        if not stats:
            self.log("⚠️ Статистика чтения недоступна — жду корректную страницу /balance.")
            return False
        initial_cards = stats["cards_found"]
        if stats and not for_event:  # event reading goes past the daily quest on purpose
            if stats["chapters_read"] >= stats["chapters_max"] and stats["cards_found"] >= stats["cards_max"]:
                self.log(tr("log_reading_limit_reached", chapters=stats["chapters_read"], cards=stats["cards_found"]))
                return

            # Card cooldown only blocks reading IF the daily 75 chapters are already finished!
            wait_card_cd = DataManager.get_setting("reading_wait_card_cooldown", False)
            if stats["chapters_read"] >= stats["chapters_max"] and not stats.get("card_ready", False):
                cd_msg = stats.get("card_cooldown") or "ожидание"
                self.log(f"⏳ 75 глав уже закрыто, а бонусные карты на кулдауне ({cd_msg}). Пропуск чтения глав.")
                return

        limit_setting = int(DataManager.get_setting("reading_chapters_limit", 20))
        if target_count is None:
            target_count = limit_setting

        remaining_today = max(0, stats["chapters_max"] - stats["chapters_read"]) if stats else 75
        if remaining_today > 0:
            target_count = min(target_count, remaining_today)
        elif not for_event:
            if stats and stats["cards_found"] >= stats["cards_max"]:
                self.log(tr("log_reading_limit_reached", chapters=stats["chapters_read"], cards=stats["cards_found"]))
                return

        delay_min = float(DataManager.get_setting("reading_delay_min", 18.0))
        delay_max = float(DataManager.get_setting("reading_delay_max", 32.0))

        state = DataManager.load_reading_state() or {}
        history_order = list(dict.fromkeys(state.get("history", [])))
        read_history = set(history_order)
        progress_map = state.get("progress", {})

        manga_list = self.get_popular_manga_slugs()
        if not manga_list:
            self.log("⚠️ Нет доступных тайтлов для чтения — повторю позже.")
            return False
        current_manga = state.get("current_manga", "elised")
        if current_manga in manga_list:
            manga_idx = manga_list.index(current_manga)
        else:
            manga_idx = 0
            current_manga = manga_list[0] if manga_list else "elised"

        chapters_read_session = 0
        cards_gained_session = 0
        scrolls_gained_session = 0
        buffer = []
        reading_run = uuid.uuid4().hex[:12]
        reading_batch = 0

        try:
            attempts = 0
            stalled_titles = 0
            while chapters_read_session < target_count and self._alive() and attempts < len(manga_list):
                self._touch_heartbeat()
                current_slug = manga_list[manga_idx % len(manga_list)]
                attempts += 1

                title_url = f"https://mangabuff.ru/manga/{current_slug}"
                try:
                    res_title = self.session.get(title_url, timeout=10)
                except Exception as e:
                    self.log(f"⚠️ Manga title fetch fail «{current_slug}»: {e}")
                    manga_idx = (manga_idx + 1) % len(manga_list)
                    continue
                if res_title.status_code != 200:
                    manga_idx = (manga_idx + 1) % len(manga_list)
                    state["current_manga"] = manga_list[manga_idx]
                    state["manga_idx"] = manga_idx
                    DataManager.save_reading_state(state)
                    continue

                soup_title = BeautifulSoup(res_title.text, "html.parser")
                ch_links = []
                pattern = re.compile(rf"/manga/{re.escape(current_slug)}/(\d+)/([\d.]+)")
                for a in soup_title.find_all("a", href=True):
                    href = a["href"]
                    m = pattern.search(href)
                    if m:
                        vol = int(m.group(1))
                        ch = float(m.group(2))
                        full_url = href if href.startswith("http") else f"https://mangabuff.ru{href}"
                        ch_links.append((vol, ch, full_url))

                # Load remaining chapters if loaded via AJAX (load-chapters-trigger)
                manga_el = soup_title.find(class_="manga")
                manga_id = manga_el.get("data-id") if manga_el else None
                if manga_id and soup_title.find(class_="load-chapters-trigger"):
                    try:
                        load_headers = {
                            "Accept": "*/*",
                            "X-CSRF-TOKEN": self.csrf_token,
                            "X-Requested-With": "XMLHttpRequest",
                            "Referer": title_url,
                            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
                        }
                        load_res = self.session.post(
                            "https://mangabuff.ru/chapters/load",
                            data={"manga_id": manga_id},
                            headers=load_headers,
                            timeout=10
                        )
                        if load_res.status_code == 200:
                            load_data = load_res.json()
                            content = load_data.get("content", "")
                            if content:
                                soup_more = BeautifulSoup(content, "html.parser")
                                for a in soup_more.find_all("a", href=True):
                                    href = a["href"]
                                    m = pattern.search(href)
                                    if m:
                                        vol = int(m.group(1))
                                        ch = float(m.group(2))
                                        full_url = href if href.startswith("http") else f"https://mangabuff.ru{href}"
                                        ch_links.append((vol, ch, full_url))
                    except Exception:
                        pass

                # Deduplicate and sort chronologically by (volume, chapter)
                ch_links = sorted(list(set(ch_links)), key=lambda x: (x[0], x[1]))

                # Check saved sequential progress for this manga
                prog = progress_map.get(current_slug, {})
                last_v = int(prog.get("vol", 0) or 0)
                last_c = float(prog.get("ch", 0) or 0)

                # Strict forward-only: NEVER fall back to older chapters.
                # Old bug: history truncated to 2000 → bot re-read early chapters,
                # site daily counter stayed frozen (re-reads don't count).
                forward = [
                    item for item in ch_links
                    if item[0] > last_v or (item[0] == last_v and item[1] > last_c)
                ]
                if not forward:
                    self.log(f"📖 «{current_slug}» пройден до конца (т.{last_v} гл.{last_c}) — следующий тайтл.")
                    manga_idx = (manga_idx + 1) % len(manga_list)
                    state["current_manga"] = manga_list[manga_idx]
                    state["manga_idx"] = manga_idx
                    DataManager.save_reading_state(state)
                    continue

                unread_chapters = [item for item in forward if item[2] not in read_history]
                if not unread_chapters:
                    # Forward chapters already in local history — jump progress to the end
                    # of known forward list so we don't loop forever.
                    end_v, end_c, _ = forward[-1]
                    progress_map[current_slug] = {"vol": end_v, "ch": end_c}
                    state["progress"] = progress_map
                    manga_idx = (manga_idx + 1) % len(manga_list)
                    state["current_manga"] = manga_list[manga_idx]
                    state["manga_idx"] = manga_idx
                    DataManager.save_reading_state(state)
                    self.log(
                        f"📖 «{current_slug}»: все следующие главы уже в истории, "
                        f"прогресс → т.{end_v} гл.{end_c}, беру следующий тайтл."
                    )
                    continue

                skip_bad = 0
                stale_site_batches = 0
                title_stalled = False
                title_credited = False
                site_chapters_before = (stats or {}).get("chapters_read")
                for vol, ch, ch_url in unread_chapters:
                    if chapters_read_session >= target_count or not self._alive():
                        break

                    try:
                        ch_res = self.session.get(ch_url, timeout=10)
                    except Exception as e:
                        self.log(f"⚠️ Chapter fetch fail: {e}")
                        skip_bad += 1
                        if skip_bad >= 25:
                            self.log(f"⚠️ Слишком много битых глав в «{current_slug}», переключаюсь.")
                            break
                        continue
                    if ch_res.status_code != 200:
                        skip_bad += 1
                        if skip_bad >= 25:
                            self.log(f"⚠️ Слишком много HTTP-ошибок глав в «{current_slug}», переключаюсь.")
                            break
                        continue

                    if re.search(r"window\.isAuth\s*=\s*(?:0|false)\s*;", ch_res.text):
                        self.log("⚠️ Глава открылась как гостю — чтение приостановлено до восстановления входа.")
                        return False

                    m_ch = re.search(r"window\.current_chapter\s*=\s*({[^;]+});", ch_res.text)
                    if not m_ch:
                        skip_bad += 1
                        if skip_bad == 1 or skip_bad % 10 == 0:
                            self.log(f"⚠️ Нет current_chapter у «{current_slug}» т.{vol} гл.{ch} (skip {skip_bad})")
                        if skip_bad >= 25:
                            self.log(f"⚠️ «{current_slug}» не отдаёт главы — беру следующий тайтл.")
                            break
                        continue

                    try:
                        ch_data = json.loads(m_ch.group(1))
                        manga_id = ch_data.get("id")
                        chapter_id = ch_data.get("chapter_id")
                    except Exception:
                        skip_bad += 1
                        continue

                    if not manga_id or not chapter_id:
                        skip_bad += 1
                        continue

                    skip_bad = 0  # found a readable chapter

                    meta_csrf = BeautifulSoup(ch_res.text, "html.parser").find("meta", attrs={"name": "csrf-token"})
                    if meta_csrf and meta_csrf.get("content"):
                        self.csrf_token = meta_csrf.get("content")

                    # Humanized reading delay (simulates page-turning and reading time)
                    delay = random.uniform(delay_min, delay_max)
                    self.log(f"📖 Читаем «{current_slug}» (т.{vol} гл.{ch})... имитация чтения ({delay:.0f}с)")
                    for _ in range(int(delay * 4)):
                        if not self._alive():
                            break
                        self._touch_heartbeat()
                        time.sleep(0.25)

                    if not self._alive():
                        break

                    buffer.append({
                        "manga_id": manga_id,
                        "chapter_id": chapter_id,
                        "url": ch_url,
                        "vol": vol,
                        "ch": ch,
                        "slug": current_slug
                    })

                    # Batch submit (every 1-2 chapters)
                    if (len(buffer) >= 2 or (chapters_read_session + len(buffer)) >= target_count
                            or ch_url == unread_chapters[-1][2]):
                        payload = {}
                        for bi, b_item in enumerate(buffer):
                            payload[f"items[{bi}][manga_id]"] = b_item["manga_id"]
                            payload[f"items[{bi}][chapter_id]"] = b_item["chapter_id"]

                        headers = {
                            "Accept": "*/*",
                            "X-CSRF-TOKEN": self.csrf_token,
                            "X-Requested-With": "XMLHttpRequest",
                            "Referer": buffer[-1]["url"],
                            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
                        }

                        try:
                            reading_batch += 1
                            request_started = time.monotonic()
                            post_res = self.session.post(
                                "https://mangabuff.ru/addHistory?r=702",
                                data=payload,
                                headers=headers,
                                timeout=10
                            )
                        except Exception as e:
                            self.log(f"⚠️ addHistory fail run={reading_run} batch={reading_batch}: {mask_secrets(str(e))}")
                            # An ambiguous response is not permission to skip ahead.
                            return False

                        secrets = (*DataManager.get_credentials(), DataManager.get_setting('proxy', ''))
                        self.log('🔎 reading_response ' + json.dumps({
                            'run': reading_run, 'batch': reading_batch,
                            'chapters': [{'manga': b['slug'], 'vol': b['vol'], 'ch': b['ch']} for b in buffer],
                            'elapsed_ms': round((time.monotonic() - request_started) * 1000),
                            'response': reading_response_details(post_res, secrets),
                        }, ensure_ascii=False))

                        # Handle rate limit (429) backoff — do NOT count as read
                        if post_res.status_code == 429:
                            retry_after = int(post_res.headers.get("Retry-After", 60))
                            wait_429 = max(45, min(180, retry_after + 5))
                            self.log(
                                f"⏳ addHistory 429 Too Many Attempts — пауза {wait_429}с, "
                                f"чтение глав останавливаю (чтобы не жечь лимит)."
                            )
                            for _ in range(int(wait_429 * 4)):
                                if not self._alive():
                                    break
                                self._touch_heartbeat()
                                time.sleep(0.25)
                            buffer.clear()
                            return False

                        if post_res.status_code == 200:
                            # The reader accepts empty/text responses when no reward
                            # drops. Reject HTML/login pages and explicit API errors;
                            # /balance below verifies actual daily credit separately.
                            body = post_res.text.strip()
                            if body.startswith("<"):
                                self.log("⚠️ addHistory вернул HTML вместо ответа API — повторю позже.")
                                return False
                            try:
                                resp_data = json.loads(body) if body else None
                            except (ValueError, TypeError):
                                resp_data = None
                            if (resp_data is False or (isinstance(resp_data, dict) and
                                    (resp_data.get("success") is False or resp_data.get("error")
                                     or resp_data.get("status") == "error"))):
                                self.log("⚠️ Сайт отклонил запись истории — прогресс не изменён, повторю позже.")
                                return False

                            live = self.get_reading_stats()
                            credited = 0
                            if live:
                                if site_chapters_before >= stats["chapters_max"]:
                                    credited = len(buffer)
                                else:
                                    credited = min(len(buffer), max(0, live["chapters_read"] - site_chapters_before))
                            chapters_read_session += credited
                            title_credited = title_credited or credited > 0
                            self._last_read_count = chapters_read_session
                            self.log('🔎 reading_credit ' + json.dumps({
                                'run': reading_run, 'batch': reading_batch,
                                'before': site_chapters_before,
                                'after': live['chapters_read'] if live else None,
                                'cap': stats['chapters_max'], 'counted': credited,
                                'count_mode': 'submitted_at_cap' if site_chapters_before >= stats['chapters_max'] else 'daily_delta',
                            }, ensure_ascii=False))
                            # Keep the submitted cursor even for re-reads; otherwise
                            # accounts with missing local history retry the same chapters.
                            for b in buffer:
                                if b["url"] not in read_history:
                                    history_order.append(b["url"])
                                read_history.add(b["url"])
                                progress_map[b["slug"]] = {"vol": b["vol"], "ch": b["ch"]}

                            state["current_manga"] = current_slug
                            state["manga_idx"] = manga_idx
                            state["progress"] = progress_map
                            state["history"] = history_order[-8000:]
                            DataManager.save_reading_state(state)

                            # Parse drop responses
                            try:
                                if isinstance(resp_data, dict) and resp_data.get("token") and resp_data.get("type"):
                                    self._take_event_drop(resp_data, headers)
                                if isinstance(resp_data, dict) and resp_data.get("name"):
                                    card_name = resp_data.get("name")
                                    card_img = resp_data.get("image")
                                    cards_gained_session += 1
                                    current_cards = initial_cards + cards_gained_session

                                    # Extract copy number
                                    copy_num = (
                                        resp_data.get("copy_number")
                                        or resp_data.get("copy")
                                        or resp_data.get("number")
                                        or (resp_data.get("card", {}).get("copy_number") if isinstance(resp_data.get("card"), dict) else None)
                                    )
                                    if not copy_num or not card_img:
                                        fetched_num, fetched_img = self._fetch_latest_card_details(card_name)
                                        if not copy_num:
                                            copy_num = fetched_num
                                        if not card_img:
                                            card_img = fetched_img

                                    copy_info = classify_card_copy_number(copy_num) if copy_num else None

                                    if copy_info and copy_info.get("copy_number"):
                                        num_str = f"#{copy_info['copy_number']:06d}" if copy_info['copy_number'] < 1000000 else f"#{copy_info['copy_number']}"
                                        special_tag = " 🔥 [УНИКАЛЬНЫЙ НОМЕР]" if copy_info.get("is_special") else ""
                                        self.log(f"🃏 ВЫПАЛА КАРТА: «{card_name}» ({num_str} — «{copy_info['title']}»{special_tag})! ({current_cards}/10 сегодня)")
                                    else:
                                        self.log(f"🃏 ВЫПАЛА КАРТА: «{card_name}»! ({current_cards}/10 сегодня)")

                                    # Download card preview to send as real photo to Telegram
                                    photo_bytes = None
                                    if card_img:
                                        photo_bytes = self._download_image_bytes(
                                            card_img,
                                            referer="https://mangabuff.ru/",
                                        )

                                    self.notifier.notify_card_dropped(
                                        card_name,
                                        card_img,
                                        current_cards,
                                        photo_bytes=photo_bytes,
                                        copy_info=copy_info,
                                        user_id=self.user_id
                                    )
                                    wait_card_cd = (stats["chapters_read"] >= stats["chapters_max"] or
                                                    DataManager.get_setting("reading_wait_card_cooldown", True))
                                    if wait_card_cd:
                                        live = None
                                        try:
                                            live = self.get_reading_stats()
                                        except Exception:
                                            live = None
                                        cards_now = (live or {}).get("cards_found", current_cards)
                                        cards_cap = (live or {}).get("cards_max", 10)
                                        ch_now = (live or {}).get("chapters_read")
                                        ch_cap = (live or {}).get("chapters_max", 75)
                                        if ch_now is None:
                                            ch_now = (stats or {}).get("chapters_read")
                                            ch_cap = (stats or {}).get("chapters_max", 75)
                                        if cards_now >= cards_cap:
                                            self.log("🎁 Бонусная карта получена, дневной лимит карт закрыт.")
                                            buffer.clear()
                                            return True
                                        self.log(
                                            f"🎁 Бонусная карта получена ({cards_now}/{cards_cap})! "
                                            f"Кулдаун ~45–60 мин (на сайте глав {ch_now}/{ch_cap})."
                                        )
                                        buffer.clear()
                                        return True

                                if isinstance(resp_data, dict) and resp_data.get("scroll"):
                                    scroll_name = resp_data.get("scroll", "Свиток заточки")
                                    scrolls_gained_session += 1
                                    self.log(f"📜 ВЫПАЛ СВИТОК: «{scroll_name}»!")
                                    self.notifier.notify_scroll_dropped(scroll_name, resp_data.get("rank"))
                            except Exception:
                                pass

                            # Process the addHistory reward first: a capped chapter
                            # counter cannot tell us whether the card roll succeeded.
                            if not live:
                                self.log("⚠️ Статистика после чтения недоступна — продолжу в следующем цикле.")
                                buffer.clear()
                                return False
                            now_ch = live["chapters_read"]
                            now_cap = live["chapters_max"]
                            stats = live
                            if now_ch >= now_cap:
                                stale_site_batches = 0
                                if live["cards_found"] >= live["cards_max"] or not live["card_ready"]:
                                    buffer.clear()
                                    return True
                            elif site_chapters_before is not None and now_ch <= site_chapters_before:
                                stale_site_batches += 1
                                if stale_site_batches >= 2:
                                    self.log(
                                        f"📖 «{current_slug}»: две пачки без роста счётчика "
                                        f"({now_ch}/{now_cap}); возможно, главы уже прочитаны. Беру другой тайтл."
                                    )
                                    title_stalled = True
                                    buffer.clear()
                                    break
                            else:
                                stale_site_batches = 0
                            site_chapters_before = now_ch

                            if credited:
                                self.log(tr("log_reading_batch", count=chapters_read_session, current=chapters_read_session, total=target_count))
                            else:
                                self.log(f"📖 История отправлена, но счётчик глав пока не вырос ({now_ch}/{now_cap}).")

                        else:
                            self.log(f"⚠️ Ошибка отправки истории (HTTP {post_res.status_code})")
                            return False
                        buffer.clear()

                if chapters_read_session < target_count and self._alive():
                    manga_idx = (manga_idx + 1) % len(manga_list)
                    state["current_manga"] = manga_list[manga_idx]
                    state["manga_idx"] = manga_idx
                    DataManager.save_reading_state(state)
                # A trailing stale batch after real growth is not a stalled title.
                if title_stalled or (stale_site_batches and not title_credited):
                    stalled_titles += 1
                    if stalled_titles >= 2:
                        self.log("🛑 На двух тайтлах нет роста счётчика. Чтение приостановлено; следующий цикл начну с другого тайтла.")
                        return False

            state["current_manga"] = manga_list[manga_idx % len(manga_list)]
            state["manga_idx"] = manga_idx
            state["progress"] = progress_map
            state["history"] = history_order[-8000:]
            DataManager.save_reading_state(state)

            if chapters_read_session > 0:
                final_stats = self.get_reading_stats()
                final_today = final_stats["chapters_read"] if final_stats else None
                self.log(tr("log_reading_summary", chapters=chapters_read_session, cards=cards_gained_session))
                if cards_gained_session > 0 or scrolls_gained_session > 0:
                    self.notifier.notify_reading_summary(
                        chapters_read_session,
                        total_today=final_today,
                        cards_gained=cards_gained_session,
                        scrolls_gained=scrolls_gained_session
                    )
            return False
        except Exception as e:
            self.log(f"⚠️ read_manga_chapters error: {e}")
            return False

    def _event_candies_wanted(self):
        """Keep reading for Halloween candies until today's drops are in, while the event page is live."""
        info = DataManager.get_setting("halloween_event_info")
        if not info or not DataManager.get_setting("halloween_event_enabled", True)                 or not DataManager.get_setting("reading_enabled", True):
            return False
        day = event_candy_day(self._msk_now().date())
        # 6 reads in a row without a drop: the site gives none now, don't read for nothing
        return day["n"] < EVENT_CANDY_DROPS and day.get("misses", 0) < 6

    def _take_event_drop(self, drop, headers):
        """Event candy/bag that a read chapter dropped: the site page sends its token to /halloween/takeCandy."""
        try:
            r = self.session.post("https://mangabuff.ru/halloween/takeCandy?r=822",
                                  data={"token": drop["token"]}, headers=headers, timeout=10)
            what = "мешок конфет" if drop.get("type") == "pumpkin" else "конфету"
            if r.status_code == 200:
                day = event_candy_day(self._msk_now().date())
                day["n"] += 1
                DataManager.set_setting("halloween_candy_day", day)
                info = DataManager.get_setting("halloween_event_info") or {}
                if info.get("balance") is not None:  # the page is re-read every 30 min; keep the count live meanwhile
                    info["balance"] += 3 if drop.get("type") == "pumpkin" else 1
                    DataManager.set_setting("halloween_event_info", info)
                self.log(f"🍬 Ивент: подобрал {what} за чтение ({day['n']}/{EVENT_CANDY_DROPS} за сегодня)")
            else:
                self.log(f"⚠️ Ивент: не удалось подобрать {what} (HTTP {r.status_code})")
        except Exception as e:
            self.log(f"⚠️ Ивент: ошибка при сборе конфеты: {e}")

    def _fetch_latest_card_details(self, card_name=None):
        """Fetch the copy number and image URL of the newest card in user inventory."""
        try:
            if not self.user_id:
                res_bal = self.session.get("https://mangabuff.ru/balance", timeout=8)
                if res_bal.status_code == 200:
                    m = re.search(r'/users/(\d+)', res_bal.text)
                    if m:
                        self.user_id = m.group(1)

            if not self.user_id:
                return None, None

            cards_url = f"https://mangabuff.ru/users/{self.user_id}/cards?sort=new"
            res_c = self.session.get(cards_url, timeout=8)
            if res_c.status_code == 200:
                soup = BeautifulSoup(res_c.text, "html.parser")
                items = soup.find_all(class_="manga-cards__item")
                for item in items[:5]:
                    c_name = item.get("data-name", "")
                    c_num = item.get("data-copy-number")
                    if not card_name or (card_name.lower() in c_name.lower() or c_name.lower() in card_name.lower()):
                        img_el = item.find(class_=re.compile(r"manga-cards__image"))
                        img_url = img_el.get("data-src") if img_el else None
                        copy_int = int(c_num) if c_num and str(c_num).isdigit() else None
                        return copy_int, img_url
                if items:
                    c_num = items[0].get("data-copy-number")
                    img_el = items[0].find(class_=re.compile(r"manga-cards__image"))
                    img_url = img_el.get("data-src") if img_el else None
                    copy_int = int(c_num) if c_num and str(c_num).isdigit() else None
                    return copy_int, img_url
        except Exception as e:
            self.log(f"⚠️ Ошибка получения инфо карты из инвентаря: {e}")
        return None, None

    def _fetch_latest_card_copy_number(self, card_name=None):
        num, _ = self._fetch_latest_card_details(card_name)
        return num

    def check_and_claim_quests(self):
        """Parse all daily quests on /battle and claim any completed unclaimed quests."""
        try:
            res = self.session.get("https://mangabuff.ru/battle", timeout=10)
            if res.status_code != 200:
                return
            soup = BeautifulSoup(res.text, "html.parser")

            root = soup.find(class_="battle-home")
            claim_url = root.get("data-daily-claim-url") if root else "https://mangabuff.ru/battle/daily-quests/claim"

            claimed_count = 0
            last_essence = None
            last_energy = None

            for article in soup.find_all("article"):
                qid = article.get("data-daily-quest-id")
                if not qid:
                    continue
                classes = article.get("class", [])
                completed = "battle-home__quest--completed" in classes
                claimed = "battle-home__quest--claimed" in classes

                if completed and not claimed:
                    data = self.claim_quest(qid, claim_url)
                    if data and data.get("success"):
                        claimed_count += 1
                        last_essence = data.get("essence")
                        last_energy = data.get("awakening_energy")
                        time.sleep(random.uniform(1.0, 2.0))

            if claimed_count > 0:
                self.log(tr("log_quests_claimed", count=claimed_count))
                self.notifier.notify_quests_claimed(claimed_count, last_essence, last_energy)
        except Exception as e:
            print(f"check_and_claim_quests exception: {e}")

    def claim_battle_chests(self):
        """Open owned battle chests sequentially; never purchase packs or chests."""
        from urllib.parse import urljoin, urlparse
        from html import escape

        opened = 0
        if not self._alive():
            return opened
        try:
            page = self.session.get("https://mangabuff.ru/battle", timeout=10)
            if page.status_code != 200:
                return opened
            soup = BeautifulSoup(page.text, "html.parser")
            root = soup.find(attrs={"data-chest-open-url": True})
            token = soup.find("meta", attrs={"name": "csrf-token"})
            if not root or not token or not token.get("content"):
                return opened
            url = urljoin("https://mangabuff.ru/battle", root["data-chest-open-url"])
            parsed = urlparse(url)
            if (parsed.scheme != "https" or parsed.netloc != "mangabuff.ru" or
                    parsed.path != "/chests/open" or parsed.query or parsed.fragment):
                self.log("⚠️ Неизвестный маршрут боевых сундуков — пропуск.")
                return opened
            self.csrf_token = token["content"]
            headers = {
                "X-CSRF-TOKEN": self.csrf_token,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://mangabuff.ru/battle",
                "Accept": "application/json",
            }
            seen = set()
            for button in root.select("[data-chest-open][data-chest-code][data-chest-amount]"):
                code = button["data-chest-code"]
                amount = str(button["data-chest-amount"])
                if (code in seen or not re.fullmatch(r"battle_[a-z0-9_]+_chest", code) or
                        not amount.isdigit() or button.has_attr("disabled")):
                    continue
                seen.add(code)
                # Bounded work per daemon cycle; remaining inventory is collected next cycle.
                for _ in range(min(int(amount), 20 - opened)):
                    if not self._alive():
                        return opened
                    self._touch_heartbeat()
                    response = self.session.post(url, data={"code": code, "_token": self.csrf_token},
                                                 headers=headers, timeout=15)
                    if response.status_code != 200:
                        self.log(f"⚠️ Открытие боевого сундука HTTP {response.status_code}; остановка до следующего цикла.")
                        return opened
                    data = response.json()
                    if not isinstance(data, dict) or data.get("success") is not True:
                        self.log("⚠️ Сайт не подтвердил открытие боевого сундука; остановка.")
                        return opened
                    opened += 1
                    name = button.get("data-chest-name") or "Боевой сундук"
                    self.log(f"🎁 Открыт {name}.")
                    labels = {"coins": "Алмазики", "random_scroll": "Заточки", "scroll": "Заточки",
                              "random_scroll_100": "Заточки 100%", "random_card": "Карты", "essence": "Эссенция"}
                    rewards = data.get("rewards")
                    lines = []
                    for reward in rewards if isinstance(rewards, list) else []:
                        if isinstance(reward, dict):
                            label = labels.get(reward.get("type"), reward.get("type") or "Награда")
                            lines.append(f"{escape(str(label))}: {escape(str(reward.get('amount', 1)))}")
                    try:
                        self.notifier.send_message(f"🎁 <b>{escape(name)}</b> открыт" +
                                                   ("\n" + "\n".join(lines) if lines else ""))
                    except Exception as exc:
                        self.log(f"⚠️ Уведомление о сундуке: {exc}")
                    for _ in range(8):
                        if not self._alive():
                            return opened
                        self._touch_heartbeat()
                        time.sleep(0.25)
            return opened
        except Exception as exc:
            # Never retry an ambiguous POST immediately; reread inventory next cycle.
            self.log(f"⚠️ Сбор боевых сундуков остановлен: {exc}")
            return opened

    def claim_quest(self, quest_id, claim_url="https://mangabuff.ru/battle/daily-quests/claim"):
        """Claim a single completed daily quest using application/x-www-form-urlencoded."""
        if not quest_id:
            return None
        try:
            payload = {
                "_token": self.csrf_token,
                "user_daily_quest_id": quest_id
            }
            headers = {
                "X-CSRF-TOKEN": self.csrf_token,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://mangabuff.ru/battle",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
            }
            res = self.session.post(claim_url, data=payload, headers=headers, timeout=10)
            if res.status_code == 200:
                try:
                    return res.json()
                except ValueError:
                    return None
            return None
        except Exception as e:
            print(f"claim_quest({quest_id}) exception: {e}")
            return None

    def auto_upgrade_pickaxe(self):
        """Automatically check and buy pickaxe upgrade and strong hit if ore balance allows."""
        if not DataManager.get_setting("auto_upgrade", True):
            return False

        try:
            res = self.session.get(CONFIG["urls"]["game"], timeout=10)
            if res.status_code != 200:
                return False
            html = res.text

            # 1. Check for "Сильный удар" (Strong Hit, 50k ore)
            if 'mine-shop__hit-power-btn' in html and self.current_balance >= 50000:
                buy_res = self.session.post(
                    "https://mangabuff.ru/mine/buy-strong-hit",
                    data={},
                    headers={"X-CSRF-TOKEN": self.csrf_token, "X-Requested-With": "XMLHttpRequest"},
                    timeout=10
                )
                if buy_res.status_code == 200:
                    self.log(tr("log_strong_hit_ok"))
                    self.notifier.notify_upgrade("Сильный удар (25 ударов/клик)", cost=50000)
                    self.current_balance -= 50000
                    self.update_stats(balance=self.current_balance)

            # 2. Check for Pickaxe Upgrade (max lvl 15)
            lvl_match = re.search(r'mine-shop__card--upgrade.*?Текущий уровень:\s*<b>?(\d+)</b>?', html, re.DOTALL | re.IGNORECASE)
            current_lvl = int(lvl_match.group(1)) if lvl_match else None

            if 'mine-shop__upgrade-btn' in html and (current_lvl is None or current_lvl < 15):
                cost = (2000 + current_lvl * 1000) if current_lvl is not None else None
                price_match = re.search(r'mine-shop__card--upgrade.*?mine-shop__price.*?>Цена:\s*([\d\s]+)', html, re.DOTALL | re.IGNORECASE)
                if price_match:
                    parsed_cost = parse_smart_number(price_match.group(1))
                    if parsed_cost > 0:
                        cost = parsed_cost

                if cost and self.current_balance >= cost:
                    self.log(tr("log_buy_upgrade", price=f"{cost:,}"))
                    upg_res = self.session.post(
                        "https://mangabuff.ru/mine/upgrade",
                        data={},
                        headers={"X-CSRF-TOKEN": self.csrf_token, "X-Requested-With": "XMLHttpRequest"},
                        timeout=10
                    )
                    if upg_res.status_code == 200:
                        try:
                            upg_data = upg_res.json()
                            new_lvl = upg_data.get("pickaxe_level", current_lvl + 1 if current_lvl else "?")
                            new_ore = upg_data.get("ore", self.current_balance - cost)
                            self.current_balance = new_ore
                            self.update_stats(balance=self.current_balance)
                            self.log(tr("log_upgrade_ok"))
                            self.notifier.notify_upgrade("Кирка", level=new_lvl, cost=cost)
                            return True
                        except Exception:
                            pass
        except Exception as e:
            print(f"auto_upgrade_pickaxe exception: {e}")
        return False

    # Base HQ weights — crystal snowball first, then combat pair, then niche.
    _TOWER_HQ_PRIORITY = {
        "supply": 100,      # +% dark crystals
        "assault": 92,      # +% ATK
        "fortitude": 90,    # +% HP
        "hunt": 55,         # +% vs bosses
        "mentorship": 30,   # +% tempering XP
    }
    _TOWER_HQ_NAMES = {
        "supply": "Снабжение",
        "assault": "Натиск",
        "fortitude": "Оплот",
        "hunt": "Знание Хранителей",
        "mentorship": "Наставление",
    }
    _TOWER_ORDER_NAMES = {
        "gathering": "Сбор (+25% кристаллов)",
        "balanced": "Баланс",
        "breakthrough": "Прорыв (+15% прогресс)",
        "hunt": "Охота (+20% vs босс)",
        "mentorship": "Наставничество (+30% XP карт)",
    }

    @staticmethod
    def _parse_tower_int(text, default=0):
        try:
            digits = re.sub(r"[^\d]", "", str(text or ""))
            return int(digits) if digits else default
        except Exception:
            return default

    def _parse_tower_hq_cards(self, soup):
        """Read HQ upgrade cards (level/cost) from tower page DOM."""
        cards = {}
        if soup is None:
            return cards
        for art in soup.select("[data-hq-upgrade-card]"):
            key = art.get("data-hq-upgrade-card")
            if key not in self._TOWER_HQ_PRIORITY:
                continue
            btn = art.select_one("button[data-upgrade-key]:not([data-upgrade-mode])") or art.select_one(
                f'button[data-upgrade-key="{key}"]'
            )
            lvl_el = art.select_one("[data-hq-upgrade-level]")
            cost_el = art.select_one("[data-hq-upgrade-cost]")
            level = self._parse_tower_int(
                (btn.get("data-upgrade-level") if btn else None)
                or (lvl_el.get_text(strip=True) if lvl_el else 0)
            )
            cost = self._parse_tower_int(
                (btn.get("data-upgrade-cost") if btn else None)
                or (cost_el.get_text(strip=True) if cost_el else 0)
            )
            cards[key] = {"level": level, "cost": cost}
        return cards

    def _hq_upgrade_score(self, key, level, cards):
        """Weighted relative marginal bonus per crystal, using the site's HQ formulas.

        Supply/mentorship have logarithmic softcaps; combat gains 0.075%/level.
        Weights express growth preferences, not a claim of globally optimal spending.
        """
        import math
        cost = int(cards.get(key, {}).get("cost") or 0)
        if cost <= 0:
            return 0.0
        level = max(0, int(level))
        if key in ("supply", "mentorship"):
            softcap = 100 if key == "supply" else 200
            current = 1 + .01 * softcap * math.log1p(level / softcap)
            following = 1 + .01 * softcap * math.log1p((level + 1) / softcap)
        else:
            current = 1 + .00075 * level
            following = current + .00075
        return self._TOWER_HQ_PRIORITY.get(key, 1) * (following / current - 1) / cost

    def _choose_tower_order(self, cards=None):
        """
        Pick expedition order for HQ growth.
        Autopilot sticks to gathering (+25% crystals) — best fuel for HQ.
        Override with tower_order_auto=False + tower_order=<name>.
        """
        if not DataManager.get_setting("tower_order_auto", True):
            return DataManager.get_setting("tower_order") or "gathering"
        # HQ snowball: no other order beats +25% dark crystals for further upgrades.
        return "gathering"

    def upgrade_tower_headquarters(self, soup=None, root=None, max_buys=40, reserve=0):
        """
        Spend Dark Crystals on Banner HQ upgrades using live level balancing.
        Works even during an active expedition.
        """
        if not DataManager.get_setting("tower_hq_auto", True):
            return 0

        try:
            if soup is None or root is None:
                res = self.session.get("https://mangabuff.ru/tower", timeout=12)
                if res.status_code != 200:
                    return 0
                soup = BeautifulSoup(res.text, "html.parser")
                root = soup.find(class_="mbf-abyss-tower") or soup.find(attrs={"data-tower-root": True})
                if not root:
                    return 0

            upgrade_url = root.get("data-upgrade-url") or "https://mangabuff.ru/tower/headquarters/upgrade"
            csrf = root.get("data-csrf-token") or self.csrf_token

            currency_el = soup.select_one("[data-hq-currency-value]") or soup.select_one("[data-hq-currency]")
            crystals = self._parse_tower_int(
                (currency_el.get_text(strip=True) if currency_el else None)
                or (currency_el.get("data-hq-currency") if currency_el else None)
            )
            if crystals <= reserve:
                return 0

            cards = self._parse_tower_hq_cards(soup)
            if not cards:
                return 0

            bought = 0
            gained = {k: 0 for k in cards}
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-CSRF-TOKEN": csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://mangabuff.ru/tower",
                "Origin": "https://mangabuff.ru",
            }

            attempts = 0
            deadline = time.monotonic() + 180
            while bought < max_buys and crystals > reserve:
                if self._module_cancelled() or time.monotonic() >= deadline:
                    break
                attempts += 1
                if attempts > max_buys + 3:
                    break
                candidates = []
                for key, info in cards.items():
                    cost = int(info.get("cost") or 0)
                    level = int(info.get("level") or 0)
                    if cost <= 0 or cost > crystals - reserve:
                        continue
                    score = self._hq_upgrade_score(key, level, cards)
                    candidates.append((score, -cost, key))
                if not candidates:
                    break
                candidates.sort(reverse=True)
                key = candidates[0][2]
                expected = int(cards[key]["level"])

                resp = self.session.post(
                    upgrade_url,
                    json={
                        "upgrade_type": key,
                        "expected_level": expected,
                        "buy_max": False,
                    },
                    headers=headers,
                    timeout=12,
                )
                if resp.status_code == 429:
                    self.log("⏳ Штаб Башни: лимит запросов, продолжу в следующем цикле.")
                    break
                if resp.status_code == 409:
                    # expected_level stale (race / concurrent UI) — refresh cards and continue.
                    self.log("⏳ Штаб Башни: уровень рассинхронился, обновляю карту штаба...")
                    time.sleep(0.8)
                    try:
                        fres = self.session.get("https://mangabuff.ru/tower", timeout=12)
                        if fres.status_code == 200:
                            soup = BeautifulSoup(fres.text, "html.parser")
                            root = soup.find(class_="mbf-abyss-tower") or soup.find(attrs={"data-tower-root": True}) or root
                            upgrade_url = (root.get("data-upgrade-url") if root else None) or upgrade_url
                            csrf = (root.get("data-csrf-token") if root else None) or csrf
                            headers["X-CSRF-TOKEN"] = csrf
                            currency_el = soup.select_one("[data-hq-currency-value]") or soup.select_one("[data-hq-currency]")
                            crystals = self._parse_tower_int(
                                (currency_el.get_text(strip=True) if currency_el else None)
                                or (currency_el.get("data-hq-currency") if currency_el else None)
                            )
                            fresh_cards = self._parse_tower_hq_cards(soup)
                            if fresh_cards:
                                for k, info in fresh_cards.items():
                                    if k in cards:
                                        cards[k]["level"] = info["level"]
                                        cards[k]["cost"] = info["cost"]
                                    else:
                                        cards[k] = info
                                        gained.setdefault(k, 0)
                    except Exception:
                        pass
                    continue
                if resp.status_code != 200:
                    self.log(f"⚠️ Штаб Башни: апгрейд {key} → HTTP {resp.status_code}")
                    break
                try:
                    payload = resp.json()
                    data = payload.get("data") or payload
                except Exception:
                    break

                # Never infer a successful purchase or retain an old balance when it is zero.
                if data.get("level") is None or data.get("dark_crystals") is None or data.get("next_cost") is None:
                    break
                new_lvl = int(data["level"])
                crystals = int(data["dark_crystals"])
                next_cost = int(data["next_cost"])
                if new_lvl <= expected or crystals < 0:
                    break
                cards[key]["level"] = new_lvl
                cards[key]["cost"] = next_cost
                gained[key] += int(data.get("levels_added") or 1)
                bought += 1
                time.sleep(0.45)

            if bought > 0:
                parts = [
                    f"{self._TOWER_HQ_NAMES.get(k, k)} +{v}"
                    for k, v in gained.items() if v > 0
                ]
                try:
                    rank = sum(int(cards[k]["level"]) for k in cards)
                    rank_s = f", ранг Знамени ~{rank}"
                except Exception:
                    rank_s = ""
                self.log(
                    f"🏛️ Штаб Башни: вложено {bought} улучшений "
                    f"({', '.join(parts)}), кристаллов осталось {crystals:,}{rank_s}"
                )
            return bought
        except Exception as e:
            self.log(f"⚠️ Ошибка прокачки Штаба Башни: {e}")
            return 0

    def _prepare_tower_order(self, root, soup=None, preferred_order=None):
        """Save HQ order for the next expedition (only works while idle)."""
        cards = self._parse_tower_hq_cards(soup) if soup is not None else {}
        order = preferred_order or self._choose_tower_order(cards)
        prep_url = (root.get("data-save-preparation-url") if root else None) or "https://mangabuff.ru/tower/preparation/save"
        csrf = (root.get("data-csrf-token") if root else None) or self.csrf_token
        try:
            resp = self.session.post(
                prep_url,
                json={"order_type": order},
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-CSRF-TOKEN": csrf,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://mangabuff.ru/tower",
                    "Origin": "https://mangabuff.ru",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                if root is not None:
                    root["data-selected-order"] = order
                DataManager.set_setting("tower_order", order)
                return order
            return None
        except Exception:
            return None

    def _plan_tower_expedition(self, root, soup, payload):
        """Pick the expedition from fresh server previews, no paid actions.

        Every difficulty x duration the page offers, then routes and orders for the three best.
        Rates are per real cycle: travel time plus the wait until the bot sends the squad again,
        so a short trip that comes back in the middle of the evening cup is not mistaken for a fast one.
        """
        preview_url = root.get("data-preview-url")
        if not preview_url or len(payload.get("card_user_ids", [])) != 4:
            return None
        headers = {
            "Accept": "application/json", "Content-Type": "application/json",
            "X-CSRF-TOKEN": root.get("data-csrf-token") or self.csrf_token,
            "X-Requested-With": "XMLHttpRequest", "Referer": "https://mangabuff.ru/tower",
        }
        min_chance = 70.0
        max_previews = 48
        by_progress = DataManager.get_setting("tower_priority", "rewards") == "progress"
        cup = DataManager.get_setting("arena_cup_enabled", True)
        now = self._msk_now()
        deadline = time.monotonic() + 300
        results = {}  # signature -> option tuple, or None when the preview rejected it
        rejected = []

        def pick(options):
            # Progress differs by hundredths between difficulties while crystals differ by ~40%,
            # so in "rewards" mode near-equal progress (within 10%) is a tie decided by crystals.
            if by_progress:
                return max(options, key=lambda o: o[0])
            top = max(o[0][0] for o in options)
            close = [o for o in options if o[0][0] >= 0.9 * top]
            return max(close, key=lambda o: (o[0][1], o[0][0], o[0][2], o[0][3]))

        def offered(group):
            if group == "order" and not DataManager.get_setting("tower_order_auto", True):
                return []
            nodes = soup.select('[data-tower-option="%s"][data-value]' % group)
            if group == "order":
                nodes += soup.select('[data-hq-order-select][data-order-key]')
            values = []
            for node in nodes:
                if node.has_attr("disabled") or node.get("data-locked") == "1" or "is-locked" in node.get("class", []):
                    continue
                value = node.get("data-value") or node.get("data-order-key")
                if group == "duration":
                    if not str(value).isdigit():
                        continue
                    value = int(value) * 60
                if value and value not in values:
                    values.append(value)
            return values

        def evaluate(candidate):
            """False only when the search must stop (error, rate limit, cancel)."""
            import math
            signature = tuple((key, str(candidate[key])) for key in sorted(candidate))
            if signature in results or len(results) >= max_previews:
                return True
            if self._module_cancelled() or time.monotonic() >= deadline:
                return False
            response = self.session.post(preview_url, json=candidate, headers=headers, timeout=10)
            if response.status_code != 200:
                return False
            envelope = response.json()
            data = envelope.get("data", envelope)
            if not isinstance(data, dict) or data.get("power_ratio") is None or data.get("estimated_progress") is None:
                return False
            ratio = float(data["power_ratio"])
            progress = float(data["estimated_progress"])
            hours = float(candidate["duration_minutes"]) / 60
            reward = (data.get("rewards") or {}).get("dark_crystals") or {}
            low, high = float(reward.get("min", 0)), float(reward.get("max", 0))
            if hours <= 0 or not all(math.isfinite(x) for x in (ratio, progress, low, high)):
                return False
            cycle = hours + tower_downtime_hours(now, hours, cup)
            chance = max(5, min(99, ratio * 85))  # same display estimate as site JS
            label = (f"{candidate['difficulty_type']} {int(hours)}ч {candidate['route_type']}/{candidate['order_type']} "
                     f"{chance:.0f}% п/ч {progress / cycle:.2f} кр/ч ~{(low + high) / (2 * cycle):.0f}")
            why = ("предупреждение сайта" if data.get("warning") else f"шанс < {min_chance:.0f}%" if chance < min_chance
                   else "нет прогресса" if progress <= 0 else "странный прогноз" if low < 0 or high < low else None)
            if why:
                results[signature] = None
                rejected.append(f"{label} — {why}")
                return True
            score = (progress / cycle, (low + high) / (2 * cycle), chance, -hours)
            results[signature] = (score, dict(candidate), chance, data, label)
            return True

        def valid():
            return [o for o in results.values() if o]

        def pair(option):
            return option[1]["difficulty_type"], option[1]["duration_minutes"]

        try:
            if not evaluate(payload):
                return None
            # 1. Difficulty x duration decide the haul: try every pair the page offers
            for difficulty in offered("difficulty") or [payload["difficulty_type"]]:
                for minutes in offered("duration") or [payload["duration_minutes"]]:
                    if not evaluate(dict(payload, difficulty_type=difficulty, duration_minutes=minutes)):
                        return None
            # 2. Route and order for the three most promising pairs
            leaders, pool = [], valid()
            while pool and len(leaders) < 3:
                leader = pick(pool)
                leaders.append(leader)
                pool = [o for o in pool if pair(o) != pair(leader)]
            for leader in leaders:
                for group, field in (("route", "route_type"), ("order", "order_type")):
                    anchor = pick([o for o in valid() if pair(o) == pair(leader)])[1]
                    for value in offered(group):
                        if not evaluate(dict(anchor, **{field: value})):
                            return None
            options = valid()
            if not options:
                self.log("⚠️ Башня: прогноз не нашёл безопасный вариант (шанс ≥70%, без предупреждений). "
                         "Отброшено: " + "; ".join(rejected[:10]))
                return None
            score, chosen, chance, data, label = pick(options)
            ranking = sorted(options, key=lambda o: o[0], reverse=True)[:8]
            note = f". Отброшено {len(rejected)}: " + "; ".join(rejected[:4]) if rejected else ""
            self.log(f"🔎 Башня: проверил {len(results)} вариантов (п/ч и кр/ч — на реальный цикл с простоем). "
                     "Лучшие: " + "; ".join(o[4] for o in ranking) + note)
            self.log(f"🔎 Башня: выбрал {label}.")
            return chosen
        except Exception as exc:
            self.log(f"⚠️ Башня: прогноз недоступен: {exc}")
            return None

    def check_tower_expedition(self):
        """Check Abyss Tower (/tower), claim completed expeditions, and start a new one if idle."""
        if not DataManager.get_setting("tower_enabled", True):
            return None

        self.log(tr("log_tower_check"))
        self._tower_page_ok = False  # lets a manual run tell "no expedition" from "page did not load"
        self._tower_claim_failed = self._tower_start_failed = False
        try:
            res = self.session.get("https://mangabuff.ru/tower", timeout=12)
            if res.status_code != 200:
                self.log(f"⚠️ Tower page status: {res.status_code}")
                return None

            soup = BeautifulSoup(res.text, "html.parser")
            root = soup.find(class_="mbf-abyss-tower") or soup.find(attrs={"data-tower-root": True})
            if not root:
                root = soup.find(attrs={"data-claim-url": True})

            if not root:
                self.log("⚠️ Could not locate Tower container.")
                return None
            self._tower_page_ok = True

            # HQ upgrades work even during an active expedition — spend crystals first.
            # No currency reserve: compare marginal value before each bounded purchase.
            self.upgrade_tower_headquarters(soup=soup, root=root, max_buys=40, reserve=0)

            claim_url = root.get("data-claim-url", "https://mangabuff.ru/tower/expedition/claim")
            start_url = root.get("data-start-url", "https://mangabuff.ru/tower/expedition/start")
            is_active = str(root.get("data-expedition-active", "0")) == "1"
            is_complete = str(root.get("data-expedition-complete", "0")) == "1"

            timer_el = soup.find(attrs={"data-tower-timer": True}) or soup.find(class_="mbf-abyss-tower__timer-value")
            if timer_el and timer_el.get("data-remaining-seconds"):
                remaining_seconds = int(timer_el.get("data-remaining-seconds"))
                time_str = timer_el.get_text(strip=True)
            else:
                remaining_seconds = int(root.get("data-remaining-seconds", 0) or 0)
                hours = remaining_seconds // 3600
                minutes = (remaining_seconds % 3600) // 60
                time_str = f"{hours}ч {minutes}м" if hours > 0 else f"{minutes}м"

            # 1. Claim completed rewards
            if is_complete:
                claim_res = self.session.post(
                    claim_url,
                    json={},
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "X-CSRF-TOKEN": root.get("data-csrf-token") or self.csrf_token,
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": "https://mangabuff.ru/tower"
                    },
                    timeout=10
                )
                if claim_res.status_code == 200:
                    try:
                        c_data = claim_res.json()
                        rewards = c_data.get("data", {}).get("rewards", {})
                        diamonds = rewards.get("diamonds", 0)
                        crystals = rewards.get("dark_crystals", 0)
                        lvl_ups = len(c_data.get("data", {}).get("level_ups", []))
                        self.log(tr("log_tower_claimed", diamonds=diamonds, crystals=crystals))
                        self.notifier.notify_tower_claimed(diamonds, crystals, lvl_ups)
                    except Exception as e:
                        self.log(f"⚠️ Tower claim parse error: {e}")

                    # Refresh tower interface right after claim so we have fresh state & CSRF to start next expedition immediately!
                    time.sleep(1)
                    res = self.session.get("https://mangabuff.ru/tower", timeout=12)
                    if res.status_code == 200:
                        soup = BeautifulSoup(res.text, "html.parser")
                        fresh_root = soup.find(class_="mbf-abyss-tower") or soup.find(attrs={"data-tower-root": True})
                        if fresh_root:
                            root = fresh_root
                            start_url = root.get("data-start-url", start_url)
                            is_active = str(root.get("data-expedition-active", "0")) == "1"
                            is_complete = str(root.get("data-expedition-complete", "0")) == "1"
                            # Spend newly claimed crystals before the next launch.
                            self.upgrade_tower_headquarters(soup=soup, root=root, max_buys=40, reserve=0)
                        else:
                            return None
                    else:
                        return None
                else:
                    self.log(f"⚠️ Tower claim failed with status {claim_res.status_code}")
                    self._tower_claim_failed = True
                    return None

            # 2. Start a new expedition if idle
            if not is_active:
                chosen_order = self._choose_tower_order(self._parse_tower_hq_cards(soup))
                raw_ids = root.get("data-selected-card-ids", "") if root else ""
                card_ids = []
                if raw_ids:
                    try:
                        card_ids = [int(x.strip()) for x in str(raw_ids).split(",") if x.strip().isdigit()][:4]
                    except Exception:
                        pass

                if not card_ids:
                    slot_cards = soup.select(".mbf-abyss-tower__squad-slot[data-card-user-id], [data-tower-squad-slot][data-card-user-id]")
                    for sc in slot_cards:
                        cid = sc.get("data-card-user-id")
                        if cid and str(cid).isdigit() and int(cid) not in card_ids:
                            card_ids.append(int(cid))

                saved_squad = [int(x) for x in DataManager.get_setting("tower_squad") or [] if str(x).isdigit()][:4]
                if not card_ids:
                    card_ids = saved_squad
                elif rerolled_out_of_squad(card_ids, saved_squad):
                    card_ids = saved_squad  # the awakening reroll took a card out of the squad on the site

                if card_ids:
                    DataManager.set_setting("tower_squad", card_ids)

                dur_val = (root.get("data-selected-duration") if root else None) or DataManager.get_setting("tower_duration") or 12
                dur_int = int(dur_val) if str(dur_val).isdigit() else 12
                duration_mins = (dur_int * 60) if dur_int <= 24 else dur_int

                difficulty = (root.get("data-selected-difficulty") if root else None) or DataManager.get_setting("tower_difficulty") or "nightmare"
                route = (root.get("data-selected-route") if root else None) or DataManager.get_setting("tower_route") or "frontier"
                order = (
                    chosen_order
                    or (root.get("data-selected-order") if root else None)
                    or DataManager.get_setting("tower_order")
                    or "gathering"
                )

                # Persist settings
                DataManager.set_setting("tower_duration", dur_int)
                DataManager.set_setting("tower_difficulty", difficulty)
                DataManager.set_setting("tower_route", route)
                DataManager.set_setting("tower_order", order)

                if card_ids:
                    payload = {
                        "card_user_ids": card_ids,
                        "duration_minutes": duration_mins,
                        "route_type": route,
                        "difficulty_type": difficulty,
                        "order_type": order,
                        "resonance_offer_token": "",
                        "resonance_accept": 0
                    }
                    payload = self._plan_tower_expedition(root, soup, payload)
                    if payload is None or self._module_cancelled():
                        return None
                    duration_mins = payload["duration_minutes"]
                    difficulty = payload["difficulty_type"]
                    route = payload["route_type"]
                    order = payload["order_type"]
                    DataManager.set_setting("tower_duration", duration_mins // 60)
                    DataManager.set_setting("tower_difficulty", difficulty)
                    DataManager.set_setting("tower_route", route)
                    DataManager.set_setting("tower_order", order)
                    csrf = (root.get("data-csrf-token") if root else None) or self.csrf_token
                    start_res = self.session.post(
                        start_url,
                        json=payload,
                        headers={
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                            "X-CSRF-TOKEN": csrf,
                            "X-Requested-With": "XMLHttpRequest",
                            "Referer": "https://mangabuff.ru/tower"
                        },
                        timeout=10
                    )
                    if start_res.status_code in (200, 201):
                        try:
                            s_data = start_res.json()
                            ends_at = s_data.get("data", {}).get("ends_at", "")
                            rem_sec = s_data.get("data", {}).get("remaining_seconds") or (duration_mins * 60)
                            dur_hours = duration_mins // 60
                            self.log(tr("log_tower_started", diff=difficulty.capitalize(), dur=dur_hours))
                            DataManager.set_setting("tower_no_squad", False)
                            self.notifier.notify_tower_started(difficulty.capitalize(), dur_hours, ends_at)
                            return max(60, int(rem_sec))
                        except Exception:
                            return duration_mins * 60
                    else:
                        self.log(f"⚠️ Tower start failed with status {start_res.status_code}")
                        self._tower_start_failed = True
                        return None
                else:
                    self.log("ℹ️ Отряд для Башни не выбран (выберите отряд на сайте mangabuff.ru/tower). Башня пропущена.")
                    DataManager.set_setting("tower_no_squad", True)
                    return None
            else:
                self.log(tr("log_tower_running", left=time_str))
                return max(60, remaining_seconds)

        except Exception as e:
            self.log(f"⚠️ Tower error: {e}")
    def get_current_arena_rank_info(self) -> dict | None:
        """Fetch current arena rank information from /battle."""
        try:
            res = self.session.get("https://mangabuff.ru/battle", timeout=10)
            if res.status_code != 200:
                return None
            soup = BeautifulSoup(res.text, "html.parser")
            tier_name = None
            icon_text = ""
            card = soup.select_one(".battle-home__rank-card--current")
            if card:
                icon = card.select_one(".battle-home__rank-icon")
                icon_text = icon.get_text(strip=True) if icon else ""
                strong = card.select_one(".battle-home__rank-content strong")
                if strong:
                    tier_name = strong.get_text(strip=True)

            division = ""
            top_el = soup.select_one(".battle-home__rank-top strong")
            if top_el:
                division = top_el.get_text(strip=True)
                if not tier_name:
                    for t in ARENA_TIERS:
                        if t.lower() in division.lower():
                            tier_name = t
                            break

            if not tier_name:
                return None

            tier_index = -1
            for idx, t in enumerate(ARENA_TIERS):
                if t.lower() == tier_name.lower():
                    tier_index = idx
                    tier_name = t
                    break

            if not icon_text:
                icon_text = TIER_ICONS.get(tier_name, "🏅")

            return {
                "tier": tier_name,
                "tier_index": tier_index,
                "icon": icon_text,
                "division": division or tier_name,
                "full_name": f"{icon_text} {tier_name}".strip(),
            }
        except Exception:
            return None

    def get_current_arena_rank(self) -> str | None:
        """Fetch current arena rank title (e.g. '💎 Платина')."""
        info = self.get_current_arena_rank_info()
        return info["full_name"] if info else None

    def check_arena_rank_promotion(self, battles_done: int = 0, wins: int = 0, losses: int = 0):
        """
        Check if the user reached a new arena rank tier (e.g. Platinum -> Diamond).
        Only sends Telegram notification upon tier promotion with duration and battle stats summary.
        """
        tracker = DataManager.get_setting("arena_rank_tracker") or {}
        tracker["battles_in_tier"] = tracker.get("battles_in_tier", 0) + battles_done
        tracker["wins_in_tier"] = tracker.get("wins_in_tier", 0) + wins
        tracker["losses_in_tier"] = tracker.get("losses_in_tier", 0) + losses
        rank_info = self.get_current_arena_rank_info()
        if not rank_info or rank_info["tier_index"] < 0:
            DataManager.set_setting("arena_rank_tracker", tracker)
            return

        now = time.time()

        current_tier = rank_info["tier"]
        current_index = rank_info["tier_index"]
        current_icon = rank_info["icon"]
        current_division = rank_info["division"]

        if not tracker or "current_tier" not in tracker:
            tracker = {
                "current_tier": current_tier,
                "tier_index": current_index,
                "icon": current_icon,
                "division": current_division,
                "tier_started_at": now,
                "battles_in_tier": tracker["battles_in_tier"],
                "wins_in_tier": tracker["wins_in_tier"],
                "losses_in_tier": tracker["losses_in_tier"],
            }
            DataManager.set_setting("arena_rank_tracker", tracker)
            return

        saved_tier = tracker.get("current_tier")
        saved_index = tracker.get("tier_index", -1)
        saved_icon = tracker.get("icon", "")

        if current_index > saved_index:
            total_battles = tracker["battles_in_tier"]
            total_wins = tracker["wins_in_tier"]
            total_losses = tracker["losses_in_tier"]
            started_at = tracker.get("tier_started_at", now)
            elapsed_sec = max(0.0, now - started_at)

            old_full = f"{saved_icon} {saved_tier}".strip()
            new_full = f"{current_icon} {current_tier}".strip()

            self.notifier.notify_rank_tier_promotion(
                old_tier=old_full,
                new_tier=new_full,
                new_division=current_division,
                duration_sec=elapsed_sec,
                battles=total_battles,
                wins=total_wins,
                losses=total_losses,
            )

            tracker = {
                "current_tier": current_tier,
                "tier_index": current_index,
                "icon": current_icon,
                "division": current_division,
                "tier_started_at": now,
                "battles_in_tier": 0,
                "wins_in_tier": 0,
                "losses_in_tier": 0,
            }
            DataManager.set_setting("arena_rank_tracker", tracker)

        elif current_index < saved_index:
            # Rank dropped or season reset
            tracker = {
                "current_tier": current_tier,
                "tier_index": current_index,
                "icon": current_icon,
                "division": current_division,
                "tier_started_at": now,
                "battles_in_tier": 0,
                "wins_in_tier": 0,
                "losses_in_tier": 0,
            }
            DataManager.set_setting("arena_rank_tracker", tracker)

        else:
            tracker["division"] = current_division
            DataManager.set_setting("arena_rank_tracker", tracker)


    def start_battle(self):
        """POST to the matchmaking endpoint; return redirect_url or error code."""
        try:
            res = self.session.post(
                getattr(self, '_arena_start_url', 'https://mangabuff.ru/battle/fight/start'),
                headers={
                    "X-CSRF-TOKEN": self.csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://mangabuff.ru/battle"
                },
                timeout=10
            )

            # Check for Cloudflare Turnstile captcha
            if "turnstile" in res.text.lower() or "requestactioncaptcha" in res.text.lower():
                self.log(tr("log_turnstile_detected"))
                self.notifier.notify_alert("Капча Cloudflare Turnstile", "Бот обнаружил проверку капчи на сайте. Пожалуйста, пройдите её в браузере.")
                return "CAPTCHA"

            if res.status_code == 419:
                if self._handle_session_expired():
                    return self.start_battle()
                return None

            if res.status_code in (400, 422):
                return None

            try:
                data = res.json()
            except ValueError:
                return None

            if data.get("success") is False or data.get("status") is False:
                if data.get("energy_left") == 0 or data.get("energy") == 0:
                    return "NO_ENERGY"
                return None

            redirect_url = data.get("redirect_url")
            return redirect_url
        except Exception:
            return None

    def check_battle_result(self, match_url):
        """Return a tuple (earned_essence, is_win) and detect jackpot card drops."""
        try:
            if not match_url:
                return None

            res = self.session.get(
                match_url if match_url.startswith("http") else "https://mangabuff.ru" + match_url,
                headers={
                    "X-CSRF-TOKEN": self.csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://mangabuff.ru/battle"
                },
                timeout=10
            )

            if res.status_code != 200:
                return None

            # Retain structured server replays for future comparative deck choices.
            # No page HTML, auth cookies, credentials or CSRF are stored.
            try:
                observation = replay_from_html(res.text)
                save_observation(os.path.join(BASE_DIR, 'arena_observations.json'), observation)
            except Exception as exc:
                self.log(f'⚠️ Арена: журнал для модели не сохранён ({type(exc).__name__}).')

            # 1. Parse earned essence
            m = re.search(r'"essence"\s*:\s*(\d+)', res.text)
            if not m:
                return None
            earned_essence = int(m.group(1))

            # 2. Parse win status
            is_win = None
            mw = re.search(r'"is_win"\s*:\s*(true|false)', res.text)
            if mw:
                is_win = (mw.group(1).lower() == "true")

            # 3. Detect high-value card drops in battle result
            card_match = re.search(r'"card"\s*:\s*(\{.+?\})[,}]', res.text)
            if card_match:
                try:
                    card_dict = json.loads(card_match.group(1))
                    self.check_card_rarity({"card": card_dict})
                except Exception:
                    pass
            else:
                rarity_match = re.search(r'"potential_rarity"\s*:\s*"([^"]+)"', res.text, re.IGNORECASE)
                name_match = re.search(r'"(?:name|title)"\s*:\s*"([^"]+)"', res.text, re.IGNORECASE)
                if rarity_match:
                    r_val = rarity_match.group(1).lower()
                    c_name = name_match.group(1) if name_match else "Card"
                    self.check_card_rarity({"card": {"potential_rarity": r_val, "name": c_name}})

            return (earned_essence, is_win)
        except Exception as e:
            print(f"check_battle_result exception: {e}")
            return None

    def check_card_rarity(self, data):
        """Detect high-value card drops and preserve them in jackpot_cards.json."""
        card = data.get("card") if isinstance(data, dict) else None
        if not isinstance(card, dict):
            return
        rarity = card.get("potential_rarity")
        if rarity and str(rarity).lower() in TARGET_RARITIES:
            card_name = card.get("name") or card.get("title") or "Карточка"
            print(f"🔥 JACKPOT: Dropped a card with tier: {rarity}! ({card_name})")
            self.log(tr("log_jackpot", rarity=str(rarity).upper()))
            self.notifier.notify_jackpot(str(rarity), card_name)
            self._preserve_card(card)

    def _preserve_card(self, card):
        """Save a jackpot card to disk so it is never consumed or modified."""
        try:
            path = os.path.join(BASE_DIR, "jackpot_cards.json")
            existing = DataManager.load_json(path)
            if not isinstance(existing, list):
                existing = []
            existing.append(card)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=4, ensure_ascii=False)
            print(f"💾 Preserved jackpot card -> {path}")
        except Exception as e:
            print(f"Preserve card error: {e}")

    def check_active_deck(self):
        """Check for active deck marker before battling."""
        try:
            res = self.session.get("https://mangabuff.ru/battle", timeout=10)
            if res.status_code != 200:
                return True
            soup = BeautifulSoup(res.text, "html.parser")
            text = soup.get_text(" ", strip=True).lower()
            empty_markers = [
                "нет активной колод", "колода пуст", "no active deck",
                "deck is empty", "установите колод", "set a deck"
            ]
            if any(m in text for m in empty_markers):
                print("⚠️ Possible empty deck detected — proceeding with battles anyway.")
            return True
        except Exception:
            return True

    def _handle_session_expired(self):
        """Re-authenticate when the server rejects the session."""
        self.log("♻️ Session expired. Re-logging in...")
        if self.login_and_steal_keys():
            self.log("✅ Re-login successful! Resuming...")
            return True
        self.log("❌ Re-login failed. Stopping.")
        return False

    def _find_active_claim_url(self):
        """Parse /balance HTML for the currently CLAIMABLE daily-reward button."""
        self._balance_unavailable = True  # None below then means "could not look", not "nothing to claim"
        try:
            res = self.session.get("https://mangabuff.ru/balance", timeout=10)
            if res.status_code != 200:
                return None
            self._balance_unavailable = False
            soup = BeautifulSoup(res.text, "html.parser")

            meta = soup.find("meta", attrs={"name": "csrf-token"})
            if meta and meta.get("content"):
                self.csrf_token = meta.get("content")

            # 1. Direct check for active daily reward calendar button
            active_btn = soup.find(class_=lambda c: c and "daily-rewards-item-exp--active" in c)
            if active_btn:
                parent = active_btn.find_parent("div", class_="daily-rewards-item")
                if parent and parent.get("data-day"):
                    return f"https://mangabuff.ru/balance/claim/{parent.get('data-day')}"

            # 2. General check for claim buttons
            claim_buttons = []
            for el in soup.find_all(string=lambda t: t and "Забрать" in t):
                node = el.parent
                if node:
                    claim_buttons.append(node)

            for btn in claim_buttons:
                parent = btn.find_parent("div", class_="daily-rewards-item")
                if parent and parent.get("data-day"):
                    pclasses = " ".join(parent.get("class", [])).lower()
                    if not any(m in pclasses for m in ("completed", "claimed", "locked", "disabled")):
                        return f"https://mangabuff.ru/balance/claim/{parent.get('data-day')}"

                href = btn.get("href") or btn.get("action")
                if href:
                    return href if href.startswith("http") else ("https://mangabuff.ru" + href)

        except Exception as e:
            print(f"_find_active_claim_url exception: {e}")
        return None

    def run_farm_loop(self, prepared=False):
        """Farm in bounded batches; stop after three consecutive zero rewards."""
        if not prepared and not self.prepare_arena_deck():
            return

        # Establish the starting tier before the first batch can promote it.
        self.check_arena_rank_promotion()

        # Claim daily quests before battling
        self.check_and_claim_quests()

        self.log(tr("log_farm_start"))

        battles_done = 0
        total_essence_ = 0 
        wins = 0
        losses = 0
        consecutive_zero_essence = 0
        consecutive_unknown = 0
        next_micro_break = random.randint(12, 18)
        cap_reached = False
        attempts = 0
        max_battles = max(1, min(50, int(DataManager.get_setting('arena_batch_battles', 30))))
        # Finish the batch well inside the module time limit; the rest goes into the next batch
        batch_ends_at = time.monotonic() + ARENA_BATCH_SECONDS

        while (self._alive() and battles_done < max_battles and attempts < max_battles + 5
               and time.monotonic() < batch_ends_at):
            for _ in range(int(random.uniform(3, 7) * 4)):
                if not self._alive():
                    break
                self._touch_heartbeat()
                time.sleep(0.25)
            if not self._alive():
                break

            match_url = self.start_battle()
            attempts += 1
            if match_url == "NO_ENERGY":
                print("Out of battle energy.")
                break
            elif match_url == "CAPTCHA":
                break
            elif not match_url:
                print("Matchmaking transient — backing off 25s, will retry.")
                self._pause(25)
                continue

            result = self.check_battle_result(match_url)
            if result is None:
                consecutive_unknown += 1
                if consecutive_unknown >= 3:
                    self.log("⚠️ 3 consecutive unparseable battle results — stopping.")
                    break
                self._pause(random.uniform(10, 15))
                continue

            consecutive_unknown = 0
            earned_essence, is_win = result

            battles_done += 1
            total_essence_ += earned_essence
            if is_win:
                wins += 1
            else:
                losses += 1

            # A single loss/zero is not proof of a daily cap.
            if earned_essence == 0:
                consecutive_zero_essence += 1
                if consecutive_zero_essence >= 3 and not DataManager.get_setting('arena_rank_mode', True):
                    self.log('⏳ Арена: три боя подряд без эссенции — фарм до следующего дня приостановлен.')
                    cap_reached = True
                    self._arena_cap_date = self._msk_now().date()
                    break
            else:
                consecutive_zero_essence = 0

            if getattr(self,'_arena_buff_remaining',None) is not None:
                self._arena_buff_remaining=max(0,self._arena_buff_remaining-1)
                if self._arena_buff_remaining==0:
                    self._arena_buff_depleted=True
                    self.log('✦ Запас выбранного бафа закончился — завершаю пачку для нового подбора.')
                    break

            if getattr(self, '_arena_min_energy', None) is not None:
                self._arena_min_energy = max(0, self._arena_min_energy - (2 if is_win else 1))
                if self._arena_min_energy < 10:
                    self.log('⏳ Арена: энергия состава снизилась — завершаю пачку для нового подбора/ротации.')
                    break

            outcome = "Win" if is_win else "Loss"
            print(f"[BATTLE] #{battles_done} {outcome} | +{earned_essence} essence (Total: +{total_essence_})")

            # Micro-break
            if battles_done >= next_micro_break:
                brk = random.uniform(20, 40)
                print(f"😴 Micro-break ({brk:.0f}s)...")
                self._pause(brk)
                next_micro_break = battles_done + random.randint(12, 18)

            self._pause(random.uniform(5, 10))

        # Summary
        if battles_done > 0:
            summary = tr("log_battle_summary", done=battles_done, wins=wins, losses=losses, essence=total_essence_)
            self.log(summary)
            self.check_arena_rank_promotion(battles_done, wins, losses)
        else:

            self.log("No battles completed during this session.")

        # Claim any quests completed DURING the battles
        self.check_and_claim_quests()

    def stop(self):
        self._stop_requested = True  # run_daemon must not switch running back on after this
        self.running = False
        # Abort the current module too, so Stop doesn't wait for a whole cup fight
        self._module_abort.set()
        self.log(tr("log_stopping"))

    def check_status_only(self):
        self.auth_log_buffer.append(tr("log_session_load"))
        if not self.validate_session():
            if not self.login_and_steal_keys():
                return

        self.auth_log_buffer.append(tr("log_source_check"))
        try:
            res = self.session.get(CONFIG["urls"]["game"], timeout=10)
            if res.status_code == 200:
                html = res.text
                ore_raw, hits_raw, cost_raw = "0", "0", None
                b_cls = CONFIG["selectors"]["balance_class"]
                ore_match = re.search(f'class="[^"]*{b_cls}[^"]*">\\s*([\\d\\.\\s,kKmM]+)\\s*<', html)
                if ore_match: ore_raw = ore_match.group(1)

                e_cls = CONFIG["selectors"]["energy_class"]
                hits_match = re.search(f'class="[^"]*{e_cls}[^"]*">\\s*([\\d\\s]+)\\s*<', html)
                if hits_match: hits_raw = hits_match.group(1)

                price_match = re.search(r'class="[^"]*cost[^"]*">\s*([\d\.\s,kKmM]+)\s*<', html)
                if not price_match:
                    price_match = re.search(r'upgrade-btn.*?<span>([\d\.\s,kKmM]+)</span>', html, re.IGNORECASE)
                if price_match:
                    cost_raw = price_match.group(1)

                ore = parse_smart_number(ore_raw)
                hits = parse_smart_number(hits_raw)

                self.current_balance = ore
                self.update_stats(energy=hits, balance=ore)
                self.auth_log_buffer.append(tr("log_stat_energy", val=hits))
                self.auth_log_buffer.append(tr("log_stat_balance", val=f"{ore:,}"))

                if cost_raw:
                    self.auth_log_buffer.append(tr("log_upgrade_cost", cost=cost_raw))
                else:
                    self.auth_log_buffer.append(tr("log_upgrade_status_max"))

            else:
                self.log(f"⚠️ API Error: {res.status_code}")
        except Exception as e:
            self.log(tr("log_error_generic", e=e))

        if self.auth_log_buffer:
            header = tr("log_startup_header")
            body = "🔸 " + "\n🔸 ".join(self.auth_log_buffer)
            self.log(header + body)
            self.auth_log_buffer.clear()

        # Check and report Tower status
        self.check_tower_expedition()

        # Check and report Daily Ads & Reading stats
        try:
            res_bal = self.session.get("https://mangabuff.ru/balance", timeout=10)
            if res_bal.status_code == 200:
                soup_bal = BeautifulSoup(res_bal.text, "html.parser")
                ads_btn = soup_bal.find(class_=lambda c: c and "user-quest__watch-ads-btn" in c)
                if ads_btn:
                    ads_cnt = ads_btn.get("data-count", "0")
                    self.log(f"📺 Реклама: {ads_cnt}/3 просмотрено")
                dia_el = soup_bal.find(class_="menu__balance")
                if dia_el:
                    m_dia = re.search(r"([\d\s]+)", dia_el.get_text())
                    if m_dia:
                        self.diamonds_balance = parse_smart_number(m_dia.group(1))
                r_stats = self.get_reading_stats(soup=soup_bal)
                if r_stats:
                    cd_info = ""
                    if r_stats.get("card_ready"):
                        cd_info = " (✅ Карта готова к дропу)"
                    elif r_stats.get("card_cooldown"):
                        cd_info = f" (⏳ Кулдаун карты: {r_stats.get('card_cooldown')})"
                    self.log(f"📖 Чтение глав: {r_stats['chapters_read']}/{r_stats['chapters_max']} глав | {r_stats['cards_found']}/{r_stats['cards_max']} бонусных карт{cd_info}")

                ore_dia = self.current_balance // 100
                total_dia = self.diamonds_balance + ore_dia
                self.log(f"💰 Всего капитал: ~{total_dia:,} 💎 (Баланс: {self.diamonds_balance:,} 💎 + Руда: {self.current_balance:,} ≈ {ore_dia:,} 💎) [Цель: 30,000 💎]")
        except Exception:
            pass

        self.log(tr("log_done"))

    def _run_mining(self):
        """Phase 1: grind ore via /mine/hit until energy is depleted."""
        self.log(tr("log_mining_start"))

        clicks = 0
        total_added = 0
        consecutive_errors = 0
        initial_hits = None
        emptied = False

        # The mine page allows about one hit per second (tap animation + request); the global
        # 1.35-2.2 s spacing between requests made mining twice as slow as a person.
        pace = (self.session.min_request_interval, self.session.max_request_jitter)
        self.session.min_request_interval, self.session.max_request_jitter = 0.55, 0.35
        try:
            while self._alive():
                try:
                    response = self.session.post(self.API_URL, json={"hits": 1}, timeout=5)

                    if response.status_code == 200:
                        try:
                            data = response.json()
                            if data.get('status') is False:
                                self.log(f"⚠️ Mine rejected: {data}")
                                time.sleep(2)
                                continue

                            ore = data.get('ore', 0)
                            hits_left = data.get('hits_left', 0)
                            added = data.get('added', 0)

                            if initial_hits is None:
                                initial_hits = max(hits_left, 100)

                            self.current_balance = ore
                            clicks += 1
                            total_added += added
                            consecutive_errors = 0

                            self.update_stats(energy=hits_left, balance=ore)
                            prog = 1.0 - (hits_left / float(initial_hits))
                            self.update_progress(max(0.0, min(1.0, prog)))

                            if hits_left <= 0:
                                self.update_stats(energy=0, balance=ore)
                                emptied = True
                                break

                            time.sleep(random.uniform(0.30, 0.50))

                        except Exception as e:
                            self.log(f"⚠️ JSON Error: {e}")
                            time.sleep(2)
                            continue

                    else:
                        if response.status_code == 403:
                            if is_anti_bot_page(response):
                                self.log("🛡️ Шахта ответила страницей защиты (403) — попробую позже.")
                                break
                            self.log("⚡ Ore energy depleted (403). Moving to battles.")
                            self.update_stats(energy=0)
                            emptied = True
                            break
                        elif response.status_code == 419:
                            self.log("♻️ Session expired (419). Auto-refreshing...")
                            if self.login_and_steal_keys():
                                self.log("✅ Re-login successful! Resuming...")
                                consecutive_errors = 0
                                time.sleep(1)
                                continue
                            else:
                                self.log("❌ Re-login failed. Stopping.")
                                break
                        elif response.status_code == 429:
                            self.log("⚠️ Rate limited (429). Backing off 30s...")
                            time.sleep(30)
                            consecutive_errors = 0
                            continue
                        else:
                            self.log(f"⚠️ Server: {response.status_code}")
                            consecutive_errors += 1
                            time.sleep(2)
                            if consecutive_errors > 3:
                                self.log(tr("log_session_restart"))
                                if self.login_and_steal_keys():
                                    consecutive_errors = 0
                                    continue
                                break

                except Exception as e:
                    err_name = type(e).__name__
                    if "Timeout" in err_name or "timeout" in str(e).lower():
                        self.log(tr("log_timeout"))
                    else:
                        self.log(tr("log_error_generic", e=e))
                        time.sleep(1)
        finally:
            self.session.min_request_interval, self.session.max_request_jitter = pace

        if clicks > 0:
            self.log(tr("log_mining_summary", clicks=clicks, added=total_added))
            self.log(tr("log_stat_balance", val=f"{self.current_balance:,}"))
            self.notifier.notify_mining_summary(clicks, total_added, self.current_balance)

        # Check and purchase pickaxe upgrades with newly mined ore
        if self._alive():
            self.auto_upgrade_pickaxe()
        return emptied

    def run(self):
        """Main execution flow: claim daily, Tower expedition, mine ore, auto-upgrade, and farm battles."""
        self.running = True
        self.auth_log_buffer.append(tr("log_session_load"))

        # Step 1: Load saved session
        if not self.validate_session():
            self.auth_log_buffer.append(tr("log_session_expired"))
            if not self.login_and_steal_keys():
                self.running = False
                return

        # Step 2: Validate session with server
        if not self._validate_session_with_server():
            self.auth_log_buffer.append(tr("log_session_expired"))
            if not self.login_and_steal_keys():
                self.running = False
                return

        # Notify Telegram about start
        self.notifier.notify_session_start(self.email, ore=self.current_balance)

        # Login streak («Дней подряд») + monthly calendar reward
        self.ping_daily_visit()
        self.claim_daily_reward()

        # Check trade proposals & clean unwanted MangaBuff notifications
        self.process_mangabuff_notifications()

        if self.auth_log_buffer:
            header = tr("log_startup_header")
            body = "🔸 " + "\n🔸 ".join(self.auth_log_buffer)
            self.log(header + body)
            self.auth_log_buffer.clear()

        # Watch daily ads (3x7 💎)
        self.watch_daily_ads()

        # Check Tower of Rift expeditions (claim & start)
        self.check_tower_expedition()

        # Phase 1: Ore mining & auto-upgrade
        self._run_mining()

        # Phase 2: Battle farming
        if self.running:
            self.run_farm_loop()

        # Phase 3: Manga chapter reading (bonus cards & quest)
        if self.running:
            self.read_manga_chapters()

        # Claim completed daily quests
        self.check_and_claim_quests()

        # Re-check Tower expedition status
        self.check_tower_expedition()

        self.running = False
        self.log(tr("log_mining_finish"))

    def _pause(self, seconds):
        """Sleep inside a module; returns early on stop or module abort."""
        end = time.monotonic() + seconds
        while self._alive() and time.monotonic() < end:
            self._touch_heartbeat()
            time.sleep(0.25)

    def _sleep_alive(self, seconds):
        """Sleep in 1 s slices, keeping the watchdog fed and honoring stop().

        Wall-clock based so a PC that slept through the wait does not resume it.
        """
        end = time.time() + seconds
        while self.running and time.time() < end:
            self._touch_heartbeat()
            time.sleep(1)

    def _authorize_with_backoff(self):
        """
        Log in at daemon start. On failure wait 5 → 10 → 20 → 40 → 60 min and retry
        instead of exiting: an exit made systemd restart us every ~10 s, i.e. a fresh
        login attempt every few seconds, which is exactly what gets an account flagged.
        """
        from html import escape

        attempt = 0
        while self.running:
            if (self.validate_session() and self._validate_session_with_server()) or self.login_and_steal_keys():
                if attempt:
                    self.log(f"✅ Вход восстановлен с {attempt + 1}-й попытки.")
                    self.notifier.notify_alert(
                        "вход на MangaBuff восстановлен",
                        f"Авторизация прошла с {attempt + 1}-й попытки, продолжаю работу.",
                    )
                return True

            attempt += 1
            reason = self.auth_log_buffer[-1] if self.auth_log_buffer else "нет подробностей"
            for line in self.auth_log_buffer:
                self.log(f"🔸 {line}")
            self.auth_log_buffer.clear()

            wait_min = min(60, 5 * 2 ** (attempt - 1))
            self.log(f"❌ Авторизация не удалась (попытка {attempt}). Повтор через ~{wait_min} мин.")
            # First failure, then about every 6 hours while it stays broken
            if attempt == 1 or attempt % 6 == 0:
                self.notifier.notify_alert(
                    "не удаётся войти на MangaBuff",
                    f"Причина: {escape(str(reason))}\n"
                    f"Попытка {attempt}, следующая через ~{wait_min} мин.\n"
                    "Проверь логин/пароль в user_data.json или прокси.",
                )
            wait_sec = wait_min * 60 + random.randint(10, 60)
            self._persist_panel_state(next_action="Повторный вход в аккаунт", next_wait_sec=wait_sec)
            self._sleep_alive(wait_sec)
        return False

    def _mining_step(self, msk_today, out):
        """Read mine energy/ore; dig when energy has built up. Sets out["energy"] (0 after digging)."""
        res_game = self.session.get(CONFIG["urls"]["game"], timeout=10)
        hits_match = None
        if res_game.status_code == 200:
            e_cls = CONFIG["selectors"]["energy_class"]
            hits_match = re.search(f'class="[^"]*{e_cls}[^"]*">\\s*([\\d\\s]+)\\s*<', res_game.text)
            if hits_match:
                out["energy"] = parse_smart_number(hits_match.group(1))

            b_cls = CONFIG["selectors"]["balance_class"]
            ore_match = re.search(f'class="[^"]*{b_cls}[^"]*">\\s*([\\d\\.\\s,kKmM]+)\\s*<', res_game.text)
            if ore_match:
                self.current_balance = parse_smart_number(ore_match.group(1))
        elif res_game.status_code in (401, 419):
            self.login_and_steal_keys()

        if out["energy"] > 0:
            self.log(f"⚡ Накопилась энергия ({out['energy']}). Запуск добычи руды...")
            emptied = self._run_mining()
            if self._alive():
                self.auto_upgrade_pickaxe()
                if emptied:
                    self._energy_exhausted_date = msk_today
            out["mined"] = bool(emptied)
            out["energy"] = 0 if emptied else out["energy"]
        elif res_game.status_code == 200 and hits_match is not None:
            self._energy_exhausted_date = msk_today

    def _ads_step(self, msk_today, out):
        """Watch the daily ads, then read the ad counter and diamonds from /balance."""
        self.watch_daily_ads()
        try:
            res_bal_check = self.session.get("https://mangabuff.ru/balance", timeout=8)
            if res_bal_check.status_code == 200:
                soup_bal = BeautifulSoup(res_bal_check.text, "html.parser")
                btn = soup_bal.find(class_=lambda c: c and "user-quest__watch-ads-btn" in c)
                out["available"] = btn is not None
                if btn:
                    out["ads_count"] = int(btn.get("data-count", 0))
                    if out["ads_count"] >= 3:
                        self._ads_completed_date = msk_today
                else:
                    # Not every account has ads on /balance — don't re-check all day
                    self._ads_completed_date = msk_today

                dia_el = soup_bal.find(class_="menu__balance")
                if dia_el:
                    m_dia = re.search(r"([\d\s]+)", dia_el.get_text())
                    if m_dia:
                        self.diamonds_balance = parse_smart_number(m_dia.group(1))
        except Exception:
            pass

    # ------------------------------------------------------------ manual runs
    # Modules the desktop app can run once on demand: name -> (title, time limit)
    MANUAL_MODULES = {
        "daily": ("Ежедневные награды", 3 * 60),
        "mining": ("Шахта", 25 * 60),
        "arena": ("Арена", ARENA_MODULE_SECONDS),
        "ads": ("Реклама", 5 * 60),
        "tower": ("Башня Разлома", 8 * 60),
        "reading": ("Чтение глав", 20 * 60),
        "club": ("Клуб", 4 * 60),
        "seasonal": ("Ивенты", 2 * 60),
        "awaken": ("Перебор пробуждения", 15 * 60),
        "seals": ("Перековка печати", 15 * 60),
    }

    def request_manual_module(self, name):
        """Queue a one-off run and cut the current step short so it starts right away.

        The interrupted step simply runs again next cycle; a live cup fight is never cut.
        """
        if name not in self.MANUAL_MODULES:
            return False
        if name not in self._manual_queue:
            self._manual_queue.append(name)
        current = getattr(self, "_current_module", None)
        if current and current != "evening_cup" and not getattr(self, "_draining", False):
            self._module_abort.set()
        return True

    def _drain_manual_queue(self):
        if getattr(self, "_cup_fight_live", False):
            return  # a manual step could take minutes and lose the fight on the turn timer
        self._draining = True
        try:
            while self._manual_queue and self.running:
                self.run_manual_module(self._manual_queue.popleft())
        finally:
            self._draining = False

    def run_manual_module(self, name):
        """Run one module once and return a one-line summary (also logged)."""
        title, limit = self.MANUAL_MODULES[name]
        if self.manual_callback:
            self.manual_callback(name, "running", "")
        result = {}
        started = time.time()
        self._run_module(name, lambda: result.update(summary=self._manual_step(name)), limit)
        summary = result.get("summary") or "не получилось — подробности в журнале"
        session = getattr(self, "session", None)
        last_ok = getattr(session, "last_ok_at", None)
        last_try = getattr(session, "_last_request_at", None)
        if (isinstance(last_ok, (int, float)) and isinstance(last_try, (int, float))
                and last_try >= started > last_ok and result.get("summary")):
            # Nothing came back from the site during the run: the summary would be built from empty data
            summary = ManualFailed("сайт не ответил — проверить не получилось. Запусти ещё раз позже")
        self.log(f"🟣 Ручной запуск «{title}»: {summary}")
        if self.manual_callback:
            ok = result.get("summary") and not isinstance(summary, ManualFailed)
            self.manual_callback(name, "done" if ok else "error", summary)
        try:
            if self.running:  # after Stop/Logout this could log the old account back in
                self.refresh_live_panel()
        except Exception:
            pass
        return summary

    def _manual_step(self, name):
        from datetime import datetime, timezone, timedelta

        msk_today = datetime.now(timezone(timedelta(hours=3))).date()
        if name == "daily":
            self.ping_daily_visit()
            if DataManager.get_setting("visit_ping_day") == str(msk_today):
                visit = "визит засчитан"
            elif self._msk_now().hour < 2:
                visit = "визит сайт засчитывает только после 02:00 МСК — бот отметит его сам"
            else:
                visit = "визит не засчитался — сайт не ответил"
            if not self._find_active_claim_url():
                if getattr(self, "_balance_unavailable", False):
                    return ManualFailed(f"{visit}; страница наград не открылась — проверить награду дня не получилось. "
                                        "Запусти ещё раз позже")
                text = (f"{visit}. Награды из календаря сейчас нет: она уже забрана, или календарь "
                        "ещё закрыт — он открывается с PRO, донатом от 300 ₽ или через 3 месяца при рейтинге от 300")
                return ManualFailed(text) if visit.startswith("визит не") else text
            self.claim_daily_reward()
            if self.daily_reward_claimed and not visit.startswith("визит не"):
                return f"{visit}, награда дня забрана"
            reward = "награда дня забрана" if self.daily_reward_claimed else "награду дня забрать не получилось"
            return ManualFailed(f"{visit}; {reward}. Работающий бот повторит сам, или запусти ещё раз позже")
        if name == "seals":
            rolls = self.run_seal_reroll(max_rolls=40)
            goal = DataManager.get_setting("seal_reroll") or {}
            left = [c for c in goal.get("cards") or [] if c.get("status") not in ("reached", "missing", "refused")]
            if not left:
                return f"перековок: {rolls}, все выбранные карты получили нужную печать"
            return f"перековок: {rolls}; продолжу позже" if rolls else \
                ManualFailed("эссенции не хватает или достигнут лимит — бот продолжит, когда она появится")
        if name == "awaken":
            rolls = self.run_awaken_reroll(max_rolls=40)
            goal = DataManager.get_setting("awaken_reroll") or {}
            left = [c["name"] for c in goal.get("cards") or [] if c.get("status") not in ("reached", "missing", "refused")]
            if not left:
                return f"бросков: {rolls}, все выбранные карты готовы"
            return f"бросков: {rolls}; продолжу, когда бои накопят энергию" if rolls else                 ManualFailed("энергии пробуждения нет — бот продолжит, когда бои её накопят")
        if name == "mining":
            out = {"energy": 0}
            self._mining_step(msk_today, out)
            if out.get("mined"):
                return f"руда добыта, на балансе {int(self.current_balance or 0):,}".replace(",", " ")
            if out.get("energy"):
                return ManualFailed(f"добыть не получилось, энергии осталось {out['energy']}. "
                                    "Работающий бот повторит через несколько минут, или запусти ещё раз позже")
            return (f"энергии шахты {out['energy']} — копать нечего. "
                    "Энергия появляется раз в день, после полуночи МСК")
        if name == "arena":
            self.run_arena_cycle()
            return "арена проверена, бои проведены, если карты были готовы"
        if name == "ads":
            out = {"ads_count": None}
            self._ads_step(msk_today, out)
            if out.get("available") is False:
                return "на этом аккаунте реклама за алмазы недоступна"
            if out["ads_count"] is None:
                return ManualFailed("счётчик рекламы не загрузился — сколько засчитано, неизвестно. Запусти ещё раз позже")
            return f"просмотрено {out['ads_count']}/3 за сегодня"
        if name == "tower":
            left = self.check_tower_expedition()
            if left:
                h, m = divmod(max(60, int(left)) // 60, 60)
                return f"отряд в Башне, вернётся через {f'{h} ч {m} мин' if h else f'{m} мин'}"
            if not getattr(self, "_tower_page_ok", True):
                return ManualFailed("страница Башни не открылась — проверить не получилось. Запусти ещё раз позже")
            if getattr(self, "_tower_claim_failed", False):
                return ManualFailed("экспедиция закончилась, но забрать награду не получилось. Запусти ещё раз позже")
            if getattr(self, "_tower_start_failed", False):
                return ManualFailed("отправить отряд в Башню не получилось — сайт не принял. Запусти ещё раз позже")
            return "Башня проверена"
        if name == "reading":
            out = {"card_wait_sec": 45 * 60, "chapters_wait_sec": 0, "ch_read": 0,
                   "ch_max": 75, "cards_found": 0, "cards_max": 10}
            self._reading_step(out)
            if not out.get("stats_ok"):
                return ManualFailed("прогресс чтения не загрузился со страницы /balance — сколько прочитано, неизвестно. "
                                    "Запусти ещё раз позже")
            line = (f"главы {out['ch_read']}/{out['ch_max']}, "
                    f"бонусные карты {out['cards_found']}/{out['cards_max']}")
            if out["cards_found"] >= out["cards_max"]:
                return line + " — на сегодня всё собрано"
            return line + f". Следующая карта примерно через {max(1, int(out['card_wait_sec']) // 60)} мин"
        if name == "club":
            self.run_club_cycle()
            return "клуб проверен"
        if name == "seasonal":
            live = self.run_seasonal_watch(force=True)  # a click must really check, not skip by interval
            return "ивент идёт!" if live else "ивентов пока нет"
        raise ValueError(name)

    def _reading_step(self, out):
        """One reading pass: chapters toward the daily quest and bonus-card drops; updates out in place."""
        self._last_read_count = None  # stays None unless this pass reads (normal reads drop candies too)
        card_wait_sec, chapters_wait_sec, ch_read, ch_max, cards_found, cards_max = (out["card_wait_sec"], out["chapters_wait_sec"], out["ch_read"], out["ch_max"], out["cards_found"], out["cards_max"])
        try:
            reading_stats = self.get_reading_stats()
            if not reading_stats and self._keep_logged_in(force=True):
                reading_stats = self.get_reading_stats()  # a dropped session looks like a page without stats
            out["stats_ok"] = bool(reading_stats)
            if not reading_stats:
                self.log('⚠️ /balance недоступен или разметка изменилась — чтение отложено, cache только для панели.')

            if reading_stats:
                ch_read = int(reading_stats.get("chapters_read") or 0)
                ch_max = int(reading_stats.get("chapters_max") or 75)
                cards_found = int(reading_stats.get("cards_found") or 0)
                cards_max = int(reading_stats.get("cards_max") or 10)
                card_ready = bool(reading_stats.get("card_ready", False))
                cd_str = reading_stats.get("card_cooldown") or ""

                midnight_lag = (
                    ch_read >= ch_max
                    and cards_found < cards_max
                    and self._msk_now().hour < 6
                )

                if midnight_lag and not card_ready:
                    self.log(
                        f"⏳ На сайте главы ещё {ch_read}/{ch_max} после полуночи "
                        f"(карты {cards_found}/{cards_max}) — ждём сброса счётчика глав."
                    )
                    chapters_wait_sec = random.randint(15 * 60, 25 * 60)
                elif ch_read < ch_max:
                    batch_size = min(15, ch_max - ch_read)
                    self.log(f"📖 Дневной лимит глав ({ch_read}/{ch_max}): читаем пачку из {batch_size} глав...")
                    self.read_manga_chapters(target_count=batch_size)
                    fresh_stats = self.get_reading_stats()
                    if fresh_stats:
                        reading_stats = fresh_stats
                        ch_read = int(fresh_stats.get("chapters_read") or ch_read)
                        ch_max = int(fresh_stats.get("chapters_max") or ch_max)
                        cards_found = int(fresh_stats.get("cards_found") or cards_found)
                        card_ready = bool(fresh_stats.get("card_ready", False))
                        cd_str = fresh_stats.get("card_cooldown") or ""

                    if ch_read < ch_max:
                        chapters_wait_sec = random.randint(3 * 60, 5 * 60)
                    else:
                        chapters_wait_sec = get_seconds_until_midnight_msk()
                else:
                    chapters_wait_sec = get_seconds_until_midnight_msk()

                cards_off = DataManager.get_setting("bonus_cards_off_date") == str(self._msk_now().date())
                if cards_found >= cards_max:
                    card_wait_sec = get_seconds_until_midnight_msk()
                    self.log(f"🃏 Все бонусные карты за сегодня собраны ({cards_found}/{cards_max}).")
                elif card_ready and cards_off:
                    card_wait_sec = get_seconds_until_midnight_msk()
                elif card_ready:
                    self.log(
                        f"🃏 Бонусная карта готова к дропу ({cards_found}/{cards_max})! "
                        f"Читаем до выпадения (сайт: главы {ch_read}/{ch_max})..."
                    )
                    self.read_manga_chapters(target_count=10)
                    fresh_r = self.get_reading_stats()
                    if fresh_r:
                        reading_stats = fresh_r
                        ch_read = int(fresh_r.get("chapters_read") or ch_read)
                        ch_max = int(fresh_r.get("chapters_max") or ch_max)
                        before = cards_found
                        cards_found = int(fresh_r.get("cards_found") or cards_found)
                        # "Ready" but nothing drops: accounts without access (e.g. no VIP) never get them.
                        # Counted in settings so restarts of the app don't reset it.
                        today = str(self._msk_now().date())
                        dry = DataManager.get_setting("bonus_cards_dry") or {}
                        earlier = int(dry.get("chapters") or 0) if dry.get("date") == today else 0
                        dry = {"date": today, "chapters": 0 if cards_found > before else earlier + getattr(self, "_last_read_count", 0)}
                        DataManager.set_setting("bonus_cards_dry", dry)
                        if dry["chapters"] >= 30:
                            DataManager.set_setting("bonus_cards_off_date", str(self._msk_now().date()))
                            self.log("🃏 Бонусные карты не выпадают — похоже, аккаунту они пока недоступны "
                                     "(у новых аккаунтов сайт ставит дополнительные условия). "
                                     "До завтра читаю только ради 75 глав за алмазы.")
                        if not fresh_r.get("card_ready", True):
                            card_wait_sec = parse_card_cooldown_seconds(fresh_r.get("card_cooldown", "")) or (45 * 60)
                        else:
                            card_wait_sec = 3 * 60
                    if ch_read < ch_max:
                        chapters_wait_sec = random.randint(3 * 60, 5 * 60)
                else:
                    card_wait_sec = parse_card_cooldown_seconds(cd_str) or (45 * 60)
                    self.log(
                        f"⏳ Бонусные карты на кулдауне ({cd_str or 'ожидание'}). "
                        f"Следующая проверка через ~{card_wait_sec // 60} мин."
                    )
            else:
                chapters_wait_sec = random.randint(10 * 60, 15 * 60)
                ch_read, ch_max = 0, 75
                self.log("⚠️ Стата чтения недоступна — повтор через ~10–15 мин.")
            if (reading_stats and ch_read >= ch_max and self._last_read_count is None
                    and self._event_candies_wanted()):
                # Halloween: reading drops a candy at most every 3 min, up to 35 a day
                before = event_candy_day(self._msk_now().date())["n"]
                self.read_manga_chapters(target_count=2, for_event=True)
                day = event_candy_day(self._msk_now().date())
                accepted = (self._last_read_count or 0) > 0
                if day["n"] > before:
                    day["misses"] = 0
                elif accepted:
                    day["misses"] = day.get("misses", 0) + 1
                DataManager.set_setting("halloween_candy_day", day)
                # a read the site did not accept says nothing about drops: retry later, not every 3 min
                out["event_wait_sec"] = random.randint(190, 240) if accepted else random.randint(8 * 60, 12 * 60)
            elif reading_stats and ch_read >= ch_max and self._event_candies_wanted():
                # This pass read for a bonus card; come back for candies in minutes, not after its 45 min cooldown
                out["event_wait_sec"] = random.randint(190, 240)
        finally:
            out.update(card_wait_sec=card_wait_sec, chapters_wait_sec=chapters_wait_sec, ch_read=ch_read, ch_max=ch_max, cards_found=cards_found, cards_max=cards_max)

    def run_daemon(self):
        """Smart event-driven scheduler daemon. Runs 24/7, sleeping until the earliest event."""
        if getattr(self, "_stop_requested", False):
            return  # Stop came before this thread started
        self.running = True
        self._cup_fight_live = bool(DataManager.load_json(os.path.join(BASE_DIR, 'manual_arena_state.json')).get('active_fight'))
        self._touch_heartbeat()
        self._start_watchdog()
        self.log("🚀 MangaBuff Miner — Запуск автономного Smart Daemon (24/7)")
        self._persist_panel_state(next_action=None, next_wait_sec=0)

        # Validate session at start (waits and retries instead of exiting on failure)
        if not self._authorize_with_backoff():
            return

        # Warm / refresh DDoS-Guard cookies up front (proxy required on VPS)
        self.ensure_site_access()
        self.refresh_profile()
        self.refresh_halloween_info()

        # Fill the stats panel now instead of after the first full cycle
        try:
            if self.refresh_live_panel():
                self.log("📊 Статистика аккаунта обновлена.")
        except Exception as e:
            self.log(f"⚠️ Не удалось обновить статистику: {e}")

        if self.auth_log_buffer:
            header = tr("log_startup_header")
            body = "🔸 " + "\n🔸 ".join(self.auth_log_buffer)
            self.log(header + body)
            self.auth_log_buffer.clear()

        while self.running:
            try:
                from datetime import datetime, timezone, timedelta
                msk_today = datetime.now(timezone(timedelta(hours=3))).date()
                self._touch_heartbeat()
                # Awake now: the app must not keep showing "resting until" from the last sleep
                self._persist_panel_state(next_action=None, next_wait_sec=0)

                # One probe per backoff window — hammering check.js makes DDoS-Guard stricter.
                self._wait_if_anti_bot_paused()
                if self._anti_bot_down_since:
                    if not self.ensure_site_access():
                        self.log("🛡️ Сайт всё ещё на проверке. Жду паузу, без лишних запросов.")
                        if self._anti_bot_paused_until <= time.time():
                            self._anti_bot_paused_until = time.time() + random.randint(180, 300)
                        continue

                # After a long idle, one homepage hit before the work burst
                # so DDoS-Guard cookies don't go cold and trip a challenge.
                last_ok = float(getattr(self.session, "last_ok_at", 0) or 0)
                if last_ok and time.time() - last_ok > 40 * 60:
                    self.ensure_site_access()
                self._keep_logged_in()

                # 0. Login streak («Дней подряд») + monthly calendar reward
                # 0. Evening Cup (19:00 - 22:00 MSK only)
                cup_busy = [False]
                cup_now_h = self._msk_now().hour
                if not (19 <= cup_now_h < 22 and DataManager.get_setting('arena_cup_enabled', True)):
                    self._cup_fight_live = False
                if 19 <= cup_now_h < 22 and DataManager.get_setting('arena_cup_enabled', True):
                    cup_failed = [False]

                    def _mod_cup():
                        try:
                            cup_busy[0] = self.run_evening_cup()
                        except Exception:
                            cup_failed[0] = True
                            raise
                        finally:
                            cup_busy[0] = cup_busy[0] or bool(DataManager.load_json(os.path.join(BASE_DIR, 'manual_arena_state.json')).get('active_fight'))
                    self._run_module('evening_cup', _mod_cup, 5 * 60)
                    self._cup_fight_live = bool(cup_busy[0])
                    self._cup_fail_streak = getattr(self, '_cup_fail_streak', 0) + 1 if cup_failed[0] else 0
                    if self._cup_fail_streak >= 3 and not self._validate_session_with_server():
                        self.log("🔑 Кубок: сессия слетела — вхожу заново")
                        self.login_and_steal_keys()
                    if cup_busy[0]:
                        # A failing cup page must not be hammered every second (that earns a 429)
                        wait = 30 if cup_failed[0] else min(30, max(1, getattr(self, '_cup_next_check_at', 0) - time.time()))
                        self._persist_panel_state(next_action="Вечерний кубок", next_wait_sec=wait)
                        self._sleep_alive(wait)
                        continue



                def _mod_daily():
                    self.ping_daily_visit()
                    self.claim_daily_reward()

                self._run_module("daily", _mod_daily, 3 * 60)

                # Trades / notif cleanup — at most once per ~10 min outside sleep polls
                if time.time() - self._last_notif_poll_at >= 10 * 60:
                    self._run_module(
                        "notifications",
                        self.process_mangabuff_notifications,
                        5 * 60,
                    )
                    self._last_notif_poll_at = time.time()

                # 1. Mining & Energy check
                energy = 0
                if getattr(self, "_energy_exhausted_date", None) != msk_today:
                    mining_out = {"energy": 0}

                    def _mod_mining():
                        self._mining_step(msk_today, mining_out)

                    self._run_module("mining", _mod_mining, 25 * 60)
                    energy = mining_out["energy"]

                # Arena module (checks battles and weekly seasonal chests)
                arena_now = time.time()
                if arena_now >= getattr(self, '_arena_next_check_at', 0):
                    self._arena_next_check_at = arena_now + 30 * 60
                    self._run_module('arena', self.run_arena_cycle, ARENA_MODULE_SECONDS)


                # 2. Daily Ads check (3x7 💎)
                ads_cnt = 3
                if self.running and DataManager.get_setting("ads_enabled", True):
                    if getattr(self, "_ads_completed_date", None) != msk_today:
                        ads_out = {"ads_count": ads_cnt}

                        def _mod_ads():
                            self._ads_step(msk_today, ads_out)

                        self._run_module("ads", _mod_ads, 5 * 60)
                        ads_cnt = ads_out["ads_count"]
                    else:
                        ads_cnt = 3

                # 3. Abyss Tower — skip re-fetch while expedition has long time left
                tower_left_sec = None
                if self.running and DataManager.get_setting("tower_enabled", True):
                    def _mod_tower():
                        nonlocal tower_left_sec
                        now_ts = time.time()
                        cached_left = int(self._tower_ends_at - now_ts) if self._tower_ends_at else 0
                        if (
                            cached_left > 20 * 60
                            and now_ts < self._tower_next_check_at
                        ):
                            tower_left_sec = max(60, cached_left)
                        else:
                            t_sec = self.check_tower_expedition()
                            if t_sec is not None and t_sec > 0:
                                tower_left_sec = max(60, t_sec)
                                self._tower_ends_at = now_ts + tower_left_sec
                                if tower_left_sec > 40 * 60:
                                    self._tower_next_check_at = now_ts + random.randint(15 * 60, 20 * 60)
                                else:
                                    self._tower_next_check_at = now_ts + min(
                                        tower_left_sec,
                                        random.randint(6 * 60, 10 * 60),
                                    )
                            else:
                                self._tower_ends_at = 0.0
                                self._tower_next_check_at = now_ts + random.randint(8 * 60, 12 * 60)

                    self._run_module("tower", _mod_tower, 8 * 60)

                # 4. Manga Reading check (cards & 75-chapter quest).
                card_wait_sec = 60 * 45
                chapters_wait_sec = 0
                ch_read = 0
                ch_max = 75
                cards_found = 0
                cards_max = 10
                event_wait_sec = None
                if self.running and DataManager.get_setting("reading_enabled", True):
                    reading_out = {"card_wait_sec": card_wait_sec, "chapters_wait_sec": chapters_wait_sec, "ch_read": ch_read, "ch_max": ch_max, "cards_found": cards_found, "cards_max": cards_max}

                    def _mod_reading():
                        self._reading_step(reading_out)

                    self._run_module("reading", _mod_reading, 20 * 60)
                    card_wait_sec, chapters_wait_sec, ch_read, ch_max, cards_found, cards_max = (reading_out["card_wait_sec"], reading_out["chapters_wait_sec"], reading_out["ch_read"], reading_out["ch_max"], reading_out["cards_found"], reading_out["cards_max"])
                    event_wait_sec = reading_out.get("event_wait_sec")
                # Clean up site notifications after reading (card drops, etc.) — skip if just polled.
                if time.time() - self._last_notif_poll_at >= 8 * 60:
                    self._run_module(
                        "notifications",
                        self.process_mangabuff_notifications,
                        5 * 60,
                    )
                    self._last_notif_poll_at = time.time()

                # 4.5. Club operations (crystals & card donation alerts)
                if self.running and DataManager.get_setting("club_enabled", True):
                    now_ts = time.time()
                    if now_ts - getattr(self, "_last_club_check_at", 0) >= self.club_check_interval():
                        self._run_module("club", self.run_club_cycle, 4 * 60)
                        self._last_club_check_at = now_ts

                # 4.6. Seasonal pages watch (Halloween / event) — TG when they go live
                if self.running and DataManager.get_setting("seasonal_watch_enabled", True):
                    self._run_module("seasonal", self.run_seasonal_watch, 2 * 60)
                # Owner-only watcher for new site features; off unless enabled in settings
                if self.running and DataManager.get_setting("site_watch_enabled", False):
                    self._run_module("site_watch", self.run_site_watch, 3 * 60)
                if (self.running and DataManager.get_setting("halloween_event_enabled", True)
                        and time.time() - getattr(self, "_halloween_event_at", 0) >= 30 * 60):
                    self._halloween_event_at = time.time()
                    self._run_module("halloween_event", self.run_halloween_event, 2 * 60)
                if self.running and DataManager.get_setting("halloween_watch_enabled", False):
                    self._run_module("halloween_watch", self.run_halloween_watch, 2 * 60)

                # 5. Mine wait: energy is once per day at 00:00:10 MSK!
                mine_wait_sec = get_seconds_until_midnight_msk() if energy <= 0 else 60

                # 6. Ads wait (resets at midnight MSK if all 3 watched, else short retry)
                ads_wait_sec = get_seconds_until_midnight_msk() if ads_cnt >= 3 else 120

                candidates = [
                    ("Шахта", mine_wait_sec),
                    ("Реклама", ads_wait_sec),
                    ('Арена', max(1, getattr(self, '_arena_next_check_at', time.time()) - time.time())),
                ]
                if DataManager.get_setting("club_enabled", True):
                    candidates.append(('Клуб (вклады)', max(60, int(self.club_check_interval() - (time.time() - getattr(self, '_last_club_check_at', 0))))))


                if DataManager.get_setting('arena_cup_enabled',True):
                    cup_now=self._msk_now()
                    if 19<=cup_now.hour<22:
                        cup_at=max(getattr(self,'_cup_next_check_at',0),getattr(self,'_cup_retry_at',0))
                        candidates.append(('Вечерний кубок',max(1,cup_at-time.time())))
                    else:
                        cup_open=cup_now.replace(hour=19,minute=0,second=0,microsecond=0)
                        if cup_open<=cup_now:cup_open+=timedelta(days=1)
                        candidates.append(('Вечерний кубок',(cup_open-cup_now).total_seconds()))
                if tower_left_sec is not None:
                    candidates.append(("Башня", tower_left_sec))
                if event_wait_sec:
                    candidates.append(("Конфеты за чтение", event_wait_sec))

                if not DataManager.get_setting("reading_enabled", True):
                    pass  # reading off: its zeroed counters must not wake the bot every few minutes
                elif ch_read < ch_max:
                    if chapters_wait_sec <= 0:
                        chapters_wait_sec = random.randint(3 * 60, 5 * 60)
                    candidates.append(("Главы (до 75)", chapters_wait_sec))
                else:
                    if chapters_wait_sec and chapters_wait_sec < 40 * 60 and cards_found < cards_max:
                        candidates.append(("Сброс глав (сайт)", chapters_wait_sec))
                    if cards_found < cards_max:
                        if card_wait_sec <= 0:
                            card_wait_sec = 5 * 60
                        candidates.append(("Карта", card_wait_sec))

                positive = [(name, s) for name, s in candidates if s > 0]
                if not positive:
                    next_name, sleep_sec = "Полночь МСК", get_seconds_until_midnight_msk()
                else:
                    next_name, sleep_sec = min(positive, key=lambda x: x[1])

                # Floor sleeps so we never wake every minute with a full request burst.
                sleep_sec = max(180, int(sleep_sec))
                # Small human jitter, but never push midnight waits past ~+1 min
                sleep_sec += random.randint(20, 55)

                w_h = sleep_sec // 3600
                w_m = (sleep_sec % 3600) // 60
                w_s = sleep_sec % 60
                if w_h > 0:
                    wait_human = f"{w_h}ч {w_m}м"
                else:
                    wait_human = f"{w_m} мин {w_s} сек"
                self.log(f"💤 Все задачи проверены. Ближайшее действие: {next_name} (~{wait_human}). Ухожу в сон.")

                # Heartbeat notification to Telegram once per cycle
                if tower_left_sec is not None:
                    t_h = tower_left_sec // 3600
                    t_m = (tower_left_sec % 3600) // 60
                    tower_str = f"{t_h}ч {t_m}м" if t_h > 0 else f"{t_m}м"
                else:
                    tower_str = "Отключена / Не настроена"

                cards_cnt_str = f"{cards_found}/{cards_max}"
                chapters_str = f"{ch_read}/{ch_max}"
                ore_bal = int(self.current_balance or 0)
                dia_bal = int(self.diamonds_balance or 0) if self.diamonds_balance is not None else None
                total_dia = (dia_bal + ore_bal // 100) if dia_bal is not None else (ore_bal // 100)

                self._persist_panel_state(
                    next_action=next_name,
                    next_wait_sec=sleep_sec,
                    energy=energy,
                    ads_count=ads_cnt,
                    tower_str=tower_str,
                    ore=ore_bal,
                    diamonds=dia_bal,
                    diamonds_total=total_dia,
                    chapters_str=chapters_str,
                    cards_str=cards_cnt_str,
                )

                self.notifier.notify_cycle_heartbeat(
                    energy=energy,
                    balance=ore_bal,
                    ads_count=ads_cnt,
                    cards_count=cards_cnt_str,
                    tower_str=tower_str,
                    next_action=next_name,
                    next_wait_min=max(1, sleep_sec // 60),
                    diamonds=dia_bal,
                    chapters_str=chapters_str
                )

                # Non-blocking sleep: checks self.running every 1 sec.
                # Trades every 12 min (was 6) — fewer hits, fewer DDoS-Guard checks.
                # Once ~45 min, a single homepage keepalive so cookies don't expire mid-sleep.
                # A wall-clock deadline, not a tick count: after the PC sleeps through the wait,
                # the bot gets to work at once instead of sitting out the remaining ticks.
                wake_at = time.time() + int(sleep_sec)
                tick = -1
                while time.time() < wake_at:
                    tick += 1
                    if not self.running:
                        break
                    self._touch_heartbeat()
                    if self._manual_queue:
                        self._drain_manual_queue()
                    if time.time() < self._anti_bot_paused_until:
                        time.sleep(1)
                        continue
                    if tick > 0 and tick % 720 == 0:
                        try:
                            self.process_mangabuff_notifications()
                            self._last_notif_poll_at = time.time()
                        except Exception:
                            pass
                    elif tick > 0 and tick % 180 == 0 and DataManager.get_setting("halloween_watch_enabled", False):
                        try:
                            self.run_halloween_watch()  # the opening must not wait for a long sleep to end
                        except Exception:
                            pass
                    elif tick > 0 and tick % 2700 == 0:
                        try:
                            self.session.get("https://mangabuff.ru/", timeout=20)
                        except Exception:
                            pass
                    time.sleep(1)

            except Exception as e:
                self.log(f"⚠️ Ошибка в Daemon цикле: {e}")
                for _ in range(30):
                    if not self.running:
                        break
                    self._touch_heartbeat()
                    time.sleep(1)

    def _validate_session_with_server(self):
        """Make a lightweight GET request to verify the session is still valid."""
        try:
            res = self.session.get(CONFIG["urls"]["game"], timeout=10)
            if res.status_code == 200:
                if "/login" in res.url.lower() or "auth-form" in res.text.lower():
                    self.auth_log_buffer.append("⚠️ Session validation failed: redirected to login")
                    return False
                self.auth_log_buffer.append(tr("log_session_valid"))
                return True
            elif res.status_code in (401, 419, 403):
                self.auth_log_buffer.append(f"⚠️ Session validation failed with status {res.status_code}")
                return False
            else:
                self.auth_log_buffer.append(f"⚠️ Session validation returned status {res.status_code}")
                return False
        except Exception as e:
            self.auth_log_buffer.append(f"⚠️ Session validation error: {e}")
            return False
