// 端到端演练（不发真实网络请求）：
// 客户发机器码 → 回调解密 → 建待审订单 → 群机器人收到审批链接
// → 作者点链接 → 签发授权码 → 客服 API 把码发给客户 → 二次点击被拒
//
// 用桩替换 fetch，验证的是我们自己的业务逻辑，不依赖企业微信是否可达。

import crypto from "node:crypto";
import { encryptMessage, checkSignature } from "../src/wecom.mjs";
import { Store } from "../src/store.mjs";
import { buildReplies } from "../src/replies.mjs";
import { createHandler } from "../src/handler.mjs";
import { LicenseSeedPlaceholder } from "./seed.mjs";

const results = [];
const ok = (name, pass, extra = "") => {
  results.push({ name, pass });
  console.log(`${pass ? "OK  " : "FAIL"} ${name}${extra ? "  " + extra : ""}`);
};

// ── 测试配置 ────────────────────────────────────────────────────────────
const TOKEN = "TestToken123";
const AES = crypto.randomBytes(32).toString("base64").replace(/=+$/, "");
const cfg = {
  corpId: "ww_test",
  corpSecret: "secret_test",
  token: TOKEN,
  aesKey: AES,
  openKfId: "wk_test",
  groupWebhook: "https://example.invalid/webhook",
  approveSecret: "approve-secret",
  seedHex: LicenseSeedPlaceholder,
  tokenSeedHex: "d72123f8790704a1fdff8ba0e25f31bf65e8dee7ee24e5d7fe285bf355136000",
  apiToken: "",
  publicBaseUrl: "https://bot.example.com",
  downloadUrl: "https://example.com/download",
  defaultPlan: "trial10",
  adminContact: "微信：807108536",
};

// ── 劫持 fetch ──────────────────────────────────────────────────────────
const sent = { group: [], kf: [] };
globalThis.fetch = async (url, init) => {
  const u = String(url);
  if (u.includes("gettoken")) {
    return new Response(JSON.stringify({ access_token: "TOKEN123", expires_in: 7200 }));
  }
  if (u.includes("kf/send_msg")) {
    sent.kf.push(JSON.parse(init.body));
    return new Response(JSON.stringify({ errcode: 0, errmsg: "ok" }));
  }
  if (u.includes("example.invalid/webhook")) {
    sent.group.push(JSON.parse(init.body));
    return new Response(JSON.stringify({ errcode: 0, errmsg: "ok" }));
  }
  return new Response(JSON.stringify({ errcode: 0 }));
};

const store = new Store({});
const handler = createHandler({ cfg, store, replies: buildReplies(cfg) });

const call = (pathAndQuery, body, method = "POST") => {
  const request = new Request(`https://bot.example.com${pathAndQuery}`, {
    method,
    body: method === "POST" ? body : undefined,
  });
  return handler(request);
};

// ── 1) 回调 URL 验证（echostr）───────────────────────────────────────────
{
  const ts = String(Math.floor(Date.now() / 1000));
  const nonce = "n1";
  const echo = encodeURIComponent(encryptMessage("hello-echo", AES, "ww_test"));
  const sig = crypto
    .createHash("sha1")
    .update([TOKEN, ts, nonce].sort().join(""))
    .digest("hex");
  const res = await call(
    `/wecom/callback?timestamp=${ts}&nonce=${nonce}&msg_signature=${sig}&echostr=${echo}`,
    "",
    "GET"
  );
  const text = await res.text();
  ok("回调验证回显 echostr", text === "hello-echo", `得到 ${text.slice(0, 20)}`);
}

