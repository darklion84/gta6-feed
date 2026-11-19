"""Офлайн-тесты: все сетевые вызовы подменены. Запуск: python -m unittest -v"""
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import config as cfg
from feed import __main__ as app
from feed import dedup, links, net, store, telegram, worker, youtube

T = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


def vid(id_, title, views, age_h, duration=40, channel="UC1", **kw):
    return {"id": id_, "snippet": {
                "title": title, "description": kw.get("description", ""),
                "channelTitle": "Автор " + channel, "channelId": channel,
                "publishedAt": store.iso(T - timedelta(hours=age_h)),
                "liveBroadcastContent": "none"},
            "contentDetails": {"duration": f"PT{duration}S"},
            "statistics": {"viewCount": str(views), "likeCount": "10", "commentCount": "3"}}


class Fake:
    """Подмена net.*: YouTube, Telegram, TikTok oEmbed."""

    def __init__(self):
        self.videos = {}
        self.search_ids = []
        self.searches = 0
        self.posts = []
        self.replies = []
        self.candidates = []
        self.edits = []
        self.deleted = []
        self.delete_fails = False
        self.answers = []
        self.updates = []
        self.polls = 0
        self.worker_events = []
        self.worker_down = False
        self.acks = []

    def get_json(self, service, url, params=None, headers=None):
        if url.endswith("/pull"):
            if self.worker_down:
                raise net.ApiError("worker", 0, "ConnectionError")
            assert headers["Authorization"] == "Bearer " + hashlib.sha256(b"sync:t").hexdigest()
            self.acks.append(params["ack"])
            self.worker_events = [e for e in self.worker_events if e["seq"] > params["ack"]]
            return {"events": list(self.worker_events)}
        if url.endswith("/search"):
            self.searches += 1
            return {"items": [{"id": {"videoId": i}} for i in self.search_ids]}
        if url.endswith("/videos"):
            return {"items": [self.videos[i] for i in params["id"].split(",") if i in self.videos]}
        if "tiktok.com/oembed" in url:
            return {"title": "Полёт на байке #gta6", "author_name": "rider"}
        raise AssertionError(url)

    def post_json(self, service, url, payload):
        if url.endswith("/getUpdates"):
            self.polls += 1
            return {"result": [u for u in self.updates if u["update_id"] >= payload["offset"]]}
        if url.endswith("/sendMessage"):
            if payload["chat_id"] == "@chan":
                self.posts.append(payload)
            elif "reply_markup" in payload:
                self.candidates.append(payload)
                return {"ok": True, "result": {"message_id": 1000 + len(self.candidates)}}
            else:
                self.replies.append(payload)
            return {"ok": True}
        if url.endswith("/forwardMessage"):
            self.posts.append(payload)
            return {"ok": True}
        if url.endswith("/deleteMessage"):
            if self.delete_fails:
                raise net.ApiError("telegram", 400, "message can't be deleted")
            self.deleted.append((payload["chat_id"], payload["message_id"]))
            return {"ok": True}
        if url.endswith("/editMessageText"):
            self.edits.append(payload)
            return {"ok": True}
        if url.endswith("/answerCallbackQuery"):
            self.answers.append(payload)
            return {"ok": True}
        raise AssertionError(url)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        store.DB_PATH = os.path.join(d, "data", "db.json")
        store.FEED_PATH = os.path.join(d, "docs", "feed.json")
        store.LINKS_PATH = os.path.join(d, "links.txt")
        store.WORKER_URL_PATH = os.path.join(d, "data", "worker_url.txt")
        os.environ["WORKER_URL"] = ""
        self.fake = Fake()
        self._orig = (net.get_json, net.post_json, net.get_bytes, net.resolve)
        net.get_json, net.post_json = self.fake.get_json, self.fake.post_json
        net.get_bytes = lambda service, url: None
        net.resolve = lambda url: "https://www.tiktok.com/@rider/video/7300000000000000001?x=1"
        os.environ.update(YOUTUBE_API_KEY="k", TELEGRAM_BOT_TOKEN="t",
                          TELEGRAM_CHANNEL="@chan", TELEGRAM_ADMIN_IDS="42")
        self._cfg = {k: getattr(cfg, k) for k in (
            "MODERATION", "POPULAR_MIN_VIEWS", "FRESH_MAX_AGE_HOURS",
            "FRESH_MIN_VIEWS", "FRESH_MIN_VELOCITY")}
        # тесты не должны зависеть от того, как настроен боевой config.py
        cfg.MODERATION = False
        cfg.POPULAR_MIN_VIEWS, cfg.FRESH_MAX_AGE_HOURS = 100_000, 6
        cfg.FRESH_MIN_VIEWS, cfg.FRESH_MIN_VELOCITY = 5_000, 2_000

    def tearDown(self):
        for k, v in self._cfg.items():
            setattr(cfg, k, v)
        net.get_json, net.post_json, net.get_bytes, net.resolve = self._orig
        self.tmp.cleanup()

    def feed(self):
        with open(store.FEED_PATH, encoding="utf-8") as f:
            return json.load(f)

    def db(self):
        with open(store.DB_PATH, encoding="utf-8") as f:
            return json.load(f)


