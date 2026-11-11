"""生成「转录任务可视化」原型的无头浏览器截图（可复跑）。

用法：C:\\Python310\\python.exe 截图_转录页.py
产物：samples/转录任务可视化_效果预览.png
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HTML = HERE / "流量捕手_转录任务可视化原型.html"
OUT = HERE.parent / "转录任务可视化_效果预览.png"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def main() -> int:
    if not HTML.exists():
        raise SystemExit(f"找不到原型页：{HTML.name}")
    if not EDGE.exists():
        raise SystemExit("找不到 Edge，无法无头截图")
    profile = Path(tempfile.gettempdir()) / "tc_edge_transcribe_shot"
    cmd = [
        str(EDGE), "--headless=new", "--disable-gpu", "--no-sandbox",
        "--no-first-run", "--no-default-browser-check", "--hide-scrollbars",
        "--force-device-scale-factor=1", "--window-size=1360,1620",
        "--virtual-time-budget=4000", f"--user-data-dir={profile}",
        f"--screenshot={OUT}", HTML.as_uri(),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if not OUT.exists():
        raise SystemExit(f"截图失败：{(r.stderr or '')[-400:]}")
    print(f"已生成 {OUT.name}  {OUT.stat().st_size // 1024} KB")
    print("说明：静态预览展示的是 sync_contract.py 注入的生成时刻真实快照；")
    print("      真窗口由 Python 只读扫描覆盖，数据以 _bridge_result_transcribe.json 为准。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
