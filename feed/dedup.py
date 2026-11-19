"""Поиск дублей: один и тот же ролик, перезалитый другим автором.

Тот же ID в той же сети отсекается ещё раньше (ключ записи в базе). Здесь
ловим перезаливы: совпала длительность (+-1 с) И при этом похожи либо
названия, либо обложки.
"""
import io
import re
from difflib import SequenceMatcher

import config as cfg

_TAG = re.compile(r"#\w+")
_PUNCT = re.compile(r"[^\w\s]|_")
_SPACE = re.compile(r"\s+")


def norm_title(t):
    t = _TAG.sub(" ", (t or "").lower())
    t = _PUNCT.sub(" ", t)
    return _SPACE.sub(" ", t).strip()


def title_similarity(a, b):
    a, b = norm_title(a), norm_title(b)
    if len(a) < 8 or len(b) < 8:      # слишком короткие названия ничего не доказывают
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


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
    da, db = a.get("duration"), b.get("duration")
    if not da or not db or abs(da - db) > cfg.DURATION_TOLERANCE:
        return False
    if title_similarity(a.get("title"), b.get("title")) >= cfg.TITLE_SIMILARITY:
        return True
    ha, hb = a.get("thumb_hash"), b.get("thumb_hash")
    return bool(ha and hb and hamming(ha, hb) <= cfg.THUMB_DISTANCE)


def find_duplicate(item, published):
    """Ключ уже опубликованного ролика-двойника или None."""
    for other in published:
        if other["key"] != item["key"] and is_duplicate(item, other):
            return other["key"]
    return None
