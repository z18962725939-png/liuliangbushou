#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2 任务流门禁：作业簿的隐私红线、六个步骤映射、原子写与开机降级。

钉死这些，是因为这一层最容易被日后"顺手加个字段"毁掉：
· 一旦有人往作业簿塞 Token / 客户名，它就从本机进度表变成泄露源；
· 一旦步骤页面名和实际导航对不上，「下一步」按钮会把客户送到空白页；
· 一旦读文件崩溃，首页直接白屏——比没有这个功能还糟。

运行：python tools/_validate_taskflow.py
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def check(cond, name, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def read(rel):
    p = ROOT / rel
    return p.read_text(encoding="utf-8") if p.is_file() else ""


print("=" * 64)
print("P2 任务流 / 轻量作业簿门禁")
print("=" * 64)

# ---------------------------------------------------------------- 1 静态
print("\n1) 隐私红线（作业簿不许变成敏感信息收集器）")
tf_src = read("gui_qt/taskflow.py")
check(bool(tf_src), "taskflow.py 存在")
check("import os" in tf_src and "tempfile" in tf_src and "os.replace" in tf_src,
      "原子写（临时文件 + os.replace）")
check("fsync" in tf_src, "落盘前 fsync（断电不留半截 JSON）")
check("MAX_BOOK_BYTES" in tf_src, "作业簿有大小上限")

# 从源码 AST 取白名单三元组，避免门禁自己复制一份字符串导致两处漂移
tree = ast.parse(tf_src)
consts = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1 \
            and isinstance(node.targets[0], ast.Name):
        name = node.targets[0].id
        if name in ("BOOK_FIELDS", "JOB_FIELDS", "STEP_FIELDS", "STEPS"):
            try:
                consts[name] = ast.literal_eval(node.value)
            except Exception:                                  # noqa: BLE001
                pass
fields = " ".join(str(consts.get(k, "")) for k in
                  ("BOOK_FIELDS", "JOB_FIELDS", "STEP_FIELDS")).lower()
for bad in ("token", "secret", "password", "license", "machine",
            "customer", "api_key", "apikey", "credential", "cookie"):
    check(bad not in fields, f"字段白名单不含 {bad}")

check("cookie" not in fields and "douyin_cookie" not in tf_src,
      "作业簿不碰抖音 cookie")
check("render_summary" in tf_src
      and ("Path(art).name" in tf_src or "Path(x.strip()).name" in tf_src),
      "复制清单只留产物文件名，不带本机路径")

# ---------------------------------------------------------------- 2 步骤映射
print("\n2) 六步流水线 → 现有导航页（不新增第 20 页）")
from gui_qt import taskflow as tf                              # noqa: E402

steps = consts.get("STEPS") or []
check(len(steps) == 6, "流水线正好 6 步", f"实际 {len(steps)}")
keys = [s[0] for s in steps]
check(len(set(keys)) == 6, "步骤 key 不重复")
check(all(len(s) == 4 and all(s) for s in steps), "每步四要素齐全")

main_src = read("gui_qt/main_window.py")
nav_block = main_src[main_src.find("NAV_GROUPS = ["):main_src.find("FREE_NAV_GROUPS")]
page_names = set(re.findall(r'"([^"]+)"', nav_block))
for key, title, page, hint in steps:
    check(page in page_names, f"步骤 {key} 的页面「{page}」在导航里",
          f"可用：{sorted(page_names)}")
check("工作台" not in [s[2] for s in steps], "没有让作业步骤跳回工作台自己")

# 作业模块不许自己 import Qt / 业务模块（否则启动顺序一乱就崩）
imports = re.findall(r"^\s*(?:from|import)\s+([\w\.]+)", tf_src, re.M)
check(not any(i.startswith(("PySide6", "PyQt", "gui_qt.pages", "requests"))
              for i in imports), "核心模块纯标准库（门禁可脱离 GUI 跑）")

# ---------------------------------------------------------------- 3 运行时
print("\n3) 运行时断言（临时目录，不碰用户真实作业簿）")
check(tf.self_check() == [], "模块自检通过", str(tf.self_check()))

with tempfile.TemporaryDirectory(prefix="tc_taskflow_gate_") as td:
    p = Path(td) / "taskflow.json"

    # 空簿读取
    b = tf.load(p)
    check(b["jobs"] == [] and b["active_id"] == "", "文件不存在时返回空簿")

    # 新建 + 完成 + 下一步
    job = tf.create(b, "门禁作业")
    check(job["id"] and len(b["jobs"]) == 1 and b["active_id"] == job["id"],
          "新建作业并设为当前")
    check(len(job["steps"]) == 6 and all(not s["done"] for s in job["steps"]),
          "新作业的六步全未完成")
    check(tf.next_step(job)["page"] == steps[0][2], "下一步=第一页")
    tf.mark_step(b, job["id"], steps[0][0], done=True,
                 artifact="C:/Users/someone/视频.mp4", note="挑好了")
    j = tf.active_job(b)
    check(tf.progress(j) == (1, 6, 16), "进度计算正确", str(tf.progress(j)))
    check(tf.next_step(j)["page"] == steps[1][2], "下一步推进到第二页")
    check("C:/Users" not in tf.render_summary(j), "清单不含本机路径")
    check("视频.mp4" in tf.render_summary(j), "清单保留产物文件名")

    # 写 / 读往返
    check(tf.save(b, p), "保存成功")
    chk = json.loads(p.read_text(encoding="utf-8"))
    check("token" not in chk and "machine" not in chk, "落盘内容无敏感键")
    b2 = tf.load(p)
    check(len(b2["jobs"]) == 1
          and b2["jobs"][0]["steps"][0]["artifact"] == "C:/Users/someone/视频.mp4",
          "读写往返保真（含产物名）")

    # 注入攻击：手工塞敏感字段，读回来必须被洗掉
    evil = dict(chk)
    evil["token"] = "SECRET"
    evil["jobs"] = [dict(chk["jobs"][0], api_key="KEY", machine_id="M")] 
    (Path(td) / "evil.json").write_text(json.dumps(evil), encoding="utf-8")
    clean = tf.load(Path(td) / "evil.json")
    joined = json.dumps(clean, ensure_ascii=False)
    check("SECRET" not in joined and "KEY" not in joined and "M" not in joined,
          "外部塞进来的敏感字段被清洗", joined[:120])

    # 损坏降级
    badp = Path(td) / "bad.json"
    badp.write_bytes(b"{not json at all")
    check(tf.load(badp)["jobs"] == [], "损坏文件降级为空簿")
    check(list(Path(td).glob("bad.bad-*.json")), "损坏文件另存留底，不直接覆盖")

    # 删除与自动切换
    b3 = tf.empty_book()
    j1 = tf.create(b3, "甲")
    j2 = tf.create(b3, "乙")
    check(tf.delete_job(b3, j2["id"]) and b3["active_id"] == j1["id"],
          "删当前作业后自动切到另一个")
    tf.delete_job(b3, j1["id"])
    check(b3["active_id"] == "" and tf.active_job(b3) is None,
          "删空后回到无作业态")

# ---------------------------------------------------------------- 3b 第二片
print("\n3b) 产物归位 / 目录快照（P2 第二片，零侵入业务页）")
tf_src2 = tf_src
check("DIR_ROLE" in tf_src2 and "STEP_EXTS" in tf_src2,
      "目录角色映射与文件类型白名单存在")
check(all(k in ("dl_save_dir", "lm_save_dir", "transcribe_out_dir")
          for k in tf.DIR_ROLE), "目录角色只用现成设置键，不引入新键")
check(all(v in tf.STEP_KEYS for v in tf.DIR_ROLE.values()),
      "目录角色都映射到合法步骤")
check("Path(x).name" in tf_src2 and "_ART_SEP" in tf_src2,
      "清单逐段只留文件名（多产物也不泄本机路径）")
check("不侵入任何业务页" in tf_src2, "核心模块明确零侵入边界")

with tempfile.TemporaryDirectory(prefix="tc_taskflow_collect_") as td:
    base = Path(td)
    dl = base / "dl"
    out = base / "out"
    dl.mkdir()
    out.mkdir()
    for n in ("A.mp4", "B.mp4", "old.mp4"):
        (dl / n).write_bytes(b"x")
    (out / "稿.txt").write_bytes(b"x")
    import os as _os
    import time as _time
    frozen = _time.time()
    _os.utime(dl / "old.mp4", (frozen - 200 * 3600, frozen - 200 * 3600))
    # B 放在 10 小时前：48h 窗内、1h 窗外，用来验证时间窗收紧会筛掉它
    _os.utime(dl / "B.mp4", (frozen - 10 * 3600, frozen - 10 * 3600))
    ctx_dirs = {"dl_save_dir": str(dl), "transcribe_out_dir": str(out),
                "token": "SECRET", "base_url": "http://evil"}

    b = tf.empty_book()
    j = tf.create(b, "归位测试")
    check(tf.snapshot_dirs(b, j["id"], ctx_dirs) == 2,
          "快照只收两个目录键（token/base_url 被挡）")
    saved = tf.apply_dirs(b, j["id"])
    check(set(saved) == set(tf.DIR_ROLE.keys()) or
          set(saved).issubset(set(tf.DIR_ROLE.keys())),
          "apply_dirs 只返回目录键")
    check("token" not in saved and "SECRET" not in json.dumps(saved),
          "快照与导出都不含 token")

    r1 = tf.scan_artifacts(b, j["id"], ctx_dirs, now=frozen)
    check(r1["attached"] == 3, "归位 3 个新产物（旧文件被时间窗过滤）", str(r1))
    check(r1["by_step"] == {"material": 2, "transcribe": 1},
          "产物按目录角色挂到对应步骤", str(r1["by_step"]))
    r2 = tf.scan_artifacts(b, j["id"], ctx_dirs, now=frozen)
    check(r2["attached"] == 0, "重复归位不产生双份（按文件名去重）")
    mat = next(s for s in tf.active_job(b)["steps"] if s["key"] == "material")
    names = [Path(x).name for x in mat["artifact"].split(" | ")]
    check(set(names) == {"A.mp4", "B.mp4"}, "一个步骤可挂多个产物", str(names))
    summary = tf.render_summary(tf.active_job(b))
    check(str(dl) not in summary and "C:" not in summary,
          "多产物清单仍不带本机路径")
    check("A.mp4" in summary and "B.mp4" in summary,
          "多产物在清单里逐段列全")

    # 24h 时间窗参数生效
    b4 = tf.empty_book()
    j4 = tf.create(b4, "窗口测试")
    r3 = tf.scan_artifacts(b4, j4["id"], ctx_dirs, hours=1, now=frozen)
    check(r3["attached"] < r1["attached"], "时间窗收紧后归位数减少")

    # 目录不存在 / 权限异常：静默跳过，不抛异常
    b5 = tf.empty_book()
    j5 = tf.create(b5, "坏目录")
    r4 = tf.scan_artifacts(b5, j5["id"],
                           {"dl_save_dir": str(base / "不存在")}, now=frozen)
    check(r4["attached"] == 0, "不存在的目录安全跳过")

    # 归位后落盘重载：多产物不被截断
    tf.save(b, base / "b.json")
    reload_b = tf.load(base / "b.json")
    reload_mat = next(s for s in tf.active_job(reload_b)["steps"]
                      if s["key"] == "material")
    check(len(reload_mat["artifact"].split(" | ")) == 2,
          "多产物重载后未截断")

print("\n4) 工作台接线（不新增导航 / 遵守 UI 契约）")
print("\n4) 工作台接线（不新增导航 / 遵守 UI 契约）")
home = read("gui_qt/pages/home_page.py")
check("from gui_qt import taskflow" in home or "import taskflow" in home,
      "工作台引入作业模块")
check("_panel_job" in home and "当前作业" in home, "工作台有作业卡")
check("_refresh_job" in home and "self._refresh_job()" in home,
      "刷新钩子已接（含切主题）")
check("connect_guarded(self.job_next_btn.clicked" in home
      and "connect_guarded(self.job_done_btn.clicked" in home,
      "作业按钮走 connect_guarded（禁裸 connect）")
check("QInputDialog" in home, "新建/切换用轻量对话框，不新增页面")
check("_on_job_collect" in home and "归位产物" in home,
      "工作台有「归位产物」入口")
check("_apply_job_dirs" in home and "snapshot_dirs" in home,
      "切作业应用目录快照、新建作业时拍快照")
check("if not str(s.get(k) or \"\").strip():" in home,
      "快照只补空值（不冲掉客户手填的目录）")
check("connect_guarded" in home and "归位产物" in home,
      "归位按钮走 connect_guarded")
check("三张状态卡" not in home or "授权状态" in home,
      "原有三张状态卡没被作业卡挤掉")
check("def _goto" in home and "switch_to" in home, "跳转入口保持可用")

chain = read("tools/release_all.py")
check("_validate_taskflow.py" in chain, "门禁已挂进发版链")

print("\n" + "=" * 64)
print(f"结果：{PASS} 通过 / {FAIL} 失败")
print("=" * 64)
sys.exit(1 if FAIL else 0)
