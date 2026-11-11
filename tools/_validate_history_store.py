# -*- coding: utf-8 -*-
"""自检：本地历史库 history_store（59 项）。

真正要守死的四条规则（改错不报编译错，只有这里能拦住）：
  1. 每天一个数据点 —— 同一天重复抓取只覆盖，绝不堆点
  2. 跨天必须留痕 —— 昨天 100 赞今天 200 赞，要能画出上涨的线
  3. 粉丝曲线两来源合并 —— 挖作品顺带记的粗估值 + 查主页的精确值
  4. 出任何问题都不能拖垮采集 —— 坏库 / 脏字段，一律静默

   python tools/_validate_history_store.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import justoneapi_collector.history_store as hs  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def chk(cond, name: str, detail: str = "") -> bool:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  -- " + str(detail)) if (detail and not cond) else ""))
    return bool(cond)


def sec(title: str) -> None:
    print("\n" + title)


# ── 把"今天"钉死，免得自检在跨零点的那一瞬间翻车
TODAY = "2026-10-15"
_REAL_OFFSET = hs._day_offset
hs._today = lambda *a: TODAY
hs._day_offset = lambda d, base="": _REAL_OFFSET(d, base or TODAY)


def mk(pid, digg=100, fans=5000, author="A001", name="老陈说机械"):
    """一条作品。**字段名必须和 PostItem 一致**（是 post_id 不是 id）。"""
    return {
        "post_id": pid, "platform": "dy", "title": "加长臂这么选，一台顶三台",
        "author_id": author, "author_name": name,
        "url": "https://www.douyin.com/video/%s" % pid,
        "digg": digg, "comment": 12, "collect": 6, "share": 3,
        "play": 20000, "author_fans": fans, "publish_ts": 1760000000, "duration": 42,
    }


def prof(uid, name, fans, platform="dy"):
    return {"author_id": uid, "platform": platform, "nickname": name, "fans": fans}


def rows(t: str) -> int:
    r = hs._query("SELECT COUNT(*) FROM %s" % t)
    return int(r[0][0]) if r else -1


# ------------------------------------------------------------------ 环境
TMP = tempfile.mkdtemp(prefix="hs_test_")
DB = os.path.join(TMP, "history.db")
BAD = os.path.join(TMP, "broken.db")
with open(BAD, "wb") as f:
    f.write(b"\x00\x01garbage not a database" * 50)

hs.set_enabled(True)
hs._DB_PATH = DB
hs._reset_conn()
hs.clear_all()

print("=" * 70)
print("自检：本地历史库（history_store）  ——  今天固定为 " + TODAY)
print("临时库：" + DB)
print("=" * 70)

# ---------------------------------------------------------- 1. 基本读写（8）
sec("【1】基本读写")
chk(hs.available(), "sqlite3 可用", "缺 sqlite3 模块")
chk(str(hs.db_path()) == DB, "库路径可覆盖（自检不污染真实数据）", hs.db_path())
chk(hs.stats()["rows"] == 0, "空库统计全为 0", hs.stats())
n = hs.snap_posts([mk("V1001"), mk("V1002", digg=500)], source="抖音/作品搜索")
chk(n == 2, "两条作品写进去两行", n)
tables = {r[0] for r in (hs._query("SELECT name FROM sqlite_master WHERE type='table'") or [])}
chk("post_snap" in tables and "author_snap" in tables, "作品表与账号表都在", tables)
hs.snap_posts([mk("V1001", digg=777)], source="抖音/作品搜索")
chk(rows("post_snap") == 2, "同一天再抓同一条 → 覆盖而不是新增一行", rows("post_snap"))
chk(hs._to_int("1.2万") == 12000, "『1.2万』读成 12000", hs._to_int("1.2万"))
chk(hs._to_int(None) == 0 and hs._to_int("abc") == 0 and hs._to_int("1,234") == 1234,
    "脏数值安全归零 / 逗号无碍", (hs._to_int(None), hs._to_int("abc"), hs._to_int("1,234")))

# -------------------------------------------------------- 2. 每日粒度（6）
sec("【2】每日粒度")
for day in ("2026-10-12", "2026-10-13", "2026-10-14"):
    hs._today = lambda *a, d=day: d
    hs.snap_posts([mk("V1001", digg=100 + int(day[-2:]))], source="抖音/作品搜索")
hs._today = lambda *a: TODAY
hs.snap_posts([mk("V1001", digg=999)], source="抖音/作品搜索")
tr = hs.post_trend("dy", "V1001", 30)
chk(len(tr) == 4, "跨 4 天留下 4 个数据点", [(r["day"], r["digg"]) for r in tr])
chk([r["digg"] for r in tr] == [112, 113, 114, 999], "每天各记各的值，互不相盖",
    [(r["day"], r["digg"]) for r in tr])
days = [r["day"] for r in tr]
chk(days == sorted(days), "曲线按天升序", days)
chk(all(len(d) == 10 and d[4] == "-" for d in days), "日期格式 YYYY-MM-DD", days)
chk(tr[-1]["interact"] == 999 + 12 + 6 + 3, "互动量＝点赞+评论+收藏+分享", tr[-1]["interact"])
chk(hs._day_offset(-7, TODAY) == "2026-10-08" and hs._day_offset(-365, TODAY) == "2025-10-15",
    "往前推天数算得对", (hs._day_offset(-7, TODAY), hs._day_offset(-365, TODAY)))

# ------------------------------------------------------ 3. 粉丝曲线（7）
sec("【3】粉丝曲线（粗估 + 精确两来源）")
tr = hs.author_trend("dy", "A001", 30)
chk(len(tr) >= 4, "只挖作品也能拼出粉丝曲线", [(r["day"], r["fans"], r["exact"]) for r in tr])
chk(any(not r["exact"] for r in tr), "顺带记的粉丝数被标为粗估", tr[:2])
hs.snap_author(prof("A001", "老陈说机械", 8888), source="抖音/账号主页")
tr = hs.author_trend("dy", "A001", 30)
chk(TODAY in [r["day"] for r in tr if r["exact"]], "查过主页 → 当天有精确值",
    [(r["day"], r["exact"]) for r in tr])
last = tr[-1]
chk(last["day"] == TODAY and last["fans"] == 8888 and last["exact"],
    "同一天精确值盖掉粗估（不出现两个点）", last)
chk(len([r for r in tr if r["day"] == TODAY]) == 1, "同一天粉丝只留一个点", tr)
chk(all(r["fans"] > 0 for r in hs.author_trend("dy", "A001", 30)),
    "粉丝数不会记成 0（接口没给就沿用上一天）", "有 0 值")
hs.snap_author(prof("A002", "运营小林", 3000), source="抖音/账号主页")
chk(len([r for r in hs.author_trend("dy", "A002", 30) if r["exact"]]) == 1,
    "只查过主页的账号也画得出曲线", "空")

# -------------------------------------------------- 4. 账号与作品清单（6）
sec("【4】账号清单")
accs = hs.tracked_authors(365)
chk(len(accs) == 2, "两个账号都在清单里", [(a["author_id"], a["nickname"]) for a in accs])
a1 = [a for a in accs if a["author_id"] == "A001"][0]
chk(a1["nickname"] == "老陈说机械", "昵称取真实名字", a1["nickname"])
chk(a1["snap_days"] >= 4, "记录天数＝两表日期并集（挖作品的天数也算）", a1["snap_days"])
a2 = [a for a in accs if a["author_id"] == "A002"][0]
chk(a2["snap_days"] == 1, "只查一次主页就是 1 天", a2["snap_days"])
chk([a["fans"] for a in accs] == sorted([a["fans"] for a in accs], reverse=True),
    "清单按粉丝数从高到低", [(a["nickname"], a["fans"]) for a in accs])
posts = hs.author_posts("dy", "A001", 365)
chk(len(posts) == 2 and all(p["author"] == "老陈说机械" for p in posts),
    "按账号筛出他的作品", [(p["post_id"], p["author"]) for p in posts])

# ------------------------------------------------------------ 5. 爆款库（5）
sec("【5】爆款库")
for i in range(6):
    hs.snap_posts([mk("B%03d" % i, digg=1000 - i * 100)], source="抖音/作品搜索")
top = hs.top_posts(365, "", "digg", 3)
chk(len(top) == 3, "limit 生效", len(top))
chk([p["digg"] for p in top] == sorted([p["digg"] for p in top], reverse=True),
    "按点赞从高到低", [p["digg"] for p in top])
chk(all(p["url"] and p["author"] for p in top), "带链接与作者（能直接复制）",
    {k: top[0][k] for k in ("url", "author")})
chk(bool(hs.top_posts(365, "", "comment", 3)), "换一个排序字段照样能查", "空")
chk(all(p["last_day"] >= hs._day_offset(-1, TODAY) for p in hs.top_posts(1, "", "digg", 99)),
    "范围 1 天只出最近一天", "混进了旧数据")

# ------------------------------------------------------- 6. 范围与清理（4）
sec("【6】清理")
before = rows("post_snap")
deleted = hs.prune(1)
chk(deleted > 0, "清理返回删除行数", deleted)
chk(rows("post_snap") < before, "旧记录确实被删", (before, rows("post_snap")))
left = {r["last_day"] for r in hs.top_posts(365, "", "digg", 99)}
chk(all(d >= hs._day_offset(-1, TODAY) for d in left), "只留下最近一天内的", sorted(left))
chk(hs.stats()["days"] == 2, "统计里的天数跟着变", hs.stats()["days"])

# ------------------------------------------------------------- 7. 容错（8）
sec("【7】容错（库坏了也不能拖垮采集）")
try:
    hs.snap_posts([], source="测试")
    chk(True, "空列表不报错")
except Exception as e:                                  # pragma: no cover
    chk(False, "空列表不报错", e)
try:
    hs.snap_posts([{"post_id": None, "digg": "不是数字"},
                   {"platform": "dy", "digg": None},
                   "我不是字典"], source="测试")
    chk(True, "脏数据不崩（缺作品号的自动跳过）")
except Exception as e:                                  # pragma: no cover
    chk(False, "脏数据不崩（缺作品号的自动跳过）", e)
chk(hs.post_trend("dy", "V1001", 30) != [], "脏数据没有连累已有的记录", "记录没了")
hs._reset_conn()
hs._DB_PATH = BAD                                       # 指向一个坏库
chk(hs.post_trend("dy", "V1001", 30) == [], "库坏了 → 查出来是空，不抛异常", "有数据")
chk(hs.stats()["rows"] == 0, "库坏了 → 统计归零而不是炸掉", hs.stats())
try:
    hs.snap_posts([mk("V9999")], source="测试")
    chk(True, "库坏了 → 写入静默失败，采集照常继续")
except Exception as e:                                  # pragma: no cover
    chk(False, "库坏了 → 写入静默失败，采集照常继续", e)
hs._reset_conn()
hs._DB_PATH = DB                                        # 换回好库
hs.set_enabled(False)
n0 = rows("post_snap")
hs.snap_posts([mk("OFF1")], source="测试")
chk(rows("post_snap") == n0, "关掉开关后不再记录", rows("post_snap"))
hs.set_enabled(True)
r = hs._query("SELECT digg, comment_cnt FROM post_snap WHERE post_id='V1001'")
chk(all(isinstance(v, int) for row in r for v in row), "数值列存的是整数", r)

# -------------------------------------------------------- 8. 并发与清空（6）
sec("【8】并发")
hs.clear_all()
errs: list[str] = []


def worker(i):
    try:
        # 作品与账号一一对应：12 条作品 + 12 个账号，两个来源指向同一批号
        hs.snap_posts([mk("T%03d" % i, digg=i, author="C%03d" % i,
                          name="并发%d" % i)], source="测试")
        hs.snap_author(prof("C%03d" % i, "并发%d" % i, i * 10), source="测试")
    except Exception as e:                              # pragma: no cover
        errs.append(str(e))


ts = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
[t.start() for t in ts]
[t.join() for t in ts]
chk(not errs, "12 个线程同时写不报错", errs[:2])
chk(rows("post_snap") == 12, "并发写入一条不丢", rows("post_snap"))
chk(rows("author_snap") == 12, "账号表并发也完整", rows("author_snap"))
st = hs.stats()
chk(st["posts"] == 12 and st["authors"] == 12, "并发后统计准确",
    {"posts": st["posts"], "authors": st["authors"]})
chk(st["size_bytes"] > 0, "占用空间算得出来", st["size_bytes"])
chk(rows("post_snap") != 0 and hs.clear_all() >= 0
    and rows("post_snap") == 0 and rows("author_snap") == 0,
    "清空后两张表都空", (rows("post_snap"), rows("author_snap")))

# ------------------------------------------------------ 9. 界面 / 打包契约（7）
sec("【9】与界面 / 打包的契约")
st = hs.stats()
for k in ("rows", "posts", "authors", "days", "first_day", "last_day", "size_bytes"):
    chk(k in st, "统计字段 %s 存在（页面直接读）" % k, list(st))
chk(isinstance(st["size_bytes"], int) and isinstance(st["first_day"], str),
    "统计字段类型正确", {k: type(v).__name__ for k, v in st.items()})
chk(len(hs.EXPORT_COLUMNS) == 12, "导出 12 列", len(hs.EXPORT_COLUMNS))
exp = hs.export_rows(hs.top_posts(365, "", "digg", 99))
chk(not exp or len(exp[0]) == 12, "导出行宽与表头一致",
    (len(exp[0]) if exp else 0, 12))
ad = os.path.join(ROOT, "justoneapi_collector", "platforms", "adapter.py")
src = open(ad, encoding="utf-8").read()
chk("def _snap" in src and "def _snap_author" in src, "适配层定义了落库钩子", "没定义")
chk(src.count("_snap(") >= 4, "钩子挂满了各个取数方法", src.count("_snap("))
cc = os.path.join(ROOT, "justoneapi_collector", "competitor_check.py")
chk("_snapshot_account(" in open(cc, encoding="utf-8").read(),
    "竞品体检（不走适配层）也单独挂了", "漏挂")
spec = os.path.join(ROOT, "justoneapi_collector.spec")
chk("history_store" in open(spec, encoding="utf-8").read(),
    "打包清单含 history_store（漏了 → 装完曲线永远空）", "漏了")

# ------------------------------------------- 10. 趋势页取用的字段真实存在（2）
sec("【10】趋势页取用的字段真实存在")
page = os.path.join(ROOT, "gui_qt", "pages", "trend_page.py")
if os.path.exists(page):
    psrc = open(page, encoding="utf-8").read()
    # 页面写的是 st = hs.stats() 再 st['posts']，所以先找变量名再找它取过的键。
    # 这条检查防的是：改了 stats() 的字段名、页面读了个不存在的键 —— 不报错，
    # 卡片上永远是 0，用户以为"没记上"。
    var = re.search(r"(\w+)\s*=\s*hs\.stats\(\)", psrc)
    name = re.escape(var.group(1)) if var else "st"
    used = set(re.findall(r"\b%s\[['\"]([^'\"]+)['\"]\]" % name, psrc))
    used |= set(re.findall(r"\b%s\.get\(\s*['\"]([^'\"]+)['\"]" % name, psrc))
    chk(bool(used) and used <= set(st), "页面读的统计键都在返回值里（读错键＝永远显示 0）",
        (used, set(st)))
    calls = set(re.findall(r"\bhs\.(\w+)\(", psrc))
    missing = [c for c in calls if not hasattr(hs, c)]
    chk(not missing, "页面调用的历史库接口全部存在", missing)
else:                                                   # pragma: no cover
    chk(False, "趋势页存在", "找不到 trend_page.py")

# ------------------------------------------------------------------ 收尾
hs._reset_conn()
try:
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)
except Exception:
    pass

print("\n" + "=" * 70)
print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
if FAIL:
    print("\n没过的：")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("全部通过")
