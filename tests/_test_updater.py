# -*- coding: utf-8 -*-
"""自动更新模块单测。

更新是会「动用户安装目录」的操作，比一般功能危险得多：下错了包、
校验漏了、装到一半失败，用户就等于被卸载了。所以这里重点钉死：

  1. 清单解析（自有 version.json / GitHub Release / 各种残缺数据）
  2. 版本比较与「跳过此版本 / 每天只提醒一次」节流
  3. 下载 + SHA256 校验（校验不过必须删文件，绝不能留下半个包）
  4. 安装入口的失败兜底（路径不存在时不能瞎启动）

全程用本地假 HTTP 服务，断网也能跑；不依赖外网。
"""
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="llbs_upd_")
os.environ["APPDATA"] = _TMP
os.environ["LOCALAPPDATA"] = _TMP

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from justoneapi_collector import branding as B   # noqa: E402
from justoneapi_collector import updater as U    # noqa: E402

PASS, FAIL = [], []
_TMP_DIRS = [_TMP]


def chk(name, cond, extra=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name}  ←  {extra}")


# ======================================================================
# 1. 清单解析（离线，纯数据）
# ======================================================================
_feed = {
    "version": "9.9.9",
    "published": "2026-10-08",
    "notes": "1. 新增直播录制指示灯\n2. 修复录制时长不刷新",
    "mandatory": False,
    "min_version": "1.0.0",
    "win": {"url": "https://example.com/a.exe", "size": 20000000, "sha256": "AB" * 32},
}
i = U.parse_manifest(json.dumps(_feed))
chk("自有清单：识别为新版本", i.available, i.error)
chk("自有清单：版本号正确", i.version == "9.9.9", i.version)
chk("自有清单：下载地址取到 win.url", i.url == "https://example.com/a.exe", i.url)
chk("自有清单：大小正确", i.size == 20000000, str(i.size))
chk("自有清单：sha256 取到", i.sha256 == "AB" * 32)
chk("自有清单：更新说明完整", "指示灯" in i.notes)
chk("自有清单：能接受 JSON 字符串输入", U.parse_manifest(json.dumps(_feed)).available)
chk("summary 形如 旧→新", "→" in i.summary() and "9.9.9" in i.summary(), i.summary())

_flat = {"version": "8.0.0", "url": "https://example.com/b.exe", "sha256": "cd" * 32,
         "notes": "扁平写法"}
i2 = U.parse_manifest(_flat)
chk("扁平写法也能解析", i2.available and i2.url.endswith("b.exe"), i2.error)

_old = {"version": "0.0.1", "win": {"url": "https://example.com/old.exe"}}
i3 = U.parse_manifest(_old)
chk("旧版本不提示更新", not i3.available, i3.version)
chk("旧版本 summary 说明已是最新", "最新" in i3.summary(), i3.summary())

_same = {"version": B.APP_VERSION, "win": {"url": "https://example.com/same.exe"}}
chk("同版本不提示更新", not U.parse_manifest(_same).available)

_gh = {
    "tag_name": "v7.5.0",
    "body": "GitHub 的更新说明",
    "published_at": "2026-10-09T08:00:00Z",
    "assets": [
        {"name": "readme.txt", "browser_download_url": "https://gh/r.txt", "size": 10},
        {"name": f"{B.SETUP_NAME}_v7.5.0.exe",
         "browser_download_url": "https://gh/Setup.exe", "size": 30000000},
    ],
}
i4 = U.parse_manifest(_gh, source="gh")
chk("GitHub Release：识别版本（去掉 v 前缀）", i4.version == "7.5.0", i4.version)
chk("GitHub Release：优先挑 exe 附件", i4.url == "https://gh/Setup.exe", i4.url)
chk("GitHub Release：body 当更新说明", i4.notes == "GitHub 的更新说明")
chk("GitHub Release：published_at 截到日期", i4.published == "2026-10-09", i4.published)
chk("GitHub Release：可用", i4.available, i4.error)
chk("GitHub Release：source 被记录", i4.source == "gh")

