"""微信「合并转发」聊天归档解析与导出（纯本地，零外部依赖、零网络）。

背景
----
用户在微信里多选聊天记录 →「合并转发」→ 保存到文件，会得到一个 ZIP：
里面有一份 ``聊天记录.txt``（逐条消息）+ 图片/视频等附件。本模块把这个 ZIP
解析成结构化消息，并提供三种本地出口：

1. ``transcript_to_text()``  —— 纯文本，供页面复制到剪贴板；
2. ``render_obsidian_note()`` —— Obsidian 笔记（YAML frontmatter + 消息 + 附件链接）；
3. ``save_archive_copy()``    —— 原始 ZIP 留存（笔记里引用，保证可重建）。

格式来源
--------
移植自开源项目 WeChatBridge（MIT, github.com/freestylefly/WeChatBridge）的
``WeChatTranscriptRecord`` / ``WeChatNativeArchive`` / ``ObsidianNote``。
微信写出的 TXT 是行式、 undocumented 的格式::

    ·发言人
    2026年9月20日 09:10
    第一行
    第二行

    ·另一个人
    2026年9月20日 09:11
    收到

即 ``·sender`` 头 + ``yyyy年M月d日 HH:mm`` 时间头 + 正文（到下一个头为止）。

安全边界（对齐上游 fail-closed 策略）
-------------------------------------
* 只处理用户主动选择的本地 ZIP；不读微信数据库、不注入微信进程、不扫描目录、不联网。
* TXT 单条目上限 16MB、解压总量上限 1GB，防止 ZIP 炸弹。
* 用标准库 zipfile 读取，拒绝加密条目；附件只按 basename 保存，杜绝路径穿越。
* 解析失败的 TXT 保留原文（body），但 records=None，不阻塞导出。
"""
from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

# 与上游一致的安全上限
MAX_TEXT_ENTRY_BYTES = 16 * 1024 * 1024        # 16MB：单个 TXT 条目
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024         # 1GB：解压总量

# 消息头：·发言人 \n yyyy年M月d日 HH:mm \n
_HEADER_RE = re.compile(
    r"^·([^\n]+)\n(\d{4})年(\d{1,2})月(\d{1,2})日 (\d{2}):(\d{2})\n",
    re.MULTILINE,
)
# 正文里形如 [图片] xxx.jpg / [视频] xxx.mp4 的附件引用
_ATTACH_RE = re.compile(r"\[(图片|视频|语音|文件)\]\s*([^\n\r]+?)\s*$", re.MULTILINE)
EMBEDDABLE = {
    "png", "jpg", "jpeg", "gif", "bmp", "webp", "tif", "tiff", "heic", "avif",
    "mp4", "mov", "m4v", "avi", "mkv", "wmv", "flv", "webm", "mpg", "mpeg", "3gp",
    "mp3", "m4a", "aac", "wav", "flac", "ogg", "opus", "amr", "wma",
    "pdf",
}


class ArchiveError(ValueError):
    """归档不可读 / 不安全。页面层捕获后给用户中文原因。"""


@dataclass
class Message:
    """一条微信消息。"""

    sender: str
    at: datetime
    text: str
    attachments: list[str] = field(default_factory=list)


@dataclass
class Transcript:
    """一份聊天归档的解析结果。"""

    source: str                       # ZIP 文件路径
    txt_name: str                     # 命中的 TXT 条目名
    body: str                         # TXT 原文（解析失败也有）
    messages: list[Message]           # 解析出的消息（失败为 []）
    media_names: list[str] = field(default_factory=list)  # ZIP 内附件相对名
    ok: bool = True                   # TXT 是否成功解析成消息

    @property
    def senders(self) -> list[str]:
        seen: list[str] = []
        for m in self.messages:
            if m.sender and m.sender not in seen:
                seen.append(m.sender)
        return seen

    @property
    def start(self) -> datetime | None:
        return min((m.at for m in self.messages), default=None)

    @property
    def end(self) -> datetime | None:
        return max((m.at for m in self.messages), default=None)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def _safe_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """读取中央目录并做安全校验：拒绝加密、超大、ZIP 炸弹。"""
    infos = zf.infolist()
    if not infos:
        raise ArchiveError("归档是空的")
    total = 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise ArchiveError("归档条目已加密，无法读取")
        total += info.file_size
    if total > MAX_EXPANDED_BYTES:
        raise ArchiveError("归档解压后超过 1GB，已拒绝（可能是异常文件）")
    return infos


