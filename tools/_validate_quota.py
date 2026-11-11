"""针对「每日调用配额」的无头校验（用临时目录，不碰用户真实 usage.json）。

覆盖：
  1) 日界按 UTC+8
  2) 计数 / 累加 / 跨天重置
  3) 双写防删：删掉一个文件，计数不回血
  4) 限额来自授权码（licensing.daily_quota）
  5) 超限拦截文案与剩余额度
  6) install(client) 打闸：成功才计数、超限不发请求、缓存命中不计数
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from justoneapi_collector import licensing, quota  # noqa: E402
from justoneapi_collector.models import ApiResponse  # noqa: E402

# 把状态文件重定向到临时目录，避免污染本机真实用量
_TMP = tempfile.mkdtemp(prefix="quota_test_")
quota._state_paths = lambda: [  # type: ignore[assignment]
    __import__("pathlib").Path(_TMP) / "usage.json",
    __import__("pathlib").Path(_TMP) / "local" / "usage.json",
]


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise SystemExit(f"校验未通过：{name}")


# 1) 日界
day = quota._today()
check("日界字段格式正确", len(day) == 10 and day[4] == "-", f"today={day}")

# 2) 计数与累加
quota.reset_today()
check("初始计数为 0", quota.today_count() == 0)
quota.consume()
quota.consume(2)
check("累加计数正确", quota.today_count() == 3, f"count={quota.today_count()}")

# 3) 双写防删：删掉第一个文件，第二个仍在
import pathlib  # noqa: E402
p0 = pathlib.Path(_TMP) / "usage.json"
p0.unlink(missing_ok=True)
check("删一个文件计数不回血", quota.today_count() == 3, f"count={quota.today_count()}")

# 4) 限额来自授权码
lim = quota.limit()
check("限额可被读取", isinstance(lim, int), f"limit={lim}")

# 5) 超限判定：把限额临时改小，验证拦截与文案
orig_limit = quota.limit
quota.limit = lambda: 2                                  # type: ignore[assignment]
ok, reason = quota.check(1)
check("额度用完时拦截", (not ok) and "额度已用完" in reason, f"reason={reason[:40]}")
check("剩余额度为 0", quota.remaining() == 0)
quota.limit = lambda: licensing.UNLIMITED                # type: ignore[assignment]
ok2, reason2 = quota.check(1)
check("不限量时放行", ok2 and reason2 == "", f"reason={reason2[:30]}")
check("不限量时 remaining=-1", quota.remaining() == licensing.UNLIMITED)
quota.limit = lambda: 0                                  # type: ignore[assignment]
ok3, reason3 = quota.check(1)
check("未激活时拦截", (not ok3) and "授权" in reason3, f"reason={reason3[:30]}")
quota.limit = orig_limit                                 # 还原

# 6) install(client) 打闸
class _FakeClient:
    def __init__(self):
        self.calls = 0
        self.cached = False

    def request(self, platform, endpoint, params=None):
        self.calls += 1
        return ApiResponse(
            success=True, code=0, message="", data={"ok": 1},
            raw_json={"code": 0, "data": {"ok": 1},
                      **({"_cached": True} if self.cached else {})},
        )


quota.reset_today()
quota.limit = lambda: 5                                  # type: ignore[assignment]
fake = _FakeClient()
quota.install(fake)
check("install 后打了标记", getattr(fake, "_quota_installed", False))

r1 = fake.request("douyin", "video-detail")
check("放行时正常返回", r1.success and fake.calls == 1)
check("成功后计数 +1", quota.today_count() == 1, f"count={quota.today_count()}")

fake.cached = True
fake.request("douyin", "video-detail")
check("缓存命中不计数", quota.today_count() == 1, f"count={quota.today_count()}")
fake.cached = False

# 用光额度
while quota.today_count() < 5:
    fake.request("douyin", "video-detail")
calls_before = fake.calls
r2 = fake.request("douyin", "video-detail")
check("超限返回失败响应", (not r2.success) and r2.code == quota.QUOTA_EXCEEDED_CODE,
      f"code={r2.code}")
check("超限不再发起请求", fake.calls == calls_before, f"calls={fake.calls}")
check("超限提示可读", "额度已用完" in (r2.message or ""), f"msg={r2.message[:40]}")

# 幂等：重复 install 不会重复包装
quota.install(fake)
quota.limit = orig_limit
check("install 幂等", getattr(fake, "_quota_installed", False))

# summary 结构
s = quota.summary()
check("summary 字段齐全",
      {"limit", "used", "remaining", "unlimited", "date"} <= set(s.keys()),
      f"keys={sorted(s.keys())}")

quota.reset_today()
check("reset 后归零", quota.today_count() == 0)

print("\nALL_QUOTA_CHECKS_PASS")
sys.exit(0)
