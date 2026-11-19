"""Telegram Bot API: посты в канал, приём ссылок и модерация в личке бота."""
import html
import os

import config as cfg
from . import links, net, pages


def token():
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def channel():
    return os.environ.get("TELEGRAM_CHANNEL", "").strip() or cfg.TELEGRAM_CHANNEL


def admin_ids():
    raw = os.environ.get("TELEGRAM_ADMIN_IDS", "")
    return {int(x) for x in raw.replace(";", ",").split(",") if x.strip().lstrip("-").isdigit()}


def enabled():
    return bool(token() and channel())


def _call(method, payload):
    return net.post_json("telegram", f"https://api.telegram.org/bot{token()}/{method}", payload)


fmt_views = links.fmt_views


def site_link(item):
    if not cfg.SITE_URL:
        return ""
    return pages.share_url(item)   # превью берётся со страницы ролика, а открывается лента на нём


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


def _preview(item):
    return {"url": item["url"], "prefer_large_media": True}


def post(item):
    """Пост в канал.

    Пересланное админом сообщение уходит в канал штатной пересылкой Telegram
    (с подписью "переслано из ..."), всё остальное: ссылкой, по которой
    Telegram сам строит превью со встроенным плеером.
    """
    if item.get("fwd"):
        try:
            _call("forwardMessage", {"chat_id": channel(), "from_chat_id": item["fwd"][0],
                                     "message_id": item["fwd"][1]})
            return
        except net.ApiError as e:
            if e.status != 400 or not item.get("url"):
                raise   # источник запретил пересылку, а ссылки на пост нет
    _call("sendMessage", {
        "chat_id": channel(), "text": caption(item), "parse_mode": "HTML",
        "link_preview_options": _preview(item),
    })


def top_text(items):
    """Текст ежедневной подборки: места, названия со ссылками в ленту, просмотры."""
    lines = [f"🔥 <b>Top {len(items)} GTA 6 clips of the day</b>", ""]
    for n, item in enumerate(items, 1):
        title = html.escape(pages.clean_title(item)[:70])
        lines.append(f'{n}. <a href="{html.escape(site_link(item))}">{title}</a>')
        meta = []
        if item.get("author"):
            meta.append("👤 " + html.escape(item["author"]))
        if item.get("views"):
            meta.append("👁 " + fmt_views(item["views"]))
        if meta:
            lines.append("    " + " · ".join(meta))
    lines += ["", f'▶️ <a href="{html.escape(cfg.SITE_URL)}top/{pages.FEED_MARK}">Watch them all in a row</a>']
    return "\n".join(lines)


def post_top(items):
    """Подборка лучших роликов за сутки одним постом в канал."""
    _call("sendMessage", {
        "chat_id": channel(), "text": top_text(items), "parse_mode": "HTML",
        "link_preview_options": {"url": cfg.SITE_URL + "top/", "prefer_large_media": True},
    })


def reply(chat_id, text):
    try:
        _call("sendMessage", {"chat_id": chat_id, "text": text,
                              "link_preview_options": {"is_disabled": True}})
    except net.ApiError:
        pass


# --- модерация ----------------------------------------------------------------

def send_candidate(chat_id, item, note=""):
    """Кандидат админу в личку с кнопками. -> message_id или None.

    Текст сообщения совпадает с будущим постом буква в букву: обработчик на
    Cloudflare публикует в канал именно то, что видит админ. Служебная
    статистика поэтому живёт не в тексте, а на отдельной кнопке-подписи.
    """
    rows = [[
        {"text": "✅ В канал", "callback_data": f"ok|{item['key']}"},
        {"text": "❌ Пропустить", "callback_data": f"no|{item['key']}"},
    ]]
    if note:
        rows.append([{"text": note, "callback_data": "noop"}])
    res = _call("sendMessage", {
        "chat_id": chat_id, "text": caption(item), "parse_mode": "HTML",
        "link_preview_options": _preview(item),
        "reply_markup": {"inline_keyboard": rows},
    })
    return (res.get("result") or {}).get("message_id")


