"""Проверка модели, которая смотрит кадры, на уже размеченных роликах.

tools/vision_labels.json: 258 роликов, по которым модератор принял решение 19–21
ноября, с оценкой кадров, сделанной вручную (clean / edited / none; сценок-пародий,
которые модератор позже тоже разрешил, в разметке нет). Скрипт задаёт
те же ролики модели (feed/vision.py) и печатает, насколько она совпала с ручной
оценкой и что это значит для автоматического режима.

Запуск: workflow "vision" (Actions -> vision -> Run workflow). Нужен секрет
ANTHROPIC_API_KEY или GEMINI_API_KEY. На ленту не влияет.
"""
import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feed import net, vision  # noqa: E402

LIMIT = int(os.environ.get("EVAL_LIMIT") or 0)
PAUSE = float(os.environ.get("EVAL_PAUSE") or (4.5 if vision.provider() == "gemini" else 0.3))


def main():
    if not vision.enabled():
        sys.exit("нет ключа модели: задайте секрет ANTHROPIC_API_KEY или GEMINI_API_KEY")
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "vision_labels.json"), encoding="utf-8") as f:
        labels = json.load(f)
    with open("data/db.json", encoding="utf-8") as f:
        items = json.load(f)["items"]
    todo = [(k, v) for k, v in sorted(labels.items()) if k in items]
    if LIMIT:
        todo = todo[:LIMIT]
    print(f"модель: {vision.provider()}, роликов: {len(todo)}, пауза {PAUSE} с")
    table = collections.Counter()        # (ручная оценка, ответ модели)
    by_decision = collections.Counter()  # (ответ модели, решение модератора)
    failed = 0
    for n, (key, mine) in enumerate(todo, 1):
        item = items[key]
        try:
            result = vision.check(item)
        except net.ApiError as e:
            failed += 1
            print(f"  сбой {key}: {e}")
            if e.status in (401, 403) or failed >= 8:
                sys.exit("модель недоступна: дальше проверять бессмысленно")
            time.sleep(PAUSE * 4)
            continue
        got = result["verdict"] if result else "нет ответа"
        if not result and table[("?", "debug")] < 5:      # первые несколько случаев: что именно пришло
            table[("?", "debug")] += 1
            print(f"  нет ответа {key}: {vision.last}")
        table[(mine, got)] += 1
        decision = "одобрен" if item["status"] == "published" else "пропущен"
        by_decision[(got, decision)] += 1
        if got != mine:
            print(f"  расхождение {key}: вручную {mine}, модель {got}"
                  f" ({(result or {}).get('why', '')}) | {decision} | {item.get('title', '')[:60]}")
        time.sleep(PAUSE)
    kinds = ["clean", "skit", "edited", "none", "нет ответа"]
    print("\nручная оценка -> ответ модели")
    print(f"{'':10}" + "".join(f"{k:>12}" for k in kinds))
    for mine in ("clean", "edited", "none"):
        print(f"{mine:10}" + "".join(f"{table[(mine, got)]:>12}" for got in kinds))
    del table[("?", "debug")]
    total = sum(table.values())
    same = sum(v for (a, b), v in table.items() if a == b)
    # для автоматического режима важно одно: выпускать (clean) или нет
    gate = sum(v for (a, b), v in table.items() if (a == "clean") == (b in vision.PASS))
    print(f"\nсовпало с ручной оценкой: {same} из {total} ({same / max(total, 1):.0%});"
          f" по вопросу «выпускать или нет»: {gate} из {total} ({gate / max(total, 1):.0%})")
    junk_out = sum(v for (a, b), v in table.items() if a != "clean" and b in vision.PASS)
    good_lost = sum(v for (a, b), v in table.items() if a == "clean" and b in ("edited", "none"))
    print(f"мусор, который модель выпустила бы: {junk_out}; чистый геймплей, который она отсекла бы: {good_lost}")
    print("\nответ модели -> решение модератора")
    for got in kinds:
        a, b = by_decision[(got, "одобрен")], by_decision[(got, "пропущен")]
        if a + b:
            print(f"  {got:12} одобрено {a:>3}, пропущено {b:>3}")
    print(f"сбоев: {failed}")


if __name__ == "__main__":
    main()
