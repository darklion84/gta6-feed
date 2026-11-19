"""Telegram Bot API: посты в канал и приём ссылок от админа в личке бота."""
import html
import os

import config as cfg
from . import links, net


def token():
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def channel():
    return os.environ.get("TELEGRAM_CHANNEL", "").strip()


def admin_ids():
    raw = os.environ.get("TELEGRAM_ADMIN_IDS", "")
    return {int(x) for x in raw.replace(";", ",").split(",") if x.strip().lstrip("-").isdigit()}


def enabled():
    return bool(token() and channel())


def _call(method, payload):
    return net.post_json("telegram", f"https://api.telegram.org/bot{token()}/{method}", payload)


def fmt_views(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.1f}K".replace(".0K", "K")
    return str(n)


def site_link(item):
    if not cfg.SITE_URL:
        return ""
    return f"{cfg.SITE_URL}#{item['platform']}-{item['id']}"


def caption(item):
    title = (item.get("title") or "").strip()[:200] or "GTA 6 clip"
    lines = [f"🎬 <b>{html.escape(title)}</b>"]
    meta = []
    if item.get("author"):
        meta.append("👤 " + html.escape(item["author"]))
    if item.get("views"):
        meta.append("👁 " + fmt_views(item["views"]))
    if item.get("duration"):
        d = item["duration"]
        meta.append(f"⏱ {d // 60}:{d % 60:02d}")
    if meta:
        lines.append(" · ".join(meta))
    refs = [f'<a href="{html.escape(item["url"])}">{links.NAMES[item["platform"]]}</a>']
    if site_link(item):
        refs.append(f'<a href="{html.escape(site_link(item))}">watch in the feed</a>')
    lines.append(" · ".join(refs))
    return "\n".join(lines)


def post(item):
    """Пост в канал. Превью со встроенным плеером Telegram строит сам по ссылке."""
    _call("sendMessage", {
        "chat_id": channel(),
        "text": caption(item),
        "parse_mode": "HTML",
        "link_preview_options": {"url": item["url"], "prefer_large_media": True},
    })


def reply(chat_id, text):
    try:
        _call("sendMessage", {"chat_id": chat_id, "text": text,
                              "link_preview_options": {"is_disabled": True}})
    except net.ApiError:
        pass


def inbox(offset):
    """Новые личные сообщения боту от админов -> ([(chat_id, text)], новый offset)."""
    data = _call("getUpdates", {"offset": offset, "timeout": 0,
                                "allowed_updates": ["message"]})
    out, admins = [], admin_ids()
    for upd in data.get("result", []):
        offset = max(offset, upd["update_id"] + 1)
        msg = upd.get("message") or {}
        sender = (msg.get("from") or {}).get("id")
        text = msg.get("text") or msg.get("caption") or ""
        if sender in admins and text:
            out.append((msg["chat"]["id"], text))
    return out, offset
