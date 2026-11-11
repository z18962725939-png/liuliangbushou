"""视频下载页「真实数据契约」抽取器（只读源码，绝不改主程序）。

为什么要这一层：上一轮证明了「设计令牌」可以唯一真源，这一轮要证明
「业务数据契约」同样可以。网页原型不该由人手写列名和平台名——它应当
直接消费现有 Qt 页面里的真实定义，否则原型一上线就和软件对不上。

    真源：gui_qt/pages/video_download_page.py
          justoneapi_collector/media_parser.py
    产物：download_contract.json   → 网页静态预览 + bridge.py 同时读取

用法：python extract_contract.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PAGE = ROOT / "gui_qt" / "pages" / "video_download_page.py"
PARSER = ROOT / "justoneapi_collector" / "media_parser.py"
OUT = HERE / "download_contract.json"


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SystemExit(f"读不到真源文件：{path}（{exc}）")


def need(value, what: str):
    """抽取不到就明确失败，绝不静默退回编造的数据。"""
    if not value:
        raise SystemExit(f"契约抽取失败：{what}。请检查 {PAGE.name} 是否改过写法。")
    return value


def extract() -> dict:
    page = read(PAGE)
    parser = read(PARSER)

    # ① 表格列名（Qt: setHorizontalHeaderLabels(["选择", "平台", ...])）
    head_block = need(re.search(r"setHorizontalHeaderLabels\(\[(.*?)\]\)", page, re.S),
                      "表格列名 setHorizontalHeaderLabels")
    columns = need(re.findall(r'"([^"]+)"', head_block.group(1)), "表格列名内容")

    # ② 下载内容类型（Qt: self.media_combo.addItem("视频", "video")）
    media = need(re.findall(r'self\.media_combo\.addItem\("([^"]+)",\s*"([^"]+)"\)', page),
                 "下载内容类型 media_combo")
    media_types = [{"label": label, "value": value} for label, value in media]

    # ③ 顶部统计卡标签与单位（Qt: MetricStrip([("待下载","0","条",...), ...])）
    stat_block = need(re.search(r"MetricStrip\(\s*\[(.*?)\]\s*,\s*mode=", page, re.S),
                      "统计卡 MetricStrip")
    stats = need(re.findall(r'\("([^"]+)",\s*"[^"]*",\s*"([^"]+)"', stat_block.group(1)),
                 "统计卡标签")
    stat_cards = [{"label": label, "unit": unit} for label, unit in stats]

    # ④ 页面自带演示行（Qt: _load_demo_rows 的 demo 列表）
    demo_block = need(re.search(r"def _load_demo_rows.*?demo\s*=\s*\[(.*?)\]", page, re.S),
                      "演示行 _load_demo_rows")
    demo = need(re.findall(r'\("([^"]+)",\s*"([^"]+)",\s*"([^"]+)"\)', demo_block.group(1)),
                "演示行内容")

    # ⑤ 平台中文名：界面不许出现英文内部键（PLATFORM_LABELS 是唯一来源）
    label_block = need(re.search(r"PLATFORM_LABELS\s*:\s*dict\[str,\s*str\]\s*=\s*\{(.*?)\n\}",
                                 parser, re.S), "平台中文名 PLATFORM_LABELS")
    labels = dict(need(re.findall(r'"(\w+)"\s*:\s*"([^"]+)"', label_block.group(1)),
                       "平台中文名条目"))
    supported = ["douyin", "xiaohongshu", "kuaishou", "weixin_channels"]
    platforms = [{"key": key, "label": need(labels.get(key), f"平台 {key} 的中文名")}
                 for key in supported]

    # ⑥ 博主批量采集的两个下拉（真源码里的选项）
    blogger = need(re.findall(r'self\.blogger_platform_combo\.addItem\("([^"]+)",\s*"([^"]+)"\)', page),
                   "博主平台下拉")
    list_types = need(re.findall(r'self\.list_type_combo\.addItem\("([^"]+)",\s*"([^"]+)"\)', page),
                      "列表类型下拉")

    # ⑦ 进度口径：这套公式是软件真正的业务规则，原型必须照抄而不是编进度
    throttle = need(re.search(r"last_emit\[0\]\s*<\s*([\d.]+)", page), "进度节流 0.12 秒")
    partial_cap = need(re.search(r"min\(([\d.]+),\s*d\s*/\s*t\)", page), "未完成条目上限 0.999")
    formula = need(re.search(r"pct\s*=\s*min\(100\.0,\s*\((.*?)\)\s*\*\s*100\.0\s*/\s*total\)",
                             page), "进度公式")

    # ⑧ 完成状态文案。★ 源码里有 4 处 setText(f"下载完成…")，只有带
    #    「成功 {ok} 条，失败 {fail} 条」的那条是批次汇总；早先用宽松正则
    #    抽成了「下载完成：{ok} 条 → {actual_dir}」，必须钉死关键词。
    done_text = need(re.search(
        r'self\.status\.setText\(f"(下载完成：成功 \{ok\} 条，失败 \{fail\} 条[^"\n]*)"\)',
        page), "批次完成文案（成功/失败汇总）")

    # ⑨ 免登录说明
    login_note = need(re.search(r"_only\s*=\s*QLabel\(\"([^\"]+)\"\)", page), "平台支持说明")

    return {
        "_说明": "自动生成，请勿手改。真源为 Qt 下载页与 media_parser.py，运行 extract_contract.py 重新抽取。",
        "version": "1.0",
        "source": {
            "page": "gui_qt/pages/video_download_page.py",
            "labels": "justoneapi_collector/media_parser.py",
        },
        "columns": columns,
        "media_types": media_types,
        "stat_cards": stat_cards,
        "demo_rows": [{"platform": p, "title": t, "duration": d} for p, t, d in demo],
        "platforms": platforms,
        "blogger_platforms": [{"label": a, "value": b} for a, b in blogger],
        "list_types": [{"label": a, "value": b} for a, b in list_types],
        "progress": {
            "formula": formula.group(1).strip(),
            "throttle_seconds": float(throttle.group(1)),
            "partial_cap": float(partial_cap.group(1)),
            "note": "完成一条记满 1.0，未完成按字节比例加分，全程只增不减",
        },
        "done_text": done_text.group(1),
        "login_note": login_note.group(1),
        "states": ["排队中", "下载中", "已完成", "失败", "已停止"],
    }


def main() -> int:
    data = extract()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"契约抽取：{OUT.name}")
    print(f"  列名 {len(data['columns'])} 项：{' / '.join(data['columns'])}")
    print(f"  平台 {len(data['platforms'])} 个：{' / '.join(p['label'] for p in data['platforms'])}")
    print(f"  下载内容 {len(data['media_types'])} 种：{' / '.join(m['label'] for m in data['media_types'])}")
    print(f"  演示行 {len(data['demo_rows'])} 条、统计卡 {len(data['stat_cards'])} 张")
    print(f"  进度口径：({data['progress']['formula']}) / 总条数，"
          f"节流 {data['progress']['throttle_seconds']}s，单条上限 {data['progress']['partial_cap']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
