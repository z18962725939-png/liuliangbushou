# -*- coding: utf-8 -*-
"""门禁：免凭证公开热榜降级源不得越界、不得退化。

背景（2026-11-14 22:20）：
  客户没有 XiaoYingAPI 项目凭证时，「看热榜」原本只会弹一句无解报错。
  新增 drama_public_rank 作为降级源：只取红果官网首页 SSR 的公开
  元数据（剧名 / 名次 / 集数 / 分类 / 封面），让没凭证也能找选题。

★ 这条门禁守的是合规边界，不只是功能：
  · 绝不解析播放直链 / CENC 解密 / 登录 / 翻页 / 批量下载；
  · 公开源条目绝不许拿去调 detail 与下载（series_id 用途不同，
    否则又变成「点了没反应」）；
  · 有凭证时仍优先走 drama_api.rank()，降级不能抢主链路。
"""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

PASS = 0
FAIL = 0


def chk(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[OK] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}" + (f" :: {detail}" if detail else ""))


MOD = (ROOT / "justoneapi_collector" / "drama_public_rank.py").read_text(encoding="utf-8")
DP = (ROOT / "gui_qt" / "pages" / "drama_parse_page.py").read_text(encoding="utf-8")
SPEC = (ROOT / "justoneapi_collector.spec").read_text(encoding="utf-8")
REL = (ROOT / "tools" / "release_all.py").read_text(encoding="utf-8")

print("\n[1] 静态：合规边界（只许元数据）")
tree = ast.parse(MOD)
calls: set[str] = set()
for node in ast.walk(tree):
    if not isinstance(node, ast.Call):
        continue
    if isinstance(node.func, ast.Name):
        calls.add(node.func.id)
    elif isinstance(node.func, ast.Attribute):
        calls.add(node.func.attr)
chk("不调用播放 / 下载 / 解密 / 登录类动作",
    not (calls & {"download_episode", "play_url", "ResolveMediaAsync",
                  "Decode", "login", "post", "request"}),
    str(sorted(calls & {"download_episode", "play_url", "post", "request"})))
urls = [n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and n.value.startswith("http")]
chk("只访问红果官网首页一个地址", urls == ["https://hongguoduanju.com/"], str(urls))
_dl_fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "_download"), None)
_hdr_keys: set[str] = set()
for _n in ast.walk(_dl_fn) if _dl_fn is not None else []:
    if isinstance(_n, ast.Dict):
        for _k in _n.keys:
            if isinstance(_k, ast.Constant) and isinstance(_k.value, str):
                _hdr_keys.add(_k.value)
chk("请求头只有 UA 与语言，不带 Cookie / 签名 / 鉴权头",
    _hdr_keys == {"User-Agent", "Accept-Language"}, str(_hdr_keys))
chk("禁走系统代理（与 media_parser / drama_api 一致）", "trust_env=False" in MOD)
chk("带浏览器 UA（官网对裸 UA 可能降级）", "_UA" in MOD and "Mozilla/5.0" in MOD)
chk("有单次条数上限，不是全量批量抓取",
    "MAX_ITEMS" in MOD and "limit = max(1, min(int(limit), MAX_ITEMS))" in MOD)
chk("每条结果的 url 恒为空（不产生播放入口）",
    '"url": "",' in MOD and "_norm_item" in MOD)
chk("失败文案都提示还有配置凭证这条路", "_TAIL" in MOD and MOD.count("_TAIL") >= 4)
chk("新模块已登记进 spec hiddenimports",
    '"justoneapi_collector.drama_public_rank"' in SPEC)
chk("门禁已挂进发版链", "_validate_drama_public_rank.py" in REL)

print("\n[2] 静态：页面降级接线")
chk("有凭证时仍走 drama_api.rank()（降级不抢主链路）",
    "if drama_api.credentials_ready():" in DP and
    "Worker(self._run_rank)" in DP)
chk("无凭证时改走免凭证公开源",
    "Worker(self._run_public_rank)" in DP and
    "drama_public_rank.fetch_rank()" in DP)
chk("公开模式用 _public_mode 标记且渲染时设置",
    "self._public_mode = False" in DP and
    "self._public_mode = (kind == \"public-rank\")" in DP)
_seg_click = DP.split("def _on_series_clicked", 1)[1].split("def _run_detail", 1)[0]
chk("公开模式点剧前置拦截，且拦截发生在 detail Worker 之前",
    "if self._public_mode:" in _seg_click and
    "Worker(" in _seg_click and
    _seg_click.index("if self._public_mode:") < _seg_click.index("return") <
    _seg_click.index("Worker("))
