// 企业微信回调加解密 + 接口调用封装。
//
// 实现依据：企业微信官方「回调加解密」规范（AES-256-CBC + PKCS7 + 签名校验）。
// 官方样例 SDK 的做法是手动补位并关闭自动填充，这里保持一致，否则与官方服务器对不上。

import crypto from "node:crypto";

const BLOCK = 32;

// ── 签名校验 ────────────────────────────────────────────────────────────
// msg_signature = sha1(sort(token, timestamp, nonce) 拼接)
export function checkSignature(token, timestamp, nonce, signature) {
  const arr = [String(token), String(timestamp), String(nonce)].sort();
  const sha = crypto.createHash("sha1").update(arr.join("")).digest("hex");
  return sha === String(signature);
}

// ── AES 密钥 ────────────────────────────────────────────────────────────
// EncodingAESKey 是 43 位 base64 字符，补一个 "=" 后解码得到 32 字节
function aesKey(encodingAESKey) {
  const key = Buffer.from(String(encodingAESKey || "") + "=", "base64");
  if (key.length !== 32) {
    throw new Error(`EncodingAESKey 解出 ${key.length} 字节，应为 32 字节`);
  }
  return key;
}

function pkcs7Pad(buf) {
  const pad = BLOCK - (buf.length % BLOCK);
  return Buffer.concat([buf, Buffer.alloc(pad, pad)]);
}

function pkcs7Unpad(buf) {
  const pad = buf[buf.length - 1];
  if (pad < 1 || pad > BLOCK || pad > buf.length) return buf;
  return buf.subarray(0, buf.length - pad);
}

// ── 解密（官方推送给我们的消息体）────────────────────────────────────────
// 明文布局：random(16) | msg_len(4, 大端) | msg | receiveid
export function decryptMessage(encryptedBase64, encodingAESKey) {
  const key = aesKey(encodingAESKey);
  const iv = key.subarray(0, 16);
  const decipher = crypto.createDecipheriv("aes-256-cbc", key, iv);
  decipher.setAutoPadding(false);
  const raw = Buffer.concat([decipher.update(Buffer.from(encryptedBase64, "base64")), decipher.final()]);
  const plain = pkcs7Unpad(raw);
  const len = plain.readUInt32BE(16);
  return {
    message: plain.subarray(20, 20 + len).toString("utf8"),
    receiveId: plain.subarray(20 + len).toString("utf8"),
  };
}

// ── 加密（我们回给官方的响应体）──────────────────────────────────────────
export function encryptMessage(message, encodingAESKey, receiveId = "") {
  const key = aesKey(encodingAESKey);
  const iv = key.subarray(0, 16);
  const random = crypto.randomBytes(16);
  const body = Buffer.from(String(message), "utf8");
  const len = Buffer.alloc(4);
  len.writeUInt32BE(body.length, 0);
  const plain = Buffer.concat([random, len, body, Buffer.from(String(receiveId || ""), "utf8")]);
  const cipher = crypto.createCipheriv("aes-256-cbc", key, iv);
  cipher.setAutoPadding(false);
  const enc = Buffer.concat([cipher.update(pkcs7Pad(plain)), cipher.final()]);
  return enc.toString("base64");
}

// ── 被动响应 XML（企业微信要求回 XML）─────────────────────────────────────
export function buildEncryptedResponse(xmlObj, encodingAESKey, token, receiveId = "") {
  const encrypted = encryptMessage(xmlObj, encodingAESKey, receiveId);
  const ts = Math.floor(Date.now() / 1000);
  const nonce = crypto.randomBytes(4).toString("hex");
  const sig = crypto
    .createHash("sha1")
    .update([String(token), String(ts), String(nonce), encrypted].sort().join(""))
    .digest("hex");
  return [
    "<xml>",
    `<Encrypt><![CDATA[${encrypted}]]></Encrypt>`,
    `<MsgSignature><![CDATA[${sig}]]></MsgSignature>`,
    `<TimeStamp>${ts}</TimeStamp>`,
    `<Nonce><![CDATA[${nonce}]]></Nonce>`,
    "</xml>",
  ].join("");
}

// ── access_token（带内存缓存，进程内复用，避免每次回调都去换票）─────────────
let _tokenCache = { value: "", expiresAt: 0 };

export async function getAccessToken(corpId, corpSecret) {
  if (_tokenCache.value && Date.now() < _tokenCache.expiresAt - 60_000) {
    return _tokenCache.value;
  }
  const url = `https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid=${encodeURIComponent(
    corpId
  )}&corpsecret=${encodeURIComponent(corpSecret)}`;
  const res = await fetch(url);
  const data = await res.json();
  if (data.errcode && data.errcode !== 0) {
    throw new Error(`获取 access_token 失败：${data.errcode} ${data.errmsg}`);
  }
  _tokenCache = {
    value: data.access_token,
    expiresAt: Date.now() + (data.expires_in || 7200) * 1000,
  };
  return _tokenCache.value;
}

// ── 微信客服：给客户发消息 ───────────────────────────────────────────────
export async function sendKfText({ corpId, corpSecret, openKfId, externalUserId, content }) {
  const token = await getAccessToken(corpId, corpSecret);
  const res = await fetch(`https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg?access_token=${token}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      touser: externalUserId,
      open_kfid: openKfId,
      msgtype: "text",
      text: { content },
    }),
  });
  const data = await res.json();
  if (data.errcode !== 0) {
    throw new Error(`客服消息发送失败：${data.errcode} ${data.errmsg}`);
  }
  return data;
}

// ── 群机器人：往内部群推审批通知 ──────────────────────────────────────────
export async function pushGroupMarkdown(webhookUrl, markdown) {
  const res = await fetch(webhookUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ msgtype: "markdown", markdown: { content: markdown } }),
  });
  const data = await res.json();
  if (data.errcode !== 0) {
    throw new Error(`群机器人推送失败：${data.errcode} ${data.errmsg}`);
  }
  return data;
}

// ── 审批链接签名（防止有人猜 URL 白嫖授权码）───────────────────────────────
export function signOrder(orderId, secret) {
  return crypto.createHmac("sha256", secret).update(String(orderId)).digest("base64url");
}

export function verifyOrderSignature(orderId, signature, secret) {
  const expect = signOrder(orderId, secret);
  const a = Buffer.from(String(signature || ""));
  const b = Buffer.from(expect);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}
