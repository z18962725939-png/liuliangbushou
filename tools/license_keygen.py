# -*- coding: utf-8 -*-
"""
「流量捕手」发卡工具 —— 只给你（软件作者）自己用。

═══════════════════════════════════════════════════════════════════════════
怎么用
═══════════════════════════════════════════════════════════════════════════
    cd 项目根目录
    python tools/license_keygen.py

    单张出码：填客户名 + 客户发来的机器码 → 选有效期 → 选套餐额度 → 点「生成授权码」
              → 把授权码（或导出的 .lic 文件）发给客户
    批量出码：一行一个「客户名 机器码 [天数] [每日额度]」，点「批量生成」，
              结果导出成 CSV，适合一次卖给一个团队/工作室

═══════════════════════════════════════════════════════════════════════════
套餐额度怎么选（软件按「包 API」卖，接口成本由你承担，必须限额）
═══════════════════════════════════════════════════════════════════════════
    体验版 10 次/天   → 首购 19.9 元 / 38 天（31 天 + 赠送 7 天），默认就选这个
    标准版 30 次/天   → 续费 29.9 元/月（勾「续费客户」就不送那 7 天）
    专业版 100 次/天  → 59.9 元/月，重度用户 / 工作室
    不限量            → 你自己用（别发给客户！）

    额度写进授权码、受签名保护，客户改不了；每天 UTC+8 零点自动重置。
    只统计 JustOneAPI 调用；抖音直连的喜欢/收藏抓取和视频下载不占额度。

═══════════════════════════════════════════════════════════════════════════
⚠️ 这个工具里的私钥 = 你的印钞机
═══════════════════════════════════════════════════════════════════════════
    私钥在 tools/license_private.key，它【绝对不能】跟着安装包发给客户：
    拿到私钥的人可以随便给自己签永久授权码。
    · 别把 tools/ 目录打包进安装包（iss 脚本里已排除）；
    · 别把项目目录整个压缩发给别人；
    · 换电脑记得先把这个文件拷走 —— 丢了就只能换密钥对，
      换密钥对意味着老客户的授权码全部失效，只能重新发码。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# 允许直接 `python tools/license_keygen.py` 运行
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from justoneapi_collector import branding, licensing  # noqa: E402

PRIVATE_KEY_FILENAME = "license_private.key"
PRIVATE_KEY_PATH = Path(__file__).resolve().parent / PRIVATE_KEY_FILENAME


def _candidate_key_paths() -> list[Path]:
    """私钥的可能位置（按顺序找）。

    打包成 exe 后 ``__file__`` 指向临时解压目录，所以第一优先是【exe 同目录】
    —— 你把 license_private.key 跟 exe 放一起即可，换台电脑也能用。
    """
    paths: list[Path] = []
    if getattr(sys, "frozen", False):          # 打包后的独立 exe
        paths.append(Path(sys.executable).resolve().parent / PRIVATE_KEY_FILENAME)
    paths.append(PRIVATE_KEY_PATH)
    return paths


def find_private_key() -> Path | None:
    """找到可用的私钥文件；找不到返回 None（由调用方引导用户）。"""
    for p in _candidate_key_paths():
        try:
            if p.is_file() and p.read_text(encoding="utf-8").strip():
                return p
        except Exception:                       # noqa: BLE001
            continue
    return None

EXPIRE_CHOICES = {
    "31 天（首购档）": 31,
    "30 天": 30,
    "90 天": 90,
    "180 天": 180,
    "365 天": 365,
    "永久": 0,
}

# ── 活动：首购默认多送 7 天 ──
# 19.9 元 / 31 天 + 赠送 7 天 = 38 天。改这个数字就能改赠送力度（0 = 取消活动）。
BONUS_DAYS = 7
BONUS_NOTE = f"活动：首购默认赠送 {BONUS_DAYS} 天（31 天 + {BONUS_DAYS} = 38 天）"


def load_private_key() -> str:
    found = find_private_key()
    if found is None:
        raise SystemExit(
            f"找不到私钥文件（已在这些位置找过）：\n"
            + "\n".join(f"  · {p}" for p in _candidate_key_paths())
            + "\n\n首次使用请先生成密钥对（一生只需一次）：\n"
            "    python -c \"import secrets,pathlib;"
            "p=pathlib.Path('tools/license_private.key');"
            "p.write_text(secrets.token_hex(32))\"\n"
            "或者打开图形界面（--gui），按提示选择已有的私钥文件。"
        )
    return found.read_text(encoding="utf-8").strip()


# ======================================================================
#  命令行模式（适合脚本/批量）
# ======================================================================

def main_cli(argv: list[str]) -> int:
    seed = load_private_key()
    if len(argv) >= 2:
        # keygen.py 客户名 机器码 [天数] [token]
        customer = argv[0]
        machine = argv[1]
        base_days = int(argv[2]) if len(argv) > 2 else 365
        # CLI 也默认送 7 天；传 --no-bonus 可关掉（放在任意参数位都行）
        days = base_days if (base_days == 0 or "--no-bonus" in argv) else base_days + BONUS_DAYS
        token = argv[3] if len(argv) > 3 else ""
        quota = int(argv[4]) if len(argv) > 4 else licensing.DEFAULT_DAILY_QUOTA
        # 第 6 个参数：功能位，逗号分隔（如 geo / collect,download）。
        # 留空 = 全部【常规】功能；独立付费模块（GEO 等）必须显式传，不会默认送。
        # 单独卖 GEO 月卡时用 `python ... 客户名 机器码 天数 Token 额度 geo`
        # 采集套餐 + 加开 GEO：把常规位和 geo 一起传，如 collect,keyword,batch,...,geo
        feats = (argv[5] if len(argv) > 5 else "").strip()
        bad = [f for f in feats.split(",") if f and f not in licensing.FEATURES]
        if bad:
            print(f"功能位不存在：{bad}；可选：{', '.join(licensing.FEATURES)}")
            return 1
        standalone = [licensing.FEATURES[k] for k, v in licensing.FEATURE_BACKFILL.items()
                      if v == "deny"]
        if not feats and standalone:
            print(f"[提示] 未指定功能位 → 按「常规全功能」签发，"
                  f"不含独立付费模块：{'、'.join(standalone)}。\n"
                  f"        要给客户开这些模块，请显式作为第 6 个参数传入（如 geo）。",
                  file=sys.stderr)
        if not token:
            print("[警告] 第 4 个参数（Token）为空 → 这张码不含采集凭证。\n"
                  "        客户端没有 Token 输入框，客户激活后会一直提示\n"
                  "        「未连接采集服务」，无法采集任何数据。\n"
                  "        建议：先把自己的 JustOneAPI Token 传进来。",
                  file=sys.stderr)
        code = licensing.issue_license(seed, license_token=token, machine=machine,
                                       days=days, customer=customer, quota=quota,
                                       features=feats)
        print(code)
        return 0
    print(f"{branding.APP_NAME} 发卡工具（CLI）")
    print(f"  用法: python tools/license_keygen.py 客户名 机器码 [天数] [Token] [每日额度]")
    print(f"  机器码写 ANY 表示不限机器（多机版）")
    print(f"  加 GUI: python tools/license_keygen.py --gui")
    return 0


# ======================================================================
#  图形界面
# ======================================================================

def main_gui() -> int:                                       # pragma: no cover
    import shutil
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    # 先把窗口建好（隐藏），这样"找不到私钥"时可以直接弹窗引导
    root = tk.Tk()
    root.withdraw()

    key_path = find_private_key()
    if key_path is None:
        messagebox.showwarning(
            "缺少私钥",
            "没有找到私钥文件 license_private.key。\n\n"
            "请选择你的私钥文件（就是那串 64 位十六进制字符的文件）；\n"
            "选好后会自动复制一份到本工具旁边，以后不用再选。\n\n"
            "如果确实还没有私钥，需要先生成一个（一生只需一次），\n"
            "生成后把弹出来的公钥填进 licensing.py 的 LICENSE_PUBLIC_KEY_HEX。")
        picked = filedialog.askopenfilename(
            title="选择私钥文件", filetypes=[("私钥文件", "*.key"), ("所有文件", "*.*")])
        if not picked:
            root.destroy()
            return 1
        dest = _candidate_key_paths()[0]
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(picked, dest)
            key_path = dest
        except Exception:                                # noqa: BLE001
            key_path = Path(picked)
    seed = key_path.read_text(encoding="utf-8").strip()
    pub_hex = licensing.ed25519_public_key(bytes.fromhex(seed)).hex()
    key_ok = (pub_hex == licensing.LICENSE_PUBLIC_KEY_HEX)

    root.deiconify()
    root.title(f"{branding.APP_NAME} · 发卡工具（内部使用）")
    root.geometry("880x680")

    style = ttk.Style()
    try:
        style.theme_use("vista")
    except tk.TclError:
        pass
    for w in ("TLabel", "TButton", "TCheckbutton", "TLabelframe.Label"):
        style.configure(w, font=("Microsoft YaHei UI", 9))
    style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 9, "bold"))

    # -- 头部 ----------------------------------------------------------
    head = ttk.Frame(root, padding=(10, 8))
    head.pack(fill="x")
    ttk.Label(head, text=f"🔑 {branding.APP_NAME} 授权码签发",
              font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
    warn = "✔ 私钥与程序内置公钥匹配" if key_ok else "✘ 私钥与程序公钥不匹配！"
    ttk.Label(head, text=warn, foreground="#2e7d32" if key_ok else "#c62828",
              font=("Microsoft YaHei UI", 9, "bold")).pack(side="right")

    def copy_pub():
        root.clipboard_clear()
        root.clipboard_append(pub_hex)
        messagebox.showinfo(
            "已复制公钥",
            f"{pub_hex}\n\n把它填进 licensing.py 的 LICENSE_PUBLIC_KEY_HEX，"
            "然后重新打包客户端 —— 否则客户激活会报「签名校验失败」。")

    ttk.Button(head, text="📋 复制公钥", command=copy_pub).pack(side="right", padx=(0, 10))
    if not key_ok:
        ttk.Label(root, foreground="#c62828", wraplength=840, justify="left", padding=(10, 0),
                  text=("⚠️ 私钥与 licensing.py 里的 LICENSE_PUBLIC_KEY_HEX 对不上，"
                        "现在签发的授权码客户激活时会报「签名校验失败」。"
                        "请把上面这行公钥填进 licensing.py。")).pack(fill="x")

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=10, pady=6)

    # =================== 单张出码 ===================
    tab1 = ttk.Frame(nb, padding=10)
    nb.add(tab1, text="  单张出码  ")

    form = ttk.LabelFrame(tab1, text="客户信息", padding=10)
    form.pack(fill="x")

    def row(label, widget_factory, hint=""):
        f = ttk.Frame(form)
        f.pack(fill="x", pady=3)
        ttk.Label(f, text=label, width=12).pack(side="left")
        w = widget_factory(f)
        w.pack(side="left", fill="x", expand=True)
        if hint:
            ttk.Label(f, text=hint, foreground="#777777").pack(side="left", padx=(6, 0))
        return w

    v_customer = tk.StringVar()
    v_machine = tk.StringVar()
    v_expire = tk.StringVar(value="31 天（首购档）")   # 默认就落在「19.9 元 38 天」这一档
    v_custom_days = tk.StringVar(value="")
    v_bonus = tk.BooleanVar(value=True)                # 活动赠送，默认开
    v_renew = tk.BooleanVar(value=False)               # 续费客户（与赠送互斥）
    v_token = tk.StringVar()

    row("客户名称", lambda p: ttk.Entry(p, textvariable=v_customer), "会写进授权码，客户激活后能看到")
    row("机器码", lambda p: ttk.Entry(p, textvariable=v_machine),
        "客户在「设置 → 授权与激活」里复制给你；填 ANY = 不限机器")

    fexp = ttk.Frame(form)
    fexp.pack(fill="x", pady=3)
    ttk.Label(fexp, text="有效期", width=12).pack(side="left")
    cb = ttk.Combobox(fexp, textvariable=v_expire, state="readonly", width=10,
                      values=list(EXPIRE_CHOICES.keys()))
    cb.pack(side="left")
    ttk.Label(fexp, text="自定义天数:", foreground="#777777").pack(side="left", padx=(12, 4))
    ttk.Entry(fexp, textvariable=v_custom_days, width=8).pack(side="left")
    ttk.Label(fexp, text="（填了就优先用这个；0 = 永久）",
              foreground="#777777").pack(side="left", padx=(6, 0))

    # 新购 / 续费 二选一：赠送只给首购，续费按 29.9 元/月原价，不送
    def _on_renew():
        if v_renew.get():
            v_bonus.set(False)

    def _on_bonus():
        if v_bonus.get():
            v_renew.set(False)

    fbonus = ttk.Frame(form)
    fbonus.pack(fill="x", pady=3)
    ttk.Label(fbonus, text="", width=12).pack(side="left")   # 对齐上面的字段
    ttk.Checkbutton(fbonus, text=BONUS_NOTE, variable=v_bonus,
                    command=_on_bonus).pack(side="left")
    ttk.Checkbutton(fbonus, text="续费客户（不赠送，29.9 元/月）", variable=v_renew,
                    command=_on_renew).pack(side="left", padx=(12, 0))
    ttk.Label(fbonus, text="（二选一；永久码不受影响）",
              foreground="#777777").pack(side="left", padx=(6, 0))

    # ── 套餐 / 每日调用额度 ──
    # 软件按「包 API」卖，接口成本由作者承担，所以每个码都要带额度上限。
    # 额度写进授权码并受签名保护，客户改不了；-1 = 不限（作者自用）。
    v_plan = tk.StringVar(value=licensing.PLANS["std30"]["label"])
    v_custom_quota = tk.StringVar(value="")
    fplan = ttk.Frame(form)
    fplan.pack(fill="x", pady=3)
    ttk.Label(fplan, text="套餐额度", width=12).pack(side="left")
    ttk.Combobox(fplan, textvariable=v_plan, state="readonly", width=12,
                 values=[str(p["label"]) for p in licensing.PLANS.values()]).pack(side="left")
    ttk.Label(fplan, text="自定义:", foreground="#777777").pack(side="left", padx=(12, 4))
    ttk.Entry(fplan, textvariable=v_custom_quota, width=8).pack(side="left")
    ttk.Label(fplan, text="（填了优先用；-1 = 不限；按 UTC+8 每日 0 点重置）",
              foreground="#777777").pack(side="left", padx=(6, 0))

    row("接口 Token", lambda p: ttk.Entry(p, textvariable=v_token, show="•"),
        "强烈建议填写。填了就加密进授权码，客户无需自己申请 Token（你的成本靠上面的额度控制）")

    def use_my_token():
        """把自己当前配置里的 Token 一键填进来（方便给客户出码）。"""
        code = licensing.imported_license_code()
        tok = licensing.verify_license(code).get("embedded_token") if code else ""
        if not tok:
            # 注意键名：本软件一直把 Token 存在 config.json 的 "token" 里，
            # 早期这里误写成 "api_token"，导致点了按钮却读不到、出码没带 Token。
            # 两个键都试一遍，兼容历史配置。
            try:
                import json
                cfg = branding.app_data_dir() / "config.json"
                data = json.loads(cfg.read_text(encoding="utf-8"))
                tok = (data.get("token") or data.get("api_token") or "").strip()
            except Exception:                              # noqa: BLE001
                tok = ""
        if tok:
            v_token.set(tok)
            messagebox.showinfo("已填入", "已从本机配置读取到 Token。")
        else:
            messagebox.showwarning(
                "没读到 Token",
                "本机配置里没有找到 Token。\n"
                "如果你的商业模式是「客户自带 Token」，这个字段留空即可。")

    ttk.Button(form, text="用我本机的 Token", command=use_my_token).pack(anchor="w", pady=(6, 0))

    # 功能勾选
    # ★ 独立付费模块（FEATURE_BACKFILL == "deny"，如 GEO）默认【不勾】：
    #   它不随采集套餐售出，客户要单独买。默认勾上会让签出去的码自带 GEO，
    #   等于白送；同时也会让「全勾＝不写 f 字段」的旧省略写法把它一并吞掉。
    feat_box = ttk.LabelFrame(
        tab1, text="包含功能（默认全选＝采集套件；带 ★ 的为独立付费模块，需单独勾选）",
        padding=8)
    feat_box.pack(fill="x", pady=(8, 0))
    feat_vars: dict[str, tk.BooleanVar] = {}
    for i, (k, label) in enumerate(licensing.FEATURES.items()):
        standalone = licensing.FEATURE_BACKFILL.get(k) == "deny"
        v = tk.BooleanVar(value=not standalone)
        feat_vars[k] = v
        ttk.Checkbutton(feat_box, text=("★ " + label) if standalone else label,
                        variable=v).grid(row=i // 4, column=i % 4, sticky="w", padx=6, pady=2)

    out_box = ttk.LabelFrame(tab1, text="生成的授权码", padding=8)
    out_box.pack(fill="both", expand=True, pady=(8, 0))
    out_text = tk.Text(out_box, height=4, wrap="char", font=("Consolas", 9))
    out_text.pack(fill="both", expand=True)

    def with_bonus(base: int) -> int:
        """活动赠送：有效期的码统一 +7 天，永久码（0）不受影响。"""
        if base <= 0:
            return base
        return base + BONUS_DAYS if v_bonus.get() else base

    def current_days() -> int:
        custom = v_custom_days.get().strip()
        if custom:
            try:
                return with_bonus(int(custom))
            except ValueError:
                messagebox.showwarning("提示", "自定义天数必须是数字")
                return with_bonus(365)
        return with_bonus(EXPIRE_CHOICES.get(v_expire.get(), 365))

    def current_quota() -> int:
        """当前选中的每日调用额度（-1 = 不限）。"""
        custom = v_custom_quota.get().strip()
        if custom:
            try:
                return int(custom)
            except ValueError:
                messagebox.showwarning("提示", "自定义额度必须是整数（-1 表示不限）")
                return licensing.DEFAULT_DAILY_QUOTA
        for p in licensing.PLANS.values():
            if str(p["label"]) == v_plan.get().strip():
                return int(p["quota"])
        return licensing.DEFAULT_DAILY_QUOTA

    def selected_features() -> str:
        """把勾选结果转成授权码里的 f 字段。

        ★ 不再使用「全勾 = 留空」的省略写法（2026-10-12 改）。
        原因：功能清单里现在既有随套餐售出的常规位，也有独立付费模块（GEO）。
        省略写法下「全勾」和「一个都不勾」都会变成空值，客户端解析不出差别 ——
        前者会连独立付费模块一起送出去，后者又会退化成「全选」全送。
        一律显式列出勾了哪些位；空值只保留给「全选」的历史语义。

        返回空串的场景已经不可能出现：调用方 gen()/bulk_gen() 会先拦住空勾选。
        """
        picked = [k for k, v in feat_vars.items() if v.get()]
        return ",".join(picked)

    def _confirm_no_feature() -> bool:
        """拦住「一个功能都没勾」的码。

        那种码客户激活后每个页面都提示「当前授权不包含…」，等于卖了个空壳。
        """
        if [k for k, v in feat_vars.items() if v.get()]:
            return True
        messagebox.showwarning(
            "没勾任何功能",
            "「包含功能」里一个都没勾，这样签出来的码客户激活后\n"
            "每个页面都会提示「当前授权不包含…」，等于卖了个空壳。\n\n"
            "请至少勾选「数据采集」；做 GEO 单卖时勾「★ AI 可见度监测（GEO）」。")
        return False

    def _confirm_no_token() -> bool:
        """签发前拦住「没带接口凭证」的码。

        ★ 2026-10-12 客户投诉补的：客户激活成功却一直提示「未连接采集服务」，
        真正原因是码里没有内嵌 Token。而现在的客户端【已经没有 Token 输入框】
        （凭证只能随码下发），所以不带 Token 的码 = 客户激活了也用不了，
        他只会以为软件坏了跑来投诉。签这种码之前必须先确认。
        """
        if v_token.get().strip():
            return True
        return messagebox.askyesno(
            "接口 Token 为空，确定要签吗？",
            "「接口 Token」是空的，签出来的授权码【不含采集凭证】。\n\n"
            "客户端已经没有 Token 输入框了（凭证只能随码下发），\n"
            "所以这种码客户激活后会一直提示「未连接采集服务」，无法采集任何数据。\n\n"
            "建议：点「用我本机的 Token」自动填入，或手动粘贴你的 JustOneAPI Token。\n\n"
            "确实要签一张不能采集的码吗？（例如客户自带 Token 的特殊情况）")

    def gen():
        cust = v_customer.get().strip()
        machine = v_machine.get().strip() or licensing.ANY_MACHINE
        if not _confirm_no_feature():
            return
        if not _confirm_no_token():
            return
        try:
            quota = current_quota()
            days_used = current_days()          # 已含活动赠送，状态栏显示它，出码前能核对
            code = licensing.issue_license(
                seed, license_token=v_token.get().strip(),
                machine=machine, days=days_used,
                customer=cust, features=selected_features(), quota=quota,
            )
        except Exception as e:                         # noqa: BLE001
            messagebox.showerror("签发失败", str(e))
            return
        # 自检：自己签的码自己必须验得过
        info = licensing.verify_license(code)
        self_ok = info["valid"] or (not info["machine_ok"] and machine != licensing.ANY_MACHINE)
        out_text.delete("1.0", "end")
        out_text.insert("1.0", code)
        q = info.get("daily_quota")
        q_txt = "不限" if q == licensing.UNLIMITED else f"{q} 次/天"
        d_txt = "永久" if days_used == 0 else f"{days_used} 天"
        tail = f"　（自检：{'通过' if self_ok else '❌ 未通过 —— ' + info['reason']}）"
        status_var.set(f"已生成 {len(code)} 字符的授权码{tail}　有效期：{d_txt}　额度：{q_txt}")

    def copy():
        txt = out_text.get("1.0", "end").strip()
        if not txt:
            return
        root.clipboard_clear()
        root.clipboard_append(txt)
        status_var.set("授权码已复制到剪贴板")

    def save_lic():
        txt = out_text.get("1.0", "end").strip()
        if not txt:
            messagebox.showwarning("提示", "先生成授权码")
            return
        cust = v_customer.get().strip() or "customer"
        path = filedialog.asksaveasfilename(
            title="保存授权文件", defaultextension=".lic",
            initialfile=f"{cust}_{branding.APP_NAME}.lic",
            filetypes=[("授权文件", "*.lic"), ("文本文件", "*.txt")])
        if not path:
            return
        licensing.export_license_file(txt, Path(path), note=cust)
        status_var.set(f"已保存：{path}")

    btns = ttk.Frame(tab1)
    btns.pack(fill="x", pady=(8, 0))
    ttk.Button(btns, text="🔏 生成授权码", command=gen).pack(side="left")
    ttk.Button(btns, text="📋 复制", command=copy).pack(side="left", padx=6)
    ttk.Button(btns, text="💾 保存为 .lic", command=save_lic).pack(side="left")

    # =================== 批量出码 ===================
    tab2 = ttk.Frame(nb, padding=10)
    nb.add(tab2, text="  批量出码  ")

    ttk.Label(tab2, foreground="#555555", justify="left",
              text=("每行一个客户，格式：　客户名 机器码 [天数] [每日额度]　（空格或逗号分隔）\n"
                    "天数省略按上面「有效期」，额度省略按上面「套餐额度」；机器码写 ANY 表示不限机器。\n"
                    "示例：\n"
                    "    张三 A1B2-C3D4-E5F6-7890 365 30\n"
                    "    某某工作室 ANY 0 100\n"
                    "    试用客户 A1B2-C3D4-E5F6-7890 7 10")).pack(anchor="w")
    bulk_in = tk.Text(tab2, height=9, font=("Consolas", 9))
    bulk_in.pack(fill="both", expand=True, pady=6)
    bulk_in.insert("1.0", "示例客户 A1B2-C3D4-E5F6-7890 365\n")

    bulk_out = tk.Text(tab2, height=10, font=("Consolas", 8), wrap="none")
    bulk_out.pack(fill="both", expand=True)

    def bulk_gen():
        # ★ 批量出码曾经把 token 写死成空串 → 一次签出的几十张码全都不含采集凭证，
        #   客户激活后一律「未连接采集服务」。现在改用「单张出码」里填的同一个
        #   Token（你一个人的 Token 发给全部客户，这是既定商业模式）。
        # ★ 批量出的码功能位与「单张出码」页的勾选完全一致（含 GEO 勾选状态），
        #   不存在「批量签的码功能不一样」的暗坑。
        if not _confirm_no_feature():
            return
        if not _confirm_no_token():
            return
        token_shared = v_token.get().strip()
        lines = [l.strip() for l in bulk_in.get("1.0", "end").splitlines() if l.strip()]
        rows = ["客户名,机器码,有效期天数,每日额度,授权码"]
        ok = 0
        for line in lines:
            parts = [p for p in line.replace(",", " ").split() if p]
            if len(parts) < 2:
                continue
            cust, machine = parts[0], parts[1]
            days = current_days()
            quota = current_quota()
            token = token_shared
            # 第 3 个参数是天数，第 4 个是每日额度（都允许负数）
            for idx, p in enumerate(parts[2:]):
                if not p.lstrip("-").isdigit():
                    continue
                if idx == 0:
                    days = with_bonus(int(p))      # 批量出码同样享受活动赠送
                elif idx == 1:
                    quota = int(p)
            try:
                code = licensing.issue_license(
                    seed, license_token=token, machine=machine, days=days,
                    customer=cust, features=selected_features(), quota=quota)
            except Exception as e:                     # noqa: BLE001
                rows.append(f"{cust},{machine},{days},{quota},签发失败:{e}")
                continue
            ok += 1
            rows.append(f"{cust},{machine},{days},{quota},{code}")
        bulk_out.delete("1.0", "end")
        bulk_out.insert("1.0", "\n".join(rows))
        status_var.set(f"批量签发完成：{ok}/{len(lines)} 条。点「导出 CSV」保存。")

    def bulk_save():
        txt = bulk_out.get("1.0", "end").strip()
        if not txt:
            messagebox.showwarning("提示", "先批量生成")
            return
        path = filedialog.asksaveasfilename(
            title="导出结果", defaultextension=".csv",
            initialfile=f"授权码_{time.strftime('%Y%m%d_%H%M')}.csv",
            filetypes=[("CSV 表格", "*.csv")])
        if not path:
            return
        # 带 BOM，Excel 打开不乱码
        Path(path).write_text("\ufeff" + txt, encoding="utf-8-sig")
        status_var.set(f"已导出：{path}")

    bb = ttk.Frame(tab2)
    bb.pack(fill="x")
    ttk.Button(bb, text="⚡ 批量生成", command=bulk_gen).pack(side="left")
    ttk.Button(bb, text="📊 导出 CSV", command=bulk_save).pack(side="left", padx=6)

    # =================== 关于 ===================
    tab3 = ttk.Frame(nb, padding=10)
    nb.add(tab3, text="  说明  ")
    info = f"""产品：{branding.APP_NAME} v{branding.APP_VERSION}
