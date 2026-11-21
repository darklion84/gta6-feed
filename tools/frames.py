"""Разовый эксперимент: кадры роликов, по которым админ уже принял решение.

Скачивает с YouTube по несколько кадров каждого ролика и собирает из них листы
по восемь роликов. На листах только условные номера, без названий и без решений:
оценка кадров делается вслепую, а с решениями сверяется потом.

Запускается вручную из workflow "frames"; результат лежит в артефакте запуска.
В работе ленты не участвует и ничего в ней не меняет.
"""
import io
import json
import os
import random
import sys

import requests
from PIL import Image, ImageDraw

OUT = sys.argv[1] if len(sys.argv) > 1 else "frames_out"
BOX_W, BOX_H, LABEL_H, PER_CLIP, COLS, ROWS = 200, 300, 26, 4, 2, 4
# сначала кадры в родных пропорциях (у вертикальных роликов они вертикальные), затем обычные
VARIANTS = [("oardefault", "oar1", "oar2", "oar3"), ("hqdefault", "hq1", "hq2", "hq3")]


def fetch(video_id, name):
    try:
        r = requests.get(f"https://i.ytimg.com/vi/{video_id}/{name}.jpg", timeout=15)
        if r.status_code != 200 or len(r.content) < 1500:
            return None
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception:
        return None


def frames(video_id):
    for names in VARIANTS:
        got = [(n, fetch(video_id, n)) for n in names]
        if sum(1 for _, im in got if im) >= 2:
            return names[0][:3], [im for _, im in got]
    return "none", [None] * PER_CLIP


def cell(code, images):
    tile = Image.new("RGB", (BOX_W * PER_CLIP, BOX_H + LABEL_H), (18, 18, 24))
    draw = ImageDraw.Draw(tile)
    draw.text((6, 6), f"#{code}", fill=(255, 220, 90))
    for n, im in enumerate(images):
        if im is None:
            continue
        im.thumbnail((BOX_W - 4, BOX_H - 4))
        tile.paste(im, (n * BOX_W + (BOX_W - im.width) // 2, LABEL_H + (BOX_H - im.height) // 2))
    return tile


def main():
    with open("data/db.json", encoding="utf-8") as f:
        items = json.load(f)["items"]
    decided = [i for i in items.values() if i.get("platform") == "yt" and i.get("mod_sent_at")
               and (i["status"] == "rejected" or (i["status"] == "published" and i.get("source") != "manual"))]
    decided.sort(key=lambda i: i["key"])
    random.Random(6).shuffle(decided)          # порядок не зависит ни от времени, ни от решения
    os.makedirs(OUT, exist_ok=True)
    index, kinds, tiles = {}, {}, []
    for code, item in enumerate(decided, 1):
        kind, images = frames(item["id"])
        kinds[kind] = kinds.get(kind, 0) + 1
        index[str(code)] = {"key": item["key"], "frames": kind}
        tiles.append(cell(code, images))
    per_sheet = COLS * ROWS
    for n in range(0, len(tiles), per_sheet):
        sheet = Image.new("RGB", (COLS * BOX_W * PER_CLIP + (COLS - 1) * 12, ROWS * (BOX_H + LABEL_H + 8)), (0, 0, 0))
        for m, tile in enumerate(tiles[n:n + per_sheet]):
            sheet.paste(tile, ((m % COLS) * (BOX_W * PER_CLIP + 12), (m // COLS) * (BOX_H + LABEL_H + 8)))
        sheet.save(os.path.join(OUT, f"sheet_{n // per_sheet + 1:02d}.jpg"), quality=82)
    with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f)
    print("роликов:", len(decided), "кадры:", kinds, "листов:", (len(tiles) + per_sheet - 1) // per_sheet)


if __name__ == "__main__":
    main()
