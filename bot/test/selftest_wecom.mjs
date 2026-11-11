// 企业微信加解密自测：加密 -> 解密 必须还原，且 receiveid 正确带回。
// 同时验证签名校验与审批链接防伪造。
import crypto from "node:crypto";
import {
  decryptMessage,
  encryptMessage,
  checkSignature,
  signOrder,
  verifyOrderSignature,
  buildEncryptedResponse,
} from "../src/wecom.mjs";

const results = [];
const ok = (name, pass, extra = "") => {
  results.push({ name, pass, extra });
  console.log(`${pass ? "OK  " : "FAIL"} ${name}${extra ? "  " + extra : ""}`);
};

// 43 位 EncodingAESKey（32 字节 base64 去掉填充）
const aesKey43 = crypto.randomBytes(32).toString("base64").replace(/=+$/, "");
ok("EncodingAESKey 长度 43", aesKey43.length === 43, `实际 ${aesKey43.length}`);

const xml =
  '<xml><ToUserName><![CDATA[ww123]]></ToUserName><MsgType><![CDATA[text]]></MsgType>' +
  "<Content><![CDATA[你好，我要买]]></Content></xml>";
const corpId = "ww1234567890abcdef";

const enc = encryptMessage(xml, aesKey43, corpId);
const dec = decryptMessage(enc, aesKey43);
ok("加解密往返还原", dec.message === xml);
ok("receiveid 正确带回", dec.receiveId === corpId, `得到 ${dec.receiveId}`);

// 中文 + 长文本（真实消息里都是中文）
const longCn = "我的机器码是 A1B2-C3D4-E5F6-7890，已经付款了，麻烦发授权码 ".repeat(3);
const dec2 = decryptMessage(encryptMessage(longCn, aesKey43, corpId), aesKey43);
ok("中文长文本往返", dec2.message === longCn);

// 签名校验
const token = "TestToken123";
const ts = String(Math.floor(Date.now() / 1000));
const nonce = "abc123";
const sig = crypto
  .createHash("sha1")
  .update([token, ts, nonce].sort().join(""))
  .digest("hex");
ok("签名校验通过", checkSignature(token, ts, nonce, sig));
ok("错误签名被拒", !checkSignature(token, ts, nonce, "deadbeef"));

// 审批链接防伪造
const secret = "approve-secret-demo";
const s = signOrder("order-42", secret);
ok("审批签名通过", verifyOrderSignature("order-42", s, secret));
ok("篡改签名被拒", !verifyOrderSignature("order-42", s + "x", secret));
ok("换订单号被拒", !verifyOrderSignature("order-43", s, secret));

// 被动响应可被官方解密（自洽校验）
const resp = buildEncryptedResponse(xml, aesKey43, token, corpId);
const m = resp.match(/<Encrypt><!\[CDATA\[(.*?)\]\]><\/Encrypt>/);
ok("响应可解析出 Encrypt", !!m);
if (m) {
  const back = decryptMessage(m[1], aesKey43);
  ok("响应体官方可解密", back.message === xml && back.receiveId === corpId);
}

const failed = results.filter((r) => !r.pass);
console.log();
console.log(
  failed.length === 0
    ? `全部通过（${results.length}/${results.length}）—— 加解密与签名实现可用`
    : `失败 ${failed.length} 项`
);
process.exit(failed.length === 0 ? 0 : 1);