签名算法：Ed25519（RFC 8032）
内置公钥：{licensing.LICENSE_PUBLIC_KEY_HEX}
私钥文件：{PRIVATE_KEY_PATH}
私钥指纹：{pub_hex[:16]}…

【怎么收钱】
  1. 客户下载安装包 → 打开软件 → 设置页「授权与激活」看到本机机器码
  2. 客户把机器码发给你 + 付钱
  3. 你在这里生成授权码，发给客户
  4. 客户在软件里粘贴激活（或双击 .lic 文件）

【有效期怎么设计都行】
  年费制：选 365 天，到期客户续费，你再发一张新码
  永久买断：选「永久」，一次性收钱
  多机器/工作室版：机器码填 ANY

【Token 政策 —— 必读，填错客户就投诉】
  ⚠ 客户端【没有】Token 输入框，凭证只能随授权码下发。
  填了 Token → 客户激活即可用（唯一可行方式）
  留空     → 客户激活后一直提示「未连接采集服务」，无法采集任何数据，
             他只会以为软件坏了来找你。批量出码也一样（已加拦截确认）。

【更新发布】
  1. 改 branding.py 里的 APP_VERSION
  2. 重新打包 + 用 Inno Setup 编译安装包
  3. 把安装包和 version.json 传到你的更新源（GitHub Release / 自己的服务器）
  4. 客户程序启动时会自动提示更新

⚠️ 私钥文件 {PRIVATE_KEY_PATH.name} 千万不要随产品分发。
"""
    ttk.Label(tab3, text=info, justify="left", font=("Consolas", 9)).pack(anchor="w")

    status_var = tk.StringVar(value="就绪")
    bar = ttk.Frame(root)
    bar.pack(fill="x", side="bottom")
    ttk.Label(bar, textvariable=status_var, padding=(10, 6),
              foreground="#1565c0").pack(anchor="w")

    root.mainloop()
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    args = sys.argv[1:]
    if args and args[0] == "--gui":
        raise SystemExit(main_gui())
    if not args:
        try:
            raise SystemExit(main_gui())
        except Exception:                                  # noqa: BLE001
            raise SystemExit(main_cli([]))
    raise SystemExit(main_cli(args))
