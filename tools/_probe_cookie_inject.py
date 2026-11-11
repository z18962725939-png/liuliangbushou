# -*- coding: utf-8 -*-
"""验证登录态到底注没注入进渲染浏览器，以及注入后还跳不跳首页。

三种结果对应三种修法：
  · 注入后浏览器里 Cookie 条数 ≈ 0 → 注入姿势有问题（domain / secure / sameSite）
  · 注入成功但访问作品页仍跳 /jingxuan → Cookie 本身已过期，只能让客户重新扫码
  · 注入成功且不再跳 → 修复成立，可以打包
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from justoneapi_collector.douyin_cdp_crawler import DouyinCDPCrawler  # noqa: E402

VID = "7698127444486328433"


def main() -> int:
    cfg = json.loads((Path.home() / "AppData" / "Roaming" / "TrafficCatcher"
                      / "config.json").read_text(encoding="utf-8"))
    cookie = str(cfg.get("douyin_cookie") or "")
    print(f"配置里的 Cookie 长度：{len(cookie)}")

    prof = Path.home() / "AppData" / "Roaming" / "TrafficCatcher" / "browser_profile" / "_resolve_probe"
    c = DouyinCDPCrawler(prof, headless=True, timeout=25.0)
    ok, msg = c.start()
    print("启动浏览器：", ok, msg)
    if not ok:
        return 1
    try:
        n = c.add_cookies(cookie)
        print(f"setCookies 提交条数：{n}")

        # 先打开 douyin.com 让 Cookie 落到该域下，再读回来
        c.navigate("https://www.douyin.com/")
        time.sleep(2.0)
        stored = c.get_cookie_header()
        names = [x.split("=")[0] for x in stored.split(";") if "=" in x]
        print(f"浏览器里 douyin 域 Cookie 条数：{len(names)}")
        key = [k for k in names if k.strip() in
               ("sessionid", "sessionid_ss", "sid_tt", "ttwid", "odin_tt")]
        print(f"关键项是否落地：{key}")

        # 关键一步：带上登录态访问作品页，看会不会又被踢去 /jingxuan
        print(f"\n访问作品页 /video/{VID} …（连续盯 25 秒，看验证码会不会自己过去）")
        c.navigate(f"https://www.douyin.com/video/{VID}")
        for i in range(17):
            time.sleep(1.5)
            href = str(c.evaluate("location.href") or "")
            title = str(c.evaluate("document.title") or "")
            raw = str(c.evaluate(
                r"""(function(){
                      var vs=[];
                      document.querySelectorAll('video').forEach(function(v){
                        (v.querySelectorAll('source')||[]).forEach(function(s){vs.push(s.src||'');});
                        if(v.src) vs.push(v.src);
                      });
                      return JSON.stringify({videos: vs.length,
                        hasCaptcha: !!document.querySelector('#captcha_container,.captcha_verify_container,[class*=captcha]'),
                        text:(document.body&&document.body.innerText||'').replace(/\s+/g,' ').slice(0,120)});
                    })()""") or "")
            state = ""
            if raw:
                try:
                    d = json.loads(raw)
                    state = (f"video标签={d.get('videos')} 验证码={d.get('hasCaptcha')}"
                             f" 文字=『{str(d.get('text') or '')[:50]}』")
                except Exception:                   # noqa: BLE001
                    state = ""
            print(f"  {i*1.5+1.5:5.1f}s  title={title[:24]:<24}  {state}")
    finally:
        c.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
