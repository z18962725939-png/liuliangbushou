"""webcast 房间状态 / 流地址接口客户端。

实测（2026-11-14）：
  * 路径必须带尾斜杠 /webcast/room/info/，不带会 301 再跳。
  * 参数 aid=1988&room_id=<roomId>；status_code=0 表示接口成功，
    data.status：2=在播、4=已结束（和现有抖音 webcast 完全同构）。
  * 这个域名不设网页那层 JS 挑战，工程自带的 httpx 就能调，
    不需要额外装指纹模拟库；但**仍然必须走海外出口**。
  * 流地址带 sign + expire，本次实测有效期 14 天；每次开播都会变。
"""

from __future__ import annotations

import time

import httpx

from .config import ProtoConfig
from .core import RoomSnapshot, parse_room

WEBCAST_URL = "https://webcast.tiktok.com/webcast/room/info/"
AID = "1988"


class WebcastClient:
    """一次实例化可复用连接池；轮询期间不要反复 new。"""

    def __init__(self, cfg: ProtoConfig, cookie_header: str = ""):
        self.cfg = cfg
        self.cookie_header = cookie_header or ""
        self.last_error = ""

    def _headers(self, room_id: str) -> dict:
        h = {
            "User-Agent": self.cfg.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"https://www.tiktok.com/@/live?room_id={room_id}",
        }
        if self.cookie_header:
            h["Cookie"] = self.cookie_header
        return h

    def get_payload(self, room_id: str) -> dict:
        """只负责发请求拿 JSON；网络/代理问题在这里转成可读中文错误。"""
        url = f"{WEBCAST_URL}?aid={AID}&room_id={room_id}"
        kwargs = dict(headers=self._headers(room_id), timeout=20.0,
                      follow_redirects=True, trust_env=False)
        if self.cfg.proxy:
            kwargs["proxy"] = self.cfg.proxy
        last: Exception | None = None
        for attempt in range(2):                       # 抖一次自动重试一次
            try:
                with httpx.Client(**kwargs) as c:
                    r = c.get(url)
                if r.status_code >= 500:
                    raise RuntimeError(f"服务端 HTTP {r.status_code}")
                self.last_error = ""
                return r.json()
            except Exception as exc:                    # noqa: BLE001
                last = exc
                if attempt == 0:
                    time.sleep(1.0)
        msg = f"{type(last).__name__}: {last}" if last else "未知错误"
        self.last_error = msg
        proxy_hint = "" if self.cfg.proxy else "（当前没配代理，国内直连 TikTok 会被重置）"
        raise ConnectionError(f"房间状态接口请求失败{proxy_hint}：{msg[:180]}")

    def fetch(self, room_id: str) -> RoomSnapshot:
        """永远返回 RoomSnapshot，不把异常抛给上层状态机。"""
        if not room_id or not str(room_id).isdigit():
            snap = RoomSnapshot()
            snap.error = "room_id 为空或不是数字，需要先过浏览器取直播间页"
            return snap
        try:
            return parse_room(self.get_payload(str(room_id)))
        except ConnectionError as exc:
            snap = RoomSnapshot()
            snap.error = str(exc)
            return snap
        except Exception as exc:                        # noqa: BLE001
            snap = RoomSnapshot()
            snap.error = f"接口解析异常：{type(exc).__name__}: {str(exc)[:160]}"
            return snap
