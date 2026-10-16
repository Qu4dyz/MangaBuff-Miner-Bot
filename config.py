import os
import sys


# True inside a packaged build: PyInstaller sets sys.frozen, Nuitka defines __compiled__
IS_FROZEN = bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def get_base_path():
    """Folder for settings, session and state files.

    Script runs (VPS/systemd) keep everything next to the code. The packaged
    desktop app runs from a temp extraction dir, so it stores data in
    %APPDATA%\\Buffy instead (override with BUFFY_HOME).
    """
    override = os.getenv("BUFFY_HOME")
    if override:
        return override
    if IS_FROZEN:
        root = os.getenv("APPDATA") or os.path.expanduser("~")
        return os.path.join(root, "Buffy")
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_path()

# ==========================================
# CONFIGURATION & SELECTORS
# ==========================================
CONFIG = {
    "urls": {
        "login": "https://mangabuff.ru/login",
        "game": "https://mangabuff.ru/mine"
    },
    "selectors": {
        "balance_class": "mine-shop__ore-count",
        "energy_class": "main-mine__game-hits-left"
    }
}

# High-value card tiers we never consume — preserved for future trading
TARGET_RARITIES = ["legendary", "mythic"]
