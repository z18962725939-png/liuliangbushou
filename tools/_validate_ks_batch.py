# -*- coding: utf-8 -*-
"""快手批量解析链路门禁（2026-11-25）。

钉住本轮"两级降级 + 四类 schema 归一化 + 双向分流"的关键不变量，全部离线：
  ① 免费兜底文案禁用词为 0（额度 / 余额 / 暂不支持 / 需激活 / 激活授权码 / 填写 API Token）；
  ② 首屏边界文案存在且 SSR 兜底分支必现；
  ③ 归一化四类 schema 关键字段覆盖（内置四类样本离线断言）；
  ④ Worker 铁律（每页 status/progress 只 1 次、connect_guarded + lambda、start()、无 on_error=self._status.set）；
  ⑤ 三链路边界（media_parser 不含 okdata、ENDPOINT_MAP 未新增快手 SSR 映射、SSR 不调付费接口）；
  ⑥ 不新增页面 / 功能位（PAGES / FEATURES 本轮未新增快手批量位）。

风格参照 tools/_validate_titlebar.py：纯标准库 + check() + PASS/FAIL + 按 FAIL 定退出码。
跑法：C:/Python310/python.exe tools/_validate_ks_batch.py
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK ] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


DL_SRC = read("justoneapi_collector/video_downloader.py")
PAGE_SRC = read("gui_qt/pages/video_download_page.py")

# 免费通道禁用词（P0-2 / PRD §6.1）——只在"免费兜底相关文案"里查，
# 不误伤设置页 okdata 充值提示（那是付费通道自己的、且不进免费兜底分支）。
KS_NOTE_BLOCK = "KS_NOTE_SSR_BOUNDARY"
KS_FAIL_BLOCK = "KS_FAIL_TEXT"
BANNED = ("额度", "余额", "暂不支持", "需激活", "激活授权码", "填写 API Token")

print("\n== 1. 免费兜底文案禁用词 ==")
for const in (KS_NOTE_BLOCK, KS_FAIL_BLOCK):
    # 截取常量定义块（到下一个空行分隔的顶层定义为止，粗略取 900 字符足够覆盖）
    start = DL_SRC.find(const)
    chunk = DL_SRC[start:start + 900] if start >= 0 else ""
    for word in BANNED:
        check(f"{const} 不含「{word}」", word not in chunk)

print("\n== 2. 首屏边界文案存在且兜底分支必现 ==")
check("KS_NOTE_SSR_BOUNDARY 定义含「免费通道只能拿到首屏」",
      "免费通道只能拿到首屏" in DL_SRC)
# ★ 2026-11-25 QA 加固（第 2 轮 + 最终收尾）：此断言锚定真实上屏路径，不能在
#   模块全局盲搜 `if ssr_used:`，也不能用 ast.walk 钻进函数体内嵌套 helper——
#   否则把写入搬去从未调用的 helper（C/C2 重构事故）仍会假绿。锚点：
#   `list_kuaishou_blogger_videos` 自身层级须同时存在
#   ① 真 `if ssr_used:` 分支写入 KS_NOTE_SSR_BOUNDARY（注释/死分支/条件变形不算；
#      遍历遇嵌套 FunctionDef/AsyncFunctionDef/Lambda/ClassDef 停止深入，
#      仅"被该分支真调用的 helper"内写入才算，未调用 helper 不计入）
#   ② 真实上屏动作 `self._last_list_note = ...`（否则写入进不了 UI）。
#   D 场景（分支前注入 ssr_used=False）属 AST 固有边界，不做数据流分析。
def _ssr_boundary_written_in_live_branch(src: str) -> bool:
    """锚定 list_kuaishou_blogger_videos 自身层级：真分支写入 + 上屏 sink。

    QA 第 2 轮加固：原实现用 ast.walk(target) 递归遍历，会钻进函数体内
    嵌套定义的 helper，导致"从未调用的 helper 里的写入"也假绿。现改为显式
    遍历 target 自身层级，遇 FunctionDef/AsyncFunctionDef/Lambda/ClassDef 即
    停止深入其 body；只认目标函数自身层级 `if ssr_used:` 分支的写入，或被该
    分支真调用的、同函数内定义 helper 的写入（未调用 helper 一律不计入）。
    D 场景（分支前 ssr_used=False）属 AST 静态分析固有边界，不做数据流分析。
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    const = "KS_NOTE_SSR_BOUNDARY"

    # ① 只认目标函数——写入搬去模块级/顶层函数就找不到锚点
    target = None
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "list_kuaishou_blogger_videos"):
            target = node
            break
    if target is None:
        return False

    def walk_own(node, include_defs: bool = False):
        """遍历 node 自身层级子节点；嵌套函数/类/lambda 停止深入其 body。

        include_defs=True 时仍 yield 定义节点本身，用于收集"被分支调用的
        helper"；无论哪种模式都绝不深入嵌套定义的 body。
        """
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda, ast.ClassDef)):
                if include_defs:
                    yield child
                continue
            yield child
            yield from walk_own(child, include_defs)

    def refs_const(node) -> bool:
        """节点自身层级内是否引用常量（跳过嵌套函数体）。"""
        if isinstance(node, ast.Name) and node.id == const:
            return True
        return any(refs_const(n) for n in walk_own(node))

    def writes_const(node) -> bool:
        """节点自身层级内是否有对常量的 append/赋值（跳过更深嵌套函数）。"""
        for n in walk_own(node):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in ("append", "extend", "insert", "add", "write")
                    and any(refs_const(a) for a in n.args)):
                return True
            if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                value = getattr(n, "value", None)
                if value is not None and refs_const(value):
                    return True
        return False

    # 目标函数自身层级定义的 helper（含分支内 def）：名字 → 节点
    helpers = {}
    for n in walk_own(target, include_defs=True):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            helpers[n.name] = n

    written = False
    for node in walk_own(target):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        # 只认条件恰为 `ssr_used`；`and False`/`== True`/嵌套等变形不算
        if not (isinstance(test, ast.Name) and test.id == "ssr_used"):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                func = stmt.value.func
                if (isinstance(func, ast.Attribute)
                        and func.attr in ("append", "extend", "insert", "add", "write")
                        and any(refs_const(a) for a in stmt.value.args)):
                    written = True
                # 只有被本分支真调用的 helper 才算；未调用 helper 一律不计入
                if (isinstance(func, ast.Name) and func.id in helpers
                        and writes_const(helpers[func.id])):
                    written = True
            if isinstance(stmt, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                value = getattr(stmt, "value", None)
                if value is not None and refs_const(value):
                    written = True
    if not written:
        return False

    # ② 同一函数体自身层级必须有真实上屏 sink：self._last_list_note = ...
    has_sink = False
    for node in walk_own(target):
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if (isinstance(t, ast.Attribute) and t.attr == "_last_list_note"
                    and isinstance(t.value, ast.Name) and t.value.id == "self"):
                has_sink = True
    return has_sink

check("SSR 生效分支锚定 list_kuaishou_blogger_videos 上屏路径（写入+_last_list_note 双条件）",
      _ssr_boundary_written_in_live_branch(DL_SRC))
check("首屏提示含可操作指引（切回完整采集通道 / 单条作品链接）",
      "切回完整采集通道" in DL_SRC and "单条作品链接" in DL_SRC)
check("UI 原样上屏 _last_list_note（不新增控件）",
      "_last_list_note" in PAGE_SRC and "note" in PAGE_SRC)

print("\n== 3. 归一化四类 schema 关键字段覆盖（离线样本） ==")
try:
    from justoneapi_collector.video_downloader import (
        KS_SCHEMA_OKDATA, KS_SCHEMA_LEGACY, KS_SCHEMA_SSR, KS_SCHEMA_PARSER,
        ks_normalize_to_video_info, ks_effective_media_type, ks_fail_text,
        KS_FAIL_TEXT,
    )
except Exception as exc:                                   # noqa: BLE001
    check("video_downloader 可离线 import", False, repr(exc))
    KS_SCHEMA_OKDATA = KS_SCHEMA_LEGACY = KS_SCHEMA_SSR = KS_SCHEMA_PARSER = ""
    ks_normalize_to_video_info = ks_effective_media_type = ks_fail_text = None
    KS_FAIL_TEXT = {"content": ""}
else:
    check("video_downloader 可离线 import", True)
    if ks_normalize_to_video_info is not None:
        samples = {
            KS_SCHEMA_OKDATA: {
                "id": "7708845995798416653", "text": "okdata 作品",
                "nickname": "纳兰", "create_time": 1700000000, "digg_count": 12,
                "cover_url": "https://cdn.example.com/c.jpg", "images": [],
            },
            KS_SCHEMA_LEGACY: {
                "photo_id": "7708845995798416653", "caption": "旧通道作品",
                "user_name": "纳兰", "timestamp": 1700000000000,
                "main_mv_urls": [{"url": "http://v.example.com/a.mp4"}],
            },
            KS_SCHEMA_SSR: {
                "photo": {"id": "7708845995798416653", "caption": "SSR 作品",
                          "userName": "纳兰", "timestamp": 1700000000000,
                          "photoUrl": "https://v.example.com/a.mp4"},
            },
            KS_SCHEMA_PARSER: {
                "video_id": "7708845995798416653", "title": "解析作品",
                "author": "纳兰", "video_url": "https://v.example.com/a.mp4",
                "images": [], "videos": [],
            },
        }
        for source, raw in samples.items():
            v, why = ks_normalize_to_video_info(raw, source)
            check(f"{source} 产出非空 video_id", bool(v and v.video_id),
                  f"why={why!r} v={v!r}")
            check(f"{source} platform 固定 kuaishou", bool(v and v.platform == "kuaishou"))

        # 图集样本（③ SSR atlas.cdn+list、④ parser images）
        atlas_raw = {"photo": {"id": "atlas_1", "workType": "ATLAS",
                               "atlas": {"cdn": ["cdn.example.com/"],
                                         "list": ["/a.jpg", "/b.jpg"]}}}
        av, _ = ks_normalize_to_video_info(atlas_raw, KS_SCHEMA_SSR)
        check("SSR 图集 image_list 填充 2 张、无直链",
              bool(av and len(av.image_list) == 2 and not av.no_watermark_url),
              repr(av.image_list if av else None))
        pv, _ = ks_normalize_to_video_info(
            {"video_id": "p1", "images": ["https://i/1.jpg", "https://i/2.jpg"]},
            KS_SCHEMA_PARSER)
        check("parser images 填进 image_list", bool(pv and len(pv.image_list) == 2))

        # create_time 口径：毫秒 /1000、ISO 字符串不炸
        mv, _ = ks_normalize_to_video_info(
            {"photo_id": "t1", "timestamp": 1700000000000}, KS_SCHEMA_LEGACY)
        check("毫秒 create_time 转秒级 int",
              bool(mv and mv.create_time == 1700000000), repr(mv.create_time if mv else None))
        iv, _ = ks_normalize_to_video_info(
            {"id": "t2", "create_time": "2023-11-14T22:13:20Z"}, KS_SCHEMA_OKDATA)
        check("ISO create_time 解析为秒级 int", bool(iv and iv.create_time > 0))

        # 丢弃规则：video_id 取不到 → 返回原因，不静默产出
        nv, nwhy = ks_normalize_to_video_info({"text": "缺 id"}, KS_SCHEMA_OKDATA)
        check("缺 video_id 返回 (None, 结构原因)", nv is None and bool(nwhy), nwhy)

        # 双向分流（客户截图 P0）：视频误选 image → video；图集误选 video → image
        from justoneapi_collector.video_downloader import VideoInfo
        video_only = VideoInfo(video_id="v1", no_watermark_url="https://v/a.mp4")
        atlas_only = VideoInfo(video_id="a1", image_list=["https://i/1.jpg"])
        empty_one = VideoInfo(video_id="e1")
        check("真视频选 image → 实际按 video 下",
              ks_effective_media_type(video_only, "image")[0] == "video")
        check("真图集选 video → 实际按 image 下",
              ks_effective_media_type(atlas_only, "video")[0] == "image")
        check("真视频选 audio → 尊重 audio",
              ks_effective_media_type(video_only, "audio")[0] == "audio")
        check("两者都无 → 返回内容类原因、不落内部术语",
              ks_effective_media_type(empty_one, "video") == ("", KS_FAIL_TEXT["content"]))

        # ★ 2026-11-25 客户澄清：截图报错实际发生在 **TikTok 视频** 提取，
        #   不是快手。分流函数虽带 ks_ 前缀（历史命名），但函数体不读 platform，
        #   对全部平台生效。这两条断言把「TikTok 也被覆盖」钉死，防止日后
        #   有人加平台守卫把它改成快手专用，让 TikTok 客户重新踩同一个坑。
        tiktok_video = VideoInfo(
            video_id="tk1", platform="tiktok",
            no_watermark_url="https://v/tiktok.mp4")
        check("TikTok 视频误选 image → 实际按 video 下（平台无关）",
              ks_effective_media_type(tiktok_video, "image")[0] == "video")
        tiktok_album = VideoInfo(
            video_id="tk2", platform="tiktok",
            image_list=["https://i/tiktok.jpg"])
        check("TikTok 图集误选 video → 实际按 image 下（平台无关）",
              ks_effective_media_type(tiktok_album, "video")[0] == "image")

        def _media_type_is_platform_agnostic(src: str) -> bool:
            """AST 校验：ks_effective_media_type 函数体不得读 video.platform。

            docstring 里的说明文字不算（跳过首个 Constant 表达式），
            只查真实执行语句里的 Attribute / getattr / Subscript 平台引用。
            """
            try:
                tree = ast.parse(src)
            except SyntaxError:
                return False
            target = None
            for node in ast.walk(tree):
                if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name == "ks_effective_media_type"):
                    target = node
                    break
            if target is None:
                return False
            body = target.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(getattr(body[0], "value", None), ast.Constant)):
                body = body[1:]
            for stmt in body:
                for sub in ast.walk(stmt):
                    if isinstance(sub, ast.Attribute) and sub.attr == "platform":
                        return False
                    if (isinstance(sub, ast.Call)
                            and isinstance(sub.func, ast.Name)
                            and sub.func.id == "getattr"
                            and any(isinstance(a, ast.Constant) and a.value == "platform"
                                    for a in sub.args)):
                        return False
                    if (isinstance(sub, ast.Subscript)
                            and isinstance(sub.slice, ast.Constant)
                            and sub.slice.value == "platform"):
                        return False
            return True

        check("双向分流不读 platform（TikTok 等全平台生效，AST 级）",
              _media_type_is_platform_agnostic(DL_SRC))

        check("_download_images 内部术语已改人话",
              "该作品不是图集/无图片列表" not in DL_SRC
              and "切到「视频」" in DL_SRC)
        check("ks_fail_text 七类齐全",
              all(k in KS_FAIL_TEXT for k in
                  ("config", "network", "limit", "content", "schema", "link", "empty")))

