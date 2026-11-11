"""
素材库：下载的素材按「项目 / 平台 / 博主 / 日期」自动归档并去重。

解决的问题：几百个视频堆在一个文件夹里，同名文件互相覆盖，
根本找不到哪个是哪个博主的、什么时候下载的。

去重依据是平台作品 ID（不是文件名），所以同一条作品重复下载时会被跳过，
省时间也省 API 额度。
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .platforms.adapter import PLATFORM_DISPLAY, normalize_platform


def _safe_name(text: str, max_len: int = 40) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", (text or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:max_len] or "未命名"


@dataclass
class MediaRecord:
    post_id: str = ""
    platform: str = ""
    title: str = ""
    author: str = ""
    author_id: str = ""
    file: str = ""            # 相对素材目录的路径
    size: int = 0
    publish_ts: int = 0
    archived_at: float = 0.0
    source: str = ""          # "download" 下载 / "live" 直播录制

    def to_dict(self) -> dict:
        return {
            "post_id": self.post_id, "platform": self.platform, "title": self.title,
            "author": self.author, "author_id": self.author_id, "file": self.file,
            "size": self.size, "publish_ts": self.publish_ts,
            "archived_at": self.archived_at, "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MediaRecord":
        return cls(**{k: v for k, v in d.items()
                      if k in cls.__dataclass_fields__})  # type: ignore[arg-type]


class MediaVault:
    """绑定到一个项目的素材库。"""

    def __init__(self, media_dir: Path):
        self.media_dir = Path(media_dir)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.media_dir / "_素材索引.json"
        self._records: dict[str, MediaRecord] = {}
        self.load()

    # -- 索引 ------------------------------------------------------

    def load(self) -> None:
        self._records = {}
        try:
            if self.index_file.exists():
                data = json.loads(self.index_file.read_text(encoding="utf-8"))
                for d in data.get("records", []):
                    r = MediaRecord.from_dict(d)
                    if r.post_id:
                        self._records[r.post_id] = r
        except Exception:
            self._records = {}

    def save(self) -> bool:
        try:
            payload = {"records": [r.to_dict() for r in self._records.values()]}
            self.index_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except Exception:
            return False

    def has(self, post_id: str) -> bool:
        r = self._records.get(str(post_id))
        if not r:
            return False
        p = self.media_dir / r.file
        return p.exists()

    def get(self, post_id: str) -> MediaRecord | None:
        r = self._records.get(str(post_id))
        if not r:
            return None
        if not (self.media_dir / r.file).exists():
            return None
        return r

    def abs_path(self, rec: MediaRecord) -> Path:
        return self.media_dir / rec.file

    # -- 归档 ------------------------------------------------------

    def target_path(self, platform: str, author: str, post_id: str,
                    ext: str = ".mp4", publish_ts: int = 0) -> Path:
        """项目素材 / 平台 / 博主 / 年月 / 标题_ID.mp4"""
        slug = normalize_platform(platform)
        plat = _safe_name(PLATFORM_DISPLAY.get(slug, slug), 12)
        who = _safe_name(author, 24)
        if publish_ts:
            ym = time.strftime("%Y-%m", time.localtime(publish_ts))
        else:
            ym = time.strftime("%Y-%m")
        sub = self.media_dir / plat / who / ym
        sub.mkdir(parents=True, exist_ok=True)
        ext = ext if ext.startswith(".") else f".{ext}"
        return sub / f"{_safe_name(post_id, 32)}{ext}"

    def archive(self, src: Path, platform: str, author: str, post_id: str,
                title: str = "", author_id: str = "", publish_ts: int = 0,
                source: str = "download", move: bool = True) -> Path | None:
        """把已下载的文件归进素材库。已存在则跳过（去重）。返回最终路径。"""
        src = Path(src)
        if not src.exists():
            return None
        if self.has(post_id):
            return self.abs_path(self._records[str(post_id)])

        dst = self.target_path(platform, author, post_id, src.suffix or ".mp4", publish_ts)
        counter = 1
        while dst.exists():
            dst = dst.with_name(f"{dst.stem}_{counter}{dst.suffix}")
            counter += 1
        try:
            if move:
                shutil.move(str(src), str(dst))
            else:
                shutil.copy2(str(src), str(dst))
        except Exception:
            return None

        self._records[str(post_id)] = MediaRecord(
            post_id=str(post_id), platform=normalize_platform(platform),
            title=title, author=author, author_id=author_id,
            file=str(dst.relative_to(self.media_dir)),
            size=dst.stat().st_size if dst.exists() else 0,
            publish_ts=publish_ts, archived_at=time.time(), source=source,
        )
        self.save()
        return dst

    # -- 查询 ------------------------------------------------------

    def records(self, platform: str = "", author: str = "") -> list[MediaRecord]:
        out = list(self._records.values())
        if platform:
            slug = normalize_platform(platform)
            out = [r for r in out if r.platform == slug]
        if author:
            out = [r for r in out if r.author == author]
        return sorted(out, key=lambda r: r.archived_at, reverse=True)

    def stats(self) -> dict:
        total = len(self._records)
        size = 0
        by_platform: dict[str, int] = {}
        by_author: dict[str, int] = {}
        for r in self._records.values():
            size += r.size
            by_platform[r.platform] = by_platform.get(r.platform, 0) + 1
            by_author[r.author] = by_author.get(r.author, 0) + 1
        return {
            "总数": total, "总大小": size,
            "按平台": {PLATFORM_DISPLAY.get(k, k): v for k, v in by_platform.items()},
            "按作者": by_author,
        }

    def prune_missing(self) -> int:
        """清掉索引里文件已被手动删除的记录。返回清理条数。"""
        gone = [pid for pid, r in self._records.items()
                if not (self.media_dir / r.file).exists()]
        for pid in gone:
            self._records.pop(pid, None)
        if gone:
            self.save()
        return len(gone)
