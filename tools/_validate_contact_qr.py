# -*- coding: utf-8 -*-
"""门禁：作者个人微信二维码（对外联系）与收款码严格分区，纯标准库。

钉死以下事实，防止以后改崩：
  · 唯一图片源 docs/contact/wechat-personal-qr.jpg 存在且未被 gitignore；
  · README / 内测招募 用相对路径引用它，旧的过期群二维码 URL 不再作为主入口；
  · 官网首页 & 子页构建脚本引用的是「个人码」assets 路径，不是收款码文件；
  · 付款弹窗「收款码」仍是 tools/pay_qr 那张（客户付款靠它），绝不能被个人码替换；
  · 弹窗里两个码有带标题的分区（① 付款码 / ② 加好友码），防止客户扫错付不了款；
  · 个人码走相对路径不内联 base64（首页有 <400KB 体积门禁）；
  · DESIGN.md 有明确的排版契约，微信号只作兜底。

跑法：python tools/_validate_contact_qr.py
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK ] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def exists(rel: str) -> bool:
    return os.path.isfile(os.path.join(ROOT, rel))


QR = "docs/contact/wechat-personal-qr.jpg"
ASSET = "assets/wechat-personal-qr.jpg"

print("== 1. 图片资源 ==")
check("个人微信二维码图片存在", exists(QR))
check("体积合理（20KB~600KB，不是空文件/占位图）",
      exists(QR) and 20_000 <= os.path.getsize(os.path.join(ROOT, QR)) <= 600_000)
# git 可跟踪：不能被 .gitignore 挡掉，否则 push 后 GitHub 上是破图
ignored = subprocess.run(["git", "check-ignore", "-q", QR], cwd=ROOT,
                         capture_output=True).returncode == 0
check("图片未被 .gitignore 忽略", not ignored,
      "会被忽略，push 后 README 二维码破图")

print("\n== 2. GitHub 仓库说明页 ==")
readme = read("README.md")
check("README 展示个人微信二维码", QR in readme)
check("README 用相对路径而非外链", f'<img src="{QR}"' in readme)
check("README 保留微信号兜底", "zsx807108536" in readme)
beta = read("内测招募.md")
check("内测招募改用个人微信码", QR in beta)
check("内测招募已弃用过期群码作为主图", "beta-qrcode-20261115.jpg" not in beta)
check("内测招募去掉过期有效期警告", "2026 年 11 月 22 日前" not in beta)

print("\n== 3. 官网：两类二维码严格分区 ==")
site = read("tools/build_site.py")
sub = read("tools/build_subpages.py")
check("首页引用个人码 assets 路径", f'PERSONAL_QR_ASSET = "{ASSET}"' in site)
check("首页构建时复制个人码到 standalone", "STANDALONE / PERSONAL_QR_ASSET" in site)
check("首页弹窗有个人码 img 变量", "PERSONAL_QR_IMG=personal_qr_img" in site)
check("个人码不内联 base64", "b64encode" not in site.split("personal_qr_img =")[1][:200]
      if "personal_qr_img =" in site else False)
check("收款码仍是独立 base64 内嵌", "qr_img = f'<img src=\"data:image/" in site)
check("收款码仍指向 tools/pay_qr 配置", 'pay.get("wechat_qr")' in site)
_pay_cfg = read("tools/payment_config.json")
check("个人码与收款码不是同一个文件",
      '"wechat_qr": "tools/pay_qr/wechat.png"' in _pay_cfg and
      '"wechat_qr": "docs/contact/' not in _pay_cfg)
check("首页弹窗有分区标题 ① 付款码", "① 购买授权 · 付款码" in site)
check("首页弹窗有分区标题 ② 加好友码", "② 咨询 / 售后 · 加作者个人微信" in site)
check("弹窗有『别扫错』提示", "别扫错" in site)
check("首页页脚/下载区有扫码入口", "扫码加作者微信" in site)

print("\n== 4. 官网子页 ==")
check("子页引用同一份个人码", f'PERSONAL_QR_ASSET = "{ASSET}"' in sub)
check("子页页脚展示个人码",
      'class="f-qr"' in sub and "PERSONAL_QR_ASSET" in sub and ASSET in sub)
check("子页保留微信号兜底", "WECHAT_ID" in sub)
css = read("tools/site_theme.css")
check("CSS 有 .personal-qr 样式", ".personal-qr{" in css)
check("CSS 有 .qr-label 分区标题样式", ".qr-label{" in css)
check("CSS 有子页 .f-qr 样式", ".f-qr{" in css)
check("弹窗可滚动（两个码撑出屏幕也能看全）", "max-height:88vh" in css)

print("\n== 5. 排版布局契约 ==")
design = read("DESIGN.md")
check("DESIGN.md 写明二维码排版规范", "联系方式二维码排版" in design)
check("DESIGN.md 写明两类码绝不互相替换", "绝不互相替换" in design)
check("DESIGN.md 写明不内联 base64", "不内联 base64" in design)
check("DESIGN.md 未改动功能名『微信聊天整理』", "微信聊天整理" in design)

print("\n== 6. 产物与体积 ==")
home = os.path.join(ROOT, "installer_output", "standalone", "index.html")
if os.path.isfile(home):
    check("首页产物 < 400KB", os.path.getsize(home) < 400_000,
          f"{os.path.getsize(home)} 字节")
    with open(home, encoding="utf-8") as f:
        html = f.read()
    check("产物含个人码引用", ASSET in html)
    check("产物含收款码（付款没被弄丢）", "data:image/png;base64," in html or
          "data:image/jpeg;base64," in html)
    check("产物含两个分区标题", "① 购买授权 · 付款码" in html and
          "② 咨询 / 售后 · 加作者个人微信" in html)
    check("产物二维码资源已落地",
          os.path.isfile(os.path.join(ROOT, "installer_output", "standalone", ASSET)))
else:
    check("首页产物存在", False, "先跑 build_site.py --no-upload")

print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
