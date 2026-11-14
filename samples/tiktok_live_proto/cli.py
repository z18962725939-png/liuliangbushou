"""命令行入口：粘贴 @handle → 自动监控 → 开播自动录。

常用：
  # 只诊断一次（取房间号 + 查状态，不录制）
  python samples/tiktok_live_proto/cli.py --handle ofdwex9 --once

  # 用本地缓存页离线验证解析，跳过浏览器
  python samples/tiktok_live_proto/cli.py --handle ofdwex9 --html 页面.html --once

  # 完整监控：开播自动录，下播自动停（Ctrl+C 结束）
  python samples/tiktok_live_proto/cli.py --handle ofdwex9 --proxy http://127.0.0.1:7897

  # 只录 10 秒验证链路，录完自动收工
  python samples/tiktok_live_proto/cli.py --handle ofdwex9 --record-seconds 10 --timeout 180
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # samples/

from tiktok_live_proto.config import ProtoConfig, detect_proxy, default_ffmpeg  # noqa: E402
from tiktok_live_proto.monitor import ProtoMonitor                             # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="TikTok 直播监控原型（独立，不接发版链）")
    p.add_argument("--handle", default="", help="主播号或直播间链接，如 @ofdwex9")
    p.add_argument("--proxy", default=None, help="HTTP 代理；不填则自动探测")
    p.add_argument("--dir", default=None, help="录制保存目录")
    p.add_argument("--ffmpeg", default=None, help="ffmpeg.exe 路径；不填自动找工程自带")
    p.add_argument("--interval", type=float, default=None, help="下播时轮询秒数，默认 20")
    p.add_argument("--waf-wait", type=float, default=None, help="等浏览器校验的秒数，默认 8")
    p.add_argument("--once", action="store_true", help="只诊断一次，不录制")
    p.add_argument("--room-id", default="", help="跳过页面解析，直接指定 room_id（调试用）")
    p.add_argument("--html", default="", help="用本地 HTML 文件解析 roomId（离线验证用）")
    p.add_argument("--record-seconds", type=float, default=0.0,
                   help="每段只录这么多秒，录完一段自动停（0=一直录到下播）")
    p.add_argument("--timeout", type=float, default=0.0, help="最多运行多少秒，0=不限")
    p.add_argument("--headed", action="store_true", help="显示浏览器窗口（默认无头）")
    return p


def emit(kind: str, text: str, _data: dict) -> None:
    tag = {"info": "·", "warn": "!", "error": "×", "record": "●", "done": "■"}.get(kind, "·")
    print(f"[{time.strftime('%H:%M:%S')}] {tag} {text}", flush=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = ProtoConfig.load(
        handle=args.handle or None,
        proxy=args.proxy if args.proxy is not None else None,
        save_dir=args.dir,
        ffmpeg=args.ffmpeg,
        poll_interval=args.interval,
        waf_wait=args.waf_wait,
        record_duration=args.record_seconds or None,
    )
    if args.headed:
        cfg.headless = False
    if not cfg.handle and not args.room_id:
        print("请给 --handle（主播号或直播间链接），或用 --room-id 直接调试", file=sys.stderr)
        return 2
    if not cfg.proxy:
        print("提示：没探测到代理，TikTok 在国内直连会被重置；"
              "可用 --proxy http://127.0.0.1:7897 指定", flush=True)

    print(f"配置：handle=@{cfg.handle or '(未填)'} 代理={cfg.proxy or '(无)'}"
          f" ffmpeg={cfg.ffmpeg or '(未找到)'} 保存={cfg.save_dir}", flush=True)

    mon = ProtoMonitor(cfg, emit=emit, room_id_override=args.room_id,
                       html_file=args.html,
                       max_segments=1 if args.record_seconds > 0 else 0)
    if args.once:
        result = mon.diagnose()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    timer = None
    if args.timeout > 0:
        timer = threading.Timer(args.timeout, mon.request_stop)
        timer.daemon = True
        timer.start()
    try:
        mon.run_forever()
    except KeyboardInterrupt:
        emit("warn", "收到 Ctrl+C，正在收尾…", {})
        mon.request_stop()
    finally:
        if timer:
            timer.cancel()
        if mon.browser:
            mon.browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