class Units(Base):
    def test_links(self):
        p = links.parse
        self.assertEqual(p("https://youtube.com/shorts/abcDEF12345?feature=share")["id"], "abcDEF12345")
        self.assertEqual(p("https://www.youtube.com/watch?t=3&v=abcDEF12345")["id"], "abcDEF12345")
        self.assertEqual(p("https://youtu.be/abcDEF12345")["platform"], "yt")
        tt = p("https://www.tiktok.com/@some.user/video/7300000000000000009?lang=en")
        self.assertEqual((tt["platform"], tt["id"]), ("tt", "7300000000000000009"))
        self.assertEqual(tt["url"], "https://www.tiktok.com/@some.user/video/7300000000000000009")
        self.assertEqual(p("https://vm.tiktok.com/ZMabc123/")["id"], "7300000000000000001")
        self.assertEqual(p("https://www.instagram.com/reel/Cxyz_-12/?igsh=1")["id"], "Cxyz_-12")
        self.assertEqual(p("https://www.instagram.com/someone/reel/Cxyz12/")["platform"], "ig")
        self.assertIsNone(p("https://example.com/video/1"))
        self.assertEqual(p("https://t.me/gtaclipz/777?single")["id"], "gtaclipz/777")
        self.assertEqual(p("https://t.me/s/gtaclipz/777")["platform"], "tg")
        self.assertIsNone(p("https://t.me/c/1234567890/55"))      # закрытый канал
        self.assertIsNone(p("https://t.me/gtaclipz"))             # канал, а не пост
        self.assertEqual(links.split_text("https://youtu.be/abcDEF12345 Погоня"),
                         [("https://youtu.be/abcDEF12345", "Погоня")])

    def test_duration(self):
        self.assertEqual(youtube.parse_duration("PT1M5S"), 65)
        self.assertEqual(youtube.parse_duration("PT45S"), 45)
        self.assertEqual(youtube.parse_duration("PT1H2M3S"), 3723)
        self.assertEqual(youtube.parse_duration("P0D"), 0)

    def test_relevance(self):
        ok = lambda title, **kw: app.is_relevant({"title": title, **kw})
        self.assertTrue(ok("GTA VI funny moment"))
        self.assertTrue(ok("Insane stunt #gta6 #shorts"))
        self.assertTrue(ok("ГТА 6 приколы"))
        self.assertTrue(ok("Cop chase gone wrong", tags=["grand theft auto vi"]))
        self.assertTrue(ok("Peak GTA 6 NPC Moment😂 #shorts"))
        self.assertTrue(ok("MAN WHAT THE HECK 😭 #gta6 #funnymoments"))
        self.assertFalse(ok("GTA Vice City best mission"))
        self.assertFalse(ok("GTA 5 online heist"))
        self.assertFalse(ok("GTA 6 trailer 3 funny reaction"))
        # хайп и мемы вокруг игры, а не моменты из неё
        self.assertFalse(ok("GTA 6 IS HERE 🥹"))
        self.assertFalse(ok("GTA 6 comes out but you have school tomorrow #roblox #funny"))
        self.assertFalse(ok("Comment which one is from GTA 6 #gta6 #memes #funny"))

    def test_dedup_rules(self):
        a = {"key": "yt:a", "title": "GTA 6 cop flips car lol #shorts", "duration": 40}
        b = {"key": "yt:b", "title": "GTA 6 Cop Flips Car LOL!!", "duration": 41}
        c = {"key": "yt:c", "title": "GTA 6 cop flips car lol", "duration": 55}
        d = {"key": "yt:d", "title": "Совсем другое видео про GTA 6", "duration": 40}
        self.assertTrue(dedup.is_duplicate(a, b))
        self.assertFalse(dedup.is_duplicate(a, c))   # длительность не та
        self.assertFalse(dedup.is_duplicate(a, d))   # название не то
        d["thumb_hash"], a["thumb_hash"] = "ffff0000ffff0001", "ffff0000ffff0000"
        self.assertTrue(dedup.is_duplicate(a, d))    # обложки почти одинаковые

    def test_dhash(self):
        from io import BytesIO
        from PIL import Image
        img = Image.linear_gradient("L").resize((480, 360))
        buf = BytesIO(); img.save(buf, "JPEG")
        h = dedup.dhash(buf.getvalue())
        self.assertEqual(len(h), 16)
        self.assertEqual(dedup.hamming(h, h), 0)
        self.assertIsNone(dedup.dhash(b"not an image"))

    def test_caption_escapes_html(self):
        text = telegram.caption({"platform": "yt", "id": "x", "url": "https://y/x",
                                 "title": "<b>hack</b> & co", "author": "A<B", "views": 1234567,
                                 "duration": 65})
        self.assertIn("&lt;b&gt;hack&lt;/b&gt; &amp; co", text)
        self.assertIn("1.2M", text)
        self.assertIn("1:05", text)


