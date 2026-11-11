// 验证：Node 内置 crypto 能否签出客户端（Python）认可的授权码。
// 若成立，发卡就能跑在无服务器环境（边缘函数/云函数），不必买常开服务器。
import crypto from "node:crypto";
import fs from "node:fs";

const seed = fs
  .readFileSync(new URL("./license_private.key", import.meta.url), "utf8")
  .trim();

// 32 字节 seed -> PKCS#8 DER（Ed25519 固定前缀）
const PKCS8_PREFIX = Buffer.from("302e020100300506032b657004220420", "hex");
const der = Buffer.concat([PKCS8_PREFIX, Buffer.from(seed, "hex")]);
const priv = crypto.createPrivateKey({ key: der, format: "der", type: "pkcs8" });

const b64u = (buf) =>
  Buffer.from(buf).toString("base64url");

const payload =
  "v=1|n=aabbccddeeff|m=ANY|e=1794919002|c=NodeSignTest|f=all|q=10|i=1791635802";

const sig = crypto.sign(null, Buffer.from(payload, "utf8"), priv);
const code = `LLBS1-${b64u(Buffer.from(payload, "utf8"))}.${b64u(sig)}`;

console.log("payload :", payload);
console.log("sig len :", sig.length);
console.log("CODE=" + code);

// 顺带导出 Node 侧算出的公钥，跟 Python 侧比对
const pubDer = crypto.createPublicKey(priv).export({ format: "der", type: "spki" });
console.log("PUB=" + pubDer.subarray(pubDer.length - 32).toString("hex"));
