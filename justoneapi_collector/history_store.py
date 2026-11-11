"""
本地历史库：把每次抓到的数据留在自己硬盘上，让「涨了多少」有据可查。

为什么要有它（2026-10-14 对标蝉妈妈 / 飞瓜之后立的）：
    那两家真正值钱的不是"能看到数据"，而是"能看到三个月前的数据"——
    「这个号最近 30 天涨还是跌」「这条视频是不是突然爆了」，全靠历史对比。
    本工具之前每次抓完就丢，永远只有当下这一帧，所以只能当「取款机」，当不了「账本」。
    有了一本机的时间序列，才能支撑：自己的账号看板、发布后表现曲线、定时监控。
    它同时也是我们唯一有、而云端 SaaS 结构性给不了的东西：**数据不出本机**。

设计取舍：
    - **粒度＝每天一个数据点**。同一天反复抓同一条，只在库里覆盖成最新值，
      不会把库撑爆；隔几天再抓就多一个点，趋势自己就出来了。
    - **零第三方依赖**，只用标准库 sqlite3。
    - **任何异常一律吞掉并静默返回**。落库是附加价值，绝不能因为数据库
      坏了把采集拖垮 —— 这是硬约束，写本模块每一行都要守。
    - **缺失 sqlite 时整体降级为空操作**：某些打包环境可能没带 sqlite 动态库，
      那时本模块所有写 读接口安全返回空，主程序照常跑。

架构（2026-11-05 重构，v2 —— 详见 docs/数据库架构优化.md）：
    问：「昨天 500 赞、今天 800 赞」存几张表最合适？
    答：**两张，而且必须分家**。原来只有 post_snap（每天一行）一张表，
        于是「看板要的每作品一行」只能靠 GROUP BY 现算 —— 90 万行时
        一次刷新要 300~400ms，且随行数超线性恶化（TEM P B-TREE 落盘）。
        现在的分层是标准的「事实表 + 物化视图」：

        post_snap   / author_snap    事实表：一天一行，只追加，画曲线用
        post_latest / author_latest  派生表：一个作品/账号一行，看板/清单用

    派生表在**写入的同一个事务里**增量维护（UPSERT），不需要定时刷新，
    也不会出现「曲线新、看板旧」的不一致。派生表随时可由事实表重建
    （rebuild_derived），所以它只是缓存，不是真相来源 —— 这一点很重要：
    派生表算错了，重建一次就回来了；事实表丢了，历史就真没了。

    读路径因此全部变成「按索引取一批行」，不再有全表分组：
        看板爆款库   O(log n) 索引定位 + LIMIT
        账号作品     (platform, author_id, digg DESC) 复合索引，零排序
        账号清单     author_latest 单表扫描，O(账号数)
"""

from __future__ import annotations

import os
import re
import threading
import time
import weakref
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

try:                                                # pragma: no cover - 环境相关
    import sqlite3
    _HAS_SQLITE = True
except Exception:                                   # noqa: BLE001
    sqlite3 = None                                  # type: ignore[assignment]
    _HAS_SQLITE = False

DB_NAME = "history.db"
SCHEMA_VERSION = 2          # 派生表引入版；小于它的库会在首次连接时自动回填

# 写锁：SQLite 同一时刻只允许一个写事务。读不加锁（WAL 下读写互不阻塞）。
_LOCK = threading.RLock()
_WRITE_LOCK = _LOCK
# 每个线程一条连接：GUI 前台读不再被后台采集的写卡住（原来共用一个连接 + 一把锁，
# 采集写库的那几百毫秒里，界面刷新是排队等着的）。
_LOCAL = threading.local()
_CONNS: "weakref.WeakValueDictionary[int, Any]" = weakref.WeakValueDictionary()
_ENABLED = True
_DB_PATH = None            # 路径覆盖（自检脚本指向临时库，平时为 None）

# stats() 结果缓存：看板每次切回来都会问一次，而它要扫全表数行数。
# 写入 / 清理会立刻让它失效，TTL 只是兜底。
_STATS_CACHE: dict[str, Any] = {"ts": 0.0, "val": None}
_STATS_TTL = 1.5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS post_snap (
    platform     TEXT    NOT NULL,
    post_id      TEXT    NOT NULL,
    day          TEXT    NOT NULL,
    seen_ts      INTEGER NOT NULL DEFAULT 0,
    title        TEXT    DEFAULT '',
    author_id    TEXT    DEFAULT '',
    author_name  TEXT    DEFAULT '',
    author_fans  INTEGER DEFAULT 0,
    digg         INTEGER DEFAULT 0,
    comment_cnt  INTEGER DEFAULT 0,
    collect_cnt  INTEGER DEFAULT 0,
    share_cnt    INTEGER DEFAULT 0,
    play_cnt     INTEGER DEFAULT 0,
    duration     INTEGER DEFAULT 0,
    publish_ts   INTEGER DEFAULT 0,
    url          TEXT    DEFAULT '',
    source       TEXT    DEFAULT '',
    PRIMARY KEY (platform, post_id, day)
);
CREATE INDEX IF NOT EXISTS idx_post_day  ON post_snap(day);

CREATE TABLE IF NOT EXISTS author_snap (
    platform    TEXT    NOT NULL,
    author_id   TEXT    NOT NULL,
    day         TEXT    NOT NULL,
    seen_ts     INTEGER NOT NULL DEFAULT 0,
    nickname    TEXT    DEFAULT '',
    fans        INTEGER DEFAULT 0,
    source      TEXT    DEFAULT '',
    PRIMARY KEY (platform, author_id, day)
);
CREATE INDEX IF NOT EXISTS idx_author_day ON author_snap(day);

-- 派生表①：一个作品一行（看板 / 爆款库 / 账号作品都读它）
CREATE TABLE IF NOT EXISTS post_latest (
    platform     TEXT    NOT NULL,
    post_id      TEXT    NOT NULL,
    last_day     TEXT    NOT NULL,
    seen_ts      INTEGER NOT NULL DEFAULT 0,
    title        TEXT    DEFAULT '',
    author_id    TEXT    DEFAULT '',
    author_name  TEXT    DEFAULT '',
    author_fans  INTEGER DEFAULT 0,
    digg         INTEGER DEFAULT 0,
    comment_cnt  INTEGER DEFAULT 0,
    collect_cnt  INTEGER DEFAULT 0,
    share_cnt    INTEGER DEFAULT 0,
    play_cnt     INTEGER DEFAULT 0,
    duration     INTEGER DEFAULT 0,
    publish_ts   INTEGER DEFAULT 0,
    url          TEXT    DEFAULT '',
    source       TEXT    DEFAULT '',
    snap_days    INTEGER DEFAULT 1,          -- 累计观察天数（不是范围天数）
    PRIMARY KEY (platform, post_id)
);
CREATE INDEX IF NOT EXISTS idx_latest_digg ON post_latest(digg DESC);
CREATE INDEX IF NOT EXISTS idx_latest_last ON post_latest(last_day);
CREATE INDEX IF NOT EXISTS idx_latest_auth ON post_latest(platform, author_id, digg DESC);

