import os
import json
import logging
import tempfile
import threading
import time
from path import DATA_FILE, SESSION_FILE, READING_STATE_FILE


# Settings are read-modify-written; the desktop app touches them from the UI and
# the bot thread at once, so serialise writes to avoid losing one of them.
_WRITE_LOCK = threading.RLock()

# On Windows a file another thread (get_setting) or an antivirus has open for a moment cannot be
# read for update or replaced: retry briefly instead of failing (or, worse, writing over it).
_RETRIES, _PAUSE = 40, 0.025


def _read_for_update(filename):
    for attempt in range(_RETRIES):
        if not os.path.exists(filename):
            return {}
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}  # a corrupt file is rewritten, as before
        except PermissionError:
            if attempt == _RETRIES - 1:
                raise
            time.sleep(_PAUSE)
    return {}


def _replace_with_retry(temp_name, filename):
    for attempt in range(_RETRIES):
        try:
            os.replace(temp_name, filename)
            return
        except PermissionError:
            if attempt == _RETRIES - 1:
                try:
                    os.remove(temp_name)
                except OSError:
                    pass
                raise
            time.sleep(_PAUSE)


# ==========================================
# DATA MANAGEMENT
# ==========================================
class DataManager:
    @staticmethod
    def load_json(filename):
        try:
            return _read_for_update(filename)
        except OSError:
            return {}

    @staticmethod
    def save_json(filename, data, drop=()):
        """Atomically saves JSON data to prevent file corruption on crash; `drop` removes keys."""
        with _WRITE_LOCK:
            DataManager._save_json_locked(filename, data, drop)

    @staticmethod
    def _save_json_locked(filename, data, drop=()):
        try:
            # Not load_json: its {} on a briefly locked file would overwrite every other setting
            current = _read_for_update(filename)
            current.update(data)
            for key in drop:
                current.pop(key, None)
            dir_name = os.path.dirname(os.path.abspath(filename))
            os.makedirs(dir_name, exist_ok=True)

            # Atomic write via temp file
            with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
                json.dump(current, tf, indent=4, ensure_ascii=False)
                temp_name = tf.name
            _replace_with_retry(temp_name, filename)
        except OSError as e:
            logging.getLogger("buffy").warning("settings not saved to %s: %s", filename, e)

    @staticmethod
    def get_credentials():
        data = DataManager.load_json(DATA_FILE)
        return data.get("email"), data.get("password")

    @staticmethod
    def set_credentials(email, password, login_method=None):
        # login_method "social": no password, the bot lives on cookies from Buffy's sign-in window
        DataManager.save_json(DATA_FILE, {"email": email, "password": password, "login_method": login_method})

    @staticmethod
    def has_login():
        email, password = DataManager.get_credentials()
        return bool(email and (password or DataManager.get_setting("login_method") == "social"))

    # Remembered per account; app preferences (toggles, Telegram, proxy) stay shared
    ACCOUNT_KEYS = ("arena_no_deck", "arena_rank_tracker", "bonus_cards_dry", "bonus_cards_off_date",
                    "club_crystals_date", "club_none_date", "club_slug", "notified_trade_ids",
                    "tower_no_squad", "tower_squad", "tracked_active_trades", "visit_ping_day",
                    "halloween_candy_day", "halloween_event_info", "halloween_watch_state", "account_profile")
    ACCOUNT_FILES = ("reading_state.json", "manual_arena_state.json", "jackpot_cards.json", "arena_management.json")

    @staticmethod
    def switch_account(email):
        """Forget the previous account's state when a different account logs in."""
        previous = DataManager.get_setting("account_email")
        if previous and previous.lower() != email.lower():
            DataManager.save_json(DATA_FILE, {}, drop=DataManager.ACCOUNT_KEYS)
            base = os.path.dirname(os.path.abspath(DATA_FILE))
            for name in DataManager.ACCOUNT_FILES:
                try:
                    os.remove(os.path.join(base, name))
                except OSError:
                    pass
        DataManager.set_setting("account_email", email)

    @staticmethod
    def clear_credentials():
        email = DataManager.get_credentials()[0]
        if email and not DataManager.get_setting("account_email"):
            DataManager.set_setting("account_email", email)  # installs from before switch_account existed
        DataManager.save_json(DATA_FILE, {"email": None, "password": None, "login_method": None},
                              drop=("account_profile",))
        if os.path.exists(SESSION_FILE):
            try:
                os.remove(SESSION_FILE)
            except OSError:
                pass

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

    @staticmethod
    def get_setting(key, default=None):
        data = DataManager.load_json(DATA_FILE)
        return data.get(key, default)

    @staticmethod
    def set_setting(key, value):
        DataManager.save_json(DATA_FILE, {key: value})

    @staticmethod
    def load_reading_state():
        return DataManager.load_json(READING_STATE_FILE)

    @staticmethod
    def save_reading_state(data):
        DataManager.save_json(READING_STATE_FILE, data)