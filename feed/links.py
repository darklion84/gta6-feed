"""Разбор ссылок на ролики: какая сеть, какой ID, какой адрес у плеера."""
import re

from . import net

NAMES = {"yt": "YouTube", "tt": "TikTok", "ig": "Instagram"}

_YT = re.compile(
    r"(?:youtube\.com/(?:shorts/|embed/|live/|watch\?(?:[^#\s]*&)?v=)|youtu\.be/)([\w-]{11})(?![\w-])")
_TT = re.compile(r"tiktok\.com/(@[\w.-]+)/video/(\d+)")
_TT_PLAYER = re.compile(r"tiktok\.com/player/v1/(\d+)")
_TT_SHORT = re.compile(r"https?://(?:(?:vm|vt)\.tiktok\.com/\w+|(?:www\.)?tiktok\.com/t/\w+)")
_IG = re.compile(r"instagram\.com/(?:[\w.]+/)?(?:reels?|p|tv)/([\w-]+)")

URL = re.compile(r"https?://\S+")


def parse(url):
    """-> {"platform", "id", "url"} или None, если ссылка не распознана."""
    url = url.strip()
    m = _YT.search(url)
    if m:
        return {"platform": "yt", "id": m.group(1),
                "url": f"https://www.youtube.com/shorts/{m.group(1)}"}
    if _TT_SHORT.match(url):
        url = net.resolve(url) or url
    m = _TT.search(url)
    if m:
        return {"platform": "tt", "id": m.group(2),
                "url": f"https://www.tiktok.com/{m.group(1)}/video/{m.group(2)}"}
    m = _TT_PLAYER.search(url)
    if m:
        return {"platform": "tt", "id": m.group(1),
                "url": f"https://www.tiktok.com/@_/video/{m.group(1)}"}
    m = _IG.search(url)
    if m:
        return {"platform": "ig", "id": m.group(1),
                "url": f"https://www.instagram.com/reel/{m.group(1)}/"}
    return None


def key(parsed):
    return f"{parsed['platform']}:{parsed['id']}"


def split_text(text):
    """'ссылка подпись' -> [(ссылка, подпись)]. Подпись общая для всех ссылок."""
    urls = URL.findall(text or "")
    title = URL.sub(" ", text or "").strip(" |\n\t")
    return [(u.rstrip(").,"), title) for u in urls]