chk("坏 JSON：给出可读错误",
    "JSON" in U.parse_manifest("这不是 json").error,
    U.parse_manifest("这不是 json").error)
chk("缺 version：明确报错",
    "version" in U.parse_manifest({"win": {"url": "x"}}).error)
chk("缺下载地址：明确报错",
    "下载地址" in U.parse_manifest({"version": "9.9.9"}).error)
chk("Release 没 exe 附件：明确报错",
    "exe" in U.parse_manifest({"tag_name": "v9.9.9", "assets": []}).error)
chk("顶层不是对象：明确报错",
    "对象" in U.parse_manifest([1, 2, 3]).error)
chk("解析任何垃圾都不抛异常",
    all(isinstance(U.parse_manifest(x), U.UpdateInfo)
        for x in ["", "[]", "{}", "null", {"assets": "notalist"}]))

chk("强制更新标记能读出来",
    U.parse_manifest({"version": "9.9.9", "url": "u", "mandatory": True}).mandatory)
chk("size_text 人类可读", "MB" in i.size_text(), i.size_text())

# ======================================================================
# 2. 版本比较 / 节流 / 跳过
# ======================================================================
chk("is_newer：更高版本", B.is_newer("2.0.0", "1.9.9"))
chk("is_newer：同版本为 False", not B.is_newer("1.0.0", "1.0.0"))
chk("is_newer：更低版本为 False", not B.is_newer("0.9.0", "1.0.0"))

st = {}
chk("首次应提示", U.should_prompt(st, "9.9.9"))
U.mark_prompted(st)
chk("刚提醒过 → 不再提示", not U.should_prompt(st, "9.9.9"))
chk("隔一天后又提示", U.should_prompt(st, "9.9.9", interval_days=0))
st2 = {"update_skip_version": "9.9.9"}
chk("跳过过的版本不再提示", not U.should_prompt(st2, "9.9.9"))
chk("跳过只针对那个版本（新版本照常提示）", U.should_prompt(st2, "9.9.10"))
U.mark_skipped(st2, "9.9.10")
chk("mark_skipped 写入版本号", st2.get("update_skip_version") == "9.9.10")
U.clear_skipped(st2)
chk("clear_skipped 能清掉", U.should_prompt(st2, "9.9.10"))
chk("空版本号不提示", not U.should_prompt({}, ""))

_tpl = json.loads(U.version_file_template())
chk("version.json 模板是合法 JSON", isinstance(_tpl, dict))
chk("模板里含 version/notes/win.url",
    "version" in _tpl and "notes" in _tpl and "url" in _tpl.get("win", {}))

# ======================================================================
# 3. 本地假 HTTP 服务
# ======================================================================
PAYLOAD = os.urandom(300 * 1024)
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()
ROUTES: dict[str, tuple[int, str, bytes]] = {}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, ctype: str, body: bytes, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        route = ROUTES.get(self.path)
        if not route:
            self.send_response(404)
            self.end_headers()
            return
        code, ctype, body = route
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self):
        route = ROUTES.get(self.path)
        if not route:
            self._send(404, "text/plain", b"not found")
            return
        code, ctype, body = route
        if code != 200:
            self._send(code, ctype, body)
            return
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            if m:
                s = int(m.group(1))
                e = int(m.group(2)) if m.group(2) else len(body) - 1
                e = min(e, len(body) - 1)
                chunk = body[s:e + 1]
                self._send(206, ctype, chunk,
                           {"Content-Range": f"bytes {s}-{e}/{len(body)}"})
                return
        self._send(200, ctype, body)


