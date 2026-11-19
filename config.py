"""Настройки ленты.

Секреты (ключ YouTube, токен бота) сюда НЕ писать: они берутся из переменных
окружения / GitHub Secrets. Здесь только то, что не страшно показать всем.
"""

# --- Сайт и канал -----------------------------------------------------------
SITE_TITLE = "GTA6 NEWS"
SITE_TAGLINE = "The funniest GTA 6 clips in one feed. Unofficial fan project."
SITE_URL = "https://darklion84.github.io/gta6-feed/"   # со слэшем в конце; сменить после подключения домена
TELEGRAM_URL = "https://t.me/gta6hotreels"
TELEGRAM_CHANNEL = "@gta6hotreels"   # куда постить; секрет TELEGRAM_CHANNEL, если задан, важнее

# --- Что искать на YouTube --------------------------------------------------
# Запросы перебираются по кругу, по SEARCHES_PER_RUN штук за запуск.
QUERIES = [
    "gta 6 funny moments",
    "gta 6 shorts",
    "gta vi clip",
    "gta 6 fail",
    "gta 6 glitch",
    "gta 6 stunt",
    "gta 6 npc",
    "gta 6 police chase",
    "гта 6 приколы",
]

# Ролик берём, только если в названии / описании / тегах есть одно из этих
# выражений (регулярки, без учёта регистра). \b нужен, чтобы "GTA Vice City"
# не принималась за "GTA VI".
MUST_MATCH = [
    r"\bgta\s*(6|vi)\b",
    r"\bgrand theft auto\s*(6|vi)\b",
    r"\bгта\s*6\b",
]

# В НАЗВАНИИ должно быть хотя бы одно из этих выражений (регулярки). Так
# отсекаются мемы, новости и хайп вокруг игры: остаются ролики, похожие на
# моменты из самой игры. Пустой список = не проверять.
TITLE_HINTS = [
    r"\bfunn(y|iest)", r"\bmoments?\b", r"\bnpcs?\b", r"\bglitch", r"\bbugs?\b",
    r"\bfails?\b", r"\bstunts?\b", r"\bphysics\b", r"\bwtf\b", r"\blol\b",
    r"\bcops?\b", r"\bpolice\b", r"\bchase", r"\bcrash", r"\bragdoll",
    r"\bgameplay\b", r"\bclips?\b", r"\bhighlights?\b", r"easter egg",
    r"прикол", r"момент",
]

# Если в НАЗВАНИИ есть одно из этих слов, ролик отбрасывается.
STOP_WORDS = [
    "trailer", "трейлер", "leak", "concept", "fan made", "fanmade",
    "countdown", "release date", "дата выхода",
    "roblox", "minecraft", "fortnite", "pubg", "free fire",
    "meme", "comment ", "pre-order", "preorder", "delay", "coming soon",
    "comes out", "hours until", "game awards", "unboxing", "ranking",
]

SEARCH_LANGUAGE = "en"       # какой язык поиск ставит выше; остальные не отсекаются. "" = без приоритета
BLOCK_CHANNELS = []          # ID каналов (UC...), которые не брать никогда
YOUTUBE_CATEGORY = "20"      # 20 = Gaming; "" чтобы искать по всем категориям

MIN_DURATION = 5             # секунд
MAX_DURATION = 90            # секунд
SEARCH_WINDOW_HOURS = 48     # ищем ролики не старше этого

# --- Квота YouTube ----------------------------------------------------------
# По умолчанию Google даёт 100 вызовов поиска в сутки. 2 поиска x 48 запусков
# (каждые 30 минут) = 96.
SEARCHES_PER_RUN = 2
SEARCH_DAILY_BUDGET = 95

# --- Когда ролик достоин публикации -----------------------------------------
# "Уже популярный": набрал столько просмотров.
POPULAR_MIN_VIEWS = 100_000
# "Свежий и быстро растёт": моложе N часов, есть минимум просмотров и скорость.
FRESH_MAX_AGE_HOURS = 6
FRESH_MIN_VIEWS = 5_000
FRESH_MIN_VELOCITY = 2_000   # просмотров в час

MAX_POSTS_PER_RUN = 4        # не больше стольких постов за один запуск
MAX_PER_CHANNEL_PER_DAY = 2  # чтобы один автор не забивал ленту

# --- Дубли ------------------------------------------------------------------
DEDUP_WINDOW_HOURS = 72
DURATION_TOLERANCE = 1       # секунд
TITLE_SIMILARITY = 0.8       # 0..1, схожесть названий
THUMB_DISTANCE = 8           # макс. расстояние Хэмминга между хэшами обложек (из 64)

# --- Хранение ---------------------------------------------------------------
KEEP_DAYS = 14               # старше этого записи удаляются из базы
FEED_SIZE = 150              # сколько роликов отдаём сайту
