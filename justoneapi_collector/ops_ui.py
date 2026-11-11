"""
运营功能界面（选题挖掘 / 复盘监控 / 周报）——以 mixin 形式接入主界面，
避免在主 GUI 文件里做大手术。

宿主需提供：root、client、pm（ProjectManager）、settings、status_var、notebook。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .platforms.adapter import PLATFORM_DISPLAY, PlatformAdapter, probe_platform
from .projects import ProjectManager
from .reporter import build_report, export_excel_report, export_html_report
from .topic_mining import (COMMENT_EXPORT_COLUMNS, RANKED_POST_COLUMNS,
                           analyze_comments, fetch_comments, insight_to_rows,
                           mine_keywords_batch)
from .watchtower import ANOMALY_COLUMNS, WatchTower

PLATFORM_OPTIONS = ["抖音", "快手", "小红书"]


def _open_folder(path: Path) -> None:
    try:
        p = str(path)
        if sys.platform.startswith("win"):
            os.startfile(p)          # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", p], check=False)
        else:
            subprocess.run(["xdg-open", p], check=False)
    except Exception:
        pass


class OpsMixin:
    """运营工作台：项目栏 + 三个新标签页。"""

    # ================================================================
    #  项目（客户）选择栏
    # ================================================================

    def _create_project_bar(self):
        if not hasattr(self, "pm") or self.pm is None:
            self.pm = ProjectManager()
        bar = ttk.Frame(self.root, padding=(8, 5))
        bar.pack(fill="x", side="top")
        ttk.Label(bar, text="当前项目：", font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        self.proj_var = tk.StringVar()
        self.proj_combo = ttk.Combobox(bar, textvariable=self.proj_var,
                                       state="readonly", width=20,
                                       font=("Microsoft YaHei UI", 9))
        self.proj_combo.pack(side="left")
        self.proj_combo.bind("<<ComboboxSelected>>", lambda e: self._on_project_switch())
        ttk.Button(bar, text="新建项目", command=self._new_project, width=10).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="重命名", command=self._rename_project, width=8).pack(side="left", padx=4)
        ttk.Button(bar, text="打开目录", command=self._open_project_dir, width=10).pack(side="left")
        self.proj_info = ttk.Label(bar, text="", foreground="#666666")
        self.proj_info.pack(side="left", padx=12)
        self._refresh_project_bar()

    def _refresh_project_bar(self):
        self.pm.load()
        if not self.pm.all():
            self.pm.ensure_default()
        names = self.pm.names
        self.proj_combo["values"] = names
        cur = self.pm.current
        if cur:
            self.proj_var.set(cur.name)
            self.proj_info.config(text=cur.summary)
        elif names:
            self.proj_var.set(names[0])

    def _on_project_switch(self):
        self.pm.set_current_by_name(self.proj_var.get())
        cur = self.pm.current
        if cur:
            self.proj_info.config(text=cur.summary)
            self.status_var.set(f"已切换到项目「{cur.name}」")
            self._ops_refresh_watch_accounts()
            self._ops_refresh_topic_watch()

    def _new_project(self):
        top = tk.Toplevel(self.root)
        top.title("新建项目")
        top.geometry("420x230")
        top.transient(self.root)
        ttk.Label(top, text="项目名称（必填）").pack(anchor="w", padx=14, pady=(14, 2))
        name_var = tk.StringVar()
        ttk.Entry(top, textvariable=name_var, width=44).pack(padx=14, fill="x")
        ttk.Label(top, text="客户 / 品牌名").pack(anchor="w", padx=14, pady=(10, 2))
        client_var = tk.StringVar()
        ttk.Entry(top, textvariable=client_var, width=44).pack(padx=14, fill="x")
        ttk.Label(top, text="备注").pack(anchor="w", padx=14, pady=(10, 2))
        note_var = tk.StringVar()
        ttk.Entry(top, textvariable=note_var, width=44).pack(padx=14, fill="x")

        def ok():
            name = name_var.get().strip()
            if not name:
                messagebox.showwarning("提示", "请填写项目名称")
                return
            self.pm.create(name, client=client_var.get().strip(),
                           note=note_var.get().strip())
            self._refresh_project_bar()
            top.destroy()
            self.status_var.set(f"项目「{name}」已创建")

        ttk.Button(top, text="创建", command=ok).pack(pady=14)
        ttk.Button(top, text="取消", command=top.destroy).pack()

    def _rename_project(self):
        cur = self.pm.current
        if not cur:
            return
        top = tk.Toplevel(self.root)
        top.title("重命名项目")
        top.geometry("360x150")
        top.transient(self.root)
        ttk.Label(top, text="新名称").pack(anchor="w", padx=14, pady=(16, 4))
        v = tk.StringVar(value=cur.name)
        ttk.Entry(top, textvariable=v, width=40).pack(padx=14, fill="x")
        ttk.Label(top, text="客户 / 品牌名").pack(anchor="w", padx=14, pady=(10, 4))
        cv = tk.StringVar(value=cur.client)
        ttk.Entry(top, textvariable=cv, width=40).pack(padx=14, fill="x")

        def ok():
            cur.name = v.get().strip() or cur.name
            cur.client = cv.get().strip()
            self.pm.update(cur)
            self._refresh_project_bar()
            top.destroy()

        ttk.Button(top, text="保存", command=ok).pack(pady=10)

    def _open_project_dir(self):
        cur = self.pm.current
        if not cur:
            return
        cur.ensure_dirs()
        _open_folder(cur.root)

    # ================================================================
    #  挂载新标签页
    # ================================================================

    def _add_ops_tabs(self):
        t7 = ttk.Frame(self.notebook)
        self.notebook.add(t7, text="  选题挖掘  ")
        self._mount_tab(t7, "topic", self._create_topic_tab)

        t8 = ttk.Frame(self.notebook)
        self.notebook.add(t8, text="  复盘监控  ")
        self._mount_tab(t8, "watch", self._create_watch_tab)

        t9 = ttk.Frame(self.notebook)
        self.notebook.add(t9, text="  周报  ")
        self._mount_tab(t9, "report", self._create_report_tab)

    @staticmethod
    def _ops_tree(parent, columns, widths=None, height=10):
        """建一个带滚动条的表格，返回 (frame, tree)。"""
        f = ttk.Frame(parent)
        tree = ttk.Treeview(f, show="headings", columns=columns, height=height)
        ys = ttk.Scrollbar(f, orient="vertical", command=tree.yview)
        xs = ttk.Scrollbar(f, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        for i, c in enumerate(columns):
            w = (widths or {}).get(c) or max(70, min(240, len(c) * 14 + 20))
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="w")
        tree.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        return f, tree

    def _ops_async(self, fn, on_done):
        """后台跑，回主线程回调。"""
        def run():
            try:
                res = fn()
            except Exception as e:      # noqa: BLE001
                res = ("__error__", f"{type(e).__name__}: {e}")
            self.root.after(0, lambda: on_done(res))
        threading.Thread(target=run, daemon=True).start()

    def _ops_require_client(self) -> bool:
        if not self.client:
            messagebox.showwarning("提示", "请先在「设置」中配置 API Token")
            self.notebook.select(2)
            return False
        return True

    def _ops_current_project(self):
        cur = self.pm.current if hasattr(self, "pm") else None
        if not cur:
            messagebox.showwarning("提示", "请先新建一个项目")
            return None
        return cur

    # ================================================================
    #  Tab: 选题挖掘
    # ================================================================

    def _create_topic_tab(self, parent):
        head = ttk.LabelFrame(parent, text="低粉爆款挖掘（找「跟我同量级却爆了」的号，比看大号有用得多）",
                              padding=10)
        head.pack(fill="x", padx=8, pady=8)

        row1 = ttk.Frame(head)
        row1.pack(fill="x", pady=(0, 6))
        ttk.Label(row1, text="平台：").pack(side="left")
        self.tp_platform = ttk.Combobox(row1, values=PLATFORM_OPTIONS, state="readonly", width=8)
        self.tp_platform.set("抖音")
        self.tp_platform.pack(side="left")
        ttk.Label(row1, text="作者粉丝上限：").pack(side="left", padx=(14, 0))
        self.tp_maxfans = tk.StringVar(value="100000")
        ttk.Entry(row1, textvariable=self.tp_maxfans, width=10).pack(side="left")
        ttk.Label(row1, text="点赞下限：").pack(side="left", padx=(14, 0))
        self.tp_mindigg = tk.StringVar(value="10000")
        ttk.Entry(row1, textvariable=self.tp_mindigg, width=10).pack(side="left")
        ttk.Label(row1, text="翻页数：").pack(side="left", padx=(14, 0))
        self.tp_pages = tk.StringVar(value="2")
        ttk.Entry(row1, textvariable=self.tp_pages, width=5).pack(side="left")

        ttk.Label(head, text="关键词（每行一个，会并发挖掘）").pack(anchor="w")
        self.tp_keywords = tk.Text(head, height=3, font=("Microsoft YaHei UI", 10))
        self.tp_keywords.pack(fill="x", pady=(2, 6))

        row2 = ttk.Frame(head)
        row2.pack(fill="x")
        self.tp_mine_btn = ttk.Button(row2, text="开始挖掘", command=self._topic_mine)
        self.tp_mine_btn.pack(side="left")
        self.tp_export_btn = ttk.Button(row2, text="导出Excel", command=self._topic_export,
                                        state="disabled")
        self.tp_export_btn.pack(side="left", padx=6)
        self.tp_copy_btn = ttk.Button(row2, text="复制链接", command=self._topic_copy_links,
                                      state="disabled")
        self.tp_copy_btn.pack(side="left")
        self.tp_watch_btn = ttk.Button(row2, text="作者加入监控", command=self._topic_add_watch,
                                       state="disabled")
        self.tp_watch_btn.pack(side="left", padx=6)
        ttk.Button(row2, text="接口自检", command=self._topic_probe).pack(side="left", padx=6)
        ttk.Label(row2, text="爆粉比 = 点赞 ÷ 粉丝数，越高说明内容力越强",
                  foreground="#666666").pack(side="left", padx=10)

        f, self.tp_tree = self._ops_tree(parent, RANKED_POST_COLUMNS,
                                         widths={"标题": 260, "爆粉比": 70}, height=5)
        f.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.tp_rows: list = []

        # ---- 评论区挖掘 ----
        cm = ttk.LabelFrame(parent, text="评论区痛点挖掘（用户在问什么、骂什么、想要什么 = 免费选题库）",
                            padding=10)
        cm.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(cm, text="作品链接或作品ID（每行一个，可从上方表格复制链接）").pack(anchor="w")
        self.tp_cmt_input = tk.Text(cm, height=2, font=("Microsoft YaHei UI", 10))
        self.tp_cmt_input.pack(fill="x", pady=(2, 6))
        crow = ttk.Frame(cm)
        crow.pack(fill="x")
        self.tp_cmt_btn = ttk.Button(crow, text="挖掘评论", command=self._topic_mine_comments)
        self.tp_cmt_btn.pack(side="left")
        self.tp_cmt_export_btn = ttk.Button(crow, text="导出评论Excel",
                                            command=self._topic_export_comments,
                                            state="disabled")
        self.tp_cmt_export_btn.pack(side="left", padx=6)
        self.tp_cmt_summary = ttk.Label(crow, text="", foreground="#666666")
        self.tp_cmt_summary.pack(side="left", padx=10)

        cf, self.tp_kw_tree = self._ops_tree(cm, ["关键词", "出现次数"],
                                             widths={"关键词": 140}, height=4)
        cf.pack(side="left", fill="both", expand=True, pady=(6, 0))
        self.tp_cmt_rows: list = []

    # -- 回调 --

    def _topic_mine(self):
        if not self._ops_require_client():
            return
        cur = self._ops_current_project()
        if not cur:
            return
        kws = [l.strip() for l in self.tp_keywords.get("1.0", "end").splitlines() if l.strip()]
        if not kws:
            messagebox.showwarning("提示", "请先填写至少一个关键词")
            return
        try:
            max_fans = int(self.tp_maxfans.get())
            min_digg = int(self.tp_mindigg.get())
            pages = int(self.tp_pages.get())
        except ValueError:
            messagebox.showwarning("提示", "粉丝上限 / 点赞下限 / 翻页数 必须是数字")
            return

        platform = self.tp_platform.get()
        self.tp_mine_btn.config(state="disabled")
        self.status_var.set(f"正在挖掘 {len(kws)} 个关键词…")

        logs: list[str] = []

        def run():
            return mine_keywords_batch(self.client, platform, kws, max_fans=max_fans,
                                       min_digg=min_digg, pages=pages,
                                       on_progress=logs.append)

        def done(res):
            self.tp_mine_btn.config(state="normal")
            if isinstance(res, tuple) and res and res[0] == "__error__":
                messagebox.showerror("挖掘失败", res[1])
                self.status_var.set("挖掘失败")
                return
            self.tp_rows = res
            for i in self.tp_tree.get_children():
                self.tp_tree.delete(i)
            for r in res:
                self.tp_tree.insert("", "end", values=[str(v) for v in r.row()])
            has = bool(res)
            for b in (self.tp_export_btn, self.tp_copy_btn, self.tp_watch_btn):
                b.config(state="normal" if has else "disabled")
            self.status_var.set(f"挖掘完成：{len(res)} 条以小博大作品")
            if not res and logs:
                messagebox.showinfo("没有符合条件的作品",
                                    "可能原因：\n· 阈值设得太严（试试放宽点赞下限）\n· "
                                    "该平台暂不支持或接口返回为空\n\n"
                                    + "\n".join(logs[-6:]))

        self._ops_async(run, done)

    def _topic_export(self):
        if not self.tp_rows:
            return
        cur = self._ops_current_project()
        if not cur:
            return
        cur.ensure_dirs()
        path = filedialog.asksaveasfilename(
            title="导出爆款清单", defaultextension=".xlsx",
            initialdir=str(cur.export_dir),
            initialfile=f"低粉爆款_{time.strftime('%m%d')}.xlsx",
            filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
            wb = Workbook()
            ws = wb.active
            ws.title = "低粉爆款"
            ws.append(RANKED_POST_COLUMNS)
            for c in range(1, len(RANKED_POST_COLUMNS) + 1):
                ws.cell(row=1, column=c).font = Font(bold=True, color="FFFFFF")
                ws.cell(row=1, column=c).fill = PatternFill("solid", fgColor="2F5597")
            for r in self.tp_rows:
                ws.append([str(v) for v in r.row()])
            for i, col in enumerate(RANKED_POST_COLUMNS, start=1):
                ws.column_dimensions[chr(64 + i) if i <= 26 else "A"].width = \
                    40 if col == "标题" else 16
            wb.save(path)
            self.status_var.set(f"已导出 {len(self.tp_rows)} 条到 {path}")
            messagebox.showinfo("导出成功", f"已保存：\n{path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def _topic_copy_links(self):
        sel = self.tp_tree.selection()
        rows = [self.tp_rows[int(i)] for i in sel] if sel else self.tp_rows
        links = [r.post.url or r.post.post_id for r in rows if r.post.url or r.post.post_id]
        if not links:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(links))
        self.status_var.set(f"已复制 {len(links)} 条链接到剪贴板")

    def _topic_add_watch(self):
        cur = self._ops_current_project()
        if not cur:
            return
        sel = self.tp_tree.selection()
        rows = [self.tp_rows[int(i)] for i in sel] if sel else self.tp_rows
        n = 0
        for r in rows:
            if r.post.author_id and cur.add_watch_author(r.post.platform, r.post.author_id,
                                                         r.post.author_name):
                n += 1
        self.pm.update(cur)
        self._refresh_project_bar()
        self._ops_refresh_watch_accounts()
        self.status_var.set(f"已将 {n} 位作者加入对标监控")

    def _topic_probe(self):
        """接口自检：确认各平台真实返回结构与解析是否对得上。"""
        if not self._ops_require_client():
            return
        lines: list[str] = []

        def run():
            for disp in PLATFORM_OPTIONS:
                lines.append(f"—— {disp} ——")
                try:
                    probe_platform(self.client, disp, log=lines.append)
                except Exception as e:      # noqa: BLE001
                    lines.append(f"[异常] {disp}: {e}")
            return "\n".join(lines)

        def done(res):
            text = res[1] if isinstance(res, tuple) else str(res)
            top = tk.Toplevel(self.root)
            top.title("接口自检结果")
            top.geometry("700x420")
            txt = tk.Text(top, wrap="word", font=("Consolas", 10))
            txt.pack(fill="both", expand=True, padx=10, pady=10)
            txt.insert("1.0", text)
            txt.config(state="disabled")
            self.status_var.set("接口自检完成")

        self.status_var.set("正在自检各平台接口…")
        self._ops_async(run, done)

    def _topic_mine_comments(self):
        if not self._ops_require_client():
            return
        raw = self.tp_cmt_input.get("1.0", "end").strip()
        ids: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            # 从链接里抠出作品 ID
            if "/" in line:
                seg = [s for s in line.rstrip("/").split("/") if s]
                ids.append(seg[-1].split("?")[0])
            else:
                ids.append(line)
        if not ids:
            messagebox.showwarning("提示", "请粘贴作品链接或作品ID")
            return
        platform = self.tp_platform.get()
        self.tp_cmt_btn.config(state="disabled")
        self.status_var.set(f"正在拉取 {len(ids)} 个作品的评论…")

        def run():
            cmts = fetch_comments(self.client, platform, ids, max_each=50,
                                  on_progress=lambda m: None)
            return analyze_comments(cmts), cmts

        def done(res):
            self.tp_cmt_btn.config(state="normal")
            if isinstance(res, tuple) and res and res[0] == "__error__":
                messagebox.showerror("拉取失败", res[1])
                return
            insight, cmts = res
            self.tp_cmt_rows = cmts
            self.tp_cmt_insight = insight
            for i in self.tp_kw_tree.get_children():
                self.tp_kw_tree.delete(i)
            for w, c in insight.keywords:
                self.tp_kw_tree.insert("", "end", values=[w, c])
            self.tp_cmt_summary.config(text=insight.summary())
            self.tp_cmt_export_btn.config(state="normal" if cmts else "disabled")
            self.status_var.set(f"评论挖掘完成：{insight.summary()}")
            if not cmts:
                messagebox.showinfo("没有拉到评论",
                                    "该平台可能不支持评论接口，或作品ID不正确。\n"
                                    "可点「接口自检」确认。")

        self._ops_async(run, done)

    def _topic_export_comments(self):
        if not getattr(self, "tp_cmt_rows", None):
            return
        cur = self._ops_current_project()
        if not cur:
            return
        cur.ensure_dirs()
        path = filedialog.asksaveasfilename(
            title="导出评论分析", defaultextension=".xlsx",
            initialdir=str(cur.export_dir),
            initialfile=f"评论挖掘_{time.strftime('%m%d')}.xlsx",
            filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
            wb = Workbook()
            ws = wb.active
            ws.title = "评论明细"
            ws.append(COMMENT_EXPORT_COLUMNS)
            for c in range(1, len(COMMENT_EXPORT_COLUMNS) + 1):
                ws.cell(row=1, column=c).font = Font(bold=True, color="FFFFFF")
                ws.cell(row=1, column=c).fill = PatternFill("solid", fgColor="2F5597")
            for r in insight_to_rows(self.tp_cmt_insight):
                ws.append([str(v) for v in r])
            ws.column_dimensions["A"].width = 10
            ws.column_dimensions["B"].width = 18
            ws.column_dimensions["C"].width = 70
            ws.column_dimensions["D"].width = 10
            ws.column_dimensions["E"].width = 10
            ws.column_dimensions["F"].width = 16

            ws2 = wb.create_sheet("高频词")
            ws2.append(["关键词", "出现次数"])
            for w, c in self.tp_cmt_insight.keywords:
                ws2.append([w, c])
            wb.save(path)
            messagebox.showinfo("导出成功", f"已保存：\n{path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    # ================================================================
    #  Tab: 复盘监控
    # ================================================================

    def _create_watch_tab(self, parent):
        add = ttk.LabelFrame(parent, text="监控账号（自营账号看数据异动，对标账号看更新预警）",
                             padding=10)
        add.pack(fill="x", padx=8, pady=8)
        r = ttk.Frame(add)
        r.pack(fill="x")
        ttk.Label(r, text="平台：").pack(side="left")
        self.wt_platform = ttk.Combobox(r, values=PLATFORM_OPTIONS, state="readonly", width=8)
        self.wt_platform.set("抖音")
        self.wt_platform.pack(side="left")
        ttk.Label(r, text="账号ID / secUid：").pack(side="left", padx=(12, 0))
        self.wt_id = tk.StringVar()
        ttk.Entry(r, textvariable=self.wt_id, width=34).pack(side="left")
        ttk.Label(r, text="备注名：").pack(side="left", padx=(12, 0))
        self.wt_name = tk.StringVar()
        ttk.Entry(r, textvariable=self.wt_name, width=16).pack(side="left")
        self.wt_kind = tk.StringVar(value="对标")
        ttk.Combobox(r, textvariable=self.wt_kind, values=["自营", "对标"],
                     state="readonly", width=6).pack(side="left", padx=(12, 0))
        ttk.Button(r, text="添加", command=self._watch_add, width=8).pack(side="left", padx=8)
        ttk.Button(r, text="移除选中", command=self._watch_remove, width=10).pack(side="left")

        lf, self.wt_acc_tree = self._ops_tree(add, ["类型", "平台", "账号ID", "备注名"],
                                              widths={"账号ID": 320}, height=4)
        lf.pack(fill="x", pady=(8, 0))

        ctrl = ttk.Frame(parent)
        ctrl.pack(fill="x", padx=8)
        self.wt_check_btn = ttk.Button(ctrl, text="立即巡检一次", command=self._watch_check)
        self.wt_check_btn.pack(side="left")
        ttk.Label(ctrl, text="每次巡检会打一次数据快照，积累多次才能判断走势",
                  foreground="#666666").pack(side="left", padx=10)
        self.wt_last = ttk.Label(ctrl, text="", foreground="#666666")
        self.wt_last.pack(side="left", padx=10)

        an = ttk.LabelFrame(parent, text="数据异动预警", padding=8)
        an.pack(fill="both", expand=True, padx=8, pady=8)
        af, self.wt_an_tree = self._ops_tree(an, ANOMALY_COLUMNS,
                                             widths={"说明": 340, "作品": 180}, height=5)
        af.pack(fill="both", expand=True)
        self.wt_anomalies: list = []

        nw = ttk.LabelFrame(parent, text="竞品新作品（发现即提醒，第一时间去拆）", padding=8)
        nw.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        nf, self.wt_new_tree = self._ops_tree(
            nw, ["平台", "账号", "标题", "点赞", "发布时间", "链接"],
            widths={"标题": 300}, height=4)
        nf.pack(fill="both", expand=True)
        self.wt_new_posts: list = []

        self._ops_refresh_watch_accounts()

    def _ops_refresh_watch_accounts(self):
        if not hasattr(self, "wt_acc_tree"):
            return
        for i in self.wt_acc_tree.get_children():
            self.wt_acc_tree.delete(i)
        cur = self.pm.current if hasattr(self, "pm") else None
        if not cur:
            return
        for a in cur.own_authors:
            self.wt_acc_tree.insert("", "end", values=[
                "自营", PLATFORM_DISPLAY.get(a.get("platform"), a.get("platform")),
                a.get("author_id", ""), a.get("nickname", "")])
        for a in cur.watch_authors:
            self.wt_acc_tree.insert("", "end", values=[
                "对标", PLATFORM_DISPLAY.get(a.get("platform"), a.get("platform")),
                a.get("author_id", ""), a.get("nickname", "")])

    def _watch_add(self):
        cur = self._ops_current_project()
        if not cur:
            return
        aid = self.wt_id.get().strip()
        if not aid:
            messagebox.showwarning("提示", "请填写账号ID / secUid")
            return
        platform = self.wt_platform.get()
        nick = self.wt_name.get().strip()
        if self.wt_kind.get() == "自营":
            ok = cur.add_own_author(platform, aid, nick)
        else:
            ok = cur.add_watch_author(platform, aid, nick)
        self.pm.update(cur)
        self._refresh_project_bar()
        self._ops_refresh_watch_accounts()
        self.wt_id.set("")
        self.wt_name.set("")
        self.status_var.set("已添加" if ok else "该账号已在清单中")

    def _watch_remove(self):
        cur = self._ops_current_project()
        if not cur:
            return
        sel = self.wt_acc_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先在上方列表选中要移除的账号")
            return
        for i in sel:
            vals = self.wt_acc_tree.item(i, "values")
            kind, _, aid, _ = vals
            self._remove_by_id(cur, "own" if kind == "自营" else "watch", aid)
        self.pm.update(cur)
        self._refresh_project_bar()
        self._ops_refresh_watch_accounts()
        self.status_var.set("已移除")

    @staticmethod
    def _remove_by_id(project, kind: str, author_id: str) -> None:
        key = "own_authors" if kind == "own" else "watch_authors"
        lst = getattr(project, key)
        setattr(project, key, [a for a in lst if a.get("author_id") != author_id])

    def _watch_check(self):
        if not self._ops_require_client():
            return
        cur = self._ops_current_project()
        if not cur:
            return
        accounts = ([(a.get("platform"), a.get("author_id"), a.get("nickname", ""), "own")
                     for a in cur.own_authors]
                    + [(a.get("platform"), a.get("author_id"), a.get("nickname", ""), "watch")
                       for a in cur.watch_authors])
        if not accounts:
            messagebox.showwarning("提示", "请先添加至少一个监控账号")
            return

        self.wt_check_btn.config(state="disabled")
        self.status_var.set(f"正在巡检 {len(accounts)} 个账号…")
        store = cur.root / "监控数据"
        tower = WatchTower(store)

        def one(item):
            platform, aid, nick, kind = item
            ad = PlatformAdapter(self.client, platform)
            posts, _, _ = ad.user_posts(aid, count=20)
            fans = 0
            try:
                prof = ad.user_profile(aid)
                if prof:
                    fans = prof.fans
                    nick = prof.nickname or nick
            except Exception:
                pass
            new, anomalies = tower.record(platform, aid, nick, posts, fans)
            return {"kind": kind, "new": new, "anomalies": anomalies,
                    "nick": nick, "platform": platform, "error": ad.last_error}

        def run():
            out = []
            with ThreadPoolExecutor(max_workers=3) as ex:
                for r in ex.map(one, accounts):
                    out.append(r)
            return out

        def done(res):
            self.wt_check_btn.config(state="normal")
            if isinstance(res, tuple) and res and res[0] == "__error__":
                messagebox.showerror("巡检失败", res[1])
                return
            new_posts, anomalies, errs = [], [], []
            for r in res:
                for p in r["new"]:
                    new_posts.append((r, p))
                anomalies.extend(r["anomalies"])
                if r["error"] and not r["new"]:
                    errs.append(f"{r['platform']} {r['nick'] or r.get('kind')}: {r['error']}")

            self.wt_anomalies = anomalies
            self.wt_new_posts = new_posts
            for i in self.wt_an_tree.get_children():
                self.wt_an_tree.delete(i)
            for a in anomalies:
                self.wt_an_tree.insert("", "end", values=[str(v) for v in a.row()])
            for i in self.wt_new_tree.get_children():
                self.wt_new_tree.delete(i)
            for r, p in new_posts:
                self.wt_new_tree.insert("", "end", values=[
                    PLATFORM_DISPLAY.get(p.platform, p.platform), r["nick"],
                    p.title[:50], p.digg, p.publish_text, p.url])
            self.wt_last.config(text=f"上次巡检：{time.strftime('%H:%M:%S')}")
            self.status_var.set(
                f"巡检完成：发现 {len(new_posts)} 条新作品，{len(anomalies)} 条数据异动")
            if errs and not new_posts and not anomalies:
                messagebox.showwarning("巡检完成但无数据", "\n".join(errs[:5]))

        self._ops_async(run, done)

    # ================================================================
    #  Tab: 周报
    # ================================================================

    def _create_report_tab(self, parent):
        head = ttk.LabelFrame(parent, text="一键生成周报 / 月报（可直接发给客户）", padding=10)
        head.pack(fill="x", padx=8, pady=8)
        r = ttk.Frame(head)
        r.pack(fill="x")
        ttk.Label(r, text="统计周期：").pack(side="left")
        self.rp_days = tk.StringVar(value="7")
        ttk.Combobox(r, textvariable=self.rp_days, values=["7", "30"],
                     state="readonly", width=5).pack(side="left")
        ttk.Label(r, text="天").pack(side="left")
        self.rp_gen_btn = ttk.Button(r, text="生成报告", command=self._rep_generate)
        self.rp_gen_btn.pack(side="left", padx=12)
        self.rp_xlsx_btn = ttk.Button(r, text="导出Excel", command=self._rep_export_xlsx,
                                      state="disabled")
        self.rp_xlsx_btn.pack(side="left")
        self.rp_html_btn = ttk.Button(r, text="导出HTML", command=self._rep_export_html,
                                      state="disabled")
        self.rp_html_btn.pack(side="left", padx=6)
        ttk.Button(r, text="打开报告目录", command=self._rep_open_dir).pack(side="left", padx=6)
        self.rp_status = ttk.Label(r, text="", foreground="#666666")
        self.rp_status.pack(side="left", padx=10)

        ttk.Label(head, text="说明：报告基于「复盘监控」积累的快照数据，"
                             "账号巡检次数越多、结论越准",
                  foreground="#666666").pack(anchor="w", pady=(6, 0))

        body = ttk.LabelFrame(parent, text="报告结论", padding=10)
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.rp_text = tk.Text(body, wrap="word", font=("Microsoft YaHei UI", 10),
                               height=14)
        ys = ttk.Scrollbar(body, orient="vertical", command=self.rp_text.yview)
        self.rp_text.configure(yscrollcommand=ys.set)
        self.rp_text.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        self.rp_data = None

    def _rep_generate(self):
        cur = self._ops_current_project()
        if not cur:
            return
        try:
            days = int(self.rp_days.get())
        except ValueError:
            days = 7
        tower = WatchTower(cur.root / "监控数据")

        def run():
            return build_report(cur, tower, period_days=days)

        def done(res):
            if isinstance(res, tuple) and res and res[0] == "__error__":
                messagebox.showerror("生成失败", res[1])
                return
            data = res
            self.rp_data = data
            self.rp_text.delete("1.0", "end")
            self.rp_text.insert("1.0", f"{data.client or data.project_name}　"
                                       f"{data.period_text}报告\n"
                                       f"生成于 {data.date_text}\n\n")
            self.rp_text.insert("end", "核心指标\n")
            self.rp_text.insert("end",
                                f"　账号 {len(data.accounts)} 个　作品 {data.total_posts} 条　"
                                f"爆款 {data.viral_count} 条（{data.viral_rate_text}）　"
                                f"净涨粉 {data.total_fans_delta}\n\n")
            self.rp_text.insert("end", "本期结论\n")
            for i, h in enumerate(data.highlights, 1):
                self.rp_text.insert("end", f"　{i}. {h}\n")
            if data.best_hours:
                self.rp_text.insert("end", "\n最佳发布时段（按中位点赞排序）\n")
                for hh, n, med in data.best_hours[:6]:
                    self.rp_text.insert("end", f"　{hh:02d}:00-{hh+1:02d}:00　"
                                               f"样本 {n} 条　中位点赞 {med}\n")
            if data.anomalies:
                self.rp_text.insert("end", f"\n数据异动 {len(data.anomalies)} 条\n")
                for a in data.anomalies[:10]:
                    self.rp_text.insert("end", f"　[{a.level_text}] {a.kind}　"
                                               f"{a.title[:28]}　{a.message}\n")
            self.rp_xlsx_btn.config(state="normal")
            self.rp_html_btn.config(state="normal")
            self.rp_status.set(f"已生成 {data.period_text}报告")
            self.status_var.set("报告已生成")

        self.status_var.set("正在生成报告…")
        self._ops_async(run, done)

    def _rep_export_xlsx(self):
        cur = self._ops_current_project()
        if not cur or not self.rp_data:
            return
        cur.ensure_dirs()
        path = cur.report_dir / (
            f"{cur.client or cur.name}_{time.strftime('%Y%m%d')}_"
            f"{self.rp_days.get()}天报告.xlsx")
        out = export_excel_report(self.rp_data, path)
        if out:
            messagebox.showinfo("导出成功", f"已保存：\n{out}")
            self.status_var.set(f"报告已导出：{out}")
        else:
            messagebox.showerror("导出失败", "未能写入 Excel（可能缺少 openpyxl）")

    def _rep_export_html(self):
        cur = self._ops_current_project()
        if not cur or not self.rp_data:
            return
        cur.ensure_dirs()
        path = cur.report_dir / \
            f"{cur.client or cur.name}_{time.strftime('%Y%m%d')}_{self.rp_days.get()}天报告.html"
        out = export_html_report(self.rp_data, path)
        messagebox.showinfo("导出成功", f"已保存：\n{out}")
        self.status_var.set(f"报告已导出：{out}")
        try:
            import webbrowser
            webbrowser.open(str(out))
        except Exception:
            pass

    def _rep_open_dir(self):
        cur = self._ops_current_project()
        if not cur:
            return
        cur.ensure_dirs()
        _open_folder(cur.report_dir)

    def _ops_refresh_topic_watch(self):
        """切换项目后，选题页的监控按钮状态跟着重置。"""
        if hasattr(self, "tp_watch_btn"):
            self.tp_watch_btn.config(
                state="normal" if getattr(self, "tp_rows", None) else "disabled")
