// 发卡内核（Node 版）——与 Python 客户端 licensing.py 完全对齐。
//
// 为什么要有这一份：机器人跑在服务端，没有本地 Python，也没有注册机 exe。
// 这里用 Node 内置 crypto 复现同样的签发结果，客户端验签无需任何改动。
//
// 已验证：本模块签出的码，用 Python licensing.verify_license() 验签通过，
// parse 出的客户名/额度/内嵌 Token 均正确（见 bot/test/selftest.mjs）。

import crypto from "node:crypto";

export const LICENSE_PREFIX = "LLBS1";
export const ANY_MACHINE = "ANY";
export const UNLIMITED = -1;

// 32 字节 Ed25519 种子 -> PKCS#8 DER 的固定前缀
const PKCS8_PREFIX = Buffer.from("302e020100300506032b657004220420", "hex");

// 套餐：与 licensing.py 的 PLANS 保持一致
export const PLANS = {
  trial10: { label: "体验版", quota: 10, days: 31, bonus: 7, price: 19.9 },
  std30: { label: "标准版", quota: 30, days: 30, bonus: 0, price: 29.9 },
  pro100: { label: "专业版", quota: 100, days: 30, bonus: 0, price: 59.9 },
  unlimited: { label: "不限量", quota: UNLIMITED, days: 0, bonus: 0, price: 0 },
};

const b64u = (buf) => Buffer.from(buf).toString("base64url");

function seedBytes(hexStr, name) {
  const raw = Buffer.from(String(hexStr || "").trim(), "hex");
  if (raw.length !== 32) {
    throw new Error(`${name} 必须是 32 字节（64 位 hex），当前 ${raw.length} 字节`);
  }
  return raw;
}

function privateKeyFromSeed(seedHex) {
  const der = Buffer.concat([PKCS8_PREFIX, seedBytes(seedHex, "私钥")]);
  return crypto.createPrivateKey({ key: der, format: "der", type: "pkcs8" });
}

// ── Token 加密：与 licensing.py 的 encrypt_token 逐字节对齐 ────────────────
// key = sha256(seed || "|tok|" || nonce)
// ct  = token XOR keystream(key)
// mac = hmac_sha256(key, ct)[:8]
// out = base64url(ct || mac)

function keystream(key, length) {
  const out = Buffer.alloc(length);
  let off = 0;
  let counter = 0;
  while (off < length) {
    // 对齐 Python 的 counter.to_bytes(8, "big")
    const ctr = Buffer.alloc(8);
    ctr.writeUInt32BE(counter >>> 0, 4);
    const block = crypto.createHash("sha256").update(key).update(ctr).digest();
    const take = Math.min(block.length, length - off);
    block.copy(out, off, 0, take);
    off += take;
    counter += 1;
  }
  return out;
}

export function encryptToken(token, nonce, tokenSeedHex) {
  const t = String(token || "").trim();
  if (!t) return "";
  const seed = seedBytes(tokenSeedHex, "TOKEN_SEED");
  const key = crypto
    .createHash("sha256")
    .update(seed)
    .update(Buffer.from("|tok|", "utf8"))
    .update(Buffer.from(String(nonce || ""), "utf8"))
    .digest();
  const data = Buffer.from(t, "utf8");
  const ks = keystream(key, data.length);
  const ct = Buffer.alloc(data.length);
  for (let i = 0; i < data.length; i++) ct[i] = data[i] ^ ks[i];
  const mac = crypto.createHmac("sha256", key).update(ct).digest().subarray(0, 8);
  return b64u(Buffer.concat([ct, mac]));
}

// ── 载荷 ────────────────────────────────────────────────────────────────

export function buildPayload(opts) {
  const {
    token = "",
    machine = ANY_MACHINE,
    days = 0,
    customer = "",
    features = "",
    quota = 0,
    nonce = "",
    issuedAt = Math.floor(Date.now() / 1000),
    tokenSeedHex,
  } = opts;

  const n = nonce || crypto.randomBytes(6).toString("hex");
  let exp = 0;
  if (days) exp = Math.floor(issuedAt) + Math.floor(days) * 86400;

  const parts = ["v=1", `n=${n}`, `m=${machine || ANY_MACHINE}`, `e=${exp}`];
  if (customer) parts.push("c=" + customer.replace(/[|\r\n]/g, " ").slice(0, 40));
  if (features) {
    parts.push("f=" + features.toLowerCase().replace(/[^a-z0-9,]/g, ""));
  } else {
    parts.push("f=all");
  }
  if (quota) {
    const q = Number.isFinite(Number(quota)) ? Math.trunc(Number(quota)) : UNLIMITED;
    parts.push(`q=${String(q)}`);
  }
  if (token) {
    if (!tokenSeedHex) throw new Error("要内嵌 Token 必须提供 TOKEN_SEED_HEX");
    parts.push("t=" + encryptToken(token, n, tokenSeedHex));
  }
  parts.push(`i=${Math.floor(issuedAt)}`);
  return parts.join("|");
}

// ── 签发 ────────────────────────────────────────────────────────────────

export function issueLicense(opts) {
  const { seedHex } = opts;
  const payload = buildPayload(opts);
  const sig = crypto.sign(null, Buffer.from(payload, "utf8"), privateKeyFromSeed(seedHex));
  return `${LICENSE_PREFIX}-${b64u(Buffer.from(payload, "utf8"))}.${b64u(sig)}`;
}

// 服务端自检：签完立刻用公钥验一遍，防止配置错私钥却把废码发给客户
export function selfVerify(code, seedHex, expectedPublicHex) {
  const pub = crypto
    .createPublicKey(privateKeyFromSeed(seedHex))
    .export({ format: "der", type: "spki" })
    .subarray(-32)
    .toString("hex");
  const pubOk = !expectedPublicHex || pub === expectedPublicHex;
  const [head, sig] = String(code).slice(LICENSE_PREFIX.length + 1).split(".");
  const payload = Buffer.from(head, "base64url").toString("utf8");
  const sigOk = crypto.verify(
    null,
    Buffer.from(payload, "utf8"),
    crypto.createPublicKey(privateKeyFromSeed(seedHex)),
    Buffer.from(sig, "base64url")
  );
  return { pubOk, sigOk, pub, payload };
}

// 按套餐算出实际天数（首购赠送 BONUS_DAYS，永久码 days=0 不受影响）
export function daysFor(planKey, customDays) {
  if (customDays !== undefined && customDays !== null && customDays !== "") {
    return Math.trunc(Number(customDays));
  }
  const p = PLANS[planKey];
  if (!p) return 31 + 7;
  if (p.days === 0) return 0;
  return p.days + (p.bonus || 0);
}
