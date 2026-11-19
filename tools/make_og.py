"""Картинка-превью сайта (docs/og.png, 1200x630) для мессенджеров и соцсетей.

Запускать вручную после смены названия сайта:  python tools/make_og.py
Шрифт Inter берётся из системы; логотипы и арт игры не используются.
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config as cfg

W, H = 1200, 630
FONTS = "/usr/share/fonts/opentype/inter/"


def font(name, size):
    try:
        return ImageFont.truetype(FONTS + name, size)
    except OSError:
        return ImageFont.load_default(size)


def gradient(size, left, right):
    w, h = size
    img = Image.new("RGB", size)
    px = img.load()
    for x in range(w):
        t = x / max(w - 1, 1)
        c = tuple(round(left[i] + (right[i] - left[i]) * t) for i in range(3))
        for y in range(h):
            px[x, y] = c
    return img


img = Image.new("RGB", (W, H), (12, 10, 18))
# мягкое свечение в углах
glow = Image.new("RGB", (W, H), (12, 10, 18))
g = ImageDraw.Draw(glow)
g.ellipse((-200, 250, 500, 900), fill=(120, 30, 80))
g.ellipse((800, -300, 1500, 300), fill=(120, 60, 20))
img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(160)), 0.75)

d = ImageDraw.Draw(img)
title_font = font("Inter-Black.otf", 150)
box = d.textbbox((0, 0), cfg.SITE_TITLE, font=title_font)
tw, th = box[2] - box[0], box[3] - box[1]
x, y = (W - tw) // 2 - box[0], 190 - box[1]
mask = Image.new("L", (W, H), 0)
ImageDraw.Draw(mask).text((x, y), cfg.SITE_TITLE, font=title_font, fill=255)
img.paste(gradient((W, H), (255, 79, 154), (255, 154, 61)), (0, 0), mask)

sub_font = font("Inter-Medium.otf", 40)
line = "Short GTA 6 clips in one feed"
sw = d.textlength(line, font=sub_font)
d.text(((W - sw) // 2, 190 + th + 60), line, font=sub_font, fill=(243, 238, 248))
note_font = font("Inter-Regular.otf", 26)
note = "Unofficial fan project"
nw = d.textlength(note, font=note_font)
d.text(((W - nw) // 2, H - 80), note, font=note_font, fill=(157, 147, 173))

out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "og.png")
img.save(out, optimize=True)
print("saved", out, os.path.getsize(out), "bytes")
