import customtkinter as ctk
from tkinter import messagebox
from bs4 import BeautifulSoup
from data_management import DataManager, DATA_FILE
from languages import tr
import datetime
from datetime import timedelta, timezone
import time
from engine import MangaMinerBot
import threading
from tg_notifier import send_message

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

# ==========================================
# GUI MAIN APPLICATION CLASS
# ==========================================
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


def run_cli_bot():

    def _cli_log(msg):
        print(f"[BOT] {msg}")
        send_message(f"[BOT] {msg}")

    print("Starting MangaBuff Miner in CLI mode...")

    bot = MangaMinerBot(
        log_callback=_cli_log,
        progress_callback=lambda p: None,
        stats_callback=lambda **kw: None,
        headless=True
    )
    bot.run()