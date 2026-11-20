# -*- coding: utf-8 -*-
"""见舟工作台桥接（2026-11-20）。

见舟 · 自媒体运营工作台（github.com/tangchengjiao/jianzhou-media-workbench）
是一个**独立的本地 Web 应用**（Python FastAPI + Vue 3 + SQLite，默认监听
127.0.0.1:8741）。它不是流量捕手的一个模块，本文件也不复制它的任何源码
（上游仓库未附许可证，只做公开接口对接）。

本模块只做三件小事，全部走本地回环地址：
  1. 探测 ``/healthz``，把见舟的运行状态显示到流量捕手里；
  2. 把流量捕手采集到的线索按见舟公开的 ``POST /radar/signals/manual``
     接口推进「内容雷达」；
  3. 给出安装 / 启动指引，方便没装见舟的客户自己部署。

安全边界（不可放宽）：
  * 只接受 127.0.0.1 / localhost，禁止指向公网 —— 防止把本机当跳板打别人；
  * 显式禁用环境代理 —— 客户机器上的 Clash / 系统代理不能截走回环请求；
  * 所有读取都有字节上限与超时，失败一律返回结构化错误，不抛到界面线程。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_BASE = "http://127.0.0.1:8741"
PROJECT_URL = "https://github.com/tangchengjiao/jianzhou-media-workbench"
PROJECT_NAME = "见舟 · 自媒体运营工作台"

MAX_ROWS = 50
MAX_TITLE = 500
MAX_URL = 2048
MAX_NOTE = 2000
_MAX_RESPONSE_BYTES = 256 * 1024
_ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


def normalize_base(raw: str | None) -> str:
    """把用户输入规整为安全的本机基址；非本机地址直接拒绝。"""
    text = (raw or "").strip().rstrip("/")
    if not text:
        return DEFAULT_BASE
    if not text.startswith(("http://", "https://")):
        text = "http://" + text
    parts = urllib.parse.urlsplit(text)
    if parts.hostname not in _ALLOWED_HOSTS:
        raise ValueError("只允许连接本机的见舟服务（127.0.0.1 或 localhost）")
    if parts.username or parts.password:
        raise ValueError("见舟是本机服务，不需要账号密码")
    if parts.path not in ("", "/"):
        raise ValueError("地址不要带路径，例如 http://127.0.0.1:8741")
    if parts.query or parts.fragment:
        raise ValueError("地址不要带参数或 #")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:                                # noqa: BLE001
        raise ValueError("端口写法不正确") from exc
    if not 1 <= port <= 65535:
        raise ValueError("端口超出范围")
    return f"{parts.scheme}://{parts.hostname}:{port}"


def _opener() -> urllib.request.OpenerDirector:
    """禁用环境代理：本机回环请求绝不能被系统代理 / Clash 截走。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _friendly_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"见舟返回 HTTP {exc.code}（请确认版本 ≥ v2.1.0）"
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", exc)
        text = str(reason)
        if "refused" in text.lower() or "10061" in text:
            return "连不上：见舟还没启动（默认 http://127.0.0.1:8741）"
        if "timed out" in text.lower() or "timedout" in text.lower():
            return "连接超时：见舟进程可能卡住或端口被占用"
        return f"连不上见舟：{text}"
    if isinstance(exc, ValueError):
        return str(exc)
    if isinstance(exc, (TimeoutError, OSError)):
        return f"连接失败：{exc}"
    return f"连接失败：{exc}"


def _request_json(url: str, timeout: float, payload: dict | None = None) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    with _opener().open(req, timeout=timeout) as resp:
        raw = resp.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("见舟返回数据过大，已拒绝读取")
    result = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(result, dict):
        raise ValueError("见舟返回格式不正确")
    return result


def _unwrap(result: dict) -> Any:
    """见舟统一用 {"success": true, "data": ...} 包装；兼容裸结构。"""
    if "data" in result and isinstance(result.get("success"), bool):
        return result["data"]
    return result


def probe(raw_base: str, timeout: float = 3.0) -> dict:
    """探测见舟 /healthz，返回可直接展示给客户的结构化状态。"""
    try:
        base = normalize_base(raw_base)
    except ValueError as exc:
        return {"ok": False, "base": raw_base, "error": str(exc)}
    try:
        data = _unwrap(_request_json(base + "/healthz", timeout=timeout))
        if not isinstance(data, dict) or not data.get("ok"):
            raise ValueError("见舟健康检查未通过")
        caps = data.get("capabilities") or {}
        enabled = [key for key, value in caps.items() if value] if isinstance(caps, dict) else []
        return {
            "ok": True,
            "base": base,
            "demo_mode": bool(data.get("demo_mode")),
            "schema_version": data.get("schema_version", "—"),
            "build_id": str(data.get("build_id") or "")[:40],
            "capabilities": enabled,
            "summary": (
                f"已连接 · {'演示数据' if data.get('demo_mode') else '真实模式'} · "
                f"数据库 v{data.get('schema_version', '—')}"
            ),
        }
    except Exception as exc:                                 # noqa: BLE001
        return {"ok": False, "base": base, "error": _friendly_error(exc)}


