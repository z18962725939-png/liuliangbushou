# -*- coding: utf-8 -*-
"""门禁：SocialDataX 红果专用通道（纯标准库，不联网、不引 Qt）。

锚定 justoneapi_collector/socialdatax_client.py 与
gui_qt/pages/drama_parse_page.py 的接线：
  · 七个红果端点 + 只读余额端点，路径不许改、不许混用平台
  · 凭证隔离：sd Key 与 okdata / XiaoYingAPI / media_parser 不共用
  · 业务错误必须先查 code；401/403/429/5xx 分类人话
  · 积分 points.cost/balance 上屏；不内置支付，只给官网链接
  · 用户限制上屏：授权、积分、链接时效、无周榜
  · 下载必须即时换址 + CENC 密钥同档配对，ffmpeg -decryption_key 在 -i 之前
  · 页面三通道降级顺序 + 每个 Worker 都 start + 全部 guarded
  · AST 确认没有裸 clicked.connect / on_error=self._status.set

跑法：python tools/_validate_socialdatax.py
"""
from __future__ import annotations

import ast
import io
import os
import re
import sys
import tokenize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0


def code_only(src: str) -> str:
    """剥掉注释与字符串字面量，只留可执行代码（负例真写进代码仍会报红）。"""
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out)


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK ] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


from justoneapi_collector import socialdatax_client as s  # noqa: E402

print("\n== 1. 端点与常量 ==")
check("服务域名锁定", s.BASE_URL == "https://mcp.socialdatax.com")
check("API 前缀锁定", s.API_PREFIX == "/socialdatax/api/v1")
check("七个红果端点齐全", len(s.ENDPOINTS) == 7)
for k, tail in (
    ("search", "hongguo/search/general"),
    ("series_detail", "hongguo/series/detail"),
    ("video_download_links", "hongguo/video/download-links"),
    ("series_comments", "hongguo/series/comment/list"),
    ("series_comment_replies", "hongguo/series/comment/reply/list"),
    ("video_comments", "hongguo/video/comment/list"),
    ("video_comment_replies", "hongguo/video/comment/reply/list"),
):
    check(f"端点 {k} 正确", s.ENDPOINTS.get(k, "").endswith("/" + tail))
check("余额端点只读且独立", s.BALANCE_PATH.endswith("/points/balance") and
      s.BALANCE_PATH not in s.ENDPOINTS.values())
check("没有 okdata 端点混入", all("okdata" not in p for p in s.ENDPOINTS.values()))
check("没有小影端点混入", all("xiaoying" not in p for p in s.ENDPOINTS.values()))

print("\n== 2. 凭证隔离与不回显 ==")
src = open(os.path.join(ROOT, "justoneapi_collector", "socialdatax_client.py"),
           encoding="utf-8").read()
code = code_only(src)
check("读环境变量 SOCIALDATAX_API_KEY", "SOCIALDATAX_API_KEY" in src)
check("配置段独立 socialdatax", '"socialdatax"' in src)
def literal_strings(src: str) -> list[str]:
    """取所有字符串字面量的值（排除 docstring，负例支付 URL 在常量里）。"""
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


lits = literal_strings(src)
_OKDATA_MARKERS = ("okd_", "okdata_key", "okdata_api_key", "OKDATA_API_KEY",
                   "OKDATA_KEY")
check("不读 okd_ Key",
      not any(m in code for m in _OKDATA_MARKERS) and
      not any(any(m in v for m in _OKDATA_MARKERS) for v in lits))
check("不读小影 project/secret", "TRAFFIC_XIAOYING" not in code and
      not any("TRAFFIC_XIAOYING" in v for v in lits))
check("不读 media_parser Key", "TRAFFIC_MEDIA_KEY" not in code and
      not any("TRAFFIC_MEDIA_KEY" in v for v in lits))
check("Key 不写日志", "print(" not in code and "logger" not in code)
s.configure("sk-test-123")
check("configure 立即生效", s.current_credentials() == "sk-test-123")
check("来源只显示本机配置", s.credential_source() == "本机配置")
s.configure("")

print("\n== 3. 充值与限制（不内置支付） ==")
check("官网链接正确", s.WEBSITE_URL == "https://socialdatax.com/ai")
check("充值链接正确", s.RECHARGE_URL == "https://socialdatax.com/dashboard/billing")
check("充值提示含链接", s.RECHARGE_URL in s.RECHARGE_HINT)
check("权限提示明确只给链接", "软件不收款" in s.CRED_HINT or "不内置支付" in s.CRED_HINT)
check("限制含授权边界", "自有" in s.LIMIT_HINT and "授权" in s.LIMIT_HINT)
check("限制含侵权责任", "侵权" in s.LIMIT_HINT)
check("限制含积分消耗", "积分" in s.LIMIT_HINT)
check("限制含链接时效", "时效" in s.LIMIT_HINT)
check("限制含无周榜声明", "周榜" in s.LIMIT_HINT)
_PAY_HOSTS = ("alipay.com", "wxpay", "stripe.com/pay", "paypal.com", "weixin://pay")
check("源码无支付表单/收款",
      all(k not in code for k in ("支付表单", "二维码")) and
      not any(h in v.lower() for v in lits for h in _PAY_HOSTS))

