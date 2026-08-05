import os
from dotenv import load_dotenv

from path import BASE_DIR
from tg_notifier import send_message
from engine import MangaMinerBot
from gui_app import App

USE_CLI_MODE = True # Set to True to run in CLI mode, False for GUI mode

def main():
    os.makedirs(BASE_DIR, exist_ok=True)
    load_dotenv()

    if USE_CLI_MODE:
        print("Starting MangaBuff Miner in CLI mode...")

        def _cli_log(msg):
            print(f"[BOT] {msg}")
            send_message(f"[BOT] {msg}")

        bot = MangaMinerBot(
            log_callback=_cli_log,
            progress_callback=lambda p: None,
            stats_callback=lambda **kw: None,            
        )
        bot.run()

    else:
        print("Starting MangaBuff Miner in GUI mode...")
        app = App()
        app.mainloop()

if __name__ == "__main__":
    main()