import re


# ==========================================
# UTILS
# ==========================================
def parse_smart_number(text):
    if not text: return 0
    text = str(text).lower().strip().replace(",", ".")
    multiplier = 1
    if 'k' in text:
        multiplier = 1000
    elif 'm' in text:
        multiplier = 1000000

    clean_num = re.sub(r'[^\d\.]', '', text)
    try:
        if not clean_num: return 0
        return int(float(clean_num) * multiplier)
    except:
        return 0


def safe_cookies_to_dict(session):
    """Build a plain {name: value} dict from a requests cookie jar.

    Iterates the jar (an iterator of Cookie objects) and overwrites on
    duplicate names, so DDoS-Guard's repeated __ddg8_ / __ddg1_ cookies
    collapse to a single entry (last value wins). This avoids
    requests.utils.dict_from_cookiejar(), which raises
    MultipleCookiesWithSameNameError on duplicate names, and avoids any
    create_cookie()/jar.set() reconstruction that would corrupt the
    essential auth cookies (mangabuff_session, XSRF-TOKEN).
    """
    out = {}
    for c in session.cookies:
        out[c.name] = c.value  # last write wins
    return out


def normalize_proxy_url(proxy_str):
    """Normalize various proxy formats (ip:port:user:pass, socks5, http) into a valid URL."""
    if not proxy_str or not isinstance(proxy_str, str):
        return None
    proxy_str = proxy_str.strip()
    if not proxy_str:
        return None

    if proxy_str.startswith(("http://", "https://", "socks5://", "socks5h://")):
        return proxy_str

    parts = proxy_str.split(":")
    # Format: ip:port:user:password
    if len(parts) == 4:
        ip, port, user, pwd = parts
        return f"http://{user}:{pwd}@{ip}:{port}"
    # Format: ip:port
    if len(parts) == 2 and "@" not in proxy_str:
        return f"http://{proxy_str}"

    if "@" in proxy_str:
        return f"http://{proxy_str}"

    return f"http://{proxy_str}"


def mask_proxy_url(proxy_url):
    """Mask password in proxy URL for safe logging and display."""
    if not proxy_url:
        return ""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", proxy_url)

