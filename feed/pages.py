"""Статические страницы сайта помимо ленты.

docs/c/<сеть>-<id>/index.html  страница ролика
docs/top/index.html            лучшие ролики за сутки
docs/sitemap.xml               карта сайта для поисковиков

Страница ролика нужна дважды. Мессенджер при пересылке ссылки не выполняет
скрипты и строит превью по её мета-тегам. А поисковик индексирует её как
обычную страницу: с названием, описанием, плеером и разметкой видео.

Человеку же удобнее лента. Поэтому ссылки из канала и из кнопки "Поделиться"
заканчиваются на #feed (см. FEED_MARK): с такой меткой страница сразу
перебрасывает в ленту на нужный ролик. Поисковик метку отбрасывает и видит
страницу как есть, без переадресации.
"""
import html
import json
import os
import re
import shutil

import config as cfg
from . import links, store

FEED_MARK = "#feed"
MORE_CLIPS = 6        # сколько соседних роликов показывать ссылками под плеером

STYLE = """
:root{--bg:#0c0a12;--card:#17131f;--line:#2a2436;--text:#f3eef8;--dim:#9d93ad;--a1:#ff4f9a;--a2:#ff9a3d}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--a2)}
header{display:flex;align-items:center;gap:10px;height:52px;padding:0 14px;border-bottom:1px solid var(--line)}
.logo{font-size:18px;font-weight:800;letter-spacing:.02em;text-decoration:none;
background:linear-gradient(90deg,var(--a1),var(--a2));-webkit-background-clip:text;background-clip:text;color:transparent}
.pill,.btn{display:inline-block;border-radius:999px;font-weight:700;text-decoration:none;border:0;cursor:pointer;
font:inherit;font-weight:700;background:linear-gradient(90deg,var(--a1),var(--a2));color:#1a0f14}
.pill{margin-left:auto;padding:6px 12px;font-size:13px}
.btn{padding:12px 24px;font-size:15px}
main{max-width:560px;margin:0 auto;padding:18px 14px 40px}
h1{margin:0 0 6px;font-size:20px;line-height:1.3;overflow-wrap:anywhere}
.meta{margin:0 0 14px;color:var(--dim);font-size:13px}
.player{position:relative;width:min(100%,360px,calc((100vh - 250px)*9/16));min-width:200px;aspect-ratio:9/16;margin:0 auto 16px;
background:#000 center/cover no-repeat;border-radius:14px;overflow:hidden;display:flex;align-items:center;justify-content:center}
.player iframe{position:absolute;inset:0;width:100%;height:100%;border:0}
.player.ig iframe{background:#fff}
.gate{position:relative;padding:16px;text-align:center;background:rgba(12,10,18,.78);border-radius:12px;margin:12px}
.gate small{display:block;margin-top:10px;color:var(--dim);font-size:12px;line-height:1.4}
.next{text-align:center;margin:0 0 26px}
h2{margin:0 0 10px;font-size:16px}
ul,ol{margin:0;padding:0;list-style:none}
li{border-top:1px solid var(--line)}
li a{display:flex;gap:12px;align-items:center;padding:10px 0;color:var(--text);text-decoration:none}
li img{width:54px;height:96px;object-fit:cover;border-radius:8px;flex:none;background:var(--card)}
li b{display:block;font-weight:600;overflow-wrap:anywhere}
li span{color:var(--dim);font-size:13px}
.rank{flex:none;width:26px;font-size:20px;font-weight:800;color:var(--a2);text-align:center}
footer{margin-top:28px;color:#6f6680;font-size:12px;text-align:center}
footer a{color:var(--dim)}
"""

HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} | {site}</title>
<meta name="description" content="{desc}">
<meta name="theme-color" content="#0c0a12">
<link rel="canonical" href="{url}">
<meta property="og:type" content="{og_type}">
<meta property="og:site_name" content="{site}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{image}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:image" content="{image}">
<script>if(location.hash==="{mark}")location.replace({target});</script>
{extra_head}<style>{style}</style>
</head>
<body>
<header>
<a class="logo" href="{root}">{site}</a>
{telegram}</header>
<main>
"""

FOOT = """<footer>
Unofficial fan project. Not affiliated with Rockstar Games or Take-Two Interactive.
All clips belong to their creators and are played through the platforms' official embedded players.<br>
<a href="{root}privacy.html">Privacy &amp; cookies</a>
</footer>
</main>
{beacon}</body>
</html>
"""

# Плеер на странице ролика. Как и в ленте, сторонний плеер загружается только
# после согласия зрителя (оно общее с лентой, хранится в том же ключе).
PLAYER_JS = """<script>
(function(){
var KEY='gta6feed.consent',box=document.getElementById('player'),gate=document.getElementById('gate'),ok=false;
if(!box||!box.dataset.src)return;
try{ok=localStorage.getItem(KEY)==='1';}catch(e){}
function play(){
if(gate)gate.remove();
var f=document.createElement('iframe');
f.src=box.dataset.src;f.title=box.dataset.title||'Video';
f.allow='autoplay; encrypted-media; picture-in-picture; fullscreen; web-share';
f.allowFullscreen=true;f.referrerPolicy='strict-origin-when-cross-origin';
box.appendChild(f);
}
if(ok){play();return;}
document.getElementById('watch').addEventListener('click',function(){
try{localStorage.setItem(KEY,'1');}catch(e){}
play();
});
})();
</script>
"""

e = lambda s: html.escape(str(s), quote=True)


def clean_title(item):
    """Название без хвоста из хештегов: так оно читается и в поиске, и в подборке."""
    raw = (item.get("title") or "").strip()
    text = re.sub(r"\s+", " ", re.sub(r"#\S+", " ", raw)).strip(" -|·")
    return (text or raw)[:120] or "GTA 6 clip"


def image_url(item):
    if item["platform"] == "yt":
        return f"https://i.ytimg.com/vi/{item['id']}/hqdefault.jpg"
    return cfg.SITE_URL + "og.png"


def embed_url(item):
    """Адрес встроенного плеера. Те же правила, что в docs/index.html (embedUrl)."""
    p, i = item["platform"], item["id"]
    if p == "yt":
        return f"https://www.youtube.com/embed/{i}?playsinline=1&rel=0&autoplay=1&mute=1"
    if p == "tt":
        return f"https://www.tiktok.com/player/v1/{i}?rel=0&description=0&music_info=0&autoplay=1"
    if p == "ig":
        return f"https://www.instagram.com/reel/{i}/embed/"
    if p == "tg" and re.fullmatch(r"[A-Za-z]\w{3,31}/\d+", i):
        return f"https://t.me/{i}?embed=1&dark=1"
    return ""


def page_url(item):
    return f"{cfg.SITE_URL}c/{links.slug(item)}/"


def share_url(item):
    """Ссылка для людей: превью берётся со страницы ролика, а открывается лента."""
    return page_url(item) + FEED_MARK


def _script_json(data):
    """JSON для вставки внутрь <script>: без символов, которыми можно закрыть тег."""
    text = json.dumps(data, ensure_ascii=False)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _telegram_pill():
    if not cfg.TELEGRAM_URL:
        return ""
    return f'<a class="pill" href="{e(cfg.TELEGRAM_URL)}" target="_blank" rel="noopener">Telegram</a>\n'


def _beacon():
    token = getattr(cfg, "CF_BEACON_TOKEN", "")
    if not token:
        return ""
    return ("<script type='module' src='https://static.cloudflareinsights.com/beacon.min.js' "
            f"data-cf-beacon='{e(json.dumps({'token': token}))}'></script>\n")


def _byline(item):
    parts = [links.NAMES.get(item["platform"], "")]
    if item.get("author"):
        parts.append(item["author"].strip())
    return " · ".join(p for p in parts if p)


def _video_markup(item, title, desc):
    """Разметка schema.org для поисковиков. Только там, где есть свой кадр ролика."""
    if item["platform"] != "yt" or not item.get("published_at"):
        return ""
    data = {"@context": "https://schema.org", "@type": "VideoObject",
            "name": title, "description": desc,
            "thumbnailUrl": [image_url(item)],
            "uploadDate": item["published_at"],
            "embedUrl": f"https://www.youtube.com/embed/{item['id']}"}
    if item.get("duration"):
        data["duration"] = f"PT{int(item['duration'])}S"
    return f'<script type="application/ld+json">{_script_json(data)}</script>\n'


def _row(item, root, rank=None, views=False):
    """Строка списка роликов: кадр, название, автор."""
    thumb = (f'<img src="{e(image_url(item))}" alt="" loading="lazy" width="54" height="96">'
             if item["platform"] == "yt" else "")
    note = _byline(item)
    if views and item.get("views"):
        note += f" · {links.fmt_views(item['views'])} views"
    num = f'<span class="rank">{rank}</span>' if rank else ""
    return (f'<li><a href="{e(root)}c/{e(links.slug(item))}/">{num}{thumb}'
            f'<div><b>{e(clean_title(item))}</b><span>{e(note)}</span></div></a></li>\n')


def render(item, more=()):
    """Страница ролика. more: соседние ролики, ссылки на которые встанут под плеером."""
    title = clean_title(item)
    who = (item.get("author") or "").strip()
    length = f"{int(item['duration'])}-second " if item.get("duration") else ""
    desc = (f"{title}. A {length}GTA 6 clip" + (f" by {who}" if who else "")
            + f" on {links.NAMES.get(item['platform'], 'the web')}. Watch it on {cfg.SITE_TITLE}, "
              "a feed of short funny GTA 6 moments.")
    slug = links.slug(item)
    root = "../../"
    out = [HEAD.format(
        title=e(title), site=e(cfg.SITE_TITLE), desc=e(desc), url=e(page_url(item)),
        image=e(image_url(item)), og_type="video.other", mark=FEED_MARK,
        target=_script_json(f"{root}#{slug}"), extra_head=_video_markup(item, title, desc),
        style=STYLE, root=root, telegram=_telegram_pill())]
    out.append(f"<h1>{e(title)}</h1>\n")
    source = (f'<a href="{e(item["url"])}" target="_blank" rel="noopener">'
              f'{e(links.NAMES.get(item["platform"], "Source"))}</a>'
              if str(item.get("url", "")).startswith("https://") else "")
    out.append('<p class="meta">' + " · ".join(p for p in (source, e(who)) if p) + "</p>\n")
    src = embed_url(item)
    poster = (f' style="background-image:url({e(image_url(item))})"'
              if item["platform"] == "yt" else "")
    out.append(f'<div class="player {e(item["platform"])}" id="player" data-src="{e(src)}" '
               f'data-title="{e(title)}"{poster}>\n'
               '<div class="gate" id="gate"><button type="button" class="btn" id="watch">Watch clip</button>'
               f'<small>Played by the official {e(links.NAMES.get(item["platform"], ""))} player, '
               'which sets its own cookies. '
               f'<a href="{root}privacy.html">Privacy &amp; cookies</a></small></div>\n</div>\n')
    out.append(f'<p class="next"><a class="btn" href="{root}#next:{e(slug)}">Next clip ▼</a></p>\n')
    if more:
        out.append("<h2>More GTA 6 clips</h2>\n<ul>\n")
        out += [_row(m, root) for m in more]
        out.append("</ul>\n")
    out.append(PLAYER_JS)
    out.append(FOOT.format(root=root, beacon=_beacon()))
    return "".join(out)


def render_top(items, day):
    """Страница лучших роликов за сутки. day: дата подборки, строка ГГГГ-ММ-ДД."""
    root = "../"
    title = f"Top {len(items)} GTA 6 clips today" if items else "Top GTA 6 clips today"
    desc = ("The most watched short GTA 6 clips of the last 24 hours: funny moments, glitches, "
            "stunts and NPC chaos. Updated every day.")
    out = [HEAD.format(
        title=e(title), site=e(cfg.SITE_TITLE), desc=e(desc), url=e(cfg.SITE_URL + "top/"),
        image=e(cfg.SITE_URL + "og.png"), og_type="website", mark=FEED_MARK,
        target=_script_json(f"{root}#top"), extra_head="", style=STYLE, root=root,
        telegram=_telegram_pill())]
    out.append(f"<h1>{e(title)}</h1>\n")
    if items:
        out.append(f'<p class="meta">Most watched in the last 24 hours · updated {e(day)}</p>\n')
        out.append(f'<p class="next"><a class="btn" href="{root}#top">Play all ▶</a></p>\n<ol>\n')
        out += [_row(i, root, rank=n, views=True) for n, i in enumerate(items, 1)]
        out.append("</ol>\n")
    else:
        out.append('<p class="meta">The first daily top is on its way. Meanwhile, the feed is open.</p>\n'
                   f'<p class="next"><a class="btn" href="{root}">Open the feed</a></p>\n')
    out.append(FOOT.format(root=root, beacon=_beacon()))
    return "".join(out)


def _write(path, text):
    """Записать файл, только если содержимое изменилось. -> изменился ли."""
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True


def sitemap(published, top_day=""):
    """published: от новых к старым."""
    rows = [(cfg.SITE_URL, published[0]["posted_at"][:10] if published else ""),
            (cfg.SITE_URL + "top/", top_day)]
    rows += [(page_url(i), i["posted_at"][:10]) for i in published]
    body = "".join(f"<url><loc>{e(u)}</loc>" + (f"<lastmod>{e(d)}</lastmod>" if d else "") + "</url>\n"
                   for u, d in rows)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + body + "</urlset>\n")


def build(published, top=(), top_day=""):
    """Привести страницы в соответствие с лентой: создать новые, убрать лишние.

    published: ролики ленты от новых к старым. top: ролики подборки по местам.
    """
    if not cfg.SITE_URL:
        return
    wanted = {links.slug(i): i for i in published}
    os.makedirs(store.CLIPS_DIR, exist_ok=True)
    for name in os.listdir(store.CLIPS_DIR):
        if name not in wanted:
            shutil.rmtree(os.path.join(store.CLIPS_DIR, name), ignore_errors=True)
    for n, item in enumerate(published):
        # Соседи: ролики, вышедшие прямо перед этим. Они уже не меняются, поэтому
        # с выходом каждого нового ролика не приходится переписывать все страницы.
        _write(os.path.join(store.CLIPS_DIR, links.slug(item), "index.html"),
               render(item, published[n + 1:n + 1 + MORE_CLIPS]))
    _write(os.path.join(store.TOP_DIR, "index.html"), render_top(list(top), top_day))
    _write(store.SITEMAP_PATH, sitemap(published, top_day))