print("\n== 4. 参数校验与业务错误 ==")
check("搜索空关键词被拦", s.search("")[0] is None)
check("搜索超长关键词被拦", s.search("x" * 201)[0] is None)
check("非法内容类型被拦", s.search("逆袭", ["bad_type"])[0] is None)
check("series_id 与 url 二选一", s.series_detail("123", "http://x")[0] is None)
check("非法 series_id 被拦", s.series_detail("abc")[0] is None)
check("video_id 与 video_url 二选一", s.video_download_links("123", "http://x")[0] is None)
check("series_comments 必须有目标", s.series_comments()[0] is None)
check("video_comments 必须有目标", s.video_comments()[0] is None)
check("HTTP 200 业务错误先查 code",
      'if "code" in body and "message" in body:' in src)
check("401 人话提示", "Key 无效或已失效" in src)
check("403 人话提示", "权限不足" in src)
check("429 人话提示", "请求过于频繁" in src)
check("5xx 人话提示", "服务端暂时不可用" in src)
check("积分消耗上屏", "_points_note" in src and "points" in src)

print("\n== 5. 归一化与下载解密 ==")
norm = s.normalize_series({
    "type": "series", "series_id": "7", "title": "剧名",
    "hot_score": 123, "description": "简介",
})
check("搜索归一化 name 取 title", norm["name"] == "剧名")
check("搜索归一化 heat 取 hot_score", norm["heat"] == "123")
detail = s.normalize_detail({
    "title": "剧名", "series_id": "7",
    "episodes": [{"video_id": "99", "episode_number": 3}],
})
check("详情归一化集号", detail["episodes"][0]["ep"] == 3)
check("详情不缓存播放地址", detail["episodes"][0]["url"] == "")
check("详情带 video_id", detail["episodes"][0]["video_id"] == "99")
streams = [{"definition": "360p"}, {"definition": "1080p"}]
check("清晰度优先 1080p", s.pick_stream(streams)["definition"] == "1080p")
check("空流返回空", s.pick_stream([]) == {})
stream = {"definition": "1080p", "download_url": "https://x/y.mp4",
          "decryption_key": "a" * 32}
cmd = s.ffmpeg_decrypt_command(stream, "e.mp4", "o.mp4")
check("ffmpeg 命令含解密密钥", cmd[cmd.index("-decryption_key") + 1] == "a" * 32)
check("-decryption_key 在 -i 之前", cmd.index("-decryption_key") < cmd.index("-i"))
check("保留音轨映射", "0:a:0?" in cmd)
try:
    s.ffmpeg_decrypt_command({**stream, "decryption_key": "BAD"}, "e.mp4", "o.mp4")
    check("非法密钥被拒", False, "未抛异常")
except ValueError:
    check("非法密钥被拒", True)
check("未授权下载被拒", s.download_episode(stream, "o.mp4", authorized=False)[0] is False)
check("下载前即时换址（源码证据）", "video_download_links(" in src)

print("\n== 6. 页面接线 AST ==")
page_path = os.path.join(ROOT, "gui_qt", "pages", "drama_parse_page.py")
page = open(page_path, encoding="utf-8").read()
tree = ast.parse(page)
check("导入 socialdatax_client", "socialdatax_client" in page)
check("三通道常量齐全", all(x in page for x in ('_CH_SD', '_CH_XY', '_CH_PUBLIC')))
check("设置通道入口", "_edit_sd_credentials" in page)
check("查询余额入口", "_query_balance" in page)
check("充值说明入口", "_show_recharge" in page)
check("限制文案上屏", "LIMIT_HINT" in page)
check("okdata 不冒充短剧通道", "okdata" not in code_only(page).lower())

# 裸 connect / on_error=self._status.set（注释里不算）
bare = []
status_set = []
for node in ast.walk(tree):
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        continue
    if node.func.attr == "connect" and isinstance(node.func.value, ast.Attribute):
        if node.func.value.attr in ("clicked", "returnPressed"):
            bare.append(node.lineno)
    for kw in node.keywords:
        if kw.arg == "on_error":
            v = kw.value
            if (isinstance(v, ast.Attribute) and isinstance(v.value, ast.Attribute)
                    and v.value.attr == "_status" and v.attr == "set"):
                status_set.append(node.lineno)
check("无裸 clicked.connect", not bare, str(bare))
check("无 on_error=self._status.set", not status_set, str(status_set))

worker_calls = [n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "Worker"]
starts = code_only(page).count("w . start ( )") + code_only(page).count("w.start()")
check("每个 Worker 都 start", len(worker_calls) == starts,
      f"Worker={len(worker_calls)} start={starts}")
check("通道优先级 SD→XY→公开",
      page.index("_CH_SD") < page.index("_CH_XY") < page.index("_CH_PUBLIC"))

print(f"\n== 结果：{PASS} 通过 / {FAIL} 失败 ==")
sys.exit(1 if FAIL else 0)
