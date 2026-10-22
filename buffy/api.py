# -*- coding: utf-8 -*-
"""Bridge between the Buffy web UI and the bot engine.

Every public method here is callable from JavaScript as
``window.pywebview.api.<name>(...)``; internals start with ``_`` so
pywebview does not expose them.
"""
from __future__ import annotations

import collections
import logging
import logging.handlers
import os
import platform
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urlsplit

import requests

from buffy import APP_NAME, REPO_URL, __version__
from buffy.humanize import MODULES as MODULE_TITLES, humanize
from data_management import DataManager
from diagnostics import mask_secrets
from buffy.diagnostics import save_report
from utils import normalize_proxy_url

MSK = timezone(timedelta(hours=3))

# Settings the UI may read and change: key -> default
SETTINGS = {
    "profile_theme": True,  # colour the app like the MangaBuff profile skin
    # daemon modules
    "battles_enabled": True,
    "arena_cup_enabled": True,
    "tower_enabled": True,
    "reading_enabled": True,
    "ads_enabled": True,
    "club_enabled": True,
    "auto_upgrade": True,
    "arena_auto_awaken": False,
    "seasonal_watch_enabled": True,
    # telegram
    "tg_enabled": False,
    "tg_token": "",
    "tg_chat_id": "",
    "tg_night_mode": True,
    "tg_silent_all": False,
    # network
    "proxy": "",
    # desktop app
    "app_start_bot_on_launch": True,
    "app_minimize_to_tray": True,
    "app_close_action": "ask",  # ask | tray | quit
    "app_onboarded": False,
    "app_auto_update": True,
}

RESTART_DELAY_SEC = 30

# A switched-off module skips its work, so "run now" on it would only pretend to run
MANUAL_TOGGLES = {"arena": "battles_enabled", "reading": "reading_enabled", "tower": "tower_enabled",
                  "ads": "ads_enabled", "club": "club_enabled", "seasonal": "seasonal_watch_enabled"}


def _level(text: str) -> str:
    # Things the person should do or know is missing read as warnings, whatever the emoji
    if any(w in text for w in ("нет колоды", "не выпадают", "Башня пропущена", "Ты не состоишь в клубе")):
        return "warn"
    if "Stopping" in text or "Остановка" in text:
        return "info"
    if any(m in text for m in ("❌", "🛑", "Traceback")):
        return "error"
    if any(m in text for m in ("⚠️", "🛡️", "⏳")):
        return "warn"
    if any(m in text for m in ("✅", "🎁", "🃏", "🏆", "🎉")):
        return "success"
    return "info"


