import os
import sys
import time
import argparse
from dotenv import load_dotenv

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from path import BASE_DIR
from data_management import DataManager
from engine import MangaMinerBot

USE_CLI_MODE = True  # Set to True to default to CLI mode, False for GUI mode


def main():
    os.makedirs(BASE_DIR, exist_ok=True)
    load_dotenv()

    parser = argparse.ArgumentParser(description="MangaBuff Miner Bot")
    parser.add_argument("--cli", action="store_true", help="Run in CLI mode (single run)")
    parser.add_argument("--daemon", action="store_true", help="Run in autonomous 24/7 Smart Daemon mode")
    parser.add_argument("--gui", action="store_true", help="Run in GUI mode")
    parser.add_argument("--status", action="store_true", help="Check status and exit")
    parser.add_argument("--proxy", type=str, default=None, help="Proxy (ip:port:user:pass or http://user:pass@ip:port)")
    args = parser.parse_args()

    run_gui = args.gui or (not args.cli and not args.status and not args.daemon and not USE_CLI_MODE)

    if run_gui:
        try:
            from gui_app import App
            print("Starting MangaBuff Miner in GUI mode...")
            app = App()
            app.mainloop()
        except (ImportError, ModuleNotFoundError) as e:
            print(f"⚠️ GUI not supported in headless environment ({e}). Falling back to CLI mode...")
            run_gui = False

    if not run_gui:
        print("=" * 50)
        if args.daemon:
            print("🤖 MangaBuff Miner — Smart Daemon Mode (24/7)")
        elif args.status:
            print("📊 MangaBuff Miner — Checking Account Status...")
        else:
            print("🤖 MangaBuff Miner — CLI Mode (Single Run)")
        print("=" * 50)

        email, pwd = DataManager.get_credentials()
        if not email or not pwd:
            env_login = os.getenv("MANGA_LOGIN")
            env_pass = os.getenv("MANGA_PASSWORD")
            if env_login and env_pass:
                DataManager.set_credentials(env_login, env_pass)
                email, pwd = env_login, env_pass
            else:
                email = input("Введите email MangaBuff: ").strip()
                pwd = input("Введите пароль MangaBuff: ").strip()
                if email and pwd:
                    DataManager.set_credentials(email, pwd)
                else:
                    print("❌ Логин и пароль не могут быть пустыми.")
                    return

        def _cli_log(msg):
            t = time.strftime("%H:%M:%S")
            print(f"[{t}] [BOT] {msg}")

        bot = MangaMinerBot(
            log_callback=_cli_log,
            progress_callback=lambda p: None,
            stats_callback=lambda **kw: None,
            headless=True,
            proxy=args.proxy
        )
        if args.status:
            bot.check_status_only()
        elif args.daemon:
            bot.run_daemon()
        else:
            bot.run()


if __name__ == "__main__":
    main()