[![ru](https://img.shields.io/badge/lang-ru-red.svg)](README.md) [![en](https://img.shields.io/badge/lang-en-green.svg)](README.en.md)

# 💜 Buffy — autopilot for MangaBuff

Buffy mines ore, reads chapters for bonus cards, fights in the arena, sends your squad to the Abyss Tower, contributes to your club and watches ads for diamonds. It runs 24/7 in the background on Windows and reports anything important to Telegram.

[![Latest release](https://img.shields.io/github/v/release/Qu4dyz/MangaBuff-Miner-Bot)](https://github.com/Qu4dyz/MangaBuff-Miner-Bot/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/Qu4dyz/MangaBuff-Miner-Bot/total)](https://github.com/Qu4dyz/MangaBuff-Miner-Bot/releases)

> ⚠️ **The only official repository and builds are here: [Qu4dyz/MangaBuff-Miner-Bot](https://github.com/Qu4dyz/MangaBuff-Miner-Bot).** Don't download Buffy from forks or under other names: the app gets access to your account, and a third-party build can steal it.

## Download and run

1. Open the [latest release](https://github.com/Qu4dyz/MangaBuff-Miner-Bot/releases/latest) and download **Buffy-Setup-….exe** (installer) or **Buffy-…-portable.zip** (no install).
2. Run it. Windows may say "Windows protected your PC" because the app has no paid code signature. Click **More info → Run anyway**.
3. The first-run wizard asks you to sign in to MangaBuff, **with email and password** or **through VK, Discord, Yandex or Google**, then offers to connect Telegram (optional).
4. Turn on the modules you want and press **Start**. Closing the window hides Buffy to the tray; the bot keeps working.

Buffy updates itself, never during the evening cup.

## Features

- ⛏️ **Mine:** digs when energy is up, buys pickaxe upgrades and Strong Hit.
- 📖 **Chapter reading:** fills the daily 75 chapters and bonus cards with human-like pauses; switches titles if the site stops counting one.
- ⚔️ **Arena and Evening Cup:** picks opponents by card strength and battle history, plays the cup 19:00–22:00 MSK, protects valuable cards.
- 🏰 **Abyss Tower:** claims rewards, re-sends the squad on the best expedition, upgrades the HQ.
- 🏛️ **Club:** contributes crystals and tells you when the club needs a card you own.
- 📺 **Ads for diamonds and daily rewards:** three ads a day, login gifts, chests, quests.
- 📲 **Telegram:** card drops with images, trades, summaries, alerts when sign-in fails or the site asks for a captcha; quiet hours.

## Signing in with VK, Discord, Yandex or Google

Pick your provider in the sign-in wizard. A normal MangaBuff window opens; sign in as in a browser and Buffy takes over. Buffy never sees or stores your social network password.

When the site drops the session, Buffy first signs in again silently: the provider remembers you in that window, so it usually takes a couple of seconds. If the provider asks you to confirm something, Buffy shows the window and alerts you in Telegram.

Google sometimes blocks sign-in inside app windows. In that case use "Забыли пароль?" on the MangaBuff sign-in page to set a password for your email, and sign in to Buffy with email and password.

## Your data

Settings, sign-in and logs stay on your computer in `%APPDATA%\Buffy`. Buffy talks only to MangaBuff, Telegram (if connected) and GitHub (update checks).

## Reporting a problem

Use **Settings → Report a problem** (Сообщить о проблеме). Buffy saves a full `Buffy-diagnostic.txt` report with passwords, emails and tokens masked, opens its folder and a new GitHub issue. Drag the file into the issue and send it.

---

## For developers and servers

The same bot runs headless on a Linux server (Python 3.10+).

| Mode | Command | What it does |
|------|---------|--------------|
| 24/7 daemon | `python main.py --daemon` | Runs forever, sleeping until the next event (energy, tower, chest, MSK midnight). |
| Single run | `python main.py` | One full cycle, then exits. |
| Status | `python main.py --status` | Reads account state only. |
| Buffy | `python main.py --app` | Windows app, needs `requirements-desktop.txt`. |

```bash
git clone https://github.com/Qu4dyz/MangaBuff-Miner-Bot.git
cd MangaBuff-Miner-Bot
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt   # Buffy: requirements-desktop.txt
```

Optional `.env` (otherwise the bot asks on first run):

```env
MANGA_LOGIN=you@example.com
MANGA_PASSWORD=your_password
TG_TOKEN=123456:bot-token
TG_CHAT_ID=123456789
```

Servers support email and password only; social sign-in needs the Buffy window. A systemd unit example is in the [Russian README](README.md#служба-systemd). Tests: `python -m pytest tests`.

---

*Unofficial project, not affiliated with MangaBuff. Use at your own risk.*