-- 派生表②：一个账号一行（账号清单读它，不再现算日期并集）
CREATE TABLE IF NOT EXISTS author_latest (
    platform    TEXT    NOT NULL,
    author_id   TEXT    NOT NULL,
    nickname    TEXT    DEFAULT '',
    fans        INTEGER DEFAULT 0,
    fans_day    TEXT    DEFAULT '',          -- fans 这个值是哪天的
    fans_exact  INTEGER DEFAULT 0,           -- 1=查过主页的精确值，0=挖作品顺带的粗估
    last_day    TEXT    DEFAULT '',
    snap_days   INTEGER DEFAULT 1,
    source      TEXT    DEFAULT '',
    PRIMARY KEY (platform, author_id)
);
CREATE INDEX IF NOT EXISTS idx_authlatest_fans ON author_latest(fans DESC);
CREATE INDEX IF NOT EXISTS idx_authlatest_day  ON author_latest(last_day);

-- 计数物化表：看板顶部两张卡要「共多少条快照 / 记录了多少天」，
-- 在大表上每次 COUNT(*) + COUNT(DISTINCT day) 要扫 100 万行（88ms，
-- 相当于界面掉 5 帧）。改成写入时增量累加，读的时候是一次主键查找。
CREATE TABLE IF NOT EXISTS _meta (
    k TEXT PRIMARY KEY,
    v INTEGER NOT NULL DEFAULT 0
);
"""

# 索引升级单独跑：DROP 在坏库 / 老库上可能失败，不能让它把后面的建表一起带停。
_INDEX_UPGRADE = """
DROP INDEX IF EXISTS idx_post_auth;
CREATE INDEX IF NOT EXISTS idx_post_auth_day
    ON post_snap(platform, author_id, day, author_fans);
