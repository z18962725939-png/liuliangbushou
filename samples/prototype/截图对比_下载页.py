"""生成「现有 Qt 视频下载页 vs 网页原型」并排评审图。

左侧：现有软件的真实离屏截图（tools/_shot_pages.py 视频下载）
右侧：本轮原型页由 Edge 无头浏览器渲染（与 WebView2 同为 Chromium 内核）
输出：samples/界面对比_视频下载_Qt与网页.png

用法：python 截图对比_下载页.py
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
TC_PY = Path(r"D:\tcvenv\Scripts\python.exe")
HTML = HERE / "流量捕手_视频下载原型.html"
WEB_SHOT = HERE / "_web_shot_download.png"
QT_SHOT = ROOT / "_tmp_shots" / "视频下载.png"
OUT = ROOT / "samples" / "界面对比_视频下载_Qt与网页.png"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "msyhbd.ttc" if bold else "msyh.ttc"
    path = Path(r"C:\Windows\Fonts") / name
    if not path.exists():
        return ImageFont.load_default()
    return ImageFont.truetype(str(path), size)


def ensure_qt_shot() -> None:
    env = os.environ.copy()
    env["SHOT_HEIGHT"] = "1120"
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    print("① 离屏渲染现有 Qt 视频下载页…")
    result = subprocess.run(
        [str(TC_PY), str(ROOT / "tools" / "_shot_pages.py"), "视频下载"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=180,
    )
    if not QT_SHOT.exists():
        raise SystemExit(f"Qt 截图失败：{(result.stderr or result.stdout)[-400:]}")
    print(f"   {QT_SHOT.name}  {QT_SHOT.stat().st_size // 1024} KB")


def ensure_web_shot() -> None:
    print("② 用 Chromium 无头渲染网页原型…")
    profile = Path(tempfile.gettempdir()) / "tc_edge_download_shot"
    cmd = [
        str(EDGE), "--headless=new", "--disable-gpu", "--no-sandbox",
        "--no-first-run", "--no-default-browser-check", "--hide-scrollbars",
        "--force-device-scale-factor=1", "--window-size=1180,1120",
        "--virtual-time-budget=3000", f"--user-data-dir={profile}",
        f"--screenshot={WEB_SHOT}", HTML.as_uri(),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if not WEB_SHOT.exists():
        raise SystemExit(f"网页截图失败：{(result.stderr or '')[-400:]}")
    print(f"   {WEB_SHOT.name}  {WEB_SHOT.stat().st_size // 1024} KB")


def fit(img: Image.Image, width: int, height: int) -> Image.Image:
    w, h = img.size
    scaled = img.resize((width, int(h * width / w)), Image.LANCZOS)
    if scaled.height > height:
        scaled = scaled.crop((0, 0, width, height))
    return scaled


def compose() -> None:
    qt = fit(Image.open(QT_SHOT).convert("RGB"), 760, 730)
    web = fit(Image.open(WEB_SHOT).convert("RGB"), 760, 730)

    gap, pad = 24, 24
    panel_w, panel_h = 760, 730
    width = pad * 2 + panel_w * 2 + gap
    height = 96 + 34 + panel_h + 18 + 130 + pad
    canvas = Image.new("RGB", (width, height), "#F3F4F6")
    draw = ImageDraw.Draw(canvas)

    draw.text((pad, 20), "视频下载页 · 两种渲染引擎", font=font(26, True), fill="#111827")
    draw.text((pad, 60), "流量捕手 v1.28.2 · 真桥接评审样张 · 2026-11-11",
              font=font(14), fill="#6B7280")

    left_x = pad
    right_x = pad + panel_w + gap
    label_y = 96
    draw.rectangle((left_x, label_y, left_x + panel_w, label_y + 34), fill="#E7E8EC")
    draw.rectangle((right_x, label_y, right_x + panel_w, label_y + 34), fill="#E6F1FB")
    draw.text((left_x + 12, label_y + 7), "现状：Qt Widgets + QSS（现有软件离屏实拍）",
              font=font(14, True), fill="#374151")
    draw.text((right_x + 12, label_y + 7), "提案：源码契约 + WebView2（本轮真桥接原型）",
              font=font(14, True), fill="#0052D9")

    img_y = label_y + 34
    canvas.paste(qt, (left_x, img_y))
    canvas.paste(web, (right_x, img_y))
    draw.rectangle((left_x, img_y, left_x + panel_w - 1, img_y + panel_h - 1),
                   outline="#D1D5DB", width=1)
    draw.rectangle((right_x, img_y, right_x + panel_w - 1, img_y + panel_h - 1),
                   outline="#0052D9", width=1)

    note_y = img_y + panel_h + 18
    notes = [
        "① 右侧表格多出「行内进度 / 状态 / 来源」三列：Web 渲染层白拿，Qt 表格要为它写 delegate 自绘。",
        "② 左侧是软件真实初始空态；右侧 5 行中前 3 行取自源码 _load_demo_rows，后 2 行标注「原型补充」，均为演示数据。",
        "③ 列名、平台中文名、下载内容、完成文案由 extract_contract.py 从 Qt 源码抽取；进度按软件真实口径推送。",
        "④ 真窗口自检 15/15 通过：契约、本机输出目录、进度回调、失败态、统计卡、令牌同源与换肤全部落回网页。",
    ]
    for i, line in enumerate(notes):
        draw.text((pad, note_y + i * 26), line, font=font(13), fill="#374151")
    draw.text((pad, note_y + 110),
              "右侧为无头浏览器静态渲染；真实窗口桥接数据见 prototype/_bridge_result_download.json",
              font=font(11), fill="#9CA3AF")

    canvas.save(OUT, "PNG")
    print(f"③ 已生成对比图：{OUT}  {canvas.width}x{canvas.height}")


def main() -> int:
    if not EDGE.exists():
        raise SystemExit("未找到 Edge/Chromium，无法渲染网页原型")
    ensure_qt_shot()
    ensure_web_shot()
    compose()
    return 0


if __name__ == "__main__":
    main()
