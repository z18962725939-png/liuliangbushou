# -*- coding: utf-8 -*-
"""生成桌面界面用的内嵌字体（gui_qt/styles/fonts/*.ttf）。

为什么要它（2026-10-17 UI 诊断 P3 / 10-17 晚二次改版）
--------------------------------------------------------
第一版用的是思源黑体 GB2312 子集，客户反馈「字体不好看」。原因不是思源本身差，
而是它偏「公文感」：字面小、笔画末端收得紧、x-height 低 —— 在 13px 的界面字号
上显得发虚、发挤，和「大公司软件」那种开阔感差一截。

第二版换成 **Zen Maru Gothic SC**（OFL，免费商用）圆体，回应客户第二次反馈的
"字体干巴巴、不够圆润"。

★ 第三版（2026-10-27）换回 **MiSans**，理由不是审美口味，是**量出来的**：

    13px 下      x-height   行高    行高/字号
    Zen Maru      8.0       25.0     1.92
    MiSans        9.0       23.0     1.77
    微软雅黑       9.0       21.0     1.62

  圆体把笔画末端收成圆弧，代价就是**字面被吃掉一圈**：同样 13px，圆体的字要
  矮 1px（12.5%），而行高却高 4px。两个叠在一起就是"字又小、行又松"，
  看起来发虚、发飘、不精神 —— 这正是客户第三次反馈的"打开软件就觉得字体难看"。

  ★ 「圆润」不该靠圆体做。苹果中文站用的是苹方（PingFang SC），它**不是圆体**：
    笔画末端是平切，靠的是 x-height 高 + 字重档细 + 大字号配负字距。
    所以这一版把"润"交给排版（字重 4 档 + 负字距 + 大号字），把"清晰"
    交回字面更大的几何体 MiSans。

  MiSans 是小米发布的简体字族，**官方声明免费商用**（含嵌入到软件中分发），
  且 GB2312 子集后零缺字。四档 400/500/600/700 全部重编号，
  界面里写 font-weight:600 会真正落到 Semibold 而不是被顶到 Bold。

★ 四个必须知道的点
--------------------------------------------------------
1. **Qt 不认 woff2**：QFontDatabase.addApplicationFont 只支持 TrueType /
   OpenType。官方只发 woff2（4.6 MB）和 ttf（13 MB），这里下 woff2、
   在 --prepare 里用 fontTools 转成 ttf（需要 brotli）。

2. **子集要按 GB2312 全集裁，不能只裁源码里的静态文案**
   表格里装的是**运行时抓来的**视频标题、评论、昵称 —— 那些字源码里根本没有。
   只裁静态文案的话，动态内容会回退到系统字体，一个表格里一半新字体一半雅黑。

3. **四个字重的 family 名必须完全一致，weightClass 必须重编号**
   MiSans 原包的 usWeightClass 是 330 / 380 / 520 / 630 这种非标准值 ——
   Qt 按 CSS 数值就近匹配，写 font-weight:600 会落到 520 那一档、写 500 会落到
   380，层与层之间差半档，标题看着就"不够狠"。
   这里统一重编成 400 / 500 / 600 / 700，并把 nameID 1/2/4/6 全部改成同一族名。

   ★ 只有三档时最致命的一处：界面里满屏的 font-weight:600 全部落到 700（Bold），
     标题于是"过重、发糊"。补上 600 这一档，标题才会是真正的 Semibold。

4. **源文件放的是「已裁过的中间体」**
   官方包 227 MB，不可能塞进仓库。`--prepare` 会把官方 zip 解出来的四个字重
   先裁成 GB2312+拉丁+符号的中间体（约 7 MB）存到 tools/fonts_src/misans/，
   日常构建只吃这四份中间体（毫秒级，无需联网）。要换字重或换字体时才需要重跑
   `--prepare`。

用法
--------------------------------------------------------
    python tools/build_app_font.py --prepare <MiSans.zip 的解压目录>   # 一次性
    python tools/build_app_font.py                                     # 生成
    python tools/build_app_font.py --check                             # 只校验

★ 换回 Zen Maru Gothic 的方法（万一客户就是想要圆体）：
    把下面的 SRC_DIR 改成 fonts_src/zenmaru，并把 _WEIGHTS 换成
      ZenMaru-Regular 400 / ZenMaru-Medium 500 / ZenMaru-Bold 700 三行，
    再删掉 theme.py 里 _APP_FONT_FILES 的第四项即可。
    中间体都在 tools/fonts_src/ 下，两套都留着，切回去不用重新联网。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "tools" / "fonts_src" / "harmony"
OUT_DIR = ROOT / "gui_qt" / "styles" / "fonts"

FAMILY = "TrafficCatcher Sans"

# 源文件 -> (子家族名, usWeightClass, 输出文件名)
# ★ 2026-10-27 五次改版：Zen Maru Gothic SC（圆体）→ 回到 MiSans。
#
#   圆体在 13px 上 x-height 只有 8（MiSans/雅黑是 9）、行高却是 25（雅黑 21）——
#   字小一圈、行松一截，客户第三次反馈「打开软件就觉得字体难看」。
#   实测数据就写在本文件顶部的注释里，别再凭"圆不圆"做决定，量 x-height。
#
#   MiSans 的字面大、x-height 高、数字干净（几何体，宽 96 vs 圆体 80），
#   GB2312 子集后零缺字。四档字重全部重编号，界面里的 600 才不会顶到 700。
_WEIGHTS = [
    ("Regular.ttf", "Regular", 400, "TrafficSans-Regular.ttf"),
    ("Medium.ttf", "Medium", 500, "TrafficSans-Medium.ttf"),
    ("Semibold.ttf", "Semibold", 600, "TrafficSans-Semibold.ttf"),
    ("Bold.ttf", "Bold", 700, "TrafficSans-Bold.ttf"),
]

_CJK = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")

# 拉丁 / 数字 / 界面常用符号
_LATIN = "".join(chr(c) for c in range(0x20, 0x7F))
_EXTRA = "°·—…×÷≈≤≥±★▲▼●○✓✕①②③④⑤⑥⑦⑧⑨⑩℃￥§¶†‡‹›«»"


def collect_charset() -> str:
    """界面可能显示到的全部字符。"""
    chars: set[str] = set(_LATIN) | set(_EXTRA)
    for base in ("gui_qt", "justoneapi_collector"):
        for p in (ROOT / base).rglob("*.py"):
            try:
                chars |= set(_CJK.findall(p.read_text(encoding="utf-8")))
            except Exception:                                # noqa: BLE001
                continue
    for p in (ROOT / "gui_qt" / "styles").glob("*.qss"):
        try:
            chars |= set(_CJK.findall(p.read_text(encoding="utf-8")))
        except Exception:                                    # noqa: BLE001
            continue

    # ★ GB2312 全集：运行时动态内容（视频标题 / 评论 / 昵称）的兜底。
    #
    # ★ 取值范围有讲究（踩过）：GB2312 的**符号区在 0xA1~0xA9**，汉字从 0xB0
    #   才开始。只从 0xB0 起取的话，↑ ↓ ← → ★ ℃ 这些符号全部缺字 ——
    #   面板里「↑ ↓ 选择」会渲染成莫名其妙的数字，而且**不会报错**。
    for hi in list(range(0xA1, 0xA4)) + [0xA9] + list(range(0xB0, 0xF8)):
        for lo in range(0xA1, 0xFF):
            try:
                chars.add(bytes([hi, lo]).decode("gb2312"))
            except Exception:                                # noqa: BLE001
                continue
    return "".join(sorted(chars))


def _rename(font, sub: str, weight: int) -> None:
    """统一 name 表 —— 四个字重必须同名，Qt 才认得它们是同一个族。"""
    for nid, value in ((1, FAMILY),
                       (2, sub),
                       (4, f"{FAMILY} {sub}"),
                       (6, f"{FAMILY}-{sub}"),
                       (16, FAMILY)):
        try:
            font["name"].setName(value, nid, 3, 1, 0x409)
        except Exception:                                    # noqa: BLE001
            pass
    try:
        font["OS/2"].usWeightClass = weight
    except Exception:                                        # noqa: BLE001
        pass
    # fsSelection：Regular 要置 bit6，Bold 要置 bit5，其余清掉，
    # 否则部分渲染路径会以为是 Bold 变体而做合成加粗（字会糊）。
    try:
        fs = font["OS/2"].fsSelection
        fs = (fs | 0x40) if sub == "Regular" else (fs & ~0x40)
        fs = (fs | 0x20) if sub == "Bold" else (fs & ~0x20)
        font["OS/2"].fsSelection = fs
    except Exception:                                        # noqa: BLE001
        pass
    # macStyle 同步，避免 mac 端按 Bold 处理
    try:
        style = font["head"].macStyle
        style = (style | 0x01) if sub == "Bold" else (style & ~0x01)
        font["head"].macStyle = style
    except Exception:                                        # noqa: BLE001
        pass


def _subset(src: Path, charset: str):
    from fontTools.subset import Options, Subsetter
    from fontTools.ttLib import TTFont

    font = TTFont(src)
    opts = Options()
    opts.drop_tables = ["DSIG"]
    # kern/liga 保留：拉丁数字的成对间距全靠它，丢了两字母间距会松
    opts.layout_features = ["kern", "liga", "calt"]
    opts.notdef_outline = True
    opts.recalc_bounds = True
    opts.name_IDs = [1, 2, 3, 4, 6]
    subber = Subsetter(options=opts)
    subber.populate(text=charset)
    subber.subset(font)
    return font


def prepare(raw_dir: Path) -> int:
    """从官方字体目录取各字重，裁成中间体存进 fonts_src/misans。

    ★ 源文件可以是 .woff2（小、下载快）。fontTools 读 woff2 需要 brotli 模块，
      存盘前必须把 flavor 清掉，否则中间体又是 woff2、Qt 加载不了。
    """
    raw_dir = Path(raw_dir)
    charset = collect_charset()
    print(f"字符集：{len(set(charset))} 个")
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    for src_name, _sub, _w, _out in _WEIGHTS:
        cand = list(raw_dir.rglob(src_name))
        if not cand:
            # 允许直接把 woff2 丢进来：按同名换后缀再找一次
            alt = src_name.replace(".ttf", ".woff2")
            cand = list(raw_dir.rglob(alt))
        if not cand:
            print(f"  ✗ 找不到 {src_name}（在 {raw_dir} 下）")
            return 1
        font = _subset(cand[0], charset)
        font.flavor = None                     # woff2 → 真正的 ttf
        dst = SRC_DIR / src_name
        font.save(dst)
        print(f"  {src_name:24s} -> {dst.stat().st_size / 1024:7.0f} KB")
    print(f"中间体已就绪：{SRC_DIR}")
    return 0


def build_one(src_name: str, sub: str, weight: int, out_name: str) -> int:
    src = SRC_DIR / src_name
    if not src.is_file():
        raise SystemExit(f"[错误] 缺少字体源：{src}（先跑 --prepare）")
    font = _subset(src, collect_charset())
    font.flavor = None
    _rename(font, sub, weight)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / out_name
    font.save(out)
    return out.stat().st_size


def check() -> int:
    missing_files = [o for _s, _b, _w, o in _WEIGHTS if not (OUT_DIR / o).is_file()]
    if missing_files:
        print("  ✗ 缺少产物：", "、".join(missing_files))
        return 1

    from PySide6.QtGui import QFontDatabase, QFontMetrics
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])        # noqa: F841
    db = QFontDatabase()
    for _s, _sub, _w, out_name in _WEIGHTS:
        if db.addApplicationFont(str(OUT_DIR / out_name)) < 0:
            print(f"  ✗ 加载失败：{out_name}")
            return 1

    fams = set(db.families())
    ok = FAMILY in fams
    styles = db.styles(FAMILY) if ok else []
    total = sum((OUT_DIR / w[3]).stat().st_size for w in _WEIGHTS)
    print(f"  家族名在位：{ok}   可用字重：{styles}")
    print(f"  合计：{total / 1024:.0f} KB")
    if not ok or len(styles) < len(_WEIGHTS):
        return 1

    probe = "流量捕手工作台选题挖掘评论区截流ABCabc0123°…★↑↓￥"
    fm = QFontMetrics(db.font(FAMILY, "Regular", 13))
    missing = [c for c in probe if not fm.inFontUcs4(ord(c))]
    print(f"  探针 {len(probe)} 字，缺字：{missing or '无'}")
    return 1 if missing else 0


def main() -> int:
    args = sys.argv[1:]
    if "--prepare" in args:
        i = args.index("--prepare")
        if i + 1 >= len(args):
            print("用法：--prepare <MiSans 解压目录>")
            return 2
        return prepare(Path(args[i + 1]))
    if "--check" in args:
        return check()
    if not SRC_DIR.is_dir():
        print(f"[错误] 字体源目录不存在：{SRC_DIR}（先跑 --prepare）")
        return 1
    total = 0
    for src_name, sub, weight, out_name in _WEIGHTS:
        size = build_one(src_name, sub, weight, out_name)
        total += size
        print(f"  {out_name:30s} {size / 1024:7.0f} KB  ({sub} {weight})")
    print(f"合计 {total / 1024:.0f} KB -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