class Pipeline(Base):
    def seed(self):
        f = self.fake
        f.videos = {
            "popular0001": vid("popular0001", "GTA 6 cop flips car lol #shorts", 500_000, 20),
            "toolong0001": vid("toolong0001", "GTA 6 full mission", 900_000, 10, duration=120),
            "reupload001": vid("reupload001", "GTA 6 Cop Flips Car LOL", 300_000, 5, channel="UC2"),
            "freshfast01": vid("freshfast01", "GTA VI alligator steals boat, funny", 20_000, 2, duration=30, channel="UC3"),
            "freshslow01": vid("freshslow01", "GTA 6 walking around NPC", 800, 2, duration=30, channel="UC4"),
            "vicecity001": vid("vicecity001", "GTA Vice City nostalgia", 999_999, 3, channel="UC5"),
        }
        f.search_ids = list(f.videos)

    def test_full_cycle(self):
        self.seed()
        self.assertEqual(app.run(T), 2)
        items = self.db()["items"]
        self.assertEqual(items["yt:popular0001"]["status"], "published")
        self.assertEqual(items["yt:freshfast01"]["status"], "published")
        self.assertEqual(items["yt:reupload001"]["status"], "duplicate")
        self.assertEqual(items["yt:reupload001"]["dup_of"], "yt:popular0001")
        self.assertEqual(items["yt:freshslow01"]["status"], "new")       # ждёт просмотров
        self.assertEqual(items["yt:toolong0001"]["status"], "rejected")
        self.assertEqual(items["yt:vicecity001"]["status"], "rejected")
        self.assertEqual(len(self.fake.posts), 2)
        self.assertEqual(self.fake.searches, cfg.SEARCHES_PER_RUN)
        ids = [i["id"] for i in self.feed()["items"]]
        self.assertCountEqual(ids, ["popular0001", "freshfast01"])

        # второй проход: ничего не публикуется повторно, медленный ролик набрал просмотры
        self.fake.videos["freshslow01"]["statistics"]["viewCount"] = "150000"
        self.assertEqual(app.run(T + timedelta(minutes=30)), 1)
        self.assertEqual(len(self.fake.posts), 3)
        self.assertEqual(len(self.feed()["items"]), 3)
        self.assertEqual(app.run(T + timedelta(minutes=60)), 0)
        self.assertEqual(len(self.fake.posts), 3)

    def test_posts_per_run_and_channel_cap(self):
        f = self.fake
        f.videos = {f"samechan{n:03d}": vid(f"samechan{n:03d}", f"GTA 6 clip number {n} unique {n * 7919}",
                                            200_000 + n, 10, duration=20 + n * 3, channel="UCsame")
                    for n in range(6)}
        f.search_ids = list(f.videos)
        self.assertEqual(app.run(T), cfg.MAX_PER_CHANNEL_PER_DAY)

    def test_search_budget(self):
        self.seed()
        for n in range(60):
            app.run(T + timedelta(minutes=n))
        self.assertLessEqual(self.fake.searches, cfg.SEARCH_DAILY_BUDGET)

    def test_expire_and_prune(self):
        self.seed()
        app.run(T)
        app.run(T + timedelta(hours=50))
        self.assertEqual(self.db()["items"]["yt:freshslow01"]["status"], "expired")
        self.fake.search_ids = []
        app.run(T + timedelta(days=cfg.KEEP_DAYS + 1))
        self.assertEqual(self.db()["items"], {})
        self.assertEqual(self.feed()["items"], [])

    def test_manual_from_bot_and_file(self):
        self.fake.updates = [
            {"update_id": 10, "message": {"from": {"id": 42}, "chat": {"id": 42},
                                          "text": "https://vm.tiktok.com/ZMabc123/"}},
            {"update_id": 11, "message": {"from": {"id": 666}, "chat": {"id": 666},
                                          "text": "https://www.instagram.com/reel/SPAM/"}},
            {"update_id": 12, "message": {"from": {"id": 42}, "chat": {"id": 42}, "text": "привет"}},
        ]
        with open(store.LINKS_PATH, "w", encoding="utf-8") as f:
            f.write("# комментарий\nhttps://www.instagram.com/reel/Cabc123/ Прыжок с небоскрёба\n")
        self.assertEqual(app.run(T), 2)
        items = self.db()["items"]
        self.assertEqual(items["tt:7300000000000000001"]["author"], "rider")
        self.assertEqual(items["ig:Cabc123"]["title"], "Прыжок с небоскрёба")
        self.assertNotIn("ig:SPAM", items)                 # чужим нельзя
        self.assertEqual(len(self.fake.replies), 1)        # только подсказка на "привет"
        self.assertEqual(self.db()["state"]["tg_offset"], 13)
        # повторный проход ничего не дублирует
        self.assertEqual(app.run(T + timedelta(minutes=30)), 0)
        self.assertEqual(len(self.fake.posts), 2)
        # и после очистки базы строка из links.txt не публикуется заново
        self.assertEqual(app.run(T + timedelta(days=cfg.KEEP_DAYS + 1)), 0)

    def test_works_without_keys(self):
        for k in ("YOUTUBE_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL", "TELEGRAM_ADMIN_IDS"):
            os.environ[k] = ""
        self.assertEqual(app.run(T), 0)
        self.assertEqual(self.feed()["items"], [])

    def test_telegram_failure_keeps_item_in_queue(self):
        self.seed()
        def boom(service, url, payload):
            raise net.ApiError("telegram", 400, "chat not found")
        net.post_json = boom
        self.assertEqual(app.run(T), 0)
        self.assertEqual(self.db()["items"]["yt:popular0001"]["status"], "new")

    def test_errors_do_not_leak_secrets(self):
        e = net.ApiError("youtube", 403, "quotaExceeded")
        self.assertNotIn("key=", str(e))

    def test_search_is_throttled_between_frequent_runs(self):
        self.seed()
        app.run(T)
        app.run(T + timedelta(minutes=10))
        app.run(T + timedelta(minutes=20))
        self.assertEqual(self.fake.searches, cfg.SEARCHES_PER_RUN)
        app.run(T + timedelta(minutes=30))
        self.assertEqual(self.fake.searches, cfg.SEARCHES_PER_RUN * 2)

    def test_feed_file_untouched_when_nothing_changes(self):
        self.seed()
        app.run(T)
        stamp = self.feed()["updated_at"]
        app.run(T + timedelta(minutes=10))
        self.assertEqual(self.feed()["updated_at"], stamp)


