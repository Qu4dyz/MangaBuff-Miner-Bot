import os
import sys
from config import CONFIG

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
READING_STATE_FILE = os.path.join(BASE_DIR, "reading_state.json")
