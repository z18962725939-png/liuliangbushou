# -*- coding: utf-8 -*-
"""
生成「客户下载页」并上传到 COS —— 发微信群里的就是这个页面。

为什么要有这个页面
──────────────────
直接把 exe 直链丢群里有两个问题：
  1. 微信内置浏览器经常拦 exe 下载，客户点了没反应，还以为你跑路了；
  2. 一串带 %E6%B5%81… 的编码链接看着像钓鱼网站，转化率低。
做成页面后，群里只发一个干净链接，页面里能讲清「怎么装、怎么付、怎么激活」，
客户自助完成，不用你一对一解释。

⚠️ 腾讯云 COS 会强制下载 HTML（响应头固定带 Content-Disposition: attachment +
x-cos-force-download: true，设 inline / 开静态网站托管都无效），所以这个页面
**不能只放 COS**，必须再部署到真正的静态托管（EdgeOne Pages）。用 --standalone
生成自包含版本（收款码转 base64 内嵌，不依赖任何其他资源）再部署。

用法
────
    python tools/build_download_page.py              # 生成 + 上传 COS
    python tools/build_download_page.py --standalone # 额外产出自包含版（给静态托管用）
    python tools/build_download_page.py --local      # 只生成本地文件，看看效果

页面信息（版本号 / 体积 / 更新日期）全部从 installer_output/version.json 读，
所以每次发版跑一下就会自动更新，不用手改。

收款二维码
──────────
看 tools/payment_config.json：
  · wechat_qr / alipay_qr  → 你的收款码图片路径，会自动上传并在页面弹出
  · wechat_id              → 没有图片时的备用方案，生成「加作者微信」二维码
  · pay_link               → 有付款链接时，点按钮直接跳转，不弹二维码
三者都没配 → 页面不显示购买按钮，退回「加微信联系作者」。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# 更新日志与官网共用同一套渲染（tools 目录随脚本一起跑，直接 import 即可）
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import build_changelog as _bclog
except ImportError:                                     # noqa: BLE001
    _bclog = None

ROOT = Path(__file__).resolve().parent.parent
INSTALLER_OUT = ROOT / "installer_output"
CONFIG_FILE = ROOT / "tools" / "cos_config.json"
PAY_CONFIG_FILE = ROOT / "tools" / "payment_config.json"
OUT_HTML = INSTALLER_OUT / "download.html"

# ── 定价与文案：改这里就能改页面上的价格 ──────────────────────────────
PRICE_PLANS = [
    {
        "name": "尝鲜版",
        "price": "19.9",
        "unit": "元",
        "period": "38 天",
        "desc": "31 天 + 赠送 7 天，仅限首购",
        "quota": "每天 10 次采集额度",
        "tag": "首购专享",
        "hot": True,
        "buy": True,
    },
    {
        "name": "续费",
        "price": "29.9",
        "unit": "元",
        "period": "每月",
        "desc": "到期接着用，数据不丢",
        "quota": "每天 30 次采集额度",
        "tag": "",
        "hot": False,
        "buy": True,
    },
    {
        "name": "专业版",
        "price": "59.9",
        "unit": "元",
        "period": "每月",
        "desc": "重度用户 / 工作室",
        "quota": "每天 100 次采集额度",
        "tag": "",
        "hot": False,
        "buy": True,
    },
]
PROMO_TEXT = "首购 19.9 元即得 38 天（31 天 + 赠送 7 天），到期后续费 29.9 元/月，数据不丢。"
CONTACT_TEXT = "购买 / 续费 / 有问题，直接微信联系作者"

# 二维码图片上传到 COS 后的存放位置（前缀 + 文件名）
QR_REMOTE = {
    "wechat": "pay_wechat.png",
    "alipay": "pay_alipay.png",
}

# 真正发给客户的页面地址（静态托管，点开就是网页，不像 COS 那样被强制下载）
# 客户真正访问的下载页地址。
# 曾用 WorkBuddy 托管域名，但它对微信 User-Agent 返回 403「请在浏览器中打开」——
# 客户在微信里点链接直接被拒。COS 静态网站（myqcloud.com）不拒绝微信 UA，故迁到这里。
# 换托管时记得同步改 build_poster.py 的 DOWNLOAD_URL。
PAGE_URL = "https://liuliangbushou-1258850027.cos-website.ap-guangzhou.myqcloud.com/"


def _mb(size: int) -> str:
    return f"{size / 1024 / 1024:.1f} MB"


# ─────────────────────────────────────────────────────────────────────
#  收款二维码：本地图 → COS → 拿到直链
# ─────────────────────────────────────────────────────────────────────

def read_pay_config() -> dict:
    """读 payment_config.json，忽略下划线开头的注释键。"""
    if not PAY_CONFIG_FILE.exists():
        return {}
    raw = json.loads(PAY_CONFIG_FILE.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def make_qr_from_text(text: str, out_path: Path) -> bool:
    """用 qrcode 库把一段文本（如微信号）生成二维码 PNG。"""
    try:
        import qrcode  # type: ignore
    except ImportError:
        print("   [跳过] 没装 qrcode 库，无法生成备用二维码（pip install qrcode pillow）")
        return False
    img = qrcode.make(text)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path))
    return True


def collect_qr_images(pay: dict) -> list[dict]:
    """
    把本地收款码图片收集到 installer_output 待上传。
    返回 [{"label": "微信", "key": "pay_wechat.png", "local": Path}, ...]
    """
    jobs: list[dict] = []

    # 1) 微信收款码
    wx = (pay.get("wechat_qr") or "").strip()
    if wx:
        p = (ROOT / wx) if not Path(wx).is_absolute() else Path(wx)
        if p.exists():
            dst = INSTALLER_OUT / QR_REMOTE["wechat"]
            shutil.copyfile(p, dst)
            jobs.append({"label": "微信", "key": QR_REMOTE["wechat"], "local": dst})
        else:
            print(f"   [警告] wechat_qr 指向的图片不存在：{p}")

    # 2) 支付宝收钱码
    ali = (pay.get("alipay_qr") or "").strip()
    if ali:
        p = (ROOT / ali) if not Path(ali).is_absolute() else Path(ali)
        if p.exists():
            dst = INSTALLER_OUT / QR_REMOTE["alipay"]
            shutil.copyfile(p, dst)
            jobs.append({"label": "支付宝", "key": QR_REMOTE["alipay"], "local": dst})
        else:
            print(f"   [警告] alipay_qr 指向的图片不存在：{p}")

    # 3) 备用：微信号 → 生成「加作者微信」二维码
    if not jobs and (pay.get("wechat_id") or "").strip():
        dst = INSTALLER_OUT / QR_REMOTE["wechat"]
        if make_qr_from_text(pay["wechat_id"].strip(), dst):
            jobs.append({"label": "微信", "key": QR_REMOTE["wechat"], "local": dst})

    return jobs


def upload_qr_images(jobs: list[dict], client, bucket: str, prefix: str, base: str) -> list[dict]:
    """上传二维码图片，返回带直链的列表。"""
    out = []
    for j in jobs:
        ext = j["local"].suffix.lower()
        ctype = {".png": "image/png", ".jpg": "image/jpeg",
                 ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext, "image/png")
        key = f"{prefix}/{j['key']}"
        client.put_object_from_local_file(
            Bucket=bucket, LocalFilePath=str(j["local"]), Key=key,
            ContentType=ctype,
            Metadata={"Cache-Control": "no-store, max-age=0"},
        )
        out.append({"label": j["label"], "url": f"{base}/{key}"})
        print(f"   [OK] 已上传 {j['label']}收款码 → {key}")
    return out


# ─────────────────────────────────────────────────────────────────────
#  页面
# ─────────────────────────────────────────────────────────────────────

CSS_EXTRA = """
  .buy{{display:block;width:100%;margin-top:12px;padding:10px;border:0;
       background:#e8453c;color:#fff;font-size:15px;font-weight:600;
       border-radius:8px;cursor:pointer;font-family:inherit}}
  .buy.ghost{{background:#fff;color:#e8453c;border:1.5px solid #e8453c}}
  .buy:active{{opacity:.75}}
  .mask{{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:99;
        display:none;align-items:center;justify-content:center;padding:20px}}
  .mask.on{{display:flex}}
  .sheet{{background:#fff;border-radius:16px;width:100%;max-width:340px;
         padding:22px 20px 18px;text-align:center;position:relative}}
  .sheet h3{{font-size:17px;margin-bottom:4px}}
  .sheet .amt{{font-size:26px;font-weight:700;color:#e8453c;margin-bottom:14px}}
  .qrimg{{width:240px;height:240px;object-fit:contain;border:1px solid #eee;
         border-radius:10px;background:#fff}}
  .qrtip{{font-size:13px;color:#e8453c;font-weight:600;margin-top:12px}}
  .qrsub{{font-size:12px;color:#888;margin-top:6px}}
  .sheet .after{{font-size:13px;color:#555;background:#fafafa;border-radius:8px;
                padding:10px;margin-top:14px;text-align:left;line-height:1.6}}
  .close{{margin-top:14px;width:100%;padding:11px;border:1px solid #ddd;
         background:#fff;border-radius:8px;font-size:15px;color:#666;
         cursor:pointer;font-family:inherit}}
  .tabs{{display:flex;gap:8px;justify-content:center;margin-bottom:12px}}
  .tab{{padding:5px 16px;border:1px solid #ddd;border-radius:20px;font-size:13px;
       color:#666;cursor:pointer;background:#fff}}
  .tab.on{{background:#e8453c;color:#fff;border-color:#e8453c}}
"""

JS = """
var QR = %s;
var cur = 0;
function openPay(name, price){
  document.getElementById('pname').textContent = name;
  document.getElementById('pamt').textContent = price;
  var tabs = document.getElementById('tabs');
  if (QR.length > 1){
    tabs.style.display = 'flex';
    tabs.innerHTML = '';
    QR.forEach(function(q, i){
      var b = document.createElement('div');
      b.className = 'tab' + (i === 0 ? ' on' : '');
      b.textContent = q.label;
      b.onclick = function(){
        cur = i;
        document.getElementById('qrimg').src = q.url;
        var all = tabs.querySelectorAll('.tab');
        for (var k = 0; k < all.length; k++) all[k].className = 'tab' + (k === i ? ' on' : '');
      };
      tabs.appendChild(b);
    });
  } else {
    tabs.style.display = 'none';
  }
  cur = 0;
  document.getElementById('qrimg').src = QR[0].url;
  document.getElementById('mask').classList.add('on');
  document.body.style.overflow = 'hidden';
}
function closePay(){
  document.getElementById('mask').classList.remove('on');
  document.body.style.overflow = '';
}
document.getElementById('mask').addEventListener('click', function(e){
  if (e.target.id === 'mask') closePay();
});
"""


def build_html(version: str, published: str, size: int, url: str, notes: str,
               qr_list: list[dict], pay: dict,
               changelog: list[dict] | None = None,
               free_version: str = "", free_size: int = 0,
               free_url: str = "") -> str:
    pay_link = (pay.get("pay_link") or "").strip()
    pay_note = (pay.get("pay_note") or "长按二维码 → 识别图中二维码 → 付款").strip()
    after_pay = (pay.get("after_pay") or "付款后把截图发给作者，再把机器码一起发来。").strip()
    has_qr = bool(qr_list)
    can_buy = has_qr or bool(pay_link)

    # ★ 免费版独立安装包按钮（2026-10-23）
    #   这个下载页是客户从微信里点进来的第一站。以前只有一个「下载 Windows 版」，
    #   点下去拿到的是完整版安装包 —— 客户装上看到一排要授权码的功能，第一反应
    #   就是"怎么用不了"。免费版单独给一个按钮，两个版本分清楚。
    #   没有免费版产物（version_free.json 还没生成）时不显示，页面不会空一块。
    if free_url:
        free_btn = (
            f'<a class="btn btn-free" href="{free_url}">↓ 下载免费版 '
            f'v{free_version or version}（只要视频下载 + 直播录制）</a>'
        )
        free_size_txt = (f"　·　免费版 {_mb(free_size)}" if free_size else "")
    else:
        free_btn = ""
        free_size_txt = ""

    plans_html = ""
    for p in PRICE_PLANS:
        tag_html = f'<span class="tag">{p["tag"]}</span>' if p["tag"] else ""
        hot_cls = " hot" if p["hot"] else ""
        price_txt = f"¥{p['price']}"
        if can_buy and p.get("buy", True):
            if pay_link:
                btn = (f'<a class="buy" href="{pay_link}" target="_blank" '
                       f'rel="noopener">立即购买</a>')
            else:
                btn = (f'<button class="buy" onclick="openPay(\'{p["name"]}\','
                       f'\'{price_txt}\')">立即购买</button>')
        else:
            btn = ""
        plans_html += f"""
        <div class="plan{hot_cls}">
          <div class="plan-name">{p['name']}{tag_html}</div>
          <div class="plan-price"><span class="cur">¥</span>{p['price']}<span class="per">/ {p['period']}</span></div>
          <div class="plan-desc">{p['desc']}</div>
          <div class="plan-quota">{p['quota']}</div>
          {btn}
        </div>"""

    notes_html = f'<p class="notes">本次更新：{notes}</p>' if notes else ""

    # 更新日志栏目：与安装包里的日志页共用同一套渲染，两边长得一模一样。
    # 首页只直接铺最近 6 条，更早的收进折叠 —— 二十几个版本全摊开会长得没边。
    if _bclog and changelog:
        clog_body = _bclog.render_entries_html(changelog, newest=version,
                                               fold_after=6)
        clog_css = _bclog.CLOG_CSS
        clog_html = f"""
  <div class="card">
    <h2>更新日志</h2>
    <p class="sub" style="margin-bottom:12px">共 {len(changelog)} 个版本 · 最新版 v{version}</p>
    {clog_body}
  </div>
"""
    else:
        clog_css = ""
        clog_html = ""

    # 弹层只在有二维码时渲染
    if has_qr:
        sheet = f"""
  <div class="mask" id="mask">
    <div class="sheet">
      <h3>购买 <span id="pname"></span></h3>
      <div class="amt" id="pamt"></div>
      <div class="tabs" id="tabs"></div>
      <img class="qrimg" id="qrimg" src="" alt="收款码">
      <p class="qrtip">{pay_note}</p>
      <p class="qrsub">付完别关，截图留着</p>
      <div class="after">{after_pay}</div>
      <button class="close" onclick="closePay()">关闭</button>
    </div>
  </div>"""
        js_block = f"<script>\n{JS % json.dumps(qr_list, ensure_ascii=False)}\n</script>"
    else:
        sheet = ""
        js_block = ""

    if not can_buy:
        buy_hint = ('<div class="promo">付款方式：加作者微信，转完把截图发过去即可。</div>')
    else:
        buy_hint = f'<div class="promo">{PROMO_TEXT}</div>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>流量捕手 · 下载</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
       background:#f5f6f8;color:#1a1a1a;line-height:1.7;padding:16px}}
  .wrap{{max-width:560px;margin:0 auto}}
  .card{{background:#fff;border-radius:14px;padding:22px;margin-bottom:14px;
        box-shadow:0 2px 12px rgba(0,0,0,.06)}}
  h1{{font-size:24px;margin-bottom:6px}}
  .sub{{color:#666;font-size:14px}}
  .meta{{margin-top:14px;font-size:13px;color:#888;
        border-top:1px dashed #eee;padding-top:12px}}
  .meta span{{margin-right:14px;display:inline-block}}
  .btn{{display:block;text-align:center;background:#1a73e8;color:#fff;
       text-decoration:none;font-size:18px;font-weight:600;
       padding:16px;border-radius:12px;margin:18px 0 10px}}
  .btn:active{{background:#1557b0}}
  .btn-free{{background:#0b7a54}}
  .btn-free:active{{background:#095f42}}
  .btn-sub{{text-align:center;font-size:13px;color:#888}}
  .warn{{background:#fff8e1;border-left:4px solid #f5a623;padding:12px 14px;
        border-radius:8px;font-size:14px;color:#7a5200;margin-bottom:14px}}
  h2{{font-size:17px;margin-bottom:12px}}
  .steps{{counter-reset:s}}
  .step{{position:relative;padding-left:34px;margin-bottom:14px;font-size:15px}}
  .step:before{{counter-increment:s;content:counter(s);position:absolute;left:0;top:1px;
    width:24px;height:24px;background:#1a73e8;color:#fff;border-radius:50%;
    font-size:13px;text-align:center;line-height:24px}}
  .plans{{display:flex;gap:10px;flex-wrap:wrap}}
  .plan{{flex:1;min-width:150px;border:2px solid #eee;border-radius:12px;padding:14px;
        display:flex;flex-direction:column}}
  .plan.hot{{border-color:#e8453c;background:#fff6f5}}
  .plan-name{{font-size:15px;font-weight:600;margin-bottom:6px}}
  .tag{{background:#e8453c;color:#fff;font-size:11px;padding:1px 6px;
       border-radius:4px;margin-left:6px;vertical-align:2px}}
  .plan-price{{font-size:26px;font-weight:700;color:#e8453c}}
  .cur{{font-size:15px;margin-right:1px}}
  .per{{font-size:13px;color:#888;font-weight:400}}
  .plan-desc{{font-size:13px;color:#555;margin-top:4px}}
  .plan-quota{{font-size:12px;color:#999;margin-top:6px;flex:1}}
  .promo{{background:#e8f3ff;border-radius:10px;padding:12px;font-size:14px;
         color:#1557b0;margin-top:12px}}
  .notes{{font-size:14px;color:#555;background:#fafafa;border-radius:8px;
         padding:10px 12px;margin-top:12px}}
  .faq dt{{font-weight:600;font-size:14px;margin-top:12px}}
  .faq dd{{font-size:14px;color:#666;margin-left:0}}
  .foot{{text-align:center;font-size:13px;color:#999;padding:10px 0 30px}}
  code{{background:#f2f2f2;padding:1px 5px;border-radius:4px;font-size:13px}}{CSS_EXTRA}{clog_css}
</style>
</head>
<body>
<div class="wrap">

  <div class="card">
    <h1>流量捕手</h1>
    <p class="sub">抖音作品 / 喜欢 / 收藏 一键采集与下载</p>
    <a class="btn" href="{url}">↓ 下载完整版</a>
    {free_btn}
    <p class="btn-sub">完整版 v{version} · {_mb(size)}{free_size_txt} · 支持 Win10 / Win11</p>
    <div class="meta">
      <span>版本 v{version}</span>
      <span>更新 {published}</span>
      <span>{_mb(size)}</span>
    </div>
    {notes_html}
  </div>

  <div class="warn">
    <strong>微信里点下载没反应？</strong><br>
    微信内置浏览器会拦截安装包。请点右上角 <b>···</b> → 选「<b>在浏览器打开</b>」，再点下载即可。
  </div>

  <div class="card">
    <h2>价格</h2>
    <div class="plans">{plans_html}
    </div>
    {buy_hint}
  </div>

  <div class="card">
    <h2>三步开始使用</h2>
    <div class="steps">
      <div class="step">下载并安装（一路下一步即可，不用改任何设置）</div>
      <div class="step">打开软件 → <b>设置 → 授权与激活</b>，复制里面的<b>机器码</b></div>
      <div class="step">点上面「立即购买」付款，把截图 + 机器码发给作者，秒收<b>授权码</b></div>
    </div>
  </div>

  <div class="card">
    <h2>常见问题</h2>
    <dl class="faq">
      <dt>机器码在哪找？</dt>
      <dd>软件里「设置 → 授权与激活」页面上就有，一行字符串，复制发给作者。</dd>
      <dt>换了电脑 / 重装系统还能用吗？</dt>
      <dd>授权码绑定机器码，换机需要重新发机器码给作者换码，不额外收费。</dd>
      <dt>每天额度用完了怎么办？</dt>
      <dd>额度按自然日每天 0 点自动重置。软件内「设置」页能看到今日已用和剩余。</dd>
      <dt>以后怎么升级？</dt>
      <dd>软件右上角「检查更新」，一键升到最新版，数据不会丢。</dd>
    </dl>
  </div>
  {clog_html}
  <p class="foot">{CONTACT_TEXT}</p>
</div>
{sheet}
{js_block}
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="生成并上传客户下载页")
    ap.add_argument("--local", action="store_true", help="只生成本地文件，不上传")
    ap.add_argument("--standalone", action="store_true",
                    help="额外产出 installer_output/standalone/index.html（二维码内嵌，用于静态托管部署）")
    args = ap.parse_args()

    vj = INSTALLER_OUT / "version.json"
    if not vj.exists():
        print("[错误] 没有 version.json，先跑一次发版。")
        return 1
    data = json.loads(vj.read_text(encoding="utf-8"))
    url = data.get("win", {}).get("url", "")
    if not url:
        print("[错误] version.json 里没有下载地址。")
        return 1

    # 更新日志：每次生成页面都自己同步一次 —— 这样不管发版流程里谁先谁后，
    # 官网栏目永远有内容（出现过"页面比数据先生成，栏目整块空白"的坑）。
    changelog: list[dict] = []
    if _bclog:
        try:
            changelog, _info = _bclog.build()
            print(f"更新日志：{len(changelog)} 个版本，最新 v{_info.get('latest', '?')}")
        except Exception as exc:                        # noqa: BLE001
            print(f"  [警告] 更新日志生成失败：{exc}（官网栏目会空着）")
    else:
        print("[警告] 找不到 build_changelog.py，官网更新日志栏目会空着")

    pay = read_pay_config()
    jobs = collect_qr_images(pay)
    qr_list: list[dict] = []

    if args.local:
        # 本地预览：二维码直接用相对路径引用 installer_output 下的图片
        qr_list = [{"label": j["label"], "url": j["key"]} for j in jobs]
    elif jobs:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        from qcloud_cos import CosConfig, CosS3Client  # type: ignore
        client = CosS3Client(CosConfig(Region=cfg["region"], SecretId=cfg["secret_id"],
                                       SecretKey=cfg["secret_key"], Scheme="https"))
        prefix = str(cfg.get("prefix") or "trafficcatcher").strip("/")
        base = (cfg.get("cdn_domain") or
                f"https://{cfg['bucket']}.cos.{cfg['region']}.myqcloud.com").rstrip("/")
        print("上传收款码：")
        qr_list = upload_qr_images(jobs, client, cfg["bucket"], prefix, base)

    # 免费版独立安装包（可选，见 tools/release_free.py）
    free_ver, free_size, free_url = "", 0, ""
    _free_json = INSTALLER_OUT / "version_free.json"
    if _free_json.is_file():
        try:
            _fv = json.loads(_free_json.read_text(encoding="utf-8"))
            _fw = _fv.get("win") or {}
            free_ver = str(_fv.get("version") or "")
            free_size = int(_fw.get("size") or 0)
            free_url = str(_fw.get("url") or "")
        except Exception as exc:                        # noqa: BLE001
            print(f"  [警告] version_free.json 读取失败：{exc}")

    html = build_html(
        version=data.get("version", "?"),
        published=data.get("published", ""),
        size=int(data.get("win", {}).get("size", 0)),
        url=url,
        notes=data.get("notes", ""),
        qr_list=qr_list,
        pay=pay,
        changelog=changelog,
        free_version=free_ver,
        free_size=free_size,
        free_url=free_url,
    )
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"已生成 {OUT_HTML}（{len(html):,} 字节）")

    # ── 自包含版：二维码转 base64 内嵌，整页不依赖任何外部资源 ──
    # 用途是部署到真正的静态托管（COS 强制下载 HTML，页面放 COS 打不开）
    if args.standalone:
        import base64
        stand = html
        for j, q in zip(jobs, qr_list):
            ext = (j["local"].suffix.lstrip(".").lower() or "png")
            if ext == "jpg":
                ext = "jpeg"
            b64 = base64.b64encode(j["local"].read_bytes()).decode()
            stand = stand.replace(q["url"], f"data:image/{ext};base64,{b64}")
        sd = INSTALLER_OUT / "standalone"
        sd.mkdir(parents=True, exist_ok=True)
        sp = sd / "index.html"
        sp.write_text(stand, encoding="utf-8")
        print(f"已生成自包含版 {sp}（{len(stand):,} 字节，可直接部署到静态托管）")

    if not qr_list and not (pay.get("pay_link") or "").strip():
        print("\n[提示] 还没配置收款码，页面上的「立即购买」按钮不会出现。")
        print("       填好 tools/payment_config.json 再跑一次即可。")

    if args.local:
        return 0

    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    from qcloud_cos import CosConfig, CosS3Client  # type: ignore
    client = CosS3Client(CosConfig(Region=cfg["region"], SecretId=cfg["secret_id"],
                                   SecretKey=cfg["secret_key"], Scheme="https"))
    prefix = str(cfg.get("prefix") or "trafficcatcher").strip("/")
    key = f"{prefix}/index.html"
    client.put_object_from_local_file(
        Bucket=cfg["bucket"], LocalFilePath=str(OUT_HTML), Key=key,
        ContentType="text/html; charset=utf-8",
        Metadata={"Cache-Control": "no-store, max-age=0"},
    )
    base = (cfg.get("cdn_domain") or
            f"https://{cfg['bucket']}.cos.{cfg['region']}.myqcloud.com").rstrip("/")
    print(f"已上传 → {key}")
    print(f"\nCOS 地址（会被强制下载，**不能直接发客户**）：\n  {base}/{key}")

    stand = INSTALLER_OUT / "standalone" / "index.html"
    if stand.exists():
        print("\n真正发给客户的链接（静态托管，点开就是网页）：")
        print(f"  {PAGE_URL}")
        print("  内容变了要重新发布：把")
        print(f"  {stand}")
        print("  重新发布一次即可。")
    else:
        print("\n还没生成自包含版，加 --standalone 再跑一次，然后部署到静态托管。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
