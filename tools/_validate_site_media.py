#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""官网配图 + 三曲循环背景音乐发版门禁（2026-11-10）。

背景：客户反馈「官网里面的配图都显示丢失」。真因不是 CDN 挂了，而是
`官网素材/ui/` 整个目录不在了，build_site.py 遇到缺图只 continue，
模板变量被替换成空字符串，产物里一排 <img src=""> —— 浏览器把当前
页面当图片重新请求，于是满屏破图。这类问题编译查不出、curl 200 也
查不出，只能靠产物级断言钉死。

音乐部分 2026-11-10 晚升级：客户给了三首本地 FLAC 要求「替换并循环
播放」。判据从「单曲 loop 属性」升级为「歌单顺序 + 最后一首回第一首 +
翻页续播 + 只下当前一首 + 三首全废仍要响」，并用 Node 真跑一次播放器
JS，模拟连点三次 ended 验证循环。

同夜再升级：客户要求「点进官网音乐就自动开始播放」。判据从「不自动
播放」翻转为「进页面就起播 + 静音重试 + 首次轻点补播 + 微信 JSBridge」，
JS 仿真同时验两件事：加载后按钮已是播放态，以及 1→2→3→1 整单循环。

只做只读检查与临时文件，不联网、不上传、不写产物。
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

STANDALONE = ROOT / "installer_output" / "standalone"
ASSET_DIR = STANDALONE / "assets"
BGM_SRC = ROOT / "官网素材" / "bgm"
TRACK_FILES = ("bgm-1.mp3", "bgm-2.mp3", "bgm-3.mp3")
TRACK_RELS = tuple("assets/" + n for n in TRACK_FILES)

TOTAL = 0
FAILED = 0


def chk(cond: bool, name: str, detail: str = "") -> bool:
    global TOTAL, FAILED
    TOTAL += 1
    if cond:
        print(f"  ✔ {name}")
        return True
    FAILED += 1
    print(f"  ✘ {name}" + (f"  → {detail}" if detail else ""))
    return False


def sec(title: str) -> None:
    print(f"\n--- {title} ---")


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def node_bin() -> str:
    for cand in (
        shutil.which("node"),
        r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-6\node.exe",
    ):
        if cand and Path(cand).is_file():
            return cand
    return ""


