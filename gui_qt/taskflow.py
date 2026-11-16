# -*- coding: utf-8 -*-
"""流量捕手 · 轻量作业簿（P2 任务流第一片）

解决的是什么
────────────
客户真实作业是一条跨页流水线：找对标 → 扒文案 → 仿写 → 下素材 → 转录 → 成片。
改造前每一步都要自己记进度、自己搬中间产物、自己重填参数；关掉软件再打开，
「我上次做到哪了」全靠脑子记。这一层把「进度」显式化成一个本机作业簿：
一次作业串起六个动作，每一步可勾完成、可挂一个产物文件名，随时复制成清单。

第二片（2026-11-16，P2 补闭环：产物归位 + 目录快照）
────────────────────────
· 产物自动归位：扫各页保存目录，把「最近新增」的文件按目录角色挂到对应步骤
  （下载目录→下素材、转录输出→转文字稿）。只登记，不搬动、不复制原文件。
· 目录快照：作业记住本次各页保存目录；切换作业时写回 ctx，下游页自动指过来
  ——转录页的 _scan() 本来就自动读 ctx.downloader.save_dir，这条链路零侵入就通。

边界（始终不做）
────────────────
· 不新增第 20 个页面，也不进 NAV_GROUPS / FEATURES —— 只作为工作台的一张卡存在。
· 不侵入任何业务页的落盘逻辑、不自动回填各页输入框参数 —— 高风险，页面是常驻
  缓存的，改 settings 不会回填已经构造好的控件。快照走「目录」这条现成通路。
· 不联网、不上报。这里存的路径只在本机作业簿里；复制成清单时只留文件名。

设计纪律
────────
· 纯标准库、不依赖 Qt / 业务模块 —— 门禁可以脱离 GUI 直接跑运行时断言。
· 所有路径可注入（path 参数），默认落 %APPDATA%/TrafficCatcher/taskflow.json，
  这样沙盒测试不会碰用户的真实作业簿。
· 原子写（临时文件 + fsync + os.replace）：断电时不会留下半截 JSON。
· 读损坏自动降级为空簿并把坏文件另存 .bad，绝不因为作业簿坏掉拖垮启动。
· 字段白名单：簿里只允许 version/jobs/active_id 三层固定键，
  不存 token / 授权码 / 机器码 / 客户名 —— 见 _validate_taskflow.py 的断言。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

# 六步流水线。每项 (key, 标题, 跳转页面, 一句话提示)。
# ★ 跳转页面必须都在 main_window.NAV_GROUPS 里，门禁会逐个核对；
#   「文案仿写」占两步是有意的——「扒文案」和「仿写成稿」是两个不同动作，
#   但共用同一个页面，下一步按钮会把人带回那里。
STEPS = [
    ("benchmark", "找对标爆款", "选题挖掘", "挑一条数据真跑起来的"),
    ("extract",   "扒出它的文案", "文案仿写", "粘链接，自动转成口播稿"),
    ("rewrite",   "仿写成你的", "文案仿写", "选体裁时长，出分镜脚本"),
    ("material",  "下载素材", "视频下载", "批量下载无水印视频"),
    ("transcribe", "转成文字稿", "音视频转文本", "口播转文字，剪辑更好对齐"),
    ("finish",    "剪出成片", "短剧成片", "把素材合成能发的成品"),
]

STEP_KEYS = tuple(s[0] for s in STEPS)
STEP_PAGES = tuple(s[2] for s in STEPS)

# 字段白名单（门禁断言用；也是 load() 清洗的唯一依据）
BOOK_FIELDS = ("version", "jobs", "active_id")
JOB_FIELDS = ("id", "title", "created", "steps", "dirs")
STEP_FIELDS = ("key", "done", "artifact", "note")

# 目录角色：ctx.settings 里现成的保存目录键 → 产物该挂到哪一步。
# ★ 只映射语义明确的三条；直播录制归到「素材」（它是素材来源，不是成片）。
#   不映射的目录一律不扫，避免把无关文件挂进作业。
DIR_ROLE = {
    "dl_save_dir": "material",          # 视频下载 → 下素材
    "lm_save_dir": "material",          # 直播录制 → 下素材
    "transcribe_out_dir": "transcribe", # 转录输出 → 转文字稿
}

# 每一类步骤认的文件扩展名（小写）。按步骤挂，跨类型不混。
STEP_EXTS = {
    "material": (".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".m4v"),
    "transcribe": (".txt", ".srt", ".vtt", ".ass", ".json", ".md"),
}
_ART_SEP = " | "                         # 一个步骤可挂多个产物，
_MAX_ART = 12                            # 但要限量，别撑爆 240 字符字段

BOOK_VERSION = 1
MAX_BOOK_BYTES = 1_048_576          # 1 MB；作业簿正常几千字节，超了说明写坏了

# 这些词不许出现在字段名里（防止哪天有人顺手把 Token 塞进作业簿）
_FORBIDDEN_KEYS = ("token", "secret", "password", "license", "machine",
                   "customer", "api_key", "apikey", "credential", "cookie")


# ------------------------------------------------------------------ 路径
def default_path() -> Path:
    """默认作业簿路径。优先复用 branding 的应用数据目录，失败回退 APPDATA。"""
    try:
        from justoneapi_collector import branding
        return Path(branding.app_data_dir()) / "taskflow.json"
    except Exception:                                          # noqa: BLE001
        return (Path(os.environ.get("APPDATA", str(Path.home())))
                / "TrafficCatcher" / "taskflow.json")


def empty_book() -> dict:
    return {"version": BOOK_VERSION, "jobs": [], "active_id": ""}


# ------------------------------------------------------------------ 读写
def load(path=None) -> dict:
    """读作业簿。文件缺失 / 损坏 / 结构不对一律降级为空簿，不抛异常。

    损坏文件不会被直接覆盖：改名为 taskflow.bad-<秒级时间戳>.json 留底，
    这样即便清洗丢掉了内容，用户也还有原件可找回。
    """
    p = Path(path) if path is not None else default_path()
    if not p.is_file():
        return empty_book()
    try:
        if p.stat().st_size > MAX_BOOK_BYTES:
            raise ValueError("作业簿超过 1MB，拒绝读取")
        data = json.loads(p.read_text(encoding="utf-8"))
        return _sanitize(data)
    except Exception:                                          # noqa: BLE001
        try:
            bad = p.with_name(f"{p.stem}.bad-{int(time.time())}.json")
            p.replace(bad)
        except Exception:                                      # noqa: BLE001
            pass
        return empty_book()


def save(book: dict, path=None) -> bool:
    """原子写。成功 True；写失败 False（调用方给一句提示即可，不该崩界面）。"""
    p = Path(path) if path is not None else default_path()
    try:
        text = json.dumps(_sanitize(book), ensure_ascii=False, indent=2)
        if len(text.encode("utf-8")) > MAX_BOOK_BYTES:
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".taskflow-", suffix=".json",
                                   dir=str(p.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return True
    except Exception:                                          # noqa: BLE001
        return False


def _sanitize(data) -> dict:
    """只保留白名单字段；非法步骤键丢弃，未知字段一律不进内存。

    这是隐私红线的第二道闸：第一道是门禁断言源码不写敏感键，
    第二道是这里——就算旧版本真的写进去了，读回来也会被洗掉。
    """
    out = empty_book()
    if not isinstance(data, dict):
        return out
    jobs = data.get("jobs")
    if isinstance(jobs, list):
        for raw in jobs[:200]:                                 # 上限，防异常大文件
            if not isinstance(raw, dict):
                continue
            steps = []
            raw_steps = raw.get("steps")
            if isinstance(raw_steps, list):
                by_key = {s.get("key"): s for s in raw_steps
                          if isinstance(s, dict) and s.get("key") in STEP_KEYS}
            else:
                by_key = {}
            for key, title, page, hint in STEPS:
                s = by_key.get(key) or {}
                steps.append({
                    "key": key,
                    "done": bool(s.get("done")),
                    # 归位后一个步骤可挂多个产物，上限放宽到 1000
                    "artifact": _clean_text(s.get("artifact"), 1000),
                    "note": _clean_text(s.get("note"), 240),
                })
            jid = _clean_text(raw.get("id"), 64)
            if not jid:
                continue
            # 目录快照：只留 DIR_ROLE 认得的键，值一律当路径字符串处理。
            # 不把任意字段放进来 —— 防止哪天有人往 dirs 里塞 token。
            dirs = {}
            raw_dirs = raw.get("dirs")
            if isinstance(raw_dirs, dict):
                for dk in DIR_ROLE:
                    dv = _clean_text(raw_dirs.get(dk), 500)
                    if dv:
                        dirs[dk] = dv
            out["jobs"].append({
                "id": jid,
                "title": _clean_text(raw.get("title"), 80) or "未命名作业",
                "created": _clean_text(raw.get("created"), 32),
                "steps": steps,
                "dirs": dirs,
            })
        ids = {j["id"] for j in out["jobs"]}
        aid = _clean_text(data.get("active_id"), 64)
        out["active_id"] = aid if aid in ids else ""
    return out


def _clean_text(value, limit: int) -> str:
    if value is None:
        return ""
    try:
        s = str(value)
    except Exception:                                          # noqa: BLE001
        return ""
    # 控制字符（含 NUL）会让 JSON 显示成乱码，也可能来自断电损坏的文件
    s = "".join(ch for ch in s if ch >= " " or ch in "\t\n")
    return s[:limit]


# ------------------------------------------------------------------ 业务
def create(book: dict, title: str) -> dict:
    """新建作业并设为当前。返回新作业（调用方负责 save）。"""
    import uuid
    job = {
        "id": uuid.uuid4().hex[:12],
        "title": _clean_text(title, 80) or "未命名作业",
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "steps": [{"key": k, "done": False, "artifact": "", "note": ""}
                  for k, _t, _p, _h in STEPS],
        "dirs": {},
    }
    book.setdefault("jobs", []).insert(0, job)
    book["active_id"] = job["id"]
    del book["jobs"][200:]
    return job


def active_job(book: dict) -> dict | None:
    for j in book.get("jobs", []):
        if j.get("id") == book.get("active_id"):
            return j
    return None


def set_active(book: dict, job_id: str) -> bool:
    if any(j.get("id") == job_id for j in book.get("jobs", [])):
        book["active_id"] = job_id
        return True
    return False


def mark_step(book: dict, job_id: str, step_key: str,
              *, done: bool | None = None, artifact: str = "",
              note: str = "") -> bool:
    """勾一步 / 挂产物名。done=None 表示翻转；返回是否找到这一步。"""
    for j in book.get("jobs", []):
        if j.get("id") != job_id:
            continue
        for s in j.get("steps", []):
            if s.get("key") != step_key:
                continue
            s["done"] = (not s["done"]) if done is None else bool(done)
            if artifact:
                s["artifact"] = _clean_text(artifact, 240)
            if note:
                s["note"] = _clean_text(note, 240)
            return True
    return False


def next_step(job: dict | None) -> dict | None:
    """第一个未完成的步骤（含页面名，供 UI 一键跳转）；全做完返回 None。"""
    if not job:
        return None
    for step in job.get("steps", []):
        if not step.get("done"):
            key = step.get("key")
            for k, title, page, hint in STEPS:
                if k == key:
                    return {"key": k, "title": title, "page": page,
                            "hint": hint, "artifact": step.get("artifact", ""),
                            "note": step.get("note", "")}
    return None


def progress(job: dict | None) -> tuple[int, int, int]:
    """(已完成数, 总数, 百分比)。无作业返回 (0, 6, 0)。"""
    steps = (job or {}).get("steps", [])
    total = len(STEPS)
    done = sum(1 for s in steps if s.get("done"))
    return done, total, int(100 * done / total) if total else 0


def delete_job(book: dict, job_id: str) -> bool:
    before = len(book.get("jobs", []))
    book["jobs"] = [j for j in book.get("jobs", []) if j.get("id") != job_id]
    if len(book["jobs"]) == before:
        return False
    if book.get("active_id") == job_id:
        book["active_id"] = book["jobs"][0]["id"] if book["jobs"] else ""
    return True


# ------------------------------------------------------------ 第二片：归位/快照
def snapshot_dirs(book: dict, job_id: str, ctx_dirs: dict) -> int:
    """把当前各页保存目录存进作业（写快照）。返回存了几个。

    ctx_dirs 形如 {"dl_save_dir": "D:/...", "transcribe_out_dir": "..."}。
    只收 DIR_ROLE 认得的键 —— 调用方传整份 settings 进来也不会存进敏感字段。
    """
    n = 0
    for j in book.get("jobs", []):
        if j.get("id") != job_id:
            continue
        dirs = j.setdefault("dirs", {})
        for dk in DIR_ROLE:
            v = _clean_text((ctx_dirs or {}).get(dk), 500)
            if v:
                dirs[dk] = v
                n += 1
        break
    return n


def apply_dirs(book: dict, job_id: str) -> dict:
    """取作业保存的目录快照，供 UI 写回 ctx。返回 {settings键: 路径}。

    只返回目录键，绝不返回 token 之类 —— 调用方拿到的东西是安全的。
    """
    for j in book.get("jobs", []):
        if j.get("id") == job_id:
            return {k: v for k, v in (j.get("dirs") or {}).items()
                    if k in DIR_ROLE and isinstance(v, str) and v}
    return {}


def scan_artifacts(book: dict, job_id: str, ctx_dirs: dict,
                   *, hours: float = 48.0, now: float | None = None) -> dict:
    """扫各页保存目录，把最近新增的产物登记到对应步骤。只登记不搬文件。

    · 时间窗默认 48 小时：跨天作业也能归位，又不会把陈年旧文件全挂上。
    · 去重：按「文件名」比对，重复调用不会产生双份。
    · 按目录角色挂：下载目录→下素材，转录输出→转文字稿；不认识的不碰。
    · now 可注入，门禁用固定时间戳测，不受真实时钟影响。
    返回 {"scanned": 扫到的候选数, "attached": 新增登记数, "by_step": {...}}。
    """
    import time as _time
    ref = _time.time() if now is None else float(now)
    cutoff = ref - max(0.0, hours) * 3600
    result = {"scanned": 0, "attached": 0, "by_step": {}}
    job = next((j for j in book.get("jobs", []) if j.get("id") == job_id), None)
    if job is None:
        return result

    for dk, step_key in DIR_ROLE.items():
        raw = (ctx_dirs or {}).get(dk)
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            d = Path(raw.strip())
            if not d.is_dir():
                continue
            exts = STEP_EXTS.get(step_key, ())
            step = next((s for s in job.get("steps", [])
                         if s.get("key") == step_key), None)
            if step is None:
                continue
            existing = [x.strip() for x in str(step.get("artifact") or "").split(_ART_SEP)
                        if x.strip()]
            have = {Path(x).name for x in existing}
            added = []
            # 只扫目录顶层的常规文件；递归会把整个下载库翻出来，又慢又容易误挂
            for p in sorted(d.iterdir(), key=lambda q: q.stat().st_mtime, reverse=True):
                try:
                    if not p.is_file() or p.suffix.lower() not in exts:
                        continue
                    if p.stat().st_mtime < cutoff:
                        continue
                except OSError:                                # noqa: BLE001
                    continue
                result["scanned"] += 1
                if p.name in have or len(existing) + len(added) >= _MAX_ART:
                    continue
                have.add(p.name)
                added.append(str(p))
            if added:
                step["artifact"] = _clean_text(
                    _ART_SEP.join(existing + added), 1000)
                result["attached"] += len(added)
                result["by_step"][step_key] = len(added)
        except OSError:                                        # noqa: BLE001
            continue                                           # 目录读不了就跳过
    return result


def render_summary(job: dict | None) -> str:
    """复制成纯文本清单。产物只留文件名（basename），不带本地路径。"""
    if not job:
        return "（还没有作业）"
    done, total, pct = progress(job)
    lines = [f"流量捕手 · 作业：{job.get('title', '')}",
             f"进度：{done}/{total}（{pct}%）  建于 {job.get('created', '')}",
             ""]
    for i, (key, title, page, hint) in enumerate(STEPS, 1):
        s = next((x for x in job.get("steps", []) if x.get("key") == key), {})
        mark = "x" if s.get("done") else " "
        tail = ""
        art = _clean_text(s.get("artifact"), 1000)
        if art:
            # 一个步骤可能挂了多个产物（" | " 分隔）。逐段只留文件名，
            # 复制出去时不暴露本机目录结构；也兼容第一片的单路径格式。
            names = [Path(x.strip()).name for x in art.split(_ART_SEP)
                     if x.strip()]
            if names:
                tail = f"  [{'、'.join(names)}]"
        note = _clean_text(s.get("note"), 240)
        if note:
            tail += f"  · {note}"
        lines.append(f"  [{mark}] {i}. {title}{tail}")
    nxt = next_step(job)
    if nxt:
        lines += ["", f"下一步：{nxt['title']}（{nxt['page']}）— {nxt['hint']}"]
    else:
        lines += ["", "六步全完成，可以收工了。"]
    return "\n".join(lines)


# ------------------------------------------------------------------ 自检
def self_check() -> list[str]:
    """返回问题列表；空列表=通过。给门禁和启动自检共用。"""
    problems = []
    if len(STEPS) != 6:
        problems.append("流水线不是 6 步")
    if len(set(STEP_KEYS)) != 6:
        problems.append("步骤 key 有重复")
    for k, t, page, hint in STEPS:
        if not all((k, t, page, hint)):
            problems.append(f"步骤 {k} 字段不全")
    for field in BOOK_FIELDS + JOB_FIELDS + STEP_FIELDS:
        low = field.lower()
        if any(bad in low for bad in _FORBIDDEN_KEYS):
            problems.append(f"字段白名单含敏感键 {field}")
    return problems
