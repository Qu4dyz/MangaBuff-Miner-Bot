# -*- coding: utf-8 -*-
"""Buffy desktop shell: web UI window, tray icon, single-instance guard."""
from __future__ import annotations

import os
import sys
import threading

from buffy import APP_NAME, __version__
from buffy.api import BuffyApi
from buffy.updater import Updater
from data_management import DataManager


def resource_path(*parts):
    """Bundled file path (PyInstaller unpacks data to sys._MEIPASS)."""
    root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, *parts)


SHOW_EVENT = "Local\\BuffyShowWindow"


def _single_instance():
    """Two copies would run two daemons on one account — allow only one.

    A second launch asks the running copy to show its window instead.
    """
    if sys.platform != "win32":
        return True
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, False, "Global\\BuffyMangaBuffApp")
    already = kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    if already:
        event = kernel32.OpenEventW(0x0002, False, SHOW_EVENT)  # EVENT_MODIFY_STATE
        if event:
            kernel32.SetEvent(event)
            kernel32.CloseHandle(event)
        else:
            ctypes.windll.user32.MessageBoxW(
                None, "Buffy уже запущен — ищи его иконку возле часов.", APP_NAME, 0x40
            )
    return not already


def _listen_for_show(show):
    """Bring the window back when another launch of Buffy signals it."""
    if sys.platform != "win32":
        return
    import ctypes

    kernel32 = ctypes.windll.kernel32
    event = kernel32.CreateEventW(None, False, False, SHOW_EVENT)

    def wait():
        while kernel32.WaitForSingleObject(event, 0xFFFFFFFF) == 0:
            try:
                show()
            except Exception:
                pass

    threading.Thread(target=wait, name="buffy-show", daemon=True).start()


class Tray:
    def __init__(self, api: BuffyApi, show, quit_app):
        import pystray
        from PIL import Image

        self._api = api
        self._show = show
        self._quit = quit_app
        self._hint_shown = False
        image = Image.open(resource_path("buffy", "assets", "tray.png"))
        menu = pystray.Menu(
            pystray.MenuItem("Открыть Buffy", lambda *_: show(), default=True),
            pystray.MenuItem(
                lambda _: "Остановить бота" if api.is_running() else "Запустить бота",
                lambda *_: self._toggle(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", lambda *_: quit_app()),
        )
        self.icon = pystray.Icon(APP_NAME, image, f"{APP_NAME} {__version__}", menu)

    def _toggle(self):
        if self._api.is_running():
            self._api.stop()
        else:
            self._api.start()

    def refresh(self, status=None):
        labels = {
            "running": "работает",
            "starting": "запускается",
            "restarting": "перезапускается",
            "stopping": "останавливается",
            "stopped": "остановлен",
        }
        try:
            self.icon.title = f"{APP_NAME} — бот {labels.get(status, status or '')}".strip()
            self.icon.update_menu()
        except Exception:
            pass

    def hint_once(self):
        if self._hint_shown:
            return
        self._hint_shown = True
        try:
            self.icon.notify("Buffy работает в фоне. Открыть — двойной клик по иконке.", APP_NAME)
        except Exception:
            pass

    def run(self):
        self.icon.run_detached()

    def stop(self):
        try:
            self.icon.stop()
        except Exception:
            pass


def run(start_minimized=False):
    if not _single_instance():
        return

    import webview

    api = BuffyApi()
    state = {"quitting": False}

    width, height = _window_size(webview)
    window = webview.create_window(
        APP_NAME,
        # A new address per version: WebView2 must never serve the previous release's cached page
        url=resource_path("buffy", "ui", "index.html") + f"?v={_ui_stamp()}",
        js_api=api,
        width=width,
        height=height,
        min_size=(860, 540),  # fits a 1366x768 laptop at 125% scaling
        background_color="#0c0a14",
        hidden=start_minimized,
    )
    api._window = window

    def show():
        window.show()
        window.restore()

    def quit_app():
        state["quitting"] = True
        api.stop()
        tray.stop()
        window.destroy()

    tray = Tray(api, show, quit_app)
    _listen_for_show(show)
    api._on_status_change = tray.refresh
    # The installer replaces our files, so quit as soon as it is running
    api._updater = Updater(
        # Never restart a working bot into an update during the evening cup (19-22 MSK): it would drop a live fight
        auto_enabled=lambda: DataManager.get_setting("app_auto_update", True) and not (_cup_hours() and api.is_running()),
        on_install=quit_app,
    )

    api._quit = quit_app
    api._on_hidden = tray.hint_once
    api._notify = lambda text, title: tray.icon.notify(text, title)

    def on_closing():
        action = DataManager.get_setting("app_close_action", "ask")
        if state["quitting"] or action == "quit":
            api.stop()
            tray.stop()
            return True
        if action == "tray":
            window.hide()
            tray.hint_once()
            return False  # keep the app alive in the tray
        # Ask in the UI; evaluate_js from this handler would block the GUI thread
        threading.Thread(target=lambda: window.evaluate_js("askClose()"), daemon=True).start()
        return False

    window.events.closing += on_closing

    def on_started():
        tray.run()
        api._updater.start_background()
        if start_minimized:
            # Started with Windows: say it is running, or people launch it again or think it failed
            threading.Timer(3, tray.hint_once).start()
        onboarded = DataManager.get_setting("app_onboarded", False)
        if DataManager.has_login() and onboarded and DataManager.get_setting("app_start_bot_on_launch", True):
            api.start()

    webview.start(on_started, private_mode=False, storage_path=_storage_dir())
    # Window closed for good
    api.stop()
    tray.stop()
    # Give the bot a moment to finish its step, then exit hard: a request stuck in a worker
    # thread would otherwise keep the process (and the single-instance lock) alive forever
    import time

    deadline = time.time() + 8
    while api._status not in ("stopped",) and api._bot is not None and time.time() < deadline:
        time.sleep(0.2)
    os._exit(0)


def _cup_hours():
    from datetime import datetime, timedelta, timezone

    return 19 <= datetime.now(timezone(timedelta(hours=3))).hour < 22


def _window_size(webview):
    """1320x860, shrunk to fit smaller or high-DPI screens (sizes are in logical px)."""
    width, height = 1320, 860
    try:
        screen = webview.screens[0]
        width = min(width, int(screen.width * 0.9))
        height = min(height, int(screen.height * 0.88))
    except Exception:
        pass
    return max(width, 860), max(height, 540)


def _storage_dir():
    from config import BASE_DIR

    path = os.path.join(BASE_DIR, "webview")
    os.makedirs(path, exist_ok=True)
    _drop_stale_ui_cache(path)
    return path


def _ui_stamp():
    """Version plus a fingerprint of index.html: a changed page never comes from WebView2's cache,
    even when the version number stays the same (test builds, hotfixes)."""
    import hashlib

    try:
        with open(resource_path("buffy", "ui", "index.html"), "rb") as page:
            return f"{__version__}-{hashlib.sha1(page.read()).hexdigest()[:10]}"
    except OSError:
        return __version__


def _drop_stale_ui_cache(path):
    """WebView2 keeps serving the old index.html after an update: clear its page cache once per page change."""
    stamp = _ui_stamp()
    if DataManager.get_setting("app_ui_version") == stamp:
        return
    import shutil

    for sub in ("Cache", "Code Cache"):
        shutil.rmtree(os.path.join(path, "EBWebView", "Default", sub), ignore_errors=True)
    DataManager.set_setting("app_ui_version", stamp)


if __name__ == "__main__":
    run(start_minimized="--minimized" in sys.argv)
