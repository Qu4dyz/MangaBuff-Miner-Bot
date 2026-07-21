[![ru](https://img.shields.io/badge/lang-ru-red.svg)](README.ru.md) [![en](https://img.shields.io/badge/lang-en-green.svg)](README.md)

# 🤖 MangaBuff Miner Bot

**MangaBuff Miner** is a Python automation tool for the MangaBuff mining game and battle system. It uses pure HTTP requests (no browser automation) to log in, claim daily rewards, complete daily quests, farm battles for essence, and mine ore.

![Status](https://img.shields.io/badge/Status-Active%20Development-yellow)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Mode](https://img.shields.io/badge/Mode-CLI%20Test%20Mode-orange)

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
* **🌍 Multi-language Support (Code-Level):** Translations for English, Russian, Ukrainian exist in code (GUI currently disabled).
* **🖥️ GUI (Disabled):** CustomTkinter GUI exists in code but is **currently disabled** — the script runs in CLI test mode via `__main__` block.

## 🚀 How to Run (From Source)

1. Install Python 3.10+.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the script:
   ```bash
   python main.py
   ```
   - On first run, it will prompt for your MangaBuff email and password (saved to `user_data.json`).
   - The script runs in **CLI test mode**: logs in (or restores session), claims daily reward, claims daily quests, mines ore, upgrades pickaxe, then enters the battle farming loop until daily essence cap or energy depletion.

## 🛠 Tech Stack

* **HTTP:** `requests` + `requests.Session` (persistent cookie jar + headers)
* **HTML Parsing:** `BeautifulSoup4` + `regex` (for embedded JSON in battle result pages)
* **GUI (Disabled):** `CustomTkinter` — code present but commented out in `__main__`
* **Config:** `user_data.json` (credentials), `session_cache.json` (session), `jackpot_cards.json` (preserved cards)

## 📁 Key Files

| File | Purpose |
|------|---------|
| `main.py` | Main entry point, `MangaMinerBot` class, GUI classes (disabled) |
| `requirements.txt` | Python dependencies |
| `user_data.json` | Stores email, password, language (created on first run) |
| `session_cache.json` | Cached session cookies, CSRF token, User-Agent |
| `jackpot_cards.json` | Preserved legendary/mythic card drops |

## ⚙️ Configuration

Edit the `CONFIG` dict in `main.py` to adjust:
- URLs (`login`, `game`, `mine`)
- CSS selectors for parsing mine page (balance, energy, upgrade cost)
- `TARGET_RARITIES` — card tiers to preserve (default: `["legendary", "mythic"]`)

## ⚠️ Known Limitations

| Area | Status |
|------|--------|
| **GUI** | Disabled — runs in CLI mode only |
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