_srv = HTTPServer(("127.0.0.1", 0), _Handler)
threading.Thread(target=_srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{_srv.server_port}"

ROUTES["/version.json"] = (200, "application/json", json.dumps({
    "version": "9.9.9",
    "published": "2026-10-08",
    "notes": "测试更新说明",
    "win": {"url": f"{BASE}/Setup.exe", "size": len(PAYLOAD), "sha256": PAYLOAD_SHA},
}).encode("utf-8"))
ROUTES["/badsha.json"] = (200, "application/json", json.dumps({
    "version": "9.9.9",
    "win": {"url": f"{BASE}/Setup.exe", "size": len(PAYLOAD), "sha256": "00" * 32},
}).encode("utf-8"))
ROUTES["/nohash.json"] = (200, "application/json", json.dumps({
    "version": "9.9.9",
    "win": {"url": f"{BASE}/Setup.exe", "size": len(PAYLOAD)},
}).encode("utf-8"))
ROUTES["/broken.json"] = (200, "application/json", b"{not json")
ROUTES["/Setup.exe"] = (200, "application/octet-stream", PAYLOAD)
ROUTES["/api/latest"] = (200, "application/json", json.dumps({
    "tag_name": "v9.9.9",
    "body": "Release 说明",
    "published_at": "2026-10-09T00:00:00Z",
    "assets": [{"name": f"{B.SETUP_NAME}.exe",
                "browser_download_url": f"{BASE}/Setup.exe", "size": len(PAYLOAD)}],
}).encode("utf-8"))

# ======================================================================
# 4. check_for_update（真联网，但只连本机）
# ======================================================================
info = U.check_for_update(f"{BASE}/version.json")
chk("联网检查：发现新版本", info.available, info.error)
chk("联网检查：版本号正确", info.version == "9.9.9", info.version)
chk("联网检查：拿得到 sha256", info.sha256 == PAYLOAD_SHA)
chk("联网检查：拿得到大小", info.size == len(PAYLOAD), str(info.size))

info_gh = U.check_for_update(f"{BASE}/api/latest")
chk("联网检查：GitHub 风格接口也能用", info_gh.available, info_gh.error)

info404 = U.check_for_update(f"{BASE}/nope.json")
chk("404 给出可读提示", not info404.available and "404" in info404.error, info404.error)
chk("404 时 latest 仍是当前版本", info404.latest == B.APP_VERSION)

info_bad = U.check_for_update(f"{BASE}/broken.json")
chk("清单是坏 JSON → 明确报错", "JSON" in info_bad.error, info_bad.error)

# 产品里 branding.UPDATE_FEED_URL 已填真实地址；这里临时置空，
# 专门验证「完全没配置更新地址」这个分支（否则会去查 GitHub 返回 404）。
_orig_feed = B.UPDATE_FEED_URL
B.UPDATE_FEED_URL = ""
info_empty = U.check_for_update("")
B.UPDATE_FEED_URL = _orig_feed
chk("没配地址时提示未配置", "尚未配置" in info_empty.error, info_empty.error)

info_dead = U.check_for_update("http://127.0.0.1:9/never.json", timeout=3)
chk("连不上时给出可读错误", not info_dead.available and bool(info_dead.error),
    info_dead.error)

# ======================================================================
# 5. 下载 + 校验
# ======================================================================
_DL_DIR = Path(_TMP) / "updates"

prog_calls = []
ok, res = U.download_update(info, dest_dir=_DL_DIR,
                           progress=lambda d, t: prog_calls.append((d, t)))
chk("下载成功", ok, res)
chk("文件确实落盘", ok and Path(res).is_file(), res)
chk("文件名取自 URL", ok and Path(res).name == "Setup.exe", res)
chk("下载内容与源完全一致",
    ok and hashlib.sha256(Path(res).read_bytes()).hexdigest() == PAYLOAD_SHA)
chk("有进度回调", len(prog_calls) > 0)
chk("进度是单调递增的",
    all(prog_calls[k][0] <= prog_calls[k + 1][0] for k in range(len(prog_calls) - 1)))

# 已存在且校验通过 → 复用，不重复下载
ok2, res2 = U.download_update(info, dest_dir=_DL_DIR)
chk("已下好且校验通过时直接复用", ok2 and res2 == res)

# sha256 不匹配 → 必须删文件
info_bad_sha = U.check_for_update(f"{BASE}/badsha.json")
_bad_dir = Path(_TMP) / "updates_bad"
ok3, res3 = U.download_update(info_bad_sha, dest_dir=_bad_dir)
chk("sha256 不匹配 → 判定失败", not ok3, res3)
chk("sha256 不匹配原因可读", "SHA256" in str(res3) or "校验" in str(res3), str(res3))
chk("sha256 不匹配 → 下载文件被删掉（不留半个包）",
    not (Path(_bad_dir) / "Setup.exe").exists())

# 清单没给 sha256 → 跳过校验但允许安装（并说明）
info_no_hash = U.check_for_update(f"{BASE}/nohash.json")
logs: list[str] = []
ok4, res4 = U.download_update(info_no_hash, dest_dir=Path(_TMP) / "updates_nohash",
                              log=logs.append)
chk("没有 sha256 时仍可下载", ok4, res4)
chk("没有 sha256 时给出跳过校验的提示",
    any("跳过完整性校验" in t for t in logs), " | ".join(logs))

# sha256_of 自身正确性
_f = Path(_TMP) / "hashme.bin"
_f.write_bytes(b"hello world")
chk("sha256_of 与 hashlib 一致",
    U.sha256_of(_f) == hashlib.sha256(b"hello world").hexdigest())

chk("guess_filename 用 URL 里的文件名", U.guess_filename(info) == "Setup.exe")
_weird = U.UpdateInfo()
_weird.url = "https://x/y/z"
chk("guess_filename 无扩展名时补 exe",
    U.guess_filename(_weird).endswith(".exe"), U.guess_filename(_weird))
_weird2 = U.UpdateInfo()
_weird2.url = "https://x/安装包:测试?.exe"
chk("guess_filename 清掉非法字符",
    all(c not in U.guess_filename(_weird2) for c in ':*?"<>|'),
    U.guess_filename(_weird2))

# ======================================================================
# 6. 安装入口（只验证兜底，绝不真的执行）
# ======================================================================
ok5, err5 = U.launch_installer(Path(_TMP) / "根本没有这个文件.exe")
chk("安装包不存在 → 拒绝启动并说明", not ok5 and "找不到" in err5, err5)

found = U.find_local_setup(_DL_DIR)
chk("能在目录里找到本地安装包", any(p.name == "Setup.exe" for p in found), str(found))
chk("找不到目录时返回空列表", U.find_local_setup(Path(_TMP) / "没有这个目录") == [])

cache = U.update_cache_dir()
chk("更新缓存目录可用", cache.is_dir(), str(cache))
chk("缓存目录名带品牌", B.DATA_DIR_NAME in str(cache), str(cache))

# ----------------------------------------------------------------------
# 7. 下载直链 URL 编码（中文文件名不能裸奔，否则 CDN 404）
# ----------------------------------------------------------------------
_u1 = "https://github.com/z807108536hub/TrafficCatcher/releases/latest/download/流量捕手_Setup_v1.0.0.exe"
_e1 = U._encode_url(_u1)
chk("中文文件名被 percent-encode", "%E6%B5%81" in _e1, _e1)
chk("编码后 host 不变", "github.com/z807108536hub/TrafficCatcher" in _e1, _e1)
chk("编码后保留 .exe 后缀", _e1.endswith(".exe"), _e1)
_u2 = "https://example.com/a.exe"
chk("纯 ASCII 下载地址编码后不变", U._encode_url(_u2) == _u2, U._encode_url(_u2))
_u3 = "https://example.com/普通路径/file.exe"
chk("路径中的中文也被编码", "%E6%99%AE%E9%80%9A" in U._encode_url(_u3), U._encode_url(_u3))


_srv.shutdown()
for d in _TMP_DIRS:
    shutil.rmtree(d, ignore_errors=True)

print()
print(f"结果: {len(PASS)} 通过 / {len(FAIL)} 失败")
if FAIL:
    print("失败项:")
    for f in FAIL:
        print("  " + f)
    sys.exit(1)
