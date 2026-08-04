import os
import json
from path import DATA_FILE, SESSION_FILE
import time

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