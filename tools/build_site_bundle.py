# -*- coding: utf-8 -*-
"""
把官网整站（index.html + 4 张截图）打成「单文件自包含 HTML」。

用途：给腾讯云 SCF 云函数返回用。云函数只返回这一个 HTML 字符串即可，
     不需要路由、不需要读取二进制、不依赖 COS / 任何外部资源。

用法：
    python tools/build_site_bundle.py

输出：
    deploy/scf_site/site.html      自包含 HTML（图片 base64 内嵌）
"""
import base64
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SD = os.path.join(ROOT, "installer_output", "standalone")
OUT_DIR = os.path.join(ROOT, "deploy", "scf_site")
OUT_HTML = os.path.join(OUT_DIR, "site.html")

# 已经是 data: 开头的（比如收款码）不要重复处理
DATA_URI_RE = re.compile(r"^data:", re.I)


def _shrink_to_webp(raw: bytes, quality: int = 82) -> bytes | None:
    """把 PNG/JPG 转成 WebP，通常能省 60%~75% 体积。
    转不了（没装 Pillow / 图损坏）就返回 None，调用方回退用原图。"""
    try:
        import io

        from PIL import Image

        im = Image.open(io.BytesIO(raw))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
        else:
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=quality, method=4)
        out = buf.getvalue()
        return out if len(out) < len(raw) else None
    except Exception:
        return None


def to_data_uri(path: str) -> str:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    mime = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "gif": "image/gif",
        "svg": "image/svg+xml",
    }.get(ext, "application/octet-stream")
    with open(path, "rb") as f:
        raw = f.read()

    # 位图先压成 WebP，能省一大半流量
    if ext in ("png", "jpg", "jpeg"):
        webp = _shrink_to_webp(raw)
        if webp:
            raw, mime = webp, "image/webp"

    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))


def main() -> int:
    idx = os.path.join(SD, "index.html")
    if not os.path.isfile(idx):
        print("[错误] 找不到官网页面，请先跑 python tools/build_site.py：%s" % idx)
        return 1

    html = open(idx, encoding="utf-8").read()

    # 收集 src= 引用的本地图片
    imgs = re.findall(r"""(<img[^>]+src=["'])([^"']+)(["'])""", html)
    local = [(a, u, c) for a, u, c in imgs if not DATA_URI_RE.match(u) and not u.startswith(("http://", "https://", "//"))]

    # 按「唯一文件名」处理，避免同一张图被引用多次时重复内嵌（白白翻倍体积）
    seen, missing = {}, []
    for tag, src, quote in local:
        if src in seen:
            continue
        fp = os.path.join(SD, src)
        if not os.path.isfile(fp):
            missing.append(src)
            continue
        seen[src] = to_data_uri(fp)

    for src, uri in seen.items():
        before = os.path.getsize(os.path.join(SD, src))
        n_all = html.count(src)
        # 同一 src 的所有出现（含 CSS url()）一次性替换掉
        html = html.replace('"%s"' % src, '"%s"' % uri)
        html = html.replace("'%s'" % src, "'%s'" % uri)
        html = html.replace("url(%s)" % src, "url(%s)" % uri)
        print("  内嵌 %-28s %7.1f KB -> %7.1f KB (base64)  替换 %d 处" % (
            src, before / 1024, len(uri) / 1024, n_all))

    # 校验：不允许再有任何指向本地的相对资源
    left = [s for s in re.findall(r"""<img[^>]+src=["']([^"']+)["']""", html) if not DATA_URI_RE.match(s)]
    if left:
        print("[警告] 仍有非内嵌图片引用：%s" % left)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    size = os.path.getsize(OUT_HTML)
    print()
    print("[完成] %s" % OUT_HTML)
    print("       共内嵌 %d 张图，最终 %.1f KB（原页面 %.1f KB）" % (
        len(seen), size / 1024, os.path.getsize(idx) / 1024))
    if missing:
        print("[警告] 缺失文件：%s" % missing)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