chk("公开模式点剧给出可操作提示",
    "设置凭证" in _seg_click and "看热榜" in _seg_click)
chk("换结果时清空集数与选中态（防误下载残留）",
    "self._eps_list.clear()" in DP and "self._eps = []" in DP)
chk("公开模式集数区前置说明边界（不点也知道限制）",
    "公开热榜只提供选题" in DP and "配置凭证后再点「看热榜」" in DP)
chk("无裸 .clicked.connect（槽异常会静默卡死）", ".clicked.connect(" not in DP)
def _bad_on_error(src: str) -> list[str]:
    """AST 精确判定：注释 / 文档字符串里提到旧 bug 不算违规。"""
    found: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "on_error":
                continue
            v = kw.value
            if (isinstance(v, ast.Attribute) and v.attr == "set"
                    and isinstance(v.value, ast.Attribute)
                    and v.value.attr in ("_status", "status")):
                found.append(f"line {v.lineno}")
    return found


chk("无 on_error=self._status.set（Worker 从不启动的老伤）",
    not _bad_on_error(DP), str(_bad_on_error(DP)))
chk("公开榜 Worker 也走 _wire_worker", DP.count("self._wire_worker(") == 5,
    f"实际 {DP.count('self._wire_worker(')} 处")

print("\n[3] 功能：内联 JSON 解析、去重、字段映射、失败兜底")
from justoneapi_collector import drama_public_rank as pub  # noqa: E402

_router = {
    "loaderData": {"page": {"homeSections": [
        {"tab_name": "热播短剧", "video_list": [
            {"series_id": "A", "series_title": "甲", "rank": 1, "episode_cnt": 30,
             "series_cover": "https://img/a.jpg",
             "category_list": [{"name": "玄幻"}, {"name": "重生"}]},
            {"series_id": "B", "series_title": "乙", "rank": 2, "episode_cnt": 60,
             "category_list": [{"name": "都市"}]},
        ]},
        {"tab_name": "热播真人剧", "video_list": [
            {"series_id": "A", "series_title": "甲重复项", "rank": 1},
            {"series_id": "C", "series_title": "丙", "rank": 3},
        ]},
    ]}}
}
_FIXTURE = ("<html><body><script>_ROUTER_DATA = "
            + json.dumps(_router, ensure_ascii=False)
            + ";window.foo=function(){return JSON.parse('{}')};</script></body></html>")

items, err = pub.fetch_rank(html_text=_FIXTURE)
chk("[3] 解析成功且无错误", not err, err)
chk("[3] 4 条原始记录去重后剩 3 条", len(items) == 3, f"实际 {len(items)}")
chk("[3] 保留首次出现的榜序（all 分区优先）",
    [x["name"] for x in items] == ["甲", "乙", "丙"], str([x["name"] for x in items]))
first = items[0]
chk("[3] 字段映射：剧名 / 名次 / 集数 / 分类 / 封面",
    first["name"] == "甲" and first["heat"] == "热榜第 1 名"
    and "全30集" in first["tags"] and "玄幻" in first["tags"]
    and first["cover"] == "https://img/a.jpg", str(first))
chk("[3] 所有条目都不带播放 / 外链入口", all(x["url"] == "" for x in items))
chk("[3] limit 生效且不小于 1", len(pub.fetch_rank(limit=2, html_text=_FIXTURE)[0]) == 2)
chk("[3] JSON 尾巴跟着 JS 也能解析（必须 raw_decode）",
    len(pub.extract_router_data(_FIXTURE)["loaderData"]["page"]["homeSections"]) == 2)
bad, why = pub.fetch_rank(html_text="<html>没有数据块</html>")
chk("[3] 官网结构变化：返回空列表 + 人话错误，不抛异常",
    bad == [] and "结构已调整" in why and "设置凭证" in why, why)
empty_html = "<script>_ROUTER_DATA = " + json.dumps({"loaderData": {"page": {}}}) + ";</script>"
bad2, why2 = pub.fetch_rank(html_text=empty_html)
chk("[3] 无榜单分区：不崩，提示可配置凭证",
    bad2 == [] and "设置凭证" in why2, why2)
with mock.patch.object(pub, "_download",
                       side_effect=AssertionError("注入 html 时不该联网")) as m_dl:
    pub.fetch_rank(html_text=_FIXTURE)
    chk("[3] 离线注入路径绝不联网", m_dl.call_count == 0)
chk("[3] 归一化字段与 drama_api.normalize_series 同构",
    all(k in first for k in ("series_id", "name", "cover", "url", "tags",
                             "intro", "heat", "score", "favorite", "like")))

