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
        "login_input": 'input[name="email"]',
        "pass_input": 'input[name="password"]',
        "login_btn": ".login-button",
        "mine_element": ".main-mine__header",
        "balance_class": "mine-shop__ore-count",
        "energy_class": "main-mine__game-hits-left"
    }
}

# High-value card tiers we never consume — preserved for future trading
TARGET_RARITIES = ["legendary", "mythic"]
