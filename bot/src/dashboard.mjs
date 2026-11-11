// 运营实时看板的路由层：登录、SSE 订阅、订单快照。
//
// 单独成文件的原因：handler.mjs 已经在管企微回调和发码，是「客户的链路」；
// 看板是「运营的链路」，两者鉴权模型完全不同（回调靠 msg_signature，看板靠会话 cookie）。
// 混在一个文件里早晚会有人把两套鉴权搞串，所以物理隔开。

import fs from "node:fs";
import { listRecentOrders } from "./store.mjs";
import {
  createSseResponse,
  checkDashboardAuth,
  buildSessionCookie,
  buildClearCookie,
  signSession,
  safeEqual,
  SESSION_TTL_MS,
} from "./sse.mjs";

const json = (obj, status = 200, extraHeaders = {}) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...extraHeaders },
  });

const maskId = (id) => {
  const s = String(id || "");
  if (!s) return "";
  return s.length <= 6 ? s.slice(0, 2) + "***" : s.slice(0, 6) + "***" + s.slice(-2);
};

const NOT_ENABLED = {
  errcode: 503,
  errmsg: "实时看板未启用：请在 .env 中设置 DASHBOARD_TOKEN（至少 16 位）后重启机器人",
};

export function createDashboard({ cfg, store, bus }) {
  let pageCache = null;

  function readPage() {
    if (pageCache !== null) return pageCache;
    try {
      pageCache = fs.readFileSync(new URL("../public/dashboard.html", import.meta.url), "utf8");
    } catch {
      pageCache =
        "<!doctype html><meta charset='utf-8'><div style=\"font-family:system-ui;padding:40px\">" +
        "<h2>看板页面缺失</h2><p>找不到 bot/public/dashboard.html，请检查部署包是否漏了 public 目录。</p></div>";
    }
    return pageCache;
  }

  /** 未启用 / 未登录时返回 Response，已登录返回 null */
  function guard(request) {
    if (!cfg.dashboardEnabled) return json(NOT_ENABLED, 503);
    if (!checkDashboardAuth(cfg, request).ok) {
      return json({ errcode: 401, errmsg: "未登录或登录已过期" }, 401);
    }
    return null;
  }

  function page() {
    return new Response(readPage(), {
      status: 200,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        // 看板是内部工具，禁止被搜索引擎/别家 iframe 收录
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
      },
    });
  }

  async function login(request) {
    if (!cfg.dashboardEnabled) return json(NOT_ENABLED, 503);

    const ct = request.headers.get("content-type") || "";
    const raw = await request.text();
    let token = "";
    if (ct.includes("application/json")) {
      try {
        token = String(JSON.parse(raw)?.token || "");
      } catch {
        token = "";
      }
    } else {
      token = new URLSearchParams(raw).get("token") || "";
    }

    if (!safeEqual(token, cfg.dashboardToken)) {
      // 失败也等一下：不让响应时间成为「口令对不对」的探针
      await new Promise((r) => setTimeout(r, 300));
      return json({ ok: false, errmsg: "口令不正确" }, 401);
    }

    const cookie = buildSessionCookie(cfg, signSession(cfg.dashboardToken));
    return json(
      { ok: true, expiresIn: Math.floor(SESSION_TTL_MS / 1000) },
      200,
      { "Set-Cookie": cookie, "Cache-Control": "no-store" }
    );
  }

  function logout() {
    return json({ ok: true }, 200, { "Set-Cookie": buildClearCookie(cfg) });
  }

  function session(request) {
    if (!cfg.dashboardEnabled) return json({ ok: false, reason: "not_configured", errmsg: NOT_ENABLED.errmsg }, 200);
    const auth = checkDashboardAuth(cfg, request);
    return json({ ok: auth.ok, reason: auth.reason || "", dashboard: true });
  }

  function events(request, url) {
    const denied = guard(request);
    if (denied) return denied;

    const topicsRaw = url.searchParams.get("topics") || "";
    const topics = topicsRaw
      ? topicsRaw.split(",").map((t) => t.trim()).filter(Boolean)
      : null;

    return createSseResponse({
      bus,
      topics,
      lastEventId:
        request.headers.get("last-event-id") || url.searchParams.get("lastEventId") || "",
      heartbeatMs: cfg.sseHeartbeatMs,
      meta: {
        ua: String(request.headers.get("user-agent") || "").slice(0, 80),
        ip: String(request.headers.get("x-forwarded-for") || "").split(",")[0].trim(),
      },
    });
  }

  async function orders(request, url) {
    const denied = guard(request);
    if (denied) return denied;

    const limit = Math.min(200, Math.max(1, Number(url.searchParams.get("limit")) || 50));
    const list = await listRecentOrders(store, { limit });

    const todayStart = new Date();
    todayStart.setHours(0, 0, 0, 0);
    const today = list.filter((o) => (o.createdAt || 0) >= todayStart.getTime());

    return json({
      orders: list.map((o) => ({
        id: o.id,
        machine: o.machine,
        from: maskId(o.externalUserId),
        paid: !!o.paid,
        status: o.status || "pending",
        createdAt: o.createdAt || 0,
        issuedAt: o.issuedAt || 0,
        codeSent: !!o.codeSent,
      })),
      // 注意：统计口径是「最近 limit 条里的今日订单」，不是全库，够看板用
      today: {
        total: today.length,
        issued: today.filter((o) => o.status === "issued").length,
        pending: today.filter((o) => o.status === "pending").length,
      },
      serverTime: Date.now(),
    });
  }

  function status(request) {
    const denied = guard(request);
    if (denied) return denied;
    return json({ ok: true, bus: bus.stats(), serverTime: Date.now() });
  }

  return { page, login, logout, session, events, orders, status, guard };
}
