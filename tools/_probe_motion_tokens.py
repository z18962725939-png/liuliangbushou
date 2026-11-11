"""动效令牌自检：曲线形状、档位门控、时长换算、自定义曲线在事件循环里是否安全。"""
import os
import time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QVariantAnimation                  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel            # noqa: E402

from gui_qt.widgets import motion                             # noqa: E402

app = QApplication([])                                        # noqa: F841

print("level:", motion.motion_level())
print("anim on:", motion.animations_enabled(), "travel:", motion.travel_enabled())

print("\n--- 曲线采样（0 → 1，每 0.1）---")
for kind in ("out", "in_out", "soft"):
    row = [round(motion.ease(kind, i / 10), 3) for i in range(11)]
    print(f"{kind:7s}", row)

print("\n--- 曲线强度对照（内置 OutCubic 起步太慢，这就是换掉它的理由）---")
print("  out@0.25 =", round(motion.ease("out", 0.25), 3),
      "| 内置 OutCubic@0.25 ≈ 0.578")

print("\n--- 时长换算（reduced 档位模拟）---")
os.environ["TRAFFIC_NO_ANIM"] = "1"
print("  NO_ANIM 档位:", motion.motion_level(),
      "->", motion.scaled(240, "opacity"), motion.scaled(240, "travel"))
del os.environ["TRAFFIC_NO_ANIM"]


def pump(seconds: float) -> None:
    """不用 app.exec()：离屏环境里 exec + quit 容易在析构期炸，手动泵事件。"""
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


print("\n--- Tween（线性进度 + 自施曲线）+ 事件循环 ---")
holder = QLabel()
seen = []
tw = motion.Tween(holder, duration=200, kind="out",
                  on_change=lambda v: seen.append(round(float(v), 1)))
tw.run(0.0, 100.0)
pump(0.5)
print("  采样:", seen[:6], "...", seen[-3:] if len(seen) > 6 else "")
print("  单调递增:", all(b >= a for a, b in zip(seen, seen[1:])))
print("  前段更快（ease-out 特征）:",
      (seen[1] if len(seen) > 1 else 0) > 100 * 0.25 * 0.6)

print("\n--- SmoothValue 可打断（中途改目标不会跳回起点）---")
sv = motion.SmoothValue(holder, duration=200)
sv.to(1.0)
pump(0.06)
mid = sv.value
sv.to(0.0)
pump(0.02)
print(f"  中途值 {mid:.2f} -> 立刻反向后 {sv.value:.2f}（应 ≤ 中途值，不是 0 也不是 1）")
pump(0.4)
print("  最终:", round(sv.value, 2))

print("\n--- count_up 端到端 ---")
lab = QLabel("0")
print("  动画中:", motion.count_up(lab, "128", duration=200))
pump(0.6)
print("  最终文本:", lab.text())
print("OK")
