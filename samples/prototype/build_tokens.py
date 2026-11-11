"""设计令牌生成器：design_tokens.json → tokens.css + tokens.py

唯一真源是 design_tokens.json。网页原型引用 tokens.css，Python/Qt 侧
import tokens.py，两边读到的颜色、圆角、间距必然一致。

用法：python build_tokens.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "design_tokens.json"
CSS_OUT = HERE / "tokens.css"
PY_OUT = HERE / "tokens.py"
HTML_PAGES = sorted(HERE.glob("流量捕手_*原型.html"))
LINK_TAG = '<link rel="stylesheet" href="tokens.css">'


def load() -> dict:
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    light = data["vars"]["light"]
    dark = data["vars"]["dark"]
    missing = sorted(set(light) ^ set(dark))
    if missing:
        raise SystemExit(f"两套主题变量不一致：{missing}")
    for theme, group in (("light", light), ("dark", dark)):
        for key, value in group.items():
            if not key.startswith("--") or not isinstance(value, str) or not value.strip():
                raise SystemExit(f"{theme} 的变量 {key} 不合法")
    return data


def render_css(data: dict) -> str:
    lines = [
        "/* 自动生成，请勿手改。唯一真源：design_tokens.json → python build_tokens.py */",
        ":root{",
    ]
    for key, value in data["vars"]["light"].items():
        lines.append(f"  {key}:{value};")
    for key, value in data["common"].items():
        lines.append(f"  {key}:{value};")
    lines.append("}")
    lines.append('html[data-theme="dark"]{')
    for key, value in data["vars"]["dark"].items():
        lines.append(f"  {key}:{value};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def render_py(data: dict) -> str:
    payload = {
        "version": data["version"],
        "light": data["vars"]["light"],
        "dark": data["vars"]["dark"],
        "common": data["common"],
    }
    body = json.dumps(payload, ensure_ascii=False, indent=4)
    return (
        '"""自动生成，请勿手改。唯一真源：design_tokens.json → python build_tokens.py"""\n'
        "from __future__ import annotations\n\n"
        f"TOKENS = {body}\n\n\n"
        "def color(name: str, theme: str = \"light\") -> str:\n"
        '    """取一个颜色/尺寸令牌，例：color("--brand", "dark")。"""\n'
        "    key = name if name.startswith('--') else '--' + name\n"
        "    group = TOKENS.get(theme, TOKENS['light'])\n"
        "    return group.get(key, TOKENS['common'].get(key, ''))\n\n\n"
        "def as_qss(theme: str = 'light') -> str:\n"
        '    """给 Qt 侧用：把令牌拼成可直接被 QSS 消费的占位映射文本。"""\n'
        "    merged = {**TOKENS['common'], **TOKENS[theme]}\n"
        "    return '\\n'.join(f'{k} = {v}' for k, v in merged.items())\n"
    )


def sync_html(css: str) -> int:
    """把令牌样式同步进**每一张**原型页，让 HTML 自包含。

    页面本身仍然是派生物：样式块由本函数从唯一真源覆写，手改会被下次生成覆盖。
    返回成功同步的页面数；新加页面只要带 <style id="tokens"> 占位就会被覆盖。
    """
    done = 0
    for page in HTML_PAGES:
        html = page.read_text(encoding="utf-8")
        block = '<style id="tokens">\n' + css + '</style>'
        if '<style id="tokens">' in html:
            html = re.sub(r'<style id="tokens">.*?</style>', lambda _: block, html, flags=re.S)
        elif LINK_TAG in html:
            html = html.replace(LINK_TAG, block)
        else:
            continue
        page.write_text(html, encoding="utf-8")
        done += 1
    return done


def main() -> int:
    data = load()
    css = render_css(data)
    CSS_OUT.write_text(css, encoding="utf-8")
    PY_OUT.write_text(render_py(data), encoding="utf-8")
    n = len(data["vars"]["light"])
    print(f"令牌唯一真源：{SOURCE.name}（v{data['version']}）")
    print(f"  → {CSS_OUT.name}：浅色 {n} 项 + 深色 {n} 项 + 通用 {len(data['common'])} 项")
    print(f"  → {PY_OUT.name}：Python / Qt 侧同源读取")
    print(f"  → 原型页：{sync_html(css)}/{len(HTML_PAGES)} 张已同步为自包含样式")
    print("  校验：两套主题键集合一致，变量名与取值合法，生成完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