class BuffyApi:
    def __init__(self):
        self._logs = collections.deque(maxlen=2000)  # plain-language lines for the journal
        self._raw = collections.deque(maxlen=3000)  # everything, for problem reports
        self._log_ctx = {}
        self._log_seq = 0
        self._log_lock = threading.Lock()
        self._bot = None
        self._thread = None
        self._status = "stopped"  # stopped | starting | running | stopping | restarting
        self._started_at = None
        self._want_running = False
        self._window = None
        self._social_lock = threading.Lock()  # one MangaBuff sign-in window at a time
        self._notify = None  # set by the app: Windows notification from the tray icon
        self._on_status_change = None  # set by the app (tray menu refresh)
        self._updater = None  # set by the app
        self._quit = None  # set by the app: full exit
        self._on_hidden = None  # set by the app: tray hint after hiding
        self._manual_bot = None  # bot used for one-off runs while the daemon is stopped
        self._manual = {}  # module -> {"state", "summary", "at"}

    # ------------------------------------------------------------------ logs
    def _log(self, message):
        raw = mask_secrets(str(message))
        ts = time.strftime("%H:%M:%S")
        _file_log().info(raw)
        with self._log_lock:
            self._raw.append(f"[{ts}] {raw}")
            try:
                text = humanize(raw, self._log_ctx)
            except Exception:
                text = None
            if not text:
                return
            self._log_seq += 1
            self._logs.append({
                "seq": self._log_seq,
                "ts": ts,
                "text": text,
                "level": _level(text),
            })

    def get_logs(self, after_seq=0):
        after = int(after_seq or 0)
        with self._log_lock:
            return [entry for entry in self._logs if entry["seq"] > after]

    def clear_logs(self):
        with self._log_lock:
            self._logs.clear()
        return True

    def report_problem(self, description):
        """Save full diagnostics, then open the issue editor with a short preview (even if saving failed)."""
        result = self.export_logs(description)
        with self._log_lock:
            raw = list(self._raw)
        url = build_issue_url(description, raw, self._report_secrets(), status=self._status,
                              report_name=os.path.basename(result['path']) if result['ok'] else '')
        webbrowser.open(url)
        if not result['ok']:
            return {'ok': True, 'warning': 'Отчёт не сохранился, в сообщение попал только сокращённый журнал.'}
        return result

    def _report_secrets(self):
        email, password = DataManager.get_credentials()
        settings = DataManager.load_json(_data_file())
        return [email, password, settings.get('tg_token'), settings.get('tg_chat_id'), settings.get('proxy')]

    def _save_report(self, description):
        with self._log_lock:
            raw = list(self._raw)
        return save_report(os.path.dirname(os.path.abspath(_data_file())), description, raw,
                           self._report_secrets(), self._status, _file_log())

    def _reveal_report(self, path):
        if sys.platform == 'win32':
            subprocess.Popen(['explorer.exe', '/select,', os.path.normpath(path)])

    def export_logs(self, description=''):
        try:
            path = self._save_report(description)
        except OSError:
            return {'ok': False, 'error': 'Не удалось сохранить отчёт. Проверь свободное место и доступ к папке Buffy.'}
        result = {'ok': True, 'path': path}
        try:
            self._reveal_report(path)
        except OSError:
            result['warning'] = 'Не удалось открыть папку. Отчёт сохранён: ' + path
        return result

    # -------------------------------------------------------------- settings
    def get_settings(self):
        data = DataManager.load_json(_data_file())
        out = {key: data.get(key, default) for key, default in SETTINGS.items()}
        out["autostart_windows"] = _autostart_enabled()
        return out

    def set_setting(self, key, value):
        if key == "autostart_windows":
            return _set_autostart(bool(value))
        if key not in SETTINGS:
            return False
        default = SETTINGS[key]
        if key == "app_close_action" and value not in ("ask", "tray", "quit"):
            return False
        if isinstance(default, bool):
            value = bool(value)
        elif value is None:
            value = ""
        else:
            value = str(value).strip()
        DataManager.set_setting(key, value)
        return True

    def get_bootstrap(self):
        email = DataManager.get_credentials()[0]
        settings = self.get_settings()
        return {
            "app": APP_NAME,
            "version": __version__,
            "repo": REPO_URL,
            "email": email or "",
            "has_credentials": DataManager.has_login(),
            "login_method": DataManager.get_setting("login_method"),
            "profile": DataManager.get_setting("account_profile"),
            "onboarded": bool(settings.get("app_onboarded")) and DataManager.has_login(),
            "settings": settings,
            "data_dir": os.path.dirname(_data_file()),
        }

    def finish_onboarding(self):
        DataManager.set_setting("app_onboarded", True)
        return True

    # ----------------------------------------------------------- account
    def check_login(self, email, password):
        """Save credentials and verify them with one real login."""
        email = (email or "").strip()
        password = password or ""  # spaces may be part of a password
        if not email or not password:
            return {"ok": False, "error": "Введи почту и пароль"}
        if self._status in ("running", "starting", "restarting", "stopping") or self._manual_bot is not None:
            return {"ok": False, "error": "Сначала останови бота и дождись конца ручного запуска"}
        try:
            from engine import MangaMinerBot

            bot = MangaMinerBot(
                log_callback=lambda *_: None,
                progress_callback=lambda *_: None,
                stats_callback=lambda **_: None,
            )
            # Try the new login first; a typo must not overwrite a working one
            bot.email, bot.password = email, password
            ok = bot.login_and_steal_keys()
            for line in bot.auth_log_buffer:
                _file_log().info("[login] " + str(line))
            if ok:
                DataManager.switch_account(email)
                DataManager.set_credentials(email, password)
                threading.Thread(target=bot.refresh_profile, daemon=True).start()
                self._log(f"✅ Вход выполнен: {email}")
                return {"ok": True}
            if getattr(bot, "login_rejected", False):
                return {"ok": False, "error": "Неверная почта или пароль. Проверь и попробуй ещё раз."}
            return {"ok": False, "error": "Не удалось связаться с MangaBuff. Проверь интернет или прокси на странице «Сеть»."}
        except Exception as e:
            return {"ok": False, "error": f"Ошибка соединения: {e}"}

    def social_login(self, provider):
        """Sign in through MangaBuff's own page (VK, Discord, Yandex, Google) and keep that window's cookies."""
        name = SOCIAL_PROVIDERS.get(provider)
        if not name:
            return {"ok": False, "error": "Неизвестный способ входа"}
        if self._status in ("running", "starting", "restarting", "stopping") or self._manual_bot is not None:
            return {"ok": False, "error": "Сначала останови бота и дождись конца ручного запуска"}
        signed_in = self._social_window(provider, hidden_sec=0)
        if not signed_in:
            return {"ok": False, "error": "Вход отменён: окно закрыто до входа на сайт"}
        try:
            from engine import MangaMinerBot

            bot = MangaMinerBot(log_callback=lambda *_: None, progress_callback=lambda *_: None,
                                stats_callback=lambda **_: None)
            bot.session.cookies.clear()
            bot.use_cookies(*signed_in)
            alive = bot.adopt_cookie_session()
        except Exception as e:
            return {"ok": False, "error": f"Ошибка соединения: {e}"}
        if alive is None:
            return {"ok": False, "error": "Не удалось связаться с MangaBuff. Проверь интернет и попробуй ещё раз."}
        if not alive:
            return {"ok": False, "error": "Сайт не подтвердил вход. Попробуй ещё раз или войди по почте и паролю."}
        label = f"{name} · id{bot.user_id}" if bot.user_id else name
        DataManager.switch_account(label)
        DataManager.set_credentials(label, None, login_method="social")
        DataManager.set_setting("login_provider", provider)
        threading.Thread(target=bot.refresh_profile, daemon=True).start()
        self._log(f"✅ Вход выполнен: {label}")
        return {"ok": True}

    def _social_window(self, provider, hidden_sec, timeout_sec=None):
        """Open MangaBuff's sign-in through `provider` and wait until the site lets us in.
        The window starts hidden for `hidden_sec`: VK/Google/Yandex remember the user, so a
        repeat sign-in usually finishes by itself; only then is the window shown.
        Returns (cookies, user_agent) or None if closed or timed out."""
        import webview

        with self._social_lock:
            win = webview.create_window(f"Вход в MangaBuff через {SOCIAL_PROVIDERS[provider]}",
                                        f"https://mangabuff.ru/login/redirect/{provider}",
                                        width=560, height=760, hidden=hidden_sec > 0)
            closed = threading.Event()
            win.events.closed += closed.set
            try:
                started = time.time()
                shown = hidden_sec <= 0
                while not closed.is_set() and time.time() - started < (timeout_sec or SOCIAL_LOGIN_TIMEOUT_SEC):
                    try:
                        url = urlsplit(win.get_current_url() or "")
                        # The site sends a signed-in user from /login/callback/... to an ordinary page
                        if url.hostname == "mangabuff.ru" and not url.path.startswith("/login"):
                            cookies = {m.key: m.value for jar in win.get_cookies() for m in jar.values()}
                            return cookies, win.evaluate_js("navigator.userAgent")
                    except Exception:
                        pass  # pywebview gives up after 20 s of a still-loading page; MangaBuff often takes longer
                    if not shown and time.time() - started >= hidden_sec:
                        self._log(f"🔑 MangaBuff просит снова войти через {SOCIAL_PROVIDERS[provider]} — открыл окно входа")
                        win.show()
                        shown = True
                    closed.wait(1)
                return None
            finally:
                if not closed.is_set():
                    win.destroy()

    def _silent_social_relogin(self):
        """Called by the bot when the saved social session died."""
        provider = DataManager.get_setting("login_provider")
        if provider not in SOCIAL_PROVIDERS:
            return None
        # The daemon waits here without a heartbeat: stay well under its 8-minute watchdog
        return self._social_window(provider, hidden_sec=SOCIAL_SILENT_SEC, timeout_sec=5 * 60)

    # ------------------------------------------------------- awakening reroll
    def reroll_state(self):
        """Current reroll goal and, for the Arena page, how likely its minimums are."""
        goal = DataManager.get_setting("awaken_reroll") or None
        return {"goal": goal, "odds": self.reroll_odds(goal or {})["odds"] if goal else None}

    def reroll_odds(self, goal):
        from arena import MANAGEMENT_FILE, reroll_odds

        try:
            goal = {k: float((goal or {}).get(k) or 0) for k in ("min_attack", "min_health", "min_crit")}
        except (TypeError, ValueError):
            return {"odds": None}
        cards = DataManager.load_json(MANAGEMENT_FILE).get("inventory") or []
        return {"odds": reroll_odds(cards, goal), "sample": sum(1 for c in cards if c.get("battle_attack"))}

    def reroll_search(self, query):
        """Cards from the collection by name, awakened ones with their stats."""
        query = (query or "").strip()
        if len(query) < 2:
            return {"ok": False, "error": "Введи хотя бы две буквы названия карты"}
        try:
            from engine import MangaMinerBot
            from urllib.parse import urljoin

            bot = MangaMinerBot(log_callback=lambda *_: None, progress_callback=lambda *_: None,
                                stats_callback=lambda **_: None)
            if not bot.validate_session():
                return {"ok": False, "error": "Сначала запусти бота, чтобы он вошёл в аккаунт"}
            _, root = bot.management_page("/battle/unawaken", ".battle-unawaken")
            route = urljoin("https://mangabuff.ru", root.select_one(".card-filter-form[data-url]")["data-url"])
            found = []
            for awake in (1, -1):
                rows = bot.management_post(route, {"search": query, "is_battle": awake, "per_page": 70, "page": 1})
                found += [_reroll_card_view(c, awake == 1) for c in rows.get("data") or []
                          if not int(c.get("in_trade") or 0)]
            # The site also matches the manga title: cards named like the query come first
            q = query.lower()
            found.sort(key=lambda c: q not in (c["name"] or "").lower())
            return {"ok": True, "cards": found}
        except Exception as e:
            return {"ok": False, "error": f"Не удалось получить коллекцию: {e}"}

    def reroll_start(self, cards, goal):
        """Save the cards and minimums, then start right away (queued for the bot's next pause if it is running)."""
        try:
            mins = {k: max(0, int(float((goal or {}).get(k) or 0))) for k in ("min_attack", "min_health", "min_crit")}
            picked = [{"card_user_id": int(c["id"]), "name": str(c.get("name") or ""), "rank": c.get("rank"),
                       "copy": c.get("copy"), "image": c.get("image"), "manga": c.get("manga"), "status": "waiting",
                       "attempts": 0, "history": []} for c in cards or []]
        except (TypeError, ValueError, KeyError):
            return {"ok": False, "error": "Выбери карты и укажи числа"}
        if not picked:
            return {"ok": False, "error": "Выбери хотя бы одну карту"}
        if not any(mins.values()):
            return {"ok": False, "error": "Укажи хотя бы один минимум"}
        DataManager.set_setting("awaken_reroll", {**mins, "active": True, "cards": picked})
        names = ", ".join(f"«{c['name']}»" for c in picked)
        self._log(f"🎲 Перебор пробуждения: {names} до ATK {mins['min_attack']}, HP {mins['min_health']}, "
                  f"крит {mins['min_crit']}%.")
        started = self.run_module("awaken")
        return {"ok": True, "queued": started.get("queued"), "warning": None if started.get("ok") else started.get("error")}

    def seal_info(self):
        from arena import SEAL_INFO, SEAL_META, SEALS

        out = {}
        for sid, (n, s, r) in SEAL_INFO.items():
            pop, effect, verdict = SEAL_META.get(SEALS.get(sid), (None, None, ""))
            out[sid] = {"name": n, "short": s, "rarity": r, "popularity": pop, "effect": effect, "verdict": verdict}
        return out

    def seal_odds(self, seals):
        """Chance of one of these seals per reforge, from how often they sit on the person's awakened cards."""
        from arena import MANAGEMENT_FILE

        cards = [c for c in DataManager.load_json(MANAGEMENT_FILE).get("inventory") or [] if c.get("battle_seal_id")]
        wanted = {int(s) for s in seals or []}
        return {"odds": sum(int(c["battle_seal_id"]) in wanted for c in cards) / len(cards) if cards else None}

    def seal_state(self):
        return {"goal": DataManager.get_setting("seal_reroll"), "essence": DataManager.get_setting("reroll_essence")}

    def seal_essence(self):
        """Essence balance, read from the site when the Awakening page opens (cached for a minute)."""
        cached = DataManager.get_setting("reroll_essence") or {}
        if time.time() - float(cached.get("at") or 0) < 60:
            return {"ok": True, **cached}
        try:
            from arena import _digits, note_essence
            from engine import MangaMinerBot

            bot = MangaMinerBot(log_callback=lambda *_: None, progress_callback=lambda *_: None,
                                stats_callback=lambda **_: None)
            if not bot.validate_session():
                return {"ok": False, **cached}
            _, root = bot.management_page("/battle/reroll", ".battle-reroll")
            note_essence(_digits(root.select_one("[data-reroll-essence]").get_text()))
            return {"ok": True, **(DataManager.get_setting("reroll_essence") or {})}
        except Exception:
            return {"ok": False, **cached}

    def seal_start(self, cards, seals, max_essence):
        try:
            wanted = sorted({int(s) for s in seals or []})
            limit = max(0, int(float(max_essence or 0)))
            picked = [{"card_user_id": int(c["id"]), "name": str(c.get("name") or ""), "rank": c.get("rank"),
                       "copy": c.get("copy"), "image": c.get("image"), "manga": c.get("manga"),
                       "seal": c.get("seal"), "status": "waiting", "attempts": 0, "history": []}
                      for c in cards or [] if c.get("awake") is not False]
        except (TypeError, ValueError, KeyError):
            return {"ok": False, "error": "Выбери карты и печати"}
        if not picked:
            return {"ok": False, "error": "Выбери хотя бы одну пробуждённую карту"}
        if not wanted:
            return {"ok": False, "error": "Выбери хотя бы одну нужную печать"}
        DataManager.set_setting("seal_reroll", {"active": True, "seals": wanted, "max_essence": limit, "spent": 0,
                                                "cards": picked})
        self._log(f"🔮 Перековка печати: {', '.join(c['name'] for c in picked)}"
                  + (f", не больше {limit} эссенции" if limit else ""))
        started = self.run_module("seals")
        return {"ok": True, "queued": started.get("queued"), "warning": None if started.get("ok") else started.get("error")}

    def seal_resume(self, max_essence=None):
        goal = DataManager.get_setting("seal_reroll")
        if not goal or not goal.get("cards"):
            return {"ok": False, "error": "Нечего продолжать"}
        if max_essence is not None:
            goal = {**goal, "max_essence": max(0, int(float(max_essence or 0)))}
        DataManager.set_setting("seal_reroll", {**goal, "active": True})
        started = self.run_module("seals")
        return {"ok": True, "queued": started.get("queued"), "warning": None if started.get("ok") else started.get("error")}

    def seal_stop(self):
        goal = DataManager.get_setting("seal_reroll")
        if goal:
            DataManager.set_setting("seal_reroll", {**goal, "active": False})
        return {"ok": True}

    def reroll_owned(self):
        """Awakened cards from the bot's own cache: instant, no site request. Strongest first."""
        from arena import MANAGEMENT_FILE, arena_score

        cards = [c for c in DataManager.load_json(MANAGEMENT_FILE).get("inventory") or []
                 if c.get("battle_attack") and not int(c.get("in_trade") or 0)]
        cards.sort(key=arena_score, reverse=True)
        return {"cards": [_reroll_card_view(c, True) for c in cards]}

    def _reroll_reached(self, name, stats):
        self._log(f"🎉 Перебор пробуждения: «{name}» готова — {stats}")
        if self._notify:
            try:
                self._notify(f"«{name}» готова: {stats}", "Перебор пробуждения")
            except Exception:
                pass

    def reroll_goal(self):
        """Light read for the Awakening page while a series runs (polled twice a second)."""
        return {"goal": DataManager.get_setting("awaken_reroll"), "energy": DataManager.get_setting("awaken_energy")}

    def reroll_energy(self):
        """Awakenings left, read from the site when the Awakening page opens (cached for a minute)."""
        cached = DataManager.get_setting("awaken_energy") or {}
        if time.time() - float(cached.get("at") or 0) < 60:
            return {"ok": True, **cached}
        try:
            from engine import MangaMinerBot

            bot = MangaMinerBot(log_callback=lambda *_: None, progress_callback=lambda *_: None,
                                stats_callback=lambda **_: None)
            if not bot.validate_session():
                return {"ok": False, **cached}
            bot._reroll_energy()  # stores the fresh number
            return {"ok": True, **(DataManager.get_setting("awaken_energy") or {})}
        except Exception:
            return {"ok": False, **cached}

    def reroll_resume(self):
        """Go on with the same cards and minimums after «Остановить»."""
        goal = DataManager.get_setting("awaken_reroll")
        if not goal or not goal.get("cards"):
            return {"ok": False, "error": "Нечего продолжать — начни новый перебор"}
        DataManager.set_setting("awaken_reroll", {**goal, "active": True})
        started = self.run_module("awaken")
        return {"ok": True, "queued": started.get("queued"), "warning": None if started.get("ok") else started.get("error")}

    def reroll_set_mins(self, goal):
        """New minimums for the cards already chosen; a card that now passes is kept on the next check."""
        current = DataManager.get_setting("awaken_reroll")
        if not current:
            return {"ok": False, "error": "Перебор не найден"}
        try:
            mins = {k: max(0, int(float((goal or {}).get(k) or 0))) for k in ("min_attack", "min_health", "min_crit")}
        except (TypeError, ValueError):
            return {"ok": False, "error": "Укажи числа"}
        if not any(mins.values()):
            return {"ok": False, "error": "Укажи хотя бы один минимум"}
        # A finished card whose result no longer passes goes back into the queue
        cards = [dict(c, status="waiting") if c.get("status") == "reached" and not _passes(c.get("result"), mins) else c
                 for c in current.get("cards") or []]
        DataManager.set_setting("awaken_reroll", {**current, **mins, "cards": cards})
        self._log(f"🎲 Перебор пробуждения: новые минимумы — атака {mins['min_attack']}, HP {mins['min_health']}, "
                  f"крит {mins['min_crit']}%.")
        left = any(c.get("status") not in ("reached", "missing", "refused") for c in cards)
        if left and not current.get("active"):
            self.reroll_resume()
            return {"ok": True, "resumed": True}
        return {"ok": True, "resumed": bool(current.get("active"))}

    def reroll_keep(self, card_user_id, kind="stats"):
        """«Оставить как есть»: the card keeps its last roll and leaves the queue."""
        key = "seal_reroll" if kind == "seal" else "awaken_reroll"
        goal = DataManager.get_setting(key)
        if not goal:
            return {"ok": False, "error": "Перебор не найден"}
        cards = []
        for c in goal.get("cards") or []:
            if int(c["card_user_id"]) == int(card_user_id) and c.get("history"):
                last = c["history"][-1]
                c = dict(c, status="reached", kept=True,
                         result={"seal": last} if kind == "seal" else last)
            cards.append(c)
        left = any(c.get("status") not in ("reached", "missing", "refused") for c in cards)
        DataManager.set_setting(key, {**goal, "cards": cards, "active": bool(goal.get("active") and left)})
        return {"ok": True}

    def reroll_stop(self):
        goal = DataManager.get_setting("awaken_reroll")
        if goal:
            DataManager.set_setting("awaken_reroll", {**goal, "active": False})
        return {"ok": True}

    def logout(self):
        self.stop()
        DataManager.clear_credentials()
        DataManager.set_setting("app_onboarded", False)
        return True

    # -------------------------------------------------------------- bot
    def start(self):
        if not DataManager.has_login():
            return {"ok": False, "error": "Нет данных для входа"}
        if self._status in ("running", "starting", "restarting"):
            return {"ok": True}
        if self._status == "stopping":
            # The old bot may still be finishing requests; a second one would share the account
            return {"ok": False, "error": "Бот ещё останавливается — подожди пару секунд"}
        if self._manual_bot is not None:
            return {"ok": False, "error": "Дождись, пока закончится ручной запуск модуля"}
        self._want_running = True
        self._spawn_bot()
        return {"ok": True}

    def stop(self):
        self._want_running = False
        if self._manual_bot is not None:
            self._manual_bot.stop()
        bot = self._bot
        if bot is not None and self._status in ("running", "starting", "restarting"):
            self._set_status("stopping")
            bot.stop()
        elif self._status == "restarting":
            self._set_status("stopped")
        return {"ok": True}

    # ------------------------------------------------------- one-off module runs
    def run_module(self, name):
        """Run one module once. While the daemon works it is queued for its next pause."""
        from engine import MangaMinerBot

        if name not in MangaMinerBot.MANUAL_MODULES:
            return {"ok": False, "error": "Этот модуль нельзя запустить вручную"}
        toggle = MANUAL_TOGGLES.get(name)
        if toggle and not DataManager.get_setting(toggle, True):
            return {"ok": False, "error": "Модуль выключен — сначала включи его переключатель"}
        if not DataManager.has_login():
            return {"ok": False, "error": "Сначала войди в аккаунт"}
        if self._status == "stopping":
            return {"ok": False, "error": "Бот останавливается, попробуй через пару секунд"}
        if self._status == "restarting":
            # A one-off bot now would run beside the daemon that is about to restart
            return {"ok": False, "error": "Бот перезапускается — попробуй через минуту"}
        bot = self._bot if self._status in ("running", "starting") else self._manual_bot
        if bot is not None and bot.running:
            bot.request_manual_module(name)
            self._on_manual(name, "queued", "")
            return {"ok": True, "queued": self._status in ("running", "starting")}

        bot = MangaMinerBot(
            log_callback=self._log,
            progress_callback=lambda *_: None,
            stats_callback=lambda **_: None,
        )
        bot.manual_callback = self._on_manual
        bot.social_relogin = self._silent_social_relogin
        bot.on_reroll_reached = self._reroll_reached
        bot.fatal_exit = lambda: setattr(bot, "running", False)  # never kill the app over a one-off run
        bot.running = True
        bot.request_manual_module(name)
        self._manual_bot = bot
        self._on_manual(name, "queued", "")

        def _work():
            try:
                if not ((bot.validate_session() and bot._validate_session_with_server()) or bot.login_and_steal_keys()):
                    for queued in list(bot._manual_queue):
                        self._on_manual(queued, "error", "не удалось войти в аккаунт — проверь почту и пароль")
                    bot._manual_queue.clear()  # keep the error on screen, finally must not reset it to idle
                    return
                bot.auth_log_buffer.clear()
                bot.ensure_site_access()
                bot._drain_manual_queue()
            except Exception as e:
                self._log(f"❌ Ручной запуск упал: {e}")
            finally:
                bot.running = False
                for queued in list(bot._manual_queue):
                    self._on_manual(queued, "idle", "")
                if self._manual_bot is bot:
                    self._manual_bot = None

        threading.Thread(target=_work, name="buffy-manual", daemon=True).start()
        return {"ok": True, "queued": False}

    def _on_manual(self, name, state, summary):
        self._manual[name] = {"state": state, "summary": summary, "at": time.strftime("%H:%M")}

    def _spawn_bot(self):
        from engine import MangaMinerBot

        self._set_status("starting")
        bot = MangaMinerBot(
            log_callback=self._log,
            progress_callback=lambda *_: None,
            stats_callback=lambda **_: None,
        )
        bot.fatal_exit = lambda b=bot: self._on_fatal(b)
        bot.manual_callback = self._on_manual
        bot.social_relogin = self._silent_social_relogin
        bot.on_reroll_reached = self._reroll_reached
        self._bot = bot
        self._started_at = time.time()

        def _run():
            if not self._want_running:
                if self._bot is bot:
                    self._set_status("stopped")
                return
            self._set_status("running")
            try:
                bot.run_daemon()
            except Exception as e:  # never let the UI thread see this
                self._log(f"❌ Демон упал: {e}")
            finally:
                for queued in list(bot._manual_queue):
                    self._on_manual(queued, "idle", "")
                if self._bot is bot and self._status != "restarting":
                    self._started_at = None
                    self._set_status("stopped")
                    self._log("⏹ Бот остановлен.")

        self._thread = threading.Thread(target=_run, name="buffy-daemon", daemon=True)
        self._thread.start()

    def _on_fatal(self, bot):
        """The daemon hung: drop that instance and start a fresh one shortly."""
        if bot is not self._bot:
            return
        self._log(f"♻️ Бот завис — перезапуск через {RESTART_DELAY_SEC} с.")
        self._set_status("restarting")
        bot.running = False

        def _later():
            time.sleep(RESTART_DELAY_SEC)
            if self._want_running and self._status == "restarting":
                self._spawn_bot()

        threading.Thread(target=_later, daemon=True).start()

    def _set_status(self, status):
        self._status = status
        if self._on_status_change:
            try:
                self._on_status_change(status)
            except Exception:
                pass

    def is_running(self):
        # A one-off module run counts too, so the tray offers to stop it instead of a start that is refused
        return self._status in ("running", "starting", "restarting") or self._manual_bot is not None

    # ------------------------------------------------------------ state
    def get_state(self):
        state = DataManager.load_reading_state() or {}
        stats = state.get("stats") or {}
        panel = state.get("panel") or {}
        today = datetime.now(MSK).strftime("%Y-%m-%d")
        if stats.get("date") != today:
            stats = {}
        rank = DataManager.get_setting("arena_rank_tracker") or {}
        with self._log_lock:
            last_seq = self._log_seq
        return {
            "status": self._status,
            "profile": DataManager.get_setting("account_profile"),
            "reroll": DataManager.get_setting("awaken_reroll"),
            "seal": DataManager.get_setting("seal_reroll"),
            "uptime_sec": int(time.time() - self._started_at) if self._started_at else 0,
            "now_msk": datetime.now(MSK).isoformat(),
            "cards": {
                "found": stats.get("cards_found"),
                "max": stats.get("cards_max") or 10,
                "ready": bool(stats.get("card_ready")),
                "cooldown": stats.get("card_cooldown"),
            },
            "chapters": {
                "read": stats.get("chapters_read"),
                "max": stats.get("chapters_max") or 75,
                "manga": stats.get("current_manga") or state.get("current_manga"),
            },
            "panel": {
                "next_action": panel.get("next_action"),
                "next_wake_at": panel.get("next_wake_at"),
                "energy": panel.get("energy"),
                "ads_count": panel.get("ads_count"),
                "tower": panel.get("tower_str"),
                "ore": panel.get("ore"),
                "diamonds": panel.get("diamonds"),
                "diamonds_total": panel.get("diamonds_total"),
                "updated_at": panel.get("updated_at"),
            },
            "halloween": _halloween_state(today),
            "setup": {"bonus_cards_off": DataManager.get_setting("bonus_cards_off_date") == today,
                      "arena_no_deck": bool(DataManager.get_setting("arena_no_deck")),
                      "tower_no_squad": bool(DataManager.get_setting("tower_no_squad"))},
            "rank": {
                "tier": rank.get("current_tier"),
                "division": rank.get("division"),
                "icon": rank.get("icon"),
                "wins": rank.get("wins_in_tier", 0),
                "losses": rank.get("losses_in_tier", 0),
            },
            "last_log_seq": last_seq,
            "current_module": MODULE_TITLES.get(self._log_ctx.get("module") or "", ""),
            "manual": dict(self._manual),
            "manual_busy": self._manual_bot is not None,
        }

    # ------------------------------------------------------------ checks
    def test_telegram(self, token, chat_id):
        token = (token or "").strip()
        chat_id = (chat_id or "").strip()
        if not token or not chat_id:
            return {"ok": False, "error": "Нужны токен бота и Chat ID"}
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id, "text": "💜 Buffy на связи! Уведомления настроены."},
                timeout=10,
            )
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code == 200 and body.get("ok"):
                return {"ok": True}
            return {"ok": False, "error": _tg_error(body.get("description") or f"HTTP {r.status_code}")}
        except Exception as e:
            return {"ok": False, "error": "Нет связи с Telegram. Проверь интернет и попробуй ещё раз."}

    def find_chat_id(self, token):
        """Read the chat id from the last message the user sent to their bot."""
        token = (token or "").strip()
        if not token:
            return {"ok": False, "error": "Сначала вставь токен"}
        try:
            r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10).json()
            if not r.get("ok"):
                return {"ok": False, "error": _tg_error(r.get("description") or "Unauthorized")}
            for upd in reversed(r.get("result") or []):
                chat = (upd.get("message") or {}).get("chat") or {}
                if chat.get("id"):
                    return {"ok": True, "chat_id": str(chat["id"])}
            return {"ok": False, "error": "Напиши своему боту любое сообщение и нажми ещё раз"}
        except Exception as e:
            return {"ok": False, "error": "Нет связи с Telegram. Проверь интернет и попробуй ещё раз."}

    def test_proxy(self, proxy):
        raw = (proxy or "").strip()
        proxies = None
        if raw:
            norm = normalize_proxy_url(raw)
            try:
                parts = urlsplit(norm or "")
                valid = bool(parts.hostname and parts.port and " " not in raw)
            except ValueError:
                valid = False
            if not valid:
                return {"ok": False, "error": "Не понял формат прокси — нужен адрес и порт"}
            proxies = {"http": norm, "https": norm}
        try:
            started = time.time()
            r = requests.get(
                "https://mangabuff.ru/",
                proxies=proxies,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                timeout=12,
            )
            ms = int((time.time() - started) * 1000)
            from http_session import is_anti_bot_page

            block = is_anti_bot_page(r)
            if block == "ddos_guard_geo":
                return {"ok": False, "error": "Сайт не пускает с этого IP (блок по стране) — нужен прокси из другой страны"}
            if block:
                return {"ok": False, "error": "Связь с сайтом есть, но он показал страницу защиты от ботов. "
                                              "Бот обычно проходит её сам — сохрани и запусти; если вход не удастся, попробуй другой прокси"}
            if r.status_code == 200:
                return {"ok": True, "detail": f"MangaBuff доступен · {ms} мс"}
            return {"ok": False, "error": f"Сайт ответил кодом {r.status_code} (возможно, IP заблокирован)"}
        except Exception as e:
            return {"ok": False, "error": f"Нет соединения: {e}"}

    # ------------------------------------------------------------- window
    def open_url(self, url):
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            webbrowser.open(url)
        return True

    def open_data_dir(self):
        path = os.path.normpath(os.path.dirname(os.path.abspath(_data_file())))
        os.makedirs(path, exist_ok=True)
        if sys.platform == "win32":
            # ShellExecute from pywebview's worker thread fails ("not found"); a
            # separate explorer process opens the folder reliably.
            subprocess.Popen(["explorer.exe", path])
        return path

    # --------------------------------------------------------------- updates
    def get_update(self):
        return self._updater.status() if self._updater else {"state": "idle", "current": __version__}

    def check_update(self):
        return self._updater.check_now() if self._updater else False

    def install_update(self):
        if not self._updater:
            return False
        threading.Thread(target=self._updater.install, daemon=True).start()
        return True

    def hide_window(self):
        if self._window:
            self._window.hide()
        if self._on_hidden:
            self._on_hidden()
        return True

    def quit_app(self):
        """Exit completely: stop the bot, remove the tray icon, close the window."""
        if self._quit:
            threading.Thread(target=self._quit, daemon=True).start()
        return True


