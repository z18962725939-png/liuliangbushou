// 签发一张测试码，交给 Python 客户端交叉验证（见 tools/_verify_bot_license.py）。
// 私钥只从本地文件读，绝不硬编码进代码。
import fs from "node:fs";
import { issueLicense, selfVerify, daysFor } from "../src/license.mjs";

const seedHex = fs
  .readFileSync(new URL("../../tools/license_private.key", import.meta.url), "utf8")
  .trim();

// 与 licensing.py 的 TOKEN_SEED_HEX 一致（客户端也有，用于解开内嵌 Token）
const TOKEN_SEED_HEX = "d72123f8790704a1fdff8ba0e25f31bf65e8dee7ee24e5d7fe285bf355136000";
const TEST_TOKEN = "TESTTOKEN-abc123XYZ";
const CUSTOMER = "Node机器人";
const MACHINE = "A1B2-C3D4-E5F6-7890";

const days = daysFor("trial10");
const issuedAt = Math.floor(Date.now() / 1000);

const code = issueLicense({
  seedHex,
  token: TEST_TOKEN,
  tokenSeedHex: TOKEN_SEED_HEX,
  machine: MACHINE,
  days,
  customer: CUSTOMER,
  quota: 10,
  issuedAt,
});

const check = selfVerify(code, seedHex, "37d6836cac560e5cbf01f0f1eb3097f54ceff0e3c4fbdfd2b4bf7980ae2f7991");

const out = {
  code,
  expect: { customer: CUSTOMER, quota: 10, machine: MACHINE, days, token: TEST_TOKEN },
  selfCheck: { pubOk: check.pubOk, sigOk: check.sigOk, payload: check.payload },
};

fs.writeFileSync(
  new URL("./_out.json", import.meta.url),
  JSON.stringify(out, null, 2),
  "utf8"
);

console.log("码已生成 ->", new URL("./_out.json", import.meta.url).pathname);
console.log("派生公钥匹配:", check.pubOk, "| 自验签通过:", check.sigOk);
console.log("payload:", check.payload);
console.log("预期天数:", days, "(体验版 31 + 赠送 7)");