"""


# ------------------------------------------------------------------ 基础工具

def db_path() -> Path:
    """历史库文件位置（默认 %APPDATA%/TrafficCatcher/history.db）。

    `_DB_PATH` 为空时才走默认目录 —— 留这个口子是为了自检脚本能把库指到
    临时目录，不污染真实数据（也方便日后支持"换个盘存历史"）。
    """
    if _DB_PATH:
        # 外面可能塞进来字符串，一律转成 Path —— 否则调用方的 p.exists()
        # 会抛 AttributeError，被下游容错吞成"库大小 0"，看不出原因。
        return Path(_DB_PATH)
    try:
        from . import branding
        return Path(branding.app_data_dir()) / DB_NAME
    except Exception:                               # noqa: BLE001
        root = os.environ.get("APPDATA") or str(Path.home())
        return Path(root) / "TrafficCatcher" / DB_NAME


def available() -> bool:
    """本机是否真的能用 SQLite（缺失时整个模块降级为空操作）。"""
    return _HAS_SQLITE


def enabled() -> bool:
    """自动记录开关。关掉后所有 snap_* 直接返回 0，读接口仍可用。"""
    return _ENABLED and _HAS_SQLITE


def set_enabled(on: bool) -> None:
    """开关只影响写入，不影响读取（关了也还能回看已有数据）。"""
    global _ENABLED
    _ENABLED = bool(on)


def _today(ts: float | None = None) -> str:
    """取**本地**日期 YYYY-MM-DD。

    刻意不做 UTC 偏移换算 —— 用户心里的"今天"就是北京时间，
    按 UTC 算会出现凌晨抓的数据被记到昨天的怪现象。
    """
    d = date.fromtimestamp(ts if ts is not None else time.time())
    return d.isoformat()


def _day_offset(days: int, base: str = "") -> str:
    """days 天前那一天（负数＝往前推）。base 用于自检/回放，默认今天。"""
    d = date.fromisoformat(base) if base else date.today()
    return (d + timedelta(days=days)).isoformat()


_UNITS = {"万": 10000, "w": 10000, "W": 10000, "k": 1000, "K": 1000, "亿": 100000000}


def _to_int(v: Any) -> int:
    """把各种写法都收成整数。

    采集层返回的值不老实：有时候是 12000，有时候是 "1.2万"，
    偶尔还带逗号或空格。这里统一收口，宁可记 0 也不让一条脏数据
    把整次落库带崩（历史库坏了不影响采集，但记录错了没人会发现）。
    """
    try:
        if v is None:
            return 0
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, (int, float)):
            return int(v)
        s = str(v).strip().replace(",", "").replace(" ", "").replace("　", "")
        if not s:
            return 0
        try:
            return int(float(s))
        except Exception:                           # noqa: BLE001
            pass
        m = re.match(r"^([\d.]+)\s*([万wWkK亿])\+?$", s)
        if m:
            return int(float(m.group(1)) * _UNITS[m.group(2)])
        m2 = re.search(r"[\d.]+", s)
        if m2:
            try:
                return int(float(m2.group()))
            except Exception:                       # noqa: BLE001
                return 0
        return 0
    except Exception:                               # noqa: BLE001
        return 0


def _to_text(v: Any, limit: int = 200) -> str:
    """统一转字符串并截断：标题再长也不该把库撑大。"""
    try:
        s = "" if v is None else str(v)
    except Exception:                               # noqa: BLE001
        s = ""
    return s.replace("\r", " ").replace("\n", " ").strip()[:limit]


def _getter(obj):
    """统一 dict / 对象的取值方式。"""
    if isinstance(obj, dict):
        return lambda k, d="": obj.get(k, d)
    return lambda k, d="": (lambda v: d if v is None else v)(getattr(obj, k, None))


# ------------------------------------------------------------------ 连接管理

def _tune(conn) -> None:
    """连接级 PRAGMA。**每一条都单独吞异常** —— 某些精简版 SQLite 编译时
    关掉了 mmap，整块 try 会让后面本来能生效的设置一起失效。"""
    settings = (
        ("busy_timeout", "8000"),        # 多线程/多进程抢写时等一会儿，别直接报错
        ("cache_size", "-16384"),        # 16MB 页缓存（默认 2MB，聚合查询会频繁换页）
        ("temp_store", "2"),             # 临时表/排序放内存，杜绝 TEMP B-TREE 落盘
        ("mmap_size", "268435456"),      # 256MB 内存映射，读大表少一次拷贝
        ("synchronous", "1"),            # NORMAL：WAL 下足够安全，写入快一个数量级
        ("wal_autocheckpoint", "1000"),  # 每 1000 页回写一次，避免 WAL 无限膨胀
    )
    for pragma, value in settings:
        try:
            conn.execute(f"PRAGMA {pragma}={value}")
        except Exception:                           # noqa: BLE001
            pass


def _new_conn(path: str):
    conn = sqlite3.connect(path, timeout=15, check_same_thread=False)
    # WAL：读写不互相排队，GUI 后台线程写库时不卡界面
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:                               # noqa: BLE001
        pass
    _tune(conn)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    except Exception:                               # noqa: BLE001
        pass
    try:
        conn.executescript(_INDEX_UPGRADE)
        conn.commit()
    except Exception:                               # noqa: BLE001
        pass
    try:
        _ensure_meta(conn)
    except Exception:                               # noqa: BLE001
        pass
    try:
        _migrate(conn)
    except Exception:                               # noqa: BLE001
        pass
    return conn


def _ensure_meta(conn) -> None:
    """计数器表首次初始化：只在表里没有 rows/days 这两行时算一次。

    老库（有数据但没有 _meta）升级时会走这里，做一次全量统计把基数定下来，
    之后全靠写入时累加。
    """
    have = {r[0] for r in conn.execute("SELECT k FROM _meta")}
    if "rows" in have and "days" in have:
        return
    rows = int(conn.execute("SELECT COUNT(*) FROM post_snap").fetchone()[0] or 0)
    days = int(conn.execute("SELECT COUNT(DISTINCT day) FROM post_snap").fetchone()[0] or 0)
    conn.executemany("INSERT OR REPLACE INTO _meta(k, v) VALUES (?,?)",
                     [("rows", rows), ("days", days)])
    conn.commit()


def _migrate(conn) -> None:
    """老库升级：把事实表里已有的行回填进派生表，只跑一次。

    用 `PRAGMA user_version` 记版本 —— 老库的 user_version 是 0，
    所以升级后重装/降级都不会重复回填（回填 90 万行要几秒，不能每次开都跑）。
    """
    ver = 0
    try:
        ver = int(conn.execute("PRAGMA user_version").fetchone()[0])
    except Exception:                               # noqa: BLE001
        return
    if ver >= SCHEMA_VERSION:
        return
    rebuild_derived(conn=conn)
    try:
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
    except Exception:                               # noqa: BLE001
        pass


def _conn():
    """取得当前线程的连接；路径变了就重建（自检会把库在好库/坏库之间来回切）。"""
    key = str(db_path())
    conn = getattr(_LOCAL, "conn", None)
    if conn is not None and getattr(_LOCAL, "key", "") == key:
        return conn
    if conn is not None:
        try:
            conn.close()
        except Exception:                           # noqa: BLE001
            pass
    p = Path(key)
    if p.parent and not p.parent.exists():
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception:                           # noqa: BLE001
            pass
    conn = _new_conn(key)
    _LOCAL.conn, _LOCAL.key = conn, key
    try:
        _CONNS[threading.get_ident()] = conn
    except Exception:                               # noqa: BLE001
        pass
    return conn


# 兼容旧名（自检/外部脚本可能直接用 _connect(str(db_path()))）
def _connect(key: str = ""):
    return _conn()


def _reset_conn() -> None:
    """关闭所有线程的连接，下次调用自动重连（损坏恢复 / 测试隔离用）。"""
    for ident in list(getattr(_CONNS, "keys", lambda: [])()):
        c = _CONNS.get(ident)
        if c is not None:
            try:
                c.close()
            except Exception:                       # noqa: BLE001
                pass
        try:
            _CONNS.pop(ident, None)
        except Exception:                           # noqa: BLE001
            pass
    conn = getattr(_LOCAL, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:                       # noqa: BLE001
            pass
    _LOCAL.conn, _LOCAL.key = None, ""
    _invalidate_stats()


def _query(sql: str, args: tuple = ()) -> list[tuple]:
    """只读查询。任何失败都返回空列表，绝不抛给调用方。

    不加全局锁：WAL 下读与写互不阻塞，而 GUI 会在前台线程里读。
    """
    if not _HAS_SQLITE:
        return []
    try:
        conn = _conn()
        cur = conn.execute(sql, args)
        rows = cur.fetchall()
        cur.close()
        return rows
    except Exception:                               # noqa: BLE001
        return []


def _invalidate_stats() -> None:
    _STATS_CACHE["ts"] = 0.0
    _STATS_CACHE["val"] = None


# ------------------------------------------------------------------ 写入语句
# 同一天重复抓 = 覆盖（UPSERT 而不是 INSERT OR REPLACE）：
# REPLACE 是先 DELETE 再 INSERT，会把主键索引和两条二级索引都删一遍再插一遍，
# 长期反复抓同一批作品就在库里留下大量半空的页 —— 这是老库越用越慢的隐形原因之一。

_UPSERT_POST = """
INSERT INTO post_snap
    (platform, post_id, day, seen_ts, title, author_id, author_name, author_fans,
     digg, comment_cnt, collect_cnt, share_cnt, play_cnt, duration, publish_ts, url, source)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(platform, post_id, day) DO UPDATE SET
    seen_ts=excluded.seen_ts, title=excluded.title,
    author_id=excluded.author_id, author_name=excluded.author_name,
    author_fans=excluded.author_fans, digg=excluded.digg,
    comment_cnt=excluded.comment_cnt, collect_cnt=excluded.collect_cnt,
    share_cnt=excluded.share_cnt, play_cnt=excluded.play_cnt,
    duration=excluded.duration, publish_ts=excluded.publish_ts,
    url=excluded.url, source=excluded.source
"""

# 派生表①：前 17 个参数与 _UPSERT_POST 完全一致，直接复用同一批 tuple；
# 第 18 个参数是「这一天是不是头一回见到」（0/1），由写之前的一次批量查决定。
#
# 为什么不用 `WHERE excluded.last_day >= last_day` 挡晚到的旧数据：
# 挡掉之后 snap_days 就涨不上去了 —— 补录昨天的数据属于"新的一天"，
# 列不该覆盖、天数却该 +1，两件事必须分开处理，所以改成逐列 CASE 保护。
_UPSERT_POST_LATEST = """
INSERT INTO post_latest
    (platform, post_id, last_day, seen_ts, title, author_id, author_name, author_fans,
     digg, comment_cnt, collect_cnt, share_cnt, play_cnt, duration, publish_ts, url, source,
     snap_days)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