class Moderation(Base):
    def setUp(self):
        super().setUp()
        cfg.MODERATION = True

    def tap(self, n, action, key, sender=42):
        self.fake.updates.append({"update_id": n, "callback_query": {
            "id": f"cb{n}", "from": {"id": sender}, "data": f"{action}|{key}"}})

    def test_approve_and_reject(self):
        Pipeline.seed(self)
        self.assertEqual(app.run(T), 0)
        self.assertEqual(self.fake.posts, [])                  # без одобрения в канал ничего не идёт
        sent = [c["reply_markup"]["inline_keyboard"][0][0]["callback_data"] for c in self.fake.candidates]
        self.assertCountEqual(sent, ["ok|yt:popular0001", "ok|yt:freshfast01"])
        self.assertTrue(all(c["chat_id"] == 42 for c in self.fake.candidates))
        items = self.db()["items"]
        self.assertEqual(items["yt:popular0001"]["status"], "pending")
        self.assertEqual(items["yt:reupload001"]["status"], "duplicate")   # копию модератору не шлём
        self.assertEqual(self.feed()["items"], [])

        self.tap(20, "ok", "yt:popular0001")
        self.tap(21, "no", "yt:freshfast01")
        self.tap(22, "ok", "yt:freshfast01", sender=666)       # чужое нажатие игнорируется
        self.assertEqual(app.run(T + timedelta(minutes=10)), 1)
        items = self.db()["items"]
        self.assertEqual(items["yt:popular0001"]["status"], "published")
        self.assertEqual(items["yt:freshfast01"]["status"], "rejected")
        self.assertEqual(len(self.fake.posts), 1)
        self.assertEqual(len(self.fake.answers), 2)
        # оба сообщения с кандидатами удалены из лички: это и есть подтверждение нажатия
        self.assertCountEqual(self.fake.deleted, [(42, 1001), (42, 1002)])
        self.assertEqual(self.fake.edits, [])
        self.assertEqual([i["id"] for i in self.feed()["items"]], ["popular0001"])

        self.tap(30, "ok", "yt:freshfast01")                   # повторное нажатие по старой кнопке
        self.assertEqual(app.run(T + timedelta(minutes=20)), 0)
        self.assertEqual(self.fake.answers[-1]["text"], "Уже обработано")
        self.assertEqual(len(self.fake.posts), 1)

    def test_pending_cap_and_timeout(self):
        f = self.fake
        f.videos = {f"candidate{n:02d}": vid(f"candidate{n:02d}", f"GTA 6 clip {n} unique {n * 7919}",
                                             200_000 + n, 5, duration=20 + n * 3, channel=f"UC{n}")
                    for n in range(14)}
        f.search_ids = list(f.videos)
        app.run(T)
        self.assertEqual(len(f.candidates), cfg.MOD_BATCH)
        app.run(T + timedelta(minutes=10))
        app.run(T + timedelta(minutes=20))
        self.assertEqual(len(f.candidates), cfg.MOD_MAX_PENDING)   # пока не ответили, новых не шлём
        app.run(T + timedelta(hours=cfg.MOD_TIMEOUT_HOURS + 1))
        expired = [i for i in self.db()["items"].values() if i["status"] == "expired"]
        self.assertEqual(len(expired), cfg.MOD_MAX_PENDING)
        self.assertEqual(len(f.deleted), cfg.MOD_MAX_PENDING)      # просроченные убраны из лички

    def test_untouched_candidate_stays_until_its_tap_arrives(self):
        Pipeline.seed(self)
        app.run(T)
        self.tap(20, "no", "yt:freshfast01")
        app.run(T + timedelta(minutes=10))
        self.assertEqual(len(self.fake.deleted), 1)                # удалён только тот, по которому нажали
        self.assertEqual(self.db()["items"]["yt:popular0001"]["status"], "pending")

    def test_falls_back_to_removing_buttons_when_delete_fails(self):
        Pipeline.seed(self)
        app.run(T)
        self.fake.delete_fails = True
        self.tap(20, "no", "yt:freshfast01")
        app.run(T + timedelta(minutes=10))
        self.assertEqual(len(self.fake.edits), 1)
        self.assertEqual(self.fake.edits[0]["reply_markup"], {"inline_keyboard": []})
        self.assertIn("Пропущен", self.fake.edits[0]["text"])

    def test_manual_link_skips_moderation(self):
        self.fake.updates = [{"update_id": 10, "message": {
            "from": {"id": 42}, "chat": {"id": 42},
            "text": "https://www.instagram.com/reel/Cabc123/ Jump"}}]
        self.assertEqual(app.run(T), 1)
        self.assertEqual(len(self.fake.posts), 1)
        self.assertEqual(self.fake.candidates, [])

    def test_forward_skips_moderation_too(self):
        self.fake.updates = [{"update_id": 10, "message": {
            "from": {"id": 42}, "chat": {"id": 42}, "message_id": 55, "video": {}, "caption": "Cop flips",
            "forward_origin": {"type": "channel", "chat": {"username": "gtaclipz", "title": "GTA Clipz"},
                               "message_id": 777}}}]
        self.assertEqual(app.run(T), 1)
        self.assertEqual(self.fake.candidates, [])

    def test_admin_has_not_started_bot(self):
        Pipeline.seed(self)
        def post_json(service, url, payload):
            if url.endswith("/sendMessage") and payload["chat_id"] == 42:
                raise net.ApiError("telegram", 403, "bot can't initiate conversation with a user")
            return self.fake.post_json(service, url, payload)
        net.post_json = post_json
        self.assertEqual(app.run(T), 0)
        self.assertEqual(self.db()["items"]["yt:popular0001"]["status"], "new")   # ждёт, не теряется


