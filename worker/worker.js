// Мгновенный обработчик Telegram-бота.
//
// Telegram присылает сюда каждое нажатие кнопки и каждое сообщение (webhook).
// Воркер реагирует сразу:
//   - "В канал": публикует ролик в канале, удаляет сообщение с кандидатом;
//   - "Пропустить": удаляет сообщение с кандидатом;
//   - ссылка или пересланный пост от админа: кладёт в очередь и будит сборщик.
// Всё, что он сделал, складывается в очередь; сборщик (GitHub Actions) забирает
// её через /pull и обновляет базу и сайт. Чужие сообщения и нажатия игнорируются.

import { DurableObject } from "cloudflare:workers";

const HELP =
  "Пришлите ссылку на ролик (YouTube, TikTok, Instagram, пост публичного Telegram-канала), " +
  "после неё можно дописать подпись. Или перешлите сюда пост с видео. " +
  "Кандидаты из поиска приходят сюда с кнопками.";

const DISPATCH_GAP_MS = 45_000;       // не будить сборщик чаще
const WARN_GAP_MS = 12 * 3600_000;    // не чаще напоминать о сломанном токене
const CLAIM_TTL_S = 3 * 86400;

async function sha256hex(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
// Оба ключа выводятся из токена бота, поэтому отдельных секретов не нужно:
// сборщик и выкладка считают их так же.
const hookSecret = (env) => sha256hex("hook:" + env.TELEGRAM_BOT_TOKEN);
const syncKey = (env) => sha256hex("sync:" + env.TELEGRAM_BOT_TOKEN);

function adminIds(env) {
  return new Set(String(env.TELEGRAM_ADMIN_IDS || "").split(/[,;\s]+/).filter(Boolean).map(Number));
}

async function tg(env, method, payload) {
  const base = env.TELEGRAM_API || "https://api.telegram.org";
  try {
    const r = await fetch(`${base}/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    return await r.json();
  } catch (e) {
    return { ok: false, description: "нет связи с Telegram" };
  }
}

// Запуск сборщика в GitHub Actions. -> HTTP-статус (204 = принято), 0 = токен не задан.
async function dispatchWorkflow(env) {
  if (!env.DISPATCH_TOKEN) return 0;
  const base = env.GITHUB_API || "https://api.github.com";
  try {
    const r = await fetch(`${base}/repos/${env.REPO}/actions/workflows/${env.WORKFLOW}/dispatches`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${env.DISPATCH_TOKEN}`,
        accept: "application/vnd.github+json",
        "x-github-api-version": "2022-11-28",
        "user-agent": "gta6-feed-bot",
        "content-type": "application/json",
      },
      body: JSON.stringify({ ref: "main" }),
    });
    return r.status;
  } catch (e) {
    return -1;
  }
}

export class Queue extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    const sql = ctx.storage.sql;
    sql.exec("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL)");
    sql.exec("CREATE TABLE IF NOT EXISTS claims (key TEXT PRIMARY KEY, at INTEGER NOT NULL)");
    sql.exec("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v INTEGER NOT NULL)");
  }

  #get(k) {
    const row = this.ctx.storage.sql.exec("SELECT v FROM meta WHERE k = ?", k).toArray()[0];
    return row ? row.v : 0;
  }
  #set(k, v) {
    this.ctx.storage.sql.exec("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", k, v);
  }

  // Занять ролик: второе нажатие по тому же кандидату вернёт false.
  claim(key) {
    const sql = this.ctx.storage.sql;
    if (sql.exec("SELECT 1 FROM claims WHERE key = ?", key).toArray().length) return false;
    sql.exec("INSERT INTO claims (key, at) VALUES (?, ?)", key, Math.floor(Date.now() / 1000));
    return true;
  }
  unclaim(key) {
    this.ctx.storage.sql.exec("DELETE FROM claims WHERE key = ?", key);
  }

  push(event) {
    this.ctx.storage.sql.exec("INSERT INTO events (body) VALUES (?)", JSON.stringify(event));
  }

  // Сборщик подтверждает уже обработанное (ack) и забирает остальное.
  pull(ack) {
    const sql = this.ctx.storage.sql;
    if (ack > 0) sql.exec("DELETE FROM events WHERE seq <= ?", ack);
    sql.exec("DELETE FROM claims WHERE at < ?", Math.floor(Date.now() / 1000) - CLAIM_TTL_S);
    return sql.exec("SELECT seq, body FROM events ORDER BY seq LIMIT 200").toArray()
      .map((row) => ({ seq: row.seq, ...JSON.parse(row.body) }));
  }

  // Разбудить сборщик. Частые события склеиваются: не чаще раза в 45 секунд,
  // но последнее событие никогда не теряется (доставит будильник).
  async kick(force) {
    const now = Date.now();
    const last = this.#get("last_dispatch");
    if (!force && now - last < DISPATCH_GAP_MS) {
      if ((await this.ctx.storage.getAlarm()) === null) await this.ctx.storage.setAlarm(last + DISPATCH_GAP_MS);
      return "later";
    }
    return this.#fire(now);
  }
  async alarm() {
    await this.#fire(Date.now());
  }
  async #fire(now) {
    this.#set("last_dispatch", now);
    const status = await dispatchWorkflow(this.env);
    if (status !== 0 && status !== 204 && now - this.#get("last_warn") > WARN_GAP_MS) {
      this.#set("last_warn", now);
      for (const id of adminIds(this.env)) {
        await tg(this.env, "sendMessage", {
          chat_id: id,
          text: `⚠️ Не удалось запустить сборщик на GitHub (код ${status}). ` +
                "Скорее всего, истёк токен DISPATCH_TOKEN: выпустите новый и обновите секрет. " +
                "Кнопки продолжают работать, но сайт будет обновляться с задержкой.",
        });
      }
    }
    return status;
  }
}

