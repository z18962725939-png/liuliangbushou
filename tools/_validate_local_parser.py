# -*- coding: utf-8 -*-
"""门禁：本机 media-parser 解析引擎（纯标准库，不联网、不引 Qt）。

锚定 justoneapi_collector/local_media_parser.py 与两处接线：
  · 协议常量锁死：本机回环 8051、/api/health、/api/v1/parse
  · 四套凭证严格隔离：不读 okd_ / JustOneAPI / XiaoYing / 授权码 Token
  · 不内置支付、不回传数据、不硬编码个人盘符
  · 上游字段归一化：image_list→images、video_list→videos、author.nickname
  · 业务错误码翻成人话，失败不抛异常
  · 可选引擎排在 zacao 与抖音 CDP 之前，失败必须原样回退
  · 设置页四按钮全部 guarded，Worker 必 start，配置键独立且默认安全
  · PyInstaller hiddenimports 登记（函数体内 import 静态扫不到）

跑法：python tools/_validate_local_parser.py
"""
from __future__ import annotations

import ast
import io
import os
import sys
import tokenize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

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


def code_only(src: str) -> str:
    """剥注释与字符串，只留可执行代码（负例写进代码仍报红）。"""
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out)


def literal_strings(src: str) -> list[str]:
    """字符串常量值，排除 docstring（说明文字不参与负例判定）。"""
    tree = ast.parse(src)
    docs: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
                          ast.AsyncFunctionDef)):
            body = getattr(n, "body", [])
            if body and isinstance(body[0], ast.Expr):
                v = body[0].value
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    docs.add(id(v))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs]


def read(rel: str) -> str:
    from pathlib import Path
    return (Path(ROOT) / rel).read_text(encoding="utf-8")


def find_func(src: str, name: str) -> ast.FunctionDef | None:
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


from justoneapi_collector import local_media_parser as lp  # noqa: E402

print("\n== 1. 协议常量 ==")
check("默认地址锁死本机回环", lp.DEFAULT_BASE_URL == "http://127.0.0.1:8051")
check("默认端口 8051", lp.DEFAULT_PORT == 8051)
check("健康检查端点", lp.HEALTH_PATH == "/api/health")
check("解析端点", lp.PARSE_PATH == "/api/v1/parse")
check("上游快照地址正确",
      lp.SOURCE_ZIP == "https://github.com/ucmao/media-parser/archive/refs/heads/main.zip")
check("依赖走国内镜像", "tuna.tsinghua.edu.cn" in lp.PIP_INDEX)
check("纯 API 模式启动（免鉴权、零费用）", "API_ONLY" in read(
    "justoneapi_collector/local_media_parser.py"))
for code in ("XIAOHONGSHU_COOKIE_REQUIRED", "MEDIA_DELETED_OR_PRIVATE",
             "PLATFORM_NOT_SUPPORTED", "RATE_LIMITED", "INVALID_TEXT"):
    check(f"错误码有中文兜底 {code}", code in lp._ERROR_HINT)

print("\n== 2. 凭证隔离 / 本地边界 ==")
src = read("justoneapi_collector/local_media_parser.py")
code = code_only(src)
lits = literal_strings(src)
for bad in ("okd_", "OKDATA_KEY", "TRAFFIC_MEDIA_KEY", "TRAFFIC_XIAOYING",
            "justoneapi.com", "video.zacao.top"):
    check(f"不混用 {bad}", bad not in code and
          all(bad not in v for v in lits))
check("只认本机地址探活", any("127.0.0.1" in v for v in lits) and
      any("localhost" in v for v in lits))
check("不内置支付", all(k not in code and all(k not in v for v in lits)
                    for k in ("alipay", "wxpay", "stripe", "二维码", "支付表单")))
check("不硬编码个人盘符", not any(v[:2].isalpha() and v[1:3] == ":\\"
                                for v in lits))
check("不读授权码 Token", '"token"' not in lits and "'token'" not in lits)
check("不回传诊断数据", "diag_endpoint" not in code)

print("\n== 3. 字段归一化 ==")
norm = lp.normalize({
    "platform": "抖音", "title": "T", "video_id": "7",
    "author": {"nickname": "张三"}, "cover_url": "c",
    "video_url": "v0", "video_list": ["v1", "v2"],
    "image_list": ["i1", {"url": "i2", "live_photo_url": "live"}],
})
check("video_url 保留", norm["video_url"] == "v0")
check("image_list→images（含实况图封面）", norm["images"] == ["i1", "i2"])
check("video_list→videos（带封面）", [u for u, _ in norm["videos"]] == ["v1", "v2"])
check("author.nickname→author", norm["author"] == "张三")
check("平台名原样透传", norm["platform"] == "抖音")
check("空结果不误判", lp.parse("这段没有链接")[1].startswith("没有可解析"))

