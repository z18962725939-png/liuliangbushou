# -*- coding: utf-8 -*-
"""
生成「朋友圈营销海报」——竖版 1080×1440 PNG，配下载页二维码，长按可识别。

为什么要做成脚本
────────────────
海报上的价格、卖点、版本号会随发版变动，手改图片太痛苦。
这里改 PRICE_* / FEATURES 几个常量，跑一次就重新出图（HTML + PNG）。

产出（默认写到 项目根/宣传素材/）
─────────────────────────────────
    流量捕手朋友圈海报.html   可再用浏览器打开微调
    流量捕手朋友圈海报.png    直接发朋友圈的那张图

截图用的是本机 Edge 无头模式，不需要装 playwright / chrome。

用法
────
    python tools/build_poster.py               # 生成 HTML + PNG
    python tools/build_poster.py --html-only   # 只出 HTML（自己看效果）
    python tools/build_poster.py --copy-desktop # 额外复制一份 PNG 到桌面
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "宣传素材"
HTML_PATH = OUT_DIR / "流量捕手朋友圈海报.html"
PNG_PATH = OUT_DIR / "流量捕手朋友圈海报.png"

# ── 内容：改这里就能改海报 ────────────────────────────────────────────────
APP_NAME = "流量捕手"
APP_VERSION = "1.1.0"
TOP_TAG = f"抖音运营提效工具 · Windows 版 v{APP_VERSION}"
SLOGAN = "短视频运营 · 直播监控录制 · 音视频转文字"

# 痛点（一行扎心的问句，比罗列功能更能让人停下来）
PAIN = '还在<span class="hl">一条一条手动抄</span>对标账号？'

# 六大功能：图标用下面 ICONS 的 key
FEATURES = [
    ("like", "喜欢/收藏一键抓", "登录一次，批量导出"),
    ("batch", "批量采集", "一次上百条，自动去重"),
    ("download", "高清视频下载", "原画质，直接存本地"),
    ("live", "直播监控录制", "开播提醒 + 自动录"),
    ("transcribe", "音视频转文字", "本地转写，秒出文案"),
    ("report", "竞品 / 选题复盘", "数据报告直接导出"),
]

# 价格
PRICE_MAIN = "19.9"
PRICE_MAIN_UNIT = "元"
PRICE_MAIN_PERIOD = "/ 38 天"
PRICE_MAIN_NOTE = "首购专享：31 天 + 赠送 7 天"
PRICE_SUBS = [
    ("续费", "29.9", "元/月", "30 次/天"),
    ("专业版", "59.9", "元/月", "100 次/天"),
]

# 下载页链接（二维码内容）—— 客户长按海报就能打开
# 与 build_download_page.py 的 PAGE_URL 保持一致。
# 旧域名对微信 UA 返回 403，海报二维码扫出来打不开，故换成 COS 静态网站。
DOWNLOAD_URL = "https://liuliangbushou-1258850027.cos-website.ap-guangzhou.myqcloud.com/"
QR_TIP = "长按识别二维码 · 直接下载"
CONTACT = "微信 807108536"
FOOTER = "Windows 客户端 · 内置自动更新 · 先免费试用 7 天"

# ── 图标（24×24 线性 SVG，纯手写，不依赖任何图标库）─────────────────────
ICONS = {
    "like": '<path d="M12 20.5S3.5 15 3.5 9.2A4.7 4.7 0 0 1 12 6.6a4.7 4.7 0 0 1 8.5 2.6c0 5.8-8.5 11.3-8.5 11.3Z"/>',
    "batch": '<rect x="3.5" y="3.5" width="7" height="7" rx="1.6"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.6"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.6"/><path d="M17 13.5v7M13.5 17h7"/>',
    "download": '<path d="M12 4v10"/><path d="m7.5 10 4.5 4.5L16.5 10"/><path d="M4.5 19.5h15"/>',
    "live": '<rect x="3" y="6" width="12.5" height="9" rx="2"/><path d="m16 10.5 5-3v9l-5-3"/><circle cx="12" cy="19" r="1.4"/>',
    "transcribe": '<path d="M5 4.5h9l5 5v10a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 19.5Z"/><path d="M14 4.5v5h5"/><path d="M8.5 13h7M8.5 16.5h4.5"/>',
    "report": '<path d="M4.5 19.5h15"/><rect x="6" y="11" width="3" height="6" rx="1"/><rect x="11" y="7" width="3" height="10" rx="1"/><rect x="16" y="13" width="3" height="4" rx="1"/>',
}


def make_qr_data_uri(url: str) -> str:
    """把下载页链接编成二维码，返回可直接塞进 <img src> 的 data URI。"""
    import base64
    import io

    import qrcode
    from qrcode.constants import ERROR_CORRECT_H

    qr = qrcode.QRCode(version=None, box_size=12, border=2,
                       error_correction=ERROR_CORRECT_H)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#101833", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def icon_svg(key: str) -> str:
    return (f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
            f'{ICONS.get(key, "")}</svg>')


def build_html(qr_uri: str) -> str:
    cards = "\n".join(
        f'''      <div class="fx">
        <div class="fx-ico">{icon_svg(key)}</div>
        <div class="fx-t">{title}</div>
        <div class="fx-d">{desc}</div>
      </div>'''
        for key, title, desc in FEATURES
    )
    subs = "\n".join(
        f'''      <div class="sub">
        <span class="sub-n">{name}</span>
        <span class="sub-p">¥{price}</span>
        <span class="sub-u">{unit}</span>
        <span class="sub-q">{quota}</span>
      </div>'''
        for name, price, unit, quota in PRICE_SUBS
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{APP_NAME} 朋友圈海报</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#f2f3f5; }}
  .poster {{
    position:relative; width:1080px; height:1440px; overflow:hidden;
    background:
      radial-gradient(900px 520px at 12% -6%, rgba(254,44,85,.30), transparent 62%),
      radial-gradient(760px 520px at 92% 8%, rgba(37,244,238,.20), transparent 60%),
      radial-gradient(900px 700px at 50% 112%, rgba(124,92,255,.28), transparent 62%),
      linear-gradient(165deg, #0a0f1f 0%, #101833 48%, #0a1020 100%);
    color:#eaf0ff;
    font-family:"Microsoft YaHei","PingFang SC","Hiragino Sans GB","Source Han Sans SC",sans-serif;
    padding:52px 60px 36px; display:flex; flex-direction:column;
  }}
  /* 网格纹理 + 顶部光带 */
  .poster::before {{
    content:""; position:absolute; inset:0;
    background-image:linear-gradient(rgba(255,255,255,.045) 1px, transparent 1px),
                     linear-gradient(90deg, rgba(255,255,255,.045) 1px, transparent 1px);
    background-size:60px 60px; mask-image:linear-gradient(180deg,#000 0%,transparent 78%);
    pointer-events:none;
  }}
  .poster > * {{ position:relative; z-index:1; }}

  .tag {{
    display:inline-flex; align-items:center; gap:10px; align-self:flex-start;
    padding:9px 20px; border-radius:999px; font-size:24px; letter-spacing:.5px;
    color:#ffd9e2; background:rgba(254,44,85,.14);
    border:1px solid rgba(254,44,85,.42);
  }}
  .tag i {{ width:10px; height:10px; border-radius:50%; background:#25F4EE;
            box-shadow:0 0 12px #25F4EE; }}

  h1 {{
    margin-top:24px; font-size:110px; line-height:1; font-weight:800;
    letter-spacing:2px;
    background:linear-gradient(96deg,#fff 8%,#25F4EE 42%,#FE2C55 78%);
    -webkit-background-clip:text; background-clip:text; color:transparent;
    filter:drop-shadow(0 10px 30px rgba(0,0,0,.45));
  }}
  .slogan {{ margin-top:14px; font-size:30px; color:#a9b8dd; letter-spacing:1px; }}

  .pain {{
    margin-top:28px; display:flex; align-items:center; gap:16px;
    font-size:37px; font-weight:700; color:#fff;
  }}
  .pain .hl {{ color:#ff8fa8; }}
  .bar {{ width:8px; height:38px; border-radius:4px;
          background:linear-gradient(180deg,#FE2C55,#7C5CFF); }}

  .grid {{ margin-top:26px; display:grid; grid-template-columns:repeat(3,1fr); gap:18px; }}
  .fx {{
    padding:20px 18px 18px; border-radius:20px;
    background:linear-gradient(180deg, rgba(255,255,255,.09), rgba(255,255,255,.035));
    border:1px solid rgba(255,255,255,.13);
    backdrop-filter:blur(2px);
  }}
  .fx-ico {{
    width:46px; height:46px; border-radius:14px; display:flex;
    align-items:center; justify-content:center; margin-bottom:11px;
    color:#0a1020; background:linear-gradient(140deg,#25F4EE,#7C5CFF);
  }}
  .fx-ico svg {{ width:27px; height:27px; }}
  .fx-t {{ font-size:27px; font-weight:700; color:#fff; }}
  .fx-d {{ margin-top:6px; font-size:20px; color:#93a4cc; line-height:1.4; }}

  .price {{
    margin-top:26px; padding:22px 30px; border-radius:26px;
    background:linear-gradient(115deg, rgba(254,44,85,.22), rgba(124,92,255,.20));
    border:1px solid rgba(255,255,255,.16);
    display:flex; align-items:center; gap:34px;
  }}
  .price-l {{ flex:1; }}
  .p-label {{ font-size:23px; color:#ffc9d6; letter-spacing:1px; }}
  .p-num {{ margin-top:4px; display:flex; align-items:baseline; gap:6px; }}
  .p-cur {{ font-size:38px; font-weight:700; color:#FFD75E; }}
  .p-big {{ font-size:96px; font-weight:800; line-height:.95; color:#fff;
            text-shadow:0 8px 26px rgba(0,0,0,.4); }}
  .p-per {{ font-size:28px; color:#ffd9e2; margin-left:6px; }}
  .p-note {{ margin-top:8px; font-size:23px; color:#cbd6f5; }}
  .hot {{
    padding:14px 20px; border-radius:18px; text-align:center;
    background:rgba(255,215,94,.14); border:1px solid rgba(255,215,94,.45);
  }}
  .hot-n {{ font-size:24px; color:#FFD75E; font-weight:700; }}
  .hot-t {{ margin-top:6px; font-size:21px; color:#ffe9a8; line-height:1.45; }}

  .subs {{ margin-top:14px; display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
  .sub {{
    padding:15px 22px; border-radius:18px; display:flex; align-items:baseline; gap:10px;
    background:rgba(255,255,255,.06); border:1px solid rgba(255,255,255,.12);
  }}
  .sub-n {{ font-size:25px; color:#c3cfe9; }}
  .sub-p {{ font-size:34px; font-weight:800; color:#fff; }}
  .sub-u {{ font-size:22px; color:#93a4cc; }}
  .sub-q {{ margin-left:auto; font-size:21px; color:#25F4EE; }}

  .bottom {{ margin-top:auto; padding-top:26px; display:flex; align-items:center; gap:30px; }}
  .qrbox {{
    width:232px; height:232px; padding:14px; border-radius:24px; background:#fff;
    box-shadow:0 18px 44px rgba(0,0,0,.45);
  }}
  .qrbox img {{ width:100%; height:100%; display:block; }}
  .cta {{ flex:1; }}
  .cta-1 {{ font-size:35px; font-weight:800; color:#fff; }}
  .cta-2 {{ margin-top:12px; font-size:28px; color:#FFD75E; font-weight:700; }}
  .cta-3 {{ margin-top:12px; font-size:23px; color:#93a4cc; line-height:1.55; }}
  .foot {{ margin-top:18px; font-size:22px; color:#7f90b8; text-align:center; }}
</style>
</head>
<body>
<div class="poster">
  <div class="tag"><i></i>{TOP_TAG}</div>
  <h1>{APP_NAME}</h1>
  <div class="slogan">{SLOGAN}</div>

  <div class="pain"><span class="bar"></span><span>{PAIN}</span></div>

  <div class="grid">
{cards}
  </div>

  <div class="price">
    <div class="price-l">
      <div class="p-label">首购价</div>
      <div class="p-num">
        <span class="p-cur">¥</span><span class="p-big">{PRICE_MAIN}</span>
        <span class="p-per">{PRICE_MAIN_PERIOD}</span>
      </div>
      <div class="p-note">{PRICE_MAIN_NOTE}</div>
    </div>
    <div class="hot">
      <div class="hot-n">一杯咖啡钱</div>
      <div class="hot-t">用 38 天<br>省下几十小时</div>
    </div>
  </div>

  <div class="subs">
{subs}
  </div>

  <div class="bottom">
    <div class="qrbox"><img src="{qr_uri}" alt="下载二维码"></div>
    <div class="cta">
      <div class="cta-1">{QR_TIP}</div>
      <div class="cta-2">{CONTACT}</div>
      <div class="cta-3">买完发机器码，秒出授权码<br>不懂装、不会用，全程远程带</div>
    </div>
  </div>
  <div class="foot">{FOOTER}</div>
</div>
</body>
</html>
"""