ON CONFLICT(platform, post_id) DO UPDATE SET
    snap_days=post_latest.snap_days + ?,
    last_day=MAX(post_latest.last_day, excluded.last_day),
    seen_ts=CASE WHEN excluded.last_day >= post_latest.last_day
                 THEN excluded.seen_ts ELSE post_latest.seen_ts END,
    title=CASE WHEN excluded.last_day >= post_latest.last_day
               THEN excluded.title ELSE post_latest.title END,
    author_id=CASE WHEN excluded.last_day >= post_latest.last_day
                   THEN excluded.author_id ELSE post_latest.author_id END,
    author_name=CASE WHEN excluded.last_day >= post_latest.last_day
                     THEN excluded.author_name ELSE post_latest.author_name END,
    author_fans=CASE WHEN excluded.last_day >= post_latest.last_day
                     THEN excluded.author_fans ELSE post_latest.author_fans END,
    digg=CASE WHEN excluded.last_day >= post_latest.last_day
              THEN excluded.digg ELSE post_latest.digg END,
    comment_cnt=CASE WHEN excluded.last_day >= post_latest.last_day
                     THEN excluded.comment_cnt ELSE post_latest.comment_cnt END,
    collect_cnt=CASE WHEN excluded.last_day >= post_latest.last_day
                     THEN excluded.collect_cnt ELSE post_latest.collect_cnt END,
    share_cnt=CASE WHEN excluded.last_day >= post_latest.last_day
                   THEN excluded.share_cnt ELSE post_latest.share_cnt END,
    play_cnt=CASE WHEN excluded.last_day >= post_latest.last_day
                  THEN excluded.play_cnt ELSE post_latest.play_cnt END,
    duration=CASE WHEN excluded.last_day >= post_latest.last_day
                  THEN excluded.duration ELSE post_latest.duration END,
    publish_ts=CASE WHEN excluded.last_day >= post_latest.last_day
                    THEN excluded.publish_ts ELSE post_latest.publish_ts END,
    url=CASE WHEN excluded.last_day >= post_latest.last_day
             THEN excluded.url ELSE post_latest.url END,
    source=CASE WHEN excluded.last_day >= post_latest.last_day
                THEN excluded.source ELSE post_latest.source END
"""

_UPSERT_AUTHOR = """
INSERT INTO author_snap
    (platform, author_id, day, seen_ts, nickname, fans, source)
VALUES (?,?,?,?,?,?,?)
ON CONFLICT(platform, author_id, day) DO UPDATE SET
    seen_ts=excluded.seen_ts, nickname=excluded.nickname,
    fans=excluded.fans, source=excluded.source
"""

# 派生表②：昵称/粉丝/来源的取值规则是**精度优先，其次日期新**：
#   查过主页的精确值(exact=1)永远压过挖作品顺带的粗估值(exact=0)，
#   哪怕粗估是更晚的一天抓的。
# 为什么不是「谁新用谁」：老实现里这条被写死成「作者表优先」，放开之后
# 就会出现「今天顺带抓到 10905，把前天主页上 58005 的精确粉丝数顶掉」——
# 用户看到的是粉丝暴跌 4 万，实际上什么也没发生。等价性校验正是这么抓出来的。
_PREFER_NEW = ("(excluded.fans_exact > author_latest.fans_exact"
               " OR (excluded.fans_exact = author_latest.fans_exact"
               "     AND excluded.last_day >= author_latest.last_day))")

# 末尾多一个参数：这一天是不是头一回见到（两表并集口径），用来给 snap_days 增一。
_UPSERT_AUTHOR_LATEST = f"""
INSERT INTO author_latest
    (platform, author_id, nickname, fans, fans_day, fans_exact, last_day, snap_days, source)
VALUES (?,?,?,?,?,?,?,1,?)
ON CONFLICT(platform, author_id) DO UPDATE SET
    snap_days=author_latest.snap_days + ?,
    nickname=CASE WHEN excluded.nickname='' THEN author_latest.nickname
                  WHEN {_PREFER_NEW} THEN excluded.nickname
                  ELSE author_latest.nickname END,
    fans=CASE WHEN excluded.fans<=0 THEN author_latest.fans
              WHEN {_PREFER_NEW} THEN excluded.fans
              ELSE author_latest.fans END,
    fans_exact=CASE WHEN excluded.fans>0 AND {_PREFER_NEW}
                    THEN excluded.fans_exact ELSE author_latest.fans_exact END,
    fans_day=CASE WHEN excluded.fans>0 AND {_PREFER_NEW}
                  THEN excluded.fans_day ELSE author_latest.fans_day END,
    last_day=MAX(author_latest.last_day, excluded.last_day),
    source=CASE WHEN {_PREFER_NEW} THEN excluded.source ELSE author_latest.source END
