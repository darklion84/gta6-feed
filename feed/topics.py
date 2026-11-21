"""Повторы темы.

Один и тот же глитч или находку разные авторы выкладывают десятками. В ленте
такая тема нужна один раз на язык: второй ролик про то же самое зрителю уже
не интересен. Темы заданы в config.TOPIC_LIMITS, каждая своим выражением.

Тема считается занятой, пока ролик про неё опубликован или ждёт решения
модератора. Если модератор первый ролик пропустил, тема свободна: следующий
ролик про неё снова попадёт на модерацию.
"""
import re

import config as cfg
from . import store

# Письменность однозначно указывает язык: этого хватает, чтобы не путать ролики.
SCRIPTS = (("ru", "Ѐ", "ӿ"), ("hi", "ऀ", "ॿ"), ("ar", "؀", "ۿ"),
           ("ml", "ഀ", "ൿ"), ("ta", "஀", "௿"), ("bn", "ঀ", "৿"),
           ("th", "฀", "๿"), ("ko", "가", "힯"), ("ja", "぀", "ヿ"),
           ("zh", "一", "鿿"))
# Для языков на латинице: частые служебные слова. Нужно два совпадения, иначе английский.
WORDS = {
    "es": "el la los las del en un una que mi primer como cómo con por es más muy este esta para y se lo "
          "jugar primera persona fácil dinero infinito truco nuevo",
    "pt": "o os as um uma no na do da em como com não você achou ficar dos das para é mais meu isso e "
          "jogar primeira pessoa fácil dinheiro infinito novo",
    "fr": "le les des sur un une est du dans avec pas déjà pour sa son ce cette et au aux je il",
    "de": "der die das und ist im mit nicht ein eine auf für ich zu den dem so wie",
    "nl": "het een deze kun je door niet met van dit dat en op voor zijn naar",
    "it": "il lo gli un una che di in con per non è più come questo questa nel della sono",
    "tr": "bir ve bu için ile çok ne da de nasıl en ilk var yok",
    "id": "yang di dan ini itu dengan untuk tidak ada cara baru dari ke saya",
}
WORDS = {lang: set(text.split()) for lang, text in WORDS.items()}


def language(item):
    """Язык ролика: как указал автор на YouTube, иначе по названию."""
    stated = (item.get("lang") or "")[:2].lower()
    if stated and stated not in ("zx", "un", "mu"):      # zxx, und, mul: языка нет или он не указан
        return stated
    title = re.sub(r"#\S+", " ", item.get("title") or "").lower()
    for lang, low, high in SCRIPTS:
        if sum(1 for ch in title if low <= ch <= high) >= 3:
            return lang
    tokens = re.findall(r"[^\W\d_]+", title)
    best, hits = "en", 1
    for lang, known in WORDS.items():
        n = sum(1 for w in tokens if w in known)
        if n > hits:
            best, hits = lang, n
    return best


def topic(item):
    """Название темы из config.TOPIC_LIMITS, к которой относится ролик, или None."""
    title = item.get("title") or ""
    for name, pattern in cfg.TOPIC_LIMITS:
        if re.search(pattern, title, re.I):
            return name
    return None


def repeat(item, others, t):
    """-> ключ ролика, который уже занял ту же тему на том же языке, или None."""
    name = topic(item)
    if not name:
        return None
    lang = language(item)
    for other in others:
        if other is item or other["status"] not in ("published", "pending", "approved"):
            continue
        when = other.get("posted_at") or other.get("mod_sent_at") or other.get("found_at")
        if not when or store.hours_since(when, t) > cfg.TOPIC_WINDOW_HOURS:
            continue
        if topic(other) == name and language(other) == lang:
            return other["key"]
    return None
