# -*- coding: utf-8 -*-
"""「未连接采集服务」真实原因诊断的离线自测。

背景（2026-10-12 客户投诉）：
    客户已经激活了授权码，弹窗却让他「去设置粘贴授权码并点立即激活」。
    他反复激活都没用 —— 真正原因是**授权码里没带采集凭证**。
    把「没激活」和「激活了但码里没凭证」混成一句话，客户就无路可走。

所以这里把四种状态逐一定住，尤其是「已激活但缺凭证」必须给出换码指引，
且【绝不能】再出现「请去粘贴授权码激活」这种答非所问的话术。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from justoneapi_collector import branding, licensing      # noqa: E402
from gui_qt.pages._base import AppContext, connection_diagnosis  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> bool:
    (PASSED if cond else FAILED).append(name)
    print(f"  {'✔' if cond else '✘'} {name}" + (f"   [{extra}]" if extra else ""))
    return cond


_ORIG_STATUS = licensing.status
_ORIG_LOAD = AppContext.__dict__["_load_settings"]


def _fake_settings(provider: str = "justoneapi", key: str = "") -> None:
    AppContext._load_settings = lambda: {  # type: ignore[assignment]
        "api_provider": provider, "okdata_key": key,
    }


def _fake_status(**over) -> None:
    base = {"state": "none", "label": "", "licensed": False, "days_left": None,
            "customer": "", "expires_at": 0, "machine_id": "AAAA-BBBB-CCCC-DDDD",
            "features": [], "embedded_token": "", "tamper": 0, "trial_start": 0.0,
            "message": "", "daily_quota": 0, "plan": ""}
    base.update(over)
    licensing.status = lambda *a, **k: dict(base)


def main() -> int:
    print("1) 已激活但授权码里没有采集凭证（原 JustOneAPI 通道的真实场景）")
    _fake_settings("justoneapi", "")
    _fake_status(state="licensed", licensed=True, customer="某客户",
                 embedded_token="", machine_id="1234-5678-90AB-CDEF",
                 daily_quota=30, plan="标准版")
    title, detail = connection_diagnosis()
    check("标题点明是「缺采集凭证」而不是「未激活」", "凭证" in title, title)
    check("明确告知已激活成功（不是客户操作错）", "已经激活成功" in detail)
    check("给出换码指引", "重新签发" in detail or "换掉这张" in detail)
    check("带上机器码，客户一步就能提供", "1234-5678-90AB-CDEF" in detail)
    check("带上联系方式", (branding.APP_CONTACT or "无") in detail, branding.APP_CONTACT)
    check("★ 不再出现「请去粘贴授权码激活」这类答非所问",
          "粘贴授权码" not in detail and "立即激活" not in detail)

    print("2) 试用期")
    _fake_status(state="trial", licensed=False, days_left=5.5, daily_quota=10,
                 plan="试用", label="试用中")
    title, detail = connection_diagnosis()
    check("标题说明试用不含采集服务", "试用" in title, title)
    check("说明激活后才会接入采集服务", "激活" in detail)
    check("试用也带机器码方便客户索取授权码", "AAAA-BBBB-CCCC-DDDD" in detail)

    print("3) 授权已过期")
    _fake_status(state="expired", licensed=False, label="30 天试用已结束，请激活后继续使用")
    title, detail = connection_diagnosis()
    check("标题说过期", "过期" in title, title)
    check("带上原状态说明", "试用已结束" in detail)
    check("提示续费后重新激活", "续费" in detail or "重新激活" in detail)

    print("4) 尚未激活")
    _fake_status(state="none", licensed=False)
    title, detail = connection_diagnosis()
    check("标题说尚未激活", "尚未激活" in title, title)
    check("★ 只有这一种情况才引导去「授权与激活」",
          "设置 → 授权与激活" in detail and "立即激活" in detail)
    check("带机器码", "AAAA-BBBB-CCCC-DDDD" in detail)

    print("5) ★ okdata 默认通道（v1.28.14+）：缺 Key 要引导填 Key，不许让客户换授权码")
    _fake_status(state="licensed", licensed=True, customer="某客户",
                 embedded_token="", machine_id="1234-5678-90AB-CDEF",
                 daily_quota=30, plan="标准版")
    _fake_settings("okdata", "")
    title, detail = connection_diagnosis()
    check("标题引导填 okdata Key", "okdata Key" in title, title)
    check("明确授权码本身没问题", "授权码本身没有问题" in detail)
    check("给出注册与设置步骤", "okdata.online" in detail and "数据服务" in detail)
    check("★ 不再误导客户换授权码",
          "重新签发" not in detail and "换掉这张" not in detail)
    check("仍带机器码方便排查", "1234-5678-90AB-CDEF" in detail)

    print("6) ★ okdata 已填 Key 但仍连不上：提示检查 Key/余额/网络")
    _fake_settings("okdata", "okd_test123")
    title, detail = connection_diagnosis()
    check("标题说明是接口连接失败", "连接失败" in title, title)
    check("提示检查 Key 与余额", "余额" in detail and "Key" in detail)

    print("7) 健壮性")
    licensing.status = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        title, detail = connection_diagnosis()
        check("授权模块异常时也不崩，给出兜底文案", bool(title) and bool(detail), title)
    except Exception as e:                                   # noqa: BLE001
        check("授权模块异常时也不崩", False, repr(e))
    licensing.status = _ORIG_STATUS
    AppContext._load_settings = _ORIG_LOAD  # type: ignore[assignment]

    print("\n" + "=" * 60)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        for f in FAILED:
            print("  ✗ " + f)
        return 1
    print("授权诊断自测全部通过 ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
