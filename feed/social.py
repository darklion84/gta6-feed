"""Анонсы в других сетях: Bluesky и Threads.

Каждый ролик, вышедший в ленте, и подборка дня уходят туда короткой записью со
ссылкой на сайт. Сети включаются секретами репозитория:

  Bluesky   BLUESKY_HANDLE (например gta6hot.bsky.social) и BLUESKY_APP_PASSWORD
  Threads   THREADS_TOKEN (долгоживущий ключ из кабинета разработчика Meta)

Сбой в любой из сетей никогда не мешает основной работе: ошибка пишется в лог,
а запись повторится на следующем проходе.

Отметки о сделанном лежат в самом ролике: item["social"] = {"bsky": "done", ...}.
Значения: "done", "failed", {"fails": n} или, для Threads, {"cid": ..., "at": ...}:
там запись создаётся в два шага, и между ними нужно выждать (см. Threads).
"""
import base64
import hashlib
import logging
import os
import re
from datetime import timezone

import config as cfg
from . import links, net, pages, store, telegram

log = logging.getLogger("feed")

BSKY_API = "https://bsky.social/xrpc/"
THREADS_API = "https://graph.threads.com/"
BSKY_TEXT_LIMIT = 300        # знаков в записи Bluesky
THREADS_TEXT_LIMIT = 500
THREADS_WAIT_SECONDS = 30    # столько Threads просит подождать между созданием и публикацией
MAX_FAILS = 3


# --- что публикуем ------------------------------------------------------------------

def clip_content(item):
    title = pages.clean_title(item)
    who = (item.get("author") or "").strip()
    return {"text": title, "url": pages.share_url(item), "title": title,
            "desc": (f"Short GTA 6 clip by {who}. " if who else "Short GTA 6 clip. ")
                    + f"More in the feed on {cfg.SITE_TITLE}.",
            "image": pages.image_url(item)}


def top_content(items):
    lines = [f"🔥 Top {len(items)} GTA 6 clips of the day"]
    for n, item in enumerate(items, 1):
        line = f"{n}. {pages.clean_title(item)[:48]}"
        if len("\n".join(lines + [line])) > 220:
            break
        lines.append(line)
    return {"text": "\n".join(lines), "url": cfg.SITE_URL + "top/" + pages.FEED_MARK,
            "title": f"Top {len(items)} GTA 6 clips today",
            "desc": "The most watched short GTA 6 clips of the last 24 hours.",
            "image": cfg.SITE_URL + "og.png"}


def _with_tags(text, limit, tags):
    tail = " ".join("#" + t for t in tags)
    room = limit - len(tail) - 2
    if len(text) > room:
        text = text[:room - 1].rstrip() + "…"
    return f"{text}\n\n{tail}" if tail else text


def _pending(item):
    return any(isinstance(m, dict) and m.get("cid") for m in item.get("social", {}).values())


def jobs(db, t):
    """Что должно появиться в сетях: [(отметки, содержимое)], подборка дня первой."""
    out = []
    st = db["state"]
    day = t.astimezone(timezone.utc).strftime("%Y-%m-%d")
    if st.get("top_day") == day:
        best = [db["items"][k] for k in st.get("top", []) if k in db["items"]]
        share = st.get("top_share")
        if not share or share.get("day") != day:
            share = st["top_share"] = {"day": day, "social": {}}
        if best:
            out.append((share["social"], top_content(best)))
    fresh = []
    for i in db["items"].values():
        if i["status"] != "published" or i["platform"] not in links.SITE_PLATFORMS or not i.get("posted_at"):
            continue
        age = store.hours_since(i["posted_at"], t)
        # начатую в Threads запись доводим до конца, даже если ролик уже не свежий
        if age <= cfg.SOCIAL_WINDOW_HOURS or (_pending(i) and age <= 24):
            fresh.append(i)
    fresh.sort(key=lambda i: (i["posted_at"], i["key"]))
    out += [(i.setdefault("social", {}), clip_content(i)) for i in fresh]
    return out


def _failed(marks, name, e, what):
    """Запись не прошла по своей вине: считаем попытки и после третьей сдаёмся."""
    prev = marks.get(name)
    fails = (prev.get("fails", 0) if isinstance(prev, dict) else 0) + 1
    marks[name] = "failed" if fails >= MAX_FAILS else {"fails": fails}
    log.warning("%s: %s не прошла (%s)", name, what, e)