// Текст кандидата в том виде, в каком он должен появиться в канале.
function channelPost(msg) {
  let text = msg.text || "";
  let entities = msg.entities || [];
  // старые кандидаты несли в тексте служебную строку "⏳ ... 📈 ..." — в канал её не берём
  const cut = text.lastIndexOf("\n\n⏳");
  if (cut > 0) {
    text = text.slice(0, cut);
    entities = entities.filter((e) => e.offset + e.length <= cut);
  }
  const post = { text, entities };
  if (msg.link_preview_options) post.link_preview_options = msg.link_preview_options;
  return post;
}

async function handleTap(env, queue, cb) {
  const answer = (extra) => tg(env, "answerCallbackQuery", { callback_query_id: cb.id, ...extra });
  const data = cb.data || "";
  const bar = data.indexOf("|");
  const action = bar < 0 ? data : data.slice(0, bar);
  const key = bar < 0 ? "" : data.slice(bar + 1);
  const msg = cb.message;
  if ((action !== "ok" && action !== "no") || !key || !msg || !msg.chat) {
    await answer({});   // строка со статистикой и прочее: просто гасим "часики" на кнопке
    return;
  }
  const remove = () => tg(env, "deleteMessage", { chat_id: msg.chat.id, message_id: msg.message_id });

  if (!(await queue.claim(key))) {
    await answer({ text: "Уже обработано" });
    await remove();
    return;
  }
  let posted = false;
  try {
    if (action === "ok") {
      const r = await tg(env, "sendMessage", { chat_id: env.CHANNEL, ...channelPost(msg) });
      if (!r.ok) {
        await queue.unclaim(key);   // сообщение остаётся: можно нажать ещё раз
        await answer({ text: "Не удалось опубликовать: " + (r.description || "ошибка"), show_alert: true });
        return;
      }
      posted = true;
    }
    await queue.push({ type: "tap", key, action, posted, at: Math.floor(Date.now() / 1000) });
  } catch (e) {
    if (!posted) await queue.unclaim(key);
    throw e;
  }
  await answer({ text: action === "ok" ? "Опубликовано в канале" : "Пропущен" });
  await remove();
  if (posted) await queue.kick(false);   // пусть сборщик сразу обновит сайт
}

async function handleMessage(env, queue, m) {
  const text = m.text || "";
  if (text.startsWith("/start") || text.startsWith("/help")) {
    await tg(env, "sendMessage", { chat_id: m.chat.id, text: HELP });
    return;
  }
  // Сборщик удалит и это подтверждение, и само сообщение админа, как только
  // ролик выйдет в канале. Поэтому номер подтверждения едет в очередь вместе с ним.
  const ack = await tg(env, "sendMessage", {
    chat_id: m.chat.id,
    text: "Принял. Как только опубликую, уберу и это сообщение, и ваше.",
    reply_parameters: { message_id: m.message_id, allow_sending_without_reply: true },
  });
  await queue.push({
    type: "message", message: m, at: Math.floor(Date.now() / 1000),
    ack_id: ack.ok && ack.result ? ack.result.message_id : null,
  });
  await queue.kick(false);
}

async function handleUpdate(env, queue, upd) {
  const admins = adminIds(env);
  const cb = upd.callback_query;
  if (cb) {
    if (cb.from && admins.has(cb.from.id)) await handleTap(env, queue, cb);
    return;
  }
  const m = upd.message;
  if (m && m.from && admins.has(m.from.id) && m.chat && m.chat.type === "private") {
    await handleMessage(env, queue, m);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const queue = env.QUEUE.get(env.QUEUE.idFromName("main"));

    if (request.method === "POST" && url.pathname === "/tg") {
      if (request.headers.get("x-telegram-bot-api-secret-token") !== (await hookSecret(env))) {
        return new Response("forbidden", { status: 403 });
      }
      try {
        await handleUpdate(env, queue, await request.json());
      } catch (e) {
        console.error("update failed:", e && e.message);
      }
      // Всегда 200: иначе Telegram будет слать то же обновление снова и снова.
      return new Response("ok");
    }

    if (request.method === "GET" && url.pathname === "/pull") {
      if (request.headers.get("authorization") !== "Bearer " + (await syncKey(env))) {
        return new Response("forbidden", { status: 403 });
      }
      const ack = Number(url.searchParams.get("ack") || 0) || 0;
      return Response.json({ events: await queue.pull(ack) });
    }

    return new Response("gta6-feed bot is running");
  },

  async scheduled(event, env) {
    await env.QUEUE.get(env.QUEUE.idFromName("main")).kick(true);
  },
};
