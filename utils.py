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
