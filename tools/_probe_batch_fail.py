# -*- coding: utf-8 -*-
"""复现客户「182 条只成功 14 条」的现场，逐条打印真实原因。

核心怀疑（先写在前面，跑完对照）：
    解析链路的浏览器渲染通道有个 MAX_FAILS=3 的熔断 —— 连续 3 条读不到
    就整条通道停用，本次会话再也不会重试。而界面收尾只用一句
    「页面里没读到作品数据…登录态是有的」兜底，看不出到底是被熔断的、
    还是链接真的坏了。批量上百条时，只要中段撞上 3 条坏链/超时，
    后面的一整片好链接都会被算作失败。

判断标准：
    · 每条单独都能成功 → 问题是熔断（同一条链接，前面好后面坏）
    · 有些链接单独也失败 → 那些是真的坏链
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from justoneapi_collector.video_downloader import VideoDownloader  # noqa: E402

# 客户截图里那 5 条（原样抄下来，别改）
LINKS = [
    "https://v.douyin.com/jgwBaaWYcYk/",
    "https://v.douyin.com/NQ1mDTBURtk/",
    "https://v.douyin.com/hj9LTcDoINI/",
    "https://v.douyin.com/AfCG6QXlv_o/",
    "https://v.douyin.com/ycNsrExhK0/",
]


def load_cookie() -> str:
    import json
    p = Path.home() / "AppData" / "Roaming" / "TrafficCatcher" / "config.json"
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
        return str(cfg.get("douyin_cookie") or "")
    except Exception as exc:                        # noqa: BLE001
        print(f"[警告] 读不到配置里的 Cookie：{exc}")
        return ""


def main() -> int:
    cookie = load_cookie()
    print(f"Cookie 长度：{len(cookie)}（0 = 没有登录态）\n")

    dl = VideoDownloader(client=None, cookie=cookie, save_dir=Path.home() / "Desktop")
    for i, link in enumerate(LINKS, 1):
        t0 = time.time()
        info = None
        err = ""
        try:
            # 批量导入用的就是这个入口：返回 (info, 原因)
            info, err = dl.resolve_share_link_with_reason(link)
        except Exception as exc:                    # noqa: BLE001
            err = f"异常 {type(exc).__name__}: {exc}"
        cost = time.time() - t0
        res = dl._page_resolver
        fails = getattr(res, "_fails", "-") if res else "-"
        disabled = getattr(res, "disabled", "-") if res else "-"
        print(f"[{i}] {link}")
        print(f"    结果：{'成功 ' + (info.no_watermark_url[:60] if info else '') if info else '失败'}"
              f"  耗时 {cost:.1f}s")
        if not info:
            print(f"    原因：{err or getattr(dl, '_last_detail_reason', '') or '(空)'}")
        print(f"    渲染通道：连续失败={fails}  已停用={disabled}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
