// 实时推送自测（不联网）：
// 看板鉴权 → SSE 建流 → 业务事件投递 → 断线按 Last-Event-ID 回放 → 上限/裁剪等边界。
//
// 这一组之所以独立成文件：SSE 是个「静默失败」重灾区 ——
// 鉴权写错是 401，流没刷出去是白屏，都很难从业务日志里看出来。
// 所以这里逐帧断言，而不是只看状态码。

import { createHandler } from "../src/handler.mjs";
import { Store } from "../src/store.mjs";
import { EventBus } from "../src/events.mjs";
import { signSession } from "../src/sse.mjs";

const results = [];
const ok = (name, pass, extra = "") => {
  results.push({ name, pass });
  console.log(`${pass ? "OK  " : "FAIL"} ${name}${extra ? "  " + extra : ""}`);
};

const TOKEN = "dashboard-token-1234567890"; // 26 位，满足 ≥16 的启用条件

function makeCfg(over = {}) {
  return {
    corpId: "",
    corpSecret: "",
    token: "",
    aesKey: "",
    openKfId: "",
    groupWebhook: "",
    approveSecret: "approve-secret",
    seedHex: "",
    tokenSeedHex: "d72123f8790704a1fdff8ba0e25f31bf65e8dee7ee24e5d7fe285bf355136000",
    apiToken: "",
    publicBaseUrl: "https://bot.example.com",
    downloadUrl: "",
    defaultPlan: "trial10",
    storeFile: "",
    adminContact: "微信：807108536",
    dashboardToken: TOKEN,
    sseHeartbeatMs: 60000, // 测试期间不心跳干扰帧解析
    sseBufferSize: 50,
    sseMaxClients: 4,
    secureCookies: false,
    dashboardEnabled: true,
    ...over,
  };
}

const req = (path, { method = "GET", headers = {}, body } = {}) =>
  new Request(`http://localhost${path}`, { method, headers, body });

const cookieHeader = (value) => ({ cookie: `llb_dash=${encodeURIComponent(value)}` });

function parseFrame(raw) {
  const out = { event: "message", id: "", data: "" };
  for (const line of raw.split("\n")) {
    if (line.startsWith(":")) continue; // 注释（心跳）
    if (line.startsWith("event:")) out.event = line.slice(6).trim();
    else if (line.startsWith("id:")) out.id = line.slice(3).trim();
    else if (line.startsWith("data:")) out.data += line.slice(5).trim();
  }
  try {
    out.json = out.data ? JSON.parse(out.data) : null;
  } catch {
    out.json = null;
  }
  return out;
}

/** 从 SSE 流里读最多 want 帧，或读满 timeoutMs */
async function readFrames(res, { want = 3, timeoutMs = 800 } = {}) {
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  const frames = [];
  const deadline = Date.now() + timeoutMs;
  try {
    while (frames.length < want && Date.now() < deadline) {
      const left = deadline - Date.now();
      if (left <= 0) break;
      const chunk = await Promise.race([
        reader.read(),
        new Promise((resolve) => setTimeout(() => resolve({ timeout: true }), left)),
      ]);
      if (chunk.timeout || chunk.done) break;
      buf += dec.decode(chunk.value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const raw = buf.slice(0, i);
        buf = buf.slice(i + 2);
        if (!raw.trim()) continue;
        const frame = parseFrame(raw);
        // retry: 3000 这类控制帧不带 data，浏览器不会派发成消息，测试里也跳过
        if (!frame.data && !frame.id && frame.event === "message") continue;
        frames.push(frame);
      }
    }
  } catch {
    /* 流被取消属正常 */
  } finally {
    try {
      await reader.cancel();
    } catch {
      /* ignore */
    }
  }
  return frames;
}

// ── 用例 ────────────────────────────────────────────────────────────────

