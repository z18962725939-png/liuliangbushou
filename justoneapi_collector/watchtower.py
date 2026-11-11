"""
复盘侧：作品数据异动监控 + 竞品更新预警。

运营最怕两件事，本模块就是把它们从"感觉"变成"明确提示"：
1. 不知道作品没进推荐池 —— 发出去几小时没动静，其实是没被推，不是内容不行。
2. 不知道被限流了 —— 数据涨着涨着突然断崖，很多人一周后才发现。

做法：对每个被监控账号定期打快照，存下每条作品的点赞/评论/收藏曲线，
用「发布时长 + 绝对量 + 相邻区间增速变化」三个维度判定异动。

另一半是竞品更新预警：每次快照都记下已知作品 ID，
新出现的就是竞品刚发的，第一时间提醒去拆。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .platforms.adapter import PLATFORM_DISPLAY, PostItem, normalize_platform


@dataclass
class WatchConfig:
    """判定阈值。默认偏保守，宁可少报也不错报。"""
    cold_start_hours: float = 2.0     # 发布超过这么久仍没量 = 没进推荐池
    cold_start_digg: int = 100        # 冷启动点赞门槛
    drop_ratio: float = 0.2           # 本次增量 < 上次增量 × 该比例 = 断崖
    drop_min_delta: int = 50          # 上次增量至少这么多才参与断崖判定
    stall_hours: float = 6.0          # 发布超过这么久后连续无增长 = 停滞
    stall_checks: int = 2             # 连续几次快照无变化才算停滞
    new_post_lookback_hours: float = 72.0   # 多久内发布的算"新作品"


@dataclass
class Anomaly:
    post_id: str = ""
    title: str = ""
    kind: str = ""          # 未进推荐池 / 疑似限流 / 数据停滞
    level: str = "warn"     # warn / danger
    message: str = ""
    digg: int = 0
    url: str = ""

    def row(self) -> list:
        return [self.kind, self.level_text, self.title[:40], self.message, self.url]

    @property
    def level_text(self) -> str:
        return "严重" if self.level == "danger" else "注意"


ANOMALY_COLUMNS = ["类型", "级别", "作品", "说明", "链接"]


@dataclass
class PostTrack:
    post_id: str = ""
    title: str = ""
    url: str = ""
    publish_ts: int = 0
    first_seen: float = 0.0
    history: list[dict] = field(default_factory=list)   # [{ts, digg, comment, collect, share, play}]

    @property
    def latest(self) -> dict:
        return self.history[-1] if self.history else {}

    @property
    def age_hours(self) -> float:
        base = self.publish_ts or self.first_seen
        if not base:
            return 0.0
        return max((time.time() - base) / 3600.0, 0.0)

    def delta(self, key: str = "digg") -> int:
        """最近一次快照的增量。"""
        if len(self.history) < 2:
            return 0
        return int(self.history[-1].get(key, 0)) - int(self.history[-2].get(key, 0))


@dataclass
class AuthorTrack:
    platform: str = ""
    author_id: str = ""
    nickname: str = ""
    fans_history: list[list] = field(default_factory=list)   # [[ts, fans]]
    posts: dict[str, PostTrack] = field(default_factory=dict)
    last_checked: float = 0.0
    baseline_done: bool = False     # 首次快照只建基线，不报"新作品"

    @property
    def fans(self) -> int:
        return self.fans_history[-1][1] if self.fans_history else 0

    @property
    def fans_delta(self) -> int:
        if len(self.fans_history) < 2:
            return 0
        return self.fans_history[-1][1] - self.fans_history[-2][1]

    def to_dict(self) -> dict:
        return {
            "platform": self.platform, "author_id": self.author_id,
            "nickname": self.nickname, "fans_history": self.fans_history,
            "posts": {pid: {"post_id": t.post_id, "title": t.title, "url": t.url,
                            "publish_ts": t.publish_ts, "first_seen": t.first_seen,
                            "history": t.history}
                      for pid, t in self.posts.items()},
            "last_checked": self.last_checked, "baseline_done": self.baseline_done,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AuthorTrack":
        t = cls(platform=d.get("platform", ""), author_id=d.get("author_id", ""),
                nickname=d.get("nickname", ""),
                fans_history=d.get("fans_history") or [],
                last_checked=d.get("last_checked", 0.0),
                baseline_done=bool(d.get("baseline_done")))
        for pid, pd in (d.get("posts") or {}).items():
            t.posts[pid] = PostTrack(
                post_id=pd.get("post_id", pid), title=pd.get("title", ""),
                url=pd.get("url", ""), publish_ts=pd.get("publish_ts", 0),
                first_seen=pd.get("first_seen", 0.0), history=pd.get("history") or [])
        return t


def _analyze_post(pt: PostTrack, cfg: WatchConfig) -> list[Anomaly]:
    """对单条作品的历史曲线做异动判定。"""
    out: list[Anomaly] = []
    hist = pt.history
    if not hist:
        return out

    now_digg = int(hist[-1].get("digg", 0))
    age = pt.age_hours

    # 1) 冷启动失败：发布够久，量却没起来
    if age >= cfg.cold_start_hours and now_digg < cfg.cold_start_digg:
        out.append(Anomaly(
            post_id=pt.post_id, title=pt.title, kind="未进推荐池",
            level="warn" if now_digg > 0 else "danger",
            message=(f"发布 {age:.1f} 小时仅 {now_digg} 赞，"
                     f"低于冷启动阈值 {cfg.cold_start_digg}，基本没被推流"),
            digg=now_digg, url=pt.url))

    # 2) 增速断崖：上一区间还在涨，这一区间几乎不涨
    if len(hist) >= 3:
        prev_delta = int(hist[-2].get("digg", 0)) - int(hist[-3].get("digg", 0))
        last_delta = int(hist[-1].get("digg", 0)) - int(hist[-2].get("digg", 0))
        if prev_delta >= cfg.drop_min_delta and last_delta < prev_delta * cfg.drop_ratio:
            out.append(Anomaly(
                post_id=pt.post_id, title=pt.title, kind="疑似限流",
                level="danger",
                message=(f"点赞增速从 +{prev_delta} 骤降到 +{last_delta}，"
                         f"降幅 {100 - int(last_delta / max(prev_delta, 1) * 100)}%，疑似被限流"),
                digg=now_digg, url=pt.url))

    # 3) 长时间停滞
    if len(hist) >= cfg.stall_checks + 1 and age >= cfg.stall_hours:
        tail = hist[-(cfg.stall_checks + 1):]
        vals = [int(h.get("digg", 0)) for h in tail]
        if len(set(vals)) == 1:
            out.append(Anomaly(
                post_id=pt.post_id, title=pt.title, kind="数据停滞",
                level="warn",
                message=f"连续 {len(vals)} 次采集点赞都停在 {vals[-1]}，推荐已停止",
                digg=now_digg, url=pt.url))

    return out


class WatchTower:
    """绑定到一个存储目录的监控塔。"""

    def __init__(self, store_dir: Path, config: WatchConfig | None = None):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.cfg = config or WatchConfig()

    def _file(self, platform: str, author_id: str) -> Path:
        slug = normalize_platform(platform)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(author_id))
        return self.store_dir / f"{slug}_{safe}.json"

    def get(self, platform: str, author_id: str) -> AuthorTrack | None:
        f = self._file(platform, author_id)
        if not f.exists():
            return None
        try:
            return AuthorTrack.from_dict(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            return None

    def save(self, track: AuthorTrack) -> bool:
        try:
            self._file(track.platform, track.author_id).write_text(
                json.dumps(track.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8")
            return True
        except Exception:
            return False

    def all_tracks(self) -> list[AuthorTrack]:
        out = []
        for f in self.store_dir.glob("*.json"):
            try:
                out.append(AuthorTrack.from_dict(json.loads(f.read_text(encoding="utf-8"))))
            except Exception:
                continue
        return out

    # -- 核心：打一次快照 -------------------------------------------

    def record(self, platform: str, author_id: str, nickname: str,
               posts: list[PostItem], fans: int = 0) -> tuple[list[PostItem], list[Anomaly]]:
        """
        记录一次快照。返回 (本次新发现的作品, 全部异动)。

        第一次快照只建基线，不把已有作品当成"新作品"刷屏。
        """
        slug = normalize_platform(platform)
        track = self.get(slug, author_id) or AuthorTrack(
            platform=slug, author_id=author_id, nickname=nickname)
        track.nickname = nickname or track.nickname

        now = time.time()
        new_posts: list[PostItem] = []
        for p in posts:
            if not p.post_id:
                continue
            t = track.posts.get(p.post_id)
            if t is None:
                t = PostTrack(post_id=p.post_id, title=p.title, url=p.url,
                              publish_ts=p.publish_ts, first_seen=now)
                track.posts[p.post_id] = t
                # 首次快照建基线；之后新出现的才是真的"刚发"
                if track.baseline_done:
                    age_h = (now - (p.publish_ts or now)) / 3600.0
                    if age_h <= self.cfg.new_post_lookback_hours:
                        new_posts.append(p)
            else:
                t.title = t.title or p.title
                t.url = t.url or p.url
                if p.publish_ts:
                    t.publish_ts = p.publish_ts
            t.history.append({
                "ts": now, "digg": p.digg, "comment": p.comment,
                "collect": p.collect, "share": p.share, "play": p.play,
            })
            # 历史只保留最近 200 次，避免文件无限膨胀
            if len(t.history) > 200:
                t.history = t.history[-200:]

        if fans:
            if not track.fans_history or track.fans_history[-1][1] != fans:
                track.fans_history.append([now, fans])
                track.fans_history = track.fans_history[-200:]

        track.last_checked = now
        track.baseline_done = True

        anomalies: list[Anomaly] = []
        for t in track.posts.values():
            anomalies.extend(_analyze_post(t, self.cfg))

        self.save(track)
        return new_posts, anomalies

    # -- 查询 ------------------------------------------------------

    def anomalies(self, platform: str = "", author_id: str = "") -> list[Anomaly]:
        out: list[Anomaly] = []
        for tr in self.all_tracks():
            if platform and tr.platform != normalize_platform(platform):
                continue
            if author_id and tr.author_id != author_id:
                continue
            for t in tr.posts.values():
                for a in _analyze_post(t, self.cfg):
                    out.append(a)
        order = {"danger": 0, "warn": 1}
        out.sort(key=lambda a: (order.get(a.level, 9), -a.digg))
        return out

    def recent_posts(self, hours: float = 72.0) -> list[tuple[AuthorTrack, PostTrack]]:
        """最近 N 小时内出现（发布或被首次采集到）的作品。"""
        cutoff = time.time() - hours * 3600
        out = []
        for tr in self.all_tracks():
            for t in tr.posts.values():
                base = t.publish_ts or t.first_seen
                if base and base >= cutoff:
                    out.append((tr, t))
        out.sort(key=lambda x: (x[1].publish_ts or x[1].first_seen), reverse=True)
        return out

    def growth_curve(self, platform: str, author_id: str,
                     post_id: str) -> list[tuple[float, int]]:
        tr = self.get(platform, author_id)
        if not tr:
            return []
        t = tr.posts.get(post_id)
        if not t:
            return []
        return [(h["ts"], h.get("digg", 0)) for h in t.history]

    def author_summary(self, platform: str, author_id: str) -> dict:
        tr = self.get(platform, author_id)
        if not tr:
            return {}
        diggs = [int((t.latest or {}).get("digg", 0)) for t in tr.posts.values()]
        diggs = [d for d in diggs if d]
        if not diggs:
            median = 0
        else:
            s = sorted(diggs)
            mid = len(s) // 2
            median = s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) // 2
        viral = [d for d in diggs if median and d >= median * 3]
        return {
            "平台": PLATFORM_DISPLAY.get(tr.platform, tr.platform),
            "账号": tr.nickname or tr.author_id,
            "粉丝": tr.fans,
            "粉丝变化": tr.fans_delta,
            "作品数": len(tr.posts),
            "点赞中位数": median,
            "最高点赞": max(diggs) if diggs else 0,
            "爆款数": len(viral),
            "爆款率": f"{len(viral) / len(diggs) * 100:.0f}%" if diggs else "0%",
            "最近检查": time.strftime("%m-%d %H:%M", time.localtime(tr.last_checked))
            if tr.last_checked else "",
        }
