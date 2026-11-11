# -*- coding: utf-8 -*-
"""一键跑完运营功能全部回归测试。

用法：
    python tests/run_all.py

不需要真实 API Token —— 所有测试都用构造数据或假 client，
只有「接口自检」需要联网（那是界面里的按钮，不在本套件里）。
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent

TESTS = [
    ("平台适配层（三平台解析）", "_test_adapter.py"),
    ("项目管理 + 素材归档", "_test_workspace.py"),
    ("选题挖掘 + 评论分析", "_test_topic_mining.py"),
    ("复盘监控 + 异动判定", "_test_watchtower.py"),
    ("周报生成 + 导出", "_test_reporter.py"),
    ("内置浏览器登录（Cookie + WebSocket）", "_test_browser_login.py"),
    ("音视频转文本（显卡匹配 + 下载器 + 转写）", "_test_transcribe.py"),
    # _test_guide_and_stop / _test_transcribe_gui / _smoke_ops_gui 依赖早期
    # tkinter 界面 justoneapi_collector/gui.py，2026-11-01 随该界面一并归档到
    # _archive/legacy_tkinter/（现役界面是 gui_qt/，不再需要它们）。
    ("授权与商业化（Ed25519 + 试用期 + 门禁）", "_test_licensing.py"),
    ("自动更新（清单解析 + 下载校验）", "_test_updater.py"),
]

PY = sys.executable


def main() -> int:
    total_pass = 0
    failed: list[str] = []

    for title, fname in TESTS:
        path = HERE / fname
        if not path.exists():
            print(f"\n{'=' * 62}\n{title}  —— 文件缺失，跳过\n{'=' * 62}")
            failed.append(f"{title}（文件缺失）")
            continue
        print(f"\n{'=' * 62}\n{title}  ({fname})\n{'=' * 62}")
        try:
            r = subprocess.run([PY, str(path)], cwd=str(ROOT),
                               capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired:
            print("执行超时（600 秒）")
            failed.append(f"{title}（超时）")
            continue
        out = (r.stdout or "") + (r.stderr or "")
        tail = "\n".join(out.strip().splitlines()[-3:])
        print(tail)
        if r.returncode != 0:
            failed.append(title)
            print("前几行输出：")
            print("\n".join(out.splitlines()[:3]))
        else:
            for line in out.splitlines():
                if line.startswith("结果:") and "/" in line:
                    try:
                        n = int(line.split("：")[-1].split("通过")[0].strip()
                                .replace("结果: ", "").strip())
                        total_pass += n
                    except ValueError:
                        pass

    print(f"\n{'=' * 62}")
    print(f"全部通过项合计: {total_pass}")
    if failed:
        print(f"失败套件: {failed}")
        return 1
    print("全部测试套件通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