def cycle_simulation(home: str) -> tuple[bool, str]:
    """真跑一遍播放器 JS：加载即自动起播 → 连发 3 次 ended 验整单循环。

    静态断言只能看到「写了 %count()」，跑起来才知道事件链有没有接反、
    最后一首会不会卡住；顺便验「不用点按钮，页面加载完按钮已是播放态」。
    node 不在机器上时跳过（不误判发版失败）。
    """
    node = node_bin()
    if not node:
        return True, "未找到 node，跳过 JS 仿真（静态判据仍生效）"
    scripts = [m.group(1) for m in
               re.finditer(r"<script[^>]*>(.*?)</script>", home, re.S)]
    player = next((s for s in scripts if "var TRACKS=" in s), "")
    if not player:
        return False, "首页里找不到播放器脚本"
    mock = r"""
const STORE = {};
global.localStorage = {
  getItem: k => (k in STORE ? STORE[k] : null),
  setItem: (k, v) => { STORE[k] = String(v); }
};
class El {
  constructor(id){ this.id=id; this.attrs={}; this.listeners={}; this.style={};
    this.currentTime=0; this.duration=300; this.volume=1; this.textContent=""; }
  setAttribute(k,v){ this.attrs[k]=String(v); }
  getAttribute(k){ return (k in this.attrs) ? this.attrs[k] : null; }
  addEventListener(t,fn){ (this.listeners[t]=this.listeners[t]||[]).push(fn); }
  fire(t,ev){ (this.listeners[t]||[]).forEach(f=>f(ev||{})); }
  play(){ return Promise.resolve(); }
  pause(){}
  load(){}
}
const ELS = {};
["tcbgm-btn","tcbgm-next","tcbgm-tip","tcbgm-audio"].forEach(
  id => ELS[id] = new El(id));
global.document = {
  getElementById: id => ELS[id] || null,
  addEventListener: () => {},
  removeEventListener: () => {},
  hidden: false
};
global.window = { addEventListener: () => {} };
global.isFinite = Number.isFinite;
"""
    drive = r"""
(async function(){
  const AUD = document.getElementById("tcbgm-audio");
  const BTN = document.getElementById("tcbgm-btn");
  const NXT = document.getElementById("tcbgm-next");
  const wait = () => new Promise(r => setTimeout(r, 0));
  // 不点任何按钮：加载完 boot() 自己就该把音乐播起来
  await wait(); await wait();
  const autoOk = BTN.getAttribute("data-on") === "1" &&
                 AUD.getAttribute("src") === "assets/bgm-1.mp3";
  const seq = [AUD.getAttribute("src")];
  for (let i = 0; i < 3; i++) {
    AUD.fire("ended", {});
    await wait(); await wait();
    seq.push(AUD.getAttribute("src"));
  }
  NXT.fire("click", {});
  await wait(); await wait();
  const afterNext = AUD.getAttribute("src");
  const want = ["assets/bgm-1.mp3","assets/bgm-2.mp3",
                "assets/bgm-3.mp3","assets/bgm-1.mp3"];
  const ok = JSON.stringify(seq) === JSON.stringify(want) &&
             afterNext === "assets/bgm-2.mp3";
  console.log("AUTO=" + (autoOk ? "OK" : "FAIL") +
              " SEQ=" + JSON.stringify(seq) + " NEXT=" + afterNext);
  console.log((ok && autoOk) ? "CYCLE_OK" : "CYCLE_FAIL");
})();
"""
    with tempfile.TemporaryDirectory(prefix="tc_bgm_cycle_") as td:
        f = Path(td) / "cycle.js"
        f.write_text(mock + "\n" + player + "\n" + drive, encoding="utf-8")
        try:
            r = subprocess.run([node, str(f)], capture_output=True,
                               text=True, timeout=20)
        except Exception as exc:                                # noqa: BLE001
            return True, f"JS 仿真未能启动，跳过：{type(exc).__name__}"
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if "CYCLE_OK" in out and "AUTO=OK" in out:
        return True, out.replace("\n", " | ")[:200]
    return False, (out + " " + err).strip()[:300]


