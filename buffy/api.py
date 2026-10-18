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
from utils import normalize_proxy_url

MSK = timezone(timedelta(hours=3))

# Settings the UI may read and change: key -> default
SETTINGS = {
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


def _level(text: str) -> str:
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
        self._on_status_change = None  # set by the app (tray menu refresh)
        self._updater = None  # set by the app
        self._quit = None  # set by the app: full exit
        self._on_hidden = None  # set by the app: tray hint after hiding
        self._manual_bot = None  # bot used for one-off runs while the daemon is stopped
        self._manual = {}  # module -> {"state", "summary", "at"}

    # ------------------------------------------------------------------ logs
    def _log(self, message):
        raw = str(message)
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
        """Open a prefilled GitHub issue: the user's words plus the technical log, secrets masked."""
        with self._log_lock:
            raw = list(self._raw)
        email, password = DataManager.get_credentials()
        settings = DataManager.load_json(_data_file())
        secrets = [email, password, settings.get("tg_token"), settings.get("tg_chat_id"), settings.get("proxy")]
        url = build_issue_url(description, raw, secrets, status=self._status)
        webbrowser.open(url)
        return {"ok": True}

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
        email, password = DataManager.get_credentials()
        settings = self.get_settings()
        return {
            "app": APP_NAME,
            "version": __version__,
            "repo": REPO_URL,
            "email": email or "",
            "has_credentials": bool(email and password),
            "onboarded": bool(settings.get("app_onboarded")) and bool(email and password),
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
        if self._status in ("running", "starting"):
            return {"ok": False, "error": "Сначала останови бота"}
        DataManager.set_credentials(email, password)
        try:
            from engine import MangaMinerBot

            bot = MangaMinerBot(
                log_callback=lambda *_: None,
                progress_callback=lambda *_: None,
                stats_callback=lambda **_: None,
            )
            ok = bot.login_and_steal_keys()
            for line in bot.auth_log_buffer:
                _file_log().info("[login] " + str(line))
            if ok:
                self._log(f"✅ Вход выполнен: {email}")
                return {"ok": True}
            if getattr(bot, "login_rejected", False):
                return {"ok": False, "error": "Неверная почта или пароль. Проверь и попробуй ещё раз."}
            return {"ok": False, "error": "Не удалось связаться с MangaBuff. Проверь интернет или прокси на странице «Сеть»."}
        except Exception as e:
            return {"ok": False, "error": f"Ошибка соединения: {e}"}

    def logout(self):
        self.stop()
        DataManager.clear_credentials()
        DataManager.set_setting("app_onboarded", False)
        return True

    # -------------------------------------------------------------- bot
    def start(self):
        email, password = DataManager.get_credentials()
        if not (email and password):
            return {"ok": False, "error": "Нет данных для входа"}
        if self._status in ("running", "starting", "restarting"):
            return {"ok": True}
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
        email, password = DataManager.get_credentials()
        if not (email and password):
            return {"ok": False, "error": "Сначала войди в аккаунт"}
        if self._status == "stopping":
            return {"ok": False, "error": "Бот останавливается, попробуй через пару секунд"}
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
        self._bot = bot
        self._started_at = time.time()

        def _run():
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
        return self._status in ("running", "starting", "restarting")

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
            "setup": {"arena_no_deck": bool(DataManager.get_setting("arena_no_deck")),
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
            return {"ok": False, "error": body.get("description") or f"HTTP {r.status_code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def find_chat_id(self, token):
        """Read the chat id from the last message the user sent to their bot."""
        token = (token or "").strip()
        if not token:
            return {"ok": False, "error": "Сначала вставь токен"}
        try:
            r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10).json()
            if not r.get("ok"):
                return {"ok": False, "error": r.get("description") or "Токен не подошёл"}
            for upd in reversed(r.get("result") or []):
                chat = (upd.get("message") or {}).get("chat") or {}
                if chat.get("id"):
                    return {"ok": True, "chat_id": str(chat["id"])}
            return {"ok": False, "error": "Напиши своему боту любое сообщение и нажми ещё раз"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

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


ISSUE_URL_LIMIT = 7500  # GitHub refuses much longer prefilled URLs

_SECRET_PATTERNS = [
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"), "<tg-token>"),
    (re.compile(r"(\w+://)[^\s/:@]+:[^\s/@]+@"), r"\1<user>:<pass>@"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    (re.compile(r"(?i)(token|csrf|cookie|session|password|passwd|secret)([\"']?\s*[:=]\s*[\"']?)[^\s,;\"'&]+"),
     r"\1\2<hidden>"),
    (re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}"), "<hidden>"),
]


def mask_secrets(text, secrets=()):
    for value in secrets:
        value = str(value or "").strip()
        if len(value) >= 4:
            text = text.replace(value, "<hidden>")
    for rx, repl in _SECRET_PATTERNS:
        text = rx.sub(repl, text)
    return text


def build_issue_url(description, raw_lines, secrets=(), status=""):
    from config import IS_FROZEN

    desc = mask_secrets((description or "").strip() or "(без описания)", secrets)
    title = desc.splitlines()[0][:80]
    head = (
        f"**Что случилось**\n{desc}\n\n"
        f"**Система**\nBuffy {__version__} · {platform.platform()} · "
        f"{'exe' if IS_FROZEN else 'python'} · бот: {status}\n\n"
        "<details><summary>Журнал (заполнен автоматически)</summary>\n\n```\n"
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
