// Cloudflare Worker + Durable Object
const LINE_REPLY_ENDPOINT = "https://api.line.me/v2/bot/message/reply";
const LINE_PUSH_ENDPOINT  = "https://api.line.me/v2/bot/message/push";

// 短回退重試：5s→10s→20s→40s→60s→120s→300s
const RETRY_SCHEDULE_MS = [5000, 10000, 20000, 40000, 60000, 120000, 300000];

export class WebhookQueue {
  constructor(ctx, env) {
    this.env = env;
    this.storage = ctx.storage;
    this.queueKey = "queue_v1";
  }

  async fetch(request) {
    const url = new URL(request.url);

    // 進佇列
    if (url.pathname === "/enqueue" && request.method === "POST") {
      const bodyText = await request.text();
      const signature = request.headers.get("x-line-signature") || "";

      // Worker已計算好的通知狀態（thinking 是否已送出）
      const thinkingSentHeader   = request.headers.get("x-thinking-sent") || "0";
      const thinkingMethodHeader = request.headers.get("x-thinking-method") || "none";

      const evt = {
        id: crypto.randomUUID(),
        ts: Date.now(),
        body: bodyText,
        signature,
        tries: 0,
        thinking_sent: thinkingSentHeader === "1",
        thinking_method: thinkingMethodHeader
      };

      try {
        const q = (await this.storage.get(this.queueKey)) || [];
        q.push(evt);
        await this.storage.put(this.queueKey, q);
        console.log(`DO: enqueued ${evt.id} len=${q.length} thinking_sent=${evt.thinking_sent} method=${evt.thinking_method}`);
      } catch (e) {
        console.error("DO: enqueue storage.put fail", e);
        return new Response(JSON.stringify({ error: "storage error" }), { status: 500 });
      }

      // 非阻塞排空
      try { void this._drainOnce(); } catch { /* ignore */ }

      return new Response(JSON.stringify({ status: "enqueued", id: evt.id }), {
        status: 200, headers: { "content-type": "application/json" }
      });
    }

    // 手動排空
    if (url.pathname === "/drain" && request.method === "POST") {
      await this._drainOnce();
      return new Response(JSON.stringify({ status: "drain_triggered" }), {
        status: 200, headers: { "content-type": "application/json" }
      });
    }

    // 查看佇列
    if (url.pathname === "/status" && request.method === "GET") {
      const q = (await this.storage.get(this.queueKey)) || [];
      return new Response(JSON.stringify({ queue_len: q.length, head: q[0] || null }), {
        status: 200, headers: { "content-type": "application/json" }
      });
    }

    return new Response("not found", { status: 404 });
  }

  async alarm() {
    await this._drainOnce();
  }

  async _drainOnce() {
    const renderUrl = this.env.RENDER_CALLBACK_URL;
    if (!renderUrl) {
      console.warn("DO: No RENDER_CALLBACK_URL configured");
      return;
    }

    let q = (await this.storage.get(this.queueKey)) || [];
    if (!q.length) return;

    console.log(`DO: drain start (len=${q.length})`);
    const remaining = [];

    for (let i = 0; i < q.length; i++) {
      const evt = q[i];
      try {
        const res = await fetch(renderUrl, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "x-line-signature": evt.signature,
            "x-proxy-from": "cloudflare-worker",
            "x-thinking-sent": evt.thinking_sent ? "1" : "0",
            "x-thinking-method": evt.thinking_method || "none"
          },
          body: evt.body
        });

        if (res.ok) {
          console.log(`DO: forwarded ${evt.id} -> render (${res.status}) thinking_sent=${evt.thinking_sent}`);
        } else {
          const txt = await res.text().catch(() => "<no-body>");
          console.warn(`DO: forward fail ${evt.id} status=${res.status} body=${txt}`);
          evt.tries = (evt.tries || 0) + 1;
          remaining.push(evt);
        }
      } catch (e) {
        console.warn(`DO: forward exception ${evt.id}`, e);
        evt.tries = (evt.tries || 0) + 1;
        remaining.push(evt);
      }

