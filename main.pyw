import time
import random
import os
import re
import json
import threading
import datetime
from datetime import timedelta, timezone
import customtkinter as ctk
from tkinter import messagebox
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# === СЛОВАРЬ ПЕРЕВОДОВ ===
LANGUAGES = {
    "English": {
        "app_title": "MangaBuff Miner v8.0",
        "settings": "SETTINGS",
        "headless": "Headless Mode",
        "auto_upgrade": "Auto Upgrade",
        "controls": "CONTROLS",
        "btn_start": "🚀 START MINING",
        "btn_status": "📊 CHECK STATUS",
        "btn_stop": "🛑 STOP WORK",
        "btn_logout": "Sign Out / Change",
        "lbl_guest": "No Account",
        "timer_label": "NEXT RESET (MSK):",
        "log_title": "ACTIVITY LOG",
        "card_energy": "⚡ ENERGY",
        "card_balance": "💎 BALANCE",
        "card_clicks": "🖱️ CLICKS",
        "login_title": "Account Login",
        "login_save": "Save & Login",
        "error_env": "Please log in first!",
        "error_fill": "Please fill all fields!",

        # Логи бота
        "log_mode_headless": "👻 Mode: Headless (Invisible)",
        "log_mode_visible": "👀 Mode: Visible Browser",
        "log_stopping": "🛑 Stopping requested...",
        "log_login_start": "🚪 Logging in...",
        "log_login_ok": "✅ Login successful!",
        "log_login_fail": "❌ Login failed: {e}",
        "log_status_check": "📊 Checking Status...",
        "log_max_level": "MAX LEVEL 🌟",
        "log_check_done": "👋 Done.",
        "log_shop_check": "🔧 Checking store (End of session)...",
        "log_nothing_buy": "✅ Nothing to buy.",
        "log_buying": "💰 Buying upgrade...",
        "log_buy_ok": "🆙 Upgrade purchased!",
        "log_no_funds": "📉 Not enough funds.",
        "log_btn_missing": "⚠️ Upgrade button not found (Max Level?)",
        "log_nav_game": "🎮 Navigating to Mining Game...",
        "log_mining_start": "⛏️ Starting mining loop (JS Mode)...",
        "log_energy_info": "⚡ Energy: {energy} | Clicks: {clicks}",
        "log_energy_empty": "🛑 Energy empty.",
        "log_break": "🚬 Break ({s:.1f}s)...",
        "log_mining_finish": "🏁 Mining finished.",
        "log_browser_close": "👋 Browser closed.",
        "log_logout": "ℹ️ Signed out. Please restart or login.",
        "log_init": "🚀 Initializing Bot...",

        # Статистика в логах
        "log_stat_energy": "⚡ Energy:  {val}",
        "log_stat_balance": "💎 Balance: {val} ore",
        "log_stat_upgrade": "🛠️ Upgrade: {val}",
        "log_bal_cost": "💎 Balance: {bal} | Cost: {cost}"
    },
    "Русский": {
        "app_title": "MangaBuff Майнер v8.0",
        "settings": "НАСТРОЙКИ",
        "headless": "Скрытый режим",
        "auto_upgrade": "Авто-улучшение",
        "controls": "УПРАВЛЕНИЕ",
        "btn_start": "🚀 НАЧАТЬ МАЙНИНГ",
        "btn_status": "📊 ПРОВЕРИТЬ СТАТУС",
        "btn_stop": "🛑 ОСТАНОВИТЬ",
        "btn_logout": "Выйти / Сменить",
        "lbl_guest": "Нет аккаунта",
        "timer_label": "СБРОС (МСК):",
        "log_title": "ЛОГ ДЕЙСТВИЙ",
        "card_energy": "⚡ ЭНЕРГИЯ",
        "card_balance": "💎 БАЛАНС",
        "card_clicks": "🖱️ КЛИКИ",
        "login_title": "Вход в аккаунт",
        "login_save": "Сохранить и Войти",
        "error_env": "Сначала войдите в аккаунт!",
        "error_fill": "Заполните все поля!",

        "log_mode_headless": "👻 Режим: Скрытый (Невидимка)",
        "log_mode_visible": "👀 Режим: Видимый браузер",
        "log_stopping": "🛑 Запрос остановки...",
        "log_login_start": "🚪 Вход в аккаунт...",
        "log_login_ok": "✅ Успешный вход!",
        "log_login_fail": "❌ Ошибка входа: {e}",
        "log_status_check": "📊 Проверка статуса...",
        "log_max_level": "МАКС УРОВЕНЬ 🌟",
        "log_check_done": "👋 Готово.",
        "log_shop_check": "🔧 Проверка магазина (Конец сессии)...",
        "log_nothing_buy": "✅ Покупать нечего.",
        "log_buying": "💰 Покупка улучшения...",
        "log_buy_ok": "🆙 Улучшение куплено!",
        "log_no_funds": "📉 Недостаточно средств.",
        "log_btn_missing": "⚠️ Кнопка не найдена (Возможно Макс?)",
        "log_nav_game": "🎮 Переход в шахту...",
        "log_mining_start": "⛏️ Старт цикла (JS Mode)...",
        "log_energy_info": "⚡ Энергия: {energy} | Клики: {clicks}",
        "log_energy_empty": "🛑 Энергия закончилась.",
        "log_break": "🚬 Перекур ({s:.1f}с)...",
        "log_mining_finish": "🏁 Майнинг завершен.",
        "log_browser_close": "👋 Браузер закрыт.",
        "log_logout": "ℹ️ Выход выполнен. Войдите заново.",
        "log_init": "🚀 Запуск бота...",

        "log_stat_energy": "⚡ Энергия:  {val}",
        "log_stat_balance": "💎 Баланс: {val} руды",
        "log_stat_upgrade": "🛠️ Улучшение: {val}",
        "log_bal_cost": "💎 Баланс: {bal} | Цена: {cost}"
    },
    "Українська": {
        "app_title": "MangaBuff Майнер v8.0",
        "settings": "НАЛАШТУВАННЯ",
        "headless": "Прихований режим",
        "auto_upgrade": "Авто-покращення",
        "controls": "КЕРУВАННЯ",
        "btn_start": "🚀 ПОЧАТИ МАЙНІНГ",
        "btn_status": "📊 ПЕРЕВІРИТИ СТАТУС",
        "btn_stop": "🛑 ЗУПИНИТИ",
        "btn_logout": "Вийти / Змінити",
        "lbl_guest": "Немає акаунту",
        "timer_label": "СКИДАННЯ (МСК):",
        "log_title": "ЛОГ ДІЙ",
        "card_energy": "⚡ ЕНЕРГІЯ",
        "card_balance": "💎 БАЛАНС",
        "card_clicks": "🖱️ КЛІКИ",
        "login_title": "Вхід в акаунт",
        "login_save": "Зберегти та Увійти",
        "error_env": "Спочатку увійдіть в акаунт!",
        "error_fill": "Заповніть усі поля!",

        "log_mode_headless": "👻 Режим: Прихований (Невидимка)",
        "log_mode_visible": "👀 Режим: Видимий браузер",
        "log_stopping": "🛑 Запит зупинки...",
        "log_login_start": "🚪 Вхід в акаунт...",
        "log_login_ok": "✅ Успішний вхід!",
        "log_login_fail": "❌ Помилка входу: {e}",
        "log_status_check": "📊 Перевірка статусу...",
        "log_max_level": "МАКС РІВЕНЬ 🌟",
        "log_check_done": "👋 Готово.",
        "log_shop_check": "🔧 Перевірка магазину (Кінець сесії)...",
        "log_nothing_buy": "✅ Купувати нічого.",
        "log_buying": "💰 Купівля покращення...",
        "log_buy_ok": "🆙 Покращення придбано!",
        "log_no_funds": "📉 Недостатньо коштів.",
        "log_btn_missing": "⚠️ Кнопка не знайдена (Можливо Макс?)",
        "log_nav_game": "🎮 Перехід у шахту...",
        "log_mining_start": "⛏️ Старт циклу (JS Mode)...",
        "log_energy_info": "⚡ Енергія: {energy} | Кліки: {clicks}",
        "log_energy_empty": "🛑 Енергія закінчилася.",
        "log_break": "🚬 Перекур ({s:.1f}с)...",
        "log_mining_finish": "🏁 Майнінг завершено.",
        "log_browser_close": "👋 Браузер закрито.",
        "log_logout": "ℹ️ Вихід виконано. Увійдіть знову.",
        "log_init": "🚀 Запуск бота...",

        "log_stat_energy": "⚡ Енергія:  {val}",
        "log_stat_balance": "💎 Баланс: {val} руди",
        "log_stat_upgrade": "🛠️ Покращення: {val}",
        "log_bal_cost": "💎 Баланс: {bal} | Ціна: {cost}"
    }
}

