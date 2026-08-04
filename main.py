import sys
import time
import random
import os
import json
import threading
import re
import datetime
from datetime import timedelta, timezone
import requests
import customtkinter as ctk
from tkinter import messagebox
from bs4 import BeautifulSoup
from tg_notifier import send_message

# ==========================================
# 🛠️ FIX 1: PATH HANDLING FOR .EXE
# ==========================================
def get_base_path():
    """ Returns the folder where the .exe is running, or the script folder. """
    if getattr(sys, 'frozen', False):
        # Running as compiled .exe
        return os.path.dirname(sys.executable)
    else:
        # Running as .py script
        return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_path()
DATA_FILE = os.path.join(BASE_DIR, "user_data.json")
SESSION_FILE = os.path.join(BASE_DIR, "session_cache.json")

# ==========================================
# CONFIGURATION & SELECTORS
# ==========================================
CONFIG = {
    "urls": {
        "login": "https://mangabuff.ru/login",
        "game": "https://mangabuff.ru/mine"
    },
    "selectors": {
        "login_input": 'input[name="email"]',
        "pass_input": 'input[name="password"]',
        "login_btn": ".login-button",
        "mine_element": ".main-mine__header",
        "balance_class": "mine-shop__ore-count",
        "energy_class": "main-mine__game-hits-left"
    }
}

# High-value card tiers we never consume — preserved for future trading
TARGET_RARITIES = ["legendary", "mythic"]

# ==========================================
# TRANSLATIONS
# ==========================================
LANGUAGES = {
    "English": {
        "app_title": "MangaBuff Miner",
        "settings": "SETTINGS",
        "headless": "Headless Login",
        "controls": "CONTROLS",
        "btn_start": "🚀 START TURBO MINING",
        "btn_status": "📊 CHECK STATUS",
        "btn_stop": "🛑 STOP WORK",
        "btn_logout": "Sign Out / Change",
        "lbl_guest": "No Account",
        "timer_label": "NEXT RESET (MSK):",
        "log_title": "ACTIVITY LOG",
        "card_energy": "⚡ ENERGY",
        "card_balance": "💎 BALANCE",
        "login_title": "Account Login",
        "login_save": "Save & Login",
        "error_env": "Please log in first!",
        "error_fill": "Please fill all fields!",
        "log_login_start": "🚪 Logging in via Browser...",
        "log_process_login": "⏳ Processing Login...",
        "log_login_ok": "✅ Login successful!",
        "log_login_fail_page": "❌ Failed to reach Mining Page.",
        "log_login_fail_csrf": "❌ ERROR: CSRF Token missing.",
        "log_login_crash": "❌ Login Crash: {e}",
        "log_steal_keys": "🕵️ Stealing Session Keys...",
        "log_god_mode": "👻 Browser Closed. GOD MODE ACTIVE.",
        "log_mining_start": "🚀 STARTING API MINING...",
        "log_energy_empty": "🛑 Energy empty.",
        "log_mining_finish": "🏁 Session finished.",
        "log_logout": "ℹ️ Signed out.",
        "log_init": "🚀 Initializing...",
        "log_stopping": "🛑 Stopping...",
        "log_stat_energy": "⚡ Energy: {val}",
        "log_stat_balance": "💎 Balance: {val}",
        "log_session_load": "📂 Loading saved session...",
        "log_session_valid": "✅ Session valid!",
        "log_session_expired": "⚠️ Session expired. Re-logging...",
        "log_session_restart": "❌ Session likely expired. Restarting...",
        "log_upgrade_ok": "⬆️ UPGRADE SUCCESS! New Level.",
        "log_upgrade_fail": "⚠️ Upgrade failed or too expensive.",
        "log_upgrade_check": "🔍 Checking for upgrades...",
        "log_buy_upgrade": "💰 Buying upgrade for {price}...",
        "log_no_upgrade": "ℹ️ No upgrade found (Max Level?)",
        "log_upgrade_cost": "⬆️ Next Upgrade: {cost}",
        "log_upgrade_status_max": "⬆️ Upgrade: Max / None",
        "log_source_check": "🔍 Reading page source...",
        "log_done": "👋 Done.",
        "log_timeout": "⚠️ Timeout",
        "log_error_generic": "💥 Error: {e}",
        "log_thread_crash": "💥 Thread Crash: {e}"
    },
    "Русский": {
        "app_title": "MangaBuff Miner",
        "settings": "НАСТРОЙКИ",
        "headless": "Скрытый вход",
        "controls": "УПРАВЛЕНИЕ",
        "btn_start": "🚀 ТУРБО МАЙНИНГ",
        "btn_status": "📊 ПРОВЕРИТЬ СТАТУС",
        "btn_stop": "🛑 ОСТАНОВИТЬ",
        "btn_logout": "Выйти / Сменить",
        "lbl_guest": "Нет аккаунта",
        "timer_label": "СБРОС (МСК):",
        "log_title": "ЛОГ ДЕЙСТВИЙ",
        "card_energy": "⚡ ЭНЕРГИЯ",
        "card_balance": "💎 БАЛАНС",
        "login_title": "Вход в аккаунт",
        "login_save": "Сохранить и Войти",
        "error_env": "Сначала войдите в аккаунт!",
        "error_fill": "Заполните все поля!",
        "log_login_start": "🚪 Вход через браузер...",
        "log_process_login": "⏳ Обработка входа...",
        "log_login_ok": "✅ Успешный вход!",
        "log_login_fail_page": "❌ Не удалось открыть страницу.",
        "log_login_fail_csrf": "❌ ОШИБКА: Нет CSRF токена.",
        "log_login_crash": "❌ Ошибка входа: {e}",
        "log_steal_keys": "🕵️ Кража ключей сессии...",
        "log_god_mode": "👻 Браузер закрыт. GOD MODE АКТИВЕН.",
        "log_mining_start": "🚀 ЗАПУСК API ЦИКЛА...",
        "log_energy_empty": "🛑 Энергия закончилась.",
        "log_mining_finish": "🏁 Сессия завершена.",
        "log_logout": "ℹ️ Выход выполнен.",
        "log_init": "🚀 Инициализация...",
        "log_stopping": "🛑 Остановка...",
        "log_stat_energy": "⚡ Энергия: {val}",
        "log_stat_balance": "💎 Баланс: {val}",
        "log_session_load": "📂 Загрузка сессии...",
        "log_session_valid": "✅ Сессия активна!",
        "log_session_expired": "⚠️ Сессия истекла. Перезаходим...",
        "log_session_restart": "❌ Сессия истекла. Перезапуск...",
        "log_upgrade_ok": "⬆️ УЛУЧШЕНИЕ КУПЛЕНО! Новый уровень.",
        "log_upgrade_fail": "⚠️ Не хватает денег на улучшение.",
        "log_upgrade_check": "🔍 Проверка улучшений...",
        "log_buy_upgrade": "💰 Покупка улучшения за {price}...",
        "log_no_upgrade": "ℹ️ Улучшений нет (Макс. уровень?)",
        "log_upgrade_cost": "⬆️ След. уровень: {cost}",
        "log_upgrade_status_max": "⬆️ Улучшение: Макс / Нет",
        "log_source_check": "🔍 Чтение исходного кода...",
        "log_done": "👋 Готово.",
        "log_timeout": "⚠️ Тайм-аут соединения",
        "log_error_generic": "💥 Ошибка: {e}",
        "log_thread_crash": "💥 Крах потока: {e}"
    },
    "Українська": {
        "app_title": "MangaBuff Miner",
        "settings": "НАЛАШТУВАННЯ",
        "headless": "Прихований вхід",
        "controls": "КЕРУВАННЯ",
        "btn_start": "🚀 ТУРБО МАЙНІНГ",
        "btn_status": "📊 ПЕРЕВІРИТИ СТАТУС",
        "btn_stop": "🛑 ЗУПИНИТИ",
        "btn_logout": "Вийти / Змінити",
        "lbl_guest": "Немає акаунту",
        "timer_label": "СКИДАННЯ (МСК):",
        "log_title": "ЛОГ ДІЙ",
        "card_energy": "⚡ ЕНЕРГІЯ",
        "card_balance": "💎 БАЛАНС",
        "login_title": "Вхід в акаунт",
        "login_save": "Зберегти та Увійти",
        "error_env": "Спочатку увійдіть в акаунт!",
        "error_fill": "Заповніть усі поля!",
        "log_login_start": "🚪 Вхід через браузер...",
        "log_process_login": "⏳ Обробка входу...",
        "log_login_ok": "✅ Успішний вхід!",
        "log_login_fail_page": "❌ Не вдалося відкрити сторінку.",
        "log_login_fail_csrf": "❌ ПОМИЛКА: Немає CSRF токена.",
        "log_login_crash": "❌ Помилка входу: {e}",
        "log_steal_keys": "🕵️ Крадіжка ключів сесії...",
        "log_god_mode": "👻 Браузер закрито. GOD MODE АКТИВНИЙ.",
        "log_mining_start": "🚀 ЗАПУСК API ЦИКЛУ...",
        "log_energy_empty": "🛑 Енергія закінчилася.",
        "log_mining_finish": "🏁 Сесію завершено.",
        "log_logout": "ℹ️ Вихід виконано.",
        "log_init": "🚀 Ініціалізація...",
        "log_stopping": "🛑 Зупинка...",
        "log_stat_energy": "⚡ Енергія: {val}",
        "log_stat_balance": "💎 Баланс: {val}",
        "log_session_load": "📂 Завантаження сесії...",
        "log_session_valid": "✅ Сесія активна!",
        "log_session_expired": "⚠️ Сесія вичерпана. Перезаходимо...",
        "log_session_restart": "❌ Сесія вичерпана. Перезапуск...",
        "log_upgrade_ok": "⬆️ ПОКРАЩЕННЯ КУПЛЕНО! Новий рівень.",
        "log_upgrade_fail": "⚠️ Не вистачає грошей на покращення.",
        "log_upgrade_check": "🔍 Перевірка покращень...",
        "log_buy_upgrade": "💰 Купівля покращення за {price}...",
        "log_no_upgrade": "ℹ️ Покращень немає (Макс?)",
        "log_upgrade_cost": "⬆️ Наст. рівень: {cost}",
        "log_upgrade_status_max": "⬆️ Покращення: Макс / Немає",
        "log_source_check": "🔍 Читання вихідного коду...",
        "log_done": "👋 Готово.",
        "log_timeout": "⚠️ Тайм-аут з'єднання",
        "log_error_generic": "💥 Помилка: {e}",
        "log_thread_crash": "💥 Крах потоку: {e}"
    }
}

