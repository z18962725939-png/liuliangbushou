// 配置：全部从环境变量读取，任何密钥都不写进代码。
// 缺哪一项就在启动时明确报错，避免「跑起来了但发码全是废码」这种静默故障。

const REQUIRED = [
  "WECOM_CORP_ID",
  "WECOM_KF_SECRET",
  "WECOM_TOKEN",
  "WECOM_AES_KEY",
  "APPROVE_SECRET",
  "LICENSE_SEED_HEX",
];

// 选填但强烈建议：没有群机器人，你就收不到待审订单通知
const RECOMMENDED = ["GROUP_WEBHOOK_URL", "WECOM_KF_ID", "DOWNLOAD_URL", "DASHBOARD_TOKEN"];

const clamp = (v, min, max) => Math.min(max, Math.max(min, v));

export function loadConfig(env = process.env) {
  const cfg = {
    corpId: env.WECOM_CORP_ID || "",
    corpSecret: env.WECOM_KF_SECRET || "",
    token: env.WECOM_TOKEN || "",
    aesKey: env.WECOM_AES_KEY || "",
    openKfId: env.WECOM_KF_ID || "",
    groupWebhook: env.GROUP_WEBHOOK_URL || "",
    approveSecret: env.APPROVE_SECRET || "",
    seedHex: env.LICENSE_SEED_HEX || "",
    tokenSeedHex:
      env.TOKEN_SEED_HEX ||
      "d72123f8790704a1fdff8ba0e25f31bf65e8dee7ee24e5d7fe285bf355136000",
    apiToken: env.JUSTONEAPI_TOKEN || "",
    publicBaseUrl: (env.PUBLIC_BASE_URL || "").replace(/\/+$/, ""),
    downloadUrl: env.DOWNLOAD_URL || "",
    defaultPlan: env.DEFAULT_PLAN || "trial10",
    storeFile: env.STORE_FILE || "",
    adminContact: env.ADMIN_CONTACT || "微信：807108536",

    // ── 实时看板（SSE）────────────────────────────────────────────────
    // 口令至少 16 位才启用；太短等于没设，宁愿不开放看板
    dashboardToken: String(env.DASHBOARD_TOKEN || "").trim(),
    sseHeartbeatMs: clamp(Number(env.SSE_HEARTBEAT_MS) || 15000, 5000, 120000),
    sseBufferSize: clamp(Number(env.SSE_BUFFER_SIZE) || 200, 10, 5000),
    sseMaxClients: clamp(Number(env.SSE_MAX_CLIENTS) || 50, 1, 1000),
    secureCookies: env.COOKIE_SECURE === "1" ? true : env.COOKIE_SECURE === "0" ? false : undefined,
    dashboardEnabled: String(env.DASHBOARD_TOKEN || "").trim().length >= 16,
  };

  const missing = REQUIRED.filter((k) => !String(env[k] || "").trim());
  const missingRecommended = RECOMMENDED.filter((k) => !String(env[k] || "").trim());
  return { cfg, missing, missingRecommended };
}

export function assertConfig(cfg, { strict = true } = {}) {
  const { missing } = loadConfig(process.env);
  if (missing.length && strict) {
    throw new Error(
      "缺少必需环境变量： " + missing.join(", ") + "\n请复制 .env.example 为 .env 并填写。"
    );
  }
  return missing;
}
