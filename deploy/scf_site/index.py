# -*- coding: utf-8 -*-
"""
腾讯云 SCF「Web 函数」入口：用标准库起一个极简 HTTP 服务，直接吐官网页面。

为什么不用事件函数：
    事件函数走函数 URL 时，SCF 会往响应里塞 Content-Type: application/json 和
    Content-Disposition: attachment，浏览器收到 attachment 就会把页面当文件下载。
    Web 函数的响应头完全由这里控制，才能真正「打开网页」。

响应头我们自己写死：
    Content-Type: text/html; charset=utf-8   （不带 Content-Disposition）
页面本体在同目录 site.html（图片已 base64 内嵌，不依赖任何外部资源）。
"""
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("SCF_SERVER_PORT", "9000"))
_SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site.html")

with open(_SITE, "rb") as _f:
    HTML = _f.read()

NOT_FOUND = b"<meta charset='utf-8'><h2>404</h2>"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html", ""):
            self._send(200, HTML, "text/html; charset=utf-8")
        else:
            self._send(404, NOT_FOUND, "text/html; charset=utf-8")

    def do_HEAD(self):
        self.do_GET()

    def log_message(self, fmt, *args):
        pass  # 静音，避免刷日志


if __name__ == "__main__":
    sys.stdout.write("serving on 0.0.0.0:%d, html=%d bytes\n" % (PORT, len(HTML)))
    sys.stdout.flush()
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
