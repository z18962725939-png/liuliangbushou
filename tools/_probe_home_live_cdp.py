# -*- coding: utf-8 -*-
"""临时评估脚本：用项目现成 CDP 通道探测抖音主页能否免费读到开播状态。

只做只读探测，不写业务配置、不改代码。用完可删。
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from justoneapi_collector.douyin_cdp_crawler import DouyinCDPCrawler  # noqa: E402
from justoneapi_collector.douyin_page_resolver import _JS_STEALTH  # noqa: E402

SEC = "MS4wLjABAAAAi7XpOpFncGmD6WPBbdB2lAR9R3rI51OX30NzzDS6JOorE4fbzTA3AwuQeORg1tEp"
APPDATA = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
BASE = APPDATA / "TrafficCatcher" / "browser_profile"
cfg = json.loads((APPDATA / "TrafficCatcher" / "config.json").read_text(encoding="utf-8"))
cookie = (cfg.get("douyin_cookie") or "").strip()

crawler = DouyinCDPCrawler(BASE / "_resolve", headless=True, timeout=25.0)
try:
    ok, msg = crawler.start()
    print("启动:", ok, msg[:160])
    if not ok:
        sys.exit(0)
    crawler.add_init_script(_JS_STEALTH)
    n = crawler.add_cookies(cookie) if cookie else 0
    print("Cookie 注入:", n)
    crawler.open_url("https://www.douyin.com/")
    time.sleep(2)
    try:
        crawler._page.call("Network.enable", {}, timeout=8)
    except Exception as exc:                      # noqa: BLE001
        print("Network.enable 失败", exc)
    url = "https://www.douyin.com/user/" + SEC
    nav_ok = crawler._ensure_origin(url)
    print("导航:", nav_ok, url[:80])
    time.sleep(4)
    expr = """JSON.stringify({
      title: document.title,
      href: location.href,
      text: (document.body.innerText||'').slice(0,500),
      liveLinks: [...document.querySelectorAll('a')].map(a=>a.href)
                  .filter(h=>/live\\.douyin\\.com/.test(h)).slice(0,8),
      liveWords: (document.body.innerText||'').match(/.{0,12}直播.{0,20}/g)||[],
      router: !!window._ROUTER_DATA,
      keys: Object.keys(window).filter(k=>/router|INITIAL|SSR|DATA/i.test(k)).slice(0,15)
    })"""
    res = crawler._page.call("Runtime.evaluate", {
        "expression": expr, "returnByValue": True,
    }, timeout=10)
    val = (res.get("result") or {}).get("value")
    info = json.loads(val) if val else {}
    print("标题:", info.get("title"))
    print("最终URL:", info.get("href"))
    print("页面文字前200:", (info.get("text") or "")[:200].replace("\n", " | "))
    print("直播链接:", info.get("liveLinks"))
    print("直播字样:", (info.get("liveWords") or [])[:6])
    print("全局键:", info.get("keys"))
    urls = []
    for ev in crawler._page._events:
        if ev.get("method") == "Network.responseReceived":
            u = (ev.get("params") or {}).get("response", {}).get("url", "")
            if "douyin.com" in u and any(
                    k in u for k in ("profile", "live", "user", "room")):
                urls.append(u.split("?")[0])
    print("相关接口:")
    for u in dict.fromkeys(urls):
        print("  -", u[:150])

    # 页面全局数据里找 live/room 字段（SSR_RENDER_DATA / EXPOSE_DATA）
    gexpr = """(()=>{
      const out={};
      const walk=(o,p,d)=>{ if(d>5||!o||typeof o!=='object')return;
        for(const k of Object.keys(o)){ const v=o[k],q=p?p+'.'+k:k;
          if(/live|room|anchor/i.test(k)) out[q]=String(v).slice(0,120);
          walk(v,q,d+1);} };
      for(const n of ['SSR_RENDER_DATA','EXPOSE_DATA','_ROUTER_DATA'])
        if(window[n]) walk(window[n],n,0);
      return JSON.stringify(out);
    })()"""
    gres = crawler._page.call("Runtime.evaluate", {
        "expression": gexpr, "returnByValue": True}, timeout=10)
    gval = (gres.get("result") or {}).get("value") or "{}"
    gdata = json.loads(gval)
    print("页面全局 live/room 字段:", len(gdata))
    for k, v in list(gdata.items())[:25]:
        print("   ", k, "=", v)

    # 命门：拆 /user/profile/other/ 的响应体，看有没有开播状态字段
    def walk(obj, path=""):
        out = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                p = f"{path}.{k}" if path else k
                if any(t in k.lower() for t in ("live", "room", "anchor")):
                    out.append((p, str(v)[:120]))
                out.extend(walk(v, p))
        elif isinstance(obj, list):
            for i, v in enumerate(obj[:3]):
                out.extend(walk(v, f"{path}[{i}]"))
        return out

    hits = []
    for ev in crawler._page._events:
        if ev.get("method") != "Network.responseReceived":
            continue
        resp = (ev.get("params") or {}).get("response", {})
        if "/aweme/v1/web/user/profile/other/" not in resp.get("url", ""):
            continue
        rid = (ev.get("params") or {}).get("requestId")
        print("profile/other 完整URL:", resp.get("url", "")[:400])
        try:
            body = crawler._page.call("Network.getResponseBody", {
                "requestId": rid}, timeout=8)
            raw = (body.get("result") or {}).get("body") or ""
            print("body原始长度:", len(raw), "前缀:", repr(raw[:120]))
            data = json.loads(raw)
            hits = walk(data)
            print("profile/other 命中字段数:", len(hits))
            for p, v in hits[:30]:
                print("   ", p, "=", v)
        except Exception as exc:                  # noqa: BLE001
            print("getResponseBody 失败:", str(exc)[:120])
        break
    if not hits:
        print("profile/other 事件体已过期，改在页面内直接 fetch")
    api_url = ""
    for ev in crawler._page._events:
        if ev.get("method") == "Network.responseReceived":
            u = (ev.get("params") or {}).get("response", {}).get("url", "")
            if "/aweme/v1/web/user/profile/other/" in u:
                api_url = u
                break
    if api_url:
        fetch_expr = """new Promise(resolve=>{
          fetch(%s,{credentials:'include'}).then(r=>r.text()).then(t=>{
            try{const o=JSON.parse(t);const out={};
              const walk=(x,p,d)=>{if(d>6||!x||typeof x!=='object')return;
                for(const k of Object.keys(x)){const v=x[k],q=p?p+'.'+k:k;
                  if(/live|room|anchor/i.test(k))out[q]=String(v).slice(0,100);
                  walk(v,q,d+1);}};
              walk(o,'',0);
              resolve(JSON.stringify({len:t.length,status:'ok',hits:out}));
            }catch(e){resolve(JSON.stringify({len:t.length,status:'bad',head:t.slice(0,160)}));}
          }).catch(e=>resolve(JSON.stringify({status:'err',msg:String(e)})));
        })""" % json.dumps(api_url)
        fres = crawler._page.call("Runtime.evaluate", {
            "expression": fetch_expr, "awaitPromise": True,
            "returnByValue": True}, timeout=20)
        fval = (fres.get("result") or {}).get("value") or "{}"
        fdata = json.loads(fval)
        print("页面内 fetch:", fdata.get("status"), "长度", fdata.get("len"),
              fdata.get("msg", "")[:100], fdata.get("head", "")[:100])
        fhits = fdata.get("hits") or {}
        print("页面内 fetch 命中 live/room 字段:", len(fhits))
        for k, v in list(fhits.items())[:30]:
            print("   ", k, "=", v)

    # 找一个正在播的房间，验证在播时字段会翻转（live_status=1 / room_id!=0）
    crawler.open_url("https://live.douyin.com/")
    time.sleep(5)
    le = crawler._page.call("Runtime.evaluate", {
        "expression": """JSON.stringify({
          title: document.title,
          rooms: [...new Set([...document.querySelectorAll('a')]
            .map(a=>a.href).filter(h=>/live\\.douyin\\.com\\/\\d{6,}/.test(h)))]
            .slice(0,6)
        })""", "returnByValue": True}, timeout=10)
    lv = (le.get("result") or {}).get("value") or "{}"
    linfo = json.loads(lv)
    print("直播广场:", linfo.get("title"), "房间数", len(linfo.get("rooms") or []))
    rooms = linfo.get("rooms") or []
    import re
    from justoneapi_collector.douyin_live_stream import DouyinLiveStreamResolver
    resolver = DouyinLiveStreamResolver(timeout=12)
    for r in rooms[:3]:
        mrid = re.search(r"live\.douyin\.com/(\d+)", r)
        if not mrid or not api_url:
            continue
        try:
            info = resolver.resolve_by_web_rid(mrid.group(1))
        except Exception as exc:                  # noqa: BLE001
            print("  解析房间失败", mrid.group(1), str(exc)[:80])
            continue
        if not info.sec_uid:
            print("  房间", mrid.group(1), "无 sec_uid")
            continue
        target = re.sub(r"sec_user_id=[^&]+",
                        "sec_user_id=" + info.sec_uid, api_url)
        vexpr = """new Promise(resolve=>{
          fetch(%s,{credentials:'include'}).then(r=>r.text()).then(t=>{
            try{const o=JSON.parse(t),u=o.user||{};
              resolve(JSON.stringify({name:u.nickname,live:u.live_status,
                room:u.room_id_str,sec:u.sec_uid}));}
            catch(e){resolve(JSON.stringify({bad:t.slice(0,80)}));}
          }).catch(e=>resolve(JSON.stringify({err:String(e)})));
        })""" % json.dumps(target)
        vres = crawler._page.call("Runtime.evaluate", {
            "expression": vexpr, "awaitPromise": True,
            "returnByValue": True}, timeout=20)
        vval = (vres.get("result") or {}).get("value") or "{}"
        print("  ★ 在播验证:", info.anchor_name[:16], "→", vval[:200])
finally:
    try:
        crawler.stop(kill_browser=False)
    except Exception:                             # noqa: BLE001
        pass
