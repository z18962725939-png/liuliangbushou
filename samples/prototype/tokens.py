"""自动生成，请勿手改。唯一真源：design_tokens.json → python build_tokens.py"""
from __future__ import annotations

TOKENS = {
    "version": "1.0",
    "light": {
        "--brand": "#0052D9",
        "--brand-hover": "#366EF4",
        "--brand-soft": "#ECF2FE",
        "--on-brand": "#FFFFFF",
        "--bg-page": "#F3F4F6",
        "--bg-container": "#FFFFFF",
        "--bg-hover": "#F7F8FA",
        "--bg-sunken": "#FAFAFB",
        "--text-1": "rgba(0,0,0,.90)",
        "--text-2": "rgba(0,0,0,.60)",
        "--text-3": "rgba(0,0,0,.40)",
        "--line": "#E7E8EC",
        "--line-soft": "#F0F1F3",
        "--success": "#2BA471",
        "--success-soft": "#E8F8F0",
        "--warning": "#E37318",
        "--warning-soft": "#FFF3E8",
        "--danger": "#D54941",
        "--danger-soft": "#FDECEC",
        "--shadow": "0 1px 2px rgba(0,0,0,.04),0 4px 14px rgba(0,0,0,.03)"
    },
    "dark": {
        "--brand": "#C8F751",
        "--brand-hover": "#D9FF78",
        "--brand-soft": "rgba(200,247,81,.13)",
        "--on-brand": "#0B0E14",
        "--bg-page": "#0B0E14",
        "--bg-container": "#121722",
        "--bg-hover": "#1A2130",
        "--bg-sunken": "#0E131C",
        "--text-1": "#EAEEF7",
        "--text-2": "#A2ACC3",
        "--text-3": "#6B7590",
        "--line": "rgba(255,255,255,.09)",
        "--line-soft": "rgba(255,255,255,.055)",
        "--success": "#4ADE80",
        "--success-soft": "rgba(74,222,128,.12)",
        "--warning": "#FBBF24",
        "--warning-soft": "rgba(251,191,36,.12)",
        "--danger": "#FF7A4A",
        "--danger-soft": "rgba(255,122,74,.12)",
        "--shadow": "0 1px 2px rgba(0,0,0,.24),0 4px 18px rgba(0,0,0,.18)"
    },
    "common": {
        "--radius-sm": "6px",
        "--radius": "9px",
        "--radius-lg": "12px",
        "--space-xs": "4px",
        "--space-sm": "8px",
        "--space-md": "12px",
        "--space-lg": "16px",
        "--space-xl": "24px",
        "--font-sans": "\"PingFang SC\",\"Microsoft YaHei\",\"Hiragino Sans GB\",-apple-system,BlinkMacSystemFont,\"Segoe UI\",sans-serif",
        "--size-xs": "11px",
        "--size-sm": "12px",
        "--size-base": "13px",
        "--size-h2": "14px",
        "--size-h1": "22px"
    }
}


def color(name: str, theme: str = "light") -> str:
    """取一个颜色/尺寸令牌，例：color("--brand", "dark")。"""
    key = name if name.startswith('--') else '--' + name
    group = TOKENS.get(theme, TOKENS['light'])
    return group.get(key, TOKENS['common'].get(key, ''))


def as_qss(theme: str = 'light') -> str:
    """给 Qt 侧用：把令牌拼成可直接被 QSS 消费的占位映射文本。"""
    merged = {**TOKENS['common'], **TOKENS[theme]}
    return '\n'.join(f'{k} = {v}' for k, v in merged.items())