class Forwards(Base):
    """Пересланные боту посты и ссылки, спрятанные в разметке."""

    CHANNEL = {"type": "channel", "chat": {"username": "gtaclipz", "title": "GTA Clipz"}, "message_id": 777}

    def setUp(self):
        super().setUp()
        self.fake.videos["abcDEF12345"] = vid("abcDEF12345", "GTA 6 funny moment", 1000, 3)

    def send(self, n, sender=42, **msg):
        self.fake.updates.append({"update_id": n, "message": {
            "from": {"id": sender}, "chat": {"id": sender}, "message_id": 50 + n, **msg}})

    def test_forwarded_channel_video(self):
        self.send(1, video={}, caption="Cop flips his own car 😂\nSubscribe: https://t.me/gtaclipz",
                  forward_origin=self.CHANNEL)
        self.assertEqual(app.run(T), 1)
        item = self.db()["items"]["tg:gtaclipz/777"]
        self.assertEqual((item["title"], item["author"]), ("Cop flips his own car 😂", "GTA Clipz"))
        # в канал уходит штатной пересылкой из лички админа
        self.assertEqual(self.fake.posts, [{"chat_id": "@chan", "from_chat_id": 42, "message_id": 51}])
        self.assertEqual([(i["platform"], i["id"]) for i in self.feed()["items"]], [("tg", "gtaclipz/777")])
        # тот же пост, пересланный ещё раз, не дублируется
        self.send(2, video={}, forward_origin=self.CHANNEL)
        self.assertEqual(app.run(T + timedelta(minutes=10)), 0)
        self.assertIn("уже есть", self.fake.replies[-1]["text"])

    def test_forward_with_hidden_source_link_prefers_the_source(self):
        self.send(1, video={}, caption="Watch the original",
                  caption_entities=[{"type": "text_link", "offset": 10, "length": 8,
                                     "url": "https://youtu.be/abcDEF12345"}],
                  forward_origin=self.CHANNEL)
        self.assertEqual(app.run(T), 1)
        self.assertIn("yt:abcDEF12345", self.db()["items"])
        self.assertIn("text", self.fake.posts[0])                 # обычный пост со ссылкой, не пересылка

    def test_forward_from_closed_source_goes_to_channel_only(self):
        self.send(1, video={}, forward_origin={"type": "hidden_user", "sender_user_name": "Someone"})
        self.assertEqual(app.run(T), 1)
        self.assertEqual(len(self.fake.posts), 1)
        self.assertEqual(self.feed()["items"], [])
        self.assertIn("только в канале", self.fake.replies[-1]["text"])

    def test_forward_without_video_or_link(self):
        self.send(1, text="just some text", forward_origin=self.CHANNEL)
        self.assertEqual(app.run(T), 0)
        self.assertIn("нет ни видео", self.fake.replies[-1]["text"])

    def test_strangers_are_ignored(self):
        self.send(1, sender=666, video={}, forward_origin=self.CHANNEL)
        self.send(2, sender=666, text="https://youtu.be/abcDEF12345")
        self.assertEqual(app.run(T), 0)
        self.assertEqual(self.db()["items"], {})
        self.assertEqual(self.fake.replies, [])                   # чужим даже не отвечаем

    def test_bare_link_entity_after_emoji(self):
        self.send(1, text="😂 youtu.be/abcDEF12345",
                  entities=[{"type": "url", "offset": 3, "length": 20}])
        self.assertEqual(app.run(T), 1)
        self.assertIn("yt:abcDEF12345", self.db()["items"])

    def test_protected_source_reports_failure(self):
        self.send(1, video={}, forward_origin={"type": "hidden_user", "sender_user_name": "Someone"})
        def post_json(service, url, payload):
            if url.endswith("/forwardMessage"):
                raise net.ApiError("telegram", 400, "message can't be forwarded")
            return self.fake.post_json(service, url, payload)
        net.post_json = post_json
        self.assertEqual(app.run(T), 0)
        self.assertEqual([i["status"] for i in self.db()["items"].values()], ["failed"])
        self.assertIn("запретил пересылку", self.fake.replies[-1]["text"])


