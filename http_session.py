# -*- coding: utf-8 -*-
"""HTTP session helpers: browser TLS impersonation + DDoS-Guard recovery."""
from __future__ import annotations

import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

# Prefer curl_cffi (Chrome TLS/JA3 fingerprint). Fall back to requests.
try:
    from curl_cffi import requests as _http
    _HAS_CURL_CFFI = True
except ImportError:  # pragma: no cover
    import requests as _http
    _HAS_CURL_CFFI = False

import requests as _stdlib_requests


CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_BLOCK_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def has_curl_cffi() -> bool:
    return _HAS_CURL_CFFI


def is_anti_bot_page(resp) -> str | None:
    """
    Return a short reason string if response looks like DDoS-Guard / CF interstitial,
    otherwise None. Do NOT key off Server: ddos-guard alone — that header is on OK pages too.
    """
    if resp is None:
        return "no_response"
    try:
        status = int(getattr(resp, "status_code", 0) or 0)
    except Exception:
        status = 0

    # Binary / non-HTML responses are never an interstitial
    try:
        ctype = (resp.headers.get("content-type") or "").lower()
        if ctype.startswith(("image/", "audio/", "video/", "font/", "application/octet-stream")):
            return None
        if "application/json" in ctype:
            return None
    except Exception:
        pass

    # Magic-byte short-circuit (webp/jpeg/png/gif) even if content-type is wrong
    try:
        head = bytes(resp.content[:16] or b"")
        if head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF")):
            return None
    except Exception:
        pass

    text = getattr(resp, "text", None) or ""
    if not text and status in (403, 503, 429):
        return f"http_{status}"

    # Huge real pages are never the interstitial (~1–2 KB)
    if status == 200 and len(text) > 8000:
        # Still catch embedded challenge iframes on otherwise short-ish pages
        pass

    tl = text.lower()
    title = ""
    m = _BLOCK_TITLE_RE.search(text)
    if m:
        title = m.group(1).strip().lower()

    if "geoblocked" in tl or "restricted access from your current ip" in tl:
        return "ddos_guard_geo"
    if "is not available" in tl and "ddos-guard" in tl and len(text) < 5000:
        return "ddos_guard_geo"
    if title == "ddos-guard" or title.startswith("ddos-guard"):
        return "ddos_guard_challenge"
    if "checking your browser before accessing" in tl:
        return "ddos_guard_challenge"
    if "/.well-known/ddos-guard/js-challenge" in tl:
        return "ddos_guard_challenge"
    if "cf-browser-verification" in tl or "cf-challenge" in tl or "just a moment" in title:
        return "cloudflare_challenge"
    if "challenge-platform" in tl and ("turnstile" in tl or "cloudflare" in tl):
        return "cloudflare_challenge"
    if status in (403, 503) and "ddos-guard" in tl and len(text) < 8000:
        return "ddos_guard_http"
    return None


def create_raw_session(proxy: str | None = None, impersonate: str = "chrome131"):
    """Create an underlying Session (curl_cffi if available)."""
    if _HAS_CURL_CFFI:
        # impersonate may not exist on older builds — fall back gracefully
        try:
            sess = _http.Session(impersonate=impersonate)
        except TypeError:
            sess = _http.Session()
        except Exception:
            sess = _http.Session()
    else:
        sess = _stdlib_requests.Session()

    if proxy:
        sess.proxies.update({"http": proxy, "https": proxy})
    sess.headers.update({"User-Agent": CHROME_UA})
    return sess


def cookies_to_dict(session) -> dict:
    """Normalize cookie jar → {name: value} for both requests and curl_cffi."""
    jar = getattr(session, "cookies", None)
    if jar is None:
        return {}
    try:
        if hasattr(jar, "get_dict"):
            d = jar.get_dict()
            if isinstance(d, dict) and d:
                return dict(d)
    except Exception:
        pass
    try:
        d = dict(jar)
        if d:
            # curl_cffi often yields {name: value}
            if all(isinstance(k, str) for k in d.keys()):
                return {k: (v if isinstance(v, str) else getattr(v, "value", str(v))) for k, v in d.items()}
    except Exception:
        pass
    out = {}
    try:
        for c in jar:
            if isinstance(c, str):
                try:
                    out[c] = jar.get(c)
                except Exception:
                    out[c] = jar[c]
            else:
                out[c.name] = c.value
    except Exception:
        pass
    return out