# Глобальная переменная текущего языка
CURRENT_LANG = "English"


def tr(key, **kwargs):
    """Функция перевода с поддержкой форматирования"""
    text = LANGUAGES[CURRENT_LANG].get(key, key)
    if kwargs:
        return text.format(**kwargs)
    return text


# === КОНФИГ И СЕЛЕКТОРЫ ===
CONFIG = {
    "urls": {
        "login": "https://mangabuff.ru/login",
        "game": "https://mangabuff.ru/mine"
    },
    "selectors": {
        "login_input": 'body > div.wrapper > div.main > div > div > div.form > input:nth-child(1)',
        "pass_input": 'body > div.wrapper > div.main > div > div > div.form > input:nth-child(2)',
        "login_btn": 'body > div.wrapper > div.main > div > div > div.form > button',
        "mine_btn": ".main-mine__game button",
        "energy_counter": "body > main > div.main-mine__game > div.main-mine__game-panel > span > span",
        "shop_open_btn": "body > main > div.main-mine__header > div.main-mine__header_score",
        "current_ore": "#modal-mine-shop > div > div > div > div.mine-shop > div.mine-shop__ore-block.mb-3 > span",
        "upgrade_info": "#modal-mine-shop > div > div > div > div.mine-shop > div.mine-shop__upgrade",
        "upgrade_buy_btn": "#modal-mine-shop > div > div > div > div.mine-shop > div.mine-shop__upgrade > button"
    }
}

