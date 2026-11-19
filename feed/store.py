"""База в одном JSON-файле: {"items": {ключ: запись}, "state": {...}}."""
import json
import os
from datetime import datetime, timedelta, timezone

DB_PATH = "data/db.json"
FEED_PATH = "docs/feed.json"
LINKS_PATH = "links.txt"


def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s):
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def hours_since(s, t):
    return (t - parse(s)).total_seconds() / 3600


def pacific_day(t):
    """Квота YouTube обнуляется в полночь по тихоокеанскому времени."""
    try:
        from zoneinfo import ZoneInfo
        return t.astimezone(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    except Exception:
        return (t - timedelta(hours=8)).strftime("%Y-%m-%d")


def load():
    if os.path.exists(DB_PATH):
        with open(DB_PATH, encoding="utf-8") as f:
            db = json.load(f)
    else:
        db = {}
    db.setdefault("items", {})
    db.setdefault("state", {})
    return db


def write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def save(db):
    write_json(DB_PATH, db)
