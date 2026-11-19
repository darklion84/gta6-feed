"""Один проход сборщика: python -m feed

1. принять от админа ссылки и нажатия кнопок (бот в Telegram, links.txt)
2. найти новые ролики на YouTube
3. обновить просмотры у кандидатов
4. опубликовать одобренное; новых кандидатов отправить админу на модерацию
   (или сразу в канал, если MODERATION = False)
5. пересобрать docs/feed.json для сайта

Статусы записи: new -> pending -> approved -> published
                (а также rejected, duplicate, expired, failed)
"""
import json
import logging
import os
import re
from datetime import timedelta

import config as cfg
from . import dedup, links, net, store, telegram, youtube

log = logging.getLogger("feed")
RELEVANT = [re.compile(p, re.I) for p in cfg.MUST_MATCH]
HINTS = [re.compile(p, re.I) for p in cfg.TITLE_HINTS]


# --- отбор -------------------------------------------------------------------

def title_ok(title):
    """Название похоже на момент из игры и без стоп-слов."""
    low = (title or "").lower()
    if any(w in low for w in cfg.STOP_WORDS):
        return False
    return not HINTS or any(p.search(low) for p in HINTS)


def is_relevant(v):
    text = " ".join([v.get("title", ""), v.get("description", "")[:500],
                     " ".join(v.get("tags", []))])
    return any(p.search(text) for p in RELEVANT) and title_ok(v.get("title"))


def accept(v):
    return (cfg.MIN_DURATION <= v["duration"] <= cfg.MAX_DURATION
            and v.get("live", "none") == "none"
            and v.get("channel_id") not in cfg.BLOCK_CHANNELS
            and bool(v.get("published_at"))
            and is_relevant(v))


def velocity(item, t):
    age = max(store.hours_since(item["published_at"], t), 0.5)
    return item.get("views", 0) / age


def qualifies(item, t):
    views = item.get("views", 0)
    if views >= cfg.POPULAR_MIN_VIEWS:
        return True
    return (store.hours_since(item["published_at"], t) <= cfg.FRESH_MAX_AGE_HOURS
            and views >= cfg.FRESH_MIN_VIEWS
            and velocity(item, t) >= cfg.FRESH_MIN_VELOCITY)


def moderated():
    return bool(cfg.MODERATION and telegram.token() and telegram.admin_ids())


def yt_item(v, t, source="search"):
    return {
        "key": f"yt:{v['id']}", "platform": "yt", "id": v["id"],
        "url": f"https://www.youtube.com/shorts/{v['id']}",
        "title": v["title"], "author": v["author"], "channel_id": v["channel_id"],
        "published_at": v["published_at"], "duration": v["duration"],
        "views": v["views"], "likes": v["likes"],
        "status": "new", "source": source, "found_at": store.iso(t),
    }


# --- 1. входящие от админа ------------------------------------------------------

