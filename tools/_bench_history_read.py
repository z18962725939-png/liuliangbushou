# -*- coding: utf-8 -*-
"""真实规模对比：5万件作品 × 100天 ≈ 100万行快照。旧 SQL vs 新实现"""
import os, sys, random, sqlite3, tempfile, time, statistics, datetime

OLD_QUERIES = {
    "top_posts": (
        "SELECT platform,post_id,title,author_name,author_fans,digg,comment_cnt,collect_cnt,"
        "share_cnt,play_cnt,url,MAX(day),COUNT(*),publish_ts,author_id FROM post_snap"
        " WHERE day>=? GROUP BY platform,post_id ORDER BY digg DESC LIMIT 200", ("2025-11-05",)),
    "author_posts": (
        "SELECT platform,post_id,title,author_name,author_fans,digg,comment_cnt,collect_cnt,"
        "share_cnt,play_cnt,url,MAX(day),COUNT(*),publish_ts FROM post_snap"
        " WHERE day>=? AND platform=? AND author_id=? GROUP BY platform,post_id"
        " ORDER BY digg DESC LIMIT 300", ("2025-11-05", "douyin", "A00001")),
    "tracked_authors": (
        "SELECT platform,author_id,author_name,author_fans,MAX(day),COUNT(*),source"
        " FROM post_snap WHERE day>=? AND author_id<>'' GROUP BY platform,author_id",
        ("2025-11-05",)),
    "stats": None,
}