class Tracking(Base):
    """После решения админа сборщик продолжает следить за реакцией зрителей."""

    def test_decided_clips_keep_getting_fresh_numbers(self):
        cfg.MODERATION = True
        Pipeline.seed(self)
        app.run(T)
        self.fake.updates += [
            {"update_id": 1, "callback_query": {"id": "a", "from": {"id": 42}, "data": "ok|yt:popular0001"}},
            {"update_id": 2, "callback_query": {"id": "b", "from": {"id": 42}, "data": "no|yt:freshfast01"}},
        ]
        app.run(T + timedelta(minutes=10))
        for vid_id, views in (("popular0001", 900_000), ("freshfast01", 21_000)):
            self.fake.videos[vid_id]["statistics"].update(viewCount=str(views), likeCount="77", commentCount="9")
        app.run(T + timedelta(minutes=80))
        items = self.db()["items"]
        ok, no = items["yt:popular0001"], items["yt:freshfast01"]
        self.assertEqual((ok["views"], ok["likes"], ok["comments"]), (900_000, 77, 9))
        self.assertEqual(ok["shown"], {"views": 500_000, "likes": 10, "comments": 3})   # как было при показе
        self.assertEqual((no["views"], no["shown"]["views"]), (21_000, 20_000))         # пропущенные тоже
        self.assertEqual(self.feed()["items"][0]["views"], 900_000)                     # и на сайте свежие цифры
        self.assertNotIn("shown", items["yt:freshslow01"])                              # без решения не следим

    def test_tracking_stops_for_old_clips_and_is_throttled(self):
        Pipeline.seed(self)
        app.run(T)                                   # автопубликация: popular0001 опубликован
        calls = []
        real = self.fake.get_json
        def spy(service, url, params=None, headers=None):
            if url.endswith("/videos"):
                calls.append(params["id"])
            return real(service, url, params, headers)
        net.get_json = spy
        app.run(T + timedelta(minutes=70)); n1 = sum("popular0001" in c for c in calls)
        app.run(T + timedelta(minutes=80)); n2 = sum("popular0001" in c for c in calls)
        self.assertEqual((n1, n2), (1, 1))           # не чаще раза в час
        app.run(T + timedelta(hours=cfg.TRACK_HOURS + 1)); n3 = sum("popular0001" in c for c in calls)
        self.assertEqual(n3, 1)                      # ролик старше окна: больше не опрашиваем