def read_links_file():
    if not os.path.exists(store.LINKS_PATH):
        return []
    out = []
    with open(store.LINKS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                out += links.split_text(line)
    return out


HELP = ("Пришлите ссылку на ролик (YouTube, TikTok, Instagram, пост публичного Telegram-канала), "
        "после неё можно дописать подпись. Или перешлите сюда пост с видео. "
        "Кандидаты из поиска будут приходить сюда с кнопками.")


def add_manual(db, t, parsed, title, extra=None):
    """Ролик, выбранный админом: сразу в очередь на публикацию. -> текст ответа."""
    key = links.key(parsed)
    old = db["items"].get(key)
    if old and old["status"] in ("published", "approved"):
        return "Этот ролик уже есть в ленте или в очереди."
    item = {**parsed, "key": key, "title": title, "author": "", "channel_id": "",
            "published_at": store.iso(t), "duration": 0, "views": 0, "likes": 0,
            "status": "new", "source": "manual", "found_at": store.iso(t)}
    try:
        if parsed["platform"] == "yt" and youtube.api_key():
            found = youtube.details([parsed["id"]])
            if found:
                item = {**yt_item(found[0], t, "manual"), "title": title or found[0]["title"]}
        elif parsed["platform"] == "tt":
            meta = net.get_json("tiktok", "https://www.tiktok.com/oembed", {"url": parsed["url"]})
            item["title"] = title or meta.get("title", "")
            item["author"] = meta.get("author_name", "")
    except (net.ApiError, ValueError):
        pass  # без метаданных тоже можно публиковать
    if old and old.get("mod_msgs"):
        telegram.clear(old, "➡️ Добавлен вручную")
    item.update(extra or {})
    db["items"][key] = item
    if parsed["platform"] == "tgx":
        return ("Добавил в очередь. Выйдет только в канале: источник закрытый, "
                "встроить его пост на сайт нельзя.")
    return f"Добавил в очередь: {links.NAMES[parsed['platform']]} {parsed['id']}"


def handle_message(db, t, m):
    """Сообщение админа боту: ссылки или пересланный пост. -> тексты ответов."""
    parsed = [p for p in (links.parse(u) for u in m["urls"]) if p]
    fwd = m["forward"]
    if not fwd:
        if not parsed:
            return ["Не распознал ссылку. " + HELP if m["urls"] else HELP]
        title = links.URL.sub(" ", m["text"]).strip(" |\n\t")
        return [add_manual(db, t, p, title) for p in parsed]
    # Пересылка. Сначала ищем в посте ссылку на первоисточник во внешней сети,
    # затем берём сам пост с видео, затем ссылку на другой пост Telegram.
    external = [p for p in parsed if p["platform"] != "tg"]
    if external:
        return [add_manual(db, t, external[0], "")]
    if m["has_video"]:
        if fwd["username"] and fwd["post_id"]:
            post = links.tg_post(fwd["username"], fwd["post_id"])
        else:
            post = {"platform": "tgx", "id": f"{m['chat_id']}_{m['message_id']}", "url": ""}
        lines = [s.strip() for s in links.URL.sub(" ", m["text"]).splitlines() if s.strip()]
        return [add_manual(db, t, post, lines[0][:120] if lines else "",
                           {"author": fwd["title"], "fwd": [m["chat_id"], m["message_id"]]})]
    if parsed:
        return [add_manual(db, t, parsed[0], "")]
    return ["В пересланном сообщении нет ни видео, ни ссылки на ролик."]


def handle_tap(db, t, callback_id, data):
    """Нажатие кнопки под кандидатом."""
    action, _, key = data.partition("|")
    item = db["items"].get(key)
    if not item or item.get("status") != "pending":
        telegram.answer(callback_id, "Уже обработано")
        if item:
            telegram.clear(item, "Уже обработано")
        return
    # Сообщение с кандидатом удаляется именно здесь: его исчезновение и есть
    # подтверждение админу, что нажатие дошло и учтено.
    if action == "ok":
        item["status"], item["approved_at"] = "approved", store.iso(t)
        telegram.answer(callback_id, "Принято, выйдет в канал")
        telegram.clear(item, "✅ Одобрен")
    else:
        item["status"] = "rejected"
        telegram.answer(callback_id, "Пропущен")
        telegram.clear(item, "❌ Пропущен")


def collect_inbox(db, t):
    # links.txt: каждую строку берём один раз, обработанные помним в state
    in_file = read_links_file()
    done = set(db["state"].get("links_done", []))
    for url, title in in_file:
        if url not in done:
            parsed = links.parse(url)
            log.info("links.txt: %s", add_manual(db, t, parsed, title) if parsed
                     else f"не распознал {url}")
    db["state"]["links_done"] = sorted({u for u, _ in in_file})
    if not (telegram.token() and telegram.admin_ids()):
        return
    try:
        msgs, taps, offset = telegram.updates(db["state"].get("tg_offset", 0))
    except net.ApiError as e:
        log.warning("входящие Telegram: %s", e)
        return
    db["state"]["tg_offset"] = offset
    for callback_id, data in taps:
        handle_tap(db, t, callback_id, data)
    for m in msgs:
        for answer in handle_message(db, t, m):
            log.info("от админа: %s", answer)
            telegram.reply(m["chat_id"], answer)


# --- 2. поиск на YouTube -------------------------------------------------------

def collect_youtube(db, t):
    """-> множество ключей, добавленных в этом проходе."""
    added = set()
    if not youtube.api_key():
        log.info("YOUTUBE_API_KEY не задан, поиск пропущен")
        return added
    st = db["state"]
    last = st.get("last_search_at")
    if last and store.hours_since(last, t) * 60 < cfg.SEARCH_INTERVAL_MIN - 2:
        return added      # сборщик запускается чаще, чем можно тратить поиски
    day = store.pacific_day(t)
    if st.get("search_day") != day:
        st["search_day"], st["search_count"] = day, 0
    after = store.iso(t - timedelta(hours=cfg.SEARCH_WINDOW_HOURS))
    orders = ("date", "viewCount")
    found = []
    for _ in range(cfg.SEARCHES_PER_RUN):
        if st["search_count"] >= cfg.SEARCH_DAILY_BUDGET:
            log.info("суточный лимит поисков исчерпан")
            break
        cur = st.get("cursor", 0)
        query = cfg.QUERIES[cur % len(cfg.QUERIES)]
        # порядок меняется на каждом круге, чтобы каждый запрос шёл и по дате, и по просмотрам
        order = orders[(cur + cur // len(cfg.QUERIES)) % 2]
        st["cursor"] = cur + 1
        st["search_count"] += 1
        st["last_search_at"] = store.iso(t)
        try:
            ids = youtube.search(query, order, after, cfg.YOUTUBE_CATEGORY, cfg.SEARCH_LANGUAGE)
            log.info("поиск %r (%s): %d", query, order, len(ids))
            found += ids
        except net.ApiError as e:
            log.warning("поиск не удался: %s", e)
            break
    fresh = [i for i in dict.fromkeys(found) if f"yt:{i}" not in db["items"]]
    if not fresh:
        return added
    try:
        vids = youtube.details(fresh)
    except net.ApiError as e:
        log.warning("детали не получены: %s", e)
        return added
    for v in vids:
        key = f"yt:{v['id']}"
        if accept(v):
            db["items"][key] = yt_item(v, t)
            added.add(key)
        else:
            db["items"][key] = {"key": key, "platform": "yt", "id": v["id"],
                                "status": "rejected", "found_at": store.iso(t)}
    log.info("новых кандидатов: %d из %d", len(added), len(vids))
    return added


# --- 3. обновление статистики --------------------------------------------------

def refresh_stats(db, t, skip):
    cands = [i for k, i in db["items"].items()
             if i["status"] == "new" and i["platform"] == "yt" and k not in skip]
    if cands and youtube.api_key():
        try:
            fresh = {v["id"]: v for v in youtube.details([i["id"] for i in cands])}
            for i in cands:
                v = fresh.get(i["id"])
                if v is None:
                    i["status"] = "expired"      # удалён или скрыт автором
                else:
                    i["views"], i["likes"] = v["views"], v["likes"]
        except net.ApiError as e:
            log.warning("статистика не обновлена: %s", e)
    for i in db["items"].values():
        if i["status"] == "pending":
            if store.hours_since(i["mod_sent_at"], t) > cfg.MOD_TIMEOUT_HOURS:
                i["status"] = "expired"
                telegram.clear(i, "⌛ Время вышло")
        elif i["status"] == "new" and i.get("source") != "manual":
            if store.hours_since(i["published_at"], t) > cfg.SEARCH_WINDOW_HOURS:
                i["status"] = "expired"
            elif not title_ok(i.get("title")):
                i["status"] = "rejected"     # фильтры в config.py ужесточили уже после находки


# --- 4. публикация и модерация ----------------------------------------------------

def post(item, t):
    """Пост в канал. -> 'ok' | 'skip' | 'stop'."""
    if telegram.enabled():
        try:
            telegram.post(item)
        except net.ApiError as e:
            log.warning("пост не отправлен (%s): %s", item["key"], e)
            if e.status in (401, 403, 429):
                return "stop"   # неверный токен, бот не админ канала или лимит
            item["post_fails"] = item.get("post_fails", 0) + 1
            if item.get("fwd") or item["post_fails"] >= 3:
                item["status"] = "failed"
                if item.get("fwd"):
                    telegram.reply(item["fwd"][0], "Не удалось переслать пост в канал: "
                                                   "похоже, источник запретил пересылку.")
                else:   # сообщение с кандидатом уже удалено, так что сообщаем отдельно
                    for chat_id in sorted(telegram.admin_ids()):
                        telegram.reply(chat_id, "⚠️ Не удалось опубликовать в канале: "
                                                f"{item.get('title') or item['url']}")
            return "skip"
    item["status"], item["posted_at"] = "published", store.iso(t)
    log.info("опубликован: %s %s", item["key"], item.get("title", "")[:60])
    return "ok"


def screen(item, cands, published, recent, t):
    """Отсев перед показом: копии, лимит на автора, дубли. -> запись или None."""
    # если в очереди сразу несколько копий одного ролика, берём самую раннюю (оригинал)
    twins = [c for c in cands if c is not item and c["status"] == "new"
             and dedup.is_duplicate(item, c)]
    if twins:
        original = min([item] + twins, key=lambda c: c["published_at"])
        for c in [item] + twins:
            if c is not original:
                c["status"], c["dup_of"] = "duplicate", original["key"]
        item = original
    if item.get("channel_id"):
        same = sum(1 for p in published
                   if p.get("channel_id") == item["channel_id"]
                   and store.hours_since(p["posted_at"], t) <= 24)
        if same >= cfg.MAX_PER_CHANNEL_PER_DAY:
            return None
    if item["platform"] == "yt" and not item.get("thumb_hash"):
        data = net.get_bytes("ytimg", youtube.thumbnail_url(item["id"]))
        item["thumb_hash"] = dedup.dhash(data) if data else None
    dup = dedup.find_duplicate(item, recent)
    if dup:
        item["status"], item["dup_of"] = "duplicate", dup
        log.info("дубль: %s = %s", item["key"], dup)
        return None
    return item


def send_to_admins(item, t):
    """Кандидат на модерацию. -> удалось ли отправить хоть кому-то."""
    age = store.hours_since(item["published_at"], t)
    note = f"⏳ {age:.0f} ч назад · 📈 {telegram.fmt_views(int(velocity(item, t)))}/ч"
    msgs = []
    for chat_id in sorted(telegram.admin_ids()):
        try:
            message_id = telegram.send_candidate(chat_id, item, note)
            if message_id:
                msgs.append([chat_id, message_id])
        except net.ApiError as e:
            log.warning("кандидат не отправлен админу: %s", e)
    if not msgs:
        return False
    item["status"], item["mod_msgs"], item["mod_sent_at"] = "pending", msgs, store.iso(t)
    log.info("на модерации: %s %s", item["key"], item.get("title", "")[:60])
    return True


def publish(db, t):
    items = db["items"]
    mod = moderated()
    published = [i for i in items.values() if i["status"] == "published"]
    recent = ([p for p in published
               if store.hours_since(p["posted_at"], t) <= cfg.DEDUP_WINDOW_HOURS]
              + [i for i in items.values() if i["status"] in ("pending", "approved")])
    posted = 0

    # 1. то, что уже выбрал человек: одобренные кандидаты и присланные вручную ссылки
    ready = [i for i in items.values()
             if i["status"] == "approved" or (i["status"] == "new" and i.get("source") == "manual")]
    ready.sort(key=lambda i: i.get("approved_at") or i["found_at"])
    for item in ready:
        if posted >= cfg.MAX_POSTS_PER_RUN:
            break
        result = post(item, t)
        if result == "stop":
            return posted
        if result == "ok":
            posted += 1
            published.append(item)
            if item not in recent:
                recent.append(item)

    # 2. кандидаты из поиска: на модерацию или, без неё, сразу в канал
    cands = [i for i in items.values()
             if i["status"] == "new" and i.get("source") != "manual" and qualifies(i, t)]
    cands.sort(key=lambda i: -velocity(i, t))
    waiting = sum(1 for i in items.values() if i["status"] == "pending")
    sent = 0
    for item in cands:
        if mod:
            if sent >= cfg.MOD_BATCH or waiting + sent >= cfg.MOD_MAX_PENDING:
                break
        elif posted >= cfg.MAX_POSTS_PER_RUN:
            break
        if item["status"] != "new":
            continue  # уже помечен дублем чуть выше в этом же проходе
        item = screen(item, cands, published, recent, t)
        if item is None:
            continue
        if mod:
            if not send_to_admins(item, t):
                break   # админ ещё не нажал Start у бота, либо Telegram недоступен
            sent += 1
            recent.append(item)
        else:
            result = post(item, t)
            if result == "stop":
                break
            if result == "ok":
                posted += 1
                published.append(item)
                recent.append(item)
    return posted


# --- 5. уборка и выдача сайту ------------------------------------------------------

def prune(db, t):
    old = [k for k, i in db["items"].items()
           if store.hours_since(i["found_at"], t) > cfg.KEEP_DAYS * 24]
    for k in old:
        del db["items"][k]


def build_feed(db, t):
    pub = sorted((i for i in db["items"].values()
                  if i["status"] == "published" and i["platform"] in links.SITE_PLATFORMS),
                 key=lambda i: (i["posted_at"], i["key"]), reverse=True)[:cfg.FEED_SIZE]
    fields = ("platform", "id", "url", "title", "author", "views", "duration", "posted_at")
    feed = {"title": cfg.SITE_TITLE, "tagline": cfg.SITE_TAGLINE,
            "telegram_url": cfg.TELEGRAM_URL,
            "items": [{f: i.get(f) for f in fields} for i in pub]}
    try:
        with open(store.FEED_PATH, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = None
    # файл трогаем, только когда лента реально изменилась: от этого зависит перевыкладка сайта
    if old is None or any(old.get(k) != v for k, v in feed.items()):
        store.write_json(store.FEED_PATH, {**feed, "updated_at": store.iso(t)})
    return len(pub)


def run(t=None):
    t = t or store.now()
    db = store.load()
    collect_inbox(db, t)
    added = collect_youtube(db, t)
    refresh_stats(db, t, added)
    posted = publish(db, t)
    prune(db, t)
    total = build_feed(db, t)
    store.save(db)
    log.info("готово: опубликовано %d, в ленте %d", posted, total)
    return posted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    run()
