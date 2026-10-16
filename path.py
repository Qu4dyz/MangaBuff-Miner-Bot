import os

from config import BASE_DIR

os.makedirs(BASE_DIR, exist_ok=True)

DATA_FILE = os.path.join(BASE_DIR, "user_data.json")
SESSION_FILE = os.path.join(BASE_DIR, "session_cache.json")
READING_STATE_FILE = os.path.join(BASE_DIR, "reading_state.json")