// ── 2) 客户发来机器码 ────────────────────────────────────────────────────
const MACHINE = "A1B2-C3D4-E5F6-7890";
{
  const xml =
    `<xml><ToUserName><![CDATA[ww_test]]></ToUserName>` +
    `<MsgType><![CDATA[text]]></MsgType>` +
    `<Content><![CDATA[我已经付款了，机器码 ${MACHINE}]]></Content>` +
    `<ExternalUserId><![CDATA[wmCustomer001]]></ExternalUserId>` +
    `<OpenKfId><![CDATA[wk_test]]></OpenKfId></xml>`;
  const enc = encryptMessage(xml, AES, "ww_test");
  const body = `<xml><Encrypt><![CDATA[${enc}]]></Encrypt></xml>`;
  const ts = String(Math.floor(Date.now() / 1000));
  const nonce = "n2";
  const sig = crypto
    .createHash("sha1")
    .update([TOKEN, ts, nonce].sort().join(""))
    .digest("hex");

  const res = await call(`/wecom/callback?timestamp=${ts}&nonce=${nonce}&msg_signature=${sig}`, body);
  const text = await res.text();
  ok("机器码回调返回 success", text === "success", text);
  ok("已建待审订单", (await store.list("order:")).length === 1);
  ok("群机器人收到审批通知", sent.group.length === 1);
  ok("已自动应答客户", sent.kf.length === 1 && /正在为你开通/.test(sent.kf[0].text.content));
}

// ── 3) 取出审批链接并放行 ────────────────────────────────────────────────
let approvePath = "";
{
  const md = sent.group[0]?.markdown?.content || "";
  const m = md.match(/\(([^)\s]*\/approve\?[^)]+)\)/);
  ok("审批链接在通知里", !!m, m ? "" : md.slice(0, 120));
  // 取路径+查询部分，供本测试直接调用 handler
  approvePath = m ? m[1].replace(/^https?:\/\/[^/]+/, "") : "";
}
{
  const res = await call(approvePath, "", "GET");
  const text = await res.text();
  ok("放行后返回成功页", res.status === 200 && /已放行/.test(text), `status=${res.status}`);
  const codeMatch = text.match(/LLBS1-[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+/);
  ok("页面含授权码", !!codeMatch);
  ok("授权码已发给客户", sent.kf.some((m) => /LLBS1-/.test(m.text.content)));
  if (codeMatch) {
    console.log("\n签发的码：" + codeMatch[0].slice(0, 60) + "…");
  }
}

// ── 4) 重复点击必须被拒 ──────────────────────────────────────────────────
{
  const before = sent.kf.length;
  const res = await call(approvePath, "", "GET");
  const text = await res.text();
  ok("重复点击被拒绝", /已经发过了/.test(text));
  ok("不会重复发码", sent.kf.length === before);
}

// ── 5) 伪造签名必须被拒 ──────────────────────────────────────────────────
{
  const id = (await store.list("order:"))[0].replace("order:", "");
  const res = await call(`/approve?id=${id}&sig=forged`, "", "GET");
  ok("伪造签名被拒", res.status === 403 && /签名无效/.test(await res.text()));
}

// ── 6) 普通咨询走关键词回复 ──────────────────────────────────────────────
{
  const before = sent.kf.length;
  const xml =
    `<xml><MsgType><![CDATA[text]]></MsgType><Content><![CDATA[多少钱]]></Content>` +
    `<ExternalUserId><![CDATA[wmCustomer002]]></ExternalUserId><OpenKfId><![CDATA[wk_test]]></OpenKfId></xml>`;
  const enc = encryptMessage(xml, AES, "ww_test");
  const body = `<xml><Encrypt><![CDATA[${enc}]]></Encrypt></xml>`;
  const ts = String(Math.floor(Date.now() / 1000));
  const sig = crypto.createHash("sha1").update([TOKEN, ts, "n3"].sort().join("")).digest("hex");
  await call(`/wecom/callback?timestamp=${ts}&nonce=n3&msg_signature=${sig}`, body);
  const last = sent.kf[sent.kf.length - 1];
  ok("关键词回复命中价格", sent.kf.length === before + 1 && /19\.9/.test(last.text.content));
}

const failed = results.filter((r) => !r.pass);
console.log();
console.log(
  failed.length === 0
    ? `全部通过（${results.length}/${results.length}）—— 端到端链路可用`
    : `失败 ${failed.length} 项：${failed.map((f) => f.name).join("、")}`
);
process.exit(failed.length === 0 ? 0 : 1);
