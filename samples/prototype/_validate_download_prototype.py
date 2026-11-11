"""视频下载页原型「产物级」门禁（只读，不改任何文件）。

为什么需要它：源码编译通过、真窗口自检全绿，都不保证**交付出去的那张
HTML** 是自包含的、内联契约没被写回空值、对比图没被覆盖成半成品。
上一轮官网破图就是这么漏掉的，所以对最终产物做断言。

本门禁只管 samples/ 原型，**不接 release_all.py**：软件发版不依赖原型。
用法：python _validate_download_prototype.py
"""
from __future__ import annotations

import html.parser
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import extract_contract  # noqa: E402

HTML_FILE = HERE / "流量捕手_视频下载原型.html"
CONTRACT_FILE = HERE / "download_contract.json"
RESULT_DOWNLOAD = HERE / "_bridge_result_download.json"
RESULT_WORKBENCH = HERE / "_bridge_result.json"
COMPARE = ROOT / "samples" / "界面对比_视频下载_Qt与网页.png"
NODE = Path(r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-6\node.exe")

# HTML 空元素：<col> 常被漏掉，漏掉会把后续所有闭合标签都误判成「乱序」
VOID = {"meta", "link", "br", "hr", "img", "input", "use", "path",
        "circle", "rect", "polyline", "line", "source", "col", "area", "base"}

passed = 0
failed = 0


def chk(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [OK] {name}")
    else:
        failed += 1
        print(f"  [!!] {name}" + (f" —— {detail}" if detail else ""))


class TagChecker(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            self.errors.append(f"乱序闭合 {tag}")
            self.stack.remove(tag)


def main() -> int:
    print("① 交付页自包含与结构")
    html = HTML_FILE.read_text(encoding="utf-8")
    chk('<style id="tokens">' in html, "令牌样式块存在（离线双击不丢样式）")
    chk("#0052D9" in html and "#C8F751" in html, "浅/深两套主色都已内联")
    chk('<link rel="stylesheet" href="tokens.css">' not in html, "无外链 CSS")
    chk("id=\"inline-contract\">{}" not in html, "内联契约不是空对象")
    chk("id=\"inline-rows\">[]" not in html, "内联行数据不是空数组")

    p = TagChecker()
    p.feed(html)
    chk(not p.stack and not p.errors, "HTML 标签正确闭合",
        f"未闭合 {p.stack[:5]} 错误 {p.errors[:3]}")

    symbols = set(re.findall(r'<symbol id="([^"]+)"', html))
    uses = set(re.findall(r'<use href="#([^"]+)"', html))
    chk(bool(symbols) and not (uses - symbols), "图标引用齐全",
        f"缺失 {sorted(uses - symbols)}")

    hooks = ("onBatchStart", "onDownloadProgress", "onBatchDone",
             "get_download_contract", "get_output_stats", "get_download_rows",
             "start_batch_download", "pause_batch_download", "resume_batch_download")
    chk(all(h in html for h in hooks), "桥接钩子与 API 调用齐全")

    print("② 数据契约与源码一致")
    saved = json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))
    fresh = extract_contract.extract()
    chk(saved["columns"] == fresh["columns"], "表格列名与 Qt 源码一致", str(saved["columns"]))
    chk(saved["platforms"] == fresh["platforms"], "平台中文名与 PLATFORM_LABELS 一致")
    chk(saved["media_types"] == fresh["media_types"], "下载内容类型一致")
    chk(saved["progress"] == fresh["progress"], "进度口径（公式/节流/上限）一致")
    chk("{ok}" in saved["done_text"] and "{fail}" in saved["done_text"],
        "完成文案是批次汇总那条，不是「→ 目录」那条")

    inline = json.loads(re.search(
        r'id="inline-contract">(.*?)</script>', html, re.S).group(1))
    rows = json.loads(re.search(
        r'id="inline-rows">(.*?)</script>', html, re.S).group(1))
    chk(inline == saved, "页面内联契约 == download_contract.json（离线也同源）")
    chk(len(rows) >= len(saved["demo_rows"]), "演示行完整", f"仅 {len(rows)} 行")
    chk(all(any("\u4e00" <= c <= "\u9fff" for c in r["platform_label"])
            for r in rows), "界面数据只含平台中文名，无英文内部键")

    print("③ 真窗口自检结果")
    for path, label, least in ((RESULT_DOWNLOAD, "视频下载页", 15),
                               (RESULT_WORKBENCH, "工作台页", 6)):
        if not path.exists():
            chk(False, f"{label} 自检结果存在", str(path))
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        bad = [name for name, ok in data.get("checks", []) if not ok]
        chk(data.get("ok") is True and not bad and len(data.get("checks", [])) >= least,
            f"{label} 自检 {len(data.get('checks', []))} 项全绿", str(bad))

    print("④ 评审图与脚本语法")
    chk(COMPARE.exists() and COMPARE.stat().st_size > 100_000,
        "并排对比图已生成且非空", str(COMPARE))
    if COMPARE.exists():
        try:
            from PIL import Image
            with Image.open(COMPARE) as im:
                chk(im.width >= 1500 and im.height >= 900, "对比图尺寸可评审",
                    f"{im.width}x{im.height}")
        except ImportError:
            print("  （未装 Pillow，跳过尺寸检查）")

    js = re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
    if NODE.exists():
        tmp = HERE / "_syntax_check.js"
        tmp.write_text(js, encoding="utf-8")
        try:
            r = subprocess.run([str(NODE), "--check", str(tmp)],
                               capture_output=True, text=True, timeout=30)
            chk(r.returncode == 0, "页面 JS 语法通过", (r.stderr or "")[:160])
        finally:
            tmp.unlink(missing_ok=True)
    else:
        print("  （未找到 Node，跳过 JS 语法检查）")

    print(f"\n结果：{passed} 项通过，{failed} 项失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
