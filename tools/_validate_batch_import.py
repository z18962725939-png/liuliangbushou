# -*- coding: utf-8 -*-
"""自测：多关键词抓取 + Excel 批量导入下载（v1.4.0）。

分两部分：
  A. 纯逻辑（离线，不消耗额度）：链接提取、xlsx 读写、去重、停止、
     批量解析任务、页面里关键词拆分与粘贴链接解析。
  B. 真实端到端（联网，消耗少量额度）：真实搜索 → 真实导出 xlsx →
     真实读回 → 真实解析成视频。
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication                        # noqa: E402
app = QApplication.instance() or QApplication([])

from justoneapi_collector.video_link_import import (              # noqa: E402
    clean_url, find_links_in_text, read_links_from_file, read_links_from_rows,
    resolve_links)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))


U1 = "https://www.iesdouyin.com/share/video/7166170264458382606/?a=1"
U2 = "https://www.douyin.com/video/7667428011646164032"
U3 = "https://v.douyin.com/AbCdEfG/"

print("\n=== A. 链接提取（纯逻辑）===")
# 1) 带表头（关键词抓取导出的格式）→ 只取「链接」列
header_rows = [
    ["关键词", "平台", "标题", "作者", "粉丝数", "点赞", "评论", "收藏", "分享",
     "播放", "发布时间", "时长", "链接"],
    ["牙刷", "抖音", "标题一", "作者A", 100, 5, 1, 1, 1, 1000, "2026-01-01", "0:57", U1],
    ["牙刷", "抖音", "标题二", "作者B", 200, 6, 1, 1, 1, 2000, "2026-01-02", "1:00", U2],
    ["牙刷", "抖音", "标题三", "作者C", 300, 7, 1, 1, 1, 3000, "2026-01-03", "1:01", U3],
]
got = read_links_from_rows(header_rows)
check("带表头：只取「链接」列", got == [U1, U2, U3], f"{len(got)} 条")

# 2) 表头里有「封面」这种也含链接的列，不能误取
tricky = [
    ["标题", "封面图", "链接"],
    ["A", "https://cdn.example.com/cover1.jpg", U1],
    ["B", "https://cdn.example.com/cover2.jpg", U2],
]
got = read_links_from_rows(tricky)
check("带表头：不会把封面图地址当成视频链接", got == [U1, U2], f"{got}")

# 3) 没有表头 → 全表扫描
plain = [["随便一列"], [U1], [f"分享文案 {U3} 复制打开抖音"], ["没有链接"]]
got = read_links_from_rows(plain)
check("无表头：全表扫描并保序", got == [U1, U3], f"{got}")

# 4) 去重
dup = [["链接"], [U1], [U1], [U2], [U1]]
got = read_links_from_rows(dup)
check("重复链接去重", got == [U1, U2], f"{got}")

# 5) 尾部中文标点清洗
check("清洗中文标点", clean_url("https://a.com/v/1，。") == "https://a.com/v/1")
check("从文案里抠链接（带 emoji）", find_links_in_text(f"😀 看看这个 {U3} 复制此链接") == [U3])

print("\n=== A. 真实 xlsx 往返（模拟关键词抓取导出）===")
tmp = Path(tempfile.mkdtemp(prefix="tc_imp_"))
xlsx = tmp / "关键词视频.xlsx"
import openpyxl                                                    # noqa: E402
wb = openpyxl.Workbook(); ws = wb.active
KEYWORD_COLUMNS = ["关键词", "平台", "标题", "作者", "粉丝数", "点赞", "评论",
                   "收藏", "分享", "播放", "发布时间", "时长", "链接"]
ws.append(KEYWORD_COLUMNS)
ws.append(["牙刷", "抖音", "标题一", "作者A", 100, 5, 1, 1, 1, 1000, "t", "0:57", U1])
ws.append(["牙刷", "抖音", "标题二", "作者B", 200, 6, 1, 1, 1, 2000, "t", "1:00", U2])
ws.append(["牙刷", "抖音", "标题三", "作者C", 300, 7, 1, 1, 1, 3000, "t", "1:01", U3])
wb.save(xlsx)
links, err = read_links_from_file(xlsx)
check("读取 .xlsx 成功", err == "" and links == [U1, U2, U3], f"err={err!r} {links}")

csv_path = tmp / "links.csv"
csv_path.write_text(f"链接\n{U1}\n{U2}\n", encoding="utf-8-sig")
links, err = read_links_from_file(csv_path)
check("读取 .csv 成功", err == "" and links == [U1, U2], f"{links}")

links, err = read_links_from_file(tmp / "不存在.xlsx")
check("不存在的文件给出明确错误", links == [] and "不存在" in err)

(tmp / "old.xls").write_bytes(b"\xd0\xcf\x11\xe0")
links, err = read_links_from_file(tmp / "old.xls")
check("老式 .xls 给出引导性提示", links == [] and "另存为" in err, f"{err!r}")

print("\n=== A. 批量解析（假下载器）===")


class _FakeInfo:
    def __init__(self, vid, desc="标题"):
        self.video_id = vid
        self.desc = desc
        self.duration = 12345
        self.platform = "douyin"


class _FakeDL:
    def __init__(self, table):
        self.table = table
        self.calls = []

    def resolve_share_link_with_reason(self, url):
        self.calls.append(url)
        r = self.table.get(url)
        if r is None:
            return None, "签名失效"
        if isinstance(r, Exception):
            raise r
        return r, ""


dl = _FakeDL({U1: _FakeInfo("111"), U2: _FakeInfo("222"), U3: _FakeInfo("333")})
videos, fails = resolve_links(dl, [U1, U2, U3, "https://x.com/none"])
check("成功 3 条 / 失败 1 条", len(videos) == 3 and len(fails) == 1,
      f"{len(videos)} / {len(fails)}")
check("失败原因被带回", fails and fails[0][1] == "签名失效", f"{fails}")

# 同一作品在不同关键词下重复出现（ID 相同）→ 只保留一条
dl_dup = _FakeDL({U1: _FakeInfo("111"), U2: _FakeInfo("222"), U3: _FakeInfo("111")})
videos, _ = resolve_links(dl_dup, [U1, U2, U3])
check("同一作品 ID 只保留一条（跨关键词去重）",
      [v.video_id for v in videos] == ["111", "222"], f"{[v.video_id for v in videos]}")

# 已在下载列表里的作品（skip_ids）不再重复加入
dl_skip = _FakeDL({U1: _FakeInfo("111"), U2: _FakeInfo("222")})
videos, _ = resolve_links(dl_skip, [U1, U2], skip_ids={"111"})
check("已在列表里的作品不会重复加入", [v.video_id for v in videos] == ["222"],
      f"{[v.video_id for v in videos]}")

# 异常不能让整批中断
dl2 = _FakeDL({U1: _FakeInfo("1"), U2: RuntimeError("boom"), U3: _FakeInfo("3")})
videos, fails = resolve_links(dl2, [U1, U2, U3])
check("单条抛异常不中断整批", len(videos) == 2 and len(fails) == 1, f"{len(videos)}/{len(fails)}")

# 停止
stop_after = {"n": 0}


def _should_stop():
    stop_after["n"] += 1
    return stop_after["n"] > 3


dl3 = _FakeDL({U1: _FakeInfo("1"), U2: _FakeInfo("2"), U3: _FakeInfo("3")})
videos, fails = resolve_links(dl3, [U1, U2, U3] * 4, should_stop=_should_stop)
check("支持中途停止", len(dl3.calls) <= 4, f"实际解析 {len(dl3.calls)} 次")

print("\n=== A. 页面层（关键词拆分 / 粘贴链接）===")
from gui_qt.pages._base import AppContext                          # noqa: E402
from gui_qt.pages.keyword_page import KeywordPage                   # noqa: E402
from gui_qt.pages.video_download_page import VideoDownloadPage       # noqa: E402

ctx = AppContext()
ctx.client = None          # 强制演示模式，页面不会联网
kp = KeywordPage(ctx)
kp.keywords.setPlainText("减脂餐\n减肥食谱\n瘦肚子")
check("多行关键词拆分", kp._split_keywords() == ["减脂餐", "减肥食谱", "瘦肚子"],
      f"{kp._split_keywords()}")
kp.keywords.setPlainText("牙刷, 牙膏，漱口水")
check("逗号（中英文）也能拆", kp._split_keywords() == ["牙刷", "牙膏", "漱口水"])
kp.keywords.setPlainText("牙刷\n牙刷\n牙膏")
check("重复关键词去重", kp._split_keywords() == ["牙刷", "牙膏"])
kp._refresh_quota_hint()
check("关键词计数提示已刷新", "已识别 2 个关键词" in kp.kw_count.text(),
      f"{kp.kw_count.text()!r}")

vp = VideoDownloadPage(ctx)
check("视频下载页有导入按钮", hasattr(vp, "import_btn") and vp.import_btn.isEnabled())
vp.import_links.setPlainText(f"{U1}\n{find_links_in_text('看看这个 ' + U3)[0]}\n{U1}\n\n无链接行")
check("粘贴框解析：去重 + 跳过无链接行",
      vp._pasted_links() == [U1, U3], f"{vp._pasted_links()}")

# 批量解析后台任务（Worker 同步执行 run 的逻辑，用假下载器）
from gui_qt.pages.video_download_page import _batch_resolve_task    # noqa: E402


class _W:
    def __init__(self):
        self.logs, self.progs = [], []

    def emit_log(self, m):
        self.logs.append(m)

    def emit_progress(self, p, m=""):
        self.progs.append((p, m))


va = _W()
res = _batch_resolve_task(va, _FakeDL({U1: _FakeInfo("1"), U2: _FakeInfo("2")}),
                          [U1, U2, "https://x.com/no"], set(), lambda: False)
videos, fails, total = res
check("后台任务返回三元组", len(videos) == 2 and len(fails) == 1 and total == 3, f"{res[2]}")
check("后台任务上报进度与日志", len(va.progs) >= 3 and len(va.logs) >= 3,
      f"prog={len(va.progs)} log={len(va.logs)}")

print("\n=== B. 真实端到端（联网）===")
try:
    from justoneapi_collector.client import JustOneAPICollector      # noqa: E402
    from justoneapi_collector import keyword_search as ks            # noqa: E402
    from justoneapi_collector.video_downloader import VideoDownloader  # noqa: E402

    cl = JustOneAPICollector(token="kPa7Fxdepu2GAuxU",
                             base_url="https://api.justoneapi.com",
                             cache_enabled=False)
    try:
        kw_list = sys.argv[1:] or ["牙刷"]
        results = ks.search_keywords(cl, "douyin", kw_list,
                                     depth_label="快速（2~3 次请求）")
        rows = ks.rows_from_results(results)
        check(f"真实抓取 {len(kw_list)} 个关键词有结果", len(rows) > 0, f"{len(rows)} 条")

        real_xlsx = tmp / "真实导出.xlsx"
        wb2 = openpyxl.Workbook(); ws2 = wb2.active
        ws2.append(list(KEYWORD_COLUMNS))
        for r in rows:
            ws2.append(r)
        wb2.save(real_xlsx)

        links, err = read_links_from_file(real_xlsx)
        check("真实导出的 xlsx 能被读回", err == "" and len(links) == len(rows),
              f"导出 {len(rows)} 行 → 读出 {len(links)} 条链接")

        dl = VideoDownloader(cl, save_dir=None)
        videos, fails = resolve_links(dl, links[:3])
        check("真实链接能被解析成视频", len(videos) == 3,
              f"成功 {len(videos)} / 失败 {len(fails)}")
        for v in videos:
            print(f"       · {v.desc[:26]!r} 时长={v.duration}ms 无水印={'有' if v.no_watermark_url else '无'}")
    finally:
        cl.close()
except Exception as e:                                             # noqa: BLE001
    FAIL.append("真实端到端")
    print(f"  [FAIL] 真实端到端异常：{type(e).__name__}: {e}")

print(f"\n===== 结果：{len(PASS)} 项通过，{len(FAIL)} 项失败 =====")
if FAIL:
    print("失败项：" + "、".join(FAIL))
    sys.exit(1)
