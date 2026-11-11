// SSE 推送端 + 看板会话鉴权。
//
// 为什么不用「token 放 URL 查询参数」：
//   EventSource 不能自定义请求头，网上常见做法是把长期 token 拼在 URL 上，
//   但 URL 会进 Nginx access.log、浏览器历史、Referer —— 等于把印钞机密码写进日志。
//
// 所以这里的做法：
//   1. 运营在看板输入 DASHBOARD_TOKEN → POST /dashboard/login
//   2. 服务端比对通过后下发 httpOnly + SameSite=Strict 的签名 cookie（8 小时）
//   3. EventSource 同源请求自动带 cookie，URL 里干干净净
//   cookie 值 = exp + HMAC(DASHBOARD_TOKEN, exp)，无状态、不落库，冷启动也能验。
//   攻击者即使拿到 cookie 也无法反推出 token，且 8 小时后自动失效。

import crypto from "node:crypto";

export const COOKIE_NAME = "llb_dash";
export const SESSION_TTL_MS = 8 * 60 * 60 * 1000; // 8 小时
export const HEARTBEAT_MS = 15000; // 15 秒，低于任何常见反代的 60s 空闲超时

const enc = new TextEncoder();

// ── cookie 工具 ───────────────────────────────────────────────────────────

export function parseCookies(header) {
  const out = {};
  for (const part of String(header || "").split(";")) {
    const i = part.indexOf("=");
    if (i < 0) continue;
    const k = part.slice(0, i).trim();
    const v = part.slice(i + 1).trim();
    if (k) out[k] = decodeURIComponent(v);
  }
  return out;
}

function hmac(secret, payload) {
  return crypto.createHmac("sha256", String(secret)).update(payload).digest("hex");
}

export function signSession(secret, exp = Date.now() + SESSION_TTL_MS) {
  return `${exp}.${hmac(secret, String(exp))}`;
}

/** 校验 cookie 值，返回过期时间戳（毫秒）；无效/过期返回 0 */
export function verifySession(secret, value) {
  const [expStr, sig] = String(value || "").split(".");
  if (!expStr || !sig || !secret) return 0;
  const exp = Number(expStr);
  if (!Number.isFinite(exp) || exp <= Date.now()) return 0;
  const expect = hmac(secret, expStr);
  // 长度不同时 timingSafeEqual 会抛，先挡掉
  if (sig.length !== expect.length) return 0;
  try {
    if (!crypto.timingSafeEqual(Buffer.from(sig, "hex"), Buffer.from(expect, "hex"))) return 0;
  } catch {
    return 0;
  }
  return exp;
}

export function buildSessionCookie(cfg, value, maxAgeSec = Math.floor(SESSION_TTL_MS / 1000)) {
  const parts = [
    `${COOKIE_NAME}=${encodeURIComponent(value)}`,
    "Path=/",
    "HttpOnly",
    "SameSite=Strict",
    `Max-Age=${maxAgeSec}`,
  ];
  if (isHttps(cfg)) parts.push("Secure");
  return parts.join("; ");
}

export function buildClearCookie(cfg) {
  const parts = [`${COOKIE_NAME}=`, "Path=/", "HttpOnly", "SameSite=Strict", "Max-Age=0"];
  if (isHttps(cfg)) parts.push("Secure");
  return parts.join("; ");
}

function isHttps(cfg) {
  if (cfg?.secureCookies === true) return true;
  if (cfg?.secureCookies === false) return false;
  return /^https:/i.test(String(cfg?.publicBaseUrl || ""));
}

// ── 鉴权 ─────────────────────────────────────────────────────────────────

/** 对比口令，恒定时间，避免按位爆破 */
export function safeEqual(input, expected) {
  const a = Buffer.from(String(input || ""));
  const b = Buffer.from(String(expected || ""));
  if (!a.length || !b.length) return false;
  if (a.length !== b.length) {
    // 长度不等也要走一次比较，别让长度差异变成时间侧信道
    crypto.timingSafeEqual(b, b);
    return false;
  }
  return crypto.timingSafeEqual(a, b);
}

