"""Bounded, redacted diagnostics shared by desktop reports and the daemon."""
import json
import re


_SECRET_PATTERNS = [
    (re.compile(r'(?im)\b(authorization|cookie|set-cookie)\s*:\s*[^\r\n]+'), r'\1: <hidden>'),
    (re.compile(r'''(?i)((?:token|csrf|cookie|session|password|passwd|secret)[\w-]*["']?\s*[:=]\s*)(["'])(.*?)\2''', re.S),
     r'\1"<hidden>"'),
    (re.compile(r'''(?i)((?:token|csrf|cookie|session|password|passwd|secret)[\w-]*["']?\s*[:=]\s*)[^\s,;"'&]+'''), r'\1<hidden>'),
    (re.compile(r'\b\d{6,12}:[A-Za-z0-9_-]{30,}\b'), '<tg-token>'),
    (re.compile(r'(\w+://)[^\s/:@]+:[^\s/@]+@'), r'\1<user>:<pass>@'),
    (re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'), '<email>'),
    (re.compile(r'\b[A-Za-z0-9+/_-]{40,}={0,2}'), '<hidden>'),
]


def mask_secrets(text, secrets=()):
    text = str(text)
    for value in sorted((str(v) for v in secrets if v), key=len, reverse=True):
        if len(value) >= 4:
            text = text.replace(value, '<hidden>')
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def reading_response_details(response, secrets=()):
    """Keep API reasons, never whole HTML pages, reward tokens or cookies."""
    text = (response.text or '').strip()
    result = {'http': response.status_code,
              'content_type': mask_secrets(response.headers.get('Content-Type', ''), secrets)[:100]}
    if text.startswith('<'):
        result['body_type'] = 'html'
        return result
    try:
        data = json.loads(text) if text else None
    except (TypeError, ValueError):
        data = text
    result['body_type'] = type(data).__name__
    if isinstance(data, dict):
        for key in ('success', 'status', 'code', 'message', 'error', 'errors'):
            if key in data:
                # Nested validation details can include submitted credentials.
                value = data[key]
                if isinstance(value, (dict, list)):
                    result[key] = '<structured validation details omitted>'
                else:
                    result[key] = mask_secrets(str(value), secrets)[:400]
        result['reward'] = {key: bool(data.get(key)) for key in ('name', 'scroll', 'type')}
    elif isinstance(data, str):
        result['message'] = mask_secrets(data, secrets)[:200]
    return result