def load_cookies_into(session, cookies: dict):
    if not cookies:
        return
    for name, value in cookies.items():
        if value is None:
            continue
        try:
            session.cookies.set(name, str(value))
        except Exception:
            try:
                session.cookies.update({name: str(value)})
            except Exception:
                pass


def _deadline_call(fn, wall_sec: float):
    """Run fn() with a hard wall clock; never block on hung worker teardown."""
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        fut = pool.submit(fn)
        try:
            return fut.result(timeout=wall_sec)
        except FuturesTimeout:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                pool.shutdown(wait=False)
            raise TimeoutError(f"call hung >{int(wall_sec)}s")
    finally:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            try:
                pool.shutdown(wait=False)
            except Exception:
                pass
        except Exception:
            pass


def try_refresh_ddos_guard(session, origin: str = "https://mangabuff.ru/") -> bool:
    """
    Soft recovery for DDoS-Guard JS cookie challenge:
    1) hit check.ddos-guard.net/check.js (sets __ddg2)
    2) hit origin to mint/refresh __ddg1_/__ddg8_/…
    Returns True if a subsequent probe of origin no longer looks blocked.
    """
    # Pause before touching check.js — rapid retries make challenges stickier.
    time.sleep(random.uniform(2.0, 4.5))
    try:
        _deadline_call(
            lambda: session.get(
                "https://check.ddos-guard.net/check.js",
                headers={"Referer": origin, "Accept": "*/*"},
                timeout=15,
            ),
            30.0,
        )
    except Exception:
        pass

    # Some DDG builds expect __ddg2 / __ddg2_ present
    try:
        jar = cookies_to_dict(session)
        ddg2 = jar.get("__ddg2") or jar.get("__ddg2_")
        if ddg2:
            try:
                session.cookies.set("__ddg2_", str(ddg2))
                session.cookies.set("__ddg2", str(ddg2))
            except Exception:
                pass
    except Exception:
        pass

    try:
        time.sleep(random.uniform(1.2, 2.5))
        probe = _deadline_call(lambda: session.get(origin, timeout=20), 35.0)
        return is_anti_bot_page(probe) is None and getattr(probe, "status_code", 0) == 200
    except Exception:
        return False