CURRENT_LANG = "English"

def tr(key, **kwargs):
    lang_dict = LANGUAGES.get(CURRENT_LANG, LANGUAGES["English"])
    text = lang_dict.get(key, LANGUAGES["English"].get(key, key))
    if kwargs: return text.format(**kwargs)
    return text

# ==========================================
# DATA MANAGEMENT
# ==========================================
class DataManager:
    @staticmethod
    def load_json(filename):
        if not os.path.exists(filename): return {}
        try:
            with open(filename, "r", encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}

    @staticmethod
    def save_json(filename, data):
        try:
            current = DataManager.load_json(filename)
            current.update(data)
            with open(filename, "w", encoding='utf-8') as f:
                json.dump(current, f, indent=4, ensure_ascii=False)
        except:
            pass

    @staticmethod
    def get_credentials():
        data = DataManager.load_json(DATA_FILE)
        return data.get("email"), data.get("password")

    @staticmethod
    def set_credentials(email, password):
        DataManager.save_json(DATA_FILE, {"email": email, "password": password})

    @staticmethod
    def clear_credentials():
        DataManager.save_json(DATA_FILE, {"email": None, "password": None})
        if os.path.exists(SESSION_FILE): os.remove(SESSION_FILE)

    @staticmethod
    def save_session(cookies_dict, csrf_token, user_agent):
        data = {
            "cookies": cookies_dict,
            "csrf": csrf_token,
            "agent": user_agent,
            "timestamp": time.time()
        }
        DataManager.save_json(SESSION_FILE, data)

    @staticmethod
    def load_session():
        return DataManager.load_json(SESSION_FILE)


# ==========================================
# UTILS
# ==========================================
def parse_smart_number(text):
    if not text: return 0
    text = str(text).lower().strip().replace(",", ".")
    multiplier = 1
    if 'k' in text:
        multiplier = 1000
    elif 'm' in text:
        multiplier = 1000000

    clean_num = re.sub(r'[^\d\.]', '', text)
    try:
        if not clean_num: return 0
        return int(float(clean_num) * multiplier)
    except:
        return 0


def safe_cookies_to_dict(session):
    """Build a plain {name: value} dict from a requests cookie jar.

    Iterates the jar (an iterator of Cookie objects) and overwrites on
    duplicate names, so DDoS-Guard's repeated __ddg8_ / __ddg1_ cookies
    collapse to a single entry (last value wins). This avoids
    requests.utils.dict_from_cookiejar(), which raises
    MultipleCookiesWithSameNameError on duplicate names, and avoids any
    create_cookie()/jar.set() reconstruction that would corrupt the
    essential auth cookies (mangabuff_session, XSRF-TOKEN).
    """
    out = {}
    for c in session.cookies:
        out[c.name] = c.value  # last write wins
    return out


