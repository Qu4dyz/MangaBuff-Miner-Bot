import customtkinter as ctk
from tkinter import messagebox
import os
import datetime
from datetime import timedelta, timezone
import time
import threading

import requests
from data_management import DataManager, DATA_FILE
from languages import tr
from engine import MangaMinerBot
from tg_notifier import send_message, get_notifier
from utils import normalize_proxy_url, mask_proxy_url


# ==========================================
# GUI DIALOGS (CustomTkinter)
# ==========================================
class LoginDialog(ctk.CTkToplevel):
    def __init__(self, parent, callback):
        super().__init__(parent)
        self.callback = callback
        self.title(tr("login_title"))
        self.geometry("320x260")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        ctk.CTkLabel(self, text="MangaBuff Login", font=("Arial", 14, "bold")).pack(pady=15)
        self.entry_email = ctk.CTkEntry(self, placeholder_text="Email")
        self.entry_email.pack(pady=5, padx=20, fill="x")
        self.entry_pass = ctk.CTkEntry(self, placeholder_text="Password", show="*")
        self.entry_pass.pack(pady=5, padx=20, fill="x")

        # Prefill existing credentials if any
        cur_email, cur_pass = DataManager.get_credentials()
        if cur_email:
            self.entry_email.insert(0, cur_email)
        if cur_pass:
            self.entry_pass.insert(0, cur_pass)

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


class TelegramDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Telegram Notifier")
        self.geometry("380x410")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        ctk.CTkLabel(self, text="Настройки Telegram", font=("Arial", 14, "bold")).pack(pady=12)

        notifier = get_notifier()
        token, chat_id, enabled = notifier.get_credentials()

        self.entry_token = ctk.CTkEntry(self, placeholder_text="Bot Token (e.g. 123456:ABC...)")
        self.entry_token.pack(pady=4, padx=20, fill="x")
        if token:
            self.entry_token.insert(0, token)

        self.entry_chat_id = ctk.CTkEntry(self, placeholder_text="Chat ID (e.g. 713822262)")
        self.entry_chat_id.pack(pady=4, padx=20, fill="x")
        if chat_id:
            self.entry_chat_id.insert(0, str(chat_id))

        self.enabled_var = ctk.BooleanVar(value=enabled)
        self.chk_enabled = ctk.CTkSwitch(self, text="Включить уведомления", variable=self.enabled_var)
        self.chk_enabled.pack(pady=6, padx=20, anchor="w")

        silent_all = notifier.is_silent_all_enabled()
        self.silent_var = ctk.BooleanVar(value=silent_all)
        self.chk_silent = ctk.CTkSwitch(self, text="🔕 Тихий режим (всегда без звука)", variable=self.silent_var)
        self.chk_silent.pack(pady=6, padx=20, anchor="w")

        night_mode = DataManager.get_setting("tg_night_mode")
        if night_mode is None:
            night_mode = os.getenv("TG_NIGHT_MODE", "1").lower() in ("1", "true", "yes")
        self.night_var = ctk.BooleanVar(value=bool(night_mode))
        self.chk_night = ctk.CTkSwitch(self, text="🌙 Ночной режим (23:00 - 08:00 МСК)", variable=self.night_var)
        self.chk_night.pack(pady=6, padx=20, anchor="w")

        btn_box = ctk.CTkFrame(self, fg_color="transparent")
        btn_box.pack(pady=12, padx=20, fill="x")

        ctk.CTkButton(btn_box, text="🔔 Тест", width=100, fg_color="#3B8ED0", command=self.on_test).pack(side="left", padx=5)
        ctk.CTkButton(btn_box, text="💾 Сохранить", width=140, fg_color="#2CC985", command=self.on_save).pack(side="right", padx=5)

    def on_test(self):
        token = self.entry_token.get().strip()
        chat_id = self.entry_chat_id.get().strip()
        if not token or not chat_id:
            messagebox.showwarning("Telegram", "Заполните Token и Chat ID перед тестированием!")
            return

        DataManager.set_setting("tg_token", token)
        DataManager.set_setting("tg_chat_id", chat_id)
        DataManager.set_setting("tg_enabled", True)
        DataManager.set_setting("tg_silent_all", self.silent_var.get())
        DataManager.set_setting("tg_night_mode", self.night_var.get())

        ok, err = send_message("🔔 <b>MangaBuff Miner</b>: Тестовое уведомление успешно доставлено!", wait=True)
        if ok:
            messagebox.showinfo("Telegram", "Сообщение успешно отправлено в Telegram!")
        else:
            messagebox.showerror("Telegram Ошибка", f"Не удалось отправить сообщение:\n{err}")

    def on_save(self):
        token = self.entry_token.get().strip()
        chat_id = self.entry_chat_id.get().strip()
        enabled = self.enabled_var.get()

        DataManager.set_setting("tg_token", token)
        DataManager.set_setting("tg_chat_id", chat_id)
        DataManager.set_setting("tg_enabled", enabled)
        DataManager.set_setting("tg_silent_all", self.silent_var.get())
        DataManager.set_setting("tg_night_mode", self.night_var.get())
        self.destroy()


class ProxyDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Proxy Settings")
        self.geometry("420x310")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        ctk.CTkLabel(self, text="🌐 Настройки Прокси", font=("Arial", 14, "bold")).pack(pady=15)

        cur_proxy = DataManager.get_setting("proxy") or os.getenv("MANGABUFF_PROXY") or ""

        self.entry_proxy = ctk.CTkEntry(
            self, placeholder_text="ip:port:user:pass или http://user:pass@ip:port", width=360
        )
        self.entry_proxy.pack(pady=10, padx=20)
        if cur_proxy:
            self.entry_proxy.insert(0, cur_proxy)

        hint = (
            "Поддерживаемые форматы:\n"
            "• ip:port:user:password\n"
            "• http://user:password@ip:port\n"
            "• socks5://127.0.0.1:40000"
        )
        ctk.CTkLabel(self, text=hint, font=("Arial", 11), text_color="gray", justify="left").pack(pady=5, padx=25, anchor="w")

        btn_box = ctk.CTkFrame(self, fg_color="transparent")
        btn_box.pack(pady=15, padx=20, fill="x")

        ctk.CTkButton(btn_box, text="⚡ Проверить", width=110, fg_color="#3B8ED0", command=self.on_test).pack(side="left", padx=5)
        ctk.CTkButton(btn_box, text="🗑️ Очистить", width=100, fg_color="#D94448", command=self.on_clear).pack(side="left", padx=5)
        ctk.CTkButton(btn_box, text="💾 Сохранить", width=120, fg_color="#2CC985", command=self.on_save).pack(side="right", padx=5)

    def on_test(self):
        raw = self.entry_proxy.get().strip()
        if not raw:
            messagebox.showwarning("Прокси", "Введите адрес прокси для проверки!")
            return
        norm = normalize_proxy_url(raw)
        if not norm:
            messagebox.showerror("Прокси", "Неверный формат адреса прокси!")
            return

        def _do_test():
            try:
                res = requests.get(
                    "https://mangabuff.ru",
                    proxies={"http": norm, "https": norm},
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                    timeout=10
                )
                if res.status_code == 200:
                    self.after(0, lambda: messagebox.showinfo(
                        "Прокси",
                        f"✅ Успешное подключение к MangaBuff!\nКод: 200 OK\nЗащита: {res.headers.get('server', 'unknown')}"
                    ))
                else:
                    self.after(0, lambda: messagebox.showwarning(
                        "Прокси",
                        f"⚠️ Сервер вернул код {res.status_code}\n(DDoS-Guard может блокировать данный IP)"
                    ))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Ошибка", f"❌ Ошибка соединения:\n{e}"))

        threading.Thread(target=_do_test, daemon=True).start()

    def on_save(self):
        raw = self.entry_proxy.get().strip()
        DataManager.set_setting("proxy", raw if raw else None)
        messagebox.showinfo("Прокси", "Настройки прокси сохранены!")
        self.destroy()

    def on_clear(self):
        self.entry_proxy.delete(0, "end")
        DataManager.set_setting("proxy", None)
        messagebox.showinfo("Прокси", "Прокси очищен (будет использоваться прямое соединение).")


