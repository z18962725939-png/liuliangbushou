# -*- coding: utf-8 -*-
"""找出官网在窄屏下是哪个元素撑出了横向溢出。

做法：把一段探针脚本注入页面副本，用无头浏览器 dump DOM，
      读取每个 right 超出视口宽度的元素。
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "installer_output" / "standalone" / "index.html"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

PROBE = """
<script>
(function(){
  function run(){
    var w = document.documentElement.clientWidth;
    var out = [];
    document.querySelectorAll('body *').forEach(function(el){
      var r = el.getBoundingClientRect();
      if (r.width > 0 && r.right > w + 1) {
        var cls = el.className ? '.' + String(el.className).split(' ')[0] : '';
        out.push(el.tagName.toLowerCase() + cls + '@' + Math.round(r.right));
      }
    });
    var d = document.createElement('div');
    d.id = 'probe-out';
    d.textContent = 'VIEWPORT=' + w + ' ;; ' + (out.slice(0, 25).join(' | ') || 'NO-OVERFLOW');
    document.body.appendChild(d);
  }
  if (document.readyState === 'complete') run();
  else window.addEventListener('load', run);
})();
</script>
"""


def probe(css_width: int, scale: int = 1, win_w: int | None = None):
    """css_width = 想验证的 CSS 视口宽度。

    注意：无头窗口有最小物理宽度，直接请求 390 会实际得到 492，
    CSS 视口也就不是 390。用 2x 缩放 + 双倍物理窗口才能拿到真实窄视口。
    """
    raw = HTML.read_text(encoding="utf-8")
    raw = raw.replace("</body>", PROBE + "</body>")
    tmp = HTML.parent / "_probe_tmp.html"
    tmp.write_text(raw, encoding="utf-8")

    r = subprocess.run([
        str(EDGE), "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--no-first-run", "--disable-extensions", f"--force-device-scale-factor={scale}",
        f"--window-size={win_w or css_width},1600", "--virtual-time-budget=5000",
        f"--user-data-dir={ROOT / 'tools' / '.edge_poster_profile'}",
        "--dump-dom", tmp.as_uri(),
    ], capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace")
    tmp.unlink(missing_ok=True)

    m = re.search(r'id="probe-out">([^<]*)<', r.stdout or "")
    print(f"[目标 {css_width}px / scale {scale}] {m.group(1) if m else '没拿到探针输出（dump 失败）'}")


if __name__ == "__main__":
    probe(390, 2, 780)
    probe(360, 2, 720)
    probe(560, 2, 1120)
    probe(1024, 1)