# ==========================================
# BOT ENGINE
# ==========================================
class MangaMinerBot:
    def __init__(self, log_callback, progress_callback, stats_callback, headless=True):
        self.log = log_callback
        self.update_progress = progress_callback
        self.update_stats = stats_callback
        self.headless = headless
        self.running = False
        self.API_URL = "https://mangabuff.ru/mine/hit"
        self.session = requests.Session()
        # Default modern browser UA (overridden after login if a saved one exists)
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        })
        self.email, self.password = DataManager.get_credentials()
        self.csrf_token = None
        self.user_agent = None
        self.current_balance = 0

    def validate_session(self):
        data = DataManager.load_session()
        if not data or "cookies" not in data or "csrf" not in data:
            return False

        # Load the saved cookie dict directly. safe_cookies_to_dict() already
        # collapsed any duplicate names when it was saved, so update() is safe.
        self.session.cookies.update(data["cookies"])
        self.csrf_token = data["csrf"]
        self.user_agent = data["agent"]
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "X-CSRF-TOKEN": self.csrf_token,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://mangabuff.ru/mine",
            "Origin": "https://mangabuff.ru",
            "Content-Type": "application/json"
        })
        self.log(tr("log_session_valid"))
        return True

    def login_and_steal_keys(self):
        self.log("Logging in via API...")
        self.session.cookies.clear()
        try:
            # Standard desktop User-Agent (no browser-automation fingerprints)
            self.user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            self.session.headers.update({"User-Agent": self.user_agent})

            # 1. Initial GET to retrieve the login page (and session cookie)
            res = self.session.get(CONFIG["urls"]["login"], timeout=10)
            soup = BeautifulSoup(res.text, "html.parser")

            # Extract the CSRF token from the meta tag
            meta = soup.find("meta", attrs={"name": "csrf-token"})
            if not meta or not meta.get("content"):
                self.log(tr("log_login_fail_csrf"))
                return False
            self.csrf_token = meta.get("content")

            # 2. POST login (standard form submit; CSRF in body as _token)
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
                print(f"DEBUG: Login returned non-JSON (status {post_res.status_code})")
                print("DEBUG HTML:", post_res.text[:1000])
                self.log(tr("log_login_fail_page"))
                return False

            if response_data.get("status") == True:
                # 4. Configure API headers for all subsequent requests
                self.session.headers.update({
                    "User-Agent": self.user_agent,
                    "X-CSRF-TOKEN": self.csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://mangabuff.ru/mine",
                    "Origin": "https://mangabuff.ru",
                    "Content-Type": "application/json"
                })

                # Save cookies as a plain {name: value} dict. safe_cookies_to_dict()
                # iterates the jar and overwrites duplicate names (e.g. __ddg8_),
                # so the essential auth cookies (mangabuff_session, XSRF-TOKEN)
                # are preserved and the JSON save never raises.
                cookies_dict = safe_cookies_to_dict(self.session)
                DataManager.save_session(cookies_dict, self.csrf_token, self.user_agent)
                self.log(tr("log_login_ok"))
                self.log(tr("log_god_mode"))
                return True
            else:
                print(f"DEBUG: Login failed. Response: {response_data}")
                self.log(tr("log_login_fail_page"))
                return False

        except Exception as e:
            self.log(tr("log_login_crash", e=e))
            return False

    def claim_daily_reward(self):
        """Claim the daily login reward.

        The claim URL is NOT hardcoded. We parse the /balance page to find
        the currently-active "Claim" button for the daily calendar and extract
        its real href/action (or build it from the active day's data-day).
        Only then do we POST — with an EMPTY payload and the exact
        browser headers. If no active claim button exists, we skip gracefully.
        """
        self.log("🎁 Checking for daily reward...")
        try:
            # 1. Parse the balance page for the active claim URL (dynamic).
            claim_url = self._find_active_claim_url()
            if not claim_url:
                # No active claim button today -> nothing to do.
                self.log("ℹ️ No available daily reward to claim.")
                return

            # 2. POST to the exact endpoint the page exposes, with the
            #    exact headers and an EMPTY payload (day id is in the URL).
            self.log(f"🎁 Claiming reward: {claim_url}")
            claim_res = self.session.post(
                claim_url,
                data="",  # empty body; the day id lives in the URL
                headers={
                    "Accept": "*/*",
                    "X-CSRF-TOKEN": self.csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://mangabuff.ru/balance"
                },
                timeout=10
            )

            # --- Clean, concise diagnostic (status + raw body only) ---
            print(f"[DAILY] POST {claim_url} -> HTTP {claim_res.status_code}")
            print(f"[DAILY] BODY: {claim_res.text[:1000]}")

            if claim_res.status_code == 422:
                # 422 here means "calendar not available / already claimed".
                self.log("ℹ️ Daily reward already claimed or not available yet.")
            elif claim_res.status_code == 200:
                try:
                    data = claim_res.json()
                    if data.get("status") is True or data.get("success") is True:
                        self.log("✅ Daily reward claimed successfully!")
                    else:
                        self.log("ℹ️ Daily reward already claimed today.")
                except ValueError:
                    self.log("✅ Daily reward claimed successfully!")
            else:
                self.log(f"⚠️ Claim returned status {claim_res.status_code}")

        except Exception as e:
            print(f"[DAILY] Exception: {e}")
            self.log(f"⚠️ Daily reward error: {e}")

    def analyze_battle_page(self):
        """Fetch /battle and dump the HTML needed to reverse-engineer the
        matchmaking and daily-quest endpoints (debug/analysis only)."""
        print("=== BATTLE PAGE ANALYSIS ===")
        try:
            res = self.session.get("https://mangabuff.ru/battle", timeout=10)
            if res.status_code != 200:
                print(f"Battle page error: status {res.status_code}")
                return
            soup = BeautifulSoup(res.text, "html.parser")

            # --- Task 1: Matchmaking ("Найти бой") button ---
            print("\n--- TASK 1: MATCHMAKING BUTTON ---")
            match_btn = None
            for el in soup.find_all(string=lambda t: t and "Найти" in t):
                match_btn = el.parent
                if match_btn:
                    break
            if match_btn:
                print("Found 'Найти...' button element:")
                print(str(match_btn))
                # Walk up to the nearest <form>/<a>/<button> ancestor
                node = match_btn
                for _ in range(5):
                    node = node.parent
                    if node and getattr(node, "name", None) != "[document]":
                        if node.name in ("form", "a", "button"):
                            print(f"Ancestor <{node.name}> outerHTML:")
                            print(str(node))
            else:
                print("Could not find 'Найти бой' button by text.")

            # --- Task 2: Daily Quests (Дейлики) section ---
            print("\n--- TASK 2: DAILY QUESTS ---")
            quest_section = None
            for el in soup.find_all(string=lambda t: t and (
                "Дейлик" in t or "задан" in t.lower()
                or "квест" in t.lower() or "Ежеднев" in t
            )):
                quest_section = el.parent
                if quest_section:
                    break
            if quest_section:
                node = quest_section
                for _ in range(4):
                    node = node.parent
                    if node and getattr(node, "name", None) != "[document]":
                        if node.get("class"):
                            print(f"Quest container candidate "
                                  f"(<{node.name} class={node.get('class')}>):")
                            print(str(node)[:2500])
                            break
            else:
                print("Could not find Daily Quests section by text.")

            # Dump any element whose class hints at quests/dailies
            print("\n--- Elements with quest/daily/deylik in class ---")
            for tag in soup.find_all(class_=re.compile(r"quest|daily|deylik", re.IGNORECASE)):
                print(str(tag)[:800])
                print("---")

        except Exception as e:
            print(f"Battle analysis exception: {e}")

    def get_claimable_quests(self):
        """Return a list of daily-quest IDs that are completed but not yet claimed."""
        try:
            res = self.session.get("https://mangabuff.ru/battle", timeout=10)
            if res.status_code != 200:
                print(f"Battle page error: status {res.status_code}")
                return []
            soup = BeautifulSoup(res.text, "html.parser")

            claimable = []
            for article in soup.find_all("article"):
                qid = article.get("data-daily-quest-id")
                if not qid:
                    continue
                classes = article.get("class", [])
                completed = "battle-home__quest--completed" in classes
                claimed = "battle-home__quest--claimed" in classes
                if completed and not claimed:
                    claimable.append(qid)
            return claimable
        except Exception as e:
            print(f"get_claimable_quests exception: {e}")
            return []

    def check_and_claim_quests(self):
        """Parse all daily-quest articles ONCE; claim any COMPLETED-but-unclaimed
        quest. Strictly verifies completion via the article's CSS classes
        (battle-home__quest--completed and NOT battle-home__quest--claimed)
        before sending a claim request, so incomplete quests are never claimed.
        Single pass — does not retry the same quest every battle.
        """
        print("=== CHECK AND CLAIM QUESTS (single pass) ===")
        try:
            res = self.session.get("https://mangabuff.ru/battle", timeout=10)
            if res.status_code != 200:
                print(f"Battle page error: status {res.status_code}")
                return
            soup = BeautifulSoup(res.text, "html.parser")

            claimed_any = False
            for article in soup.find_all("article"):
                qid = article.get("data-daily-quest-id")
                if not qid:
                    continue
                classes = article.get("class", [])
                completed = "battle-home__quest--completed" in classes
                claimed = "battle-home__quest--claimed" in classes

                # STRICT check: only claim quests that are explicitly marked
                # completed AND not yet claimed. Anything else (in progress,
                # not started, already claimed) is skipped.
                if completed and not claimed:
                    print(f"Quest {qid}: completed & unclaimed -> claiming...")
                    data = self.claim_quest(qid)
                    if data is not None:
                        claimed_any = True
                    # 422 / failure already logged in claim_quest; move to next
                else:
                    status = "claimed" if claimed else ("in progress" if not completed else "unknown")
                    print(f"Quest {qid}: {status} -> skip")
            if not claimed_any:
                print("No claimable (completed) quests found.")
        except Exception as e:
            print(f"check_and_claim_quests exception: {e}")

    def claim_quest(self, quest_id):
        """POST to the daily-quest claim endpoint; returns parsed JSON or None.
        Sends a JSON body (matching the session's application/json Content-Type)
        with the CSRF token and several candidate id field names so the
        Laravel validator receives the id regardless of which key it expects."""
        print(f"claim_quest({quest_id}): POST https://mangabuff.ru/battle/daily-quests/claim")
        if not quest_id:
            print("claim_quest: empty quest_id, skipping.")
            return None
        try:
            res = self.session.post(
                "https://mangabuff.ru/battle/daily-quests/claim",
                json={
                    "_token": self.csrf_token,
                    "id": quest_id,
                    "user_daily_quest_id": quest_id,
                    "quest_id": quest_id,
                    "daily_quest_id": quest_id
                },
                headers={
                    "X-CSRF-TOKEN": self.csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://mangabuff.ru/battle"
                },
                timeout=10
            )
            print(f"Quest {quest_id} claim response status: {res.status_code}")
            if res.status_code == 422:
                print(f"Quest {quest_id} claim REJECTED (422) — invalid/missing id or already handled.")
                return None
            try:
                data = res.json()
            except ValueError:
                print("Quest claim response was not JSON:")
                print(res.text[:1000])
                return None
            print(f"Quest {quest_id} claim JSON:")
            print(json.dumps(data, indent=2, ensure_ascii=False))
            return data
        except Exception as e:
            print(f"claim_quest({quest_id}) exception: {e}")
            return None

    def start_battle(self):
        """POST to the matchmaking endpoint; return the redirect_url from JSON.
        Returns the string "NO_ENERGY" when battle stamina is depleted
        (400/422 or a success:false JSON). Returns None on transient/error
        (network error, non-JSON, or a 419 that could not be re-logged —
        the caller retries). This is the original fighting contract: the bot
        keeps calling start_battle() every loop iteration and only stops the
        farm on a real NO_ENERGY or the 3-consecutive-+0-essence cap."""
        try:
            res = self.session.post(
                "https://mangabuff.ru/battle/fight/start",
                headers={
                    "X-CSRF-TOKEN": self.csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://mangabuff.ru/battle"
                },
                timeout=10
            )

            # Session expired (419) — try to re-authenticate, then retry once.
            if res.status_code == 419:
                if self._handle_session_expired():
                    return self.start_battle()
                return None

            # 400/422 are treated as TRANSIENT (rate-limit, brief
            # server lock, race with another tab). Do NOT treat as
            # out-of-energy — the daily-limit stop is decided solely by
            # the 3-consecutive-+0-essence rule in run_farm_loop.
            if res.status_code in (400, 422):
                return None
            try:
                data = res.json()
            except ValueError:
                return None
            if data.get("success") is False or data.get("status") is False:
                # Only a clear "no energy" signal stops us here.
                if data.get("energy_left") == 0 or data.get("energy") == 0:
                    return "NO_ENERGY"
                return None
            redirect_url = data.get("redirect_url")
            if not redirect_url:
                return None
            # Stash the POST response — it is the BATTLE-ENDING request and
            # may already carry the result JSON (essence_earned, etc.).
            # check_battle_result() inspects this FIRST (more reliable than
            # scraping the redirected HTML page, which can be the nav shell).
            self._last_fight_response = res
            return redirect_url
        except Exception:
            return None

    def check_battle_result(self, match_url):
        """Return a tuple (earned_essence, is_win) parsed from the battle
        result page, or None when the result cannot be reliably determined.

        ARCHITECTURE (strict): DROP BeautifulSoup for battles. The battle
        results are NOT in standard HTML tags — they are embedded as a raw
        JSON string directly inside the page's source code (injected into a
        JS variable). We make a GET to the redirect_url and run regexes on
        response.text to extract the exact match data for the current fight:
          - earned essence:  r'"essence"\s*:\s*(\d+)'
          - win status:      r'"is_win"\s*:\s*(true|false)'

        Returning None (not 0) prevents a false "0 gained" from tripping the
        daily-limit stop. The caller (run_farm_loop) breaks the loop when
        earned_essence == 0 (server's daily cap reached).
        """
        try:
            if not match_url:
                print("[BATTLE] No redirect_url provided — cannot read result.")
                return None

            # GET the battle result page (the redirect_url from matchmaking).
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
                print(f"[BATTLE] GET {match_url} -> HTTP {res.status_code}")
                return None

            # DEBUG: dump a window around the "essence" key so we can confirm
            # the exact markup if the regex ever misses.
            print("=== BATTLE PAGE (essence context) ===")
            m_dbg = re.search(r".{0,120}essence.{0,120}", res.text, re.IGNORECASE | re.DOTALL)
            if m_dbg:
                print(m_dbg.group(0))
            else:
                print("No 'essence' substring found in page text.")
                print("First 400 chars of page text:")
                print(res.text[:400])
            print("=============================================")

            # Extract the earned essence for THIS fight via regex.
            m = re.search(r'"essence"\s*:\s*(\d+)', res.text)
            if not m:
                print("[BATTLE] Could not find 'essence' in page source.")
                return None

            earned_essence = int(m.group(1))

            # Extract win/loss status (optional, for nicer logs).
            is_win = None
            mw = re.search(r'"is_win"\s*:\s*(true|false)', res.text)
            if mw:
                is_win = (mw.group(1).lower() == "true")

            print(f"[BATTLE] Parsed essence = {earned_essence} | is_win = {is_win}")
            return (earned_essence, is_win)
        except Exception as e:
            print(f"check_battle_result exception: {e}")
            return None

    def check_card_rarity(self, data):
        """Detect high-value card drops and preserve them for trading."""
        card = data.get("card") if isinstance(data, dict) else None
        if not isinstance(card, dict):
            return
        rarity = card.get("potential_rarity")
        if rarity and str(rarity).lower() in TARGET_RARITIES:
            print(f"🔥 JACKPOT: Dropped a card with tier: {rarity}!")
            self.log(f"🔥 JACKPOT: tier {rarity}")
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
            print(f"💾 Preserved jackpot card (tier {card.get('potential_rarity')}) -> {path}")
        except Exception as e:
            print(f"Preserve card error: {e}")

    def check_active_deck(self):
        """Best-effort check for an active deck/card before battling.

        NON-FATAL: returns True in every case so the battle loop ALWAYS
        proceeds. A false-positive empty-deck marker (or an unreadable
        battle page) must never abort the farm. We only log a warning
        when the markers are clearly present; the loop still runs.
        """
        try:
            res = self.session.get("https://mangabuff.ru/battle", timeout=10)
            if res.status_code != 200:
                print(f"Battle page error: status {res.status_code} (will still attempt battles)")
                return True
            soup = BeautifulSoup(res.text, "html.parser")
            text = soup.get_text(" ", strip=True).lower()
            empty_markers = [
                "нет активной колод", "колода пуст", "no active deck",
                "deck is empty", "установите колод", "set a deck"
            ]
            if any(m in text for m in empty_markers):
                print("⚠️ Possible empty deck detected — but proceeding with battles anyway.")
                self.log("⚠️ Possible empty deck — proceeding with battles.")
            return True
        except Exception as e:
            print(f"check_active_deck exception: {e} (proceeding anyway)")
            return True

    def _handle_session_expired(self):
        """Re-authenticate when the server rejects the session (419/403 on
        battle endpoints). Returns True if a fresh session is established."""
        print("♻️ Session expired mid-farm. Auto-refreshing...")
        self.log("♻️ Session expired. Re-logging in...")
        if self.login_and_steal_keys():
            print("✅ Re-login successful! Resuming farm...")
            self.log("✅ Re-login successful! Resuming...")
            return True
        print("❌ Re-login failed. Stopping farm.")
        self.log("❌ Re-login failed. Stopping.")
        return False

    def get_actual_balance(self):
        """Scrape the user's REAL total essence from the server.

        Parses the balance page (and falls back to the mine page header)
        for the essence counter. Returns an int, or None if it cannot
        be determined. Used to track the 1000 daily cap against the
        authoritative server value instead of a blind local accumulator.
        """
        for url in ("https://mangabuff.ru/balance", "https://mangabuff.ru/mine"):
            try:
                res = self.session.get(url, timeout=10)
                if res.status_code != 200:
                    continue
                soup = BeautifulSoup(res.text, "html.parser")
                text = soup.get_text(" ", strip=True)

                # Pattern A: "Эссенция: N" / "Эссенция N" (RU, with optional colon)
                m = re.search(r"эссенц\w*\s*[:]?\s*([\d\s,.\w]+)", text, re.IGNORECASE)
                if m:
                    val = parse_smart_number(m.group(1))
                    if val > 0:
                        return val

                # Pattern B: "N эссенции" / "N essence" (value precedes word)
                m2 = re.search(r"([\d\s,.\w]+)\s*(эссенц|essence)", text, re.IGNORECASE)
                if m2:
                    val = parse_smart_number(m2.group(1))
                    if val > 0:
                        return val

                # Pattern C: any element whose class hints at essence/balance.
                # e.g. <span class="essence-count">1234</span>
                for tag in soup.find_all(class_=re.compile(r"essence|эссенц|balance|оре", re.IGNORECASE)):
                    inner = tag.get_text(strip=True)
                    val = parse_smart_number(inner)
                    if val > 0:
                        return val

                # DEBUG: if we still can't find it, dump a window around
                # the word "эссенц"/"essence" so we can see the real markup.
                print(f"=== GET_ACTUAL_BALANCE DEBUG ({url}) ===")
                m_dbg = re.search(r".{0,120}эссенц.{0,120}", text, re.IGNORECASE | re.DOTALL)
                if m_dbg:
                    print(m_dbg.group(0))
                else:
                    m_dbg2 = re.search(r".{0,120}essence.{0,120}", text, re.IGNORECASE | re.DOTALL)
                    if m_dbg2:
                        print(m_dbg2.group(0))
                    else:
                        print("No 'essence' substring found in page text.")
                        print("First 400 chars of page text:")
                        print(text[:400])
                print("=============================================")
            except Exception as e:
                print(f"get_actual_balance exception ({url}): {e}")
        return None

    def _find_active_claim_url(self):
        """Parse /balance HTML for the currently CLAIMABLE daily-reward button.

        Returns the full claim URL (e.g. https://mangabuff.ru/balance/claim/3)
        or None if there is no claimable button today.

        Strict rules so we never POST for an already-claimed / locked day:
          - The button must contain the text "Забрать" (claim) exactly.
          - It must NOT be disabled (no `disabled` attr, no `disabled` class).
          - Its container must NOT carry a "completed"/"claimed"/"locked"
            marker class.
        """
        try:
            res = self.session.get("https://mangabuff.ru/balance", timeout=10)
            if res.status_code != 200:
                return None
            soup = BeautifulSoup(res.text, "html.parser")

            # Refresh CSRF from the page meta (browser does this live).
            meta = soup.find("meta", attrs={"name": "csrf-token"})
            if meta and meta.get("content"):
                self.csrf_token = meta.get("content")

            # Find every element whose visible text is exactly the claim verb.
            claim_buttons = []
            for el in soup.find_all(string=lambda t: t and "Забрать" in t):
                node = el.parent
                if node:
                    claim_buttons.append(node)

            for btn in claim_buttons:
                # DEBUG: show exactly what element we are inspecting.
                print("=== CLAIM BUTTON CANDIDATE (prettify) ===")
                try:
                    print(btn.prettify())
                except Exception:
                    print(str(btn)[:1000])
                print("=============================================")

                # The "Забрать" text is usually inside a <span>. Walk UP
                # the DOM (find_parents) to find the real clickable element
                # that carries an href OR a data-day attribute.
                link = None
                for ancestor in [btn] + list(btn.find_parents()):
                    if ancestor is None:
                        continue
                    name = getattr(ancestor, "name", None)
                    if name in ("a", "button", "form"):
                        link = ancestor
                        break
                    if ancestor.get("href") or ancestor.get("data-day"):
                        link = ancestor
                        break
                if link is None:
                    link = btn

                # DEBUG: show the resolved clickable element.
                print("--- resolved clickable element ---")
                try:
                    print(link.prettify())
                except Exception:
                    print(str(link)[:1000])
                print("-----------------------------------------------")

                # REJECT if disabled (attribute or class).
                if link.get("disabled") is not None:
                    print("REJECTED: link has 'disabled' attribute.")
                    continue
                classes = " ".join(link.get("class", []))
                if "disabled" in classes.lower():
                    print("REJECTED: link has 'disabled' class.")
                    continue

                # REJECT if the surrounding reward item is completed/claimed/locked.
                parent = link.find_parent("div", class_="daily-rewards-item")
                if parent is None:
                    parent = link.parent
                if parent is not None:
                    pclasses = " ".join(parent.get("class", [])).lower()
                    if any(m in pclasses for m in
                           ("completed", "claimed", "locked", "inactive", "disabled", "taken")):
                        print(f"REJECTED: parent has marker class: {pclasses}")
                        continue

                # ACCEPT — extract the real URL.
                # 1) Explicit href/action on the resolved element.
                href = link.get("href") or link.get("action")
                if href:
                    if href.startswith("http"):
                        print(f"ACCEPTED claim URL: {href}")
                        return href
                    print(f"ACCEPTED claim URL: https://mangabuff.ru{href}")
                    return "https://mangabuff.ru" + href

                # 2) data-day attribute on the resolved element OR its
                #    daily-rewards-item container -> build the URL dynamically.
                day = link.get("data-day")
                if day is None and parent is not None:
                    day = parent.get("data-day")
                if day:
                    print(f"ACCEPTED claim URL (from data-day={day}): "
                          f"https://mangabuff.ru/balance/claim/{day}")
                    return f"https://mangabuff.ru/balance/claim/{day}"

                # No href and no data-day -> do NOT guess a day.
                print("REJECTED: claimable button has no href/action/data-day; skipping (no guess).")
                continue
        except Exception as e:
            print(f"_find_active_claim_url exception: {e}")
        return None

    def run_farm_loop(self):
        """Continuously grind battles until the daily essence limit is hit.

        DIRECT-EXTRACTION ARCHITECTURE (strict):
        check_battle_result() returns a tuple (earned_essence, is_win) parsed
        via regex directly from the battle result page's injected JSON. The
        earned essence for the CURRENT fight is extracted on its own, so there
        is NO need to track a running total or compute deltas.

        The daily cap (+1000 limit) is detected when earned_essence == 0
        (server stops awarding essence) — at which point we break the loop.

        Transient errors — matchmaking 400/422, success:false, network
        timeouts, unparseable results, or a 419 that could not be re-logged —
        NEVER stop the farm. They are retried with backoff so a brief
        hiccup can't prematurely end the session.
        """
        # Pre-flight: ensure an active deck/card is set
        if not self.check_active_deck():
            print("Aborting farm loop: no active deck.")
            return

        # Claim daily quests exactly ONCE before battling
        self.check_and_claim_quests()

        battles_done = 0
        consecutive_unknown = 0   # FAILSAFE: check_battle_result() returned None
        next_micro_break = random.randint(10, 15)
        while self.running if hasattr(self, 'running') else True:
            # Human-like pause between distinct actions
            time.sleep(random.uniform(4.5, 12.2))

            # Step1: start a battle, get the match redirect URL.
            # start_battle() returns None on ANY transient failure (never a
            # hard stop) and "NO_ENERGY" only when the server explicitly
            # reports 0 battle energy left.
            match_url = self.start_battle()
            if match_url == "NO_ENERGY":
                print("Out of battle energy (server signal).")
                break
            if not match_url:
                # Transient — back off briefly and retry, do NOT break.
                print("Matchmaking transient — backing off 30s, will retry.")
                time.sleep(30)
                continue

            # Step2: read the earned essence + win status for THIS fight via regex.
            result = self.check_battle_result(match_url)

            # If we could not determine the result (parse/network error),
            # do NOT count it as a 0-gain battle — that would falsely trip
            # the daily-limit stop. Retry after a normal delay.
            if result is None:
                consecutive_unknown += 1
                print(f"Battle result unknown ({consecutive_unknown}/3) — not counting toward limit; retrying.")
                if consecutive_unknown >= 3:
                    print("⚠️ 3 consecutive unparseable battle results — "
                          "aborting battle loop (likely a parser break).")
                    self.log("⚠️ Battle parser failing — stopping farm to avoid infinite loop.")
                    break
                time.sleep(random.uniform(10, 20))
                continue

            # Got a valid result — reset the unknown counter.
            consecutive_unknown = 0
            earned_essence, is_win = result
            battles_done += 1

            # Step3: DYNAMIC LIMIT CHECK (server-authoritative).
            # When the daily cap (+1000) is reached, the server awards 0 essence,
            # so earned_essence == 0. Break the farm loop immediately.
            if earned_essence == 0:
                outcome = "Win" if is_win else "Loss"
                print(f"[BATTLE] Result: {outcome} | Earned: +0 — daily cap reached. Stopping farm.")
                self.log("Daily essence limit reached (+0)")
                break

            # Step4: concise status line.
            outcome = "Win" if is_win else "Loss"
            print(f"[BATTLE] Result: {outcome} | Earned: +{earned_essence}")

            # Micro-break: simulate a human getting distracted
            if battles_done >= next_micro_break:
                brk = random.uniform(45, 120)
                print(f"😴 Micro-break ({brk:.0f}s) to simulate human activity...")
                time.sleep(brk)
                next_micro_break = battles_done + random.randint(10, 15)

            # Random human-like delay, repeat
            delay = random.uniform(10, 20)
            time.sleep(delay)

        # Claim any quests completed DURING the battles
        self.check_and_claim_quests()

    def stop(self):
        self.running = False
        self.log(tr("log_stopping"))

    def check_status_only(self):
        self.log(tr("log_session_load"))
        if not self.validate_session():
            if not self.login_and_steal_keys():
                return

        self.log(tr("log_source_check"))
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
                self.log(tr("log_stat_energy", val=hits))
                self.log(tr("log_stat_balance", val=f"{ore:,}"))

                if cost_raw:
                    self.log(tr("log_upgrade_cost", cost=cost_raw))
                else:
                    self.log(tr("log_upgrade_status_max"))

            else:
                self.log(f"⚠️ API Error: {res.status_code}")
        except Exception as e:
            self.log(tr("log_error_generic", e=e))
        self.log(tr("log_done"))

    def _run_mining(self):
        """Phase 1: grind ore via /mine/hit until energy is depleted.

        A 403 (ore energy empty) ONLY stops THIS mining phase — it does
        NOT abort the whole script. The caller (run) continues to the
        battle-farm phase regardless. 419 triggers a re-login and resume.
        """
        self.log(tr("log_mining_start"))

        clicks = 0
        consecutive_errors = 0

        while self.running:
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
                        self.current_balance = ore
                        clicks += 1
                        consecutive_errors = 0

                        self.log(f"⛏️ +{added} | ⚡ {hits_left} | 💎 {ore}")
                        self.update_stats(energy=hits_left, balance=ore)

                        prog = 1.0 - (hits_left / 100.0)
                        if prog < 0: prog = 0
                        self.update_progress(prog)

                        if hits_left <= 0:
                            self.log(tr("log_energy_empty"))
                            self.update_stats(energy=0, balance=ore)
                            break  # <-- only stops MINING, not the script

                        time.sleep(random.uniform(0.20, 0.30))

                    except Exception as e:
                        self.log(f"⚠️ JSON Error: {e}")
                        time.sleep(2)
                        continue

                else:
                    if response.status_code == 403:
                        # Ore energy depleted — stop MINING only.
                        self.log("⚡ Ore energy depleted (403). Moving to battles.")
                        self.update_stats(energy=0)
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

            except requests.exceptions.Timeout:
                self.log(tr("log_timeout"))
            except Exception as e:
                self.log(tr("log_error_generic", e=e))
                time.sleep(1)

    def run(self):
        """Main execution flow: claim daily reward, mine ore (/mine/hit)
        until energy is depleted, then farm battles until the daily essence
        cap (3 consecutive +0) or energy depletion. Clean status logging only."""
        self.running = True
        self.log(tr("log_session_load"))

        # Step 1: Load saved session (cookies + CSRF token)
        if not self.validate_session():
            self.log(tr("log_session_expired"))
            if not self.login_and_steal_keys():
                self.running = False
                return

        # Step 2: Validate session with server BEFORE making any API calls
        # (claim daily reward, mining, battles). This ensures we have a fresh
        # CSRF token and valid session before proceeding.
        if not self._validate_session_with_server():
            self.log(tr("log_session_expired"))
            if not self.login_and_steal_keys():
                self.running = False
                return

        # Claim daily reward once on startup, before any farming begins
        self.claim_daily_reward()

        # --- Phase 1: ore mining (stops on 403, never aborts script) ---
        self._run_mining()

        # --- Phase 2: battle farming (daily essence cap) ---
        # ALWAYS runs after mining, as long as the session is alive.
        # This is the ACTIVE battle loop: it calls start_battle() and
        # check_battle_result() continuously until 3 consecutive +0 essence
        # battles, a real NO_ENERGY, or the server 1000 cap.
        if self.running:
            self.run_farm_loop()

        self.running = False
        self.log(tr("log_mining_finish"))

    def _validate_session_with_server(self):
        """Make a lightweight GET request to verify the session is still valid.
        Returns True if session is valid, False if expired/invalid (419, 401, etc.)."""
        try:
            # Use the mine page as a lightweight validation endpoint
            res = self.session.get(CONFIG["urls"]["game"], timeout=10)
            if res.status_code == 200:
                # Check if we got redirected to login page (session expired)
                if "login" in res.url.lower() or "csrf-token" in res.text.lower():
                    self.log("⚠️ Session validation failed: redirected to login")
                    return False
                self.log(tr("log_session_valid"))
                return True
            elif res.status_code in (401, 419, 403):
                self.log(f"⚠️ Session validation failed with status {res.status_code}")
                return False
            else:
                self.log(f"⚠️ Session validation returned status {res.status_code}")
                return False
        except Exception as e:
            self.log(f"⚠️ Session validation error: {e}")
            return False


