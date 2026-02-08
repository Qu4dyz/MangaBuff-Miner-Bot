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
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# ==========================================
# CONFIGURATION & SELECTORS
# ==========================================
CONFIG = {
    "urls": {
        "login": "https://mangabuff.ru/login",
        "game": "https://mangabuff.ru/mine",
        "upgrade": "https://mangabuff.ru/mine/upgrade"
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

# ==========================================
# TRANSLATIONS
# ==========================================
LANGUAGES = {
    "English": {
        "app_title": "MangaBuff Miner",
        "settings": "SETTINGS",
        "headless": "Headless Login",
        "auto_upgrade": "Auto Upgrade",
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

        # LOG MESSAGES
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
        "log_auto_on": "ON",
        "log_auto_off": "OFF",
        "log_timeout": "⚠️ Timeout",
        "log_error_generic": "💥 Error: {e}",
        "log_thread_crash": "💥 Thread Crash: {e}"
    },
    "Русский": {
        "app_title": "MangaBuff Miner",
        "settings": "НАСТРОЙКИ",
        "headless": "Скрытый вход",
        "auto_upgrade": "Авто-улучшение",
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

        # LOG MESSAGES
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
        "log_auto_on": "ВКЛ",
        "log_auto_off": "ВЫКЛ",
        "log_timeout": "⚠️ Тайм-аут соединения",
        "log_error_generic": "💥 Ошибка: {e}",
        "log_thread_crash": "💥 Крах потока: {e}"
    },
    "Українська": {
        "app_title": "MangaBuff Miner",
        "settings": "НАЛАШТУВАННЯ",
        "headless": "Прихований вхід",
        "auto_upgrade": "Авто-покращення",
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

        # LOG MESSAGES
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
        "log_auto_on": "УВІМК",
        "log_auto_off": "ВИМК",
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


DATA_FILE = "user_data.json"
SESSION_FILE = "session_cache.json"


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


# ==========================================
# BOT ENGINE
# ==========================================
class MangaMinerBot:
    def __init__(self, log_callback, progress_callback, stats_callback, headless=True, auto_upgrade=False):
        self.log = log_callback
        self.update_progress = progress_callback
        self.update_stats = stats_callback
        self.headless = headless
        self.auto_upgrade = auto_upgrade
        self.running = False
        self.API_URL = "https://mangabuff.ru/mine/hit"
        self.session = requests.Session()
        self.email, self.password = DataManager.get_credentials()
        self.csrf_token = None
        self.user_agent = None
        self.current_balance = 0

    def _init_driver(self):
        options = Options()
        if self.headless: options.add_argument("--headless=new")
        options.add_argument("--window-size=1200,800")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--log-level=3")
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=options)

    def validate_session(self):
        data = DataManager.load_session()
        if not data or "cookies" not in data or "csrf" not in data:
            return False

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
        self.log(tr("log_login_start"))
        driver = None
        try:
            driver = self._init_driver()
            wait = WebDriverWait(driver, 20)

            driver.get(CONFIG["urls"]["login"])
            wait.until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, CONFIG["selectors"]["login_input"]))).send_keys(
                self.email)
            driver.find_element(By.CSS_SELECTOR, CONFIG["selectors"]["pass_input"]).send_keys(self.password)

            try:
                btn = driver.find_element(By.CSS_SELECTOR, CONFIG["selectors"]["login_btn"])
            except:
                btn = driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
            driver.execute_script("arguments[0].click();", btn)

            self.log(tr("log_process_login"))
            time.sleep(3)
            driver.get(CONFIG["urls"]["game"])

            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, CONFIG["selectors"]["mine_element"])))
                self.log(tr("log_login_ok"))
            except:
                self.log(tr("log_login_fail_page"))
                driver.quit()
                return False

            self.log(tr("log_steal_keys"))

            cookies_dict = {c['name']: c['value'] for c in driver.get_cookies()}
            self.session.cookies.update(cookies_dict)

            try:
                self.csrf_token = driver.execute_script(
                    "return document.querySelector('meta[name=\"csrf-token\"]').getAttribute('content');")
            except:
                self.csrf_token = None

            self.user_agent = driver.execute_script("return navigator.userAgent;")

            if not self.csrf_token:
                self.log(tr("log_login_fail_csrf"))
                return False

            self.session.headers.update({
                "User-Agent": self.user_agent,
                "X-CSRF-TOKEN": self.csrf_token,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://mangabuff.ru/mine",
                "Origin": "https://mangabuff.ru",
                "Content-Type": "application/json"
            })

            DataManager.save_session(cookies_dict, self.csrf_token, self.user_agent)
            driver.quit()
            self.log(tr("log_god_mode"))
            return True

        except Exception as e:
            self.log(tr("log_login_crash", e=e))
            if driver: driver.quit()
            return False

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
                # FIXED: Double backslashes \\s to avoid SyntaxWarning
                ore_match = re.search(f'class="[^"]*{b_cls}[^"]*">\\s*([\\d\\.\\s,kKmM]+)\\s*<', html)
                if ore_match: ore_raw = ore_match.group(1)

                e_cls = CONFIG["selectors"]["energy_class"]
                # FIXED: Double backslashes \\s to avoid SyntaxWarning
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

    def attempt_upgrade(self):
        self.log(tr("log_upgrade_check"))
        try:
            res = self.session.get(CONFIG["urls"]["game"], timeout=5)
            if res.status_code != 200: return
            html = res.text

            price_match = re.search(r'class="[^"]*cost[^"]*">\s*([\d\.\s,kKmM]+)\s*<', html)
            if not price_match:
                price_match = re.search(r'upgrade-btn.*?<span>([\d\.\s,kKmM]+)</span>', html, re.IGNORECASE)

            if price_match:
                price_str = price_match.group(1)
                price = parse_smart_number(price_str)

                if self.current_balance >= price and price > 0:
                    self.log(tr("log_buy_upgrade", price=price))
                    up_res = self.session.post(CONFIG["urls"]["upgrade"], json={}, timeout=5)
                    if up_res.status_code == 200:
                        self.log(tr("log_upgrade_ok"))
                    else:
                        self.log(tr("log_upgrade_fail"))
            else:
                self.log(tr("log_no_upgrade"))

        except Exception as e:
            self.log(f"⚠️ Upgrade logic error: {e}")

    def run(self):
        self.running = True
        self.log(tr("log_session_load"))

        # Initial validation
        if not self.validate_session():
            if not self.login_and_steal_keys():
                self.running = False
                return

        self.log(tr("log_mining_start"))

        status_text = tr("log_auto_on") if self.auto_upgrade else tr("log_auto_off")
        self.log(f"🚀 SPEED: Turbo | Auto-Upgrade: {status_text}")

        clicks = 0
        consecutive_errors = 0

        while self.running:
            try:
                # payload = {"hits": 1} # <-- Make sure this matches your working payload
                # Use a specific payload if needed, e.g. based on your miner version
                response = self.session.post(self.API_URL, json={"hits": 1}, timeout=5)

                if response.status_code == 200:
                    try:
                        data = response.json()
                        # ... (Rest of your parsing logic remains the same) ...
                        ore = data.get('ore', 0)
                        hits_left = data.get('hits_left', 0)
                        added = data.get('added', 0)
                        self.current_balance = ore

                        clicks += 1
                        consecutive_errors = 0  # Reset errors on success

                        self.log(f"⛏️ +{added} | ⚡ {hits_left} | 💎 {ore}")
                        self.update_stats(energy=hits_left, balance=ore)

                        prog = 1.0 - (hits_left / 100.0)
                        if prog < 0: prog = 0
                        self.update_progress(prog)

                        if self.auto_upgrade and (clicks % 15 == 0):
                            self.attempt_upgrade()

                        if hits_left <= 0:
                            self.log(tr("log_energy_empty"))
                            self.update_stats(energy=0, balance=ore)
                            break

                        time.sleep(random.uniform(0.20, 0.30))

                    except Exception as e:
                        # JSON parse error (server might have sent HTML instead of JSON)
                        self.log(f"⚠️ JSON Error: {e}")
                        time.sleep(2)
                        continue

                else:
                    self.log(f"⚠️ Server: {response.status_code}")

                    # === 🛠️ CHANGE HERE: Handle 419 Explicitly ===
                    if response.status_code == 419:
                        self.log("♻️ Session expired (419). Auto-refreshing...")

                        # Try to re-login immediately without stopping the bot
                        if self.login_and_steal_keys():
                            self.log("✅ Re-login successful! Resuming mining...")
                            consecutive_errors = 0
                            time.sleep(1)
                            continue  # Go back to the top of the loop with new keys
                        else:
                            self.log("❌ Re-login failed. Stopping.")
                            break
                    # ===============================================

                    consecutive_errors += 1
                    time.sleep(2)

                    if consecutive_errors > 3:
                        self.log(tr("log_session_restart"))
                        # Optional: One last try to login before quitting?
                        if self.login_and_steal_keys():
                            consecutive_errors = 0
                            continue
                        break

            except requests.exceptions.Timeout:
                self.log(tr("log_timeout"))
            except Exception as e:
                self.log(tr("log_error_generic", e=e))
                time.sleep(1)

        self.running = False
        self.log(tr("log_mining_finish"))


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

        self.upgrade_var = ctk.BooleanVar(value=False)
        self.chk_upgrade = ctk.CTkSwitch(self.sidebar, text=tr("auto_upgrade"), variable=self.upgrade_var,
                                         state="normal")
        self.chk_upgrade.grid(row=7, column=0, padx=20, pady=(5, 10), sticky="w")

        self.lbl_actions = ctk.CTkLabel(self.sidebar, text=tr("controls"), anchor="w", text_color="gray",
                                        font=ctk.CTkFont(size=11, weight="bold"))
        self.lbl_actions.grid(row=8, column=0, padx=20, pady=(20, 0), sticky="w")

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
        self.chk_upgrade.configure(text=tr("auto_upgrade"))
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
            headless=self.headless_var.get(),
            auto_upgrade=self.upgrade_var.get()
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
    app = App()
    app.mainloop()