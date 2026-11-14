# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec ——「流量捕手」（TrafficCatcher）桌面端。

Build:
    pyinstaller justoneapi_collector.spec --noconfirm

Output:
    dist/流量捕手/流量捕手.exe

exe 名取自 branding.EXE_NAME；改软件名只改 justoneapi_collector/branding.py。

★ 一份 spec 打两个发行版（2026-10-23）：打包前设环境变量即可，源码不动。
    pyinstaller justoneapi_collector.spec              → 完整版
    TRAFFIC_EDITION=free pyinstaller justoneapi_collector.spec → 免费版
  免费版靠 tools/free_edition_hook.py（runtime hook）在启动时注入标记，
  详见 branding.py 里 APP_EDITION 那一段的说明。
"""

import os
import sys
from pathlib import Path

# 直接按文件加载 branding.py，不走包 __init__（避免拉起 httpx 等第三方依赖，
# 也避免打包环境里包导入失败导致 spec 直接报错）。branding 只依赖标准库。
def _load_branding():
    import importlib.util
    p = Path(SPECPATH).resolve() / "justoneapi_collector" / "branding.py"
    spec = importlib.util.spec_from_file_location("_branding_spec", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_branding = _load_branding()

block_cipher = None

# Project root (where this spec file lives)
PROJECT_ROOT = Path(SPECPATH).resolve()
PKG_ROOT = PROJECT_ROOT / "justoneapi_collector"

# ---- 免费版 runtime hook（说明见文件头的「一份 spec 打两个发行版」）----
# 只在这个构建里挂：TRAFFIC_EDITION=free 时，exe 一启动就把环境变量设成
# free，branding.APP_IS_FREE 随之变 True，导航/标题/授权区全部跟着走。
# 缺 hook 就直接报错退出 —— 打出来的免费版如果忘了挂，会变成一个
# 「名字叫免费版、实际是完整版」的包，客户装上还是看到一排点不动的功能。
_IS_FREE_BUILD = (os.environ.get("TRAFFIC_EDITION") or "").strip().lower() == "free"
_runtime_hooks = []
if _IS_FREE_BUILD:
    _free_hook = PROJECT_ROOT / "tools" / "free_edition_hook.py"
    if not _free_hook.exists():
        raise SystemExit(f"[免费版打包] 缺少 runtime hook：{_free_hook}")
    _runtime_hooks.append(str(_free_hook))

# Collect all package data (registry, generated paths, etc.)
datas = [
    (str(PKG_ROOT / "platforms"), "justoneapi_collector/platforms"),
    # PySide6 界面主题（QSS 非 Python 文件，需显式收集）
    (str(PROJECT_ROOT / "gui_qt" / "styles"), "gui_qt/styles"),
]

# 自带 CPU 转写引擎（约 2 MB）：用户打开就能转写，不用先联网下载引擎包。
# CUDA 引擎体积太大（59 MB / 439 MB），不随包分发，由程序按显卡按需下载。
_BUNDLED_CPU = PROJECT_ROOT / "assets" / "whisper_cpu"
if _BUNDLED_CPU.exists():
    datas.append((str(_BUNDLED_CPU), "whisper_cpu"))

# 自带 ffmpeg（约 200MB）：直播录制 / 音视频抽取 / 转写都依赖它。
# PyInstaller 6.x 的 one-folder 会把 datas 落到 dist/流量捕手/_internal/ffmpeg/，
# 运行时各模块按 _MEIPASS/ffmpeg 与 exe 同级/_internal/ffmpeg 自动发现
# —— 新手开箱即用，不用再手动指定。安装器 Source: "dist\流量捕手\*" 一并带进安装目录。
# 安装器 [Files] Source: "dist\流量捕手\*" 会自动把它带进安装目录（2026-11-02）。
_FFMPEG_BUNDLE = PROJECT_ROOT / "assets" / "ffmpeg"
if _FFMPEG_BUNDLE.exists():
    for _ff in ("ffmpeg.exe", "ffprobe.exe"):
        _ffp = _FFMPEG_BUNDLE / _ff
        if _ffp.exists():
            datas.append((str(_ffp), "ffmpeg"))
# 注意：**不要**把 .env 打进产物。
# 界面（GUI）只从用户目录的 config.json 读取 Token，从不读 .env；
# 打进去只会把开发者的 Token 随 exe 一起分发出去，且那个 Token 一旦过期，
# 用户看到的就是"程序自带的 Token 是坏的"。用户首次运行自己填 Token 即可。

binaries = []

# Hidden imports — ensure PyInstaller bundles all submodules used dynamically
hiddenimports = [
    "httpx",
    "httpx._transports",
    "httpx._transports.default",
    "httpcore",
    "h11",
    "anyio",
    "anyio._backends",
    "anyio._backends._asyncio",
    "sniffio",
    "click",
    "openpyxl",
    "dotenv",
    "rich",
    # Our own package — include all submodules
    "justoneapi_collector",
    "justoneapi_collector.platforms",
    "justoneapi_collector.platforms.registry",
    "justoneapi_collector.platforms.api_paths_generated",
    "justoneapi_collector.platforms.base",
    "justoneapi_collector.client",
    "justoneapi_collector.config",
    "justoneapi_collector.cache",
    "justoneapi_collector.collector",
    "justoneapi_collector.exporter",
    "justoneapi_collector.models",
    "justoneapi_collector.exceptions",
    "justoneapi_collector.logger",
    "justoneapi_collector.rate_limiter",
    "justoneapi_collector.retry",
    "justoneapi_collector.video_downloader",
    "justoneapi_collector.live_monitor",
    "justoneapi_collector.douyin_live_stream",
    "justoneapi_collector.ab_sign",
    # v1.28.3 转写状态判定 / 输出去重 / 文稿映射清单（transcribe.py 顶层引用）
    "justoneapi_collector.transcribe_state",
    # 喜欢/收藏列表自研爬虫（HTTP 直连版 + 浏览器会话版）
    "justoneapi_collector.douyin_fav_crawler",
    "justoneapi_collector.douyin_cdp_crawler",
    # v1.24.2 浏览器渲染解析（抖音把作品数据从 SSR 里拿掉之后的唯一活路）。
    # ★ 它是被 video_downloader._browser_render_resolve() 在**函数体内** import 的，
    #   静态分析扫不到 —— 不写在这里，打出来的包里解析会 100% 失败。
    "justoneapi_collector.douyin_page_resolver",
    # ★ v1.24.6 服务端解析（抖音 / 小红书 / 快手的主力解析通道）。
    #   同样是 video_downloader._media_service_resolve() 内部的函数体 import，
    #   漏写这一行的后果一模一样：源码能跑、打出来的安装包直接不解析。
    "justoneapi_collector.media_parser",
    # ★ v1.24.6 服务端解析（抖音/小红书/快手的主力解析通道）。
    #   同样是 video_downloader._media_service_resolve() 内部的函数体 import，
    #   漏写这一行的后果一模一样：源码能跑、安装包报 ImportError 直接不解析。
    "justoneapi_collector.media_parser",
    "justoneapi_collector.browser_login",
    "justoneapi_collector.competitor_check",
    # 注意：justoneapi_collector.guide 与 justoneapi_collector.ops_ui 已移除。
    # 二者顶层即 `import tkinter`，且仅被废弃的旧 Tkinter 界面 gui.py 使用；
    # 新版 PySide6 界面（gui_qt）从不引用，而 spec 已 excludes=["tkinter"]，
    # 打进去只会增大体积并埋下 ImportError 隐患。
    "justoneapi_collector.browser_login",
    "justoneapi_collector.transcribe",
    "justoneapi_collector.video_script_extract",
    # 短视频文案仿写（v1.9.0）—— 引擎层与 geo_monitor 共用，两个都要打进去
    "justoneapi_collector.geo_monitor",
    "justoneapi_collector.script_rewriter",
    # 商业化 / 更新（v1.0 新增）—— 显式列出，防止被静态分析漏掉
    "justoneapi_collector.branding",
    "justoneapi_collector.licensing",
    "justoneapi_collector.quota",          # v1.1 每日调用配额闸门（运行时动态 import，静态分析扫不到）
    "justoneapi_collector.updater",
    # 运营功能子模块（ops_ui 已移除，理由见上方注释）
    "justoneapi_collector.projects",
    "justoneapi_collector.media_vault",
    "justoneapi_collector.reporter",
    "justoneapi_collector.topic_mining",
    "justoneapi_collector.watchtower",
    "justoneapi_collector.comment_mining",   # v1.7.0 评论区截流
    # v1.12.0 本地历史库（数据趋势页 + 适配层的落库钩子）。
    # 适配层里是**惰性 import**，静态分析扫不到，必须显式声明，
    # 否则打包后曲线永远是空的，而且不报任何错。
    "justoneapi_collector.history_store",
    "justoneapi_collector.geo_monitor",      # v1.8.0 AI 可见度监测（GEO）
    # —— 新版 PySide6 界面层（替代旧 Tkinter gui）——
    "PySide6.QtSvg",
    "gui_qt",
    "gui_qt.app",
    "gui_qt.main_window",
    "gui_qt.theme",
    "gui_qt.icons",
    "gui_qt.pages",
    "gui_qt.pages._base",
    "gui_qt.pages.collect_page",
    "gui_qt.pages.batch_page",
    "gui_qt.pages.settings_page",
    "gui_qt.pages.transcribe_page",
    "gui_qt.pages.live_page",
    "gui_qt.pages.competitor_page",
    "gui_qt.pages.topic_page",
    "gui_qt.pages.watch_page",
    "gui_qt.pages.report_page",
    "gui_qt.pages.video_download_page",
    "gui_qt.pages.comment_page",
    "gui_qt.pages.geo_page",
    # v1.26.0 短剧成片（DramaClip 剪辑引擎）。
    # ★ 剪辑引擎跑在**子进程**里（drama_bridge 用 subprocess 拉起外部 exe），
    #   主进程这边只剩桥接与下载器两个模块 —— 页面是顶层 import，
    #   但两者都用到动态属性（队列/线程），显式列出才不会在打包后被裁掉。
    "gui_qt.pages.drama_page",
    "justoneapi_collector.drama_bridge",
    "justoneapi_collector.drama_bundle",
    # 2026-11-14 短剧解析（HTTP 直连 XiaoYingAPI，与上面的剪辑引擎不是一条链）。
    # drama_api 里 httpx / json / branding 都在函数体内 import，显式登记防裁；
    # 页面也是新增页，打包后曾多次出现「只见状态栏不动」的静默缺失。
    "gui_qt.pages.drama_parse_page",
    "justoneapi_collector.drama_api",
    # 2026-11-14 免凭证公开热榜降级源（httpx 在函数体内 import，显式登记防裁）。
    "justoneapi_collector.drama_public_rank",
    # 评论区截流核心（页面里 import，静态分析可能漏）
    "justoneapi_collector.comment_mining",
    # 引导条组件与「使用教程」对话框（被各页面 main_window 动态引用）
    "gui_qt.widgets",
    "gui_qt.widgets.guide",
    "gui_qt.guide_dialog",
    # 强制登录门（app.py / settings_page.py 在函数内 import，静态分析易漏）
    "gui_qt.login_gate",
    # v1.15.0 视觉层新增：空态引导（main_window 顶层 import，但页面里也可能
    # 直接在函数内引用）+ 字体注册（theme 里 import QFontDatabase）
    "gui_qt.widgets.empty",
    # v1.16.0 Ctrl+K 命令面板：★ main_window._open_palette 里是**函数内 import**，
    #   静态分析扫不到。漏了它的症状是「按 Ctrl+K 毫无反应」，且不报错。
    "gui_qt.widgets.command_palette",
    "gui_qt.widgets.alert",
    # v1.27.0 屏幕区域录制：区域选择浮层（live_page 顶层 import，但为稳妥显式声明）
    "gui_qt.widgets.region_picker",
]

a = Analysis(
    [str(PROJECT_ROOT / "gui_main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=_runtime_hooks,
    excludes=[
        # Trim unused heavy modules to reduce size
        "matplotlib",
        "numpy",
        "pandas",
        "PIL",
        "PyQt5",
        "tkinter",         # 旧 Tkinter 界面已弃用，不再需要
        "unittest",
        "test",
        "pytest",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=_branding.EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # 关闭 UPX：实测 UPX 压缩 python310.dll / pyside6.abi3.dll 会导致
    # 打包后 exe 启动即崩溃（退出码 0xC0000409 / STATUS_STACK_BUFFER_OVERRUN）。
    # 商业发行包稳定性优先于体积，故禁用。
    upx=False,
    console=False,  # GUI app — no console window
    # 靶心品牌图标：由 build/app_icon.ico 提供（项目根 build 目录下）。
    # 带存在性保护——缺失则回退无图标，不阻断打包。
    icon=(
        str(PROJECT_ROOT / "build" / "app_icon.ico")
        if (PROJECT_ROOT / "build" / "app_icon.ico").exists()
        else None
    ),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    # 关闭 UPX：实测 UPX 压缩 python310.dll / pyside6.abi3.dll 会导致
    # 打包后 exe 启动即崩溃（退出码 0xC0000409 / STATUS_STACK_BUFFER_OVERRUN）。
    # 商业发行包稳定性优先于体积，故禁用。
    upx=False,
    upx_exclude=[],
    name=_branding.EXE_NAME,
)