DATA_FILE = "user_data.json"


# === МЕНЕДЖЕР ДАННЫХ ===
class DataManager:
    @staticmethod
    def load_data():
        if not os.path.exists(DATA_FILE):
            return {}
        try:
            with open(DATA_FILE, "r", encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}

    @staticmethod
    def save_data(data):
        try:
            current = DataManager.load_data()
            current.update(data)
            with open(DATA_FILE, "w", encoding='utf-8') as f:
                json.dump(current, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Save error: {e}")

    @staticmethod
    def get_credentials():
        data = DataManager.load_data()
        return data.get("email"), data.get("password")

    @staticmethod
    def set_credentials(email, password):
        DataManager.save_data({"email": email, "password": password})

    @staticmethod
    def clear_credentials():
        data = DataManager.load_data()
        if "email" in data: del data["email"]
        if "password" in data: del data["password"]
        with open(DATA_FILE, "w", encoding='utf-8') as f:
            json.dump(data, f, indent=4)


# === ЛОГИКА БОТА ===
class MangaMinerBot:
    def __init__(self, log_callback, progress_callback, stats_callback, headless=True, auto_upgrade=False):
        self.log = log_callback
        self.update_progress = progress_callback
        self.update_stats = stats_callback
        self.headless = headless
        self.auto_upgrade = auto_upgrade
        self.running = False
        self.driver = None
        self.wait = None

        self.email, self.password = DataManager.get_credentials()

    def _init_driver(self):
        options = Options()
        if self.headless:
            self.log(tr("log_mode_headless"))
            options.add_argument("--headless=new")
        else:
            self.log(tr("log_mode_visible"))

        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument("--log-level=3")

        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=options)

    def stop(self):
        self.running = False
        self.log(tr("log_stopping"))

    def _random_sleep(self, min_s, max_s):
        if self.running:
            time.sleep(random.uniform(min_s, max_s))

    def _parse_first_int(self, text):
        if not text: return 0
        digits = re.findall(r'\d+', text)
        if digits:
            return int(digits[0])
        return 0

    def login(self):
        self.log(tr("log_login_start"))
        try:
            self.driver.get(CONFIG["urls"]["login"])
            self._random_sleep(2, 4)

            self.wait.until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, CONFIG["selectors"]["login_input"]))).send_keys(
                self.email)
            self._random_sleep(0.5, 1)
            self.driver.find_element(By.CSS_SELECTOR, CONFIG["selectors"]["pass_input"]).send_keys(self.password)
            self._random_sleep(0.5, 1)
            self.driver.find_element(By.CSS_SELECTOR, CONFIG["selectors"]["login_btn"]).click()

            self.wait.until_not(EC.presence_of_element_located((By.CSS_SELECTOR, CONFIG["selectors"]["login_input"])))
            self.log(tr("log_login_ok"))
            return True
        except Exception as e:
            self.log(tr("log_login_fail", e=e))
            return False

    def close_modals(self):
        try:
            webdriver.ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
            time.sleep(1)
        except:
            pass

    def check_status_only(self):
        self.running = True
        try:
            self.driver = self._init_driver()
            self.wait = WebDriverWait(self.driver, 15)
            if self.login():
                self.log(tr("log_status_check"))
                self.driver.get(CONFIG["urls"]["game"])
                time.sleep(3)

                try:
                    counter = self.wait.until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, CONFIG["selectors"]["energy_counter"])))
                    energy = self._parse_first_int(counter.text)
                except:
                    energy = 0

                shop_btn = self.wait.until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, CONFIG["selectors"]["shop_open_btn"])))
                shop_btn.click()
                time.sleep(2)
                ore_elem = self.wait.until(
                    EC.visibility_of_element_located((By.CSS_SELECTOR, CONFIG["selectors"]["current_ore"])))
                info_elem = self.driver.find_element(By.CSS_SELECTOR, CONFIG["selectors"]["upgrade_info"])

                info_text = info_elem.text.lower()
                current_ore = self._parse_first_int(ore_elem.text)

                self.update_stats(energy=energy, balance=current_ore)

                if "максимум" in info_text or "max" in info_text:
                    cost_msg = tr("log_max_level")
                else:
                    cost = self._parse_first_int(info_elem.text)
                    cost_msg = f"{cost:,} ore"

                self.log("-" * 30)
                # ИСПОЛЬЗУЕМ ПЕРЕВОД ТЕПЕРЬ И ЗДЕСЬ
                self.log(tr("log_stat_energy", val=energy))
                self.log(tr("log_stat_balance", val=f"{current_ore:,}"))
                self.log(tr("log_stat_upgrade", val=cost_msg))
                self.log("-" * 30)
                self.close_modals()
        except Exception as e:
            self.log(f"💥 Error: {e}")
        finally:
            if self.driver: self.driver.quit()
            self.running = False
            self.log(tr("log_check_done"))

    def perform_upgrade(self):
        self.log(tr("log_shop_check"))
        try:
            if self.driver.current_url != CONFIG["urls"]["game"]:
                self.driver.get(CONFIG["urls"]["game"])
                time.sleep(2)

            shop_btn = self.wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, CONFIG["selectors"]["shop_open_btn"])))
            shop_btn.click()
            self._random_sleep(1.5, 2.5)

            ore_elem = self.wait.until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, CONFIG["selectors"]["current_ore"])))
            info_elem = self.driver.find_element(By.CSS_SELECTOR, CONFIG["selectors"]["upgrade_info"])

            info_text = info_elem.text.lower()
            current_ore = self._parse_first_int(ore_elem.text)

            self.update_stats(balance=current_ore)

            if "максимум" in info_text or "max" in info_text:
                self.log(f"💎 Balance: {current_ore} | Status: {tr('log_max_level')}")
                self.log(tr("log_nothing_buy"))
                self.close_modals()
                return

            upgrade_cost = self._parse_first_int(info_elem.text)
            # ИСПОЛЬЗУЕМ ПЕРЕВОД ЗДЕСЬ
            self.log(tr("log_bal_cost", bal=f"{current_ore:,}", cost=f"{upgrade_cost:,}"))

            if current_ore >= upgrade_cost and upgrade_cost > 0:
                self.log(tr("log_buying"))
                buy_btn = self.driver.find_element(By.CSS_SELECTOR, CONFIG["selectors"]["upgrade_buy_btn"])
                buy_btn.click()
                self._random_sleep(2, 3)
                self.log(tr("log_buy_ok"))
            else:
                self.log(tr("log_no_funds"))
            self.close_modals()
        except Exception as e:
            if "no such element" in str(e).lower():
                self.log(tr("log_btn_missing"))
            else:
                self.log(f"⚠️ Store check info: {e}")
            self.close_modals()

    def run(self):
        self.running = True
        try:
            self.driver = self._init_driver()
            self.wait = WebDriverWait(self.driver, 15)

            if self.login():
                self.log(tr("log_nav_game"))
                self.driver.get(CONFIG["urls"]["game"])
                try:
                    button = self.wait.until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, CONFIG["selectors"]["mine_btn"])))
                except:
                    self.driver.refresh()
                    time.sleep(3)
                    button = self.wait.until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, CONFIG["selectors"]["mine_btn"])))

                self.log(tr("log_mining_start"))

                clicks_done = 0
                consecutive = 0

                # Сброс прогресса в интерфейсе (ставим 50% т.к. конца мы не знаем, пока не кончится энергия)
                self.update_progress(0.1)
                self.update_stats(clicks=0)

                # === ГЛАВНОЕ ИЗМЕНЕНИЕ: Бесконечный цикл, пока есть энергия ===
                while self.running:
                    try:
                        # 1. Проверяем энергию
                        # Чтобы не грузить процессор, читаем текст энергии каждые 15 кликов
                        # (или каждый раз, если кликов мало, чтобы точно поймать ноль)
                        if clicks_done % 15 == 0 or clicks_done < 50:
                            counter = self.driver.find_element(By.CSS_SELECTOR, CONFIG["selectors"]["energy_counter"])
                            energy = self._parse_first_int(counter.text)

                            self.log(tr("log_energy_info", energy=energy, clicks=clicks_done))
                            self.update_stats(energy=energy, clicks=clicks_done)

                            # Если энергии 0 — стоп машина
                            if energy <= 0:
                                self.log(tr("log_energy_empty"))
                                self.update_stats(energy=0)
                                break

                            # Анимация прогресс-бара (просто чтобы бегал)
                            fake_progress = (clicks_done % 500) / 500
                            self.update_progress(fake_progress)

                        # 2. КЛИК (JS Mode)
                        self.driver.execute_script("arguments[0].click();", button)

                        clicks_done += 1
                        consecutive += 1

                        # 3. Человеческие паузы (чтобы не забанили за пулемет)
                        if consecutive > random.randint(40, 70):
                            # Если кликаем долго, делаем паузу
                            pause = random.uniform(2, 4)
                            self.log(tr("log_break", s=pause))
                            time.sleep(pause)
                            consecutive = 0
                        else:
                            # Микро-задержка между кликами
                            time.sleep(random.uniform(0.1, 0.2))

                    except Exception as e:
                        self.log(f"⚠️ Mining glitch: {e}")
                        try:
                            # Если кнопка потерялась, ищем снова
                            button = self.driver.find_element(By.CSS_SELECTOR, CONFIG["selectors"]["mine_btn"])
                        except:
                            break

                self.update_progress(1.0)
                self.log(tr("log_mining_finish"))

                # Проверка улучшений после окончания энергии
                if self.auto_upgrade and self.running:
                    self.perform_upgrade()

        except Exception as e:
            self.log(f"💥 Critical Error: {e}")
        finally:
            if self.driver: self.driver.quit()
            self.running = False
            self.log(tr("log_browser_close"))


