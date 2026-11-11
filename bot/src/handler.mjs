// 机器人主入口：接收企业微信回调 → 分流处理；以及作者点审批链接后自动发码。
//
// 设计取舍：
// · 回复客户一律走「客服 API 主动发消息」，回调只回 success。
//   被动响应 XML 有两种格式坑（事件/消息），主动发送更可控。
// · 审批链接必须带 HMAC 签名，否则任何人猜到订单号就能白嫖授权码。
// · 订单一旦发过码就锁死，避免手抖点两次给同一个人发两张。

import crypto from "node:crypto";
import fs from "node:fs";
import {
  checkSignature,
  decryptMessage,
  sendKfText,
  pushGroupMarkdown,
  signOrder,
  verifyOrderSignature,
} from "./wecom.mjs";
import { issueLicense, daysFor, PLANS } from "./license.mjs";
import { buildReplies, matchReply, extractMachine, looksPaid } from "./replies.mjs";
import { saveOrder, getOrder, listRecentOrders } from "./store.mjs";
import { EventBus } from "./events.mjs";
import { createDashboard } from "./dashboard.mjs";

const json = (obj, status = 200, extraHeaders = {}) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...extraHeaders },
  });

const html = (body, status = 200) =>
  new Response(body, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });

// 客户端标识脱敏：看板是给人扫一眼的，不需要完整 externalUserId
const maskId = (id) => {
  const s = String(id || "");
  if (!s) return "";
  return s.length <= 6 ? s.slice(0, 2) + "***" : s.slice(0, 6) + "***" + s.slice(-2);
};

// 从 XML 里抠字段（企业微信回调是 XML；新版部分接口用 JSON，这里都兼容）
function xmlField(xml, tag) {
  const m = String(xml || "").match(
    new RegExp(`<${tag}>(?:<!\\[CDATA\\[)?([\\s\\S]*?)(?:\\]\\]>)?</${tag}>`)
  );
  return m ? m[1] : "";
}

