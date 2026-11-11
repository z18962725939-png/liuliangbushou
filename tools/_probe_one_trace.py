# -*- coding: utf-8 -*-
"""单条链接全程追踪：短链展开 → 浏览器渲染，每一步的输出都打出来。

用来回答一个具体问题：这条链接到底是**真的坏了**，还是
**被前面几条的失败连累、根本没被真正尝试**？
区分办法：如果浏览器真的打开了页面，页面上的文字会告诉我们真相
（作品不存在 / 需要验证 / 正常标题）。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from justoneapi_collector.video_downloader import VideoDownloader  # noqa: E402

LINK = sys.argv[1] if len(sys.argv) > 1 else "https://v.douyin.com/jgwBaaWYcYk/"


def main() -> int:
    dl = VideoDownloader(client=None, save_dir=Path.home() / "Desktop")

    print(f"目标链接：{LINK}\n--- 1) 本地跟随短链 ---")
    final = ""
    try:
        final = dl.resolve_short_link(LINK)
    except Exception as exc:                        # noqa: BLE001
        print(f"   跟随异常：{type(exc).__name__}: {exc}")
    print(f"   落地页：{final or '(空 = 跟随失败)'}")
    info = VideoDownloader.parse_share_url(final or LINK)
    print(f"   判定类型：{info.get('kind')}  id={info.get('aweme_id') or info.get('sec_uid') or '-'}")

    url = final or LINK
    if "douyin.com/video/" not in url and "douyin.com/note/" not in url:
        print("\n链接不是作品页，不再往下渲染。")
        return 0

    print("\n--- 2) 真浏览器渲染 ---")
    from justoneapi_collector.douyin_page_resolver import DouyinPageResolver
    r = DouyinPageResolver(dl.browser_profile_dir)
    data, why = (None, "")
    try:
        data, why = r.resolve(url)
    except Exception as exc:                        # noqa: BLE001
        why = f"{type(exc).__name__}: {exc}"
    finally:
        # 趁浏览器还开着，把页面上的文字读出来 —— 这是判断真假的硬证据
        if r._crawler is not None:
            raw = ""
            try:
                raw = r._crawler.evaluate(
                    r"""(function(){return JSON.stringify({
                        title: document.title||'',
                        href: location.href||'',
                        text: (document.body&&document.body.innerText||'').replace(/\s+/g,' ').slice(0,300)
                    });})()""", timeout=10)
            except Exception:                       # noqa: BLE001
                pass
            if raw:
                import json
                try:
                    d = json.loads(raw)
                    print(f"   页面标题：{d.get('title') or '(空)'}")
                    print(f"   当前地址：{d.get('href') or '(空)'}")
                    print(f"   页面文字：{d.get('text') or '(空)'}")
                except Exception:                   # noqa: BLE001
                    print("   读页面文字失败")
            r.close()

    print(f"\n解析结果：{'成功' if data else '失败'}")
    if not data:
        print(f"   原因：{why}")
    print(f"   连续失败计数={r._fails}  已停用={r._disabled}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
