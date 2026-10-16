[![ru](https://img.shields.io/badge/lang-ru-red.svg)](README.ru.md) [![en](https://img.shields.io/badge/lang-en-green.svg)](README.md)

# 🤖 MangaBuff Miner Bot

**MangaBuff Miner** is a high-performance, fully autonomous automation daemon and desktop client for the MangaBuff mining game, card collection, and battle systems. Powered by direct HTTP API interaction (no browser overhead), it features advanced combat heuristics, automatic card progression, resilient network sessions, and rich Telegram notifications.

![Status](https://img.shields.io/badge/Status-Active%20Production-brightgreen)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Mode](https://img.shields.io/badge/Mode-CLI%20%7C%20Desktop%20App%20%7C%2024%2F7%20Daemon-orange)

---

## 💜 Buffy — desktop app for Windows

Don't want to touch Python? Grab **Buffy-Setup.exe** (or the portable zip) from [Releases](https://github.com/Qu4dyz/MangaBuff-Miner-Bot/releases/latest), run it, and the first-launch wizard walks you through login, modules and Telegram. Buffy runs the same 24/7 daemon as the server version, shows live stats (cards, chapters, diamonds, tower, arena rank), keeps a full activity log, and hides to the system tray when closed. Settings live in `%APPDATA%\Buffy`.

> Windows may show "Windows protected your PC" for a new unsigned app — click **More info → Run anyway**.

---

## ✨ Features

### ⚔️ Arena & Combat Intelligence (`arena.py`)
* **Autonomous Arena Battles:** Automated matchmaking, target evaluation, and battle execution.
* **Combat Effectiveness Engine:** Calculates true combat value of cards taking into account stats, buffs, card synergies, and win probabilities.
* **Opponent Scouting & Optimization:** Analyzes defense logs and opponent compositions to select highest-yield, high-confidence targets.
* **Card Progression & Protection Policy:**
  * Automated card awakening and resonance matching.
  * Intelligent lock system that prevents sacrificing or consuming valuable/legendary cards.
* **Evening Cup Automation:** Automated participation in the Evening Cup (19:00–22:00 MSK) with dynamic turn strategy and tactic activation.

### 🏛️ Club System (`club.py`)
* **Personal Crystal Contributions:** Automatically contributes crystals to the club (up to 10/10) to earn club exp and contribution points.
* **Card Request Alerts:** Monitors club card donation requests and alerts you via Telegram with direct links and card art when requested cards are in your collection.

### 🏰 Abyss Tower (Башня Разлома)
* **Continuous Expedition Cycle:** Automatically claims expedition rewards (diamonds, dark crystals, card levels).
* **Zero-Downtime Redeployment:** Instantly re-dispatches the squad on the optimal expedition immediately upon completion.
* **HQ Upgrades:** Automatically invests earned resources into Tower Headquarters upgrades.

### 📖 Manga Chapter Reading (`/addHistory`)
* **Daily Bonus Card Farming:** Sequentially processes manga chapters with natural randomized delays (10–18s) to collect up to 10 daily bonus cards and sharpening scrolls.
* **Daily Quest Integration:** Automatically completes the 75-chapter daily quest milestone.
* **State Tracking:** Tracks read history in `reading_state.json` and avoids wasteful requests once daily card drops are exhausted.

### ⛏️ Mining, Quests & Daily Routine
* **Ore Mining & Auto-Upgrades:** Sends mining hits (`/mine/hit`), purchases pickaxe upgrades and Strong Hit as soon as sufficient ore is collected.
* **Daily Rewarded Ads (3x7 💎):** Watches all 3 daily ads via `POST /balance/ads` (+21 diamonds daily) with humanized watch durations.
* **Daily Rewards & Quests:** Scans and claims daily login gifts, chest rewards, and completed battle quests.

### 🛡️ Network Robustness & Session Persistence
* **Resilient HTTP Layer (`http_session.py`):** Hybrid session engine supporting `curl_cffi` browser TLS impersonation with fallback to standard `requests`.
* **DDoS-Guard / Cloudflare Handling:** Auto-recovers cookies, deduplicates DDoS-Guard session tokens, and keeps valid session credentials in `session_cache.json`.
* **Anti-Ban Humanization:** Randomized timing jitters, micro-breaks between battles, and realistic user simulation.

### 📲 Smart Telegram Notifier (`tg_notifier.py`)
* **Visual Card Art Drops:** Sends card drop notifications with actual image attachments (WebP, PNG, GIF detection via magic bytes).
* **Trade Alerts:** Instant trade notifications with direct action links.
* **Quiet Hours & Alert Escalation:** Configurable night mode with forced loud alerts if manual captcha intervention is needed.
* **Cycle Heartbeats:** Compact periodic status summaries reporting energy, diamonds, card counts, and upcoming actions.

---

## 🕹️ Modes of Operation

| Mode | Command | Description |
|------|---------|-------------|
| **Smart Daemon (24/7)** | `python main.py --daemon` | Headless, continuous 24/7 daemon designed for VPS/servers. Automatically calculates sleep intervals until the earliest pending event (tower return, chest unlock, energy refill, midnight reset). |
| **Standard CLI** | `python main.py` | Runs a complete farm and maintenance cycle once, outputs status, and exits cleanly. |
| **Status Check** | `python main.py --status` | Fast, read-only health check of current energy, balance, and account state without performing actions. |
| **Buffy desktop app** | `python main.py --app` | Windows app with onboarding wizard, live dashboard, module toggles, Telegram setup, tray icon and autostart. Requires `requirements-desktop.txt`. |

---

## 🚀 Quick Start

### 1. Requirements
* Python 3.10 or higher
* Git

### 2. Installation
```bash
git clone https://github.com/Qu4dyz/MangaBuff-Miner-Bot.git
cd MangaBuff-Miner-Bot
python -m venv venv
# Linux / macOS:
source venv/bin/activate
# Windows:
venv\Scripts\activate

pip install -r requirements.txt
```

### 3. Configuration
Create a `.env` file in the project root:
```env
# MangaBuff Credentials (optional if entered via GUI/CLI prompt)
MB_EMAIL=your_email@example.com
MB_PASSWORD=your_password

# Telegram Notifications (optional but recommended)
TG_TOKEN=your_bot_token
TG_CHAT_ID=your_telegram_chat_id
```

### 4. Running as a Linux Systemd Service (VPS 24/7)
To run the bot continuously in the background on a Linux VPS:
```ini
# /etc/systemd/system/mangabuff.service
[Unit]
Description=MangaBuff Miner Bot Smart Daemon
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/mangabuff-bot
ExecStart=/root/mangabuff-bot/venv/bin/python /root/mangabuff-bot/main.py --daemon
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
```
Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable mangabuff
sudo systemctl start mangabuff
```

---

## 📁 Project Structure

```
├── main.py                     # Entry point (CLI, Daemon, desktop app flags)
├── engine.py                   # MangaMinerBot core farm loop & lifecycle orchestration
├── arena.py                    # Consolidated Arena subsystem (combat simulation, optimization, locks, Evening Cup)
├── club.py                     # Club automation (crystal contributions, card donation alerts)
├── http_session.py             # Robust curl_cffi/requests HTTP session layer
├── tg_notifier.py              # Asynchronous Telegram notification engine
├── buffy/                      # Buffy desktop app: pywebview window, web UI, tray, autostart
├── config.py                   # Selectors, endpoints, and constants
├── data_management.py          # Session and credential persistence
├── utils.py                    # Safe cookie handling, parsing, and utilities
├── languages.py                # Multi-language translations (EN / RU / UA)
├── path.py                     # Base path resolution (script vs compiled exe)
└── refresh_panel.py            # Headless panel stats refresher
```

---

## ⚙️ Dependencies

* `requests` & `curl_cffi` — Resilient HTTP networking with TLS fingerprint impersonation
* `beautifulsoup4` — HTML parsing and data extraction
* `pywebview`, `pystray`, `pillow` — Buffy desktop app (`requirements-desktop.txt`, Windows only)
* `python-dotenv` — Environment configuration
* `packaging` & `urllib3` — Protocol support

---

## 📄 Disclaimer

*This project is developed for educational and personal use only. Use responsibly and in accordance with MangaBuff's Terms of Service.*
