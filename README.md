[![ru](https://img.shields.io/badge/lang-ru-red.svg)](README.ru.md) [![en](https://img.shields.io/badge/lang-en-green.svg)](README.md)

# 🤖 MangaBuff Miner Bot

**MangaBuff Miner** is a Python automation tool for the MangaBuff mining game and battle system. It uses pure HTTP requests (no browser automation) to log in, claim daily rewards, complete daily quests, farm battles for essence, and mine ore.

![Status](https://img.shields.io/badge/Status-Active%20Development-yellow)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Mode](https://img.shields.io/badge/Mode-CLI%20%26%20GUI-orange)

## ✨ Actual Features

* **🔐 API-based Authentication:** Logs in via Laravel backend using email/password, extracts CSRF token from login page, maintains session via cookies (cached to `session_cache.json`).
* **🎁 Daily Reward Claiming:** Parses `/balance` page for the active "Claim" button and posts to the dynamic claim endpoint.
* **⚔️ Daily Quest Claiming:** Single-pass scan of `/battle` page for completed-but-unclaimed daily quests; claims them via API.
* **⚔️ Battle Farming Loop:** Starts battles via `/battle/fight/start`, follows redirect to result page, extracts earned essence via regex from embedded JSON. Stops at daily essence cap (1000/day detected via 3 consecutive 0-essence battles).
* **⛏️ Ore Mining & Auto-Upgrade:** Hits `/mine/hit` API endpoint, auto-checks and buys pickaxe upgrades from the shop.
* **💾 Session Persistence:** Saves cookies, CSRF token, and User-Agent to `session_cache.json`; reuses until HTTP 419 (expired) triggers auto-re-login.
* **🔐 Credential Storage:** Saves email/password locally in `user_data.json` (plaintext — local use only).
* **🛡️ Anti-Ban Humanization:** Randomized delays between actions (4.5–12.2s), between battles (10–20s), micro-breaks (45–120s every 10–15 battles).
* **🃏 High-Value Card Preservation:** Detects legendary/mythic card drops in battle results and saves them to `jackpot_cards.json` (never consumed).
* **🌍 Multi-language Support (Code-Level):** Translations for English, Russian, Ukrainian exist in code.
* **🖥️ GUI (Optional):** CustomTkinter GUI available — launch with `USE_CLI_MODE = False` in `main.py`.
* **📲 Telegram Notifications:** Sends log messages to a Telegram bot (configured via `.env`).

## 🚀 How to Run (From Source)

1. Install Python 3.10+.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Set up your `.env` file with Telegram credentials (optional but recommended):
   ```
   TG_TOKEN=your_telegram_bot_token
   TG_CHAT_ID=your_chat_id
   ```
4. Run the script:
   ```bash
   python main.py
   ```
   - On first run, it will prompt for your MangaBuff email and password (saved to `user_data.json`).
   - By default (`USE_CLI_MODE = True`), the script runs in CLI mode: logs in (or restores session), claims daily reward, claims daily quests, mines ore, upgrades pickaxe, then enters the battle farming loop until daily essence cap or energy depletion.
   - Set `USE_CLI_MODE = False` in `main.py` to launch the CustomTkinter GUI instead.

## 🛠 Tech Stack

* **HTTP:** `requests` + `requests.Session` (persistent cookie jar + headers)
* **HTML Parsing:** `BeautifulSoup4` + `regex` (for embedded JSON in battle result pages)
* **GUI:** `CustomTkinter` — optional graphical interface
* **Notifications:** `python-telegram-bot` style HTTP API calls via `tg_notifier.py`
* **Config:** `user_data.json` (credentials), `session_cache.json` (session), `jackpot_cards.json` (preserved cards)

## 📁 Key Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point with CLI/GUI switch (`USE_CLI_MODE`) |
| `engine.py` | `MangaMinerBot` class — full farm logic (login, mining, battles) |
| `gui_app.py` | `App` class — CustomTkinter GUI; also contains `run_cli_bot()` |
| `config.py` | `CONFIG` dict (URLs, CSS selectors), `TARGET_RARITIES` |
| `path.py` | `BASE_DIR`, `DATA_FILE`, `SESSION_FILE` path constants |
| `tg_notifier.py` | `send_message()` — Telegram bot notification function |
| `data_management.py` | `DataManager` — credentials/session persistence |
| `requirements.txt` | Python dependencies |
| `user_data.json` | Stores email, password, language (created on first run) |
| `session_cache.json` | Cached session cookies, CSRF token, User-Agent |
| `jackpot_cards.json` | Preserved legendary/mythic card drops |

## ⚙️ Configuration

Edit the `CONFIG` dict in `config.py` to adjust:
- URLs (`login`, `game`, `mine`)
- CSS selectors for parsing mine page (balance, energy, upgrade cost)
- `TARGET_RARITIES` in `config.py` — card tiers to preserve (default: `["legendary", "mythic"]`)

## ⚠️ Known Limitations

| Area | Status |
|------|--------|
| **Quest Claim Payload** | Sends multiple candidate field names (`user_daily_quest_id`, `id`, `quest_id`, `daily_quest_id`); exact field name not confirmed from Network tab |
| **Essence JSON Key** | Battle result essence extracted via regex from embedded JSON; key name guessed (`essence`, `essence_gained`, `reward`, `gain`, `added`, `essence_added`) |
| **Deck Detection** | Heuristic text search for "empty deck" phrases; fragile to UI text changes |
| **Daily Cap Detection** | Stops after 3 consecutive 0-essence battles; may false-stop on new/empty decks |

## 📋 Requirements

```
customtkinter==5.2.2
requests==2.32.5
beautifulsoup4==4.12.3
packaging
urllib3
```

## 📄 Disclaimer

*This software is for educational purposes only. Use at your own risk. Automation may violate MangaBuff's Terms of Service.*
