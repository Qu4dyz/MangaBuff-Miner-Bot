[![ru](https://img.shields.io/badge/lang-ru-red.svg)](README.ru.md) [![en](https://img.shields.io/badge/lang-en-green.svg)](README.md)

# 🤖 MangaBuff Miner Bot v10.2

**MangaBuff Miner** is a powerful automation tool for the MangaBuff mining game featuring a modern GUI.
It fully simulates human behavior, supports auto-upgrades, and runs in background mode.

![Status](https://img.shields.io/badge/Status-Active-success)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)

## ✨ Features

* **🖥️ Modern GUI:** Dark mode, smooth animations (CustomTkinter).
* **🌍 Multi-language:** Full support for English 🇺🇸, Russian 🇷🇺, and Ukrainian 🇺🇦.
* **👻 Headless Mode:** Can run completely invisibly in the background.
* **⚡ Smart Clicker:** Uses JavaScript injection to bypass lag and animations.
* **🛠️ Auto Upgrade:** Automatically checks the store and buys pickaxe upgrades.
* **💾 Persistence:** Remembers your login (stored locally) and last known balance.
* **⏱️ Reset Timer:** Shows countdown to daily reset (MSK time).

## 🚀 How to Run (For Users)

1. Download the `.exe` file from the **Releases** section (on the right side of the GitHub page).
2. Run the file.
3. Enter your MangaBuff credentials (data is saved locally in `user_data.json`).
4. Click **Start Mining**.

## 💻 How to Run (From Source)

1. Install Python 3.10+.
2. Install dependencies:
   ```bash
   pip install customtkinter selenium webdriver-manager

3. Run the script:
   ```bash
   python main.pyw
   
## 🛠 Tech Stack

* **UI:** CustomTkinter
* **Automation:** Selenium WebDriver
* **Browser:** Chrome (Auto-managed via webdriver-manager)

---
*Disclaimer: This software is for educational purposes only. Use at your own risk.*
