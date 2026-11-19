"""Страницы отдельных роликов: docs/c/<сеть>-<id>/index.html.

У сайта одна настоящая страница, лента. Но когда ссылку на ролик пересылают в
мессенджере, тот не выполняет скрипты и показывает превью по мета-тегам. Поэтому
у каждого ролика есть крошечная страница: в ней название и картинка для превью,
а человека она сразу перебрасывает в ленту на нужный ролик.
"""
import html
import json
import os
import shutil

import config as cfg
from . import links, store

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} | {site}</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="video.other">
<meta property="og:site_name" content="{site}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{image}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:image" content="{image}">
<script>location.replace({target});</script>
<style>body{{margin:0;padding:40px 20px;background:#0c0a12;color:#f3eef8;font:16px/1.4 system-ui,sans-serif;text-align:center}}a{{color:#ff9a3d}}</style>
</head>
<body>
<h1>{title}</h1>
<p>{desc}</p>
<p><a href="{href}">Watch this clip in the feed</a></p>
</body>
</html>
"""


def image_url(item):
    if item["platform"] == "yt":
        return f"https://i.ytimg.com/vi/{item['id']}/hqdefault.jpg"
    return cfg.SITE_URL + "og.png"


def render(item):
    title = (item.get("title") or "").strip()[:120] or "GTA 6 clip"
    who = (item.get("author") or "").strip()
    desc = (f"{who} · " if who else "") + f"Watch this GTA 6 clip on {cfg.SITE_TITLE}"
    anchor = f"../../#{item['platform']}-{item['id']}"
    e = lambda s: html.escape(s, quote=True)
    return TEMPLATE.format(
        title=e(title), site=e(cfg.SITE_TITLE), desc=e(desc),
        url=e(f"{cfg.SITE_URL}c/{links.slug(item)}/"), image=e(image_url(item)),
        target=json.dumps(anchor).replace("</", "<\\/"), href=e(anchor))


def build(published):
    """Привести папку страниц в соответствие с лентой: создать новые, убрать лишние."""
    if not cfg.SITE_URL:
        return
    wanted = {links.slug(i): i for i in published}
    os.makedirs(store.CLIPS_DIR, exist_ok=True)
    for name in os.listdir(store.CLIPS_DIR):
        if name not in wanted:
            shutil.rmtree(os.path.join(store.CLIPS_DIR, name), ignore_errors=True)
    for name, item in wanted.items():
        path = os.path.join(store.CLIPS_DIR, name, "index.html")
        text = render(item)
        try:
            with open(path, encoding="utf-8") as f:
                if f.read() == text:
                    continue
        except OSError:
            pass
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
