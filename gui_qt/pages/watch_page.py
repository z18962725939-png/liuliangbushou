"""复盘监控页（PySide6）。

功能：监控作者列表 + 定期快照（粉丝/作品/获赞变化）。复用 projects.ProjectManager
真实落盘，复用 history_store 做本机时间序列快照。无真实数据时降级为演示。

真实链路（2026-11-25 修复空壳，2026-11-28 打通周报数据源）：
    加入监控 -> Project.add_watch_author() + ProjectManager.save()
    刷新快照 -> Worker 后台逐个 user-profile-v3 -> history_store.snap_author()
             -> 同一次刷新的 user-published-videos-v1 -> WatchTower.record()
             -> 项目目录「监控数据」的 AuthorTrack JSON（周报 build_report 的数据源）
作品接口拿不到时仍用粉丝数建一条作品塔基线，不伪造任何作品；
耗时拉取一律放 Worker；界面只做展示，任何异常都落到状态栏，绝不静默假装成功。
"""
import re

from PySide6.QtWidgets import (QHeaderView, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ._base import (StatusLine, Worker, confirm_destructive, connect_guarded,
                    danger_button, guide_banner, page_title, platform_label,
                    require_feature, vertical_card)

from justoneapi_collector import history_store
from justoneapi_collector.competitor_check import (_SEC_UID_RE, _parse_profile,
                                                   _parse_videos, _pick, _to_int,
                                                   _video_metrics)
from justoneapi_collector.platforms.adapter import PostItem
from justoneapi_collector.watchtower import WatchTower

# 复盘监控目前只支持抖音（页面输入框的提示语也是这么写的）。
# Project.add_watch_author 内部会 normalize_platform，这里传内部 slug 最稳。
_PLATFORM = "douyin-tiktok-china"


def _author_id_from_input(text: str) -> str:
    """从粘贴的抖音分享文案 / 主页 URL / sec_uid 中取稳定的 author_id。

    纯本地字符串处理，不发任何网络请求（更不消耗付费 API 额度）。
    解析不出来时原样保存，绝不丢用户已经输入的内容。
    """
    t = (text or "").strip()
    if not t:
        return ""
    if t.startswith("sec_uid="):
        return t[len("sec_uid="):].strip()
    m = re.search(r"[?&]sec_uid=([^&\s]+)", t)
    if m:
        return m.group(1)
    m = _SEC_UID_RE.search(t)
    if m:
        return m.group(0)
    return t


def _fmt_count(value) -> str:
    """把接口返回的整数显示成运营熟悉的写法（12.3万），异常值显示 '-'。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return "-"
    if n < 0:
        return "-"
    if n >= 100000000:
        return f"{n / 100000000:.1f}亿"
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)


def _current_project(pm):
    """取当前项目；兼容历史写法，缺省时按 ProjectManager 的设计自动建默认项目。

    AppContext 在 build_context() 里注入真实 ProjectManager 后，新安装也有一个
    可直接写入的默认项目，用户不必先去别处「创建项目」（GUI 里本来也没入口）。
    """
    if pm is None:
        return None
    cur = getattr(pm, "current", None)
    if callable(cur):
        try:
            cur = cur()
        except Exception:  # noqa: BLE001
            cur = None
    if cur is None:
        legacy = getattr(pm, "current_project", None)
        if callable(legacy):
            try:
                cur = legacy()
            except Exception:  # noqa: BLE001
                cur = None
    if cur is None:
        ensure = getattr(pm, "ensure_default", None)
        if callable(ensure):
            try:
                cur = ensure()
            except Exception:  # noqa: BLE001
                cur = None
    return cur


def _videos_to_post_items(author_id: str, videos: list) -> list[PostItem]:
    """把 user-published-videos-v1 的原始作品映射成 WatchTower 的 PostItem。

    复用 competitor_check 已有的字段解析（不重写、不发额外请求）。只把真实
    返回的 aweme_id / 文案 / 互动数 / 发布时间映射过去；无 share_url 时用真实
    作品 ID 拼抖音标准播放页，属于派生地址而非伪造数据。
    """
    items: list[PostItem] = []
    for v in videos or []:
        if not isinstance(v, dict):
            continue
        pid = str(_pick(v, "aweme_id", "awemeId", "video_id", "id", default="") or "")
        if not pid:
            continue
        digg, comment, share, ct = _video_metrics(v)
        collect = _to_int(_pick(v, "statistics.collect_count",
                                "aweme_stats.collect_count", "collect_count"))
        play = _to_int(_pick(v, "statistics.play_count", "play_count"))
        url = str(_pick(v, "share_url", "aweme_url", "url", default="") or "")
        if not url:
            url = f"https://www.douyin.com/video/{pid}"
        items.append(PostItem(
            platform=_PLATFORM, post_id=pid,
            title=str(_pick(v, "desc", "title", "caption", default="") or ""),
            author_id=author_id, digg=digg, comment=comment, collect=collect,
            share=share, play=play, publish_ts=ct, url=url, raw=v,
        ))
    return items


def _fetch_posts(client, author_id: str) -> list[PostItem]:
    """尽力取一页作品列表；接口失败/无作品时返回空列表，不抛给上层。"""
    try:
        resp = client.request(_PLATFORM, "user-published-videos-v1",
                              {"secUid": author_id, "count": 20})
        if not getattr(resp, "success", False):
            return []
        return _videos_to_post_items(author_id, _parse_videos(getattr(resp, "data", None)))
    except Exception:  # noqa: BLE001
        return []


def _collect_author_snapshots(client, authors, snapshot=None, tower=None) -> list[dict]:
    """后台拉取每个账号的主页指标并写入本机历史库与作品塔。

    在 Worker 线程里执行，禁止触碰任何 Qt 控件。每个账号最多发两次请求：
    一次 user-profile-v3（粉丝事实表）与一次 user-published-videos-v1（作品塔）。
    单个账号失败只影响它自己那一行。``snapshot`` / ``tower`` 可注入，便于离屏
    测试用假对象验证边界而不落真实库。
    """
    write = snapshot if callable(snapshot) else history_store.snap_author
    today = history_store._today()
    out: list[dict] = []
    for author in authors or []:
        author_id = str((author or {}).get("author_id") or "").strip()
        saved_name = str((author or {}).get("nickname") or "")
        row = {
            "name": saved_name or author_id, "platform": _PLATFORM,
            "fans": "-", "posts": "-", "likes": "-",
            "updated": today, "state": "失败", "tower": "未写入",
        }
        if not author_id:
            row["state"] = "无ID"
            out.append(row)
            continue
        try:
            resp = client.request(_PLATFORM, "user-profile-v3", {"secUid": author_id})
            if not getattr(resp, "success", False):
                row["state"] = "接口失败"
                out.append(row)
                continue
            prof = _parse_profile(getattr(resp, "data", None))
            name = prof["nickname"] or saved_name or author_id
            row.update({
                "name": name,
                "fans": _fmt_count(prof["follower_count"]),
                "posts": _fmt_count(prof["aweme_count"]),
                "likes": _fmt_count(prof["total_favorited"]),
                "state": "正常",
            })
            # 历史库只收粉丝数（author_snap 的事实表口径），作品/获赞按本次接口
            # 返回值展示，不伪造进库里没有的列。失败一律由 history_store 自己吞。
            write({"platform": _PLATFORM, "author_id": author_id,
                   "nickname": name, "fans": prof["follower_count"]},
                  source="复盘监控")
            # 同一账号刷新的作品列表直接喂作品塔；周报 build_report 读的就是
            # 这个塔的 AuthorTrack JSON，不再依赖 sqlite 行（模型不同）。
            if tower is not None:
                posts = _fetch_posts(client, author_id)
                try:
                    tower.record(_PLATFORM, author_id, name, posts,
                                 fans=prof["follower_count"])
                    row["tower"] = "已写入" if posts else "仅粉丝基线"
                except Exception:  # noqa: BLE001
                    row["tower"] = "写入失败"
        except Exception:  # noqa: BLE001
            row["state"] = "失败"
        out.append(row)
    return out


class WatchPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        root.addWidget(page_title("复盘监控"))
        root.addWidget(guide_banner("复盘监控", self))

        top, top_l = vertical_card("添加监控作者（自动定期记录粉丝/作品/获赞变化）", self)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("作者主页链接 / sec_uid："))
        self.input = QLineEdit(); self.input.setPlaceholderText("粘贴抖音主页链接或直接贴 sec_uid")
        r1.addWidget(self.input, 1)
        b_add = QPushButton("加入监控"); b_add.setObjectName("Primary")
        connect_guarded(b_add.clicked, lambda *_: self._add(),
                        on_error=lambda msg: self.status.setText(msg), label="加入监控")
        r1.addWidget(b_add)
        top_l.addLayout(r1)
        r2 = QHBoxLayout()
        b_refresh = QPushButton("立即刷新快照")
        connect_guarded(b_refresh.clicked, lambda *_: self._refresh(),
                        on_error=lambda msg: self.status.setText(msg), label="刷新快照")
        b_rm = QPushButton("移除选中")
        connect_guarded(b_rm.clicked, lambda *_: self._remove(),
                        on_error=lambda msg: self.status.setText(msg), label="移除监控")
        # 清空整张监控表：破坏性动作，样式弱化 + 二次确认
        self.clear_btn = danger_button("清空列表")
        self.clear_btn.setToolTip("清空本页监控列表（不删除已积累的历史快照）")
        connect_guarded(self.clear_btn.clicked, lambda *_: self._clear(),
                        on_error=lambda msg: self.status.setText(msg), label="清空监控")
        r2.addWidget(b_refresh); r2.addWidget(b_rm); r2.addWidget(self.clear_btn)
        r2.addStretch()
        top_l.addLayout(r2)
        root.addWidget(top)

        tb, tb_l = vertical_card("监控列表", self)
        cols = ["作者", "平台", "粉丝数", "作品数", "获赞总数", "最近更新", "状态"]
        self.table = QTableWidget(0, len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        tb_l.addWidget(self.table, 1)
        root.addWidget(tb, 1)

        self.status = StatusLine("就绪"); self.status.mark("idle")
        root.addWidget(self.status)

        if self.ctx.demo:
            self._demo()
        else:
            self._load_from_project()
        self._sync_actions()

    def _demo(self):
        for a in [("示例博主A", "douyin", "12.3万", "320", "98.6万", "今天", "正常"),
                  ("示例博主B", "xiaohongshu", "5.1万", "210", "33.2万", "昨天", "正常")]:
            self._add_row(*a)
        self.status.setText("演示模式：展示示例数据，配置项目后可实时复盘")

    def _load_from_project(self):
        """正式态：把项目里已经监控的作者显示出来（真值来自落盘数据）。"""
        project = _current_project(getattr(self.ctx, "project_manager", None))
        if project is None:
            self.status.setText("项目数据未就绪：请在「设置」中配置项目后再使用复盘监控")
            self.status.mark("warn")
            return
        authors = list(getattr(project, "watch_authors", []) or [])
        if not authors:
            return
        for a in authors:
            self._add_row(
                str(a.get("nickname") or a.get("author_id") or ""),
                str(a.get("platform") or "douyin"),
                "-", "-", "-", "待采集", "新增")
        self.status.setText(f"已从项目载入 {len(authors)} 个监控作者；点「立即刷新快照」拉取最新数据")

    def _add_row(self, name, plat, fans, posts, likes, updated, state):
        r = self.table.rowCount()
        self.table.insertRow(r)
        # 平台列显示中文（存储/接口仍是内部键，只在显示这一层翻译）
        for c, v in enumerate((name, platform_label(plat), fans, posts,
                               likes, updated, state)):
            self.table.setItem(r, c, QTableWidgetItem(str(v)))
        self._sync_actions()

    def _sync_actions(self):
        """空列表时禁用「清空列表」，避免无效点击。"""
        self.clear_btn.setEnabled(self.table.rowCount() > 0)

    def _add(self):
        # ★ 付费闸门（v1.21.0）：加入监控后会定期拉账号数据，属于采集类功能
        if not require_feature(self, "analyze", "加入复盘监控"):
            return
        text = self.input.text().strip()
        if not text:
            self.status.setText("请先输入作者链接或 sec_uid")
            return
        if self.ctx.demo:
            self._add_row(text, "douyin", "-", "-", "-", "待采集", "新增")
            self.status.setText("演示模式：已加入示例监控")
            return
        pm = getattr(self.ctx, "project_manager", None)
        if pm is None:
            self.status.setText("项目数据未就绪：请先在「设置」中配置项目再加入监控")
            self.status.mark("warn")
            return
        project = _current_project(pm)
        if project is None:
            self.status.setText("请先创建或打开一个项目，再加入监控")
            self.status.mark("warn")
            return
        author_id = _author_id_from_input(text)
        try:
            added = project.add_watch_author(_PLATFORM, author_id, text)
            if not added:
                self.status.setText(f"该作者已在监控清单中：{text}")
                return
            if not pm.save():
                self.status.setText("已加入项目清单，但保存失败：请检查数据目录权限")
                self.status.mark("error")
                return
        except Exception as e:  # noqa: BLE001
            self.status.setText(f"加入失败：{e}")
            self.status.mark("error")
            return
        self._add_row(text, "douyin", "-", "-", "-", "待采集", "新增")
        self.status.setText(f"已加入监控：{text}")

    def _refresh(self):
        # ★ 付费闸门（v1.21.0）：刷新快照 = 逐个拉账号最新数据，接口成本在这里
        if not require_feature(self, "analyze", "刷新监控快照"):
            return
        if self.ctx.demo:
            self.status.setText("演示模式：刷新需连接真实项目数据")
            return
        pm = getattr(self.ctx, "project_manager", None)
        if pm is None:
            self.status.setText("项目数据未就绪：请先在「设置」中配置项目再刷新")
            self.status.mark("warn")
            return
        project = _current_project(pm)
        if project is None:
            self.status.setText("请先创建或打开一个项目，再刷新快照")
            self.status.mark("warn")
            return
        authors = list(getattr(project, "watch_authors", []) or [])
        if not authors:
            self.status.setText("监控清单为空，请先加入监控作者")
            return
        self.status.setText(f"正在刷新快照：共 {len(authors)} 个作者 …")

        def job(worker):
            # 与周报完全同一份存储：项目根 /「监控数据」的 WatchTower JSON。
            tower = WatchTower(project.root / "监控数据")
            return _collect_author_snapshots(self.ctx.client, authors, tower=tower)

        w = Worker(job)
        connect_guarded(w.done, self._on_refreshed,
                        on_error=lambda msg: self.status.setText(msg), label="刷新快照")
        connect_guarded(w.error, lambda msg: self.status.setText(f"刷新失败：{msg}"),
                        label="刷新快照")
        w.start()

    def _on_refreshed(self, rows):
        """Worker 回主线程：用真实接口值重建表格，并更新状态栏。"""
        rows = list(rows or [])
        self.table.setRowCount(0)
        for r in rows:
            self._add_row(r.get("name", ""), r.get("platform", ""),
                          r.get("fans", ""), r.get("posts", ""),
                          r.get("likes", ""), r.get("updated", ""),
                          r.get("state", ""))
        self._sync_actions()
        ok = sum(1 for r in rows if r.get("state") == "正常")
        tower_ok = sum(1 for r in rows
                       if r.get("tower") in ("已写入", "仅粉丝基线"))
        self.status.setText(
            f"刷新完成：{ok}/{len(rows)} 个作者数据已写入本机快照；"
            f"作品塔 {tower_ok}/{len(rows)} 个")

    def _remove(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            self.status.setText("请先在表格里选中要移除的作者")
            return
        for r in rows:
            self.table.removeRow(r)
        self._sync_actions()
        self.status.setText(f"已移除 {len(rows)} 个作者")

    def _clear(self):
        """清空监控列表：二次确认 → 清空 → 明确反馈。"""
        n = self.table.rowCount()
        if n == 0:
            self.status.setText("监控列表已经是空的")
            return
        if not confirm_destructive(
            self, "清空监控列表",
            f"确定清空当前 {n} 个监控作者吗？",
            ok_text="确认清空",
            detail="只清空本页列表；已积累的历史快照仍保留在项目数据里。",
        ):
            self.status.setText("已取消，监控列表未做改动")
            return
        self.table.setRowCount(0)
        self._sync_actions()
        self.status.setText(f"已清空 {n} 个监控作者")