print("\n== 4. 回退链（不许切断既有链路） ==")
vd = read("justoneapi_collector/video_downloader.py")
check("存在本机解析入口", "def _local_service_resolve" in vd)
check("非抖音链路先试本机引擎",
      "local_video, _local_reason = self._local_service_resolve(target)" in vd)
check("抖音链路先试本机引擎",
      "local_video, _lreason = self._local_service_resolve(text)" in vd)
i_local = vd.find("local_video, _local_reason = self._local_service_resolve(target)")
i_zacao = vd.find("from .media_parser import parse as _svc_parse", i_local)
check("本机引擎排在 zacao 之前且 zacao 仍在", 0 <= i_local < i_zacao)
fn = find_func(vd, "_local_service_resolve")
fncode = ast.dump(fn) if fn else ""
fn_src = (ast.get_source_segment(vd, fn) or "") if fn else ""
check("未启用时不发请求", "is_enabled" in fncode)
check("异常一律吞掉不连累批量", "except Exception" in fn_src)
mp = read("justoneapi_collector/media_parser.py")
check("zacao 地址未被改动", 'BASE_URL = "https://video.zacao.top"' in mp)

print("\n== 5. 设置页与配置 ==")
sp = read("gui_qt/pages/settings_page.py")
spcode = code_only(sp)
for t in ("检查服务", "一键部署", "启动服务", "停止服务"):
    check(f"按钮存在 {t}", f'"{t}"' in sp)
check("全部按钮走 guarded", "connect_guarded(btn.clicked" in sp)
# 只看本次新增卡片那一段，不把历史上遗留下来的 toggled.connect 算进来
_i = sp.find("本机解析引擎（免费、可选）")
_j = sp.find("每档末尾必须垫一根弹簧", _i)
_new_seg = sp[_i:_j] if 0 <= _i < _j else ""
check("新卡片按钮无裸 connect", ".clicked.connect" not in _new_seg and
      bool(_new_seg))
run_fn = find_func(sp, "_local_run")
run_src = ast.get_source_segment(sp, run_fn) or "" if run_fn else ""
check("本机引擎 Worker 必须 start", "w.start()" in run_src)
for key in ("local_parser_enabled", "local_parser_base_url",
            "local_parser_api_key", "local_parser_dir"):
    check(f"保存写入 {key}", f's["{key}"]' in sp)
base = read("gui_qt/pages/_base.py")
tree = ast.parse(base)
defaults = None
for n in ast.walk(tree):
    if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "_SETTINGS_DEFAULTS":
        defaults = ast.literal_eval(n.value)
        break
check("本机引擎默认关闭", bool(defaults) and defaults.get("local_parser_enabled") is False)
check("默认地址仅本机", bool(defaults) and "127.0.0.1" in str(defaults.get("local_parser_base_url", "")))
check("默认 Key 为空", bool(defaults) and defaults.get("local_parser_api_key") == "")
check("默认目录为空", bool(defaults) and defaults.get("local_parser_dir") == "")

print("\n== 6. 打包可见性 ==")
spec = read("justoneapi_collector.spec")
check("hiddenimports 登记本机引擎", "justoneapi_collector.local_media_parser" in spec)
check("自带离线包打进安装包", "local_parser_portable.zip" in spec)

print("\n== 7. 客户机免安装部署（一键部署链） ==")
zip_path = os.path.join(ROOT, "assets", "local_parser_portable.zip")
check("自带离线包存在", os.path.isfile(zip_path),
      "assets/local_parser_portable.zip 缺失，客户机将无法免安装部署")
if os.path.isfile(zip_path):
    import zipfile
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        check("包内带 app.py", "app.py" in names)
        check("包内带 python.exe", "python.exe" in names)
        check("包内带依赖 Flask", any(n.lower().startswith("lib/site-packages/flask/") for n in names))
        check("包内带 V8 引擎快照", any(n.endswith("snapshot_blob.bin") for n in names))
        check("包内带解析器源码", any(n.startswith("src/parsers/") and n.endswith(".py") for n in names))
        check("本机密钥不入包", not any(".secret_key" in n for n in names))
        check("本机数据库不入包", not any(n.endswith(".db") for n in names))
lp_src = read("justoneapi_collector/local_media_parser.py")
lp_code = code_only(lp_src)
check("优先找自带离线包", "BUNDLED_ZIP_NAME" in lp_code and "bundled_zip" in lp_code)
check("部署目录强制纯英文", "ASCII_ROOT_NAME" in lp_code and "_is_ascii_path" in lp_code)
check("启动用包内 Python", "bundled_python" in lp_code)
_lp_lits = literal_strings(lp_src)
check("服务脱离父进程控制台", "DETACHED_PROCESS" in _lp_lits and
      "start_new_session" in _lp_lits)
_start_fn = find_func(lp_src, "start_service")
_start_src = ast.get_source_segment(lp_src, _start_fn) or "" if _start_fn else ""
check("英文目录防护在启动前", "_is_ascii_path(root)" in _start_src and
      _start_src.find("_is_ascii_path(root)") < _start_src.find("subprocess.Popen"))

print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
