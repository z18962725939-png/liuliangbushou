# -*- coding: utf-8 -*-
"""生成官网 / 下载页的公网访问二维码（用于发朋友圈、微信群）。

用法：
    python tools/gen_site_qr.py                       # 用脚本内默认网址
    python tools/gen_site_qr.py https://你的域名.com/  # 指定网址（换域名时用这个）
    python tools/gen_site_qr.py <网址> -o 输出路径.png
输出：
    官网素材/流量捕手官网二维码.png
"""
import os
import sys

import qrcode
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer
from qrcode.image.styles.colormasks import SolidFillColorMask
from PIL import Image, ImageDraw, ImageFont

# ⚠️ 不要用 COS 域名（`xxx.cos-website.ap-guangzhou.myqcloud.com`）做官网网址：
# 腾讯云对 COS 默认域名的 GET 一律回 `Content-Disposition: attachment`，
# 浏览器不是渲染网页，而是下载一个 index.html 文件。
# 这个地方如果换了托管，务必同步改 `tools/check_public_site.py` 的 CANDIDATES。
#
# ★ 2026-10-12 改为自有域名（原先是 WorkBuddy 旧域名）：
#   旧域名 `trafficcatcher-download.app.workbuddy.host` 对**微信 UA 返回 403**，
#   而二维码绝大多数是发朋友圈 / 微信群 → 扫码的人就在微信里打开 → 直接打不开。
#   自有域名 `流量捕手.cn` 微信 UA 实测 200，且证书已签发（TrustAsia，TLS1.3）。
#   注：COS 静态网站域名微信 UA 也是 200（且更快），但对外发太丑，只作兜底。
SITE_URL = "https://xn--kpuzw91vr71b.cn/"
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "官网素材")
OUT_PATH = os.path.join(OUT_DIR, "流量捕手官网二维码.png")

TITLE = "流量捕手 · 官网"
SUB = "电脑扫码直接打开 · 微信内请点右上「···」用浏览器打开"


def parse_args(argv):
    """解析命令行：第一个位置参数是网址，-o 指定输出路径。"""
    url, out = None, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "-o" and i + 1 < len(argv):
            out = argv[i + 1]
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        if url is None:
            url = a
        i += 1
    return url, out


def main() -> int:
    global SITE_URL, OUT_PATH
    argv = sys.argv[1:]
    url, out = parse_args(argv)
    if url:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        SITE_URL = url if url.endswith("/") else url + "/"
    if out:
        OUT_PATH = out
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_H,
                       box_size=12, border=2)
    qr.add_data(SITE_URL)
    qr.make(fit=True)
    img = qr.make_image(image_factory=StyledPilImage,
                        module_drawer=RoundedModuleDrawer(),
                        color_mask=SolidFillColorMask(back_color=(255, 255, 255),
                                                      front_color=(79, 70, 229)))
    img = img.convert("RGB")
    qw, qh = img.size

    W = qw + 120
    H = qh + 200
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    canvas.paste(img, (60, 96))

    d = ImageDraw.Draw(canvas)
    font_path = r"C:\Windows\Fonts\msyh.ttc"
    f_title = ImageFont.truetype(font_path, 40)
    f_sub = ImageFont.truetype(font_path, 22)
    f_url = ImageFont.truetype(font_path, 16)

    def center(text, y, font, fill):
        w = d.textbbox((0, 0), text, font=font)[2]
        d.text(((W - w) / 2, y), text, font=font, fill=fill)

    # 副标题：托管域名（workbuddy.host）微信内会被拦 403，需提示；自有域名则不用
    host = SITE_URL.split("//", 1)[-1].rstrip("/")
    if host.endswith(".workbuddy.host"):
        sub = "电脑扫码直接打开 · 微信内请点右上「···」用浏览器打开"
    else:
        sub = "手机扫码直接打开 · 微信 / 朋友圈可直接访问"
    center(TITLE, 32, f_title, (17, 17, 26))
    center(sub, qh + 108, f_sub, (90, 90, 110))
    center(host, qh + 142, f_url, (150, 150, 168))

    canvas.save(OUT_PATH)
    print("已生成：", OUT_PATH)
    print("公网地址：", SITE_URL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
