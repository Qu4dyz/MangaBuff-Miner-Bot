"""Turn the MangaBuff profile skin (preset or event theme) into Buffy's own CSS variables.

The site gives each skin a block like ``.profile-skin--12 { --main-purple-color: #b3852d; ... }``.
Simple skins only set colours and a background gradient: they recolour Buffy's accent.
Event skins (e.g. halloween-sovereign) set a full dark palette: they recolour the whole app.
"""
import re

# Classes on <body> that are not colour skins
_NOT_COLOURS = ("font-", "video-", "glass-background", "title-border")
_COLOUR = re.compile(r"#([0-9a-f]{3,8})\b|rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)", re.I)


def skin_classes(page_html):
    body = re.search(r'<body[^>]*class="([^"]*)"', page_html)
    names = re.findall(r"profile-skin--([\w-]+)", body.group(1)) if body else []
    return [n for n in names if not n.startswith(_NOT_COLOURS)]


def stylesheet_urls(page_html):
    return re.findall(r'<link[^>]+href="(/css/[^"]+\.css[^"]*)"', page_html)


def skin_variables(css_texts, skins):
    """CSS custom properties of the given skins, later skins and stylesheets winning."""
    found = {}
    for skin in skins:
        rule = re.compile(r"\.profile-skin--%s\s*\{([^{}]*)\}" % re.escape(skin))
        for css in css_texts:
            for block in rule.findall(css):
                for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+)", block):
                    found[name] = value.replace("!important", "").strip()
    return found


def _colours(value, opaque=True):
    """Colours in a CSS value as (r, g, b), in order of appearance."""
    out = []
    for m in _COLOUR.finditer(value or ""):
        h, alpha = m.group(1), m.group(5)
        if h:
            if len(h) in (3, 4):
                h = "".join(c * 2 for c in h[:3])
            if len(h) not in (6, 8):
                continue
            out.append(tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)))
            continue
        if alpha is not None and opaque:
            a = float(alpha[:-1]) / 100 if alpha.endswith("%") else float(alpha)
            if a < 0.6:  # overlays like rgba(0,0,0,.5) are not the skin's colour
                continue
        out.append(tuple(int(m.group(i)) for i in (2, 3, 4)))
    return out


def _colour(value, opaque=True):
    found = _colours(value, opaque)
    return found[0] if found else None


def _gradient_colours(value):
    out = []
    for c in _colours(value):
        if sum(c) > 60 and c not in out:  # skip near-black stops
            out.append(c)
    return out


def _luma(rgb):
    return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255


def _bright(rgb, minimum=0.32):
    """Lighten an accent until it stands out on a dark background."""
    r, g, b = rgb
    while _luma((r, g, b)) < minimum:
        r, g, b = (min(255, int(v + (255 - v) * 0.15)) for v in (r, g, b))
    return r, g, b


def _css(rgb):
    return "#%02x%02x%02x" % rgb


def theme_from_variables(skin_vars):
    """Buffy CSS variables for a skin, or {} when the skin has no usable colours."""
    main = _colour(skin_vars.get("--main-purple-color"))
    second = _colour(skin_vars.get("--secondary-purple-color"))
    stops = _gradient_colours(skin_vars.get("--profile-skin-background"))
    main = main or (stops[0] if stops else None)
    if not main:
        return {}
    second = second if second and second != main else (stops[1] if len(stops) > 1 else None)
    main = _bright(main)
    second = _bright(second) if second else main
    theme = {
        "--pink": _css(main), "--glow": _css(main), "--violet": _css(second),
        "--pink-rgb": "%d, %d, %d" % main, "--glow-rgb": "%d, %d, %d" % main,
        "--violet-rgb": "%d, %d, %d" % second,
        "--accent": "linear-gradient(135deg, %s 0%%, %s 100%%)" % (_css(main), _css(second)),
    }
    # Full palettes (event skins) also repaint backgrounds and text, but only if they stay dark
    palette = {"--bg": "--grey-background", "--side-1": "--light-bg", "--side-2": "--dark-bg",
               "--surface": "--dark-bg", "--surface-2": "--light-bg", "--surface-3": "--medium-bg",
               "--text": "--main-text-color", "--muted": "--secondary-text-color"}
    colours = {ours: _colour(skin_vars.get(theirs)) for ours, theirs in palette.items()}
    backgrounds = [colours[k] for k in ("--bg", "--surface", "--surface-2", "--surface-3")]
    if all(colours.values()) and all(_luma(c) < 0.12 for c in backgrounds) and _luma(colours["--text"]) > 0.6:
        theme.update({k: _css(c) for k, c in colours.items()})
        border = _colour(skin_vars.get("--border-color"), opaque=False)
        if border:
            theme["--border"] = "rgba(%d, %d, %d, 0.22)" % border
    return theme


if __name__ == "__main__":
    sovereign = {"--main-purple-color": "#ffad5b", "--secondary-purple-color": "#d9a5ff",
                 "--light-bg": "#170f24", "--medium-bg": "#23162f", "--dark-bg": "#100b1c",
                 "--grey-background": "#0b0713", "--main-text-color": "#e8dbef",
                 "--secondary-text-color": "#baa5c4", "--border-color": "rgba(255,166,86,.24)"}
    t = theme_from_variables(sovereign)
    assert t["--pink"] == "#ffad5b" and t["--bg"] == "#0b0713" and t["--border"].startswith("rgba(255, 166, 86")
    blue = theme_from_variables({"--profile-skin-background": "linear-gradient(rgba(0, 0, 0, 0.5), rgba(0, 0, 0, 0.5)), "
                                 "linear-gradient(148.71deg, rgb(58, 123, 161) 5.64%, rgb(127, 126, 185) 26.38%)"})
    assert blue["--violet-rgb"] == "127, 126, 185" and "--bg" not in blue, blue
    assert theme_from_variables({}) == {}
    print("ok")
