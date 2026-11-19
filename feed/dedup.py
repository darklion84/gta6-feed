"""Поиск дублей: один и тот же ролик, залитый под другим ID.

Тот же ID в той же сети отсекается ещё раньше (ключ записи в базе). Здесь
ловим перезаливы и перенарезки: популярный клип за пару часов расходится по
десяткам каналов с другими названиями и чуть другой длиной.

Два ролика считаются одним, если выполняется любое из условий:
  1. та же длительность (+-1 с) и при этом похожи обложки или в названиях
     совпадают содержательные слова;
  2. обложки почти одинаковые, какой бы ни была длительность (другая нарезка);
  3. названия совпадают целиком, вместе с хэштегами.
"""
import io
import re

import config as cfg

_TAG = re.compile(r"#\w+")
_PUNCT = re.compile(r"[^\w\s]|_")
_PUNCT_KEEP_TAGS = re.compile(r"[^\w\s#]|_")
_SPACE = re.compile(r"\s+")

# Слова, которые есть почти в каждом названии и ничего не говорят о содержании.
COMMON = frozenset("""
gta gta6 gtavi gta5 gtav vi grand theft auto rockstar game games gaming gameplay gamer
funny funniest fun moment moments short shorts clip clips video videos viral fyp reels
the an in of to is are was and on for with this that these his her my you your it its
part best top new first when what how why not just so
гта приколы прикол момент моменты видео это как что
""".split())


def norm_title(t):
    """Название без хэштегов, знаков и эмодзи."""
    t = _TAG.sub(" ", (t or "").lower())
    t = _PUNCT.sub(" ", t)
    return _SPACE.sub(" ", t).strip()


def full_title(t):
    """Название вместе с хэштегами, без знаков и эмодзи."""
    t = _PUNCT_KEEP_TAGS.sub(" ", (t or "").lower()).replace("#", " #")
    return _SPACE.sub(" ", t).strip()


def words(t):
    """Содержательные слова названия."""
    return {w for w in norm_title(t).split() if len(w) > 1 and w not in COMMON and not w.isdigit()}


def title_similarity(a, b):
    """Доля общих содержательных слов (0..1). 0, если их слишком мало, чтобы судить."""
    wa, wb = words(a), words(b)
    if len(wa) < 2 or len(wb) < 2:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def dhash(data, size=8):
    """Перцептивный хэш картинки (64 бита, hex) или None."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("L")
        w, h = img.size
        # у вертикальных роликов по бокам обложки поля, берём середину
        img = img.crop((int(w * 0.29), 0, int(w * 0.71), h))
        img = img.resize((size + 1, size), Image.LANCZOS)
    except Exception:
        return None
    px = img.tobytes()   # режим "L": один байт на пиксель
    bits = 0
    for r in range(size):
        for c in range(size):
            bits = (bits << 1) | int(px[r * (size + 1) + c] > px[r * (size + 1) + c + 1])
    return f"{bits:016x}"


def hamming(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def is_duplicate(a, b):
    ha, hb = a.get("thumb_hash"), b.get("thumb_hash")
    dist = hamming(ha, hb) if ha and hb else None
    da, db = a.get("duration"), b.get("duration")
    same_length = bool(da and db and abs(da - db) <= cfg.DURATION_TOLERANCE)

    if same_length:
        if dist is not None and dist <= cfg.THUMB_DISTANCE_SAME_LENGTH:
            return True
        if title_similarity(a.get("title"), b.get("title")) >= cfg.TITLE_SIMILARITY:
            return True
    if dist is not None and dist <= cfg.THUMB_DISTANCE:
        return True
    fa, fb = full_title(a.get("title")), full_title(b.get("title"))
    return len(fa) >= cfg.FULL_TITLE_MIN_LENGTH and fa == fb


def find_duplicate(item, seen):
    """Ключ уже виденного ролика-двойника или None."""
    for other in seen:
        if other["key"] != item["key"] and is_duplicate(item, other):
            return other["key"]
    return None
