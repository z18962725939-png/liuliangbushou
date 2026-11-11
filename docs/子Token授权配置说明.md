# 子 Token 授权配置说明（操作手册）

> 适用产品：流量捕手（TrafficCatcher）v1.0.0
> 适用角色：软件作者（发卡方）。客户侧操作见第九章。
> 最后核对代码：`justoneapi_collector/licensing.py` / `tools/license_keygen.py`

---

## 一、这是什么（先讲清楚概念）

流量捕手的商业化授权方案由三块拼成，**一个都不能少**：

| 拼图 | 在哪里控制 | 说明 |
|------|-----------|------|
| **每客户独立子 Token** | JustOneAPI 后台 | 给每个客户单独开一个 API 子账号 Token，**不要**用你自己的主 Token |
| **预算上限** | JustOneAPI 后台 | 在每个子 Token 上设用量配额/额度，超额即停，互不牵连 |
| **加密进授权码** | `licensing.py` + 发卡工具 | 子 Token 加密后塞进授权码，客户无感、看不到明文 |

为什么是「子 Token + 预算」而不是「一个主 Token 给所有人用」：

- ✅ **预算隔离**：某客户刷爆额度，只停他一个，不影响你和其他客户；
- ✅ **风险隔离**：子 Token 泄露，你随时在后台吊销/重开一个，主 Token 永远不碰客户环境；
- ✅ **客户体验**：客户装好软件、贴个授权码就能用，不用自己去 JustOneAPI 充值填 Token。

---

## 二、整体工作原理（一图胜千言）

```
   你（作者）                         客户（运行流量捕手）
   ┌──────────────┐                  ┌────────────────────┐
   │ JustOneAPI   │  开子Token+预算   │                    │
   │   后台        │ ───────────────► │  每台机器有机器码   │
   └──────┬───────┘                  │  XXXX-XXXX-XXXX-   │
          │ 拿到子Token                │  XXXX             │
          ▼                          └─────────┬──────────┘
   ┌──────────────┐                               │ 把机器码发你
   │ 发卡工具      │  子Token → encrypt_token      │
   │ issue_license │  (TOKEN_SEED + 随机nonce加密)  │
   │  + Ed25519签名│ ────── 授权码 LLBS1-xxxx.yyyy ──┘
   └──────────────┘
                                         客户粘贴/双击 .lic 激活
                                         → verify_license 验签
                                         → decrypt_token 解出子Token
                                         → embedded_token() 自动喂给 client
```

授权码为什么伪造不了：用 **Ed25519 非对称签名**（RFC 8032）。你手里有**私钥**只负责签发，安装包里只内置**公钥**只能验签不能签发；客户就算把 exe 反编译也拿不到私钥，造不出任何授权码。

---

## 三、前置准备（只做一次）

### 1. 生成密钥对（私钥你自己留着）
私钥文件：`tools/license_private.key`（32 字节 hex）。首次使用先生成：

```bash
cd 项目根目录
python -c "import secrets,pathlib;p=pathlib.Path('tools/license_private.key');p.write_text(secrets.token_hex(32))"
```

### 2. 把公钥写进程序
生成后，用下面命令算出公钥，粘进 `justoneapi_collector/licensing.py` 的 `LICENSE_PUBLIC_KEY_HEX`：

```bash
python -c "from justoneapi_collector import licensing; print(licensing.ed25519_public_key(bytes.fromhex(open('tools/license_private.key').read().strip())).hex())"
```

> ⚠️ **发卡工具的 GUI 会自检私钥与内置公钥是否匹配**。不匹配时签出的码客户激活必报「签名校验失败」，务必先对齐。

### 3. 固定 Token 加密种子
`licensing.py` 里的 `TOKEN_SEED_HEX` 决定 Token 怎么加密。**商用后不要再改**——改了，已经发出去的所有授权码里带的子 Token 就解不开了。

---

## 四、给客户开子 Token + 设预算上限（JustOneAPI 后台）

这一步在 **JustOneAPI 平台侧**完成，不在本仓库代码里：

1. 登录 JustOneAPI 后台，为**该客户**新建一个子 Token（Sub Token / 子账号），不要用你的主 Token；
2. 给这个子 Token 设置**预算上限 / 月用量配额**（平台通常叫「额度」「配额」「预算」）；
3. 复制这个子 Token 字符串，下一步发卡时填进「接口 Token」字段。

