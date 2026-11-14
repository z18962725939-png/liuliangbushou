"""TikTok 直播监控 · 独立原型包。

边界（重要）：
  * 只放在 samples/ 下，**不接主程序、不改 gui_qt/、不挂 release_all 发版链**。
  * 复用了主工程 browser_login.py 里现成的 CDP 会话与浏览器探测（只读 import），
    以及 assets/ffmpeg/ 自带的 ffmpeg；除此之外不触碰任何线上代码。
  * 目的是验证「粘贴 @handle → 自动监控 → 开播自动录」这条链路能否跑顺，
    验证通过后再由用户决定是否并入正式版本。

运行：
  python samples/tiktok_live_proto/cli.py --handle ofdwex9 --once
  python samples/tiktok_live_proto/app.py          # 图形界面
  python samples/tiktok_live_proto/_validate.py   # 离线自测
"""

from __future__ import annotations

import sys
from pathlib import Path

# 直接以脚本方式运行某个文件时，把工程根加进 sys.path，
# 这样既能 import 主工程的 browser_login，也能 import 本包的兄弟模块。
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

__version__ = "0.1.0-proto"
