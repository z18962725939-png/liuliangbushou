# -*- coding: utf-8 -*-
"""行为等价性校验：同一份数据，旧实现 vs 新实现，逐字段比对。

旧实现从 .bak 文件动态加载，两个模块各自读同一个库文件，
所以这是「真实对照」而不是「我以为应该一样」。
"""
import importlib.machinery, importlib.util, json, os, shutil, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from justoneapi_collector import history_store as new

BAK = None
for f in sorted(os.listdir(os.path.join(ROOT, "justoneapi_collector"))):
    if f.startswith("history_store.py.bak_"):
        BAK = os.path.join(ROOT, "justoneapi_collector", f)
print("旧实现:", BAK)

spec = importlib.util.spec_from_loader(
    "old_hs", importlib.machinery.SourceFileLoader("old_hs", BAK))
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)

# 造一份「有历史」的库：10 天 × 20 件作品 × 5 个作者
TMP = tempfile.mkdtemp(prefix="equiv_")
DB = os.path.join(TMP, "history.db")
new._DB_PATH = DB
new._reset_conn()
new.clear_all()

DAYS = ["2026-10-%02d" % d for d in range(6, 16)]
AUTHORS = ["A001", "A002", "A003", "A004", "A005"]
real_today = new._today
for idx, day in enumerate(DAYS):
    new._today = lambda *a, d=day: d
    posts = []
    for i in range(20):
        a = AUTHORS[i % 5]
        posts.append({
            "platform": "dy", "post_id": "V%03d" % i,
            "title": "第%d天的作品%d" % (idx, i),
            "author_id": a, "author_name": "作者" + a[-1],
            "author_fans": 10000 + idx * 100 + int(a[-1]),
            "digg": (i * 37 + idx * 11) % 5000,
            "comment": (i * 13 + idx) % 300,
            "collect": (i * 7 + idx) % 200,
            "share": (i * 3 + idx) % 90,
            "play": (i * 911 + idx * 53) % 200000,
            "duration": 30 + i, "publish_ts": 1760000000 + i,
            "url": "https://www.douyin.com/video/%d" % i,
        })
    new.snap_posts(posts, source="抖音/作品搜索")
    # 只有偶数天查主页（模拟「有时候只挖作品」）
    if idx % 2 == 0:
        new.snap_authors([{"platform": "dy", "author_id": a, "nickname": "精确" + a[-1],
                           "fans": 50000 + idx * 1000 + int(a[-1])} for a in AUTHORS],
                         source="抖音/账号主页")
new._today = real_today

new._reset_conn()
old._DB_PATH = DB
old._reset_conn()

fails = []


def cmp(name, a, b, keys=None):
    a = json.loads(json.dumps(a, ensure_ascii=False, default=str))
    b = json.loads(json.dumps(b, ensure_ascii=False, default=str))
    if keys and isinstance(a, dict):
        a = {k: a.get(k) for k in keys}
        b = {k: b.get(k) for k in keys}
    elif keys:
        a = [{k: r.get(k) for k in keys} for r in a]
        b = [{k: r.get(k) for k in keys} for r in b]
    if a == b:
        print("  ok   %-38s %d 行/项完全一致" % (name, len(a) if isinstance(a, list) else 1))
    else:
        fails.append(name)
        print("  FAIL %-38s" % name)
        if isinstance(a, list):
            for i, (x, y) in enumerate(zip(a, b)):
                if x != y:
                    print("        第%d行 旧=%s" % (i, x))
                    print("              新=%s" % (y,))
                    break
            if len(a) != len(b):
                print("        行数 旧=%d 新=%d" % (len(a), len(b)))
        else:
            print("        旧=%s\n        新=%s" % (a, b))


print("\n" + "=" * 70)
print("同一份数据（10 天 × 20 作品 × 5 作者 = 200 行快照），逐接口对比")
print("=" * 70)

cmp("post_trend(V007)", old.post_trend("dy", "V007", 365), new.post_trend("dy", "V007", 365))
cmp("author_trend(A001)", old.author_trend("dy", "A001", 365), new.author_trend("dy", "A001", 365))
cmp("author_trend(没查过主页的 A002)", old.author_trend("dy", "A002", 365),
    new.author_trend("dy", "A002", 365))
cmp("top_posts(全站 365 天)", old.top_posts(365, "", "digg", 200),
    new.top_posts(365, "", "digg", 200))
cmp("top_posts(限平台)", old.top_posts(365, "dy", "digg", 200),
    new.top_posts(365, "dy", "digg", 200))
cmp("top_posts(按播放)", old.top_posts(365, "", "play", 200),
    new.top_posts(365, "", "play", 200))
cmp("top_posts(近 3 天)", old.top_posts(3, "", "digg", 200),
    new.top_posts(3, "", "digg", 200))
cmp("author_posts(A001)", old.author_posts("dy", "A001", 365, 300),
    new.author_posts("dy", "A001", 365, 300))
# last_day 有意做了语义统一：旧版取「作者表最后一天」（只在作品表出现的号取作品表），
# 新版统一成「任一来源的最后一天」。UI 不显示这个字段，只用于 days= 的过滤，
# 而过滤口径两者等价，所以这里比对除 last_day 外的所有字段。
cmp("tracked_authors(365)", old.tracked_authors(365), new.tracked_authors(365),
    keys=["platform", "author_id", "nickname", "fans", "snap_days", "source"])
cmp("tracked_authors(3 天)", old.tracked_authors(3), new.tracked_authors(3),
    keys=["platform", "author_id", "nickname", "fans", "snap_days", "source"])
cmp("stats", old.stats(), new.stats(), keys=["rows", "posts", "authors", "days",
                                            "first_day", "last_day", "available", "enabled"])
cmp("export_rows(top_posts)", old.export_rows(old.top_posts(365, "", "digg", 200)),
    new.export_rows(new.top_posts(365, "", "digg", 200)))

# 派生表重建后再比一次：证明「重建 == 增量维护」
print("\n派生表全量重建后复查（验证增量维护与重建结果一致）")
new.rebuild_derived()
cmp("重建后 top_posts", old.top_posts(365, "", "digg", 200), new.top_posts(365, "", "digg", 200))
cmp("重建后 tracked_authors", old.tracked_authors(365), new.tracked_authors(365),
    keys=["platform", "author_id", "nickname", "fans", "snap_days", "source"])
cmp("重建后 stats", old.stats(), new.stats(),
    keys=["rows", "posts", "authors", "days", "first_day", "last_day"])

print("\n" + "=" * 70)
if fails:
    print("不一致 %d 项：%s" % (len(fails), fails))
    sys.exit(1)
print("全部接口输出与改造前完全一致")
shutil.rmtree(TMP, ignore_errors=True)
