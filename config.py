import os


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

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