# === ОКНО ВХОДА ===
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


# === GUI ===
class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Настройка окна
        self.geometry("800x600")
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.bot = None

        # Загрузка сохраненного языка
        global CURRENT_LANG
        saved_data = DataManager.load_data()
        CURRENT_LANG = saved_data.get("language", "English")

        self._setup_ui()
        self._load_saved_stats()
        self._check_login_state()
        self._update_reset_timer()

        # Применяем язык сразу
        self.refresh_ui_text()

    def _setup_ui(self):
        self.title(tr("app_title"))
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # === ЛЕВАЯ ПАНЕЛЬ ===
        self.sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")

        self.logo = ctk.CTkLabel(self.sidebar, text="MangaBuff\nMiner", font=ctk.CTkFont(size=22, weight="bold"))
        self.logo.grid(row=0, column=0, padx=20, pady=(20, 10))

        # Выбор языка (READONLY FIX)
        self.cmb_lang = ctk.CTkComboBox(self.sidebar, values=["English", "Русский", "Українська"],
                                        command=self.change_language, width=140, state="readonly")
        self.cmb_lang.set(CURRENT_LANG)
        self.cmb_lang.grid(row=1, column=0, pady=(0, 10))

        # Статус аккаунта
        self.lbl_account = ctk.CTkLabel(self.sidebar, text="Guest", text_color="gray")
        self.lbl_account.grid(row=2, column=0)

        self.btn_logout = ctk.CTkButton(self.sidebar, text=tr("btn_logout"), height=24, width=120,
                                        fg_color="#444", font=("Arial", 10), command=self.logout)
        self.btn_logout.grid(row=3, column=0, pady=(0, 20))

        # Таймер
        self.timer_frame = ctk.CTkFrame(self.sidebar, fg_color="#2b2b2b", corner_radius=5)
        self.timer_frame.grid(row=4, column=0, padx=10, pady=5, sticky="ew")
        self.lbl_timer_title = ctk.CTkLabel(self.timer_frame, text=tr("timer_label"), font=("Arial", 10, "bold"),
                                            text_color="gray")
        self.lbl_timer_title.pack(pady=(5, 0))
        self.lbl_timer = ctk.CTkLabel(self.timer_frame, text="00:00:00", font=("Consolas", 18, "bold"),
                                      text_color="#FFAA00")
        self.lbl_timer.pack(pady=(0, 5))

        # Настройки
        self.lbl_settings = ctk.CTkLabel(self.sidebar, text=tr("settings"), anchor="w", text_color="gray",
                                         font=ctk.CTkFont(size=11, weight="bold"))
        self.lbl_settings.grid(row=5, column=0, padx=20, pady=(10, 0), sticky="w")

        self.headless_var = ctk.BooleanVar(value=True)
        self.chk_headless = ctk.CTkSwitch(self.sidebar, text=tr("headless"), variable=self.headless_var)
        self.chk_headless.grid(row=6, column=0, padx=20, pady=(10, 5), sticky="w")

        self.upgrade_var = ctk.BooleanVar(value=True)
        self.chk_upgrade = ctk.CTkSwitch(self.sidebar, text=tr("auto_upgrade"), variable=self.upgrade_var)
        self.chk_upgrade.grid(row=7, column=0, padx=20, pady=(5, 10), sticky="w")

        # Кнопки
        self.lbl_actions = ctk.CTkLabel(self.sidebar, text=tr("controls"), anchor="w", text_color="gray",
                                        font=ctk.CTkFont(size=11, weight="bold"))
        self.lbl_actions.grid(row=8, column=0, padx=20, pady=(20, 0), sticky="w")

        self.btn_start = ctk.CTkButton(self.sidebar, text=tr("btn_start"), height=40,
                                       fg_color="#2CC985", hover_color="#229A65",
                                       command=self.start_bot)
        self.btn_start.grid(row=9, column=0, padx=20, pady=(10, 5))

        self.btn_status = ctk.CTkButton(self.sidebar, text=tr("btn_status"), height=40,
                                        fg_color="#3B8ED0", hover_color="#2D6D9E",
                                        command=self.check_status)
        self.btn_status.grid(row=10, column=0, padx=20, pady=5)

        self.btn_stop = ctk.CTkButton(self.sidebar, text=tr("btn_stop"), height=40,
                                      fg_color="#D94448", hover_color="#A83236", state="disabled",
                                      command=self.stop_bot)
        self.btn_stop.grid(row=11, column=0, padx=20, pady=(5, 10))

        self.progress_bar = ctk.CTkProgressBar(self.sidebar, orientation="horizontal", height=10)
        self.progress_bar.grid(row=12, column=0, padx=20, pady=(30, 10))
        self.progress_bar.set(0)

        # === ПРАВАЯ ПАНЕЛЬ ===
        self.main_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)

        self.stats_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.stats_frame.pack(fill="x", pady=(0, 20))

        self.card_energy = self._create_card(self.stats_frame, tr("card_energy"), "?")
        self.card_energy.pack(side="left", fill="both", expand=True, padx=(0, 10))

        self.card_balance = self._create_card(self.stats_frame, tr("card_balance"), "---")
        self.card_balance.pack(side="left", fill="both", expand=True, padx=5)

        self.card_clicks = self._create_card(self.stats_frame, tr("card_clicks"), "0")
        self.card_clicks.pack(side="left", fill="both", expand=True, padx=(10, 0))

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
        frame.title_label = lbl_title  # Сохраняем ссылку для перевода
        frame.value_label = lbl_value
        return frame

    def change_language(self, new_lang):
        global CURRENT_LANG
        CURRENT_LANG = new_lang
        DataManager.save_data({"language": new_lang})
        self.refresh_ui_text()

    def refresh_ui_text(self):
        """Обновляет все тексты в UI мгновенно"""
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

        # Карточки
        self.card_energy.title_label.configure(text=tr("card_energy"))
        self.card_balance.title_label.configure(text=tr("card_balance"))
        self.card_clicks.title_label.configure(text=tr("card_clicks"))
        self.lbl_log.configure(text=tr("log_title"))

        self._check_login_state()  # Обновит текст "No Account" / "User: ..."

    # --- ЛОГИКА ---
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
        data = DataManager.load_data()
        saved_bal = data.get("last_balance", "---")
        self.card_balance.value_label.configure(text=f"{saved_bal}")

    def logout(self):
        DataManager.clear_credentials()
        self._check_login_state()
        self.log(tr("log_logout"))
        LoginDialog(self, self._check_login_state)

    def prompt_login(self):
        LoginDialog(self, self._check_login_state)

    def update_stats_ui(self, energy=None, balance=None, clicks=None):
        def _update():
            if balance is not None:
                DataManager.save_data({"last_balance": balance})
                self.card_balance.value_label.configure(text=f"{balance:,}")
            if energy is not None: self.card_energy.value_label.configure(text=str(energy))
            if clicks is not None: self.card_clicks.value_label.configure(text=str(clicks))

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
        self.bot.run()
        self.after(0, lambda: self._lock_ui(False))

    def _run_status_thread(self):
        self.bot.check_status_only()
        self.after(0, lambda: self._lock_ui(False))


if __name__ == "__main__":
    app = App()
    app.mainloop()