print("\n== 4. Worker 铁律 ==")
try:
    page_tree = ast.parse(PAGE_SRC)
    check("video_download_page.py 可解析", True)
except SyntaxError as exc:
    check("video_download_page.py 可解析", False, repr(exc))
    page_tree = None

if page_tree is not None:
    check("无 on_error=self._status.set", "on_error=self._status.set" not in PAGE_SRC)
    # ★ 2026-11-28：按钮接线按铁律统一为 connect_guarded(sig, lambda *_: ...)。
    #   验证不变量「下载按钮走 _on_download_button 分派入口（可停止）+ guarded 兜异常
    #   + 无裸 connect」，不再钉死旧字面量。
    check("下载按钮走分派入口（运行中可停止）",
          "def _on_download_button" in PAGE_SRC and
          "lambda *_: self._on_download_button()" in PAGE_SRC and
          "connect_guarded(self.download_btn.clicked" in PAGE_SRC and
          "self.download_btn.clicked.connect(" not in PAGE_SRC)
    check("停止标志用 lambda 闭包传入（无全局单例）",
          "lambda: self._download_stop" in PAGE_SRC)
    # Worker(...) 调用必须 .start()：用 AST 精确数 Name='Worker' 的 Call（排除 _SegmentWorker）
    n_worker = sum(1 for n in ast.walk(page_tree)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == "Worker")
    n_start = PAGE_SRC.count(".start()")
    check(f"真 Worker(...) 调用数({n_worker}) <= start 数({n_start})", n_worker <= n_start,
          f"注意 _SegmentWorker 不计入")
    # connect_guarded 存在（按钮 lambda 及槽接线都走它）
    check("使用 connect_guarded 接线", "connect_guarded(" in PAGE_SRC)

