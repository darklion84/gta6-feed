"""Связь сборщика с обработчиком на Cloudflare Workers (папка worker/).

Когда воркер выложен, Telegram шлёт все нажатия и сообщения ему, а не нам:
воркер реагирует мгновенно и складывает сделанное в очередь. Здесь мы эту
очередь забираем. Если воркера нет, сборщик сам опрашивает Telegram, как раньше.
"""
import hashlib
import os

from . import net, store, telegram


def url():
    """Адрес воркера: переменная WORKER_URL или файл, который пишет выкладка."""
    env = os.environ.get("WORKER_URL", "").strip()
    if env:
        return env.rstrip("/")
    try:
        with open(store.WORKER_URL_PATH, encoding="utf-8") as f:
            return f.read().strip().rstrip("/")
    except OSError:
        return ""


def sync_key():
    # тот же расчёт, что в worker.js: отдельный секрет не нужен
    return hashlib.sha256(f"sync:{telegram.token()}".encode()).hexdigest()


def pull(ack):
    """Подтвердить события до ack включительно и получить новые.

    ack сохраняется в базе и уходит воркеру только при СЛЕДУЮЩЕМ запуске:
    если этот запуск упадёт, не сохранив базу, события придут ещё раз.
    """
    data = net.get_json("worker", url() + "/pull", {"ack": ack},
                        headers={"Authorization": "Bearer " + sync_key()})
    return data.get("events", [])