      // 控制單次處理量（避免執行過久）
      if (i >= 9) {
        remaining.push(...q.slice(i + 1));
        break;
      }
    }

    await this.storage.put(this.queueKey, remaining);

    if (remaining.length > 0) {
      const tries = remaining[0].tries || 1;
      const idx = Math.min(tries - 1, RETRY_SCHEDULE_MS.length - 1);
      const delayMs = RETRY_SCHEDULE_MS[idx];
      if (this.storage && this.storage.setAlarm) {
        await this.storage.setAlarm(Date.now() + delayMs);
        console.log(`DO: next alarm in ${Math.round(delayMs / 1000)}s (remain=${remaining.length})`);
      }
    } else {
      console.log("DO: queue drained");
    }
  }
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === "/line-webhook" && request.method === "POST") {
      const bodyText = await request.text();
      const signature = request.headers.get("x-line-signature") || "";

      // 驗簽
      if (env.LINE_CHANNEL_SECRET) {
        const ok = await verifyLineSignature(env.LINE_CHANNEL_SECRET, bodyText, signature);
        if (!ok) {
          console.warn("Worker: invalid signature");
          return new Response("invalid signature", { status: 403 });
        }
      } else {
        console.warn("Worker: LINE_CHANNEL_SECRET not set; skip verify");
      }

      // 解析並過濾掉 message.text === "名單" 的 events
      let payload = null;
      try {
        payload = JSON.parse(bodyText);
      } catch (e) {
        console.warn("Worker: parse bodyText failed", e);
        payload = null;
      }

      if (payload && Array.isArray(payload.events)) {
        const filteredEvents = payload.events.filter(ev => {
          try {
            if (ev?.message?.type === "text") {
              const txt = (ev.message.text || "").trim();
              if (txt === "名單") return false; // 過濾掉
            }
            return true;
          } catch (e) {
            return true;
          }
        });

        if (!filteredEvents.length) {
          // 全部都是 "名單" 或事件被過濾完，直接回 200（不 reply、不 enqueue）
          console.log("Worker: all events filtered (名單) => ignore and return 200");
          return new Response("ok", { status: 200 });
        }

        // 如果有些事件被過濾，重建 body 裡的 events（只送需要處理的）
        if (filteredEvents.length !== payload.events.length) {
          payload.events = filteredEvents;
        }
      }

      const bodyForProcess = payload ? JSON.stringify(payload) : bodyText;

      // 先通知用戶：reply ->（失敗）-> push
      let thinkingSent = false;
      let thinkingMethod = "none";

      try {
        const parsed = payload || JSON.parse(bodyText);
        const replyRes = await replyThinkingIfPossible(parsed, env.LINE_CHANNEL_ACCESS_TOKEN);
        if (replyRes.ok) {
          thinkingSent = true;
          thinkingMethod = "reply";
        } else {
          const pushRes = await pushThinkingIfPossible(parsed, env.LINE_CHANNEL_ACCESS_TOKEN);
          if (pushRes.ok) {
            thinkingSent = true;
            thinkingMethod = "push";
          }
        }
      } catch (e) {
        console.warn("Worker: thinking notify error", e);
      }

      // 佇列轉送（送到 DO），附上「是否已通知」資訊
      const id = env.WEBHOOK_Q.idFromName("line-queue");
      const stub = env.WEBHOOK_Q.get(id);
      await stub.fetch("https://durable/enqueue", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-line-signature": signature,
          "x-thinking-sent": thinkingSent ? "1" : "0",
          "x-thinking-method": thinkingMethod
        },
        body: bodyForProcess
      });

      return new Response("ok", { status: 200 });
    }

    if (url.pathname === "/drain-all" && request.method === "POST") {
      const id = env.WEBHOOK_Q.idFromName("line-queue");
      const stub = env.WEBHOOK_Q.get(id);
      await stub.fetch("https://durable/drain", { method: "POST" });
      return new Response("drain triggered", { status: 200 });
    }

    if (url.pathname === "/") return new Response("worker ok", { status: 200 });
    return new Response("not found", { status: 404 });
  },

  async scheduled(event, env, ctx) {
    try {
      const id = env.WEBHOOK_Q.idFromName("line-queue");
      const stub = env.WEBHOOK_Q.get(id);
      await stub.fetch("https://durable/drain", { method: "POST" });
      console.log("cron: drain triggered");
    } catch (e) {
      console.warn("cron drain failed", e);
    }
  }
};

// ===== Helper: Reply 優先 =====
async function replyThinkingIfPossible(payload, token) {
  if (!token) {
    console.warn("Worker: no LINE_CHANNEL_ACCESS_TOKEN; skip reply");
    return { ok: false, status: 0 };
  }
  try {
    const events = payload?.events || [];
    let sent = false, status = 0;
    for (const ev of events) {
      if (ev.type !== "message" || ev.message?.type !== "text") continue;
      const txt = (ev.message.text || "").trim();
      if (txt === "名單") continue; // 额外保险：再次跳過
      const replyToken = ev.replyToken;
      if (!replyToken) continue;

      const res = await fetch(LINE_REPLY_ENDPOINT, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "Authorization": `Bearer ${token}`
        },
        body: JSON.stringify({
          replyToken,
          messages: [{ type: "text", text: "📊 思考分析中，請稍候..." }]
        })
      });
      status = res.status;
      if (res.ok) {
        console.log("Worker: reply thinking OK");
        sent = true;
      } else {
        const txtBody = await res.text().catch(() => "<no-body>");
        console.warn(`Worker: reply fail status=${res.status} body=${txtBody}`);
      }
    }
    return { ok: sent, status };
  } catch (e) {
    console.warn("Worker: replyThinking exception", e);
    return { ok: false, status: 0 };
  }
}

// ===== Helper: Reply 失敗就用 Push（userId/groupId/roomId 都可） =====
async function pushThinkingIfPossible(payload, token) {
  if (!token) return { ok: false, status: 0 };
  try {
    const events = payload?.events || [];
    let any = false, status = 0;

    for (const ev of events) {
      if (ev.type !== "message" || ev.message?.type !== "text") continue;
      const txt = (ev.message.text || "").trim();
      if (txt === "名單") continue; // 额外保险：再次跳過

      const src = ev.source || {};
      const to = src.userId || src.groupId || src.roomId;
      if (!to) continue;

      const res = await fetch(LINE_PUSH_ENDPOINT, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "Authorization": `Bearer ${token}`
        },
        body: JSON.stringify({
          to,
          messages: [{ type: "text", text: "📊 思考分析中，請稍候..." }]
        })
      });
      status = res.status;
      if (res.ok) {
        console.log("Worker: push thinking OK ->", (to||"").slice(0,6));
        any = true;
      } else {
        const txtBody = await res.text().catch(() => "<no-body>");
        console.warn(`Worker: push fail status=${res.status} body=${txtBody}`);
      }
    }
    return { ok: any, status };
  } catch (e) {
    console.warn("Worker: pushThinking exception", e);
    return { ok: false, status: 0 };
  }
}

// ===== LINE Signature 驗證 =====
async function verifyLineSignature(secret, bodyText, signatureHeader) {
  if (!secret || !signatureHeader) return false;
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(bodyText));
  return arrayBufferToBase64(sig) === signatureHeader;
}
function arrayBufferToBase64(buffer) {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}