from PySide6.QtWidgets import QApplication, QScrollArea               # noqa: E402

# ⚠️ 不要在这里 addApplicationFont 加载 msyh.ttc（2026-10-12 定位）：
#    Qt 6.11 + offscreen 平台下加载 .ttc 字体会**直接段错误**（进程 139 退出，
#    没有任何 traceback，try/except 也挡不住），表现为"探针脚本跑不出任何输出"。
#    系统已安装微软雅黑，QFont("Microsoft YaHei") 能正常取到，无需手动注册。

from gui_qt.pages._base import AppContext                             # noqa: E402
from gui_qt.pages.batch_page import BatchPage                         # noqa: E402
from gui_qt.pages.collect_page import CollectPage                     # noqa: E402
from gui_qt.pages.comment_page import CommentPage                     # noqa: E402
from gui_qt.pages.competitor_page import CompetitorPage               # noqa: E402
from gui_qt.pages.geo_page import GeoPage                             # noqa: E402
from gui_qt.pages.keyword_page import KeywordPage                     # noqa: E402
from gui_qt.pages.live_page import LivePage                           # noqa: E402
from gui_qt.pages.report_page import ReportPage                       # noqa: E402
from gui_qt.pages.script_page import ScriptPage                       # noqa: E402
from gui_qt.pages.settings_page import SettingsPage                   # noqa: E402
from gui_qt.pages.topic_page import TopicPage                         # noqa: E402
from gui_qt.pages.transcribe_page import TranscribePage               # noqa: E402
from gui_qt.pages.trend_page import TrendPage                         # noqa: E402
from gui_qt.pages.video_download_page import VideoDownloadPage        # noqa: E402
from gui_qt.pages.watch_page import WatchPage                         # noqa: E402
from gui_qt.theme import apply_theme                                  # noqa: E402
from gui_qt.pages.drama_page import DramaPage                          # noqa: E402

PAGES = [
    ("数据采集", CollectPage), ("批量采集", BatchPage),
    ("关键词抓取", KeywordPage), ("视频下载", VideoDownloadPage),
    ("直播监控", LivePage), ("选题挖掘", TopicPage),
    ("竞品体检", CompetitorPage), ("评论区截流", CommentPage),
    ("GEO 监测", GeoPage), ("复盘监控", WatchPage),
    ("报告", ReportPage), ("文案仿写", ScriptPage),
    ("音视频转文本", TranscribePage), ("数据趋势", TrendPage),
    ("设置", SettingsPage), ("短剧成片", DramaPage),
]

# 常见窗口可用高度：768 笔记本约 690、默认 760、1080 屏约 980
WINDOW_H = [690, 760, 980]


def main() -> int:
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 9))