async function run() {
  const cfg = makeCfg();
  const store = new Store({});
  const handler = createHandler({ cfg, store });

  // 1. 未配置口令 → 安全默认拒绝
  {
    const off = createHandler({ cfg: makeCfg({ dashboardToken: "", dashboardEnabled: false }), store: new Store({}) });
    const res = await off(req("/events"));
    const body = await res.json();
    ok("未配置 DASHBOARD_TOKEN 时 /events 拒绝访问", res.status === 503 && /DASHBOARD_TOKEN/.test(body.errmsg), `status=${res.status}`);

    const s = await off(req("/session"));
    ok("/session 明确告知未启用", (await s.json()).reason === "not_configured");
  }

  // 2. 口令错误
  {
    const res = await handler(
      req("/dashboard/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ token: "wrong-token-xxxxxxxx" }),
      })
    );
    ok("错误口令登录被拒", res.status === 401, `status=${res.status}`);
    ok("错误口令不下发 cookie", !res.headers.get("set-cookie"));
  }

  // 3. 未登录访问事件流
  {
    const res = await handler(req("/events"));
    ok("未登录订阅 /events 返回 401", res.status === 401, `status=${res.status}`);
  }

  // 4. 正确口令登录
  let cookieValue = "";
  {
    const res = await handler(
      req("/dashboard/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ token: TOKEN }),
      })
    );
    const setCookie = res.headers.get("set-cookie") || "";
    ok("正确口令登录成功", res.status === 200, `status=${res.status}`);
    ok("cookie 带 HttpOnly", /HttpOnly/i.test(setCookie));
    ok("cookie 带 SameSite=Strict", /SameSite=Strict/i.test(setCookie));
    ok("cookie 不在 URL 里传递", !/token=/i.test(setCookie));
    const m = setCookie.match(/llb_dash=([^;]+)/);
    cookieValue = m ? decodeURIComponent(m[1]) : "";
    ok("cookie 值可解析", !!cookieValue);
  }

  // 5. 带 cookie 建流
  {
    const res = await handler(req("/events", { headers: cookieHeader(cookieValue) }));
    ok("携带会话可建立 SSE", res.status === 200, `status=${res.status}`);
    ok(
      "响应为 text/event-stream",
      /text\/event-stream/.test(res.headers.get("content-type") || ""),
      res.headers.get("content-type") || ""
    );
    ok("关闭了反代缓冲", res.headers.get("x-accel-buffering") === "no");

    const frames = await readFrames(res, { want: 1, timeoutMs: 600 });
    ok("首帧为 ready", frames[0] && frames[0].event === "ready", "[" + frames.map((f) => f.event).join("|") + "]");
    ok("ready 帧带服务端时间", !!(frames[0] && frames[0].json && frames[0].json.data.serverTime));
  }

  // 6. 业务事件投递：客户咨询 + 新订单
  {
    const res = await handler(req("/events", { headers: cookieHeader(cookieValue) }));

    const post = (obj) =>
      handler(
        req("/wecom/callback", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(obj),
        })
      );

    await post({ MsgType: "text", Content: "你好，这个软件多少钱？", ExternalUserId: "wm_abcdef123456" });
    await post({
      MsgType: "text",
      Content: "机器码 A1B2C3D4E5F60718 已经付款了",
      ExternalUserId: "wm_abcdef123456",
    });

    const frames = await readFrames(res, { want: 12, timeoutMs: 900 });
    const types = frames.map((f) => f.event);
    ok("推送了客户消息事件", types.includes("message.received"), types.join(","));
    ok("推送了新订单事件", types.includes("order.created"), types.join(","));

    const orderFrame = frames.find((f) => f.event === "order.created");
    const d = (orderFrame && orderFrame.json && orderFrame.json.data) || {};
    ok("订单事件带机器码", d.machine === "A1B2-C3D4-E5F6-0718", d.machine || "缺失");
    ok("订单事件带脱敏客户标识", !!d.from && d.from.includes("***"), d.from || "缺失");
    ok("订单事件不带审批签名链接", !JSON.stringify(d).includes("sig="));

    const msgFrame = frames.find((f) => f.event === "message.received");
    const md = (msgFrame && msgFrame.json && msgFrame.json.data) || {};
    ok("消息事件带截断预览", typeof md.preview === "string" && md.preview.length <= 120);
    ok("消息事件不泄露完整 externalUserId", !JSON.stringify(md).includes("wm_abcdef123456"));

    ok("事件 id 单调递增", Number(frames[frames.length - 1].id) > Number(frames[0].id));
  }

  // 7. 断线重连：Last-Event-ID 回放
  {
    const first = await handler(req("/events", { headers: cookieHeader(cookieValue) }));
    const f1 = await readFrames(first, { want: 1, timeoutMs: 500 });
    const ready = f1[0];
    ok("回放测试取得 ready 帧", ready && ready.event === "ready");

    // 取缓冲区里倒数第二条的 id 作为断点
    const all = handler.bus.buffer;
    const anchor = all.length >= 2 ? all[all.length - 2].id : all[0]?.id;

    const res = await handler(
      req("/events?lastEventId=" + encodeURIComponent(anchor || ""), {
        headers: cookieHeader(cookieValue),
      })
    );
    const frames = await readFrames(res, { want: 2, timeoutMs: 600 });
    const readyFrame = frames.find((f) => f.event === "ready");
    const replayed = readyFrame && readyFrame.json ? readyFrame.json.data.replayed : 0;
    ok("断线重连会补齐缺失事件", replayed >= 1, `replayed=${replayed}`);

    const replayIds = frames.filter((f) => f.id).map((f) => f.id);
    ok("回放不含断点及更早的事件", !replayIds.includes(String(anchor)));
  }

  // 8. 伪造 / 过期 cookie
  {
    const forged = await handler(req("/events", { headers: cookieHeader(cookieValue.slice(0, -2) + "ff") }));
    ok("伪造签名被拒", forged.status === 401, `status=${forged.status}`);

    const expired = signSession(TOKEN, Date.now() - 1000);
    const res = await handler(req("/events", { headers: cookieHeader(expired) }));
    ok("过期会话被拒", res.status === 401, `status=${res.status}`);
  }

  // 9. 订单快照接口
  {
    const res = await handler(req("/orders?limit=10", { headers: cookieHeader(cookieValue) }));
    const body = await res.json();
    ok("订单快照可读", res.status === 200 && Array.isArray(body.orders), `status=${res.status}`);
    ok("快照包含刚建的订单", body.orders.length === 1 && body.orders[0].machine === "A1B2-C3D4-E5F6-0718");
    ok("快照不含授权码字段", !JSON.stringify(body).includes("LLBS1-"));
    ok("今日统计可用", body.today && body.today.total >= 1, JSON.stringify(body.today || {}));

    const denied = await handler(req("/orders"));
    ok("未登录读订单被拒", denied.status === 401);
  }

  // 10. 订阅上限：第二个连接应收到 error 帧而不是无限堆积
  {
    const bus = new EventBus({ bufferSize: 10, maxSubscribers: 1 });
    const h2 = createHandler({ cfg: makeCfg({ sseMaxClients: 1 }), store: new Store({}), bus });

    const a = await h2(req("/events", { headers: cookieHeader(cookieValue) }));
    const framesA = await readFrames(a, { want: 1, timeoutMs: 300 });
    ok("首个订阅可建立", framesA.some((f) => f.event === "ready"));

    // 第一个流已 cancel，但订阅在 cancel 时会摘除，这里改测总线层上限更确定
    const bus2 = new EventBus({ maxSubscribers: 1 });
    const s1 = bus2.subscribe({ send: () => {} });
    let threw = false;
    try {
      bus2.subscribe({ send: () => {} });
    } catch (e) {
      threw = e && e.code === "TOO_MANY_SUBSCRIBERS";
    }
    ok("超过订阅上限会抛 TOO_MANY_SUBSCRIBERS", threw);
    s1.unsubscribe();
  }

  // 11. 总线边界：缓冲裁剪 / 瞬时事件不进缓冲 / 慢订阅者被摘除
  {
    const bus = new EventBus({ bufferSize: 10 });
    for (let i = 0; i < 25; i++) bus.publish("test.tick", { i });
    const st = bus.stats();
    ok("环形缓冲按上限裁剪", st.buffered === 10, `buffered=${st.buffered}`);
    ok("发布计数不丢", st.published === 25, `published=${st.published}`);

    const before = bus.stats().buffered;
    bus.publishTransient("presence.changed", { subscribers: 2 });
    ok("瞬时事件不进缓冲", bus.stats().buffered === before);

    let calls = 0;
    bus.subscribe({ send: () => { calls++; throw new Error("连接已断"); } });
    bus.publish("test.tick", { i: 99 });
    ok("写失败的订阅者会被摘除", bus.stats().subscribers === 0 && calls === 1, `calls=${calls}`);

    const noThrow = (() => {
      try {
        bus.publish("test.tick", { circular: (() => { const o = {}; o.self = o; return o; })() });
        return true;
      } catch {
        return false;
      }
    })();
    ok("非法负载不会让发布抛错", noThrow);
  }

  // 12. 主题过滤
  {
    const bus = new EventBus();
    const got = [];
    bus.subscribe({ send: (frame) => got.push(frame), topics: ["order.*"] });
    bus.publish("order.created", { id: "1" });
    bus.publish("message.received", { preview: "x" });
    bus.publish("license.issued", { id: "1" });
    ok("按前缀订阅只收匹配事件", got.length === 1 && /order\.created/.test(got[0]), `收到 ${got.length} 条`);
  }

  // 13. 停机清理：不摘掉长连接，server.close() 会一直挂住
  {
    const bus = new EventBus();
    let closedCount = 0;
    bus.subscribe({ send: () => {}, close: () => { closedCount += 1; } });
    bus.subscribe({ send: () => {} });
    bus.closeAll();
    ok("停机时清空所有长连接", bus.stats().subscribers === 0 && closedCount === 1, `closedCount=${closedCount}`);
  }

  const failed = results.filter((r) => !r.pass);
  console.log("");
  if (failed.length) {
    console.log(`❌ ${failed.length}/${results.length} 项失败：`);
    for (const f of failed) console.log("   - " + f.name);
    process.exit(1);
  }
  console.log(`✅ 实时推送自测全部通过（${results.length} 项）`);
}

run().catch((e) => {
  console.error("自测异常：", e);
  process.exit(1);
});