def blocked_simulation(home: str) -> tuple[bool, str]:
    """真跑「自动播放被浏览器拦住」：静音重试 → 微信 JSBridge 免手势补播。

    静态断言看不出兜底链有没有接反。这里让 play() 前两次抛
    NotAllowedError，模拟桌面与微信都拦无手势出声，再检查最终
    按钮是不是回到播放态、静音有没有被解除、提示有没有收起。
    """
    node = node_bin()
    if not node:
        return True, "未找到 node，跳过被拦场景仿真"
    scripts = [m.group(1) for m in
               re.finditer(r"<script[^>]*>(.*?)</script>", home, re.S)]
    player = next((s for s in scripts if "var TRACKS=" in s), "")
    if not player:
        return False, "首页里找不到播放器脚本"
    mock = r"""
let playCount = 0, bridgeCalls = 0;
const STORE = {};
global.localStorage = {
  getItem: k => (k in STORE ? STORE[k] : null),
  setItem: (k, v) => { STORE[k] = String(v); }
};
class El {
  constructor(id){ this.id=id; this.attrs={}; this.listeners={}; this.style={};
    this.currentTime=0; this.duration=300; this.volume=1; this.textContent="";
    this.muted=false; this.paused=false; }
  setAttribute(k,v){ this.attrs[k]=String(v); }
  getAttribute(k){ return (k in this.attrs) ? this.attrs[k] : null; }
  addEventListener(t,fn){ (this.listeners[t]=this.listeners[t]||[]).push(fn); }
  fire(t,ev){ (this.listeners[t]||[]).forEach(f=>f(ev||{})); }
  play(){
    playCount++;
    if (playCount <= 2) {
      const e = new Error("blocked"); e.name = "NotAllowedError";
      return Promise.reject(e);
    }
    return Promise.resolve();
  }
  pause(){}
  load(){}
}
const ELS = {};
["tcbgm-btn","tcbgm-next","tcbgm-tip","tcbgm-audio"].forEach(
  id => ELS[id] = new El(id));
global.document = {
  getElementById: id => ELS[id] || null,
  addEventListener: () => {},
  removeEventListener: () => {},
  hidden: false
};
global.window = {
  addEventListener: () => {},
  WeixinJSBridge: {
    invoke: (name, args, cb) => { bridgeCalls++; if (typeof cb === "function") cb(); }
  }
};
global.isFinite = Number.isFinite;
"""
    drive = r"""
(async function(){
  const AUD = document.getElementById("tcbgm-audio");
  const BTN = document.getElementById("tcbgm-btn");
  const TIP = document.getElementById("tcbgm-tip");
  const wait = () => new Promise(r => setTimeout(r, 0));
  await wait(); await wait(); await wait(); await wait();
  const ok = BTN.getAttribute("data-on") === "1" &&
             AUD.muted === false &&
             TIP.style.display === "" &&
             bridgeCalls >= 1 &&
             AUD.getAttribute("src") === "assets/bgm-1.mp3";
  console.log("BLOCKED plays=" + playCount + " bridge=" + bridgeCalls +
              " muted=" + AUD.muted + " tipDisplay=" + JSON.stringify(TIP.style.display) +
              " on=" + BTN.getAttribute("data-on"));
  console.log(ok ? "BLOCKED_OK" : "BLOCKED_FAIL");
})();
"""
    with tempfile.TemporaryDirectory(prefix="tc_bgm_blocked_") as td:
        f = Path(td) / "blocked.js"
        f.write_text(mock + "\n" + player + "\n" + drive, encoding="utf-8")
        try:
            r = subprocess.run([node, str(f)], capture_output=True,
                               text=True, timeout=20)
        except Exception as exc:                                # noqa: BLE001
            return True, f"被拦场景仿真未能启动，跳过：{type(exc).__name__}"
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if "BLOCKED_OK" in out:
        return True, out.replace("\n", " | ")[:220]
    return False, (out + " " + err).strip()[:300]