"""


def _seen_post_days(conn, rows) -> set:
    """本批里哪些 (platform, post_id) 在 post_snap 里已经有这一天的记录。

    必须**在插入之前**问。一批 500 条只需要 1~2 条 SQL（按 (platform, day) 分组
    后 IN 查主键），不是逐条 EXISTS —— 逐条查在 500 条时会多出 500 次往返。
    """
    seen: set = set()
    if not rows:
        return seen
    day = rows[0][2]
    groups: dict[str, set] = {}
    for r in rows:
        groups.setdefault(r[0], set()).add(r[1])
    for plat, pids in groups.items():
        pids = list(pids)
        for i in range(0, len(pids), 800):
            chunk = pids[i:i + 800]
            sql = ("SELECT post_id FROM post_snap"
                   " WHERE platform=? AND day=? AND post_id IN (%s)"
                   % ",".join("?" * len(chunk)))
            try:
                for (pid,) in conn.execute(sql, (plat, day) + tuple(chunk)):
                    seen.add((plat, pid))
            except Exception:                       # noqa: BLE001
                return set()                        # 查不动就当全都"见过"，宁可少算
    return seen


def _seen_author_days(conn, rows) -> set:
    """账号的「这一天见过没有」要取两表并集：挖作品、查主页都算记录了一天。"""
    seen: set = set()
    if not rows:
        return seen
    day = rows[0][2]
    groups: dict[str, set] = {}
    for r in rows:
        groups.setdefault(r[0], set()).add(r[1])
    for table, col in (("author_snap", "author_id"), ("post_snap", "author_id")):
        for plat, aids in groups.items():
            aids = list(aids)
            for i in range(0, len(aids), 800):
                chunk = aids[i:i + 800]
                sql = ("SELECT %s FROM %s WHERE platform=? AND day=? AND %s IN (%s)"
                       % (col, table, col, ",".join("?" * len(chunk))))
                try:
                    for (aid,) in conn.execute(sql, (plat, day) + tuple(chunk)):
                        seen.add((plat, aid))
                except Exception:                   # noqa: BLE001
                    return set()
    return seen


def _write_batch(post_rows: list[tuple], author_rows: list[tuple]) -> int:
    """一个事务里写事实表 + 派生表。

    **必须同一个事务**：否则中途崩溃会留下「曲线有、看板没有」的库，
    而那种不一致没有任何界面能提示用户。

    「这一天见过没有」必须在插入前批量问一遍（见 _seen_post_days 的注释），
    这也是整个写入路径上唯一多出来的一次查询 —— 换来的是 snap_days 永远精确。
    """
    if not _HAS_SQLITE:
        return 0
    total = len(post_rows) + len(author_rows)
    if not total:
        return 0
    with _WRITE_LOCK:
        try:
            conn = _conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                if post_rows:
                    seen = _seen_post_days(conn, post_rows)
                    new_rows = len({(r[0], r[1]) for r in post_rows}) - len(seen)
                    day0 = post_rows[0][2]
                    day_is_new = 0
                    if new_rows > 0:
                        # 这一批的日期在库里是不是第一次出现（插入之前问）
                        row = conn.execute(
                            "SELECT 1 FROM post_snap WHERE day=? LIMIT 1", (day0,)).fetchone()
                        day_is_new = 0 if row else 1
                    # 作品里顺带带回来的账号也要进账号派生表（fans_exact=0 的粗估来源）。
                    # 漏了这一步的后果很隐蔽：只挖作品、没查过主页的号会从清单里消失，
                    # 而界面上什么错都不报 —— 自检里原来正是这一条先炸的。
                    derived: dict[tuple, tuple] = {}
                    for r in post_rows:
                        aid = r[5]
                        if not aid:
                            continue
                        key = (r[0], aid)
                        prev = derived.get(key)
                        if prev is None or r[7] > prev[1]:
                            derived[key] = (r[6], r[7])
                    pseudo = [(p, a, day0, 0, "", 0, "") for (p, a) in derived]
                    seen_a = _seen_author_days(conn, pseudo) if pseudo else set()
                    conn.executemany(_UPSERT_POST, post_rows)
                    conn.executemany(
                        _UPSERT_POST_LATEST,
                        [r + (0 if (r[0], r[1]) in seen else 1,) for r in post_rows])
                    if derived:
                        conn.executemany(
                            _UPSERT_AUTHOR_LATEST,
                            [(p, a, name, fans, day0, 0, day0, "",
                              0 if (p, a) in seen_a else 1)
                             for (p, a), (name, fans) in derived.items()])
                    # 计数器跟数据在同一个事务里改，绝不会出现「卡片和列表对不上」
                    if new_rows:
                        conn.execute("UPDATE _meta SET v=v+? WHERE k='rows'", (new_rows,))
                    if day_is_new:
                        conn.execute("UPDATE _meta SET v=v+1 WHERE k='days'")
                if author_rows:
                    seen_b = _seen_author_days(conn, author_rows)
                    conn.executemany(_UPSERT_AUTHOR, author_rows)
                    conn.executemany(
                        _UPSERT_AUTHOR_LATEST,
                        [(r[0], r[1], r[4], r[5], r[2], 1, r[2], r[6],
                          0 if (r[0], r[1]) in seen_b else 1) for r in author_rows])
                conn.commit()
            except Exception:                       # noqa: BLE001
                try:
                    conn.rollback()
                except Exception:                   # noqa: BLE001
                    pass
                raise
            _invalidate_stats()
            return total
        except Exception:                           # noqa: BLE001
            # 库损坏 / 磁盘满 / 文件被占用都不该影响主流程，下次重试会自愈
            try:
                _reset_conn()
            except Exception:                       # noqa: BLE001
                pass
            return 0


# ------------------------------------------------------------------ 写入接口

def snap_posts(posts, source: str = "") -> int:
    """记录一批作品快照。

    接受 PostItem 或任何带同名字段的对象，也接受 dict。
    返回实际写入行数（返回 0 不一定代表失败，也可能是输入为空）。
    """
    if not enabled():
        return 0
    now = int(time.time())
    day = _today(now)
    rows = [r for r in (_post_row(p, day, now, source) for p in (posts or [])) if r]
    return _write_batch(rows, []) if rows else 0


def _post_row(p, day: str, now: int, source: str) -> tuple | None:
    """把一个作品摊平成一行。**异常只丢这一行**，不影响同批其它行。"""
    try:
        g = _getter(p)
        post_id = _to_text(g("post_id"), 64)
        if not post_id:
            return None
        return (
            _to_text(g("platform"), 24),
            post_id,
            day,
            now,
            _to_text(g("title"), 200),
            _to_text(g("author_id"), 64),
            _to_text(g("author_name"), 64),
            _to_int(g("author_fans")),
            _to_int(g("digg")),
            _to_int(g("comment")),
            _to_int(g("collect")),
            _to_int(g("share")),
            _to_int(g("play")),
            _to_int(g("duration")),
            _to_int(g("publish_ts")),
            _to_text(g("url"), 500),
            _to_text(source or g("source"), 32),
        )
    except Exception:                               # noqa: BLE001
        return None


def snap_authors(items, source: str = "") -> int:
    """记录一批账号快照（AuthorInfo / dict 均可）。"""
    if not enabled():
        return 0
    now = int(time.time())
    day = _today(now)
    rows = [r for r in (_author_row(a, day, now, source) for a in (items or [])) if r]
    return _write_batch([], rows) if rows else 0


def _author_row(a, day: str, now: int, source: str) -> tuple | None:
    try:
        g = _getter(a)
        aid = _to_text(g("author_id") or g("user_id"), 64)
        if not aid:
            return None
        fans = g("fans", None)
        if fans is None:
            fans = g("author_fans", 0)
        return (
            _to_text(g("platform"), 24),
            aid,
            day,
            now,
            _to_text(g("nickname") or g("author_name"), 64),
            _to_int(fans),
            _to_text(source or g("source"), 32),
        )
    except Exception:                               # noqa: BLE001
        return None


def snap_post(post, source: str = "") -> int:
    """记一条作品（单条场景的便捷入口）。"""
    return snap_posts([post], source=source)


def snap_author(author, source: str = "") -> int:
    """记一个账号（单条场景的便捷入口）。"""
    return snap_authors([author], source=source)


# ------------------------------------------------------------------ 读取接口

def post_trend(platform: str, post_id: str, days: int = 120) -> list[dict]:
    """某条作品随时间的数据变化，按天升序。"""
    since = _day_offset(-max(1, int(days)))
    rows = _query(
        "SELECT day, seen_ts, title, author_name, author_fans, digg, comment_cnt,"
        " collect_cnt, share_cnt, play_cnt, url"
        "  FROM post_snap WHERE platform=? AND post_id=? AND day>=?"
        "  ORDER BY day ASC, seen_ts ASC",
        (platform, post_id, since),
    )
    out: list[dict] = []
    for day, ts, title, author, fans, digg, cmt, col, shr, play, url in rows:
        digg, cmt, col, shr = _to_int(digg), _to_int(cmt), _to_int(col), _to_int(shr)
        out.append({
            "day": day, "ts": _to_int(ts), "title": title, "author": author,
            "fans": _to_int(fans), "digg": digg, "comment": cmt, "collect": col,
            "share": shr, "play": _to_int(play), "interact": digg + cmt + col + shr,
            "url": url,
        })
    return out


def author_trend(platform: str, author_id: str, days: int = 120) -> list[dict]:
    """某账号的粉丝增长曲线。

    两个来源合并（同一天以 author_snap 为准）：
      1) author_snap —— 单独查过主页时的精确值；
      2) post_snap   —— 挖作品时顺带带回来的 author_fans。
    第二个来源很关键：**用户绝大多数时候只是在挖作品**，
    若只认 author_snap，粉丝曲线会长期是空的。

    第二条查询现在走 (platform, author_id, day, author_fans) 覆盖索引，
    不用回表 —— 这是改造前 13ms、改造后亚毫秒的那一处。
    """
    since = _day_offset(-max(1, int(days)))
    precise = {r[0]: r[1] for r in _query(
        "SELECT day, fans FROM author_snap"
        " WHERE platform=? AND author_id=? AND day>=? ORDER BY day, seen_ts",
        (platform, author_id, since))}
    coarse: dict[str, int] = {}
    for day, fans in _query(
            "SELECT day, MAX(author_fans) FROM post_snap"
            " WHERE platform=? AND author_id=? AND author_fans>0 AND day>=?"
            " GROUP BY day ORDER BY day",
            (platform, author_id, since)):
        coarse[day] = max(coarse.get(day, 0), _to_int(fans))

    out: list[dict] = []
    for day in sorted(set(precise) | set(coarse)):
        if day in precise:
            out.append({"day": day, "fans": _to_int(precise[day]), "exact": True})
        else:
            out.append({"day": day, "fans": coarse.get(day, 0), "exact": False})
    # 补齐偶发的 0：粉丝数不会是 0（除非接口没给），沿用上一天的值
    prev = 0
    for row in out:
        if row["fans"] <= 0:
            row["fans"], row["exact"] = prev, False
        else:
            prev = row["fans"]
    return out


def tracked_authors(days: int = 365, platform: str = "") -> list[dict]:
    """被记录过的账号清单（按最新粉丝数降序）。

    改造前：两次查「每个账号出现过的每一天」再在 Python 里求并集 ——
    9000 个账号 × 180 天要往内存里搬 160 万行，85ms 起步。
    现在 day 并集与天数在写入时就算好存在 author_latest，这里一次索引扫描搞定。

    「记录天数」仍然是两表日期的**并集**（挖作品的天数也算），
    只是改成增量维护，语义没变。
    """
    since = _day_offset(-max(1, int(days)))
    where = " AND platform=? " if platform else ""
    args = (since,) + ((platform,) if platform else ())
    rows = _query(
        "SELECT platform, author_id, nickname, fans, last_day, snap_days, source"
        "  FROM author_latest WHERE last_day>=? " + where +
        " ORDER BY fans DESC", args)
    out: list[dict] = []
    for plt, aid, nick, fans, last_day, snap_days, src in rows:
        out.append({"platform": plt, "author_id": aid,
                    "nickname": nick or "（未取名）", "fans": _to_int(fans),
                    "last_day": last_day or "",
                    "snap_days": _to_int(snap_days),
                    "source": src or ""})
    return out


def author_posts(platform: str, author_id: str, days: int = 365,
                 limit: int = 300) -> list[dict]:
    """某账号被记录到的作品（每条作品取最新一天的数据）。

    走 idx_latest_auth(platform, author_id, digg DESC)：过滤完直接就是
    点赞降序，省掉改造前那个必然落盘的 TEMP B-TREE 排序。
    """
    since = _day_offset(-max(1, int(days)))
    rows = _query(
        "SELECT platform, post_id, title, author_name, author_fans, digg, comment_cnt,"
        " collect_cnt, share_cnt, play_cnt, url, last_day, snap_days, publish_ts"
        "  FROM post_latest WHERE last_day>=? AND platform=? AND author_id=?"
        "  ORDER BY digg DESC LIMIT ?",
        (since, platform, author_id, int(limit)),
    )
    out: list[dict] = []
    for r in rows:
        digg, cmt, col, shr = _to_int(r[5]), _to_int(r[6]), _to_int(r[7]), _to_int(r[8])
        out.append({
            "platform": r[0], "post_id": r[1], "title": r[2], "author": r[3],
            "fans": _to_int(r[4]), "digg": digg, "comment": cmt, "collect": col,
            "share": shr, "play": _to_int(r[9]), "url": r[10], "last_day": r[11],
            "snap_days": _to_int(r[12]), "publish_ts": _to_int(r[13]),
            "interact": digg + cmt + col + shr,
        })
    return out


_ORDER_COL = {"digg": "digg", "comment": "comment_cnt", "collect": "collect_cnt",
              "share": "share_cnt", "play": "play_cnt", "fans": "author_fans"}


def top_posts(days: int = 365, platform: str = "", order_by: str = "digg",
              limit: int = 100) -> list[dict]:
    """「所有被记录过的作品」里最强的一批 —— 其实就是可用的素材库。

    它不是每次现挖，而是你积累了多久就有多久的存货，属于复用而非重新挖掘。
    改造前这里要 GROUP BY 全表 + 排序（90 万行 329ms）；现在读一行一作品的
    派生表，按索引取前 N 条就停。
    """
    since = _day_offset(-max(1, int(days)))
    col = _ORDER_COL.get(order_by, "digg")
    where = " AND platform=? " if platform else ""
    args = (since,) + ((platform,) if platform else ()) + (int(limit),)
    rows = _query(
        "SELECT platform, post_id, title, author_name, author_fans, digg, comment_cnt,"
        " collect_cnt, share_cnt, play_cnt, url, last_day, snap_days, publish_ts, author_id"
        "  FROM post_latest WHERE last_day>=? " + where +
        f"  ORDER BY {col} DESC LIMIT ?",
        args,
    )
    out: list[dict] = []
    for r in rows:
        digg, cmt, colv, shr = _to_int(r[5]), _to_int(r[6]), _to_int(r[7]), _to_int(r[8])
        out.append({
            "platform": r[0], "post_id": r[1], "title": r[2], "author": r[3],
            "fans": _to_int(r[4]), "digg": digg, "comment": cmt, "collect": colv,
            "share": shr, "play": _to_int(r[9]), "url": r[10], "last_day": r[11],
            "snap_days": _to_int(r[12]), "publish_ts": _to_int(r[13]),
            "author_id": r[14], "interact": digg + cmt + colv + shr,
        })
    return out


def stats() -> dict:
    """库的规模概览（页面顶部几张卡的数据源）。

    **必须读真实数据** —— 绝不允许给写死的示例数，那会让用户误以为已经记录了。
    改造前是 5 条查询、每次切页都重扫全表；现在合成 1 条多列子查询 + 1.5 秒缓存，
    写入/清理会立刻让缓存失效（所以卡片永远跟得上最新一次抓取）。
    """
    now = time.time()
    cached = _STATS_CACHE.get("val")
    if cached is not None and (now - float(_STATS_CACHE.get("ts") or 0)) < _STATS_TTL:
        return dict(cached)
    try:
        # 行数与天数走物化计数器（O(1) 主键查找）；首末日期走索引两端；
        # 作品数/账号数来自派生表（只有作品数、账号数那么多行，本身就很轻）。
        # 兜底：_meta 还没初始化（老库第一次打开）就现场算一次，绝不返回 0 骗人。
        meta = {r[0]: r[1] for r in _query("SELECT k, v FROM _meta")}
        if "rows" in meta and "days" in meta:
            rows_n, days_n = _to_int(meta.get("rows")), _to_int(meta.get("days"))
        else:
            c1 = _query("SELECT COUNT(*) FROM post_snap")
            c2 = _query("SELECT COUNT(DISTINCT day) FROM post_snap")
            rows_n = _to_int(c1[0][0]) if c1 else 0
            days_n = _to_int(c2[0][0]) if c2 else 0
        # MIN(day) 和 MAX(day) 必须分成两条：写成 SELECT MIN(day), MAX(day) 时
        # SQLite 会放弃「索引两端」优化，退化成 100 万行全表扫描（实测 68ms）。
        # 拆开后各走一次 B-tree 末端，成本接近 0。
        lo = _query("SELECT MIN(day) FROM post_snap")
        hi = _query("SELECT MAX(day) FROM post_snap")
        small = _query("SELECT (SELECT COUNT(*) FROM post_latest),"
                       "       (SELECT COUNT(*) FROM author_latest)")
        size = 0
        try:
            p = db_path()
            if p.exists():
                size = p.stat().st_size
        except Exception:                           # noqa: BLE001
            pass
        val = {
            "rows": rows_n,
            "posts": _to_int(small[0][0]) if small else 0,
            "authors": _to_int(small[0][1]) if small else 0,
            "days": days_n,
            "first_day": (lo[0][0] or "") if lo else "",
            "last_day": (hi[0][0] or "") if hi else "",
            "size_bytes": size, "available": _HAS_SQLITE, "enabled": enabled(),
        }
        _STATS_CACHE["ts"] = now
        _STATS_CACHE["val"] = dict(val)
        return val
    except Exception:                               # noqa: BLE001
        return {"rows": 0, "posts": 0, "authors": 0, "days": 0, "first_day": "",
                "last_day": "", "size_bytes": 0, "available": _HAS_SQLITE,
                "enabled": enabled()}


# ------------------------------------------------------------------ 维护接口

def rebuild_derived(conn=None) -> bool:
    """从事实表全量重建派生表（迁移 / 修复 / 自检用）。

    派生表是缓存而不是真相来源，所以「算错了」永远有解：重建一次就回来了。
    90 万行库上约 2~4 秒，只在版本升级或手动修复时跑。
    """
    if not _HAS_SQLITE:
        return False
    own = conn is None
    # 不额外加进程内写锁：DELETE + INSERT 由 sqlite3 的隐式事务包住，
    # 中途失败可以整体回滚，重建本身也不需要和其它写操作互斥。
    try:
        target = conn if conn is not None else _conn()
        target.execute("DELETE FROM post_latest")
        target.execute("DELETE FROM author_latest")
        # ① 作品派生表：GROUP BY 取每组 MAX(day) 那一行的全部列（SQLite 的
        #    bare column 规则：同时带 MAX() 时裸列取自最大行），顺带 COUNT 出天数。
        target.execute(
            "INSERT INTO post_latest (platform, post_id, last_day, seen_ts, title,"
            " author_id, author_name, author_fans, digg, comment_cnt, collect_cnt,"
            " share_cnt, play_cnt, duration, publish_ts, url, source, snap_days)"
            " SELECT platform, post_id, MAX(day), seen_ts, title, author_id, author_name,"
            " author_fans, digg, comment_cnt, collect_cnt, share_cnt, play_cnt, duration,"
            " publish_ts, url, source, COUNT(*)"
            "   FROM post_snap GROUP BY platform, post_id")
        # ② 账号派生表：先算天数并集（作品表 ∪ 主页表），再补昵称/粉丝。
        target.execute(
            "INSERT INTO author_latest (platform, author_id, last_day, snap_days)"
            " SELECT platform, author_id, MAX(day), COUNT(DISTINCT day) FROM ("
            "   SELECT platform, author_id, day FROM author_snap"
            "   UNION ALL"
            "   SELECT platform, author_id, day FROM post_snap WHERE author_id<>''"
            " ) GROUP BY platform, author_id")
        # ③ 精确值（查过主页）：取该账号最近一天的主页记录。
        #    空值/0 一律保留原值 —— 增量写入路径也是这么保护的，
        #    重建必须和增量得到同一个结果，否则「重建一次就修好」就成了空话。
        target.execute(
            "UPDATE author_latest SET"
            " nickname=COALESCE(NULLIF((SELECT s.nickname FROM author_snap s"
            "           WHERE s.platform=author_latest.platform AND s.author_id=author_latest.author_id"
            "           ORDER BY s.day DESC LIMIT 1),''), author_latest.nickname),"
            " fans=COALESCE((SELECT NULLIF(s.fans,0) FROM author_snap s"
            "       WHERE s.platform=author_latest.platform AND s.author_id=author_latest.author_id"
            "       ORDER BY s.day DESC LIMIT 1), author_latest.fans),"
            " fans_day=COALESCE((SELECT s.day FROM author_snap s"
            "           WHERE s.platform=author_latest.platform AND s.author_id=author_latest.author_id"
            "           ORDER BY s.day DESC LIMIT 1), author_latest.fans_day),"
            " source=COALESCE((SELECT s.source FROM author_snap s"
            "         WHERE s.platform=author_latest.platform AND s.author_id=author_latest.author_id"
            "         ORDER BY s.day DESC LIMIT 1), author_latest.source),"
            " fans_exact=1"
            " WHERE EXISTS (SELECT 1 FROM author_snap s WHERE s.platform=author_latest.platform"
            "               AND s.author_id=author_latest.author_id)")
        # ④ 只挖过作品、没查过主页的账号：退而求其次，用**最后一天有值的那条**作品里的
        #    author_fans（不是历史峰值 —— 峰值会把「现在掉粉了」显示成还在高位）。
        target.execute(
            "UPDATE author_latest SET"
            " nickname=COALESCE(NULLIF((SELECT p.author_name FROM post_snap p"
            "           WHERE p.platform=author_latest.platform AND p.author_id=author_latest.author_id"
            "             AND p.author_name<>'' ORDER BY p.day DESC LIMIT 1),''),"
            "           author_latest.nickname),"
            " fans=(SELECT p.author_fans FROM post_snap p"
            "       WHERE p.platform=author_latest.platform AND p.author_id=author_latest.author_id"
            "         AND p.author_fans>0 ORDER BY p.day DESC LIMIT 1),"
            " fans_day=(SELECT p.day FROM post_snap p"
            "       WHERE p.platform=author_latest.platform AND p.author_id=author_latest.author_id"
            "         AND p.author_fans>0 ORDER BY p.day DESC LIMIT 1),"
            " source=COALESCE((SELECT p.source FROM post_snap p"
            "         WHERE p.platform=author_latest.platform AND p.author_id=author_latest.author_id"
            "         ORDER BY p.day DESC LIMIT 1), author_latest.source),"
            " fans_exact=0"
            " WHERE fans_exact=0 AND EXISTS ("
            "   SELECT 1 FROM post_snap p WHERE p.platform=author_latest.platform"
            "     AND p.author_id=author_latest.author_id AND p.author_fans>0)")
        target.commit()
        _invalidate_stats()
        return True
    except Exception:                               # noqa: BLE001
        if own:
            try:
                _reset_conn()
            except Exception:                       # noqa: BLE001
                pass
        return False


def prune(days: int = 365) -> int:
    """删掉 days 天之前的旧快照，返回删除行数。

    默认留一年。老数据再有价值，也不值得让一个本机数据库无限膨胀。
    派生表里 last_day 更旧的也要一起清，否则看板还会显示早就删掉的作品。

    注意：`snap_days` 是「累计观察天数」，prune 之后不回退（历史天数已经删了，
    回退会让所有老作品的记录天数凭空变少，比偏大更容易让人误解）。
    """
    if not _HAS_SQLITE:
        return 0
    since = _day_offset(-max(1, int(days)))
    deleted = 0
    with _WRITE_LOCK:
        try:
            conn = _conn()
            for table, col in (("post_snap", "day"), ("author_snap", "day"),
                               ("post_latest", "last_day"), ("author_latest", "last_day")):
                cur = conn.execute(f"DELETE FROM {table} WHERE {col}<?", (since,))
                deleted += cur.rowcount or 0
                cur.close()
            # 删完重算计数器（这是罕见路径，扫一遍大表可以接受；
            # 而写入路径绝对不能扫，所以计数器才要物化）
            conn.execute("UPDATE _meta SET v=(SELECT COUNT(*) FROM post_snap) WHERE k='rows'")
            conn.execute("UPDATE _meta SET v=(SELECT COUNT(DISTINCT day) FROM post_snap)"
                         " WHERE k='days'")
            conn.commit()
            _invalidate_stats()
            _maybe_compact(conn)
        except Exception:                           # noqa: BLE001
            return 0
    return deleted


def _maybe_compact(conn) -> None:
    """删得多才整理。原来每次 prune 都无条件 VACUUM：140MB 的库要独占锁
    1~3 秒，期间界面查任何东西都在等 —— 用户感觉是「清理完之后软件卡了」。
    """
    try:
        free = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        total = int(conn.execute("PRAGMA page_count").fetchone()[0]) or 1
        if free / total >= 0.25:
            conn.execute("PRAGMA optimize")
            conn.commit()
            conn.execute("VACUUM")
            conn.commit()
        else:
            conn.execute("PRAGMA optimize")
            conn.commit()
    except Exception:                               # noqa: BLE001
        pass


def clear_all() -> int:
    """清空全部快照（页面「一键清理」用）。"""
    if not _HAS_SQLITE:
        return 0
    deleted = 0
    with _WRITE_LOCK:
        try:
            conn = _conn()
            for table in ("post_snap", "author_snap", "post_latest", "author_latest"):
                cur = conn.execute(f"DELETE FROM {table}")
                deleted += cur.rowcount or 0
                cur.close()
            conn.execute("UPDATE _meta SET v=0 WHERE k IN ('rows','days')")
            conn.commit()
            _invalidate_stats()
            # 清空是罕见操作，且数据已经删光，这时 VACUUM 很快，值得做
            try:
                conn.execute("PRAGMA optimize")
                conn.commit()
                conn.execute("VACUUM")
                conn.commit()
            except Exception:                       # noqa: BLE001
                pass
        except Exception:                           # noqa: BLE001
            return 0
    return deleted


def compact() -> bool:
    """手动整理库文件（界面上没有入口，留给排查 / 命令行用）。"""
    if not _HAS_SQLITE:
        return False
    with _WRITE_LOCK:
        try:
            conn = _conn()
            conn.execute("PRAGMA optimize")
            conn.commit()
            conn.execute("VACUUM")
            conn.commit()
            return True
        except Exception:                           # noqa: BLE001
            return False


EXPORT_COLUMNS = ["平台", "最近记录", "标题", "作者", "作者粉丝", "点赞", "评论",
                  "收藏", "分享", "播放", "观察天数", "作品链接"]


def export_rows(items: Iterable[dict]) -> list[list]:
    """把读取结果摊平成可导出的表格行（与其它页面导出格式保持一致）。"""
    rows: list[list] = []
    for it in items or []:
        rows.append([
            it.get("platform", ""), it.get("last_day", ""), it.get("title", ""),
            it.get("author", ""), it.get("fans", 0), it.get("digg", 0),
            it.get("comment", 0), it.get("collect", 0), it.get("share", 0),
            it.get("play", 0), it.get("snap_days", 0), it.get("url", ""),
        ])
    return rows
