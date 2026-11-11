// 测试用私钥：只从本地文件读，绝不写死在代码里。
// 生产环境这个值是 LICENSE_SEED_HEX 环境变量，由部署平台注入。
import fs from "node:fs";

export const LicenseSeedPlaceholder = fs
  .readFileSync(new URL("../../tools/license_private.key", import.meta.url), "utf8")
  .trim();