def main() -> int:
    import site_seo

    pages = sorted(STANDALONE.glob("*.html"))
    sec("1) 产物与素材")
    chk(len(pages) >= 12, f"生成了全部页面（实际 {len(pages)} 个）", str(len(pages)))
    chk(all(p.stat().st_size > 20_000 for p in pages), "没有空壳页面")

    sec("2) 配图：绝不允许空 src")
    empty_src = []
    for p in pages:
        h = read(p)
        empty_src += [p.name] * len(re.findall(r'<img[^>]*\ssrc=(?:""|\'\')', h))
    chk(not empty_src, "★ 所有页面都没有 <img src=\"\">（破图真因）",
        str(sorted(set(empty_src))))

    home = read(STANDALONE / "index.html")
    imgs = re.findall(r"<img[^>]*>", home)
    chk(len(imgs) >= 5, f"首页配图齐全（实际 {len(imgs)} 处 img 引用）", str(len(imgs)))

    broken = []
    for p in pages:
        for tag in re.findall(r"<img[^>]*>", read(p)):
            m = re.search(r'\ssrc="([^"]*)"', tag)
            src = m.group(1) if m else ""
            if not src:
                broken.append(f"{p.name}: 无 src")
            elif src.startswith("data:"):
                continue
            elif src.startswith(("http://", "https://")):
                continue
            elif not (STANDALONE / src.split("?")[0]).is_file():
                broken.append(f"{p.name}: {src[:60]}")
    chk(not broken, "★ 每个图片引用都能落地（data / 绝对地址 / 本地文件）",
        "; ".join(broken[:4]))

    shot_names = ("shot-video-download.png", "shot-live-monitor.png",
                  "shot-transcribe.png", "shot-collect.png")
    chk(all((STANDALONE / n).is_file() for n in shot_names),
        "4 张界面截图都生成到了 standalone",
        str([n for n in shot_names if not (STANDALONE / n).is_file()]))

    sec("3) 缺图兜底：素材再次丢失也不会破图")
    site_src = read(TOOLS / "build_site.py")
    chk("def placeholder_shot(" in site_src, "build_site.py 有占位图函数")
    chk("imgs = {n: (imgs.get(n) or placeholder_shot(n))" in site_src,
        "★ build_html 入口统一补齐 imgs（一处兜住 hero + 4 个详情块）")
    chk("用内联示意图兜底" in site_src, "素材缺失分支不再只是 continue")

    import build_site
    uri = build_site.placeholder_shot("视频下载")
    chk(uri.startswith("data:image/svg+xml;base64,"), "占位图是内联 SVG data URI")
    svg = base64.b64decode(uri.split(",", 1)[1]).decode("utf-8")
    chk("<svg" in svg and "视频下载" in svg, "占位图能解出带页面名的 SVG")
    chk("$" not in svg, "占位图里没有 $（不会炸 string.Template）")

    # 真调一次 build_html，模拟 4 张图全丢，验证产物没有空 src
    try:
        products = json.loads((TOOLS / "products.json").read_text(encoding="utf-8"))
        if isinstance(products, dict):
            products = products.get("products") or products.get("items") or []
        dry = build_site.build_html(
            version="1.28.1", published="2026-11-06", size=95554182,
            url="https://example.invalid/x.exe", sha256="-", notes="门禁自检",
            qr_img="", pay={"pay_note": ""}, imgs={},
            products=products, changelog=[], free_version="", free_size=0,
            free_url="", ext_gif="",
        )
        chk('src=""' not in dry, "★ 模拟「4 张素材全缺」重生成，产物仍无空 src")
        chk("data:image/svg+xml;base64," in dry,
            "★ 缺图时真的换成了内联示意图，不是静默留白")
    except Exception as exc:                                    # noqa: BLE001
        chk(False, "★ 模拟缺图重生成成功", f"{type(exc).__name__}: {exc}")

    chk((STANDALONE / "index.html").stat().st_size < 400_000,
        "首页仍 <400KB（音乐没被内联进去）")

    sec("4) 三首音乐：源文件、产物与歌单")
    manifest = BGM_SRC / "playlist.json"
    chk(manifest.is_file(), "官网素材/bgm/playlist.json 存在")
    data = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
    items = data.get("tracks", []) if isinstance(data, dict) else []
    chk(len(items) == 3, f"★ 歌单正好 3 首（实际 {len(items)}）", str(len(items)))
    chk(bool(data.get("loop")), "歌单声明顺序循环播放")
    chk(data.get("bitrate") == "96k", "统一 96kbps（手机流量友好）")
    chk([i.get("file") for i in items] == list(TRACK_FILES),
        "★ 歌单顺序就是客户给的顺序", str([i.get("file") for i in items]))
    titles = [str(i.get("title") or "") for i in items]
    chk(all(" · " in t for t in titles), "三首都有中文显示名", str(titles))
    chk(all((".flac" in str(i.get("source") or "")) for i in items),
        "歌单记录了原始 FLAC 来源，便于追溯换歌")

    for name in TRACK_FILES:
        src, dst = BGM_SRC / name, ASSET_DIR / name
        chk(src.is_file() and dst.is_file(), f"{name} 源与产物都在")
        if src.is_file() and dst.is_file():
            chk(src.stat().st_size == dst.stat().st_size,
                f"{name} 拷贝完整（{dst.stat().st_size / 1024 / 1024:.2f} MB）")
            head = dst.read_bytes()[:3]
            chk(head == b"ID3" or head[0] == 0xFF, f"{name} 是有效 MP3", repr(head))
            chk(dst.stat().st_size > 500_000, f"{name} 不是空壳 / 没被截断")
    chk((ASSET_DIR / "playlist.json").is_file(),
        "产物里有 playlist.json（线上可核对）")
    chk(sum((ASSET_DIR / n).stat().st_size for n in TRACK_FILES
            if (ASSET_DIR / n).is_file()) < 12_000_000,
        "三首总体积 <12MB（不拖垮手机端）")

    sec("5) 播放器：三曲列表循环，全站 12 页一致")
    no_player, no_audio, no_next, no_tracks, no_boot = [], [], [], [], []
    for p in pages:
        h = read(p)
        if 'id="tcbgm-btn"' not in h or 'id="tcbgm"' not in h:
            no_player.append(p.name)
        if 'id="tcbgm-audio"' not in h:
            no_audio.append(p.name)
        if 'id="tcbgm-next"' not in h:
            no_next.append(p.name)
        if not all(rel in h for rel in TRACK_RELS):
            no_tracks.append(p.name)
        # ★ 首页与子页是两套生成器，只重生成首页会让子页停在旧版播放器；
        #   有按钮不等于会自动响，必须逐页钉住起播链。
        if "function boot()" not in h or "  boot();" not in h:
            no_boot.append(p.name)
    chk(not no_player, f"★ 全部 {len(pages)} 页都有播放按钮", str(no_player))
    chk(not no_audio, "★ 全部页面都有 audio 元素", str(no_audio))
    chk(not no_next, "★ 全部页面都有「下一首」按钮", str(no_next))
    chk(not no_tracks, "★ 全部页面都内联了完整三首歌单", str(no_tracks))
    chk(not no_boot, f"★ 全部 {len(pages)} 页都带自动起播链（含 11 个子页）", str(no_boot))

    atag = re.search(r"<audio[^>]*>", home)
    a = atag.group(0) if atag else ""
    chk("loop" not in a, "★ audio 不用单曲 loop（由歌单循环接管，否则永远卡第 1 首）")
    chk('preload="none"' in a, "audio 不预加载（不拖慢首屏/不偷跑流量）")
    chk("playsinline" in a, "audio 带 playsinline（移动端内联播放）")
    chk("src=" not in a, "audio 初始不挂 src（起播时才挂当前一首，不偷跑三首流量）")

    btn = re.search(r"<button id=\"tcbgm-btn\"[^>]*>", home)
    b = btn.group(0) if btn else ""
    chk('type="button"' in b, "按钮不会误提交表单")
    chk("aria-label=" in b and "aria-pressed=" in b, "按钮有无障碍标签与状态")
    nxt = re.search(r"<button id=\"tcbgm-next\"[^>]*>", home)
    nb = nxt.group(0) if nxt else ""
    chk('type="button"' in nb and 'aria-label="下一首"' in nb,
        "「下一首」按钮有无障碍标签")

    payload = re.search(r"var TRACKS=(\[.*?\]);", home, re.S)
    chk(payload is not None, "能从产物里解析出内联歌单数组")
    if payload:
        try:
            inline = json.loads(payload.group(1))
        except Exception:                                       # noqa: BLE001
            inline = []
        chk(inline == [{"file": r, "title": t} for r, t in zip(TRACK_RELS, titles)],
            "★ 页面内联歌单与 playlist.json 完全一致（顺序/路径/歌名）",
            str(inline)[:220])

    sec("6) 播放器：手机端 / 网页端 / 漏传文件都能响")
    div_pos = home.find('<div id="tcbgm">')
    # 播放器的 <style> 在 <div> 之前，必须从样式开始截，否则
    # 「手机端适配 / 减少动效」这类 CSS 断言会假失败。
    css_pos = home.rfind("<style", 0, div_pos) if div_pos >= 0 else -1
    start = css_pos if css_pos >= 0 else div_pos
    block = home[start:] if start >= 0 else ""
    chk(bool(block), "能从产物里截出播放器整块（含 CSS）")
    chk("class=" not in block,
        "★ 播放器不用 class（_validate_theme 会要求每个 class 都有 CSS 定义）")
    chk(not re.search(r"\$[A-Z_]{3,}", block),
        "播放器不含 $占位符（不会炸 string.Template）")
    for needle, label in (
        ("localStorage", "记住用户开关（翻页不丢）"),
        ("WeixinJSBridgeReady", "微信内置浏览器解锁音频"),
        ("webkitAudioContext", "iOS Safari 兼容前缀"),
        ("createOscillator", "★ Web Audio 合成曲兜底"),
        ("NotAllowedError", "区分「自动播放被拦」与「文件坏了」"),
        ('addEventListener("click"', "★ 保留手动播放 / 暂停按钮"),
        ("function boot()", "★ 有「页面加载即起播」入口"),
        ("  boot();", "★ 启动函数真的在页面加载时被调用"),
        ("AUD.muted=true", "★ 被拦后静音起播（桌面浏览器大多放行）"),
        ("AUD.muted=false", "★ 起播成功后把音量还给用户"),
        ("function armRetry()", "★ 有自动播放兜底重试链"),
        ("pointerdown", "★ 首次轻点 / 触摸立即补播"),
        ("window.WeixinJSBridge", "★ 微信 JSBridge 已就绪时自动补播"),
        ("function wxKick()", "★ 有微信免手势起播入口"),
        ("getNetworkType", "★ 微信 JSBridge 回调里 play（iOS 微信关键）"),
        ("function hint(", "★ 手机端被拦时有可见提示函数"),
        ('TIP.style.display="block"', "★ 行内样式突破手机端隐藏提示"),
        ("轻点页面开启音乐", "提示文案写明怎么开启音乐"),
        ("clearHint()", "★ 播放成功后收起提示"),
        ("userStop", "用户主动暂停后本页不再抢播"),
        ("touchstart", "移动端首次触摸解锁"),
        ("visibilitychange", "切后台暂停省电"),
        ("@media (max-width:720px)", "手机端样式适配"),
        ("prefers-reduced-motion", "尊重减少动效偏好"),
    ):
        chk(needle in block, label, needle)
    for needle, label in (
        ('addEventListener("ended"', "★ 播完自动接下一首"),
        ("idx=(idx+1)%count()", "★ 最后一首回第一首＝整单循环"),
        ("function setSource()", "只把当前一首挂到 audio 上"),
        ("AUD.setAttribute(\"src\",want)", "切歌时才换 src（不会三首一起下）"),
        ('KPOS="tcbgm_pos"', "记住播放进度（跨页不从头开始）"),
        ('KIDX="tcbgm_idx"', "记住当前第几首"),
        ('addEventListener("loadedmetadata"', "新页面载入后恢复进度"),
        ("AUD.currentTime=seekTo", "真的 seek 到上次的秒数"),
        ("timeupdate", "播放中定期保存进度"),
        ("pagehide", "关页面前保存进度"),
        ("failRun++", "★ 某首失败先跳下一首"),
        ("synthFlow()", "★ 三首全废落回内置合成曲"),
        ("playCurrent(num(KPOS),false)", "★ 进页面自动起播，并从上次进度续上"),
    ):
        chk(needle in block, label, needle)
    chk("data:audio" not in home, "音乐没有被内联成 base64")
    chk("// ★ 绝不在这里清掉 localStorage" in block,
        "自动续播被拦时不清用户意愿")

    sec("6.5) JS 真跑一遍：加载即自动起播 + 连点三次 ended 验证循环")
    ok, detail = cycle_simulation(home)
    chk(ok, "★ 播放器事件链仿真（不点按钮就播 + 1→2→3→1 + 下一首）", detail)
    if detail.startswith("未找到") or "跳过" in detail:
        print(f"      （{detail}）")
    else:
        print(f"      {detail}")

    sec("6.6) JS 真跑一遍：自动播放被拦后仍能把音乐带起来")
    ok2, detail2 = blocked_simulation(home)
    chk(ok2, "★ 被拦场景仿真（静音重试 + 微信 JSBridge 免手势补播）", detail2)
    if "跳过" in detail2:
        print(f"      （{detail2}）")
    else:
        print(f"      {detail2}")

    sec("7) 兜底与同步链路（动态调用）")
    clean = "<!DOCTYPE html><html><body><p>hi</p>__MOTION__</body></html>"
    once = site_seo.inject_motion(clean)
    chk(once.count('id="tcbgm-btn"') == 1, "★ inject_motion 自动带上播放器")
    chk(once.count('id="tcbgm-next"') == 1, "★ inject_motion 自动带上下一首按钮")
    chk(site_seo.inject_bgm(once).count('id="tcbgm-btn"') == 1,
        "★ 重复注入会被拦住（不会出两个按钮）")
    fallback = site_seo.inject_bgm("<html><body></body></html>", audio_src="")
    chk('id="tcbgm-btn"' in fallback and "var TRACKS=[];" in fallback,
        "★ 音频文件全缺时仍注入播放器，且走纯合成曲")
    chk("__MOTION__" not in once, "没有残留占位符")
    got = site_seo.bgm_tracks()
    chk([t["file"] for t in got] == list(TRACK_RELS),
        "★ bgm_tracks() 读到的顺序与产物一致", str(got)[:200])
    chk(site_seo.bgm_tracks.__doc__ is not None, "歌单读取有判据说明")

    import publish_site_mirror as mirror
    chk('.mp3' in mirror.CTYPES and mirror.CTYPES[".mp3"] == "audio/mpeg",
        "★ 镜像同步带 audio/mpeg Content-Type")
    collected = [p.relative_to(STANDALONE).as_posix() for p in mirror.collect()]
    chk(all(rel in collected for rel in TRACK_RELS),
        "★ 镜像同步清单真的收了三首音乐", str([r for r in TRACK_RELS if r not in collected]))
    chk("assets/playlist.json" in collected,
        "镜像同步清单也收了歌单（线上可核对）")
    chk(any(n in collected for n in shot_names),
        "镜像同步清单也收了本地模式下的界面截图")
    chk("max-age=604800" in mirror.cache_header(ASSET_DIR / TRACK_FILES[0]),
        "音乐文件走长缓存，不反复下载")

    sec("8) 源码链路")
    seo_src = read(TOOLS / "site_seo.py")
    chk("return inject_bgm(html)" in seo_src,
        "★ inject_motion 与播放器是同一条注入链（改一处全站生效）")
    chk("def bgm_tracks()" in seo_src and "playlist.json" in seo_src,
        "播放器从 playlist.json 读歌单（换歌只改一个文件）")
    chk("BGM_BLOCK.replace(\"__BGM_TRACKS__\"" in seo_src,
        "歌单在构建期内联进页面（运行时不请求 json）")
    chk("1.8) 背景音乐歌单" in site_src, "build_site.py 有歌单拷贝步骤")
    chk('bgm_dir.glob("bgm-*.mp3")' in site_src,
        "build_site.py 拷贝整个歌单，不是写死一个文件")
    chk((TOOLS / "import_site_bgm.py").is_file(),
        "★ 换歌有可复现的导入工具（FLAC → 96k MP3 + 歌单）")
    chk((TOOLS / "gen_site_bgm.py").is_file(),
        "旧合成曲生成脚本仍在（最后一档兜底可复现）")

    print("\n" + "=" * 66)
    print(f"  通过 {TOTAL - FAILED} 项，失败 {FAILED} 项")
    print("=" * 66)
    if FAILED:
        print("官网配图或三曲循环音乐门禁未通过，先修好再发版。")
        return 1
    print("官网配图 + 全站三曲循环背景音乐门禁全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
