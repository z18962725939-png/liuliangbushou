# -*- coding: utf-8 -*-
"""离线自测：评论区截流（justoneapi_collector/comment_mining.py + 页面）。

覆盖：ID 解析、意图词过滤、去重、分页与提前终止、异常兜底、子评论、
成本预估、页面额度提示与结果填充。全程离线、不花钱。

用法： python tools/_validate_comment_mining.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS_N = 0
FAIL_N = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS_N, FAIL_N
    if cond:
        PASS_N += 1
        print(f"  ✅ {name}")
    else:
        FAIL_N += 1
        print(f"  ❌ {name}  {detail}")


# ============================================================ 假响应
class _Resp:
    def __init__(self, data=None, success=True, code=0, message=""):
        self.data = data
        self.success = success
        self.code = code
        self.message = message
        self.raw_json = {"code": code, "data": data, "message": message}


def _comment(cid, text, uid="1001", nick="用户A", uniq="user_a",
             sec="MS4wLjABAAAA_xxx", digg=0, ts=1791054123, fans=0):
    """构造一条与抖音真实返回同构的评论字典。"""
    return {
        "cid": cid, "text": text, "aweme_id": "7494166718776986914",
        "create_time": ts, "digg_count": digg, "status": 1,
        "reply_comment_total": 0,
        "user": {
            "uid": uid, "short_id": "1854870852", "nickname": nick,
            "unique_id": uniq, "sec_uid": sec, "signature": "",
            "follower_count": fans, "is_verified": False,
        },
    }


class FakeClient:
    """按 endpoint 分派的假客户端，记录每次请求。"""

    def __init__(self, pages: dict[int, list[dict]] | None = None,
                 has_more_map: dict[int, bool] | None = None,
                 raise_on: str | None = None):
        # cursor -> 评论列表
        self.pages = pages or {}
        self.has_more_map = has_more_map or {}
        self.raise_on = raise_on
        self.calls: list[tuple[str, dict]] = []
        self.reply_calls = 0
        self.token_invalid = False

    def request(self, platform, endpoint, params=None):
        params = params or {}
        self.calls.append((endpoint, dict(params)))
        if self.raise_on and endpoint == self.raise_on:
            raise RuntimeError("boom")
        if endpoint == "comment-replies-v1":
            self.reply_calls += 1
            return _Resp({"comments": [
                _comment("r1", "已经私信你了，加个微信聊", uid="9001",
                         nick="回复哥", uniq="reply_guy", digg=2),
            ], "cursor": 0, "has_more": 0})
        cursor = int(params.get("cursor") or 0)
        items = self.pages.get(cursor)
        if items is None:
            return _Resp({"comments": [], "cursor": cursor, "has_more": 0})
        return _Resp({"comments": items,
                      "cursor": cursor + len(items),
                      "has_more": self.has_more_map.get(cursor, False)})


print("=" * 62)
print("A) 作品 ID 解析")
print("=" * 62)
from justoneapi_collector import comment_mining as cm        # noqa: E402

check("标准视频链接", cm.extract_post_id(
    "https://www.douyin.com/video/7494166718776986914") == "7494166718776986914")
check("带参数链接", cm.extract_post_id(
    "https://www.iesdouyin.com/share/video/7494166718776986914/?region=CN")
    == "7494166718776986914")
check("aweme_id= 形式", cm.extract_post_id("xxx?aweme_id=7494166718776986914&a=1")
      == "7494166718776986914")
check("纯数字 ID", cm.extract_post_id("7494166718776986914") == "7494166718776986914")
check("短链无 ID → None", cm.extract_post_id("https://v.douyin.com/iRabcdef/") is None)
check("空串 → None", cm.extract_post_id("") is None)

mixed = ("https://www.douyin.com/video/7494166718776986914\n"
         "7494166718776986914，https://www.douyin.com/video/7346511817574812939\n"
         "7346511817574812939")
ids = cm.split_post_inputs(mixed)
check("混合输入拆出 2 个并去重", ids == ["7494166718776986914", "7346511817574812939"],
      f"实际 {ids}")

print()
print("=" * 62)
print("B) 意图词过滤")
print("=" * 62)
check("「多少钱」命中", "多少钱" in "这个多少钱" and bool(cm.match_intent("这个多少钱")))
hits = cm.match_intent("老板怎么买？求链接")
check("多条命中", set(hits) >= {"怎么买", "求链接"}, f"hits={hits}")
check("闲聊不命中", cm.match_intent("哈哈哈哈太好笑了") == [],
      f"hits={cm.match_intent('哈哈哈哈太好笑了')}")
check("自定义词生效", cm.match_intent("想加盟你们", ["加盟"]) == ["加盟"])
check("空文本不命中", cm.match_intent("") == [])
check("垃圾短评判定", cm.is_spam("沙发"))
check("正常评论不算垃圾", not cm.is_spam("这个多少钱啊"))

print()
print("=" * 62)
print("C) 线索构造")
print("=" * 62)
raw = _comment("c1", "这个多少钱？想买", digg=8, ts=1791054123, fans=123)
from justoneapi_collector.platforms.adapter import parse_comment   # noqa: E402
item = parse_comment("douyin", raw, "7494166718776986914")
lead = cm.build_lead("douyin", item)
check("昵称", lead.user_name == "用户A", f"{lead.user_name}")
check("抖音号", lead.user_unique_id == "user_a", f"{lead.user_unique_id}")
check("主页链接含 sec_uid", "douyin.com/user/MS4wLjABAAAA_xxx" in lead.user_home,
      lead.user_home)
check("视频链接", lead.post_url.endswith("/video/7494166718776986914"), lead.post_url)
check("点赞", lead.digg == 8, f"{lead.digg}")
check("时间格式化", lead.published.startswith("2026-"), f"{lead.published}")
check("命中词", "多少钱" in lead.hits, f"{lead.hits}")
check("「多少钱」算强信号", lead.strong is True)
check("导出列数与表头一致", len(lead.as_row()) == len(cm.COMMENT_COLUMNS),
      f"{len(lead.as_row())} vs {len(cm.COMMENT_COLUMNS)}")

# 泛词不再误伤（2026-10-12 真实踩坑：快递隐私吐槽里出现"购买"、骂快递里出现"联系"）
check("「网上购买的产品内容不该写在外包装上」不再命中",
      cm.match_intent("网上购买的产品内容不该写在外包装上，客户隐私") == [],
      f"{cm.match_intent('网上购买的产品内容不该写在外包装上，客户隐私')}")
check("「中通丢了包裹，联系态度也很差」不再命中",
      cm.match_intent("去年中通丢了包裹，恨他们联系态度也很差") == [],
      f"{cm.match_intent('去年中通丢了包裹，恨他们联系态度也很差')}")
check("真意向仍能命中", bool(cm.match_intent("这个多少钱？我想要一个")),
      f"{cm.match_intent('这个多少钱？我想要一个')}")
weak = cm.build_lead("douyin", parse_comment(
    "douyin", _comment("c9", "有用吗这个", digg=1), "p1"))
check("「有用吗」只算弱信号", weak.strong is False and weak.strength == "弱",
      f"strong={weak.strong}")
check("强信号排序在前",
      cm.mine_comments.__name__ == "mine_comments")   # 排序逻辑由 F 段覆盖

print()
print("=" * 62)
print("D) 去重（同一用户只留最高赞）")
print("=" * 62)
ls = [
    cm.build_lead("douyin", parse_comment("douyin", _comment(
        "c1", "多少钱", digg=3, uniq="same"), "p1")),
    cm.build_lead("douyin", parse_comment("douyin", _comment(
        "c2", "怎么买", digg=99, uniq="same"), "p1")),
    cm.build_lead("douyin", parse_comment("douyin", _comment(
        "c3", "多少钱", digg=5, uniq="other", uid="2002"), "p1")),
]
ded = cm.dedupe_leads(ls)
check("3 条去重成 2 条", len(ded) == 2, f"{len(ded)}")
check("保留点赞最高的那条", all(x.digg == 99 for x in ded if x.user_unique_id == "same"),
      f"{[x.digg for x in ded]}")

print()
print("=" * 62)
print("E) 成本预估")
print("=" * 62)
check("3 视频 × 3 页 = 9", cm.estimate_requests(3, 3) == 9)
check("排除作者 +3", cm.estimate_requests(3, 3, exclude_author=True) == 12)
replies_est = cm.estimate_requests(1, 1, include_replies=True)
check("子评论会加粗估", replies_est > 1, f"{replies_est}")
check("0 视频 = 0", cm.estimate_requests(0, 5) == 0)

print()
print("=" * 62)
print("F) 抓取流程（假接口）")
print("=" * 62)
# 第 0 页 3 条（1 条命中），第 3 页 2 条，has_more 控制翻页
c = FakeClient(pages={
    0: [_comment("c1", "多少钱呀", uniq="u1", uid="1", digg=5),
        _comment("c2", "哈哈哈哈", uniq="u2", uid="2"),
        _comment("c3", "求链接", uniq="u3", uid="3", digg=2)],
    3: [_comment("c4", "好喜欢", uniq="u4", uid="4")],
}, has_more_map={0: True})
res = cm.MineResult()
leads, used = cm.fetch_post_comments(c, "douyin", "7494166718776986914", pages=3,
                                     state=res)
check("只留命中的 2 条", len(leads) == 2, f"{len(leads)} -> {[x.text for x in leads]}")
check("has_more=0 后不再翻页（用 2 次）", used == 2, f"used={used}")
check("扫描总数统计", res.scanned == 4, f"{res.scanned}")

# 空页立即停
c2 = FakeClient(pages={0: []})
res2 = cm.MineResult()
_leads2, used2 = cm.fetch_post_comments(c2, "douyin", "x" * 19, pages=5, state=res2)
check("空页只花 1 次请求", used2 == 1, f"used={used2}")

# 异常被兜住（不抛到界面线程）
c3 = FakeClient(pages={0: []}, raise_on="video-comments-v1")
res3 = cm.MineResult()
try:
    cm.fetch_post_comments(c3, "douyin", "7494166718776986914", pages=2, state=res3)
    check("异常不向外抛", True)
except Exception as e:                                        # noqa: BLE001
    check("异常不向外抛", False, f"抛出了 {type(e).__name__}: {e}")
check("异常被记进 errors", len(res3.errors) >= 1, f"{res3.errors}")

# 子评论：只对命中词评论抓
c4 = FakeClient(pages={0: [_comment("c1", "多少钱", uniq="u1", uid="1", digg=5),
                           _comment("c2", "哈哈", uniq="u2", uid="2")]})
res4 = cm.MineResult()
leads4, _u4 = cm.fetch_post_comments(c4, "douyin", "7494166718776986914", pages=1,
                                     include_replies=True, state=res4)
check("子评论只给命中的评论抓（1 次）", c4.reply_calls == 1, f"{c4.reply_calls}")
check("子评论里的意向也入库",
      any("微信" in x.text for x in leads4), f"{[x.text for x in leads4]}")

# 批量 + 停止
c5 = FakeClient(pages={0: [_comment("c1", "多少钱", uniq="u1", uid="1")]})
r5 = cm.mine_comments(c5, "douyin", ["1" * 19, "2" * 19, "3" * 19], pages=1)
check("批量跑完 3 个视频", r5.posts_done == 3, f"{r5.posts_done}")
check("去重后只剩 1 人", len(r5.leads) == 1, f"{len(r5.leads)}")

r6 = cm.mine_comments(c5, "douyin", ["1" * 19, "2" * 19], pages=1,
                      should_stop=lambda: True)
check("should_stop 生效（0 个视频）", r6.posts_done == 0, f"{r6.posts_done}")

# 强信号优先于高赞弱信号
c7 = FakeClient(pages={0: [
    _comment("c1", "有用吗", uniq="weak", uid="1", digg=99999),   # 弱但赞极高
    _comment("c2", "多少钱", uniq="strong", uid="2", digg=1),     # 强但赞低
]})
r7 = cm.mine_comments(c7, "douyin", ["1" * 19], pages=1)
check("强信号排在高赞弱信号前面",
      r7.leads and r7.leads[0].user_unique_id == "strong",
      f"{[x.user_unique_id for x in r7.leads]}")

print()
print("=" * 62)
print("G) 页面（离屏）")
print("=" * 62)
from PySide6.QtWidgets import QApplication, QMessageBox       # noqa: E402
app = QApplication.instance() or QApplication([])

# 拦截弹窗
_shown = {"warning": 0, "question": 0}
_answer = {"v": QMessageBox.Yes}
QMessageBox.warning = staticmethod(                            # type: ignore[assignment]
    lambda *a, **k: _shown.__setitem__("warning", _shown["warning"] + 1) or QMessageBox.Yes)
QMessageBox.question = staticmethod(                           # type: ignore[assignment]
    lambda *a, **k: _shown.__setitem__("question", _shown["question"] + 1) or _answer["v"])

from gui_qt.pages._base import AppContext                     # noqa: E402
from gui_qt.pages.comment_page import CommentPage             # noqa: E402

ctx = AppContext()
page = CommentPage(ctx)
check("页面可构建", page is not None)

page.links.setPlainText("")
page._refresh_quota_hint()
check("空输入时不显示额度提示", page.quota_hint.text() == "", repr(page.quota_hint.text()))

page.links.setPlainText("https://www.douyin.com/video/7494166718776986914\n"
                        "https://www.douyin.com/video/7346511817574812939")
page._refresh_quota_hint()
check("识别 2 个视频", "已识别 2 个视频" in page.id_count.text(), page.id_count.text())
check("额度提示出现", "预计消耗" in page.quota_hint.text(), page.quota_hint.text())

page.pages_box.setCurrentIndex(2)      # 深度 5 页
page._refresh_quota_hint()
check("换档位后预计消耗变化（2×5=10）", "10 次调用" in page.quota_hint.text(),
      page.quota_hint.text())
page.pages_box.setCurrentIndex(0)

# 自定义词解析
page.words.setPlainText("加盟\n代理")
check("自定义词 + 内置词库", "加盟" in (page._effective_words() or [])
      and "多少钱" in (page._effective_words() or []))
page.use_default.setChecked(False)
check("取消内置词库后只剩自定义", page._effective_words() == ["加盟", "代理"],
      f"{page._effective_words()}")
page.use_default.setChecked(True)
page.words.setPlainText("")

# 结果填充
res6 = cm.MineResult()
res6.leads = [cm.build_lead("douyin", parse_comment(
    "douyin", _comment("c1", "多少钱", digg=7, uniq="buyer1"), "7494166718776986914"))]
res6.scanned = 30
res6.requests = 2
page._on_done(res6)
check("结果表填了 1 行", page.table.rowCount() == 1, f"{page.table.rowCount()}")
check("抖音号进表", page.table.item(0, 2).text() == "buyer1",
      page.table.item(0, 2).text() if page.table.item(0, 2) else "")
check("导出按钮可用", page.export_btn.isEnabled())
check("状态显示扫描/命中", "30" in page.status.text() and "1" in page.status.text(),
      page.status.text())

# 未连接服务（ctx.client 为 None，即 demo 模式）：不填示例数据
check("demo 判定", ctx.demo is True)
page2 = CommentPage(ctx)
check("未连接服务时表是空的", page2.table.rowCount() == 0)
# 2026-11-23 起文案由 connection_diagnosis() 按通道精确生成（缺 okdata Key /
# 授权码缺凭证 分开说），比旧的笼统「未连接采集服务」更准。断言改为验「有明确
# 警示且给出可操作方向」，不再钉死某句过时文案。
_status_txt = page2.status.text()
check("未连接服务时提示明确",
      bool(_status_txt) and ("未连接" in _status_txt or "请先" in _status_txt
                             or "Key" in _status_txt or "激活" in _status_txt),
      _status_txt)

print()
print("=" * 62)
print(f"结果：通过 {PASS_N} 项，失败 {FAIL_N} 项")
print("=" * 62)
sys.exit(1 if FAIL_N else 0)