NEW_CASES = {
    "top_posts": lambda hs: hs.top_posts(days=365, order_by="digg", limit=200),
    "author_posts": lambda hs: hs.author_posts("douyin", "A00001", days=365, limit=300),
    "tracked_authors": lambda hs: hs.tracked_authors(days=365),
    "stats": None,
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS post_snap (
    platform TEXT NOT NULL, post_id TEXT NOT NULL, day TEXT NOT NULL,
    seen_ts INTEGER NOT NULL DEFAULT 0, title TEXT DEFAULT '', author_id TEXT DEFAULT '',
    author_name TEXT DEFAULT '', author_fans INTEGER DEFAULT 0, digg INTEGER DEFAULT 0,
    comment_cnt INTEGER DEFAULT 0, collect_cnt INTEGER DEFAULT 0, share_cnt INTEGER DEFAULT 0,
    play_cnt INTEGER DEFAULT 0, duration INTEGER DEFAULT 0, publish_ts INTEGER DEFAULT 0,
    url TEXT DEFAULT '', source TEXT DEFAULT '', PRIMARY KEY (platform, post_id, day));
CREATE INDEX IF NOT EXISTS idx_post_day  ON post_snap(day);
CREATE INDEX IF NOT EXISTS idx_post_auth ON post_snap(platform, author_id);
CREATE TABLE IF NOT EXISTS author_snap (
    platform TEXT NOT NULL, author_id TEXT NOT NULL, day TEXT NOT NULL,
    seen_ts INTEGER NOT NULL DEFAULT 0, nickname TEXT DEFAULT '', fans INTEGER DEFAULT 0,
    source TEXT DEFAULT '', PRIMARY KEY (platform, author_id, day));
CREATE INDEX IF NOT EXISTS idx_author_day ON author_snap(day);
"""

POOL = 50000          # 作品池：5 万件
N_DAYS = 100
PER_DAY = 10000       # 每天抓 1 万条 → 100 万行
PATH = os.path.join(tempfile.gettempdir(), "bench_real_1m.db")


def build():
    if os.path.exists(PATH):
        os.remove(PATH)
    c = sqlite3.connect(PATH)
    c.executescript(SCHEMA_SQL)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=OFF")
    random.seed(42)
    base = datetime.date(2026, 7, 1)
    t0 = time.time()
    for d in range(N_DAYS):
        day = (base + datetime.timedelta(days=d)).isoformat()
        pids = random.sample(range(1, POOL + 1), PER_DAY)
        rows = [("douyin", f"P{p:06d}", day, 1750000000 + d * 86400,
                 f"加长臂这么选第{p}期"[:40], f"A{p % 3000:05d}", f"作者{p % 3000}",
                 random.randint(100, 900000), random.randint(0, 300000),
                 random.randint(0, 8000), random.randint(0, 30000),
                 random.randint(0, 5000), random.randint(0, 8000000),
                 30, 1750000000 + p, "https://www.douyin.com/video/%d" % p, "search")
                for p in pids]
        c.executemany("INSERT OR REPLACE INTO post_snap VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        c.commit()
    arows = []
    for d in range(N_DAYS):
        day = (base + datetime.timedelta(days=d)).isoformat()
        arows += [("douyin", f"A{a:05d}", day, 1750000000, f"作者{a}",
                   random.randint(100, 900000), "home") for a in range(3000)]
    c.executemany("INSERT OR REPLACE INTO author_snap VALUES (?,?,?,?,?,?,?)", arows)
    c.commit()
    n = c.execute("SELECT COUNT(*) FROM post_snap").fetchone()[0]
    c.close()
    print(f"建库完成：{n} 行  {(time.time()-t0):.0f}s  {os.path.getsize(PATH)/1048576:.0f}MB")


def _ms(fn, rounds=5):
    ts = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return min(ts), statistics.median(ts)


def _plan(c, q, a):
    return [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + q, a)]


def _measure_old(c):
    """在指定索引状态下测旧 SQL（不含派生表）"""
    out = {}
    for k, q in OLD_QUERIES.items():
        if q is None:
            def stats_old():
                for s in ("SELECT COUNT(*) FROM post_snap",
                          "SELECT COUNT(*) FROM (SELECT 1 FROM post_snap GROUP BY platform,post_id)",
                          "SELECT COUNT(*) FROM (SELECT platform,author_id FROM post_snap WHERE author_id<>''"
                          " UNION SELECT platform,author_id FROM author_snap WHERE author_id<>'')",
                          "SELECT COUNT(*) FROM (SELECT 1 FROM post_snap GROUP BY day)",
                          "SELECT MIN(day),MAX(day) FROM post_snap"):
                    c.execute(s).fetchall()
            out[k] = _ms(stats_old)
        else:
            out[k] = _ms(lambda q=q: c.execute(q[0], q[1]).fetchall())
    return out


def run_old():
    """两档对照：
       ① 原始索引（platform, author_id）—— 改造前真实状态
       ② 只换新复合索引（platform, author_id, day, author_fans）—— 「只改索引不改架构」能到哪
    """
    c = sqlite3.connect(PATH)
    res = {}
    # ① 原始状态
    c.execute("DROP INDEX IF EXISTS idx_post_auth_day")
    c.execute("CREATE INDEX IF NOT EXISTS idx_post_auth ON post_snap(platform, author_id)")
    c.commit()
    res["原始状态(旧架构)"] = _measure_old(c)
    plans_old = {k: _plan(c, q[0], q[1]) for k, q in OLD_QUERIES.items() if q}
    # ② 只换索引
    c.execute("DROP INDEX IF EXISTS idx_post_auth")
    c.execute("CREATE INDEX IF NOT EXISTS idx_post_auth_day"
              " ON post_snap(platform, author_id, day, author_fans)")
    c.commit()
    res["只换索引(旧架构)"] = _measure_old(c)
    c.close()
    return res, plans_old


def run_new():
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, ROOT)
    from justoneapi_collector import history_store as hs
    hs._DB_PATH = PATH
    hs._reset_conn()
    t0 = time.perf_counter()
    st = hs.stats()
    mig = (time.perf_counter() - t0) * 1000
    out = {}
    for k, fn in NEW_CASES.items():
        if fn is None:
            out[k] = _ms(lambda: (hs._invalidate_stats(), hs.stats()))
        else:
            out[k] = _ms(lambda fn=fn: fn(hs))
    plans = {}
    c = sqlite3.connect(PATH)
    for name, q, a in [
        ("top_posts", "SELECT platform,post_id,title,author_name,author_fans,digg FROM post_latest"
                      " WHERE last_day>=? ORDER BY digg DESC LIMIT 200", ("2025-11-05",)),
        ("author_posts", "SELECT platform,post_id,title FROM post_latest WHERE last_day>=?"
                         " AND platform=? AND author_id=? ORDER BY digg DESC LIMIT 300",
         ("2025-11-05", "douyin", "A00001")),
        ("tracked_authors", "SELECT platform,author_id,nickname,fans FROM author_latest"
                            " WHERE last_day>=? ORDER BY fans DESC", ("2025-11-05",)),
    ]:
        plans[name] = [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + q, a)]
    n_lat = c.execute("SELECT COUNT(*) FROM post_latest").fetchone()[0]
    n_auth = c.execute("SELECT COUNT(*) FROM author_latest").fetchone()[0]
    n_snap = c.execute("SELECT COUNT(*) FROM post_snap").fetchone()[0]
    c.close()
    return out, plans, mig, (n_snap, n_lat, n_auth), st


if __name__ == "__main__":
    if not os.path.exists(PATH):
        build()
    print("\n" + "=" * 78)
    olds, old_plans = run_old()
    new, new_plans, mig, counts, st = run_new()
    print(f"库：post_snap={counts[0]}  post_latest={counts[1]}  author_latest={counts[2]}")
    print(f"老库首次连接迁移耗时：{mig:.0f}ms（建派生表 + 全量回填，只跑一次）\n")
    a, b = olds["原始状态(旧架构)"], olds["只换索引(旧架构)"]
    print(f"{'查询':22s}{'①改造前':>11s}{'②只换索引':>11s}{'③新架构':>11s}{'①→③':>9s}")
    print("-" * 78)
    for k in a:
        o, i, n = a[k][1], b[k][1], new[k][1]
        print(f"{k:22s}{o:9.1f}ms{i:9.1f}ms{n:9.2f}ms{o/max(n,0.001):8.0f}x")
    print("\n新架构查询计划：")
    for k, v in new_plans.items():
        print(f"  {k}: " + " | ".join(v))
    print("\n改造前查询计划：")
    for k, v in old_plans.items():
        print(f"  {k}: " + " | ".join(v))
    print(f"\n统计口径核对：rows={st['rows']} posts={st['posts']} authors={st['authors']} days={st['days']}")