class Reuploads(Base):
    """Один и тот же клип под разными ID: админ не должен видеть его дважды."""

    def test_rules_on_real_examples(self):
        dup = lambda a, b: dedup.is_duplicate({"key": "a", **a}, {"key": "b", **b})
        # тот же клип у другого канала: та же длина, те же слова в названии
        self.assertTrue(dup({"title": "Bro found the first gta6 glitch 🥀", "duration": 30},
                            {"title": "BRO FOUND A GLITCH IN GTA 6 #gta6 #glitch", "duration": 30}))
        # то же название с теми же хэштегами, но другая нарезка
        self.assertTrue(dup({"title": "GTA 6 😅🫢 glitch #gta6 #gta #glitch #fix #gaming", "duration": 21},
                            {"title": "GTA 6 glitch😅😅 #gta6 #gta #glitch #fix #gaming", "duration": 35}))
        # почти одинаковая обложка при любой длине и любом названии
        self.assertTrue(dup({"title": "GTA 6 glitch", "duration": 21, "thumb_hash": "0bbbbf8a0969effc"},
                            {"title": "First Secret Wall Glitch Found!", "duration": 12,
                             "thumb_hash": "0bbbbf2a29b56ffc"}))
        # похожая обложка при той же длине
        self.assertTrue(dup({"title": "one", "duration": 30, "thumb_hash": "ebc70f78b81b1a9a"},
                            {"title": "two", "duration": 30, "thumb_hash": "ebc70f9b1c3d183b"}))
        # а это разные ролики
        self.assertFalse(dup({"title": "GTA 6 Funny Moments Part 12 #gta6", "duration": 40},
                             {"title": "GTA 6 Funny Moments Part 13 #gta6", "duration": 40}))
        self.assertFalse(dup({"title": "Cop chase ends in the ocean", "duration": 33},
                             {"title": "Cop chase ends at the airport", "duration": 33}))
        self.assertFalse(dup({"title": "GTA 6 glitch", "duration": 20}, {"title": "GTA 6 glitch", "duration": 45}))
        self.assertFalse(dup({"title": "one", "duration": 30, "thumb_hash": "ebc70f78b81b1a9a"},
                             {"title": "two", "duration": 55, "thumb_hash": "ebc70f9b1c3d183b"}))

    def test_reupload_of_a_skipped_clip_is_not_offered_again(self):
        cfg.MODERATION = True
        f = self.fake
        f.videos = {"original001": vid("original001", "Bro found the first GTA 6 glitch", 300_000, 5, duration=30)}
        f.search_ids = list(f.videos)
        app.run(T)
        self.assertEqual(len(f.candidates), 1)
        f.updates.append({"update_id": 1, "callback_query": {"id": "cb", "from": {"id": 42},
                                                             "data": "no|yt:original001"}})
        # через час тот же клип находится ещё у трёх каналов, плюс один действительно новый
        for n in range(3):
            k = f"reupload{n:03d}"
            f.videos[k] = vid(k, f"BRO FOUND A GLITCH IN GTA 6 #{n} #gta6", 400_000 + n, 1, duration=30, channel=f"UC{n}")
        f.videos["different01"] = vid("different01", "GTA 6 stunt lands on a yacht", 500_000, 1, duration=30, channel="UCx")
        f.search_ids = list(f.videos)
        app.run(T + timedelta(hours=1))
        shown = [c["reply_markup"]["inline_keyboard"][0][0]["callback_data"] for c in f.candidates]
        self.assertEqual(shown, ["ok|yt:original001", "ok|yt:different01"])
        items = self.db()["items"]
        self.assertEqual([items[f"yt:reupload{n:03d}"]["status"] for n in range(3)], ["duplicate"] * 3)
        # копии сначала сворачиваются между собой, а оставшаяся сверяется с пропущенным оригиналом
        self.assertIn("yt:original001", {items[f"yt:reupload{n:03d}"]["dup_of"] for n in range(3)})

    def test_reupload_of_a_published_clip_is_not_offered(self):
        cfg.MODERATION = True
        f = self.fake
        f.videos = {"original001": vid("original001", "GTA 6 😅 glitch #gta6 #gta #glitch #fix #gaming", 300_000, 5, duration=21)}
        f.search_ids = list(f.videos)
        app.run(T)
        f.updates.append({"update_id": 1, "callback_query": {"id": "cb", "from": {"id": 42},
                                                             "data": "ok|yt:original001"}})
        f.videos["recut000001"] = vid("recut000001", "GTA 6 glitch😅😅 #gta6 #gta #glitch #fix #gaming",
                                      900_000, 1, duration=35, channel="UC9")
        f.search_ids = list(f.videos)
        app.run(T + timedelta(minutes=30))
        self.assertEqual(len(f.candidates), 1)
        self.assertEqual(self.db()["items"]["yt:recut000001"]["status"], "duplicate")


