"""Проверка кадров: есть ли в ролике игра и не забит ли он посторонними вставками.

Нужна автоматическому режиму (см. AUTO_MODE в config.py): без модератора в ленту
не должны попадать ролики, где игры нет вовсе или она тонет в мемах и нарезках.

С YouTube берутся четыре кадра ролика (сам ролик не скачивается), склеиваются в
одну картинку и уходят модели вместе с названием. Ответ один из четырёх:

  clean   игра занимает кадр; лицо стримера, субтитры и подписи не мешают
  skit    живые люди разыгрывают сцены, поведение NPC или мемы из игры: тоже годится
  edited  игра есть, но с тяжёлыми вставками: мемы, чужое видео, списки, заставки
  none    игры по сути нет: человек говорит в камеру, коллаж, скриншоты, другая игра

В ленту идут clean и skit (PASS).

Модель подключается секретом репозитория: ANTHROPIC_API_KEY или GEMINI_API_KEY.
"""
import base64
import io
import json
import os
import re

from PIL import Image

import config as cfg
from . import net

VERDICTS = ("clean", "skit", "edited", "none")
PASS = ("clean", "skit")       # с такими ответами ролик может выйти сам
# сначала кадры в родных пропорциях (у вертикальных роликов они вертикальные), затем обычные
FRAME_SETS = (("oardefault", "oar1", "oar2", "oar3"), ("hqdefault", "hq1", "hq2", "hq3"))
FRAME_H = 480          # высота кадра на склейке

PROMPT = """These are four frames taken from one short video about the game GTA 6.
Title: {title}
Channel: {author}

Classify what the video shows.
- "clean": in-game footage is the main content of the frames. A streamer's facecam, subtitles, \
a caption, a small logo, or a split screen comparing two games are all fine.
- "skit": real people act out in-game situations, NPC behaviour or memes from the game in real \
life (a parody or reenactment of the game). This is welcome even though it is not game footage.
- "edited": in-game footage is present, but with heavy inserts: full-screen meme or reaction \
pictures, real-life footage, stock images, ranking or list cards, large artwork banners, or \
footage from unrelated games.
- "none": neither of the above: only a person talking to the camera, a reaction or unboxing, \
real-life video that does not reenact the game, a collage, screenshots of posts, or footage \
of another game.

Answer with JSON only: {{"verdict": "clean" | "skit" | "edited" | "none", "why": "<at most 12 words>"}}"""


last = {}      # подробности последней проверки: для разбора, почему ответа нет


def provider():
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "anthropic"
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return "gemini"
    return None


def enabled():
    return provider() is not None


def frames(video_id):
    """Кадры ролика с YouTube -> (вид, [PIL.Image | None, ...]). Вид "none": кадров нет."""
    for names in FRAME_SETS:
        got = []
        for name in names:
            data = net.get_bytes("ytimg", f"https://i.ytimg.com/vi/{video_id}/{name}.jpg")
            image = None
            if data and len(data) > 1500:      # меньше: заглушка "кадра нет"
                try:
                    image = Image.open(io.BytesIO(data)).convert("RGB")
                except Exception:
                    image = None
            got.append(image)
        if sum(1 for im in got if im) >= 2:
            return names[0][:3], got
    return "none", []


def sheet(images):
    """Склейка кадров в один ряд -> JPEG."""
    tiles = []
    for im in images:
        if im is None:
            continue
        w = max(1, round(im.width * FRAME_H / im.height))
        tiles.append(im.resize((w, FRAME_H)))
    out = Image.new("RGB", (sum(t.width for t in tiles) + 8 * (len(tiles) - 1), FRAME_H), (0, 0, 0))
    x = 0
    for t in tiles:
        out.paste(t, (x, 0))
        x += t.width + 8
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def _ask_anthropic(prompt, image):
    r = net.send("anthropic", "https://api.anthropic.com/v1/messages",
                 headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"].strip(),
                          "anthropic-version": "2023-06-01"},
                 json={"model": cfg.VISION_ANTHROPIC_MODEL, "max_tokens": 200,
                       "messages": [{"role": "user", "content": [
                           {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.b64encode(image).decode()}},
                           {"type": "text", "text": prompt}]}]})
    last["meta"] = {"stop": r.get("stop_reason"), "blocks": [b.get("type") for b in r.get("content", [])],
                    "usage": r.get("usage")}
    return "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")


def _ask_gemini(prompt, image):
    r = net.send("gemini", "https://generativelanguage.googleapis.com/v1beta/models/"
                           f"{cfg.VISION_GEMINI_MODEL}:generateContent",
                 headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"].strip()},
                 json={"contents": [{"parts": [
                     {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(image).decode()}},
                     {"text": prompt}]}],
                     "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 200}})
    first = (r.get("candidates") or [{}])[0]
    last["meta"] = {"stop": first.get("finishReason"), "usage": r.get("usageMetadata")}
    parts = (first.get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts)


def parse(text):
    """Ответ модели -> {"verdict", "why"} или None, если он не по форме."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in VERDICTS:
        return None
    return {"verdict": verdict, "why": str(data.get("why", ""))[:120]}


def check(item):
    """-> {"verdict", "why", "by"} или None, если проверить нечем (нет кадров, ответ не по форме).

    Сбой самой модели (ключ, лимит, сеть) поднимается как net.ApiError.
    """
    who = provider()
    if not who or item.get("platform") != "yt":
        return None
    last.clear()
    kind, images = frames(item["id"])
    last["frames"] = f"{kind}:{sum(1 for im in images if im)}"
    if kind == "none":
        return None
    prompt = PROMPT.format(title=(item.get("title") or "")[:200], author=(item.get("author") or "")[:80])
    ask = _ask_anthropic if who == "anthropic" else _ask_gemini
    text = ask(prompt, sheet(images))
    last["raw"] = (text or "")[:300]
    result = parse(text)
    if result:
        result["by"] = who
    return result
