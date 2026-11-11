"""针对「作品 / 喜欢 / 收藏 列表抓取」的无头校验（不启动真实浏览器、不下载文件）。

覆盖：
  1) a_bogus 签名可用
  2) sec_uid / msToken 正则提取
  3) HTTP 直连兜底路径：无登录态时返回可读错误而非崩溃
  4) 浏览器会话模块的常量正确性（Tab 参数、接口匹配、mix 排除）
  5) 浏览器会话未启动时 capture_list 安全报错
  6) list_user_list 在浏览器不可用时的降级提示
  7) 视频下载页构建（列表类型下拉 / 浏览器会话勾选 / 下载内容下拉）
  8) 拉取任务函数在纯 HTTP 模式下的错误返回
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from justoneapi_collector.douyin_live_stream import _DEFAULT_UA, _ab_sign  # noqa: E402
from justoneapi_collector.douyin_fav_crawler import DouyinFavCrawler  # noqa: E402
from justoneapi_collector import douyin_cdp_crawler as cdp  # noqa: E402
from justoneapi_collector.video_downloader import VideoDownloader  # noqa: E402
from gui_qt.pages.video_download_page import (  # noqa: E402
    VideoDownloadPage, _MockClient, _fetch_list_task,
)
from gui_qt.pages._base import AppContext  # noqa: E402


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise SystemExit(f"校验未通过：{name}")


# 1) a_bogus 签名
sig = _ab_sign("aid=6383&sec_user_id=MS4wLjABxxx", _DEFAULT_UA)
check("a_bogus 签名可用", isinstance(sig, str) and len(sig) > 8, f"len={len(sig)}")

# 2) 正则提取
crawler = DouyinFavCrawler(cookie="")
sec = crawler.extract_sec_uid("https://www.douyin.com/user/MS4wLjABABCDEFGH")
check("sec_uid 正则提取", sec == "MS4wLjABABCDEFGH", f"got={sec}")
check("msToken 提取（空）", crawler._extract_ms_token("a=1") == "")
check("msToken 提取（有）", crawler._extract_ms_token("msToken=abc123; x=1") == "abc123")

# 3) HTTP 直连兜底：无 Cookie 时返回可读错误而非崩溃
awemes, _nxt, has_more, err = crawler.fetch_favourite("MS4wLjABABCDEFGH")
check("HTTP 直连返回可读错误", isinstance(err, str) and len(err) > 0, f"err={err[:50]}")
check("HTTP 直连返回空列表", awemes == [] and has_more is False)

# 4) 浏览器会话模块常量（实测得来的真实值，防止被误改回去）
check("收藏 Tab 参数正确",
      "showTab=favorite_collection" in cdp._TAB_OF_KIND["collect"]
      and "enter_method=personal_panel" in cdp._TAB_OF_KIND["collect"],
      cdp._TAB_OF_KIND["collect"])
check("喜欢 Tab 参数正确", cdp._TAB_OF_KIND["favorite"] == "?showTab=like")
check("作品无 Tab 参数", cdp._TAB_OF_KIND["post"] == "")
check("收藏接口含 listcollection",
      any("listcollection" in p for p in cdp._LIST_PATTERNS["collect"]))
check("喜欢接口含 aweme/favorite",
      any("/aweme/v1/web/aweme/favorite/" in p for p in cdp._LIST_PATTERNS["favorite"]))
check("排除 mix 合集接口", any("mix" in p for p in cdp._EXCLUDE_PATTERNS))

# 5) 浏览器会话未启动 -> 安全报错（不真的启动浏览器）
cr = cdp.DouyinCDPCrawler(os.path.join(ROOT, "_nonexistent_profile"))
got, msg = cr.capture_list("favorite", "MS4wLjABABCDEFGH")
check("浏览器未启动时安全报错", got == [] and "浏览器会话未启动" in msg, f"msg={msg}")
check("未知类型安全报错", cr.capture_list("nope", "x")[1].startswith("未知的列表类型"))

# 6) list_user_list 在浏览器不可用时的降级（打桩 start，避免真的开浏览器）
dl = VideoDownloader(_MockClient(), save_dir=None)
orig_start = cdp.DouyinCDPCrawler.start
cdp.DouyinCDPCrawler.start = lambda self: (False, "模拟：本机无浏览器")
try:
    vids, err2 = dl.list_user_list("favorite", "MS4wLjABABCDEFGH", use_browser=True)
    check("浏览器不可用时的降级提示", vids == [] and "模拟" in err2, f"err={err2}")
finally:
    cdp.DouyinCDPCrawler.start = orig_start
vids3, err3 = dl.list_user_list("post", "MS4wLjABABCDEFGH", use_browser=False)
check("纯 HTTP 模式不崩溃", isinstance(vids3, list) and isinstance(err3, str),
      f"n={len(vids3)} err={err3[:40]}")

# 7) 页面构建（演示模式）
ctx = AppContext()
page = VideoDownloadPage(ctx)
check("下载页含『列表类型』下拉", hasattr(page, "list_type_combo"))
check("列表类型含 作品/喜欢/收藏",
      [page.list_type_combo.itemData(i) for i in range(page.list_type_combo.count())]
      == ["post", "favorite", "collect"])
check("下载页含『浏览器会话抓取』勾选", hasattr(page, "use_browser_chk"))
check("浏览器会话默认勾选", page.use_browser_chk.isChecked())
check("下载页含『下载内容』下拉", hasattr(page, "media_combo"))

# 8) 拉取任务：纯 HTTP 模式、无登录 -> 返回错误元组
class _W:
    def emit_progress(self, p, m):
        pass


res = _fetch_list_task(_W(), dl, "favorite", None, True, use_browser=False)
check("拉取任务(喜欢/本人/HTTP)返回错误",
      isinstance(res, tuple) and res[0] == [] and bool(res[1]), f"err={str(res[1])[:50]}")

print("\nALL_FAV_CRAWLER_CHECKS_PASS")
sys.exit(0)