def _halloween_state(today):
    """Candy balance (from the event page) and today's reading drops, or None outside the event."""
    info = DataManager.get_setting("halloween_event_info")
    if not info:
        return None
    day = DataManager.get_setting("halloween_candy_day") or {}
    return {"balance": info.get("balance"), "reading_today": day.get("n", 0) if day.get("date") == today else 0,
            "temperature": info.get("temperature"),
            "heat_taken": DataManager.get_setting("halloween_heat_claimed_day") == today}


def _tg_error(description):
    """Telegram's English API errors in words a person can act on."""
    text = str(description or "").lower()
    if "unauthorized" in text or "not found" in text and "chat" not in text:
        return "Токен не подошёл. Скопируй его из @BotFather ещё раз целиком."
    if "chat not found" in text:
        return "Чат не найден. Напиши своему боту любое сообщение и нажми «Найти Chat ID»."
    if "blocked" in text:
        return "Бот заблокирован в Telegram. Разблокируй его и попробуй снова."
    if "too many requests" in text:
        return "Telegram просит подождать минуту. Попробуй чуть позже."
    return f"Telegram ответил ошибкой: {description}"


def _reroll_card_view(c, awake):
    image = c.get("image")
    return {"id": int(c["id"]), "name": c.get("card_name") or "", "manga": c.get("manga_name"), "rank": c.get("rank"),
            "copy": c.get("copy_number"), "awake": awake,
            "image": f"https://mangabuff.ru/img/cards/{image}" if image else None,
            "attack": c.get("battle_attack") if awake else None, "health": c.get("battle_health"),
            "crit": c.get("battle_crit_chance"), "seal": c.get("battle_seal_id")}


