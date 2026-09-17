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


def parse_card_cooldown_seconds(text):
    """Parse cooldown duration in seconds from text like '61 мин', '1 ч 20 мин', etc."""
    if not text:
        return 0
    total = 0
    m_h = re.search(r"(\d+)\s*(?:ч|час)", text, re.IGNORECASE)
    if m_h:
        total += int(m_h.group(1)) * 3600
    m_m = re.search(r"(\d+)\s*(?:м|мин)", text, re.IGNORECASE)
    if m_m:
        total += int(m_m.group(1)) * 60
    return total if total > 0 else 60


def get_seconds_until_midnight_msk():
    """Calculate remaining seconds until next 00:00:10 MSK (UTC+3 daily reset)."""
    from datetime import datetime, timezone, timedelta
    msk_offset = timezone(timedelta(hours=3))
    now_msk = datetime.now(msk_offset)
    next_reset = (now_msk + timedelta(days=1)).replace(hour=0, minute=0, second=10, microsecond=0)
    diff = (next_reset - now_msk).total_seconds()
    return max(60, int(diff))


def classify_card_copy_number(copy_number):
    """Classify card copy number into MangaBuff's official special number tiers.

    Official categories from MangaBuff frontend (getCardCopyInfo):
    - #1: «Самая первая»
    - #2–#10: «Ранний осколок»
    - #11–#100: «Старший экземпляр»
    - #404: «Потерянная карта»
    - 777, 7777...: «Семёрка удачи»
    - 11, 22, 33, 444, 5555...: «Зеркальный след» (одинаковые цифры)
    - 123, 1234, 12345...: «Идеальная цепочка» (возрастающая последовательность)
    - 321, 4321, 54321...: «Обратная цепь» (убывающая последовательность)
    - 101, 676, 1001, 1221...: «Двойное отражение» (число-палиндром)
    - Кратные 500, 1000: «Веха коллекции»
    - Иначе: «Архивный след» (обычный порядковый номер)
    """
    try:
        num = int(copy_number)
    except (ValueError, TypeError):
        return None
    if num <= 0:
        return None

    num_str = str(num)
    if num == 1:
        return {"copy_number": num, "title": "Самая первая", "is_special": True}
    elif num <= 10:
        return {"copy_number": num, "title": "Ранний осколок", "is_special": True}
    elif num <= 100:
        return {"copy_number": num, "title": "Старший экземпляр", "is_special": True}
    elif num == 404:
        return {"copy_number": num, "title": "Потерянная карта", "is_special": True}
    elif re.match(r"^7+$", num_str):
        return {"copy_number": num, "title": "Семёрка удачи", "is_special": True}
    elif re.match(r"^(\d)\1+$", num_str):
        return {"copy_number": num, "title": "Зеркальный след", "is_special": True}
    elif len(num_str) >= 3 and num_str in "123456789":
        return {"copy_number": num, "title": "Идеальная цепочка", "is_special": True}
    elif len(num_str) >= 3 and num_str in "987654321":
        return {"copy_number": num, "title": "Обратная цепь", "is_special": True}
    elif len(num_str) >= 3 and num_str == num_str[::-1]:
        return {"copy_number": num, "title": "Двойное отражение", "is_special": True}
    elif num % 500 == 0 or num % 1000 == 0:
        return {"copy_number": num, "title": "Веха коллекции", "is_special": True}
    else:
        return {"copy_number": num, "title": "Архивный след", "is_special": False}