print("\n[4] 离屏 Qt：真点「看热榜」，验证降级与拦截")
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402
from PySide6.QtCore import QTimer                        # noqa: E402
from gui_qt.pages.drama_parse_page import DramaParsePage  # noqa: E402
from justoneapi_collector import drama_api               # noqa: E402


class _Ctx:
    demo = True


app = QApplication.instance() or QApplication([])
page = DramaParsePage(_Ctx())
btn = next((b for b in page.findChildren(QPushButton) if b.text() == "看热榜"), None)
chk("找到「看热榜」按钮", btn is not None)


def pump(ms: int = 900) -> None:
    QTimer.singleShot(ms, app.quit)
    app.exec()


_pub_items = [
    {"series_id": "p1", "name": "公开剧甲", "heat": "热榜第 1 名", "tags": ["玄幻", "全30集"]},
    {"series_id": "p2", "name": "公开剧乙", "heat": "热榜第 2 名", "tags": ["都市"]},
    {"series_id": "p3", "name": "公开剧丙", "heat": "热榜第 3 名", "tags": ["穿越"]},
]

# --- A：无凭证 → 公开榜出数据；点条目不发起 detail
with mock.patch.object(drama_api, "credentials_ready", return_value=False), \
     mock.patch.object(drama_api, "rank",
                       side_effect=AssertionError("降级时不该调签名接口")) as m_rank, \
     mock.patch.object(pub, "fetch_rank", return_value=(_pub_items, "")) as m_pub, \
     mock.patch.object(drama_api, "detail",
                       side_effect=AssertionError("公开条目不该发起 detail")) as m_detail:
    btn.click()
    pump(900)
    chk("[A] 列表渲染出 3 条公开结果",
        page._list.count() == 3, f"实际 {page._list.count()} 条")
    chk("[A] 标记为公开模式", page._public_mode is True)
    chk("[A] 状态栏标明来源与用途",
        "公开热榜" in page._status.text() and "选题参考" in page._status.text(),
        repr(page._status.text()))
    chk("[A] 没有发出 drama_api.rank 签名请求", m_rank.call_count == 0)
    chk("[A] 调用了免凭证公开源一次", m_pub.call_count == 1)
    chk("[A] 列表项带名次与标签（不只是剧名）",
        "热榜第 1 名" in page._list.item(0).text()
        and "玄幻" in page._list.item(0).text(), repr(page._list.item(0).text()))
    page._list.itemClicked.emit(page._list.item(0))
    pump(120)
    chk("[A] 点公开条目：不发起 detail 请求", m_detail.call_count == 0)
    chk("[A] 点公开条目：给出配置凭证的可操作提示",
        "设置凭证" in page._status.text() and "看热榜" in page._status.text(),
        repr(page._status.text()))
    chk("[A] 点公开条目：没有卡在「正在…」",
        "正在" not in page._status.text(), repr(page._status.text()))

# --- B：公开源失败 → 错误可见，且不会卡「正在拉取」
with mock.patch.object(drama_api, "credentials_ready", return_value=False), \
     mock.patch.object(pub, "fetch_rank",
                       return_value=([], "官网榜单结构已调整，免凭证热榜暂不可用；也可点「设置凭证」使用完整热榜")):
    btn.click()
    pump(900)
    chk("[B] 公开源失败：状态栏显示具体原因",
        "结构已调整" in page._status.text(), repr(page._status.text()))
    chk("[B] 公开源失败：不卡在「正在拉取」",
        "正在拉取" not in page._status.text())
    chk("[B] 公开源失败：告知可改用凭证链路", "凭证" in page._status.text())

# --- C：有凭证 → 仍走完整热榜，公开模式被正确复位
fake = {"results": [{"series_id": "s1", "name": "完整热榜剧",
                     "score": "9.2", "heat": "12万"}]}
with mock.patch.object(drama_api, "credentials_ready", return_value=True), \
     mock.patch.object(drama_api, "rank", return_value=(fake, "")) as m_rank2, \
     mock.patch.object(pub, "fetch_rank",
                       side_effect=AssertionError("有凭证时不该走降级源")):
    btn.click()
    pump(900)
    chk("[C] 有凭证：走 drama_api.rank 一次", m_rank2.call_count == 1)
    chk("[C] 有凭证：公开模式复位", page._public_mode is False)
    chk("[C] 有凭证：渲染完整热榜 1 条", page._list.count() == 1)
    chk("[C] 有凭证：状态栏是完整热榜口径",
        "找到 1 部短剧" in page._status.text(), repr(page._status.text()))

print(f"\n==== 免凭证公开热榜门禁：{PASS}/{PASS + FAIL} ====")
sys.exit(1 if FAIL else 0)