def _bad_key(e):
    """Сеть не приняла ключ доступа. У Meta это код 190 внутри ответа с HTTP 400."""
    return e.status == 401 or bool(re.search(r'"code"\s*:\s*190\b', e.body))


def _fatal(e):
    """Ошибка не из-за конкретной записи: ключ, лимит или сеть. Остальные тоже не пройдут."""
    return e.status in (0, 403, 429) or e.status >= 500 or _bad_key(e)


# --- Bluesky ------------------------------------------------------------------------

class Bluesky:
    name, title = "bsky", "Bluesky"
    secret_hint = "BLUESKY_HANDLE и BLUESKY_APP_PASSWORD"

    def __init__(self, db, t):
        self.handle = os.environ.get("BLUESKY_HANDLE", "").strip().lstrip("@")
        self.password = os.environ.get("BLUESKY_APP_PASSWORD", "").strip()
        self.auth = None

    def enabled(self):
        return bool(self.handle and self.password)

    def _login(self):
        if not self.auth:
            r = net.send("bluesky", BSKY_API + "com.atproto.server.createSession",
                         json={"identifier": self.handle, "password": self.password})
            self.auth = {"did": r["did"], "headers": {"Authorization": "Bearer " + r["accessJwt"]}}
        return self.auth

    @staticmethod
    def facets(text):
        """Хештеги становятся ссылками. Позиции считаются в байтах UTF-8."""
        out = []
        for m in re.finditer(r"#(\w+)", text):
            out.append({"index": {"byteStart": len(text[:m.start()].encode()),
                                  "byteEnd": len(text[:m.end()].encode())},
                        "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": m.group(1)}]})
        return out

    def _thumb(self, url):
        """Картинка для карточки ссылки. Без неё запись тоже выйдет."""
        data = net.get_bytes("image", url) if url else None
        if not data or len(data) > 950_000:
            return None
        kind = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
        try:
            return net.send("bluesky", BSKY_API + "com.atproto.repo.uploadBlob", body=data,
                            headers={**self._login()["headers"], "Content-Type": kind})["blob"]
        except (net.ApiError, KeyError):
            return None

    def post(self, c, t):
        auth = self._login()
        text = _with_tags(c["text"], BSKY_TEXT_LIMIT, cfg.SOCIAL_TAGS)
        card = {"uri": c["url"], "title": c["title"][:300], "description": c["desc"][:300]}
        thumb = self._thumb(c.get("image"))
        if thumb:
            card["thumb"] = thumb
        record = {"$type": "app.bsky.feed.post", "text": text, "langs": ["en"],
                  "createdAt": t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                  "facets": self.facets(text),
                  "embed": {"$type": "app.bsky.embed.external", "external": card}}
        net.send("bluesky", BSKY_API + "com.atproto.repo.createRecord", headers=auth["headers"],
                 json={"repo": auth["did"], "collection": "app.bsky.feed.post", "record": record})

    def run(self, todo, t):
        sent = 0
        for marks, c in todo:
            if marks.get(self.name) in ("done", "failed") or sent >= cfg.SOCIAL_MAX_PER_RUN:
                continue
            try:
                self.post(c, t)
            except net.ApiError as e:
                if _fatal(e):
                    raise
                _failed(marks, self.name, e, "запись")
                continue
            marks[self.name] = "done"
            sent += 1
        return sent


# --- Threads ------------------------------------------------------------------------

