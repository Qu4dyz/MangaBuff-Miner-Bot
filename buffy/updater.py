# -*- coding: utf-8 -*-
"""Self-update from GitHub releases: find a newer Buffy-Setup-*.exe, download it, run it silently."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import threading

import requests

from buffy import __version__

REPO_API = "https://api.github.com/repos/Qu4dyz/MangaBuff-Miner-Bot"
LATEST_URL = REPO_API + "/releases/latest"
SETUP_RE = re.compile(r"^Buffy-Setup-[\w.\-]+\.exe$", re.IGNORECASE)
CHECK_EVERY_SEC = 30 * 60  # GitHub allows 60 anonymous API calls/hour per IP; 2 is safe


def parse_version(text) -> tuple:
    """'v11.2.0' -> (11, 2, 0); anything unparsable -> ()."""
    nums = re.findall(r"\d+", str(text or ""))
    return tuple(int(n) for n in nums[:3])


def is_newer(remote, local=__version__) -> bool:
    remote_v, local_v = parse_version(remote), parse_version(local)
    return bool(remote_v) and remote_v > local_v


def _get(url, timeout):
    return requests.get(url, timeout=timeout, headers={"Accept": "application/vnd.github+json"})


def fetch_latest(timeout=15):
    """Latest release info, or None when GitHub is unreachable or has no installer."""
    resp = _get(LATEST_URL, timeout)
    if resp.status_code == 404:
        # GitHub sometimes lists no releases to logged-out users while each release is still there
        # (an outage on 22 Oct 2026): ask for the newest tag's release directly
        tags = _get(REPO_API + "/tags?per_page=30", timeout)
        if tags.status_code != 200:
            return None
        names = [t.get("name") for t in tags.json() or [] if parse_version(t.get("name"))]
        if not names:
            return None
        resp = _get(REPO_API + "/releases/tags/" + max(names, key=parse_version), timeout)
        if resp.status_code == 404:  # no releases published yet
            return None
    resp.raise_for_status()
    data = resp.json()
    if data.get("draft") or data.get("prerelease"):
        return None
    for asset in data.get("assets") or []:
        if SETUP_RE.match(asset.get("name") or ""):
            return {
                "version": str(data.get("tag_name") or "").lstrip("vV"),
                "page": data.get("html_url") or "",
                "url": asset.get("browser_download_url") or "",
                "name": asset["name"],
                "size": int(asset.get("size") or 0),
            }
    return None


def installed_copy() -> bool:
    """True for a copy installed by Buffy-Setup (Inno leaves unins000.exe next to the exe)."""
    from config import IS_FROZEN

    if not IS_FROZEN:
        return False
    return os.path.exists(os.path.join(os.path.dirname(sys.executable), "unins000.exe"))


class Updater:
    """Background checker; the UI polls status()."""

    def __init__(self, auto_enabled, on_install):
        self._auto_enabled = auto_enabled  # callable -> bool
        self._on_install = on_install  # called right after the installer is launched
        self._lock = threading.Lock()
        self._busy = False
        self._info = None
        self._wake = threading.Event()
        self._state = {"state": "idle", "version": "", "progress": 0, "error": "", "page": ""}

    def status(self):
        with self._lock:
            out = dict(self._state)
        out["current"] = __version__
        out["can_install"] = installed_copy()
        return out

    def _set(self, **fields):
        with self._lock:
            self._state.update(fields)

    def start_background(self):
        threading.Thread(target=self._loop, daemon=True, name="buffy-updater").start()

    def check_now(self):
        self._wake.set()
        return True

    def _loop(self):
        self._wake.wait(5)  # let the window come up first
        while True:
            self._wake.clear()
            info = self._check()
            if info and self._auto_enabled() and installed_copy():
                self.install(info)
            self._wake.wait(CHECK_EVERY_SEC)

    def _check(self):
        self._set(state="checking", error="")
        try:
            info = fetch_latest()
        except Exception as exc:
            self._set(state="error", error=f"Не удалось проверить обновления: {exc}")
            return None
        if not info or not is_newer(info["version"]):
            self._set(state="latest", version=info["version"] if info else "")
            return None
        self._set(state="available", version=info["version"], page=info["page"])
        self._info = info
        return info

    def install(self, info=None):
        """Download the installer and run it silently; the app exits so files can be replaced."""
        info = info or self._info
        if not info:
            return False
        with self._lock:
            if self._busy:
                return False
            self._busy = True
        try:
            path = self._download(info)
            self._set(state="installing", progress=100)
            subprocess.Popen(
                [path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                close_fds=True,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            )
            self._on_install()
            return True
        except Exception as exc:
            self._set(state="error", error=f"Не удалось обновиться: {exc}")
            return False
        finally:
            with self._lock:
                self._busy = False

    def _download(self, info):
        self._set(state="downloading", progress=0)
        folder = os.path.join(tempfile.gettempdir(), "BuffyUpdate")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, info["name"])
        part = path + ".part"
        done = 0
        with requests.get(info["url"], stream=True, timeout=30) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or info["size"] or 0)
            with open(part, "wb") as fh:
                for chunk in resp.iter_content(256 * 1024):
                    fh.write(chunk)
                    done += len(chunk)
                    if total:
                        self._set(progress=int(done * 100 / total))
        if info["size"] and done != info["size"]:
            raise IOError(f"файл скачался не полностью ({done} из {info['size']} байт)")
        os.replace(part, path)
        return path
