# 腾讯云 COS 发版配置说明

用 `tools/publish_release.py` 一键把安装包和 version.json 推到腾讯云 COS，客户打开软件就能收到更新提示。

---

## 一、开通（只需做一次，约 10 分钟）

### 1. 建存储桶

进腾讯云控制台 → 对象存储 COS → 存储桶列表 → 创建存储桶：

| 项 | 填什么 |
|---|---|
| 名称 | 随便起，如 `liuliangbushou` |
| 地域 | 选离你近的，如 `ap-guangzhou`（广州） |
| 访问权限 | **公有读私有写**（关键：必须是公有读，否则客户下不了） |
| 其他 | 默认即可 |

建好后桶名会带 AppID，形如 `liuliangbushou-1250000000`，这个完整名字就是配置里的 `bucket`。

### 2. 拿密钥

访问管理 → 访问密钥 → API 密钥管理 → 新建密钥，得到 `SecretId` 和 `SecretKey`。

**安全建议**：不要直接用主账号密钥。在「用户 → 新建子用户」里建一个只给 COS 写权限的子账号，用它的密钥。万一泄露，损失可控。

### 3. 填配置

把 `tools/cos_config.json.example` 复制成 `tools/cos_config.json`，填进去：

```json
{
  "secret_id": "AKID...",
  "secret_key": "...",
  "bucket": "liuliangbushou-1250000000",
  "region": "ap-guangzhou",
  "prefix": "trafficcatcher",
  "cdn_domain": ""
}
```

`cos_config.json` 已在 `.gitignore` 里，不会被提交出去。

---

## 二、发版（每次更新用）

```bash
# 1) 干跑：只生成本地 version.json，不联网，先看一眼对不对
python tools/publish_release.py --dry-run

# 2) 正式发布
python tools/publish_release.py --notes "修复直播录制偶发中断"

# 3) 首次发布时，顺手把更新地址写回 branding.py
python tools/publish_release.py --set-feed
```

脚本会自动：找最新安装包 → 算体积和 SHA256 → 生成 version.json → 上传 → 回读校验远端体积 → 打印直链。

### 常用参数

| 参数 | 作用 |
|---|---|
| `--notes "…"` | 更新说明，客户在更新弹窗里能看到 |
| `--mandatory` | 标记为强制更新，客户必须更才能用 |
| `--dry-run` | 只生成本地文件，不上传 |
| `--set-feed` | 把更新地址写回 branding.py |
| `--setup 路径` | 指定安装包（默认自动找最新版） |
| `--version x.y.z` | 覆盖版本号（默认读 branding.py） |

---

## 三、成本

外网下行流量 0.5–0.6 元/GB（按地域），存储 0.118 元/GB/月。按 39MB 安装包算：

| 月下载量 | 流量 | 月成本 |
|---|---|---|
| 50 次 | 1.95 GB | ≈ 1 元 |
| 200 次 | 7.8 GB | ≈ 4 元 |
| 500 次 | 19.5 GB | ≈ 10 元 |

存储费可忽略。充 50 元够用一两年。

---

## 四、要注意的几件事

**改了 branding.py 就得重新打包。** `--set-feed` 只改源码，已经装到客户电脑上的旧版本仍然指向旧地址。所以更新地址最好在首次发布时就定死，之后别再改。

**CDN 备案问题。** COS 默认域名（`<桶名>.cos.<地域>.myqcloud.com`）可以直接公网访问，**不需要备案**。但如果你想套 CDN 进一步降流量费，中国大陆 CDN 必须绑定已备案域名 —— 没备案就先用 COS 默认域名，够用。

**防盗链。** 直链是公开的，理论上能被刷流量。建议：在 COS 控制台设「流量告警阈值」（比如 20GB/天就短信提醒你），发现异常及时把桶改成私有读。别开 Referer 防盗链 —— 软件下载请求不带 Referer，会误伤正常客户。

**配了 CDN 要记得刷新缓存。** 上传新 version.json 后如果客户还拿到旧的，就是缓存没刷。去 CDN 控制台刷新 `/trafficcatcher/version.json` 这个路径。

---

## 五、日常发版：用一条龙，别手搓

改完代码跑这一条就够，`tools/release_all.py` 会把下面这些全做完：

```bash
python tools/release_all.py --notes "修复直播录制偶发中断"
```

它会依次执行：

| 步骤 | 做什么 |
|---|---|
| 0 | GUI 无头冒烟（10 个页面）+ 编译检查，没过就中止 |
| 1 | 版本号 +1（1.1.0 → 1.1.1），同步 branding.py 和 installer.iss 全部出现处 |
| 2 | 清空 dist（不清会被环境拦截导致打包中断） |
| 3 | PyInstaller 打包 → Inno Setup 生成安装包 |
| 4 | 上传 COS + 回读校验体积 |
| 5 | 端到端匿名验证：拉 version.json、比对 SHA256、下载前 1MB 验 MZ 文件头 |
| 6 | 复制安装包到桌面 |
| 7 | 打印版本号 / 体积 / SHA256 / 直链 + **可直接转发给客户的通知文案** |
| 8 | 追加到 `installer_output/release_log.md` |

**中途任何一步失败就停**，不会留半个版本在云上。

常用参数：

```bash
--bump patch|minor|major   # 版本号怎么加，默认 patch；新功能用 minor
--no-pack                  # 跳过打包，只重传现有安装包
--skip-smoke               # 跳过 GUI 冒烟（赶时间）
--dry-run                  # 只打印将要做什么，不真的执行
```

只有在**改动了 `branding.py` 的 UPDATE_FEED_URL** 时才需要额外加 `--set-feed`（走 publish_release.py），普通更新不用。