> ✅ 预算上限就是靠这里实现的。授权码本身**不携带**预算数字（见第十一章），超额拦截发生在 JustOneAPI 后台对子 Token 的配额判断上。

---

## 五、发卡：把子 Token 加密进授权码

### 方式 A：GUI 发卡工具（推荐，有自检）

```bash
cd 项目根目录
python tools/license_keygen.py --gui
```

单张出码步骤：
1. **客户名称** → 会写进授权码，客户激活后能看到（便于你台账管理）；
2. **机器码** → 客户在「设置 → 授权与激活」里复制给你；填 `ANY` = 不限机器（工作室/多机版）；
3. **有效期** → 30/90/180/365 天或永久（0）；
4. **接口 Token** → 把第四章拿到的**子 Token** 粘进来（选填，但强烈建议填，客户体验最好）；
5. 点「🔏 生成授权码」→ 工具会**自检**自己签的码自己验得过 → 复制或「💾 保存为 .lic」发给客户。

批量出码（一次性卖给团队）：
- 下方文本框一行一个：`客户名 机器码 [天数]`，例如：
  ```
  张三 A1B2-C3D4-E5F6-7890 365
  某某工作室 ANY 0
  ```
- 点「⚡ 批量生成」→「📊 导出 CSV」（带 BOM，Excel 不乱码）。
- ⚠️ 批量模式默认 Token 留空（每个客户子 Token 不同，需单独填时请走单张出码）。

> 💡 工具上有「用我本机的 Token」按钮——它读的是**你本机配置里的 Token**。**仅当你自己的商业模式就是"客户自带 Token"时别用**；按本手册的「每客户子 Token」模式，请手动填该客户的子 Token，不要用你主账号的 Token。

### 方式 B：命令行（适合脚本 / 自动化）

```bash
cd 项目根目录
# 用法：客户名 机器码 [天数] [Token]
python tools/license_keygen.py 张三 A1B2-C3D4-E5F6-7890 365 "cust_xxxx_subtoken_xxxx"
```

输出就是一行授权码字符串，直接发给客户即可。

---

## 六、Token 加密机制（技术 / 审计用）

相关函数：`licensing.encrypt_token` / `decrypt_token` / `build_payload`。

- 每个授权码签发时生成**随机 nonce**（`secrets.token_hex(6)`）；
- 加密密钥 = `SHA256(TOKEN_SEED_HEX + "|tok|" + nonce)`；
- 用 SHA256 计数器模式生成密钥流，对 Token 做 XOR 流加密，末尾附 8 字节 HMAC（防篡改，改一个字节就解不出）；
- 密文做 URL-safe base64（无填充），随载荷一起进授权码。

**效果**：每个客户的密文都不一样（nonce 不同），客户拿到授权码也看不到明文子 Token；程序内部 `decrypt_token` 用同一个 nonce 还原。

**安全边界（不吹牛）**：
- ✅ 防得住：随手翻文件 / 记事本搜索 / 反编译看公钥级别窥探；
- ❌ 防不住：专门针对你做逆向的高手。但即使他被拿下子 Token，也**伪造不出授权码**，你能在 JustOneAPI 后台通过用量异常发现并吊销该子 Token。

---

## 七、客户侧激活流程

1. 客户下载安装包 → 打开流量捕手 → 「设置 → 授权与激活」里看到**本机机器码**；
2. 客户把机器码发给你 + 付钱；
3. 你在第五章发卡，把授权码（或 .lic 文件）发回；
4. 客户在软件里**粘贴授权码**点击激活，或**双击 .lic 文件**自动激活；
5. 激活成功 → `licensing.activate()` 验签通过 → 写入本地状态 → 立即生效。

激活失败的常见原因（程序会直接给文案）：
- 「签名校验失败」→ 私钥与程序公钥不匹配，回第三章第 2 步；
- 「绑定的是另一台电脑」→ 机器码对不上，一码一机，需换机请重发；
- 「已于 YYYY-MM-DD 到期」→ 该码过期，续费重发。

---

## 八、运行期取用子 Token（接线说明）

激活后，程序在任意需要调用 JustOneAPI 的地方通过：

```python
from justoneapi_collector import licensing
token = licensing.embedded_token()   # 返回解密后的子 Token（未授权返回 ""）
client = JustOneAPICollector(token=token)
```

