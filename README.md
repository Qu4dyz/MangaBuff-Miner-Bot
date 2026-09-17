[![ru](https://img.shields.io/badge/lang-ru-red.svg)](README.ru.md) [![en](https://img.shields.io/badge/lang-en-green.svg)](README.md)

# 🤖 MangaBuff Miner Bot

**MangaBuff Miner** is a Python automation tool for the MangaBuff mining game and battle system. It uses pure HTTP requests (no browser automation) to log in, claim daily rewards, complete daily quests, farm battles for essence, and mine ore.

![Status](https://img.shields.io/badge/Status-Active%20Development-yellow)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Mode](https://img.shields.io/badge/Mode-CLI%20%26%20GUI-orange)

## ✨ Actual Features

* **🔐 API-based Authentication:** Logs in via Laravel backend using email/password, extracts CSRF token from login page, maintains session via cookies (cached to `session_cache.json`).
* **🎁 Daily Reward Claiming:** Parses `/balance` page for the active "Claim" button and posts to the dynamic claim endpoint.
* **📺 Daily Rewarded Ads (3x7 💎):** Automatically watches up to 3 rewarded ads per day via `POST /balance/ads` (+21 diamonds daily) respecting server video duration cooldowns (~18–22s).
* **📖 Manga Chapter Reading (`/addHistory`):** Sequentially reads manga chapters with humanized delays (10–18s) to farm up to 10 bonus cards/day, sharpening scrolls, and progress the 75-chapter daily quest. Saves read history in `reading_state.json`.
* **🏰 Abyss Tower Expeditions (`/tower`):** Automatically checks active expeditions. Claims completed rewards (diamonds + dark crystals + card levels), delivers Telegram report, and restarts a fresh 12h Nightmare expedition with your saved squad.
* **⚔️ Daily Quest Claiming:** Scans `/battle` page for completed-but-unclaimed daily quests; claims essence, diamonds, and scrolls safely via API.
* **⚔️ Battle Farming Loop:** Starts battles via `/battle/fight/start`, follows redirect to result page, extracts earned essence. Stops cleanly at daily essence cap (1000/day detected via 3 consecutive 0-essence battles).
* **⛏️ Ore Mining & Auto-Upgrade:** Hits `/mine/hit` API endpoint, auto-checks and buys pickaxe upgrades and Strong Hit from the shop.
* **💾 Session Persistence:** Saves cookies, CSRF token, and User-Agent to `session_cache.json`; reuses until HTTP 419 triggers automatic re-login.
* **🔐 Credential Storage:** Saves email/password locally in `user_data.json` (plaintext — local use only).
* **🛡️ Anti-Ban Humanization:** Randomized delays between actions (4.5–12.2s), between battles (10–20s), micro-breaks (35–75s every 10–15 battles).
* **🃏 High-Value Card Preservation:** Detects legendary/mythic card drops in battle results and saves them to `jackpot_cards.json` (protected from consumption).
* **🌍 Multi-language Support:** Full translations for English, Russian, and Ukrainian.
* **🖥️ GUI & CLI Modes:** CustomTkinter GUI or streamlined CLI mode with `--status` and `--gui` flags.
* **📲 Telegram Notifications:** Asynchronous non-blocking worker delivering structured HTML event summaries and alerts.

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
4. Execution Options:
   - **Default Execution (CLI):**
     ```bash
     python main.py
     ```
   - **Quick Status Check (no actions performed):**
     ```bash
     python main.py --status
     ```
   - **Graphical Interface (GUI):**
     ```bash
     python main.py --gui
     ```

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

## ⚠️ Status & Solved Limitations

| Area | Status | Resolution |
|------|--------|------------|
| **Quest Claim Payload** | ✅ **Resolved** | Exact form-encoded payload `user_daily_quest_id` reverse-engineered from `manga.js`. Verified working with `HTTP 200`. |
| **Pickaxe Auto-Upgrade** | ✅ **Implemented** | Connected `/mine/upgrade` and `/mine/buy-strong-hit` endpoints with automatic purchase logic when enough ore is mined. |
| **Telegram Notifier** | ✅ **Revamped** | Asynchronous worker queue, rate-limit protection, zero spam, beautiful HTML summaries for milestones, instant Jackpot alerts. |
| **Jackpot Card Preservation** | ✅ **Activated** | High-value card drops (`legendary`, `mythic`) detected during battles, preserved into `jackpot_cards.json`, and alerted via TG. |
| **Daily Cap Detection** | ✅ **Fixed** | Exits only after 3 consecutive 0-essence battles. Single losses no longer prematurely stop the farm. |
| **Cloudflare Turnstile** | 🛡️ **Guarded** | Captcha challenge detector with immediate Telegram alert and safe bot pause. |

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
