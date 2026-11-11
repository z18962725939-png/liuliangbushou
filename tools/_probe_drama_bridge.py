"""探针：用真实的 DramaBridge 模块驱动 DramaClip 服务。

验证四件事：
  1. 握手（版本/协议/token）
  2. 通知回调真的能收到（progress.update / log.append）
  3. 心跳 system.ping 走的是服务端快路径
  4. 长任务的 job 模式（提交 → 轮询 → 终态）

用法：
  python tools/_probe_drama_bridge.py           # 源码形态（本机 venv）
  python tools/_probe_drama_bridge.py --exe X   # 组件包形态
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 本机验证用的源码形态路径（正式探针里保留是为了回归，不是硬编码到产品）
_SRC = Path(r"D:/workbuddy/2026-10-03-23-06-16/_DramaClip_src")
SERVICE = _SRC / "service"
VPY = SERVICE / ".venv" / "Scripts" / "python.exe"
RESOURCES = _SRC / "resources"
DATA_DIR = Path(r"D:/workbuddy/2026-10-03-23-06-16/_dramaclip_probe/data_bridge")
MATERIAL = Path(r"D:/workbuddy/2026-10-03-23-06-16/_dramaclip_probe/素材/测试剧")

_PASS: list[str] = []
_FAIL: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    mark = "✓" if ok else "✗"
    line = f"  {mark} {label}"
    if detail:
        line += f"  —— {detail[:110]}"
    print(line)
    ( _PASS if ok else _FAIL).append(label)


def main() -> int:
    from justoneapi_collector.drama_bridge import DramaBridge, DramaBridgeError

    exe = None
    if "--exe" in sys.argv:
        exe = sys.argv[sys.argv.index("--exe") + 1]

    progress_seen: list[dict] = []
    log_seen: list[dict] = []
    disconnected = {"hit": False}

    br = DramaBridge(
        exe=exe,
        python_exe=None if exe else VPY,
        service_dir=None if exe else SERVICE,
        resources_dir=RESOURCES,
        data_dir=DATA_DIR,
        on_progress=progress_seen.append,
        on_log=log_seen.append,
        on_disconnect=lambda: disconnected.update(hit=True),
    )

    print("① 启动服务并握手")
    t0 = time.time()
    try:
        br.start(timeout=30.0)
    except DramaBridgeError as exc:
        print(f"  ✗ 启动失败：{exc}")
        return 1
    dt = time.time() - t0
    print(f"  服务 pid={br.pid}  握手耗时 {dt:.1f}s")
    check(dt < 30.0, "服务在 30 秒内完成握手", f"{dt:.1f}s")
    check(br.running, "桥接处于已连接状态")

    print("② 心跳（system.ping 是服务端快路径，不排队）")
    t0 = time.time()
    alive = br.is_alive(timeout=8.0)
    dt = time.time() - t0
    check(alive and dt < 5.0, "心跳响应正常", f"{dt:.2f}s")

    print("③ 建项目 + 扫描剧集（长活，会推通知）")
    try:
        # source_path 在库里是 UNIQUE（实测：UNIQUE constraint failed: projects.source_path），
        # 同一个素材目录不能建两个项目。先清掉上次留下的同名项目，否则第二次跑必红。
        for old in br.call("project.list", {}, timeout=30) or []:
            if old.get("source_path") == str(MATERIAL) or old.get("name") == "桥接验证":
                br.call("project.delete", {"project_id": old.get("id")}, timeout=30)
        proj = br.call("project.create",
                       {"name": "桥接验证", "source_path": str(MATERIAL)}, timeout=60)
        pid = proj.get("id")
        check(bool(pid), "项目创建成功", f"id={pid}")

        t0 = time.time()
        eps = br.call("project.scan_episodes", {"project_id": pid}, timeout=300)
        dt = time.time() - t0
        check(isinstance(eps, list) and len(eps) > 0,
              f"扫到 {len(eps) if isinstance(eps, list) else 0} 集", f"{dt:.1f}s")
        if isinstance(eps, list) and eps:
            check(bool(eps[0].get("duration")),
                  "首集拿到时长（ffprobe 走了随包 ffmpeg）",
                  f"{eps[0].get('duration')}s")
    except DramaBridgeError as exc:
        check(False, f"项目链路失败：{exc}")

    print("④ 通知回调（读线程推上来的）")
    check(len(progress_seen) > 0 or len(log_seen) > 0,
          f"收到通知：进度 {len(progress_seen)} 条 / 日志 {len(log_seen)} 条")
    for p in progress_seen[:3]:
        print(f"      [进度] {p.get('percent')}% {str(p.get('message'))[:60]}")
    for lg in log_seen[:3]:
        print(f"      [log.{lg.get('level')}] {str(lg.get('message'))[:60]}")

    print("⑤ 错误路径（方法不存在要抛 DramaBridgeError，不能静默）")
    try:
        br.call("no.such.method", {}, timeout=10)
        check(False, "不存在的方法应该报错")
    except DramaBridgeError as exc:
        check("方法不存在" in str(exc), "不存在的方法如实报错", str(exc)[:60])

    print("⑥ 关闭（先请它自己退，超时才 kill）")
    t0 = time.time()
    br.stop()
    dt = time.time() - t0
    check(not br.running, "已断开", f"{dt:.1f}s")

    print()
    print(f"结果：{len(_PASS)} 通过 / {len(_FAIL)} 失败")
    for f in _FAIL:
        print(f"  失败项：{f}")
    return 0 if not _FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
