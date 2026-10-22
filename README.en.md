<div align="center">

[![ru](https://img.shields.io/badge/lang-ru-red.svg)](README.md) [![en](https://img.shields.io/badge/lang-en-green.svg)](README.en.md)

# 💜 Buffy

### Autopilot for MangaBuff: mine, chapters, arena, tower and club while you do your own thing

[![Release](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FQu4dyz%2FMangaBuff-Miner-Bot%2Fbadges%2Fversion.json&label=release)](https://github.com/Qu4dyz/MangaBuff-Miner-Bot/releases/latest)
[![Downloads](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FQu4dyz%2FMangaBuff-Miner-Bot%2Fbadges%2Fdownloads.json)](https://github.com/Qu4dyz/MangaBuff-Miner-Bot/releases)
![Windows](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D6?logo=windows)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)

**[⬇️ Download](https://github.com/Qu4dyz/MangaBuff-Miner-Bot/releases/latest)** · [Features](#-what-buffy-does) · [Sign-in](#-signing-in) · [Telegram](#-telegram) · [Help](#-when-something-goes-wrong) · [Servers](#%EF%B8%8F-servers-and-developers)

</div>

---

## ✨ In short

- 🕒 **Runs on its own, 24/7.** Turn it on once; Buffy decides when to mine, read and fight, and sleeps until the next task.
- 🎮 **One window for everything.** Cards, chapters, diamonds, tower and arena rank at a glance, with a plain-language journal of what the bot did.
- 📲 **Talks to you in Telegram.** Card drops, trades, problems: you know right away, even away from the PC.
- 🛡️ **Gentle on your account.** Human-like pauses, no hammering the site with logins, backs off when the site asks to slow down.
- 🔄 **Updates itself.** Quietly, and never in the middle of the evening cup.

## 🚀 Get started in a couple of minutes

1. From the [releases page](https://github.com/Qu4dyz/MangaBuff-Miner-Bot/releases/latest) download **`Buffy-Setup-….exe`** (installer) or **`Buffy-…-portable.zip`** (no install).
2. Run it. Windows may say "Windows protected your PC" because the app has no paid code signature. Click **More info → Run anyway**.
3. Sign in to MangaBuff with email and password, or in one click with **VK, Discord, Yandex or Google**.
4. Optionally connect Telegram; the wizard finds your chat for you.
5. Turn on the modules you want and press **Start**. You can close the window: Buffy hides to the tray and keeps working.

> 💡 Buffy is published only here, on this repository's [releases page](https://github.com/Qu4dyz/MangaBuff-Miner-Bot/releases). Copies under other names are not ours, and an app with access to your account deserves some caution.

---

## 🧩 What Buffy does

Every module can be turned on or off, and most can be started by hand right away.

| | Module | What it does |
|:-:|---|---|
| 📅 | **Daily rewards** | Keeps your login streak, claims the daily reward and finished quests |
| ⛏️ | **Mine** | Spends the daily mine energy on ore, buys pickaxe upgrades and Strong Hit |
| 📖 | **Chapter reading** | Collects up to 10 bonus cards a day and the 75-chapter quest |
| ⚔️ | **Arena** | Builds the strongest deck, fights while essence is paid, opens chests |
| 🏆 | **Evening Cup** | Plays the cup move by move, 19:00–22:00 MSK |
| 🏰 | **Abyss Tower** | Claims expedition rewards, re-sends the squad, upgrades the HQ |
| 🏛️ | **Club** | Contributes crystals and tells you when the club needs your card |
| 📺 | **Ads** | Three ads a day for +21 💎, nothing to watch yourself |
| 🎃 | **Events** | Collects holiday rewards, such as Halloween candies |
| ✨ | **Awakening reroll** | Awakens chosen cards again and again until ATK, HP and CR reach your minimums |
| 🔮 | **Seal reforge** | Reforges the seal of chosen cards until a wanted one comes up, keeping ATK, HP and CR |

<details>
<summary><b>📖 More on chapter reading</b></summary>

<br>

A new bonus card unlocks roughly every 45–60 minutes. Buffy reads until it drops and fills the daily 75 chapters on the way, pausing between chapters like a real reader so the site counts them. If a title stops counting (for example, its chapters were already read), Buffy moves to another one.

</details>

<details>
<summary><b>⚔️ More on the arena and cup</b></summary>

<br>

Buffy builds the deck from attack, health, seals and card energy, swapping tired cards (under 10 energy) for rested ones, and picks opponents by strength and battle history. Free awakenings are optional and off by default, since an awakening can't be undone. In the evening cup it chooses the target and tactic every move.

</details>

<details>
<summary><b>🏰 More on the Abyss Tower</b></summary>

<br>

When the squad returns, Buffy claims diamonds and crystals and sends it on the best safe expedition, checking every difficulty and duration and the time left before the evening cup. Spare crystals go into the HQ. Pick the squad once on the site; Buffy handles the rest.

</details>

---

## ✨ Awakening: stat reroll and seal reforge

A separate «Пробуждение» page in Buffy. **Stat reroll:** pick one or several cards (tiles with art, rank, copy number and stats), set minimum ATK, HP and CR with sliders; the bot awakens, checks, unawakens for free and tries again, using only awakening energy from fights. Every roll is shown with its seal; «Stop and keep» keeps the roll on screen (3 s hold before the next one); cards go back into the deck and Tower squad; it waits during the evening cup and notifies you on Windows and Telegram. **Seal reforge:** pick awakened cards and acceptable seals; the bot reforges (500 essence each) within your essence limit, with a hint per seal on how often opponents use it and whether it wins more than deck strength predicts.

## 🔐 Signing in

**Email and password.** If the site drops the session, Buffy signs in again unnoticed.

**VK, Discord, Yandex or Google.** Press the button, sign in in the normal MangaBuff window that opens, and Buffy takes it from there. It never sees or stores your social network password. When the session drops, Buffy repeats the sign-in in a hidden window, which usually takes a couple of seconds; the window shows up only if the provider asks you to confirm something, and Buffy tells you in Telegram.

<details>
<summary><b>Google won't sign in inside the app?</b></summary>

<br>

Google sometimes blocks sign-in inside apps. Use "Забыли пароль?" on the MangaBuff sign-in page to set a password for your email, then sign in to Buffy with email and password.

</details>

## 📲 Telegram

Buffy sends card drops with images, scrolls and rare finds, trade offers with a link, club card requests, tower, ads, mine and arena summaries, and alerts when sign-in fails, the site is down or an event starts. 🌙 **Night mode** (23:00–08:00 MSK) mutes everything and skips reports; 🔕 **Always silent** keeps sound only for the most urgent messages.

## 🖥️ The app

Tray icon and start with Windows, one-click manual runs for any module, a journal in plain words instead of technical logs, and auto-updates you can turn off.

## 🔒 Your data

Sign-in, settings and logs stay on your computer in `%APPDATA%\Buffy`. Buffy talks only to MangaBuff, Telegram (if connected) and GitHub for update checks.

## 🆘 When something goes wrong

Press **Report a problem** (Сообщить о проблеме) in the journal or settings. Buffy saves a detailed `Buffy-diagnostic.txt` with passwords, emails and tokens masked, opens its folder and a GitHub issue page. Drag the file into the issue and send it.

---

## 🛠️ Servers and developers

The same bot runs headless, for example 24/7 on a Linux server.

| Mode | Command | What it does |
|---|---|---|
| 🌙 24/7 daemon | `python main.py --daemon` | Runs forever, sleeping until the next event |
| ▶️ Single run | `python main.py` | One full cycle, then exits |
| 📊 Status | `python main.py --status` | Reads account state only |
| 💜 Buffy | `python main.py --app` | The Windows app (needs `requirements-desktop.txt`) |

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

Servers support email and password only; social sign-in needs the Buffy window. A systemd unit and the project layout are in the [Russian README](README.md#%EF%B8%8F-для-сервера-и-разработчиков). Tests: `python -m pytest tests`.

---

<div align="center">

<sub>Buffy is an unofficial project, not affiliated with MangaBuff. Made with 💜 by <a href="https://github.com/Qu4dyz">Qu4dyz</a>.</sub>

</div>