def find_edge() -> Path:
    for p in (Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
              Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")):
        if p.exists():
            return p
    raise SystemExit("没找到 Edge，无法截图（可手动用浏览器打开 HTML 后截图）")


def screenshot(html: Path, png: Path) -> bool:
    edge = find_edge()
    cmd = [
        str(edge), "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--no-first-run", "--disable-extensions",
        "--force-device-scale-factor=1", "--window-size=1080,1440",
        "--virtual-time-budget=4000",
        f"--user-data-dir={Path(__file__).parent / '.edge_poster_profile'}",
        f"--screenshot={png}", html.as_uri(),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if not png.exists() or png.stat().st_size < 10000:
        print("截图失败：", r.stdout[-800:], r.stderr[-800:])
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--html-only", action="store_true", help="只生成 HTML")
    ap.add_argument("--copy-desktop", action="store_true", help="复制 PNG 到桌面")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    html = build_html(make_qr_data_uri(DOWNLOAD_URL))
    HTML_PATH.write_text(html, encoding="utf-8")
    print(f"[1/2] HTML 已生成：{HTML_PATH}")

    if args.html_only:
        return 0

    if screenshot(HTML_PATH, PNG_PATH):
        size_kb = PNG_PATH.stat().st_size / 1024
        print(f"[2/2] PNG 已生成：{PNG_PATH}（{size_kb:.0f} KB）")
    else:
        return 1

    if args.copy_desktop:
        desk = Path.home() / "Desktop" / PNG_PATH.name
        shutil.copy2(PNG_PATH, desk)
        print(f"      已复制到桌面：{desk}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
