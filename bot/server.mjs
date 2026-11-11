// 本地 HTTP 服务入口：用于自测，也可直接部署到任意有 Node 的服务器。
// 部署到云函数平台时，改为导出 createHandler 即可（见 README）。
//
// 关于 SSE：这里必须做「流式转发」，不能像普通接口那样 await out.text() 再一次 res.end()。
// 否则 ReadableStream 会被整体缓冲，浏览器永远收不到第一帧，表现为「一直转圈」。
// 云函数平台（EdgeOne Makers / SCF）通常有响应时长上限，长连接请部署到常驻进程。
import http from "node:http";
import { loadConfig } from "./src/config.mjs";
import { Store } from "./src/store.mjs";
import { buildReplies } from "./src/replies.mjs";
import { createHandler } from "./src/handler.mjs";

const PORT = Number(process.env.PORT || 8787);
const { cfg, missing, missingRecommended } = loadConfig(process.env);

if (missing.length) {
  console.warn("⚠️  缺少必需环境变量：" + missing.join(", "));
  console.warn("    机器人能起来，但涉及发码/发消息的功能会失败。复制 .env.example 填写后再跑。");
}
if (missingRecommended.length) {
  const others = missingRecommended.filter((k) => k !== "DASHBOARD_TOKEN");
  if (others.length) console.warn("ℹ️  建议也配置：" + others.join(", "));
}
if (!cfg.dashboardEnabled) {
  console.warn("ℹ️  实时看板未启用：设置 DASHBOARD_TOKEN（≥16 位）后可访问 /dashboard");
}

const store = new Store({ file: cfg.storeFile || ".data/orders.json" });
const handler = createHandler({ cfg, store, replies: buildReplies(cfg) });

// 兜底：实时推送是旁路，不该有任何异步逃逸异常把客服机器人打停。
// 未处理的 Promise 拒绝只记日志（多为某个长连接断开时的竞态）；
// 真正的同步崩溃仍然退出，让 pm2 / systemd 拉起来，避免带着坏状态硬扛。
process.on("unhandledRejection", (reason) => {
  console.error("[unhandledRejection]", reason?.stack || reason);
});
process.on("uncaughtException", (err) => {
  console.error("[uncaughtException]", err?.stack || err);
  process.exit(1);
});

const server = http.createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = Buffer.concat(chunks).toString("utf8");
  const request = new Request(`http://${req.headers.host || "localhost"}${req.url}`, {
    method: req.method,
    headers: req.headers,
    body: ["GET", "HEAD"].includes(req.method) ? undefined : body,
  });

  let out;
  try {
    out = await handler(request);
  } catch (e) {
    console.error("[handler] 未捕获异常：", e?.stack || e);
    if (!res.headersSent) {
      res.writeHead(500, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ errcode: 500, errmsg: "服务器内部错误" }));
    } else {
      res.end();
    }
    return;
  }

  const headers = Object.fromEntries(out.headers);
  const contentType = String(headers["content-type"] || "");

  // ── 流式响应（SSE）────────────────────────────────────────────────────
  if (out.body && contentType.includes("text/event-stream")) {
    res.writeHead(out.status, {
      ...headers,
      Connection: "keep-alive",
      "Cache-Control": "no-cache, no-store, no-transform",
    });
    if (typeof res.flushHeaders === "function") res.flushHeaders();

    const reader = out.body.getReader();
    let finished = false;
    const cancelStream = () => {
      if (finished) return;
      finished = true;
      // 关键：body 已被 getReader() 锁定，直接 body.cancel() 会抛 ERR_INVALID_STATE，
      // 而且它是 Promise 拒绝（不是同步 throw），外部 try/catch 抓不到，会直接杀掉进程。
      // 必须走 reader.cancel()，并且兜住拒绝。
      reader.cancel().catch(() => {});
    };
    // 只监听 res 的 close：客户端断开 / 浏览器关页 / 反代超时都走这里
    res.on("close", cancelStream);
    res.on("error", cancelStream);

    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done || finished) break;
        const ok = res.write(Buffer.from(value));
        if (!ok) {
          // 背压：等 drain；客户端断开时 drain 不会来，所以靠 close 兜底
          await new Promise((resolve) => {
            const timer = setTimeout(resolve, 5000);
            const settle = () => {
              clearTimeout(timer);
              res.off("drain", settle);
              res.off("close", settle);
              resolve();
            };
            res.once("drain", settle);
            res.once("close", settle);
          });
        }
      }
    } catch {
      // 客户端主动断开属于正常现象，不当作错误
    } finally {
      cancelStream();
      if (!res.writableEnded) {
        try {
          res.end();
        } catch {
          /* 连接已断 */
        }
      }
    }
    return;
  }

  // ── 普通响应 ─────────────────────────────────────────────────────────
  const text = await out.text();
  res.writeHead(out.status, headers);
  res.end(text);
});

// 长连接不需要 socket 空闲超时（默认已是 0，这里显式声明防止被平台改写）
server.timeout = 0;
server.keepAliveTimeout = 65000;
server.headersTimeout = 66000;

server.listen(PORT, () => {
  console.log(`流量捕手机器人已启动： http://localhost:${PORT}`);
  console.log(`  健康检查  http://localhost:${PORT}/health`);
  console.log(`  回调地址  http://localhost:${PORT}/wecom/callback  （对外需 HTTPS + 公网域名）`);
  if (cfg.dashboardEnabled) {
    console.log(`  实时看板  http://localhost:${PORT}/dashboard`);
    console.log(`  事件流    http://localhost:${PORT}/events   （SSE，需先看板登录）`);
  } else {
    console.log(`  实时看板  未启用（缺 DASHBOARD_TOKEN）`);
  }
});

// ── 优雅停机：不掐掉 SSE 长连接，server.close() 会一直等下去 ────────────────
let shuttingDown = false;
function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(`\n收到 ${signal}，正在关闭实时连接与服务器…`);
  try {
    handler.bus?.closeAll?.();
  } catch {
    /* ignore */
  }
  server.close(() => {
    console.log("已安全退出");
    process.exit(0);
  });
  // 兜底：3 秒内没退干净就强退，别让运维以为卡死
  setTimeout(() => process.exit(0), 3000).unref();
}
process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));

export { createHandler };