`embedded_token()` 的逻辑：仅在 `licensing.status()["licensed"]` 为真时才返回内嵌 Token，否则返回空串（试用 / 过期状态不泄露 Token）。

> ⚠️ **当前接线状态（诚实标注）**：`licensing` 模块已完整实现「签发 → 加密 → 验签 → 解密取回」全链路，但 `gui_qt/app.py` 的 `build_context()` 目前仍是**演示模式**（`client=None`），真正的 `JustOneAPICollector(token=licensing.embedded_token())` 注入尚未接上。要让授权码里的子 Token 真正生效驱动采集，还需完成「运行期接线」这一步（前一轮方案中的 D 项）。

---

## 九、预算上限的控制与超额处理

- **预算上限在 JustOneAPI 后台对子 Token 设置**，不在授权码里；
- 授权码的载荷字段为：`v,n,m,e,c,f,t,i`（版本 / nonce / 机器 / 过期时间 / 客户 / 功能位 / 加密Token / 签发时间），**没有 budget 字段**；
- 超额发生时，拦截点在 JustOneAPI 后台对该子 Token 的配额判断，返回业务错误，客户端照常提示「接口额度不足 / 请续费」即可；
- 🔧 **可选增强**（当前未实现）：若你希望授权码也携带预算上限、在客户端做前置拦截或展示，可在 `licensing.build_payload` 里加一个 `b=预算值` 段，并在 `parse_payload` / `verify_license` 里解析。需要我加可以另开任务。

---

## 十、作废 / 换机 / 续费

| 场景 | 怎么做 |
|------|--------|
| **停服某个客户** | 下次给他发的码里**不带 Token 字段**（空字符串），或在 JustOneAPI 后台吊销该子 Token；也可靠到期时间自然过期 |
| **换电脑** | 授权码默认一码一机（绑定机器码）。客户换机请把新机器码发你，重新出码；或当初机器码填 `ANY`（不限机器，适合工作室版） |
| **续费** | 原码到期前/后，重新发一张新码给客户，客户再次激活即覆盖 |
| **批量作废** | 没有"一键全废"接口，逐客户重发不带 Token 的码，或统一换密钥对（见红线） |

客户侧可自己在「设置 → 授权与激活」点「注销授权」（`licensing.deactivate()`）清除本地授权码。

---

## 十一、安全红线（必读，别踩）

- 🔴 **私钥 `tools/license_private.key` 绝对不能随安装包分发**。拿到它的人能给自己签任意永久授权码。打包脚本（iss）已排除 `tools/` 目录；
- 🔴 **不要把项目目录整体压缩发给别人**；
- 🔴 **换电脑先拷走私钥**。丢了只能换密钥对 → 换密钥对意味着**所有老客户的授权码全部失效**，只能重新发码；
- 🔴 `TOKEN_SEED_HEX` 决定后不要改（见第三章第 3 步）；
- 🟢 公钥、加密种子都是「防君子不防小人」级别，核心是 Ed25519 非对称签名防止伪造授权码。

---

## 十二、关键代码位置速查

| 想改什么 | 去哪改 |
|----------|--------|
| 授权码前缀 / 版本号 | `licensing.py` → `LICENSE_PREFIX = "LLBS1"` |
| 试用天数 | `licensing.py` → `TRIAL_DAYS = 7` |
| 功能清单（门禁） | `licensing.py` → `FEATURES` |
| 公钥 / 加密种子 | `licensing.py` → `LICENSE_PUBLIC_KEY_HEX` / `TOKEN_SEED_HEX` |
| 签发授权码 | `licensing.py` → `issue_license(...)` |
| Token 加解密 | `licensing.py` → `encrypt_token` / `decrypt_token` |
| 发卡工具 | `tools/license_keygen.py`（GUI 与 CLI 都在这里） |
| 运行期取 Token | `licensing.py` → `embedded_token()`（待接入 client） |
| 机器码展示 / 激活 UI | `gui_qt/pages/settings_page.py` |
| 产品名 / 联系方式 | `justoneapi_collector/branding.py`（含 `APP_CONTACT` 购买提示） |

---

## 十三、一句话流程（贴墙版）

> 后台开**子 Token + 预算** → 拿客户**机器码** → 发卡工具填「客户名 / 机器码 / 有效期 / 子Token」→ 生成授权码发给客户 → 客户激活 → 程序自动用子 Token 跑接口 → 超额/过期在后台拦。
