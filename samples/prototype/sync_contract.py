"""把「真实数据契约」同步进所有原型页（静态预览与 Python 桥接同源）。

    download_contract.json ──┬── bridge.py（真窗口 API）
                            └── 各原型页内联 JSON（静态预览 / 无头截图）

这样连离线双击 HTML 时看到的列名、平台、演示行，都不是人手写进页面的，
而是从 Qt 源码抽出来后注入的。手改页面里的内联块，下次同步会被覆盖。

用法：python sync_contract.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from bridge import Bridge  # noqa: E402
import transcribe_status as ts  # noqa: E402

CONTRACT = HERE / "download_contract.json"
TC_CONTRACT = HERE / "transcribe_contract.json"
PAGES = sorted(HERE.glob("流量捕手_*原型.html"))


def inline_json(data) -> str:
    """内联 JSON 不能出现裸 </script>，否则会把脚本标签提前闭合。"""
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


def replace_block(html: str, element_id: str, data) -> tuple[str, bool]:
    pattern = r'(<script type="application/json" id="%s">)(.*?)(</script>)' % re.escape(element_id)
    if not re.search(pattern, html, re.S):
        return html, False
    return re.sub(pattern, lambda m: m.group(1) + inline_json(data) + m.group(3),
                  html, flags=re.S), True


def main() -> int:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    rows = Bridge().get_download_rows()

    # 契约自检：界面上不许出现英文内部键，列名与统计卡必须来自源码
    labels = [p["label"] for p in contract["platforms"]]
    assert all(any("\u4e00" <= ch <= "\u9fff" for ch in label) for label in labels), \
        "平台中文名缺失"
    assert contract["columns"][0] == "选择" and len(contract["columns"]) == 4, \
        "表格列名不是 Qt 源码里的 4 列"
    assert len(contract["media_types"]) == 4 and len(contract["stat_cards"]) == 4, \
        "下载内容或统计卡数量不对"
    assert len(rows) >= len(contract["demo_rows"]), "演示行丢失"

    if not PAGES:
        raise SystemExit("没有找到原型页（流量捕手_*原型.html）")

    # 转录页契约：规则由 transcribe_status.contract() 从 transcribe.py 现场校验，
    # 快照是本机只读扫描在「生成时刻」的真实结果，静态预览与真窗口同源。
    tc = ts.contract()
    TC_CONTRACT.write_text(json.dumps(tc, ensure_ascii=False, indent=2), encoding="utf-8")
    tc_overview = ts.overview()

    for page in PAGES:
        html = page.read_text(encoding="utf-8")
        if "视频下载" in page.name:
            html, has_contract = replace_block(html, "inline-contract", contract)
            html, has_rows = replace_block(html, "inline-rows", rows)
            if not (has_contract and has_rows):
                print(f"  {page.name}：缺少内联占位块，跳过")
                continue
            roundtrip = re.search(
                r'<script type="application/json" id="inline-contract">(.*?)</script>',
                html, re.S)
            assert json.loads(roundtrip.group(1))["version"] == contract["version"], \
                f"{page.name} 内联契约回读失败"
            page.write_text(html, encoding="utf-8")
            print(f"  {page.name}：已同步下载契约 v{contract['version']} + {len(rows)} 条行数据")
        elif "转录" in page.name:
            html, has_tc = replace_block(html, "inline-transcribe-contract", tc)
            html, has_ov = replace_block(html, "inline-transcribe-overview", tc_overview)
            if not (has_tc and has_ov):
                print(f"  {page.name}：缺少转录内联占位块，跳过")
                continue
            back = re.search(
                r'<script type="application/json" id="inline-transcribe-overview">(.*?)</script>',
                html, re.S)
            assert json.loads(back.group(1))["total"] == tc_overview["total"], \
                f"{page.name} 内联状态快照回读失败"
            page.write_text(html, encoding="utf-8")
            print(f"  {page.name}：已同步状态契约 v{tc['version']} + "
                  f"真实扫描 {tc_overview['total']} 个文件快照")
        else:
            print(f"  {page.name}：工作台页不需要内联契约，跳过")

    print(f"契约唯一真源：{CONTRACT.name} / {TC_CONTRACT.name}")
    print(f"  下载：列名 {len(contract['columns'])} 项 / 平台 {len(labels)} 个 / "
          f"行数据 {len(rows)} 条")
    print(f"  转录：扩展名 {len(tc['extensions'])} 种 / 状态 {len(tc['states'])} 类 / "
          f"真实快照 {tc_overview['total']} 个文件")
    print("  校验：中文名、列名、状态枚举、内联 JSON 回读全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
