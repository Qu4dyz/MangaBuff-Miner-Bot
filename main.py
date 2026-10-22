import os
import sys
import time
import argparse
from dotenv import load_dotenv

# Ensure UTF-8 output and immediate line buffering on consoles and systemd
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from path import BASE_DIR
from data_management import DataManager
from engine import MangaMinerBot


def main():
    os.makedirs(BASE_DIR, exist_ok=True)
    load_dotenv()

    parser = argparse.ArgumentParser(description="MangaBuff Miner Bot")
    parser.add_argument("--cli", action="store_true", help="Run in CLI mode (single run)")
    parser.add_argument("--daemon", action="store_true", help="Run in autonomous 24/7 Smart Daemon mode")
    parser.add_argument("--app", "--gui", dest="app", action="store_true", help="Open the Buffy desktop app")
    parser.add_argument("--minimized", action="store_true", help="Start the desktop app hidden in the tray")
    parser.add_argument("--status", action="store_true", help="Check status and exit")
    parser.add_argument("--proxy", type=str, default=None, help="Proxy (ip:port:user:pass or http://user:pass@ip:port)")
    args = parser.parse_args()

    # The packaged .exe opens the desktop app unless a console mode is asked for
    from config import IS_FROZEN as frozen
    if args.app or (frozen and not (args.cli or args.daemon or args.status)):
        from buffy.app import run as run_app
        run_app(start_minimized=args.minimized)
        return

    print("=" * 50)
    if args.daemon:
        print("🤖 MangaBuff Miner — Smart Daemon Mode (24/7)")
    elif args.status:
        print("📊 MangaBuff Miner — Checking Account Status...")
    else:
        print("🤖 MangaBuff Miner — CLI Mode (Single Run)")
    print("=" * 50)

    email, pwd = DataManager.get_credentials()
    if not DataManager.has_login():
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