def parse_rows(text: str) -> tuple[list[dict], list[str]]:
    """解析批量推送框。

    每行支持 ``平台|标题|链接|备注``，其中标题必填、其余可省；
    也可以只贴一个网址（自动当标题）。``#`` 开头视为注释。
    返回 (条目列表, 格式错误行说明)。
    """
    rows: list[dict] = []
    errors: list[str] = []
    for index, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cells = [cell.strip() for cell in line.split("|")]
        platform, title, url, note = "", "", "", ""
        if len(cells) == 1:
            value = cells[0]
            if value.startswith(("http://", "https://")):
                url, title = value, value
            else:
                title = value
        elif len(cells) == 2:
            first, second = cells
            if second.startswith(("http://", "https://")):
                title, url = first, second
            elif first.startswith(("http://", "https://")):
                title, url = second, first
            else:
                # 「小红书|竞品封面拆解」这类没写链接的常见写法：前一段是平台。
                platform, title = first, second
        elif len(cells) == 3:
            platform, title, url = cells
        else:
            platform, title, url = cells[0], cells[1], cells[2]
            note = "|".join(cells[3:])
        if not title and url:
            title = url
        if not title:
            errors.append(f"第 {index} 行缺少标题")
            continue
        if len(rows) >= MAX_ROWS:
            errors.append(f"第 {index} 行起超出每次 {MAX_ROWS} 条上限，已忽略")
            break
        rows.append({
            "platform": platform[:50] or "流量捕手",
            "title": title[:MAX_TITLE],
            "url": url[:MAX_URL],
            "note": note[:MAX_NOTE],
        })
    return rows, errors


def push_signal(raw_base: str, item: dict, timeout: float = 8.0) -> dict:
    """把一条线索写进见舟「内容雷达 → 手动关注」。"""
    try:
        base = normalize_base(raw_base)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    title = str(item.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "标题不能为空"}
    payload = {
        "category": "manual",
        "title": title[:MAX_TITLE],
        "url": str(item.get("url") or "").strip()[:MAX_URL],
        "platform": str(item.get("platform") or "流量捕手").strip()[:50] or "流量捕手",
        "note": str(item.get("note") or "").strip()[:MAX_NOTE],
    }
    try:
        data = _unwrap(_request_json(base + "/radar/signals/manual", timeout, payload))
        if not isinstance(data, dict):
            raise ValueError("见舟返回格式不正确")
        return {
            "ok": True,
            "id": str(data.get("id") or ""),
            "existing": bool(data.get("existing")),
            "title": payload["title"],
            "note": str(data.get("note") or "已写入见舟内容雷达"),
        }
    except Exception as exc:                                 # noqa: BLE001
        return {"ok": False, "title": payload["title"], "error": _friendly_error(exc)}


def from_history(limit: int = 20, days: int = 365, order_by: str = "digg") -> list[dict]:
    """把流量捕手已采集的作品（历史库）转成见舟线索，供一键推送。

    只做格式转换，不联网、不写库；读不到历史库就返回空列表。
    """
    try:
        from . import history_store
        if not history_store.available():
            return []
        rows = history_store.top_posts(days=days, order_by=order_by,
                                       limit=min(max(1, int(limit)), MAX_ROWS))
    except Exception:                                        # noqa: BLE001
        return []
    out: list[dict] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        platform = str(item.get("platform") or "流量捕手")
        title = str(item.get("title") or "无标题作品")
        url = str(item.get("url") or "")
        digg = item.get("digg") or 0
        play = item.get("play") or 0
        note = f"流量捕手采集 · 点赞 {digg} · 播放 {play} · 作者 {item.get('author') or '未知'}"
        out.append({
            "platform": platform[:50],
            "title": title[:MAX_TITLE],
            "url": url[:MAX_URL],
            "note": note[:MAX_NOTE],
        })
    return out


def to_rows_text(items: list[dict]) -> str:
    """线索列表 → 推送框可编辑的文本（平台|标题|链接|备注）。"""
    lines = []
    for item in items:
        lines.append("|".join(str(item.get(k) or "") for k in
                              ("platform", "title", "url", "note")))
    return "\n".join(lines)


def install_hint() -> str:
    """给未安装见舟的客户一段可照抄的本机启动说明。"""
    return (
        "见舟是独立的本地工作台，不会自动装进本软件。要用桥接功能，请先在本机准备它：\n"
        "1. 打开项目地址：github.com/tangchengjiao/jianzhou-media-workbench（直接搜仓库名也能找到）。\n"
        "2. 在项目目录执行：python -m venv .venv\n"
        "3. Windows 再执行：.venv\\Scripts\\python -m pip install -r requirements.txt\n"
        "4. 设置 JIANZHOU_DEMO=1 后执行：.venv\\Scripts\\python app.py\n"
        "5. 浏览器能打开 127.0.0.1:8741 后，回到本页点「检测连接」。\n"
        "见舟的业务数据保存在它自己的 data/ 目录，本软件只读取健康状态并推送线索，不会碰它的数据库。"
    )


MODULE_LINKS = (
    ("工作总台", "聚合内容库存、今日行动与重要信号"),
    ("内容雷达", "对标账号、热点信号、行业数据源"),
    ("创作工坊", "方向、提纲、多平台稿件与体检"),
    ("渠道运营", "朋友圈与公众号文章的派生、排期与发布登记"),
    ("数据复盘", "T+1 / T+3 / T+7 快照与经验规则"),
)