# ==========================================
# GUI CLASSES (CustomTkinter)
# ==========================================
class LoginDialog(ctk.CTkToplevel):
    def __init__(self, parent, callback):
        super().__init__(parent)
        self.callback = callback
        self.title(tr("login_title"))
        self.geometry("300x250")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        ctk.CTkLabel(self, text="MangaBuff Login", font=("Arial", 14, "bold")).pack(pady=15)
        self.entry_email = ctk.CTkEntry(self, placeholder_text="Email")
        self.entry_email.pack(pady=5, padx=20, fill="x")
        self.entry_pass = ctk.CTkEntry(self, placeholder_text="Password", show="*")
        self.entry_pass.pack(pady=5, padx=20, fill="x")
        ctk.CTkButton(self, text=tr("login_save"), command=self.on_save, fg_color="#2CC985").pack(pady=20)

    def on_save(self):
        email = self.entry_email.get().strip()
        pwd = self.entry_pass.get().strip()
        if email and pwd:
            DataManager.set_credentials(email, pwd)
            self.callback()
            self.destroy()
        else:
            messagebox.showwarning("Error", tr("error_fill"))


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.geometry("800x600")
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        self.bot = None
        global CURRENT_LANG
        saved_data = DataManager.load_json(DATA_FILE)
        CURRENT_LANG = saved_data.get("language", "English")
        self._setup_ui()
        self._load_saved_stats()
        self._check_login_state()
        self._update_reset_timer()
        self.refresh_ui_text()

    def _setup_ui(self):
        self.title(tr("app_title"))
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")

        self.logo = ctk.CTkLabel(self.sidebar, text="MangaBuff\nMiner", font=ctk.CTkFont(size=22, weight="bold"))
        self.logo.grid(row=0, column=0, padx=20, pady=(20, 10))

        self.cmb_lang = ctk.CTkComboBox(self.sidebar, values=["English", "Русский", "Українська"],
                                        command=self.change_language, width=140, state="readonly")
        self.cmb_lang.set(CURRENT_LANG)
        self.cmb_lang.grid(row=1, column=0, pady=(0, 10))

        self.lbl_account = ctk.CTkLabel(self.sidebar, text="Guest", text_color="gray")
        self.lbl_account.grid(row=2, column=0)

        self.btn_logout = ctk.CTkButton(self.sidebar, text=tr("btn_logout"), height=24, width=120, fg_color="#444",
                                        font=("Arial", 10), command=self.logout)
        self.btn_logout.grid(row=3, column=0, pady=(0, 20))

        self.timer_frame = ctk.CTkFrame(self.sidebar, fg_color="#2b2b2b", corner_radius=5)
        self.timer_frame.grid(row=4, column=0, padx=10, pady=5, sticky="ew")
        self.lbl_timer_title = ctk.CTkLabel(self.timer_frame, text=tr("timer_label"), font=("Arial", 10, "bold"),
                                            text_color="gray")
        self.lbl_timer_title.pack(pady=(5, 0))
        self.lbl_timer = ctk.CTkLabel(self.timer_frame, text="00:00:00", font=("Consolas", 18, "bold"),
                                      text_color="#FFAA00")
        self.lbl_timer.pack(pady=(0, 5))

        self.lbl_settings = ctk.CTkLabel(self.sidebar, text=tr("settings"), anchor="w", text_color="gray",
                                         font=ctk.CTkFont(size=11, weight="bold"))
        self.lbl_settings.grid(row=5, column=0, padx=20, pady=(10, 0), sticky="w")

        self.headless_var = ctk.BooleanVar(value=True)
        self.chk_headless = ctk.CTkSwitch(self.sidebar, text=tr("headless"), variable=self.headless_var)
        self.chk_headless.grid(row=6, column=0, padx=20, pady=(10, 5), sticky="w")

        self.lbl_actions = ctk.CTkLabel(self.sidebar, text=tr("controls"), anchor="w", text_color="gray",
                                        font=ctk.CTkFont(size=11, weight="bold"))
        self.lbl_actions.grid(row=7, column=0, padx=20, pady=(20, 0), sticky="w")

        self.btn_start = ctk.CTkButton(self.sidebar, text=tr("btn_start"), height=40, fg_color="#2CC985",
                                       hover_color="#229A65", command=self.start_bot)
        self.btn_start.grid(row=9, column=0, padx=20, pady=(10, 5))

        self.btn_status = ctk.CTkButton(self.sidebar, text=tr("btn_status"), height=40, fg_color="#3B8ED0",
                                        hover_color="#2D6D9E", command=self.check_status)
        self.btn_status.grid(row=10, column=0, padx=20, pady=5)

        self.btn_stop = ctk.CTkButton(self.sidebar, text=tr("btn_stop"), height=40, fg_color="#D94448",
                                      hover_color="#A83236", state="disabled", command=self.stop_bot)
        self.btn_stop.grid(row=11, column=0, padx=20, pady=(5, 10))

        self.progress_bar = ctk.CTkProgressBar(self.sidebar, orientation="horizontal", height=10)
        self.progress_bar.grid(row=12, column=0, padx=20, pady=(30, 10))
        self.progress_bar.set(0)

        self.main_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)

        self.stats_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.stats_frame.pack(fill="x", pady=(0, 20))

        self.card_energy = self._create_card(self.stats_frame, tr("card_energy"), "?")
        self.card_energy.pack(side="left", fill="both", expand=True, padx=(0, 10))
        self.card_balance = self._create_card(self.stats_frame, tr("card_balance"), "---")
        self.card_balance.pack(side="left", fill="both", expand=True, padx=(10, 0))

        self.lbl_log = ctk.CTkLabel(self.main_frame, text=tr("log_title"), font=ctk.CTkFont(size=12, weight="bold"))
        self.lbl_log.pack(anchor="w", pady=(0, 5))
        self.log_area = ctk.CTkTextbox(self.main_frame, width=400, font=("Consolas", 12))
        self.log_area.pack(fill="both", expand=True)
        self.log_area.configure(state="disabled")

    def _create_card(self, parent, title, value):
        frame = ctk.CTkFrame(parent, corner_radius=10)
        lbl_title = ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=12, weight="bold"), text_color="gray")
        lbl_title.pack(pady=(10, 0))
        lbl_value = ctk.CTkLabel(frame, text=value, font=ctk.CTkFont(size=20, weight="bold"))
        lbl_value.pack(pady=(0, 10))
        frame.title_label = lbl_title
        frame.value_label = lbl_value
        return frame

    def change_language(self, new_lang):
        global CURRENT_LANG
        CURRENT_LANG = new_lang
        DataManager.save_json(DATA_FILE, {"language": new_lang})
        self.refresh_ui_text()

    def refresh_ui_text(self):
        self.title(tr("app_title"))
        self.lbl_settings.configure(text=tr("settings"))
        self.chk_headless.configure(text=tr("headless"))
        self.lbl_actions.configure(text=tr("controls"))
        self.btn_start.configure(text=tr("btn_start"))
        self.btn_status.configure(text=tr("btn_status"))
        self.btn_stop.configure(text=tr("btn_stop"))
        self.btn_logout.configure(text=tr("btn_logout"))
        self.lbl_timer_title.configure(text=tr("timer_label"))
        self.card_energy.title_label.configure(text=tr("card_energy"))
        self.card_balance.title_label.configure(text=tr("card_balance"))
        self.lbl_log.configure(text=tr("log_title"))
        self._check_login_state()

    def _update_reset_timer(self):
        msk_offset = timezone(timedelta(hours=3))
        now_msk = datetime.datetime.now(msk_offset)
        next_reset = (now_msk + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        time_left = next_reset - now_msk
        total_seconds = int(time_left.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        self.lbl_timer.configure(text=f"{hours:02}:{minutes:02}:{seconds:02}")
        self.after(1000, self._update_reset_timer)

    def _check_login_state(self):
        email, pwd = DataManager.get_credentials()
        if email and pwd:
            self.lbl_account.configure(text=f"User: {email}")
            self.btn_start.configure(state="normal")
            self.btn_status.configure(state="normal")
        else:
            self.lbl_account.configure(text=tr("lbl_guest"))
            self.btn_start.configure(state="disabled")
            self.btn_status.configure(state="disabled")

    def _load_saved_stats(self):
        data = DataManager.load_json(DATA_FILE)
        saved_bal = data.get("last_balance", "---")
        self.card_balance.value_label.configure(text=f"{saved_bal}")

    def logout(self):
        DataManager.clear_credentials()
        self._check_login_state()
        self.log(tr("log_logout"))
        LoginDialog(self, self._check_login_state)

    def prompt_login(self):
        LoginDialog(self, self._check_login_state)

    def update_stats_ui(self, energy=None, balance=None):
        def _update():
            if balance is not None:
                DataManager.save_json(DATA_FILE, {"last_balance": balance})
                self.card_balance.value_label.configure(text=f"{balance:,}")
            if energy is not None: self.card_energy.value_label.configure(text=str(energy))

        self.after(0, _update)

    def log(self, message):
        timestamp = time.strftime('%H:%M:%S')
        full_msg = f"[{timestamp}] {message}\n"

        def _log_thread_safe():
            self.log_area.configure(state="normal")
            self.log_area.insert("end", full_msg)
            self.log_area.see("end")
            self.log_area.configure(state="disabled")

        self.after(0, _log_thread_safe)

    def update_progress(self, value):
        self.after(0, lambda: self.progress_bar.set(value))

    def _init_bot(self):
        self.bot = MangaMinerBot(
            log_callback=self.log,
            progress_callback=self.update_progress,
            stats_callback=self.update_stats_ui,
            headless=self.headless_var.get()
        )

    def _lock_ui(self, is_running):
        if is_running:
            self.btn_start.configure(state="disabled", fg_color="gray")
            self.btn_status.configure(state="disabled", fg_color="gray")
            self.btn_logout.configure(state="disabled")
            self.btn_stop.configure(state="normal", fg_color="#D94448")
            self.progress_bar.set(0)
        else:
            self.btn_start.configure(state="normal", fg_color="#2CC985")
            self.btn_status.configure(state="normal", fg_color="#3B8ED0")
            self.btn_logout.configure(state="normal")
            self.btn_stop.configure(state="disabled", fg_color="gray")

    def start_bot(self):
        if self.bot and self.bot.running: return
        email, pwd = DataManager.get_credentials()
        if not email:
            self.prompt_login()
            return
        self._lock_ui(True)
        self._init_bot()
        self.log(tr("log_init"))
        threading.Thread(target=self._run_mining_thread, daemon=True).start()

    def check_status(self):
        if self.bot and self.bot.running: return
        email, pwd = DataManager.get_credentials()
        if not email:
            self.prompt_login()
            return
        self._lock_ui(True)
        self._init_bot()
        threading.Thread(target=self._run_status_thread, daemon=True).start()

    def stop_bot(self):
        if self.bot:
            self.bot.stop()
            self.btn_stop.configure(state="disabled")

    def _run_mining_thread(self):
        try:
            self.bot.run()
        except Exception as e:
            self.log(tr("log_thread_crash", e=e))
        finally:
            self.after(0, lambda: self._lock_ui(False))

    def _run_status_thread(self):
        try:
            self.bot.check_status_only()
        except Exception as e:
            self.log(tr("log_thread_crash", e=e))
        finally:
            self.after(0, lambda: self._lock_ui(False))


if __name__ == "__main__":
    # ==========================================================
    # GUI DISABLED — CLI TEST MODE
    # Re-enable the GUI later by commenting out the block below
    # and uncommenting:  app = App(); app.mainloop()
    # ==========================================================
    # app = App()
    # app.mainloop()

    # --- Temporary CLI test: init bot and test claim_daily_reward() ---
    def _cli_log(msg):
        print(f"[BOT] {msg}")
        send_message(f"[BOT] {msg}")

    print("=== CLI TEST MODE ===")
    bot = MangaMinerBot(
        log_callback=_cli_log,
        progress_callback=lambda p: None,
        stats_callback=lambda **kw: None,
        headless=True
    )

    if not bot.validate_session():
        print("No valid session — logging in via API...")
        if not bot.login_and_steal_keys():
            print("LOGIN FAILED. Aborting test.")
        else:
            print("Login OK. Running main loop (daily reward + mining + battle farm)...")
            # run() handles EVERYTHING: claims daily reward, mines (/mine/hit),
            # then calls run_farm_loop() (which claims quests ONCE and grinds
            # battles). Do NOT call run_farm_loop() again here — that would
            # duplicate the quest check and re-run the entire battle farm.
            bot.run()
    else:
        print("Session valid. Running main loop (daily reward + mining + battle farm)...")
        bot.run()
