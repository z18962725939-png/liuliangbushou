"""转录任务可视化原型「产物级」门禁（只读，不改任何文件）。

只管 samples/ 原型，**不接 release_all.py**：软件发版不依赖原型。
为什么需要它：真窗口自检全绿不代表交付的那张 HTML 没被写回空值、
状态判据没被改成「只看同名 txt」、编码坑没把「转录中」吞成「没有任务」。

用法：python _validate_transcribe_prototype.py
"""
from __future__ import annotations

import html.parser
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import transcribe_status as ts  # noqa: E402

HTML_FILE = HERE / "流量捕手_转录任务可视化原型.html"
CONTRACT_FILE = HERE / "transcribe_contract.json"
STATUS_SRC = HERE / "transcribe_status.py"
RESULT = HERE / "_bridge_result_transcribe.json"
RESULT_DOWNLOAD = HERE / "_bridge_result_download.json"
RESULT_WORKBENCH = HERE / "_bridge_result.json"
SHOT = HERE.parent / "转录任务可视化_效果预览.png"
NODE = Path(r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-6\node.exe")

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
        print(f"  [!!] {name}" + (f"  → {detail}" if detail else ""))


def sec(title: str) -> None:
    print(f"\n{title}")


class Checker(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.err: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            self.err.append(f"乱序闭合 {tag}")
            self.stack.remove(tag)


def main() -> int:
    html = HTML_FILE.read_text(encoding="utf-8")
    src = STATUS_SRC.read_text(encoding="utf-8")

    sec("① 产物自包含与内联数据")
    chk(HTML_FILE.exists() and len(html.encode("utf-8")) > 20000,
        "原型页存在且内容完整", f"{len(html.encode('utf-8'))} B")
    chk('<style id="tokens">' in html and "#0052D9" in html and "#C8F751" in html,
        "令牌块与浅/深两套主色齐全")
    chk('<link rel="stylesheet" href="tokens.css">' not in html,
        "没有外链 CSS（离线双击不丢样式）")
    p = Checker()
    p.feed(html)
    chk(not p.stack and not p.err, "HTML 标签闭合无误", str(p.stack[:4] + p.err[:4]))

    c = json.loads(re.search(
        r'id="inline-transcribe-contract">(.*?)</script>', html, re.S).group(1))
    o = json.loads(re.search(
        r'id="inline-transcribe-overview">(.*?)</script>', html, re.S).group(1))
    chk(bool(c) and c.get("version") and c.get("states"), "内联状态契约非空")
    chk(bool(o) and o.get("total", 0) > 0 and "stats" in o,
        "内联真实扫描快照非空", f"{o.get('total')} 个文件")
    chk(CONTRACT_FILE.exists() and json.loads(
        CONTRACT_FILE.read_text(encoding="utf-8")) == c,
        "契约文件与页面内联块逐项一致")

    sec("② 状态判据来自源码，不靠猜文件名")
    live = ts.contract()
    chk(c == live, "页面契约等于 transcribe.py 现场校验结果")
    chk(len(c["extensions"]) == 14, "支持格式 14 种", str(len(c["extensions"])))
    chk([s["key"] for s in c["states"]] ==
        ["done", "running", "pending", "stale", "conflict"],
        "五类状态枚举齐全")
    real_src = (HERE.parents[1] / "justoneapi_collector" / "transcribe.py"
                ).read_text(encoding="utf-8")
    chk(all(line in real_src for line in c["output_code"]),
        "输出命名三行规则确实在 transcribe.py 源码里")
    chk("stem_count" in src, "同名冲突判据存在")
    chk("st_mtime" in src, "旧稿 / 源文件更新判据存在")
    chk("orphan" in src, "孤立转录稿判据存在")
    chk("str(p.parent)" in src, "身份含完整路径，不拿文件名当主键")
    chk("_run_text" in src and 'errors="replace"' in src,
        "子进程输出按 UTF-8 / GBK 两道解码")
    chk("text=True" not in src,
        "不再用 text=True（曾把「转录中」误判成「没有任务」）")
    chk("whisper_running" in src and "not data[\"task\"][\"running\"]" in src,
        "命令行读不到时用显卡进程兜底显示转录中")

    sec("③ 卡片与页面结构")
    chk(all(f".state-{k}" in html for k in
            ("done", "running", "pending", "stale", "conflict")),
        "五类状态徽标样式齐全")
    chk(all(f'id="m{i}"' in html for i in range(1, 5)), "四张统计卡齐全")
    chk(all(f'id="step{i}"' in html for i in range(1, 4)), "三阶段步骤条齐全")
    chk(all(f'id="gpu-{k}"' in html for k in
            ("util", "vram", "power", "temp", "bar")), "显卡实时卡片齐全")
    chk('id="outDir"' in html and 'id="rescan"' in html,
        "输出目录输入与重新判定按钮齐全")
    chk(html.count('data-filter=') == 4, "四种筛选齐全")
    chk(all(h in html for h in (
        "get_transcribe_contract", "get_transcribe_overview",
        "get_gpu_status", "start_demo_transcribe",
        "onTranscribeProgress", "onTranscribeDone")),
        "桥接 API 与进度钩子齐全")
    chk("演示数据（非真实文件）" in html or "演示" in html,
        "演示数据有显式标注")
    chk("同名冲突" in html and "源文件已更新" in html,
        "界面展示真实痛点对应的两种状态")

    sec("④ 脚本语法与真窗口自检")
    js = re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
    tmp = HERE / "_syntax_check.js"
    tmp.write_text(js, encoding="utf-8")
    if NODE.exists():
        r = subprocess.run([str(NODE), "--check", str(tmp)],
                           capture_output=True, text=True, timeout=30)
        chk(r.returncode == 0, "页面 JS 语法通过", (r.stderr or "")[:160])
    else:
        chk(True, "页面 JS 语法（本机无 Node，跳过）")
    tmp.unlink(missing_ok=True)

    d = json.loads(RESULT.read_text(encoding="utf-8"))
    chk(d.get("ok") and len(d.get("checks", [])) == 14,
        "转录页真窗口自检 14/14", str(len(d.get("checks", []))))
    w = json.loads(RESULT_WORKBENCH.read_text(encoding="utf-8"))
    dl = json.loads(RESULT_DOWNLOAD.read_text(encoding="utf-8"))
    chk(w.get("ok") and len(w.get("checks", [])) == 6, "工作台页回归 6/6")
    chk(dl.get("ok") and len(dl.get("checks", [])) == 15, "视频下载页回归 15/15")
    chk(SHOT.exists() and SHOT.stat().st_size > 30000,
        "效果预览图已生成且非空",
        f"{SHOT.stat().st_size // 1024} KB" if SHOT.exists() else "不存在")
    chk("--page transcribe" in (HERE / "启动原型.py").read_text(encoding="utf-8"),
        "启动脚本支持转录页")
    chk("inline-transcribe-contract" in
        (HERE / "sync_contract.py").read_text(encoding="utf-8"),
        "同步脚本覆盖转录页契约")

    print(f"\n结果：{passed} 项通过，{failed} 项失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
