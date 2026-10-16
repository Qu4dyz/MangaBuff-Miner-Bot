"""Render Buffy's icon (PNG + multi-size ICO + tray + UI logo) into buffy/.

Two fanned collectible cards with a sparkle on a dark violet tile.
Run after changing the design: python -m buffy.make_icon
"""
import math
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
SIZE = 512
WHITE = (255, 255, 255, 255)


def _gradient(size, start, end, diagonal=False):
    w, h = size
    img = Image.new("RGBA", size)
    px = img.load()
    for y in range(h):
        for x in range(w):
            t = (x + y) / (w + h - 2) if diagonal else y / max(1, h - 1)
            px[x, y] = tuple(int(start[i] + (end[i] - start[i]) * t) for i in range(3)) + (255,)
    return img


def _sparkle(draw, cx, cy, r, color=WHITE):
    pts = []
    for i in range(8):
        angle = math.pi / 4 * i - math.pi / 2
        radius = r if i % 2 == 0 else r * 0.3
        pts.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    draw.polygon(pts, fill=color)


def _card(s, colors):
    size = (int(s * 0.42), int(s * 0.58))
    card = Image.new("RGBA", size, (0, 0, 0, 0))
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=int(s * 0.05), fill=255)
    card.paste(_gradient(size, colors[0], colors[1], diagonal=True), (0, 0), mask)
    inset = max(2, int(s * 0.016))
    ImageDraw.Draw(card).rounded_rectangle(
        [inset, inset, size[0] - inset - 1, size[1] - inset - 1],
        radius=int(s * 0.04), outline=(255, 255, 255, 140), width=max(1, int(s * 0.008)),
    )
    return card


def render(size=SIZE):
    s = size
    tile = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.24), fill=255)
    tile.paste(_gradient((s, s), (44, 30, 74), (20, 14, 34)), (0, 0), mask)

    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    for angle, colors, dx in (
        (-14, ((124, 77, 255), (180, 92, 255)), -s * 0.07),  # back card: violet
        (10, ((255, 106, 170), (255, 160, 120)), s * 0.07),  # front card: pink -> peach
    ):
        card = _card(s, colors).rotate(angle, expand=True, resample=Image.BICUBIC)
        layer.alpha_composite(card, (int(s / 2 - card.size[0] / 2 + dx), int(s / 2 - card.size[1] / 2)))
    icon = Image.alpha_composite(tile, layer)

    draw = ImageDraw.Draw(icon)
    _sparkle(draw, s * 0.56, s * 0.5, s * 0.13)
    _sparkle(draw, s * 0.8, s * 0.2, s * 0.05, (255, 210, 235, 255))
    return icon


def main():
    os.makedirs(ASSETS, exist_ok=True)
    big = render(SIZE)
    big.save(os.path.join(ASSETS, "buffy.png"))
    # The web UI is served from buffy/ui, so it needs its own copy
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(HERE, "ui", "logo.png"))
    big.resize((64, 64), Image.LANCZOS).save(os.path.join(ASSETS, "tray.png"))
    big.save(
        os.path.join(ASSETS, "buffy.ico"),
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("icons written to", ASSETS)


if __name__ == "__main__":
    main()
