# 流量捕手 · 企业微信客服机器人

客户在微信里发消息 → 机器人 24 小时自动应答；客户发来机器码 → 推到你手机 → 你点一下"放行" → 授权码自动发给客户。

## 一、它做什么 / 不做什么

| 能力 | 状态 |
|---|---|
| 常见问题自动回复（下载、价格、激活、续费、换电脑） | ✅ 已实现 |
| 客户发机器码 → 自动建单 → 推你手机审批 | ✅ 已实现 |
| 你点放行 → 自动签授权码 → 自动发给客户 | ✅ 已实现 |
| 防重复发码 / 审批链接防伪造 | ✅ 已实现 |
| 自动改代码 + 打包 + 发布 | ❌ **刻意不做**，见下文 |

**为什么不做自动修复发布**：打包要 Windows + PyInstaller + Inno Setup，云端跑不了；而且无人值守发坏包会让所有客户一起炸。这条路保留人工闸门更稳。

## 二、运行时要求（重要）

代码用了 `node:crypto`，**必须跑在 Node.js 运行时**（≥18）。

⚠️ 不能部署到 EdgeOne **边缘函数**（V8 运行时，没有 Node 内置模块、不能用 npm、CPU 上限 200ms）。
可以部署到：EdgeOne Makers **云函数**（Node.js）、腾讯云函数 SCF、腾讯云轻量服务器、或任意有 Node 的服务器。

## 三、需要准备的东西

| 凭据 | 在哪拿 |
|---|---|
| `WECOM_CORP_ID` | 企业微信管理后台 → 我的企业 → 企业信息 → 企业ID |
| `WECOM_KF_SECRET` | 客户联系 → 微信客服 → API → Secret |
| `WECOM_KF_ID` | 微信客服 → 客服账号 → 账号ID（`wk` 开头） |
| `WECOM_TOKEN` / `WECOM_AES_KEY` | 微信客服 → API → 接收消息 → 设置回调时自己填 / 后台随机生成 |
| `GROUP_WEBHOOK_URL` | 企业微信内部群 → 群设置 → 群机器人 → 添加 → 复制 Webhook |
| `LICENSE_SEED_HEX` | 本机 `tools/license_private.key` 的内容（64 位 hex） |
| `APPROVE_SECRET` | 自己起一个长随机串，审批链接靠它防伪造 |
| `PUBLIC_BASE_URL` | 机器人对外 HTTPS 地址 |

回调地址填：`https://你的域名/wecom/callback`

## 四、跑起来

```bash
cd bot
cp .env.example .env      # 然后填入上面的凭据
npm test                  # 本地自测，不联网
npm start                 # 本机跑起来（默认 8787）
```

自测覆盖 24 项：加解密往返、签名校验、审批防伪造、端到端发码链路。全部通过才算可用。

生产部署（以轻量服务器为例）：

```bash
# 用 systemd 或 pm2 常驻，前面挂 Nginx + 免费 SSL
pm2 start server.mjs --name llb-bot
```

也可用腾讯云函数 SCF：入口改成导出 `createHandler` 返回的 `handleRequest` 即可。

## 五、安全

- `LICENSE_SEED_HEX` 是**私钥 = 印钞机**。泄露后任何人都能给自己签永久码，且只能靠换密钥对补救（换密钥对 = 已发出的码全部失效）。
- 审批链接带 HMAC 签名，猜订单号也没用；订单发过码就锁死。
- `.env` 已在 `.gitignore` 里，不要提交。
- 云端只放签名所需私钥，**不要把 `tools/` 整个目录传上去**。

## 六、文件结构

```
bot/
  server.mjs          本地 HTTP 入口
  src/license.mjs     发卡内核（与 Python 客户端完全对齐）
  src/wecom.mjs       企业微信加解密 / 客服 API / 群机器人
  src/handler.mjs     回调分流 + 审批发码
  src/replies.mjs     自动回复规则 + 机器码识别
  src/store.mjs       订单存储（内存/文件/KV）
  src/config.mjs      环境变量
  test/               自测（npm test）
```

`src/license.mjs` 签出的码已用 Python 客户端 `licensing.verify_license()` 交叉验证通过，
含内嵌 Token 与中文客户名场景（`tools/_verify_bot_license.py`）。