class GuardedSession:
    """
    Thin wrapper around requests/curl_cffi Session.
    On anti-bot interstitial: attempt DDG refresh once, then optionally notify.
    """

    def __init__(
        self,
        raw_session,
        on_block=None,
        on_recovered=None,
        persist_cookies=None,
        should_abort=None,
        on_heartbeat=None,
        proxy=None,
        impersonate="chrome131",
    ):
        self._s = raw_session
        self._proxy = proxy
        self._impersonate = impersonate
        self.on_block = on_block  # callable(reason, url) -> None
        self.on_recovered = on_recovered  # callable(reason, url) -> None
        self.persist_cookies = persist_cookies  # callable() -> None
        self.should_abort = should_abort  # callable() -> bool
        self.on_heartbeat = on_heartbeat  # callable() -> None
        self._recovering = False
        self.last_block_reason = None
        self.last_block_at = 0.0
        self.last_ok_at = 0.0
        self.block_count = 0
        self._last_persist_at = 0.0
        self._last_refresh_at = 0.0
        self._last_block_cb_at = 0.0
        self._last_request_at = 0.0
        self._last_request_mono = 0.0
        self._hang_resets = 0
        # Don't hammer check.js — extra challenges make DDoS-Guard stricter.
        self.refresh_cooldown = 180.0
        self.block_callback_cooldown = 120.0
        # Global polite spacing between ANY requests (human-ish).
        self.min_request_interval = 1.35
        self.max_request_jitter = 0.85

    def _heartbeat(self):
        if not self.on_heartbeat:
            return
        try:
            self.on_heartbeat()
        except Exception:
            pass

    def _rebuild_raw(self):
        """Replace wedged session after a hung proxy/socket (keep cookies/headers)."""
        cookies = cookies_to_dict(self._s)
        try:
            headers = dict(self._s.headers)
        except Exception:
            headers = {"User-Agent": CHROME_UA}
        try:
            self._s.close()
        except Exception:
            pass
        self._s = create_raw_session(proxy=self._proxy, impersonate=self._impersonate)
        try:
            self._s.headers.update(headers)
        except Exception:
            pass
        load_cookies_into(self._s, cookies)
        self._hang_resets += 1
        # Reset throttle so we don't sleep on a stale mono stamp after rebuild.
        self._last_request_at = 0.0
        self._last_request_mono = 0.0

    # The proxy failed to open the tunnel: the request never reached the site,
    # so even a POST is safe to send again.
    # "Connection timed out" is curl's connect-phase timeout, also before anything is sent
    # ("Operation timed out" is not: the request may already be on the server).
    PROXY_RETRY_MARKERS = ("CONNECT tunnel failed", "Proxy CONNECT aborted", "curl: (7)", "Connection timed out after")
    PROXY_RETRIES = 2

    def _raw_request(self, method, url, **kwargs):
        """Request with a couple of quick retries when the proxy drops the tunnel."""
        for attempt in range(self.PROXY_RETRIES + 1):
            try:
                return self._raw_request_once(method, url, **kwargs)
            except Exception as exc:
                text = str(exc)
                if attempt >= self.PROXY_RETRIES or not any(m in text for m in self.PROXY_RETRY_MARKERS):
                    raise
                if self.should_abort and self.should_abort():
                    raise
                end = time.monotonic() + random.uniform(2.0, 4.0) * (attempt + 1)
                while time.monotonic() < end:
                    self._heartbeat()
                    time.sleep(0.25)

    def _raw_request_once(self, method, url, **kwargs):
        """
        Run underlying request with a hard wall-clock deadline.
        Critical: never use `with ThreadPoolExecutor()` — on timeout its
        __exit__ calls shutdown(wait=True) and freezes the whole daemon
        while the hung proxy CONNECT thread never returns.
        """
        req_timeout = kwargs.get("timeout", 20)
        if isinstance(req_timeout, (tuple, list)) and req_timeout:
            soft = float(req_timeout[-1] or 20)
        else:
            soft = float(req_timeout or 20)
        wall = max(soft + 10.0, 25.0)
        kwargs.setdefault("timeout", soft)

        pool = ThreadPoolExecutor(max_workers=1)
        try:
            fut = pool.submit(self._s.request, method, url, **kwargs)
            try:
                return fut.result(timeout=wall)
            except FuturesTimeout:
                self._heartbeat()
                try:
                    pool.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    pool.shutdown(wait=False)
                self._rebuild_raw()
                raise TimeoutError(
                    f"HTTP {method} {url} hung >{int(wall)}s (proxy/network)"
                )
        finally:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                try:
                    pool.shutdown(wait=False)
                except Exception:
                    pass
            except Exception:
                pass

    def _throttle(self):
        """Space out requests so bursts don't trip DDoS-Guard / 429.

        Uses monotonic clock for sleeps: wall-clock NTP jumps must never
        freeze the daemon in a throttle loop.
        """
        now_wall = time.time()
        now_mono = time.monotonic()
        if self._last_request_at <= 0:
            self._last_request_at = now_wall
            self._last_request_mono = now_mono
            return
        last_mono = getattr(self, "_last_request_mono", 0.0) or 0.0
        if last_mono <= 0:
            # Migrate / recover after clock skew on wall timestamp.
            self._last_request_at = now_wall
            self._last_request_mono = now_mono
            return
        gap = self.min_request_interval + random.uniform(0.0, self.max_request_jitter)
        wait = (last_mono + gap) - now_mono
        if wait > 0:
            end = now_mono + min(wait, 8.0)
            while time.monotonic() < end:
                if self.should_abort and self.should_abort():
                    break
                if self.on_heartbeat:
                    try:
                        self.on_heartbeat()
                    except Exception:
                        pass
                time.sleep(min(0.25, max(0.0, end - time.monotonic())))
        self._last_request_at = time.time()
        self._last_request_mono = time.monotonic()
        if self.on_heartbeat:
            try:
                self.on_heartbeat()
            except Exception:
                pass

    # --- delegate common attributes ---
    @property
    def headers(self):
        return self._s.headers

    @property
    def cookies(self):
        return self._s.cookies

    @property
    def proxies(self):
        return self._s.proxies

    def __getattr__(self, name):
        return getattr(self._s, name)

    def _maybe_persist(self):
        if not self.persist_cookies:
            return
        now = time.time()
        if now - self._last_persist_at < 120:
            return
        self._last_persist_at = now
        try:
            self.persist_cookies()
        except Exception:
            pass

    def _mark_ok(self, method, url, resp):
        """Healthy response: clear prior block state so 429/blips don't stick for days."""
        was_blocked = bool(self.last_block_reason) and self.last_block_at >= self.last_ok_at
        prev_reason = self.last_block_reason
        self.last_ok_at = time.time()
        if method.upper() == "GET" and getattr(resp, "status_code", 0) == 200:
            try:
                if len(getattr(resp, "text", "") or "") > 5000:
                    self._maybe_persist()
            except Exception:
                pass
        if was_blocked:
            self.last_block_reason = None
            if self.on_recovered:
                try:
                    self.on_recovered(prev_reason or "ok", url)
                except Exception:
                    pass

    def _after_response(self, method, url, resp, kwargs):
        # HTTP 429 is a soft rate-limit — back off without flooding check.js.
        try:
            if int(getattr(resp, "status_code", 0) or 0) == 429:
                self.last_block_reason = "http_429"
                self.last_block_at = time.time()
                self.block_count += 1
                self._emit_block("http_429", url)
                return resp
        except Exception:
            pass

        reason = is_anti_bot_page(resp)
        if not reason:
            self._mark_ok(method, url, resp)
            return resp

        now = time.time()
        self.last_block_reason = reason
        self.last_block_at = now
        self.block_count += 1

        if self._recovering:
            self._emit_block(reason, url)
            return resp

        # A refresh was just attempted — don't fire check.js again on every URL.
        if now - self._last_refresh_at < self.refresh_cooldown:
            self._emit_block(reason, url)
            return resp

        self._recovering = True
        self._last_refresh_at = now
        try:
            ok = try_refresh_ddos_guard(self._s)
            if ok:
                retry_kw = dict(kwargs)
                retry_kw.setdefault("timeout", 20)
                time.sleep(random.uniform(0.8, 1.6))
                # Same hard deadline as normal requests — never block forever here.
                resp2 = self._raw_request(method, url, **retry_kw)
                if is_anti_bot_page(resp2) is None:
                    self.last_ok_at = time.time()
                    self._last_persist_at = 0
                    self._maybe_persist()
                    if self.on_recovered:
                        try:
                            self.on_recovered(reason, url)
                        except Exception:
                            pass
                    return resp2
                resp = resp2
            self._emit_block(reason, url)
            return resp
        except TimeoutError:
            self._emit_block(reason, url)
            raise
        finally:
            self._recovering = False

    def _emit_block(self, reason, url):
        now = time.time()
        if now - self._last_block_cb_at < self.block_callback_cooldown:
            return
        self._last_block_cb_at = now
        if not self.on_block:
            return
        try:
            self.on_block(reason, url)
        except Exception:
            pass

    def request(self, method, url, **kwargs):
        if self.should_abort and self.should_abort():
            raise RuntimeError("module aborted")
        if not self._recovering:
            self._throttle()
        # During nested recovery, skip anti-bot recursion but keep the hard deadline.
        if self._recovering:
            resp = self._raw_request(method, url, **kwargs)
            return resp
        resp = self._raw_request(method, url, **kwargs)
        return self._after_response(method, url, resp, kwargs)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def head(self, url, **kwargs):
        return self.request("HEAD", url, **kwargs)

    def close(self):
        try:
            self._s.close()
        except Exception:
            pass