class WorkerMode(Base):
    """Нажатия и сообщения приходят не опросом Telegram, а из очереди воркера."""

    def setUp(self):
        super().setUp()
        cfg.MODERATION = True
        os.environ["WORKER_URL"] = "https://bot.example.workers.dev/"

    def tearDown(self):
        os.environ["WORKER_URL"] = ""
        super().tearDown()

    def test_candidate_text_is_exactly_the_future_post(self):
        Pipeline.seed(self)
        app.run(T)
        c = self.fake.candidates[0]
        self.assertNotIn("⏳", c["text"])                          # статистика не в тексте...
        rows = c["reply_markup"]["inline_keyboard"]
        self.assertEqual(rows[1][0]["callback_data"], "noop")      # ...а на кнопке-подписи
        self.assertIn("⏳", rows[1][0]["text"])

    def test_taps_handled_by_worker_are_only_recorded(self):
        Pipeline.seed(self)
        app.run(T)
        self.fake.worker_events = [
            {"seq": 1, "type": "tap", "key": "yt:popular0001", "action": "ok", "posted": True, "at": 1},
            {"seq": 2, "type": "tap", "key": "yt:freshfast01", "action": "no", "posted": False, "at": 2},
        ]
        app.run(T + timedelta(minutes=1))
        items = self.db()["items"]
        self.assertEqual(items["yt:popular0001"]["status"], "published")
        self.assertEqual(items["yt:freshfast01"]["status"], "rejected")
        self.assertEqual([i["id"] for i in self.feed()["items"]], ["popular0001"])
        # воркер уже всё сделал в Telegram: сборщик не постит, не удаляет и не опрашивает
        self.assertEqual(self.fake.posts, [])
        self.assertEqual(self.fake.deleted, [])
        self.assertEqual(self.fake.answers, [])
        self.assertEqual(self.fake.polls, 0)
        self.assertEqual(self.db()["state"]["worker_seq"], 2)
        # подтверждение уходит воркеру только на следующем запуске
        self.assertEqual(self.fake.acks, [0, 0])
        app.run(T + timedelta(minutes=2))
        self.assertEqual(self.fake.acks[-1], 2)
        self.assertEqual(self.fake.worker_events, [])

    def test_skip_sticks_whatever_state_the_record_is_in(self):
        Pipeline.seed(self)
        app.run(T)
        # база потеряла отметку "показан": запись снова выглядит как новая
        db = self.db()
        db["items"]["yt:popular0001"].update(status="new", mod_msgs=[])
        del db["items"]["yt:popular0001"]["mod_sent_at"]
        with open(store.DB_PATH, "w", encoding="utf-8") as f:
            json.dump(db, f)
        sent_before = len(self.fake.candidates)
        self.fake.worker_events = [
            {"seq": 1, "type": "tap", "key": "yt:popular0001", "action": "no", "posted": False, "at": 1},
            {"seq": 2, "type": "tap", "key": "yt:gone0000000", "action": "no", "posted": False, "at": 1},
        ]
        app.run(T + timedelta(minutes=1))
        app.run(T + timedelta(minutes=11))
        items = self.db()["items"]
        self.assertEqual(items["yt:popular0001"]["status"], "rejected")      # решение не потерялось
        self.assertEqual(items["yt:gone0000000"]["status"], "rejected")      # даже если записи не было
        again = [c for c in self.fake.candidates[sent_before:]
                 if c["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "ok|yt:popular0001"]
        self.assertEqual(again, [])                                          # и ролик не предложен снова

    def test_message_from_queue(self):
        self.fake.worker_events = [
            {"seq": 5, "type": "message", "at": 1, "message": {
                "from": {"id": 42}, "chat": {"id": 42}, "message_id": 9,
                "text": "https://www.instagram.com/reel/Cabc123/ Jump"}},
            {"seq": 6, "type": "message", "at": 1, "message": {
                "from": {"id": 666}, "chat": {"id": 666}, "message_id": 1,
                "text": "https://www.instagram.com/reel/SPAM/"}},
        ]
        self.fake.worker_events[0]["ack_id"] = 77
        self.assertEqual(app.run(T), 1)
        self.assertEqual(len(self.fake.posts), 1)
        self.assertNotIn("ig:SPAM", self.db()["items"])
        # ролик вышел: в личке не остаётся ни ссылки админа, ни "Принял", ни отчёта
        self.assertCountEqual(self.fake.deleted, [(42, 9), (42, 77)])
        self.assertEqual(self.fake.replies, [])
        self.assertNotIn("cleanup", self.db()["items"]["ig:Cabc123"])

    def test_rejected_message_keeps_the_explanation(self):
        self.fake.worker_events = [{"seq": 1, "type": "message", "at": 1, "ack_id": 77, "message": {
            "from": {"id": 42}, "chat": {"id": 42}, "message_id": 9, "text": "https://example.com/video"}}]
        self.assertEqual(app.run(T), 0)
        self.assertEqual(self.fake.deleted, [(42, 77)])            # "Принял" убран...
        self.assertIn("Не распознал", self.fake.replies[-1]["text"])   # ...а причина и сама ссылка остались

    def test_cleanup_waits_until_the_clip_is_really_posted(self):
        self.fake.worker_events = [{"seq": 1, "type": "message", "at": 1, "ack_id": 77, "message": {
            "from": {"id": 42}, "chat": {"id": 42}, "message_id": 9,
            "text": "https://www.instagram.com/reel/Cabc123/"}}]
        real = self.fake.post_json
        def flaky(service, url, payload):
            if url.endswith("/sendMessage") and payload["chat_id"] == "@chan":
                raise net.ApiError("telegram", 429, "Too Many Requests")
            return real(service, url, payload)
        net.post_json = flaky
        self.assertEqual(app.run(T), 0)
        self.assertEqual(self.fake.deleted, [])                    # не вышел: ничего не трогаем
        net.post_json = real
        self.assertEqual(app.run(T + timedelta(minutes=1)), 1)
        self.assertCountEqual(self.fake.deleted, [(42, 9), (42, 77)])

    def test_worker_down_does_not_stop_the_run(self):
        Pipeline.seed(self)
        self.fake.worker_down = True
        app.run(T)
        self.assertEqual(len(self.fake.candidates), 2)
        self.assertEqual(self.fake.polls, 0)

    def test_url_from_file_written_by_deploy(self):
        os.environ["WORKER_URL"] = ""
        os.makedirs(os.path.dirname(store.WORKER_URL_PATH), exist_ok=True)
        with open(store.WORKER_URL_PATH, "w") as f:
            f.write("https://gta6-feed-bot.someone.workers.dev\n")
        self.assertEqual(worker.url(), "https://gta6-feed-bot.someone.workers.dev")


if __name__ == "__main__":
    unittest.main()
