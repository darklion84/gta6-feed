"""YouTube Data API v3: поиск свежих коротких роликов и их статистика."""
import os
import re

from . import net

API = "https://www.googleapis.com/youtube/v3/"
_DUR = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def api_key():
    return os.environ.get("YOUTUBE_API_KEY", "").strip()


def parse_duration(s):
    """ISO 8601 ('PT1M5S') -> секунды."""
    m = _DUR.match(s or "")
    if not m:
        return 0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + se


def search(query, order, published_after, category="", language=""):
    """ID роликов по запросу. Один вызов = один поиск из суточного лимита."""
    params = {
        "part": "id", "type": "video", "q": query, "order": order,
        "publishedAfter": published_after, "videoDuration": "short",
        "maxResults": 50, "key": api_key(),
    }
    if category:
        params["videoCategoryId"] = category
    if language:
        params["relevanceLanguage"] = language
    data = net.get_json("youtube", API + "search", params)
    return [i["id"]["videoId"] for i in data.get("items", [])
            if i.get("id", {}).get("videoId")]


def details(ids):
    """Название, автор, длительность и просмотры пачками по 50 (1 единица квоты за пачку)."""
    out = []
    ids = list(ids)
    for n in range(0, len(ids), 50):
        data = net.get_json("youtube", API + "videos", {
            "part": "snippet,contentDetails,statistics",
            "id": ",".join(ids[n:n + 50]), "maxResults": 50, "key": api_key(),
        })
        for v in data.get("items", []):
            sn, st = v.get("snippet", {}), v.get("statistics", {})
            out.append({
                "id": v["id"],
                "title": sn.get("title", ""),
                "description": sn.get("description", ""),
                "tags": sn.get("tags", []),
                "author": sn.get("channelTitle", ""),
                "channel_id": sn.get("channelId", ""),
                "published_at": sn.get("publishedAt", ""),
                "live": sn.get("liveBroadcastContent", "none"),
                "lang": (sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "")[:2].lower(),
                "duration": parse_duration(v.get("contentDetails", {}).get("duration")),
                "views": int(st.get("viewCount", 0) or 0),
                "likes": int(st.get("likeCount", 0) or 0),
                "comments": int(st.get("commentCount", 0) or 0),
            })
    return out


def thumbnail_url(video_id):
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
