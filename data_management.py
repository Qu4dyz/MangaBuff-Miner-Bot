import os
import json
import tempfile
import time
from path import DATA_FILE, SESSION_FILE, READING_STATE_FILE


# ==========================================
# DATA MANAGEMENT
# ==========================================
class DataManager:
    @staticmethod
    def load_json(filename):
        if not os.path.exists(filename):
            return {}
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    @staticmethod
    def save_json(filename, data):
        """Atomically saves JSON data to prevent file corruption on crash."""
        try:
            current = DataManager.load_json(filename)
            current.update(data)
            dir_name = os.path.dirname(os.path.abspath(filename))
            os.makedirs(dir_name, exist_ok=True)
            
            # Atomic write via temp file
            with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
                json.dump(current, tf, indent=4, ensure_ascii=False)
                temp_name = tf.name
                
            os.replace(temp_name, filename)
        except OSError:
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