print("\n== 5. 三链路边界 ==")
parser_src = read("justoneapi_collector/media_parser.py")
check("media_parser.py 不含 okdata", "okdata" not in parser_src)
okdata_src = read("justoneapi_collector/okdata_client.py")
emap = okdata_src[okdata_src.find("ENDPOINT_MAP"):okdata_src.find("PARAM_ALIAS")]
check("ENDPOINT_MAP 未新增 SSR / 免费通道映射",
      "ssr" not in emap.lower() and "INIT_STATE" not in emap)
check("SSR 函数不调用付费 client.request",
      "def ks_fetch_ssr_first_page" in DL_SRC
      and "client.request" not in DL_SRC[DL_SRC.find("def ks_fetch_ssr_first_page"):
                                          DL_SRC.find("def _ks_degrade_category")])
check("okdata_client.py 的 _PASSTHROUGH 未加 pcursor/count",
      '"id", "url", "query", "cursor"' in okdata_src
      and '"pcursor"' not in okdata_src[okdata_src.find("_PASSTHROUGH"):
                                        okdata_src.find("_ERROR_TEXT")])

print("\n== 6. 不新增页面 / 功能位 ==")
registry_src = read("gui_qt/page_registry.py")
lic_src = read("justoneapi_collector/licensing.py")
check("page_registry.PAGES 未新增快手批量独立页",
      "ks_batch" not in registry_src.lower() and "快手批量" not in registry_src)
check("licensing.FEATURES 未新增快手批量功能位",
      "ks_batch" not in lic_src.lower() and "快手批量" not in lic_src)
# spec 不新增 hiddenimports（本轮函数都在既有模块内）
check("justoneapi_collector.spec 0 改动无法静态判字节，但无新模块 import",
      "import kuaishou_ssr" not in DL_SRC and "from .kuaishou" not in DL_SRC)

print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