export function createHandler({ cfg, store, replies, bus: injectedBus }) {
  const replyLib = replies || buildReplies(cfg);
  const bus =
    injectedBus ||
    new EventBus({
      bufferSize: cfg.sseBufferSize || 200,
      maxSubscribers: cfg.sseMaxClients || 50,
    });

  async function handleCallback(url, bodyRaw) {
    const ts = url.searchParams.get("timestamp") || "";
    const nonce = url.searchParams.get("nonce") || "";
    const sig = url.searchParams.get("msg_signature") || url.searchParams.get("signature") || "";

    if (cfg.token && cfg.aesKey && !checkSignature(cfg.token, ts, nonce, sig)) {
      return json({ errcode: 1, errmsg: "signature mismatch" }, 403);
    }

    // 回调 URL 验证：官方会带 echostr，解密后原样返回
    const echostr = url.searchParams.get("echostr");
    if (echostr) {
      try {
        const { message } = decryptMessage(echostr, cfg.aesKey);
        return new Response(message);
      } catch (e) {
        return json({ errcode: 2, errmsg: "echostr decrypt failed: " + e.message }, 400);
      }
    }

    // 解析消息体：XML 优先，JSON 兜底
    let payload = bodyRaw;
    const encMatch = String(bodyRaw || "").match(
      /<Encrypt>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/Encrypt>/
    );
    if (encMatch) {
      try {
        payload = decryptMessage(encMatch[1], cfg.aesKey).message;
      } catch (e) {
        return json({ errcode: 3, errmsg: "decrypt failed: " + e.message }, 400);
      }
    }

    let msgType = "";
    let content = "";
    let externalUserId = "";
    let openKfId = "";

    if (String(payload).trim().startsWith("{")) {
      try {
        const j = JSON.parse(payload);
        msgType = j.MsgType || j.msgtype || "";
        content = j.Content || j.text?.content || "";
        externalUserId = j.ExternalUserId || j.touser || j.from || "";
        openKfId = j.OpenKfId || "";
      } catch {
        /* 解析失败按空处理，下面会有兜底 */
      }
    } else {
      msgType = xmlField(payload, "MsgType");
      content = xmlField(payload, "Content");
      externalUserId = xmlField(payload, "ExternalUserId") || xmlField(payload, "FromUserName");
      openKfId = xmlField(payload, "OpenKfId");
    }

    // 事件类回调（客户进入会话等）不需要回复
    if (msgType && msgType !== "text") {
      return new Response("success");
    }

    const kfId = openKfId || cfg.openKfId;

    // ── 实时看板埋点：客户说了什么，运营要第一时间看到 ────────────────────
    // 只推截断后的预览，不推完整会话；externalUserId 做脱敏
    const machine = extractMachine(content);
    if (externalUserId) {
      bus.publish("message.received", {
        from: maskId(externalUserId),
        kind: machine ? "machine" : "text",
        paid: looksPaid(content),
        preview: String(content || "").replace(/\s+/g, " ").slice(0, 120),
      });
    }

    // ── 分流：机器码订单 vs 普通咨询 ────────────────────────────────────
    if (machine && externalUserId) {
      const id = crypto.randomBytes(8).toString("hex");
      const order = {
        id,
        machine,
        externalUserId,
        openKfId: kfId,
        raw: String(content).slice(0, 500),
        paid: looksPaid(content),
        status: "pending",
        createdAt: Date.now(),
      };
      await saveOrder(store, order);

      // 新授权请求 = 看板上最需要被看见的事件
      bus.publish("order.created", {
        id,
        machine,
        from: maskId(externalUserId),
        paid: order.paid,
        plan: cfg.defaultPlan,
      });

      const link = `${cfg.publicBaseUrl}/approve?id=${id}&sig=${signOrder(id, cfg.approveSecret)}`;
      const plan = PLANS[cfg.defaultPlan] || PLANS.trial10;

      if (cfg.groupWebhook) {
        await pushGroupMarkdown(
          cfg.groupWebhook,
          [
            "**流量捕手 · 新授权请求**",
            `> 机器码：\`${machine}\``,
            `> 客户标识：\`${externalUserId}\``,
            `> 疑似已付款：**${order.paid ? "是" : "否（先别放行）"}**`,
            `> 默认套餐：${plan.label}（${plan.quota} 次/天）`,
            order.raw ? `> 原文：${order.raw.replace(/\n/g, " ")}` : "",
            "",
            `[点此放行并自动发码](${link})`,
            "",
            "放行后授权码会自动发给客户，该链接一次性有效。",
          ]
            .filter(Boolean)
            .join("\n")
        ).catch(() => {
          // 推送失败不能让客户收不到应答，这里吞掉，靠订单列表兜底
        });
      }

      if (kfId && cfg.corpId && cfg.corpSecret) {
        await sendKfText({
          corpId: cfg.corpId,
          corpSecret: cfg.corpSecret,
          openKfId: kfId,
          externalUserId,
          content:
            `收到你的机器码：${machine}\n\n` +
            (order.paid
              ? "正在为你开通，稍等片刻授权码就会发过来。"
              : "请先把付款截图发过来，我确认后立即给你授权码。"),
        }).catch(() => {});
      }
      return new Response("success");
    }

    // 普通咨询：关键词自动回复
    if (externalUserId && kfId && cfg.corpId && cfg.corpSecret) {
      await sendKfText({
        corpId: cfg.corpId,
        corpSecret: cfg.corpSecret,
        openKfId: kfId,
        externalUserId,
        content: matchReply(replyLib, content),
      }).catch(() => {});
    }
    return new Response("success");
  }

  // ── 审批：作者点链接 → 签发 → 自动发给客户 ──────────────────────────────
  async function handleApprove(url) {
    const id = url.searchParams.get("id") || "";
    const sig = url.searchParams.get("sig") || "";
    const planKey = url.searchParams.get("plan") || cfg.defaultPlan;

    if (!verifyOrderSignature(id, sig, cfg.approveSecret)) {
      return html(page("签名无效", "这个审批链接无效或被篡改过，请从群通知里重新点开。", false), 403);
    }

    const order = await getOrder(store, id);
    if (!order) {
      return html(page("订单不存在", "没找到这个订单，可能已过期或被清理。请让客户重新发一次机器码。", false), 404);
    }
    if (order.status === "issued") {
      return html(
        page("已经发过了", `这个订单已于 ${new Date(order.issuedAt).toLocaleString("zh-CN")} 签发过授权码，不会重复发送。`, true),
        200
      );
    }

    const plan = PLANS[planKey] || PLANS.trial10;
    const days = daysFor(planKey);

    let code;
    try {
      code = issueLicense({
        seedHex: cfg.seedHex,
        token: cfg.apiToken || "",
        tokenSeedHex: cfg.tokenSeedHex,
        machine: order.machine,
        days,
        customer: (order.raw || "客户").slice(0, 8) || "客户",
        quota: plan.quota,
      });
    } catch (e) {
      bus.publish("license.failed", {
        id: order.id,
        machine: order.machine,
        plan: plan.label,
        sent: false,
        error: "签发失败：" + String(e.message || e).slice(0, 160),
      });
      return html(page("签发失败", String(e.message || e), false), 500);
    }

    let sent = false;
    let sendErr = "";
    try {
      await sendKfText({
        corpId: cfg.corpId,
        corpSecret: cfg.corpSecret,
        openKfId: order.openKfId || cfg.openKfId,
        externalUserId: order.externalUserId,
        content:
          `你的授权码（${plan.label}，${plan.quota} 次/天，${days} 天）：\n\n${code}\n\n` +
          "复制它 → 软件里「设置 → 授权与激活」粘贴 → 点激活即可。",
      });
      sent = true;
    } catch (e) {
      sendErr = String(e.message || e);
    }

    order.status = sent ? "issued" : "failed";
    order.issuedAt = Date.now();
    order.codeSent = sent;
    await saveOrder(store, order);

    bus.publish(sent ? "license.issued" : "license.failed", {
      id: order.id,
      machine: order.machine,
      plan: plan.label,
      days,
      sent,
      error: sent ? "" : String(sendErr || "").slice(0, 200),
    });

    if (sent) {
      return html(
        page(
          "已放行，授权码已发给客户",
          `机器码：${order.machine}<br>套餐：${plan.label}（${plan.quota} 次/天，${days} 天）<br><br>授权码：<br><code>${code}</code>`,
          true
        )
      );
    }
    return html(
      page(
        "已签发，但发送失败",
        `授权码已生成，但发给客户时出错：${sendErr}<br><br>请手动把下面这串码发给客户：<br><code>${code}</code>`,
        false
      ),
      500
    );
  }

  const dashboard = createDashboard({ cfg, store, bus });

  async function handleRequest(request) {
    const url = new URL(request.url);
    const p = url.pathname;

    if (p === "/health") {
      return json({
        ok: true,
        plan: cfg.defaultPlan,
        hasWebhook: !!cfg.groupWebhook,
        dashboard: cfg.dashboardEnabled,
        subscribers: bus.stats().subscribers,
      });
    }

    if (p === "/dashboard" || p === "/dashboard/") return dashboard.page();
    if (p === "/session") return dashboard.session(request);
    if (p === "/dashboard/login") {
      return request.method === "POST"
        ? dashboard.login(request)
        : json({ errcode: 405, errmsg: "请用 POST" }, 405);
    }
    if (p === "/dashboard/logout") return dashboard.logout();
    if (p === "/events") return dashboard.events(request, url);
    if (p === "/events/status") return dashboard.status(request);
    if (p === "/orders") return dashboard.orders(request, url);

    if (p === "/approve") return handleApprove(url);

    if (request.method === "GET") return handleCallback(url, "");

    if (request.method === "POST") {
      const raw = await request.text();
      return handleCallback(url, raw);
    }

    return json({ errcode: 404, errmsg: "not found" }, 404);
  }

  // 挂到函数上，server.mjs 停机时要用它把长连接掐掉
  handleRequest.bus = bus;
  handleRequest.dashboardEnabled = cfg.dashboardEnabled;
  return handleRequest;
}

function page(title, body, success) {
  const color = success ? "#0F6E56" : "#A32D2D";
  return `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<div style="max-width:560px;margin:40px auto;padding:24px;font-family:-apple-system,'Microsoft YaHei',sans-serif;line-height:1.7">
<h2 style="color:${color};margin:0 0 16px;font-size:18px">${title}</h2>
<div style="font-size:14px;color:#333">${body}</div>
</div>`;
}