def mark(item, note):
    """Убрать кнопки с сообщений-кандидатов и дописать итог."""
    for chat_id, message_id in item.get("mod_msgs", []):
        try:
            _call("editMessageText", {
                "chat_id": chat_id, "message_id": message_id,
                "text": caption(item) + f"\n\n{html.escape(note)}",
                "parse_mode": "HTML", "link_preview_options": _preview(item),
                "reply_markup": {"inline_keyboard": []},
            })
        except net.ApiError:
            pass


def delete(chat_id, message_id):
    """Удалить сообщение в личке с админом (своё или его). Молча, если уже удалено."""
    if not message_id:
        return
    try:
        _call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
    except net.ApiError:
        pass


def clear(item, note):
    """Убрать сообщения-кандидаты из лички админа: решение принято.

    Если удалить не вышло (например, сообщению больше 48 часов), хотя бы
    снимаем кнопки и дописываем итог.
    """
    for chat_id, message_id in item.get("mod_msgs", []):
        try:
            _call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
        except net.ApiError:
            mark({**item, "mod_msgs": [[chat_id, message_id]]}, note)
    item["mod_msgs"] = []


def answer(callback_id, text):
    """Всплывающий ответ на нажатие. На старые нажатия Telegram отвечает ошибкой, это не страшно."""
    try:
        _call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})
    except net.ApiError:
        pass


def _urls(msg):
    """Все ссылки сообщения: в тексте, под словами-гиперссылками и в превью."""
    out = []
    for text_key, ent_key in (("text", "entities"), ("caption", "caption_entities")):
        text = msg.get(text_key) or ""
        raw = text.encode("utf-16-le")      # смещения в entities считаются в UTF-16
        for e in msg.get(ent_key) or []:
            if e.get("type") == "text_link" and e.get("url"):
                out.append(e["url"])
            elif e.get("type") == "url":
                u = raw[e["offset"] * 2:(e["offset"] + e["length"]) * 2].decode("utf-16-le", "ignore")
                out.append(u if u.startswith("http") else "https://" + u)
        out += links.URL.findall(text)
    preview = (msg.get("link_preview_options") or {}).get("url")
    if preview:
        out.append(preview)
    return list(dict.fromkeys(u.rstrip(").,") for u in out))


def _forward(msg):
    """Откуда переслано: {"username", "post_id", "title"} или None, если это не пересылка."""
    origin = msg.get("forward_origin")
    if not origin:
        return None
    chat = origin.get("chat") or origin.get("sender_chat") or {}
    user = origin.get("sender_user") or {}
    public = origin.get("type") == "channel" and chat.get("username")
    return {"username": chat.get("username") if public else None,
            "post_id": origin.get("message_id") if public else None,
            "title": chat.get("title") or user.get("first_name")
                     or origin.get("sender_user_name") or ""}


def parse_message(msg):
    """Сообщение Telegram -> {"chat_id", "message_id", "text", "urls", "forward", "has_video"}.

    None, если оно не от админа: чужие сообщения молча отбрасываются.
    """
    if not msg or (msg.get("from") or {}).get("id") not in admin_ids():
        return None
    return {
        "chat_id": msg["chat"]["id"], "message_id": msg.get("message_id"),
        "text": msg.get("text") or msg.get("caption") or "",
        "urls": _urls(msg), "forward": _forward(msg),
        "has_video": any(k in msg for k in ("video", "animation", "video_note")),
    }


def updates(offset):
    """Опрос Telegram (когда нет обработчика на Cloudflare).

    -> (сообщения, нажатия [(id, data)], новый offset). Только от админов.
    """
    data = _call("getUpdates", {"offset": offset, "timeout": 0,
                                "allowed_updates": ["message", "callback_query"]})
    msgs, taps, admins = [], [], admin_ids()
    for upd in data.get("result", []):
        offset = max(offset, upd["update_id"] + 1)
        parsed = parse_message(upd.get("message"))
        if parsed:
            msgs.append(parsed)
        cb = upd.get("callback_query") or {}
        if (cb.get("from") or {}).get("id") in admins and cb.get("data"):
            taps.append((cb["id"], cb["data"]))
    return msgs, taps, offset
