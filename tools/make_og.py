"""Картинка-превью сайта (docs/og.png, 1200x630) для мессенджеров и соцсетей.

Запускать вручную после смены названия сайта:  python tools/make_og.py
Рисунок собственный: закат, пальмы и силуэт города. Логотипы, шрифты и арт игры
не используются. Нужны playwright и Chrome (картинка рисуется как SVG и
снимается браузером), шрифт Inter берётся из системы.
"""
import html
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import config as cfg

W, H, HORIZON = 1200, 630, 440
random.seed(6)


def skyline():
    """Силуэт города вдоль горизонта: два слоя домов разной высоты."""
    out = []
    for color, lo, hi, step in (("#3a1552", 30, 120, 46), ("#1d0c30", 18, 80, 38)):
        x = -10
        while x < W:
            w = random.randint(step - 14, step + 22)
            h = random.randint(lo, hi)
            # в центре, где солнце, дома ниже, чтобы его не закрывать
            h = int(h * (0.45 + 0.55 * min(1, abs(x + w / 2 - W / 2) / 330)))
            out.append(f'<rect x="{x}" y="{HORIZON - h}" width="{w}" height="{h + 4}" fill="{color}"/>')
            x += w + random.randint(0, 6)
    return "\n".join(out)


def frond(angle, length, droop):
    """Лист пальмы: изогнутая полоса с зубчатым нижним краем."""
    a = math.radians(angle)
    ex, ey = math.cos(a) * length, math.sin(a) * length + droop
    cx, cy = math.cos(a) * length * 0.55, math.sin(a) * length * 0.55 - 34
    teeth = []
    for i in range(9, 0, -1):
        t = i / 10
        # точка на кривой и "зуб" под ней
        px = (1 - t) ** 2 * 0 + 2 * (1 - t) * t * cx + t * t * ex
        py = (1 - t) ** 2 * 0 + 2 * (1 - t) * t * cy + t * t * ey
        teeth.append(f"L{px:.0f},{py + 6:.0f} L{px - math.cos(a) * 9:.0f},{py + 26 + 16 * t:.0f}")
    return f'<path d="M0,0 Q{cx:.0f},{cy:.0f} {ex:.0f},{ey:.0f} {" ".join(teeth)} Z"/>'


def palm(x, base, height, lean, scale=1.0, flip=False):
    top_x, top_y = x + lean, base - height
    trunk = (f'<path d="M{x - 11},{base} Q{x + lean * 0.2 - 8},{base - height * 0.55} {top_x - 5},{top_y} '
             f'L{top_x + 5},{top_y} Q{x + lean * 0.2 + 10},{base - height * 0.55} {x + 11},{base} Z"/>')
    fronds = "".join(frond(a, l, d) for a, l, d in (
        (-160, 150, 46), (-128, 165, 14), (-95, 150, -6), (-62, 168, 10), (-28, 158, 40),
        (8, 132, 74), (172, 128, 78)))
    mirror = " scale(-1,1)" if flip else ""
    return (f'<g fill="#0e0718">{trunk}<g transform="translate({top_x},{top_y}) scale({scale}){mirror}">'
            f'{fronds}<circle r="13"/></g></g>')


stripes = "".join(f'<rect x="0" y="{HORIZON - 150 + i * 30 + i * i * 1.2:.0f}" width="{W}" height="{5 + i * 2.2:.0f}"/>'
                  for i in range(6))
water = "".join(f'<rect x="{W / 2 - 150 - i * 34:.0f}" y="{HORIZON + 16 + i * 26}" width="{300 + i * 68}" height="{3 + i}" '
                f'fill="#ff9a5a" opacity="{0.5 - i * 0.07:.2f}" rx="2"/>' for i in range(6))
title = html.escape(cfg.SITE_TITLE)

SVG = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
<defs>
  <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#170a3a"/><stop offset=".38" stop-color="#5a1a78"/>
    <stop offset=".62" stop-color="#d63a8c"/><stop offset=".86" stop-color="#ff8a4c"/><stop offset="1" stop-color="#ffc45e"/>
  </linearGradient>
  <linearGradient id="sun" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#fff0a0"/><stop offset=".55" stop-color="#ffb347"/><stop offset="1" stop-color="#ff4f9a"/>
  </linearGradient>
  <linearGradient id="sea" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#5b1a6e"/><stop offset="1" stop-color="#120722"/>
  </linearGradient>
  <mask id="cut"><rect width="{W}" height="{H}" fill="#fff"/><g fill="#000">{stripes}</g></mask>
  <filter id="glow" x="-20%" y="-40%" width="140%" height="180%"><feGaussianBlur stdDeviation="14"/></filter>
  <filter id="shadow" x="-10%" y="-20%" width="120%" height="150%">
    <feDropShadow dx="0" dy="6" stdDeviation="10" flood-color="#12051f" flood-opacity=".75"/>
  </filter>
</defs>
<rect width="{W}" height="{HORIZON}" fill="url(#sky)"/>
<circle cx="{W / 2}" cy="{HORIZON - 40}" r="230" fill="#ffb347" opacity=".28" filter="url(#glow)"/>
<circle cx="{W / 2}" cy="{HORIZON - 40}" r="170" fill="url(#sun)" mask="url(#cut)"/>
{skyline()}
<rect y="{HORIZON}" width="{W}" height="{H - HORIZON}" fill="url(#sea)"/>
{water}
{palm(105, 660, 410, 60, 1.15)}
{palm(-10, 680, 300, 60, 0.95)}
{palm(1100, 660, 425, -65, 1.18, flip=True)}
{palm(1215, 690, 320, -70, 1.0, flip=True)}
<g font-family="Inter, system-ui, sans-serif" text-anchor="middle" filter="url(#shadow)">
  <text x="{W / 2}" y="176" font-size="128" font-weight="900" fill="#ffffff" letter-spacing="2">{title}</text>
  <text x="{W / 2}" y="232" font-size="38" font-weight="600" fill="#ffe9f3">Short GTA 6 clips in one feed</text>
</g>
<text x="{W / 2}" y="606" font-family="Inter, system-ui, sans-serif" font-size="22" font-weight="500"
      text-anchor="middle" fill="#e9d8f5" opacity=".8">Unofficial fan project</text>
</svg>"""

from playwright.sync_api import sync_playwright

out = os.path.join(ROOT, "docs", "og.png")
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get("CHROME", "/usr/bin/google-chrome"), args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": W, "height": H})
    page.set_content(f'<body style="margin:0;background:#000">{SVG}</body>')
    page.wait_for_timeout(300)
    page.screenshot(path=out, clip={"x": 0, "y": 0, "width": W, "height": H})
    browser.close()
print("saved", out, os.path.getsize(out), "bytes")