class Threads:
    """Запись в Threads создаётся в два шага: заготовка, затем публикация, и между
    ними сеть просит выждать около 30 секунд. Чтобы не стоять на месте, заготовка
    делается на одном проходе, а публикуется на следующем.

    Ключ доступа живёт 60 дней, но его можно продлевать. Раз в неделю сборщик
    продлевает его сам и хранит новый ключ в базе в зашифрованном виде (ключ
    шифрования выводится из токена бота, которого в репозитории нет).
    """
    name, title = "threads", "Threads"
    secret_hint = "THREADS_TOKEN"

    def __init__(self, db, t):
        self.env = os.environ.get("THREADS_TOKEN", "").strip()
        self.db, self.t = db, t

    def enabled(self):
        return bool(self.env)

    @property
    def st(self):
        st = self.db["state"].setdefault("threads", {})
        origin = hashlib.sha256(("fp:" + self.env).encode()).hexdigest()[:16]
        if st.get("origin") != origin:        # владелец положил новый ключ: начинаем с него
            st.clear()
            st.update(origin=origin, since=store.iso(self.t))
        return st

    @staticmethod
    def _cipher():
        secret = telegram.token()
        if not secret:
            return None
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            return None
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("threads:" + secret).encode()).digest()))

    def token(self):
        saved, box = self.st.get("token"), self._cipher()
        if saved and box:
            try:
                return box.decrypt(saved.encode()).decode()
            except Exception:                 # токен бота сменили: продлённый ключ не прочитать
                self.st.pop("token", None)
        return self.env

    def refresh(self):
        st, box = self.st, self._cipher()
        last = st.get("refresh_try") or st.get("refreshed_at") or st["since"]
        if not box or store.hours_since(last, self.t) < cfg.THREADS_REFRESH_DAYS * 24:
            return
        st["refresh_try"] = store.iso(self.t)
        try:
            r = net.get_json("threads", THREADS_API + "refresh_access_token",
                             {"grant_type": "th_refresh_token", "access_token": self.token()})
            st["token"] = box.encrypt(r["access_token"].encode()).decode()
            st["refreshed_at"] = store.iso(self.t)
            log.info("threads: ключ доступа продлён")
        except (net.ApiError, KeyError) as e:
            log.warning("threads: ключ продлить не удалось (%s)", e)

    def user_id(self):
        if not self.st.get("user_id"):
            r = net.get_json("threads", THREADS_API + "v1.0/me", {"fields": "id", "access_token": self.token()})
            self.st["user_id"] = str(r["id"])
        return self.st["user_id"]

    def create(self, c):
        text = _with_tags(c["text"], THREADS_TEXT_LIMIT, cfg.SOCIAL_TAGS[:1])   # в Threads у записи одна тема
        r = net.send("threads", f"{THREADS_API}v1.0/{self.user_id()}/threads",
                     data={"media_type": "TEXT", "text": text, "link_attachment": c["url"],
                           "access_token": self.token()})
        return str(r["id"])

    def publish(self, cid):
        net.send("threads", f"{THREADS_API}v1.0/{self.user_id()}/threads_publish",
                 data={"creation_id": cid, "access_token": self.token()})

    def run(self, todo, t):
        self.refresh()
        sent = created = 0
        for marks, c in todo:
            mark = marks.get(self.name)
            if mark in ("done", "failed"):
                continue
            waiting = isinstance(mark, dict) and mark.get("cid")
            if waiting and store.hours_since(mark["at"], t) * 3600 < THREADS_WAIT_SECONDS:
                continue
            if not waiting and created >= cfg.SOCIAL_MAX_PER_RUN:
                continue
            try:
                if waiting:
                    self.publish(mark["cid"])
                    marks[self.name] = "done"
                    sent += 1
                else:
                    fails = mark.get("fails", 0) if isinstance(mark, dict) else 0
                    marks[self.name] = {"cid": self.create(c), "at": store.iso(t), "fails": fails}
                    created += 1
            except net.ApiError as e:
                if _fatal(e):
                    raise
                _failed(marks, self.name, e, "публикация" if waiting else "заготовка")
        return sent


NETWORKS = (Bluesky, Threads)


def _alert(db, t, network, e):
    """Сеть не приняла ключ: один раз в сутки говорим об этом владельцу в личку бота."""
    if not _bad_key(e):
        return
    day = t.astimezone(timezone.utc).strftime("%Y-%m-%d")
    sent = db["state"].setdefault("social_alerts", {})
    if sent.get(network.name) == day:
        return
    sent[network.name] = day
    for chat_id in sorted(telegram.admin_ids()):
        telegram.reply(chat_id, f"⚠️ Автопостинг в {network.title} не работает: сеть не приняла ключ доступа. "
                                f"Проверьте секрет {network.secret_hint} в настройках репозитория.")


def crosspost(db, t):
    """-> сколько записей вышло в других сетях."""
    active = [n for n in (cls(db, t) for cls in NETWORKS) if n.enabled()]
    if not active:
        return 0
    todo, total = jobs(db, t), 0
    for network in active:
        try:
            total += network.run(todo, t)
        except net.ApiError as e:
            log.warning("%s: автопостинг остановлен до следующего прохода (%s)", network.name, e)
            _alert(db, t, network, e)
        except Exception as e:      # чужая сеть не должна ронять сборщик
            log.warning("%s: неожиданная ошибка %s", network.name, type(e).__name__)
    if total:
        log.info("в других сетях: %d", total)
    return total