/**
 * 请求是否已登录看板。
 * @returns {{ok:boolean, reason?:string}}
 */
export function checkDashboardAuth(cfg, request) {
  if (!cfg?.dashboardToken) {
    return { ok: false, reason: "not_configured" };
  }
  const cookies = parseCookies(request.headers.get("cookie"));
  const exp = verifySession(cfg.dashboardToken, cookies[COOKIE_NAME]);
  if (!exp) return { ok: false, reason: "unauthenticated" };
  return { ok: true };
}

// ── SSE 流 ───────────────────────────────────────────────────────────────

/**
 * 构造 SSE 响应。
 * @param {object} opts
 * @param {import("./events.mjs").EventBus} opts.bus
 * @param {string[]|null} [opts.topics]
 * @param {string} [opts.lastEventId] 断线重连时浏览器的 Last-Event-ID
 * @param {number} [opts.heartbeatMs]
 * @param {object} [opts.meta] 订阅者元信息（仅用于排障）
 * @returns {Response}
 */
export function createSseResponse({ bus, topics = null, lastEventId = "", heartbeatMs = HEARTBEAT_MS, meta = {} }) {
  let cleanup = () => {};
  let closed = false;
  let timer = null;
  let sub = null;

  const stream = new ReadableStream({
    start(controller) {
      const write = (chunk) => {
        if (closed) return false;
        try {
          controller.enqueue(enc.encode(chunk));
          return true;
        } catch {
          // 消费者已取消（浏览器关页 / 断网），enqueue 会抛
          cleanup();
          return false;
        }
      };

      // retry：告诉浏览器断线后 3 秒重连，别用默认值死等
      write(`retry: 3000\n\n`);

      const replay = bus.replaySince(lastEventId);
      try {
        sub = bus.subscribe({
          topics,
          meta: { ...meta, lastEventId: String(lastEventId || "") },
          send: (frame) => write(frame),
          // 先订阅再发 ready，但静默掉 presence 广播 —— 否则首帧会变成 presence.changed
          silent: true,
          close: () => {
            if (closed) return;
            closed = true;
            if (timer) clearInterval(timer);
            try {
              controller.close();
            } catch {
              /* 已关闭 */
            }
          },
        });
      } catch (e) {
        write(
          `event: error\ndata: ${JSON.stringify({
            type: "error",
            data: { code: e.code || "SUBSCRIBE_FAILED", message: String(e.message || e) },
          })}\n\n`
        );
        closed = true;
        try {
          controller.close();
        } catch {
          /* ignore */
        }
        return;
      }

      cleanup = () => {
        if (closed) return;
        closed = true;
        if (timer) clearInterval(timer);
        try {
          sub?.unsubscribe?.();
        } catch {
          /* 已摘除 */
        }
        try {
          controller.close();
        } catch {
          /* 已关闭 */
        }
      };

      // 首帧：把服务端状态告诉前端，便于页面立刻显示连接成功
      write(
        `event: ready\ndata: ${JSON.stringify({
          type: "ready",
          ts: Date.now(),
          data: {
            serverTime: Date.now(),
            replayFrom: String(lastEventId || ""),
            replayed: replay.length,
            subscribers: bus.stats().subscribers,
            heartbeatMs,
          },
        })}\n\n`
      );

      // 补推断线期间漏掉的事件
      for (const ev of replay) write(`id: ${ev.id}\nevent: ${ev.type}\ndata: ${JSON.stringify(ev)}\n\n`);

      // 自己的加入也要让其他看板看到（前面 silent 掉了，这里补一次）
      bus.publishTransient("presence.changed", { subscribers: bus.stats().subscribers });

      timer = setInterval(() => {
        // 注释行心跳：不触发 onmessage，但能让 TCP 连接和反代都保持活着
        write(`: ping ${Date.now()}\n\n`);
      }, heartbeatMs);
      if (typeof timer.unref === "function") timer.unref();
    },

    cancel() {
      cleanup();
    },
  });

  return new Response(stream, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-store, no-transform",
      "X-Accel-Buffering": "no", // Nginx 必须关缓冲，否则会攒一堆才吐
      "Content-Encoding": "identity",
    },
  });
}