def _passes(stats, mins):
    return bool(stats) and stats.get("attack", 0) >= mins["min_attack"] and stats.get("health", 0) >= mins["min_health"] \
        and stats.get("crit", 0) >= mins["min_crit"]


SOCIAL_PROVIDERS = {"vkid": "ВКонтакте", "discord": "Discord", "yandex": "Яндекс", "google": "Google"}
SOCIAL_LOGIN_TIMEOUT_SEC = 10 * 60
SOCIAL_SILENT_SEC = 30  # hidden re-login attempt before the window is shown

ISSUE_URL_LIMIT = 7500  # GitHub refuses much longer prefilled URLs

def build_issue_url(description, raw_lines, secrets=(), status="", report_name=''):
    from config import IS_FROZEN

    desc = mask_secrets((description or "").strip() or "(без описания)", secrets)[:350]
    title = desc.splitlines()[0][:80]
    head = (
        f"**Что случилось**\n{desc}\n\n"
        f"**Система**\nBuffy {__version__} · {platform.platform()} · "
        f"{'exe' if IS_FROZEN else 'python'} · бот: {status}\n\n"
        + (f'**Полный журнал:** прикрепи файл `{report_name}` из открывшейся папки.\n\n' if report_name else '')
        + "<details><summary>Сокращённый журнал (полный — в файле отчёта)</summary>\n\n```\n"
    )
    tail = "\n```\n</details>\n"
    lines = [mask_secrets(line, secrets)[:400] for line in raw_lines]
    base = REPO_URL + "/issues/new?"
    while True:
        body = head + "\n".join(lines) + tail
        url = base + urlencode({"title": f"[Buffy] {title}", "body": body}, quote_via=quote)
        if len(url) <= ISSUE_URL_LIMIT or not lines:
            return url
        lines = lines[max(1, len(lines) // 8):]  # drop the oldest lines first


_FILE_LOG = None


def _file_log():
    """Technical log in the data folder (buffy.log, 2 x 2 MB), for reading problems after the fact."""
    global _FILE_LOG
    if _FILE_LOG is None:
        from config import BASE_DIR

        logger = logging.getLogger("buffy.file")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        try:
            handler = logging.handlers.RotatingFileHandler(
                os.path.join(BASE_DIR, "buffy.log"), maxBytes=2_000_000, backupCount=1, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
            logger.addHandler(handler)
        except OSError:
            logger.addHandler(logging.NullHandler())
        _FILE_LOG = logger
    return _FILE_LOG


def _data_file():
    from path import DATA_FILE

    return DATA_FILE


# ---------------------------------------------------------------- autostart
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _launch_command():
    from config import IS_FROZEN

    if IS_FROZEN:
        return f'"{sys.executable}" --minimized'
    main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pythonw if os.path.exists(pythonw) else sys.executable
    return f'"{exe}" "{main_py}" --app --minimized'


def _autostart_enabled():
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def _set_autostart(enabled):
    if sys.platform != "win32":
        return False
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except OSError:
                pass
    return True