# ==========================================
# GUI MAIN APPLICATION CLASS
# ==========================================
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.geometry("860x620")
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

        self.sidebar = ctk.CTkFrame(self, width=230, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")

        self.logo = ctk.CTkLabel(self.sidebar, text="MangaBuff\nMiner", font=ctk.CTkFont(size=22, weight="bold"))
        self.logo.grid(row=0, column=0, padx=20, pady=(20, 10))

        self.cmb_lang = ctk.CTkComboBox(
            self.sidebar, values=["English", "Русский", "Українська"],
            command=self.change_language, width=140, state="readonly"
        )
        self.cmb_lang.set(CURRENT_LANG)
        self.cmb_lang.grid(row=1, column=0, pady=(0, 10))

        self.lbl_account = ctk.CTkLabel(self.sidebar, text="Guest", text_color="gray")
        self.lbl_account.grid(row=2, column=0)

        self.btn_logout = ctk.CTkButton(
            self.sidebar, text=tr("btn_logout"), height=24, width=120, fg_color="#444",
            font=("Arial", 10), command=self.logout
        )
        self.btn_logout.grid(row=3, column=0, pady=(0, 15))

        self.timer_frame = ctk.CTkFrame(self.sidebar, fg_color="#2b2b2b", corner_radius=5)
        self.timer_frame.grid(row=4, column=0, padx=10, pady=5, sticky="ew")
        self.lbl_timer_title = ctk.CTkLabel(
            self.timer_frame, text=tr("timer_label"), font=("Arial", 10, "bold"), text_color="gray"
        )
        self.lbl_timer_title.pack(pady=(5, 0))
        self.lbl_timer = ctk.CTkLabel(
            self.timer_frame, text="00:00:00", font=("Consolas", 18, "bold"), text_color="#FFAA00"
        )
        self.lbl_timer.pack(pady=(0, 5))

        self.lbl_settings = ctk.CTkLabel(
            self.sidebar, text=tr("settings"), anchor="w", text_color="gray",
            font=ctk.CTkFont(size=11, weight="bold")
        )
        self.lbl_settings.grid(row=5, column=0, padx=20, pady=(10, 0), sticky="w")

        # Headless mode toggle
        self.headless_var = ctk.BooleanVar(value=True)
        self.chk_headless = ctk.CTkSwitch(self.sidebar, text=tr("headless"), variable=self.headless_var)
        self.chk_headless.grid(row=6, column=0, padx=20, pady=(5, 5), sticky="w")

        # Auto-upgrade pickaxe toggle
        is_auto_upg = DataManager.get_setting("auto_upgrade", True)
        self.auto_upgrade_var = ctk.BooleanVar(value=is_auto_upg)
        self.chk_auto_upgrade = ctk.CTkSwitch(
            self.sidebar, text=tr("auto_upgrade"), variable=self.auto_upgrade_var, command=self._toggle_auto_upgrade
        )
        self.chk_auto_upgrade.grid(row=7, column=0, padx=20, pady=(0, 5), sticky="w")

        # Abyss Tower expeditions toggle
        is_tower = DataManager.get_setting("tower_enabled", True)
        self.tower_var = ctk.BooleanVar(value=is_tower)
        self.chk_tower = ctk.CTkSwitch(
            self.sidebar, text=tr("tower_enabled"), variable=self.tower_var, command=self._toggle_tower
        )
        self.chk_tower.grid(row=8, column=0, padx=20, pady=(0, 5), sticky="w")

        # Daily Ads toggle (3x7 diamonds)
        is_ads = DataManager.get_setting("ads_enabled", True)
        self.ads_var = ctk.BooleanVar(value=is_ads)
        self.chk_ads = ctk.CTkSwitch(
            self.sidebar, text=tr("ads_enabled"), variable=self.ads_var, command=self._toggle_ads
        )
        self.chk_ads.grid(row=9, column=0, padx=20, pady=(0, 5), sticky="w")

        # Manga reading toggle (bonus cards & 75-chapter quest)
        is_reading = DataManager.get_setting("reading_enabled", True)
        self.reading_var = ctk.BooleanVar(value=is_reading)
        self.chk_reading = ctk.CTkSwitch(
            self.sidebar, text=tr("reading_enabled"), variable=self.reading_var, command=self._toggle_reading
        )
        self.chk_reading.grid(row=10, column=0, padx=20, pady=(0, 5), sticky="w")

        # Stop reading on card cooldown toggle
        is_wait_cd = DataManager.get_setting("reading_wait_card_cooldown", False)
        self.card_cd_var = ctk.BooleanVar(value=is_wait_cd)
        self.chk_card_cd = ctk.CTkSwitch(
            self.sidebar, text=tr("reading_wait_card_cd"), variable=self.card_cd_var, command=self._toggle_card_cd
        )
        self.chk_card_cd.grid(row=11, column=0, padx=20, pady=(0, 5), sticky="w")

        # Telegram Settings Button
        self.btn_tg_settings = ctk.CTkButton(
            self.sidebar, text="📲 Telegram Bot", height=26, width=160, fg_color="#333",
            hover_color="#555", font=("Arial", 11), command=self._open_telegram_dialog
        )
        self.btn_tg_settings.grid(row=12, column=0, padx=20, pady=(5, 3))

        # Proxy Settings Button
        self.btn_proxy_settings = ctk.CTkButton(
            self.sidebar, text="🌐 Настройки Прокси", height=26, width=160, fg_color="#333",
            hover_color="#555", font=("Arial", 11), command=self._open_proxy_dialog
        )
        self.btn_proxy_settings.grid(row=13, column=0, padx=20, pady=(3, 10))

        self.lbl_actions = ctk.CTkLabel(
            self.sidebar, text=tr("controls"), anchor="w", text_color="gray",
            font=ctk.CTkFont(size=11, weight="bold")
        )
        self.lbl_actions.grid(row=14, column=0, padx=20, pady=(10, 0), sticky="w")

        self.btn_start = ctk.CTkButton(
            self.sidebar, text=tr("btn_start"), height=40, fg_color="#2CC985",
            hover_color="#229A65", command=self.start_bot
        )
        self.btn_start.grid(row=15, column=0, padx=20, pady=(8, 4))

        self.btn_status = ctk.CTkButton(
            self.sidebar, text=tr("btn_status"), height=40, fg_color="#3B8ED0",
            hover_color="#2D6D9E", command=self.check_status
        )
        self.btn_status.grid(row=16, column=0, padx=20, pady=4)

        self.btn_stop = ctk.CTkButton(
            self.sidebar, text=tr("btn_stop"), height=40, fg_color="#D94448",
            hover_color="#A83236", state="disabled", command=self.stop_bot
        )
        self.btn_stop.grid(row=17, column=0, padx=20, pady=(4, 10))

        self.progress_bar = ctk.CTkProgressBar(self.sidebar, orientation="horizontal", height=10)
        self.progress_bar.grid(row=18, column=0, padx=20, pady=(15, 10))
        self.progress_bar.set(0)

        # Main frame
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

    def _toggle_auto_upgrade(self):
        val = self.auto_upgrade_var.get()
        DataManager.set_setting("auto_upgrade", val)

    def _toggle_tower(self):
        val = self.tower_var.get()
        DataManager.set_setting("tower_enabled", val)

    def _toggle_ads(self):
        val = self.ads_var.get()
        DataManager.set_setting("ads_enabled", val)

    def _toggle_reading(self):
        val = self.reading_var.get()
        DataManager.set_setting("reading_enabled", val)

    def _toggle_card_cd(self):
        val = self.card_cd_var.get()
        DataManager.set_setting("reading_wait_card_cooldown", val)

    def _open_telegram_dialog(self):
        TelegramDialog(self)

    def _open_proxy_dialog(self):
        ProxyDialog(self)

    def change_language(self, new_lang):
        global CURRENT_LANG
        CURRENT_LANG = new_lang
        DataManager.save_json(DATA_FILE, {"language": new_lang})
        self.refresh_ui_text()

    def refresh_ui_text(self):
        self.title(tr("app_title"))
        self.lbl_settings.configure(text=tr("settings"))
        self.chk_headless.configure(text=tr("headless"))
        self.chk_auto_upgrade.configure(text=tr("auto_upgrade"))
        self.chk_tower.configure(text=tr("tower_enabled"))
        self.chk_ads.configure(text=tr("ads_enabled"))
        self.chk_reading.configure(text=tr("reading_enabled"))
        self.chk_card_cd.configure(text=tr("reading_wait_card_cd"))
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
            if energy is not None:
                self.card_energy.value_label.configure(text=str(energy))

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
        if self.bot and self.bot.running:
            return
        email, pwd = DataManager.get_credentials()
        if not email:
            self.prompt_login()
            return
        self._lock_ui(True)
        self._init_bot()
        self.log(tr("log_init"))
        threading.Thread(target=self._run_mining_thread, daemon=True).start()

    def check_status(self):
        if self.bot and self.bot.running:
            return
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