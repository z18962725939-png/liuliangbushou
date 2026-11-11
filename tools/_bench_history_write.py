# -*- coding: utf-8 -*-
"""写入吞吐对比（每轮都是全新作品号，避免命中缓存造成的假快）"""
import os, sys, sqlite3, tempfile, time, statistics, random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from justoneapi_collector import history_store as hs

PATH = os.path.join(tempfile.gettempdir(), "bench_real_1m.db")
OLD_UPSERT = """
INSERT OR REPLACE INTO post_snap
    (platform, post_id, day, seen_ts, title, author_id, author_name, author_fans,
     digg, comment_cnt, collect_cnt, share_cnt, play_cnt, duration, publish_ts, url, source)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""
_seq = [0]

def mk_rows(n, day):
    _seq[0] += 1
    base = 700000 + _seq[0] * 100000
    return [("douyin", f"PW{base + i:07d}", day, 1762300000, f"写入基准{base + i}",
             f"AW{(base + i) % 3000:05d}", f"作者{(base + i) % 3000}", 12345,
             random.randint(0, 300000), 100, 50, 20, 1000000,
             30, 1762300000, "https://x/y", "bench") for i in range(n)]

def mk_dicts(n, day):
    out = []
    for r in mk_rows(n, day):
        out.append({"platform": r[0], "post_id": r[1], "title": r[4],
                    "author_id": r[5], "author_name": r[6], "author_fans": r[7],
                    "digg": r[8], "comment": r[9], "collect": r[10], "share": r[11],
                    "play": r[12], "duration": r[13], "publish_ts": r[14], "url": r[15],
                    "source": r[16]})
    return out

def measure(fn, rounds=5):
    fn()   # 预热
    ts = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts)

if __name__ == "__main__":
    c = sqlite3.connect(PATH)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    hs._DB_PATH = PATH
    hs._reset_conn()
    hs.stats()
    print(f"{'批量':>6s}{'场景':>12s}{'改造前(仅事实表)':>18s}{'改造后(事实+派生)':>20s}{'开销':>9s}")
    print("-" * 68)
    for n in (50, 500, 2000):
        for tag, day in (("新的一天", "2026-11-05"), ("历史补录", "2026-08-01")):
            o = measure(lambda: (c.executemany(OLD_UPSERT, mk_rows(n, day)), c.commit()))
            new = measure(lambda: hs.snap_posts(mk_dicts(n, day)))
            print(f"{n:>6d}{tag:>12s}{o:15.1f}ms{new:17.1f}ms{new/max(o,0.001):8.1f}x")
    c.close()
