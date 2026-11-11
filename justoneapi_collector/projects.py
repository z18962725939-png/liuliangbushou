"""
多客户 / 多项目管理。

代运营同时带几个客户是常态，所以每个项目（客户）要有自己独立的：
  - 监控清单（对标账号、订阅关键词）
  - 自家账号清单（复盘监控用）
  - 导出目录 / 素材目录 / 报告目录

持久化在 %APPDATA%\\JustOneAPICollector\\projects.json，
与 GUI 的设置文件同目录，卸载重装不丢。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .platforms.adapter import PLATFORM_DISPLAY, normalize_platform

DEFAULT_WORKSPACE_NAME = "JustOneAPI工作区"


def _safe_dir_name(text: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", (text or "").strip())
    return cleaned[:40] or "未命名项目"


def _default_workspace_root() -> Path:
    desktop = Path.home() / "Desktop"
    base = desktop if desktop.exists() else Path.home()
    return base / DEFAULT_WORKSPACE_NAME


@dataclass
class Project:
    id: str = ""
    name: str = ""
    client: str = ""                       # 客户/品牌名
    platforms: list[str] = field(default_factory=list)      # 平台 slug
    watch_authors: list[dict] = field(default_factory=list)  # 对标账号
    own_authors: list[dict] = field(default_factory=list)    # 自家/客户账号
    watch_keywords: list[str] = field(default_factory=list)  # 订阅关键词
    root_dir: str = ""
    note: str = ""
    created_at: float = 0.0

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = time.time()
        if not self.platforms:
            self.platforms = ["douyin-tiktok-china"]
        self.platforms = [normalize_platform(p) for p in self.platforms]

    # -- 目录 ------------------------------------------------------

    @property
    def root(self) -> Path:
        if self.root_dir:
            return Path(self.root_dir)
        return _default_workspace_root() / _safe_dir_name(self.name)

    @property
    def export_dir(self) -> Path:
        return self.root / "导出"

    @property
    def media_dir(self) -> Path:
        return self.root / "素材"

    @property
    def report_dir(self) -> Path:
        return self.root / "报告"

    def ensure_dirs(self) -> None:
        for d in (self.export_dir, self.media_dir, self.report_dir):
            d.mkdir(parents=True, exist_ok=True)

    # -- 清单维护 --------------------------------------------------

    def add_watch_author(self, platform: str, author_id: str, nickname: str = "") -> bool:
        slug = normalize_platform(platform)
        for a in self.watch_authors:
            if a.get("platform") == slug and a.get("author_id") == author_id:
                return False
        self.watch_authors.append({
            "platform": slug, "author_id": author_id, "nickname": nickname,
            "added_at": time.time(),
        })
        return True

    def remove_watch_author(self, platform: str, author_id: str) -> bool:
        slug = normalize_platform(platform)
        before = len(self.watch_authors)
        self.watch_authors = [
            a for a in self.watch_authors
            if not (a.get("platform") == slug and a.get("author_id") == author_id)
        ]
        return len(self.watch_authors) < before

    def add_own_author(self, platform: str, author_id: str, nickname: str = "") -> bool:
        slug = normalize_platform(platform)
        for a in self.own_authors:
            if a.get("platform") == slug and a.get("author_id") == author_id:
                return False
        self.own_authors.append({
            "platform": slug, "author_id": author_id, "nickname": nickname,
            "added_at": time.time(),
        })
        return True

    def remove_own_author(self, platform: str, author_id: str) -> bool:
        slug = normalize_platform(platform)
        before = len(self.own_authors)
        self.own_authors = [
            a for a in self.own_authors
            if not (a.get("platform") == slug and a.get("author_id") == author_id)
        ]
        return len(self.own_authors) < before

    def add_keyword(self, kw: str) -> bool:
        kw = (kw or "").strip()
        if not kw or kw in self.watch_keywords:
            return False
        self.watch_keywords.append(kw)
        return True

    def remove_keyword(self, kw: str) -> bool:
        before = len(self.watch_keywords)
        self.watch_keywords = [k for k in self.watch_keywords if k != kw]
        return len(self.watch_keywords) < before

    # -- 序列化 ----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "client": self.client,
            "platforms": self.platforms, "watch_authors": self.watch_authors,
            "own_authors": self.own_authors, "watch_keywords": self.watch_keywords,
            "root_dir": self.root_dir, "note": self.note,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        return cls(
            id=d.get("id", ""), name=d.get("name", ""),
            client=d.get("client", ""),
            platforms=d.get("platforms") or ["douyin-tiktok-china"],
            watch_authors=d.get("watch_authors") or [],
            own_authors=d.get("own_authors") or [],
            watch_keywords=d.get("watch_keywords") or [],
            root_dir=d.get("root_dir", ""), note=d.get("note", ""),
            created_at=d.get("created_at", 0.0),
        )

    @property
    def summary(self) -> str:
        plats = "/".join(PLATFORM_DISPLAY.get(p, p) for p in self.platforms)
        return (f"平台 {plats}　对标 {len(self.watch_authors)} 个　"
                f"自营 {len(self.own_authors)} 个　关键词 {len(self.watch_keywords)} 个")


class ProjectManager:
    """项目增删改查 + 当前项目切换。"""

    def __init__(self, data_dir: Path | None = None):
        if data_dir is None:
            import os
            data_dir = Path(os.environ.get("APPDATA", str(Path.home()))) \
                / "JustOneAPICollector"
        self.data_dir = Path(data_dir)
        self.file = self.data_dir / "projects.json"
        self._projects: list[Project] = []
        self._current_id: str = ""
        self.load()

    # -- 持久化 ----------------------------------------------------

    def load(self) -> None:
        self._projects = []
        self._current_id = ""
        try:
            if self.file.exists():
                data = json.loads(self.file.read_text(encoding="utf-8"))
                self._projects = [Project.from_dict(d) for d in data.get("projects", [])]
                self._current_id = data.get("current_id", "")
        except Exception:
            self._projects = []
        if self._projects and self._current_id not in {p.id for p in self._projects}:
            self._current_id = self._projects[0].id

    def save(self) -> bool:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "current_id": self._current_id,
                "projects": [p.to_dict() for p in self._projects],
            }
            self.file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except Exception:
            return False

    # -- CRUD ------------------------------------------------------

    def all(self) -> list[Project]:
        return list(self._projects)

    @property
    def names(self) -> list[str]:
        return [p.name for p in self._projects]

    def get(self, pid: str) -> Project | None:
        for p in self._projects:
            if p.id == pid:
                return p
        return None

    def get_by_name(self, name: str) -> Project | None:
        for p in self._projects:
            if p.name == name:
                return p
        return None

    def create(self, name: str, client: str = "", platforms=None,
               root_dir: str = "", note: str = "") -> Project:
        p = Project(name=name, client=client,
                    platforms=platforms or ["douyin-tiktok-china"],
                    root_dir=root_dir, note=note)
        self._projects.append(p)
        if not self._current_id:
            self._current_id = p.id
        self.save()
        return p

    def delete(self, pid: str) -> bool:
        before = len(self._projects)
        self._projects = [p for p in self._projects if p.id != pid]
        if len(self._projects) < before:
            if self._current_id == pid:
                self._current_id = self._projects[0].id if self._projects else ""
            self.save()
            return True
        return False

    def update(self, project: Project) -> bool:
        for i, p in enumerate(self._projects):
            if p.id == project.id:
                self._projects[i] = project
                self.save()
                return True
        return False

    # -- 当前项目 --------------------------------------------------

    @property
    def current(self) -> Project | None:
        if not self._current_id:
            return self._projects[0] if self._projects else None
        return self.get(self._current_id)

    def set_current(self, pid: str) -> Project | None:
        p = self.get(pid)
        if p:
            self._current_id = pid
            self.save()
        return p

    def set_current_by_name(self, name: str) -> Project | None:
        p = self.get_by_name(name)
        if p:
            self._current_id = p.id
            self.save()
        return p

    def ensure_default(self) -> Project:
        """没有项目时创建一个默认项目，保证上层永远拿得到可用项目。"""
        cur = self.current
        if cur:
            return cur
        return self.create("默认项目", note="首次启动自动创建")
