# -*- coding: utf-8 -*-
"""分段截官网效果图（用锚点让无头浏览器滚到对应区块）。

用法：python tools/_shot_site.py
输出：tools/_site_<区块>.png
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "installer_output" / "standalone" / "index.html"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

SEGS = [("", "hero"), ("#features", "features"), ("#pricing", "pricing"),
        ("#download", "download"), ("#faq", "faq")]


def shot(url: str, out: Path) -> bool:
    cmd = [
        str(EDGE), "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--no-first-run", "--disable-extensions",
        "--force-device-scale-factor=1", "--window-size=1240,1500",
        "--virtual-time-budget=5000",
        f"--user-data-dir={ROOT / 'tools' / '.edge_poster_profile'}",
        f"--screenshot={out}", url,
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    ok = out.exists() and out.stat().st_size > 10000
    print(f"  {out.name:<22} {'OK' if ok else 'FAIL'}  "
          f"{out.stat().st_size // 1024 if out.exists() else 0} KB")
    return ok


def main():
    if not EDGE.exists():
        print("没找到 Edge")
        return 1

    # 平滑滚动会让无头截图在滚动到位前就拍下来（锚点等于没用），
    # 而 .rv 淡入元素初始 opacity:0，无头环境 IntersectionObserver 未必触发，
    # 截出来就是一片空白。所以临时拷一份两处都关掉的副本来截图。
    raw = HTML.read_text(encoding="utf-8")
    raw = raw.replace("scroll-behavior:smooth", "scroll-behavior:auto")
    raw = raw.replace(".rv{opacity:0;transform:translateY(18px);",
                      ".rv{opacity:1;transform:none;")
    tmp = HTML.parent / "_shot_tmp.html"
    tmp.write_text(raw, encoding="utf-8")
    uri = tmp.as_uri()

    bad = 0
    for anchor, name in SEGS:
        if not shot(uri + anchor, ROOT / "tools" / f"_site_{name}.png"):
            bad += 1
    tmp.unlink(missing_ok=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
