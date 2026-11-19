"""Разовая настройка канала: название и описание.

Запускается вручную из workflow "channel" (Actions -> channel -> Run workflow).
Заодно печатает, что у канала с реакциями и обсуждениями: их бот менять не может,
это делает владелец в настройках канала.
"""
import os
import sys

from . import net, telegram

SITE_HOST = "gta6hot.com"


def main():
    if not telegram.enabled():
        sys.exit("нет токена бота или канала")
    channel = telegram.channel()
    chat = telegram._call("getChat", {"chat_id": channel})["result"]
    print("название:", chat.get("title"))
    print("описание:", (chat.get("description") or "(пусто)").replace("\n", " / "))
    print("обсуждения:", "подключены" if chat.get("linked_chat_id") else "нет")
    reactions = chat.get("available_reactions")
    print("реакции:", "все" if reactions is None else (len(reactions) or "выключены"))
    me = telegram._call("getMe", {})["result"]["id"]
    rights = telegram._call("getChatMember", {"chat_id": channel, "user_id": me})["result"]
    print("бот может менять название:", bool(rights.get("can_change_info")))

    title = os.environ.get("NEW_TITLE", "").strip()
    description = os.environ.get("NEW_DESCRIPTION", "").replace("\\n", "\n").strip()
    try:
        if title and title != chat.get("title"):
            telegram._call("setChatTitle", {"chat_id": channel, "title": title[:128]})
            print("название изменено на:", title)
        # своё описание владельца не трогаем, если в нём уже есть ссылка на сайт
        if description and SITE_HOST not in (chat.get("description") or ""):
            telegram._call("setChatDescription", {"chat_id": channel, "description": description[:255]})
            print("описание обновлено")
    except net.ApiError as e:
        sys.exit(f"не получилось: {e}")


if __name__ == "__main__":
    main()
