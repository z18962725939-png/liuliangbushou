"""探测 PySide6 的自定义缓动与字体特性接口是否可用（写新动效令牌前先确认）。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEasingCurve                      # noqa: E402
from PySide6.QtGui import QFont                              # noqa: E402
from PySide6.QtWidgets import QApplication                   # noqa: E402

app = QApplication([])                                        # noqa: F841

print("--- QEasingCurve ---")
print("has setCustomType:", hasattr(QEasingCurve, "setCustomType"))
try:
    c = QEasingCurve()
    c.setCustomType(lambda p: p * p)
    print("setCustomType ok ->", c.valueForProgress(0.5))
except Exception as exc:                                      # noqa: BLE001
    print("setCustomType fail:", type(exc).__name__, exc)

print("--- QFont features ---")
f = QFont()
print("has setFeature:", hasattr(f, "setFeature"))
try:
    f.setFeature(0x746E756D, 1)      # 'tnum' tabular figures
    print("setFeature ok")
except Exception as exc:                                      # noqa: BLE001
    print("setFeature fail:", type(exc).__name__, exc)

print("--- reduced motion (Windows SPI) ---")
try:
    import ctypes
    v = ctypes.c_bool()
    ok = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(v), 0)
    print("SPI_GETCLIENTAREAANIMATION ->", ok, bool(v.value))
except Exception as exc:                                      # noqa: BLE001
    print("SPI fail:", type(exc).__name__, exc)
