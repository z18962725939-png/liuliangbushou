# -*- coding: utf-8 -*-
"""抖音账号主页「开播状态」免费兜底 —— CDP 页面内 fetch。

★ 为什么需要它（2026-11-23 实测）
──────────────────────────────────────────────────────────────
直播监控的账号目标原本只能靠 JustOneAPI 的 `user-profile-v3` 查
`live_status`；客户账户余额不足（code=601 INSUFFICIENT BALANCE）时
整条链路断掉——明明主播在播，软件只能显示「状态未知」。

但同一份字段在**浏览器自己发的请求**里是免费的：
  主页 https://www.douyin.com/user/<sec_uid>
  → GET /aweme/v1/web/user/profile/other/?...&sec_user_id=<sec_uid>
  → user.live_status / user.room_id / user.room_id_str / user.nickname

签名参数由页面 JS 生成，我们不碰签名算法：先在真实浏览器里打开一个
主页，从 Network 事件里记下这条请求的完整 URL 作为**模板**，随后把
模板里的 sec_user_id 换成目标账号，在 douyin.com 页面内 `fetch`。
实测在播样本 3/3 返回 live_status=1 + 19 位 room_id_str，未播为 0。

设计约束：
  · 单例长驻浏览器，轮询多个账号不重复启动；
  · 所有 CDP 调用串行化（一把锁），避免并发导航互相踩；
  · 失败一律写 error，由调用方保持「状态未知」，绝不冒充未开播；
  · 连续失败进冷却，不让每轮轮询都白开一次浏览器；
  · 不新增依赖，不调用任何付费接口。
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .douyin_cdp_crawler import DouyinCDPCrawler
from .douyin_page_resolver import _JS_STEALTH

_API_PATH = "/aweme/v1/web/user/profile/other/"
_SEC_RE = re.compile(r"^MS4wLj[A-Za-z0-9_\-]+$")

# 页面内执行：只回传四个业务字段，避免把整包用户隐私带回来
_WEB_RID_JS = r"""JSON.stringify([...document.querySelectorAll('a')]
  .map(a=>a.href).find(h=>/live\.douyin\.com\/\d{6,}/.test(h))||'')"""

_FETCH_JS = """new Promise(resolve=>{
  fetch(%s,{credentials:'include'}).then(r=>r.text()).then(t=>{
    try{
      const o=JSON.parse(t), u=o.user||{};
      resolve(JSON.stringify({status:'ok', user:{
        live_status:u.live_status, room_id:String(u.room_id||'0'),
        room_id_str:String(u.room_id_str||''), nickname:String(u.nickname||''),
        sec_uid:String(u.sec_uid||'')
      }}));
    }catch(e){resolve(JSON.stringify({status:'bad', head:String(t||'').slice(0,120)}));}
  }).catch(e=>resolve(JSON.stringify({status:'err', msg:String(e)})));
})"""


@dataclass
class HomeLiveSnapshot:
    """一次主页状态查询的结果。ok=False 时 error 说明原因。

    web_rid 是在播主页上的 `live.douyin.com/<数字>` 入口——19 位
    room_id_str 是内部房间号，**不能**拿去 webcast 解析流地址
    （实测返回 no room data），自动录制必须用这个 web_rid。
    """
    ok: bool
    live_status: int = 0
    room_id: str = ""
    nickname: str = ""
    sec_uid: str = ""
    web_rid: str = ""
    error: str = ""


class DouyinHomeLiveCDP:
    """账号主页开播状态查询器（免费、单例、线程安全）。"""

    TEMPLATE_TTL = 900.0     # 签名 URL 模板有效期；过期重新抓一次
    MAX_FAILS = 3            # 连续失败这么多次后进入冷却
    COOLDOWN = 60.0          # 冷却时长（秒）

    def __init__(self, cookie: str = "", profile_dir: str | Path = "",
                 headless: bool = True, timeout: float = 25.0):
        self.cookie = (cookie or "").strip()
        if profile_dir:
            base = Path(profile_dir)
        else:
            try:
                from . import branding
                base = branding.app_data_dir() / "browser_profile"
            except Exception:                       # noqa: BLE001
                import os
                base = Path(os.environ.get("APPDATA") or str(Path.home()))
                base = base / "TrafficCatcher" / "browser_profile"
        # 与作品解析器一样用独立子目录：不抢客户正在用的浏览器 profile
        self.profile_dir = Path(base) / "_resolve"
        self.headless = headless
        self.timeout = timeout
        self._lock = threading.RLock()
        self._crawler: DouyinCDPCrawler | None = None
        self._start_error = ""
        self._api_url = ""
        self._api_at = 0.0
        self._fails = 0
        self._disabled_until = 0.0

    # -------------------------------------------------- 生命周期
    def set_cookie(self, cookie: str) -> None:
        """用户重新扫码后立即更新登录态，不必重建 monitor。"""
        new = (cookie or "").strip()
        if new == self.cookie:
            return
        self.cookie = new
        with self._lock:
            if self._crawler is not None and new:
                try:
                    self._crawler.add_cookies(new)
                except Exception:                   # noqa: BLE001
                    pass
            # 登录态换了，旧签名模板可能绑定旧会话，强制重抓
            self._api_url = ""
            self._api_at = 0.0

    def close(self) -> None:
        with self._lock:
            if self._crawler is not None:
                try:
                    self._crawler.stop(kill_browser=False)
                except Exception:                   # noqa: BLE001
                    pass
                self._crawler = None

    @property
    def last_error(self) -> str:
        return self._start_error or ""

    def _note_fail(self, reason: str) -> None:
        self._start_error = (reason or "浏览器通道失败")[:300]
        self._fails += 1
        if self._fails >= self.MAX_FAILS:
            self._disabled_until = time.time() + self.COOLDOWN
            self._fails = 0

    def _note_ok(self) -> None:
        self._fails = 0
        self._start_error = ""

    def _ensure(self) -> bool:
        if self._crawler is not None:
            return True
        if time.time() < self._disabled_until:
            return False
        try:
            crawler = DouyinCDPCrawler(self.profile_dir,
                                       headless=self.headless,
                                       timeout=self.timeout)
            ok, msg = crawler.start()
        except Exception as exc:                    # noqa: BLE001
            self._note_fail(f"启动浏览器失败：{exc}")
            return False
        if not ok:
            self._note_fail(msg or "启动浏览器失败")
            return False
        # 必须在导航之前注册反自动化脚本
        crawler.add_init_script(_JS_STEALTH)
        if self.cookie:
            try:
                crawler.add_cookies(self.cookie)
            except Exception:                       # noqa: BLE001
                pass
        try:
            crawler.open_url("https://www.douyin.com/")
            time.sleep(1.2)                         # 给 setCookies 落地的时间
        except Exception:                           # noqa: BLE001
            pass
        self._crawler = crawler
        return True

    # -------------------------------------------------- 抓签名模板
    def _capture_template(self, sec_uid: str) -> bool:
        """打开目标主页，从 Network 事件里记下页面自己发的 profile 请求。"""
        crawler = self._crawler
        page = crawler._page if crawler is not None else None
        if page is None:
            self._note_fail("浏览器页面会话不可用")
            return False
        try:
            page.call("Network.enable", {}, timeout=8)
        except Exception:                           # noqa: BLE001
            pass
        try:
            page.drain_events()                     # 清掉上一轮残留
        except Exception:                           # noqa: BLE001
            pass
        try:
            crawler.open_url("https://www.douyin.com/")
            time.sleep(0.8)
            if not crawler._ensure_origin(
                    f"https://www.douyin.com/user/{sec_uid}"):
                self._note_fail("主页打不开（可能需要重新扫码登录抖音）")
                return False
        except Exception as exc:                    # noqa: BLE001
            self._note_fail(f"打开主页失败：{exc}")
            return False
        # 首屏请求可能在 _ensure_origin 内部就发出去了，事件已缓冲
        for _ in range(16):
            try:
                events = page.drain_events()
            except Exception:                       # noqa: BLE001
                events = []
            for ev in events:
                if ev.get("method") != "Network.responseReceived":
                    continue
                url = ((ev.get("params") or {}).get("response") or {}).get("url", "")
                if _API_PATH in url and "sec_user_id=" in url:
                    self._api_url = url
                    self._api_at = time.time()
                    return True
            time.sleep(0.4)
        self._note_fail("主页未发出资料请求（登录态失效或风控拦截）")
        return False

    def _entry_web_rid(self, sec_uid: str) -> str:
        """在播账号主页上的直播入口 ID（webcast 解析流地址要用它）。

        签名模板可能来自另一个账号的主页，所以这里必须**先导航到目标
        主页**再抓链接；拿不到也不影响开播状态判断，只是自动录制会
        退回原有的 app 签名路径。
        """
        crawler = self._crawler
        page = crawler._page if crawler is not None else None
        if page is None:
            return ""
        try:
            crawler._ensure_origin(f"https://www.douyin.com/user/{sec_uid}")
            res = page.call("Runtime.evaluate", {
                "expression": _WEB_RID_JS, "returnByValue": True,
            }, timeout=10)
            raw = (res.get("result") or {}).get("value") or ""
            href = json.loads(raw) if isinstance(raw, str) and raw else ""
            m = re.search(r"live\.douyin\.com/(\d{6,})", str(href))
            return m.group(1) if m else ""
        except Exception:                           # noqa: BLE001
            return ""

    # -------------------------------------------------- 对外查询
    def fetch(self, sec_uid: str) -> HomeLiveSnapshot:
        """查一个账号的开播状态。任何异常都转成 ok=False，不抛给调用方。"""
        sec_uid = (sec_uid or "").strip()
        if not _SEC_RE.match(sec_uid):
            return HomeLiveSnapshot(False, error="不是抖音账号 sec_uid，不能走主页查询")
        try:
            with self._lock:
                return self._fetch_locked(sec_uid)
        except Exception as exc:                    # noqa: BLE001
            with self._lock:
                self._note_fail(str(exc))
            return HomeLiveSnapshot(False, sec_uid=sec_uid,
                                    error=f"免费通道异常：{exc}")

    def _fetch_locked(self, sec_uid: str) -> HomeLiveSnapshot:
        if not self._ensure():
            return HomeLiveSnapshot(
                False, sec_uid=sec_uid,
                error=self._start_error or "免费浏览器通道暂不可用")
        if (not self._api_url
                or time.time() - self._api_at > self.TEMPLATE_TTL):
            if not self._capture_template(sec_uid):
                return HomeLiveSnapshot(
                    False, sec_uid=sec_uid,
                    error=self._start_error or "没拿到主页请求签名")
        target = re.sub(r"(sec_user_id=)[^&]*",
                        lambda m: m.group(1) + quote(sec_uid, safe=""),
                        self._api_url, count=1)
        crawler = self._crawler
        page = crawler._page if crawler is not None else None
        if page is None:
            return HomeLiveSnapshot(False, sec_uid=sec_uid, error="浏览器页面会话不可用")
        res = page.call("Runtime.evaluate", {
            "expression": _FETCH_JS % json.dumps(target),
            "awaitPromise": True,
            "returnByValue": True,
        }, timeout=20)
        raw = ((res.get("result") or {}).get("value")) or "{}"
        try:
            data = json.loads(raw)
        except Exception:                           # noqa: BLE001
            data = {}
        status = data.get("status")
        if status != "ok":
            detail = (data.get("msg") or data.get("head")
                      or "接口未返回用户资料")
            self._note_fail(f"免费通道：{detail}")
            return HomeLiveSnapshot(False, sec_uid=sec_uid,
                                    error=f"免费通道：{detail}")
        user = data.get("user") or {}
        live = int(user.get("live_status") or 0)
        room = str(user.get("room_id_str") or user.get("room_id") or "0")
        self._note_ok()
        # 在播时顺手拿主页直播入口 web_rid：19 位 room_id_str 解析不出流地址
        web_rid = self._entry_web_rid(sec_uid) if live == 1 else ""
        return HomeLiveSnapshot(
            True, live_status=live, room_id=room,
            nickname=str(user.get("nickname") or ""),
            sec_uid=str(user.get("sec_uid") or sec_uid),
            web_rid=web_rid,
        )