def _decode(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def parse_transcript_records(text: str) -> list[Message]:
    """把 TXT 正文解析成消息列表；格式不符抛 ArchiveError。

    与上游一致：第一个匹配必须从位置 0 开始，否则视为「不认识的 TXT」，
    fail-closed，不猜测内容。
    """
    normalized = text.replace("\r\n", "\n").lstrip("\ufeff")
    matches = list(_HEADER_RE.finditer(normalized))
    if not matches or matches[0].start() != 0:
        raise ArchiveError("聊天记录格式无法识别（未找到以「·发言人」开头的消息头）")
    out: list[Message] = []
    for i, m in enumerate(matches):
        y, mo, d, hh, mm = (int(m.group(k)) for k in range(2, 7))
        try:
            at = datetime(y, mo, d, hh, mm)
        except ValueError as exc:
            raise ArchiveError(f"聊天记录里出现非法时间：{y}-{mo}-{d} {hh}:{mm}") from exc
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(normalized)
        raw = normalized[start:end].strip()
        refs = [hit.group(2).strip() for hit in _ATTACH_RE.finditer(raw)]
        out.append(Message(sender=m.group(1).strip(), at=at, text=raw, attachments=refs))
    return out


def read_archive(zip_path: str | Path) -> Transcript:
    """读取一份微信合并转发 ZIP，返回解析结果。

    TXT 选择规则（对齐上游）：优先名为 ``聊天记录.txt`` 的条目；否则取
    解析出消息最多的那个 TXT。全部解析失败时 ok=False、body 仍保留原文。
    """
    p = Path(zip_path)
    if not p.is_file():
        raise ArchiveError(f"文件不存在：{p.name}")
    try:
        with zipfile.ZipFile(p) as zf:
            infos = _safe_members(zf)
            media = [
                i.filename for i in infos
                if not i.is_dir() and not i.filename.lower().endswith(".txt")
            ]
            candidates: list[tuple[str, str, list[Message]]] = []
            for info in infos:
                if info.is_dir() or not info.filename.lower().endswith(".txt"):
                    continue
                if info.file_size > MAX_TEXT_ENTRY_BYTES:
                    continue
                data = zf.read(info)
                text = _decode(data)
                if text is None:
                    continue
                try:
                    records = parse_transcript_records(text)
                except ArchiveError:
                    records = []
                candidates.append((info.filename, text, records))
            if not candidates:
                raise ArchiveError("归档里没有可读取的 TXT 聊天记录")
            native = next((c for c in candidates if Path(c[0]).name == "聊天记录.txt"), None)
            chosen = native or max(candidates, key=lambda c: len(c[2]))
            name, body, records = chosen
    except zipfile.BadZipFile as exc:
        raise ArchiveError("这不是有效的 ZIP 归档（微信合并转发保存的文件应当以 .zip 结尾）") from exc
    except ArchiveError:
        raise
    except OSError as exc:
        raise ArchiveError(f"读取文件失败：{exc}") from exc
    return Transcript(
        source=str(p), txt_name=name, body=body, messages=records,
        media_names=media, ok=bool(records),
    )


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------
def transcript_to_text(t: Transcript) -> str:
    """转成可直接粘贴给 AI 的纯文本。"""
    if not t.ok:
        return t.body.strip()
    lines: list[str] = []
    for m in t.messages:
        lines.append(f"[{m.at:%Y-%m-%d %H:%M}] {m.sender}：")
        lines.append(m.text)
        lines.append("")
    return "\n".join(lines).rstrip()


def _quote_yaml(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def note_title(t: Transcript, chat_name: str = "") -> str:
    """笔记标题：显式群名优先，否则按参与人推断。"""
    name = (chat_name or "").strip()
    if name:
        return name if name.endswith("的聊天") else f"{name}的聊天"
    senders = t.senders
    if len(senders) == 1:
        return f"{senders[0]}的聊天"
    if len(senders) == 2:
        return f"{senders[0]}与{senders[1]}的聊天"
    if len(senders) > 2:
        return f"{senders[0]}等{len(senders)}人的聊天"
    return Path(t.source).stem


def render_obsidian_note(
    t: Transcript,
    chat_name: str = "",
    scene_name: str = "",
    archive_name: str = "",
) -> str:
    """生成 Obsidian 笔记 Markdown（移植上游 ObsidianNote.Render 的核心格式）。"""
    title = note_title(t, chat_name)
    created = datetime.now()
    yaml = ["---", f"title: {_quote_yaml(title)}", "source: WeChat"]
    if chat_name.strip():
        yaml.append(f"chat: {_quote_yaml(chat_name.strip())}")
    if scene_name.strip():
        yaml.append(f"scene: {_quote_yaml(scene_name.strip())}")
    yaml.append(f"exported: {created:%Y-%m-%dT%H:%M:%S}")
    if t.messages:
        yaml.append(f"messages: {len(t.messages)}")
    if archive_name:
        yaml.append(f"archive: {_quote_yaml('附件/' + archive_name)}")
    yaml.append("---")

    lines = yaml + ["", f"# {title}", ""]
    if archive_name:
        lines.append(f"> 来源：微信 · 原始归档：[[附件/{archive_name}]]")
    if t.ok:
        lines += ["", "## 聊天记录"]
        for m in t.messages:
            lines += ["", f"**{m.sender}** · {m.at:%Y-%m-%d %H:%M}", "", m.text]
    else:
        lines += ["", "## 原始内容（格式未识别，原样保留）", "", t.body.strip()]
    return "\n".join(lines).rstrip() + "\n"


def _safe_basename(name: str) -> str:
    """只取文件名，杜绝 ../ 与绝对路径穿越。"""
    base = Path(name.replace("\\", "/")).name
    return base if base and base not in (".", "..") else "unnamed"


def save_archive_copy(zip_path: str | Path, dest_dir: str | Path) -> Path:
    """把原始 ZIP 复制到目标目录的「附件」子目录，返回目标路径。"""
    src = Path(zip_path)
    root = Path(dest_dir) / "附件"
    root.mkdir(parents=True, exist_ok=True)
    target = root / _safe_basename(src.name)
    if target.resolve() != src.resolve():
        shutil.copy2(src, target)
    return target


def extract_media(zip_path: str | Path, dest_dir: str | Path) -> list[str]:
    """把 ZIP 里的附件（非 TXT）安全落到「附件」目录，返回落盘文件名列表。"""
    src = Path(zip_path)
    root = Path(dest_dir) / "附件"
    root.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    try:
        with zipfile.ZipFile(src) as zf:
            _safe_members(zf)
            for info in zf.infolist():
                if info.is_dir() or info.filename.lower().endswith(".txt"):
                    continue
                name = _safe_basename(info.filename)
                target = root / name
                if target.exists():                       # 重名加序号，不覆盖
                    stem, suf = Path(name).stem, Path(name).suffix
                    i = 2
                    while target.exists():
                        target = root / f"{stem}_{i}{suf}"
                        i += 1
                with zf.open(info) as fin, open(target, "wb") as fout:
                    shutil.copyfileobj(fin, fout)
                saved.append(target.name)
    except (zipfile.BadZipFile, OSError):
        return saved
    return saved


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suf = path.stem, path.suffix
    i = 2
    while True:
        candidate = path.with_name(f"{stem}_{i}{suf}")
        if not candidate.exists():
            return candidate
        i += 1


def export_note(
    t: Transcript,
    out_dir: str | Path,
    chat_name: str = "",
    scene_name: str = "",
    keep_archive: bool = True,
    keep_media: bool = True,
) -> Path:
    """把一个归档导出成 Obsidian 笔记文件，返回笔记路径。

    ``out_dir`` 由页面层给定：可以是 Obsidian vault 里的子目录，也可以是
    普通文件夹——两者格式一致， Obsidian 打开即是可双链的笔记。
    """
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    archive_name = ""
    if keep_archive:
        copy = save_archive_copy(t.source, root)
        archive_name = copy.name
    if keep_media:
        extract_media(t.source, root)
    md = render_obsidian_note(t, chat_name, scene_name, archive_name)
    title = note_title(t, chat_name)
    safe = re.sub(r'[\\/:*?"<>|]', "_", title).strip() or "微信聊天"
    target = _unique_path(root / f"{safe}.md")
    target.write_text(md, encoding="utf-8")
    return target


def summarize(items: Iterable[Transcript]) -> dict:
    """给页面统计卡用：归档数 / 消息数 / 发言人数。"""
    items = list(items)
    senders: set[str] = set()
    for t in items:
        senders.update(s for s in t.senders if s)
    return {
        "archives": len(items),
        "messages": sum(len(t.messages) for t in items),
        "senders": len(senders),
        "failed": sum(1 for t in items if not t.ok),
    }
