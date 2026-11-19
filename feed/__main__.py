"""Один проход сборщика: python -m feed

1. принять ссылки, присланные вручную (бот в Telegram, links.txt)
2. найти новые ролики на YouTube
3. обновить просмотры у кандидатов
4. опубликовать лучших (без дублей) в канал
5. пересобрать docs/feed.json для сайта
"""
import logging
import os
import re
from datetime import timedelta

import config as cfg
from . import dedup, links, net, store, telegram, youtube

log = logging.getLogger("feed")
RELEVANT = [re.compile(p, re.I) for p in cfg.MUST_MATCH]


# --- отбор -------------------------------------------------------------------

def is_relevant(v):
    text = " ".join([v.get("title", ""), v.get("description", "")[:500],
                     " ".join(v.get("tags", []))])
    if not any(p.search(text) for p in RELEVANT):
        return False
    title = v.get("title", "").lower()
    return not any(w in title for w in cfg.STOP_WORDS)


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
    if item.get("source") == "manual":
        return True
    views = item.get("views", 0)
    if views >= cfg.POPULAR_MIN_VIEWS:
        return True
    return (store.hours_since(item["published_at"], t) <= cfg.FRESH_MAX_AGE_HOURS
            and views >= cfg.FRESH_MIN_VIEWS
            and velocity(item, t) >= cfg.FRESH_MIN_VELOCITY)


def yt_item(v, t, source="search"):
    return {
        "key": f"yt:{v['id']}", "platform": "yt", "id": v["id"],
        "url": f"https://www.youtube.com/shorts/{v['id']}",
        "title": v["title"], "author": v["author"], "channel_id": v["channel_id"],
        "published_at": v["published_at"], "duration": v["duration"],
        "views": v["views"], "likes": v["likes"],
        "status": "new", "source": source, "found_at": store.iso(t),
    }


# --- 1. ручные ссылки ---------------------------------------------------------

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


def add_manual(db, t, url, title):
    """-> текст ответа админу."""
    parsed = links.parse(url)
    if not parsed:
        return "Не распознал ссылку. Понимаю YouTube, TikTok и Instagram."
    key = links.key(parsed)
    old = db["items"].get(key)
    if old and old["status"] in ("published", "new"):
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
    db["items"][key] = item
    return f"Добавил в очередь: {links.NAMES[parsed['platform']]} {parsed['id']}"


def collect_manual(db, t):
    # links.txt: каждую строку берём один раз, обработанные помним в state
    in_file = read_links_file()
    done = set(db["state"].get("links_done", []))
    entries = [(None, u, title) for u, title in in_file if u not in done]
    db["state"]["links_done"] = sorted({u for u, _ in in_file})
    if telegram.token() and telegram.admin_ids():
        try:
            msgs, offset = telegram.inbox(db["state"].get("tg_offset", 0))
            db["state"]["tg_offset"] = offset
            for chat_id, text in msgs:
                found = links.split_text(text)
                if not found:
                    telegram.reply(chat_id, "Пришлите ссылку на ролик, после неё можно дописать подпись.")
                entries += [(chat_id, u, title) for u, title in found]
        except net.ApiError as e:
            log.warning("входящие Telegram: %s", e)
    for chat_id, url, title in entries:
        answer = add_manual(db, t, url, title)
        log.info("ручная ссылка: %s", answer)
        if chat_id is not None:
            telegram.reply(chat_id, answer)


# --- 2. поиск на YouTube -------------------------------------------------------

def collect_youtube(db, t):
    """-> множество ключей, добавленных в этом проходе."""
    added = set()
    if not youtube.api_key():
        log.info("YOUTUBE_API_KEY не задан, поиск пропущен")
        return added
    st = db["state"]
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
        if (i["status"] == "new" and i.get("source") != "manual"
                and store.hours_since(i["published_at"], t) > cfg.SEARCH_WINDOW_HOURS):
            i["status"] = "expired"


# --- 4. публикация ---------------------------------------------------------------

def publish(db, t):
    items = db["items"]
    published = [i for i in items.values() if i["status"] == "published"]
    recent = [i for i in published
              if store.hours_since(i["posted_at"], t) <= cfg.DEDUP_WINDOW_HOURS]
    cands = [i for i in items.values() if i["status"] == "new" and qualifies(i, t)]
    cands.sort(key=lambda i: (i.get("source") != "manual", -velocity(i, t)))
    posted = 0
    for item in cands:
        if posted >= cfg.MAX_POSTS_PER_RUN:
            break
        if item["status"] != "new":
            continue  # уже помечен дублем чуть выше в этом же проходе
        manual = item.get("source") == "manual"
        if not manual:
            # если в очереди сразу несколько копий одного ролика, берём самую раннюю (оригинал)
            twins = [c for c in cands if c is not item and c["status"] == "new"
                     and c.get("source") != "manual" and dedup.is_duplicate(item, c)]
            if twins:
                original = min([item] + twins, key=lambda c: c["published_at"])
                for c in [item] + twins:
                    if c is not original:
                        c["status"], c["dup_of"] = "duplicate", original["key"]
                item = original
        if not manual and item.get("channel_id"):
            same = sum(1 for p in published
                       if p.get("channel_id") == item["channel_id"]
                       and store.hours_since(p["posted_at"], t) <= 24)
            if same >= cfg.MAX_PER_CHANNEL_PER_DAY:
                continue
        if item["platform"] == "yt" and not item.get("thumb_hash"):
            data = net.get_bytes("ytimg", youtube.thumbnail_url(item["id"]))
            item["thumb_hash"] = dedup.dhash(data) if data else None
        dup = None if manual else dedup.find_duplicate(item, recent)
        if dup:
            item["status"], item["dup_of"] = "duplicate", dup
            log.info("дубль: %s = %s", item["key"], dup)
            continue
        if telegram.enabled():
            try:
                telegram.post(item)
            except net.ApiError as e:
                log.warning("пост не отправлен (%s): %s", item["key"], e)
                if e.status == 429:
                    break
                continue
        item["status"], item["posted_at"] = "published", store.iso(t)
        published.append(item)
        recent.append(item)
        posted += 1
        log.info("опубликован: %s %s", item["key"], item.get("title", "")[:60])
    return posted


# --- 5. уборка и выдача сайту ------------------------------------------------------

def prune(db, t):
    old = [k for k, i in db["items"].items()
           if store.hours_since(i["found_at"], t) > cfg.KEEP_DAYS * 24]
    for k in old:
        del db["items"][k]


def build_feed(db, t):
    pub = sorted((i for i in db["items"].values() if i["status"] == "published"),
                 key=lambda i: i["posted_at"], reverse=True)[:cfg.FEED_SIZE]
    fields = ("platform", "id", "url", "title", "author", "views", "duration", "posted_at")
    store.write_json(store.FEED_PATH, {
        "title": cfg.SITE_TITLE, "tagline": cfg.SITE_TAGLINE,
        "telegram_url": cfg.TELEGRAM_URL, "updated_at": store.iso(t),
        "items": [{f: i.get(f) for f in fields} for i in pub],
    })
    return len(pub)


def run(t=None):
    t = t or store.now()
    db = store.load()
    collect_manual(db, t)
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
