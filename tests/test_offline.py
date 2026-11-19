"""Офлайн-тесты: все сетевые вызовы подменены. Запуск: python -m unittest -v"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import config as cfg
from feed import __main__ as app
from feed import dedup, links, net, store, telegram, youtube

T = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


def vid(id_, title, views, age_h, duration=40, channel="UC1", **kw):
    return {"id": id_, "snippet": {
                "title": title, "description": kw.get("description", ""),
                "channelTitle": "Автор " + channel, "channelId": channel,
                "publishedAt": store.iso(T - timedelta(hours=age_h)),
                "liveBroadcastContent": "none"},
            "contentDetails": {"duration": f"PT{duration}S"},
            "statistics": {"viewCount": str(views), "likeCount": "10"}}


class Fake:
    """Подмена net.*: YouTube, Telegram, TikTok oEmbed."""

    def __init__(self):
        self.videos = {}
        self.search_ids = []
        self.searches = 0
        self.posts = []
        self.replies = []
        self.updates = []

    def get_json(self, service, url, params=None):
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
            return {"result": [u for u in self.updates if u["update_id"] >= payload["offset"]]}
        if url.endswith("/sendMessage"):
            (self.posts if payload["chat_id"] == "@chan" else self.replies).append(payload)
            return {"ok": True}
        raise AssertionError(url)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        store.DB_PATH = os.path.join(d, "data", "db.json")
        store.FEED_PATH = os.path.join(d, "docs", "feed.json")
        store.LINKS_PATH = os.path.join(d, "links.txt")
        self.fake = Fake()
        self._orig = (net.get_json, net.post_json, net.get_bytes, net.resolve)
        net.get_json, net.post_json = self.fake.get_json, self.fake.post_json
        net.get_bytes = lambda service, url: None
        net.resolve = lambda url: "https://www.tiktok.com/@rider/video/7300000000000000001?x=1"
        os.environ.update(YOUTUBE_API_KEY="k", TELEGRAM_BOT_TOKEN="t",
                          TELEGRAM_CHANNEL="@chan", TELEGRAM_ADMIN_IDS="42")

    def tearDown(self):
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
        self.assertTrue(ok("wow", tags=["grand theft auto vi"]))
        self.assertFalse(ok("GTA Vice City best mission"))
        self.assertFalse(ok("GTA 5 online heist"))
        self.assertFalse(ok("GTA 6 trailer 3 reaction"))

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
            "freshfast01": vid("freshfast01", "GTA VI alligator steals boat", 20_000, 2, duration=30, channel="UC3"),
            "freshslow01": vid("freshslow01", "GTA 6 walking around", 800, 2, duration=30, channel="UC4"),
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
        self.assertEqual(len(self.fake.replies), 2)        # "добавил" и "пришлите ссылку"
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


if __name__ == "__main__":
    unittest.main()
