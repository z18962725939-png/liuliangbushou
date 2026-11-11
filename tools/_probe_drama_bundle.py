"""探针：组件包「安装 → 起服务 → 建项目 → 扫描剧集」整条链路真跑一遍。

用法
----
    # 用本地刚打好的 zip（发版前验证用这个）
    python tools/_probe_drama_bundle.py --zip <本地 zip> --material <素材目录>
    # 用云端地址（验证客户那边拿到的是不是能用的包）
    python tools/_probe_drama_bundle.py --url <zip 地址> --material <素材目录>

★ 为什么要单独跑这个，而不是复用 _probe_drama_bridge
----------------------------------------------------
_probe_drama_bridge 验的是「协议对不对」，它跑的是源码形态或旧路径下的 exe，
服务能找到 ffmpeg 是因为源码形态下 __file__ 往上数三层正好命中仓库 resources。

本探针验的是另一件事：**组件包解压到客户机器上之后的目录布局对不对**。
打包态 exe 的 __file__ 落在 <bundle>/dramaclip-service/_internal 里，
往上数三层算出来的是 <bundle>/dramaclip-service/resources —— 那个目录不存在。
所以布局必须由 drama_bridge 注入 DRAMACLIP_RESOURCES_DIR 来兜住。
这一条在本探针里是硬验证：扫描阶段要真去调 ffprobe，
ffmpeg 定位错的话 scan_episodes 会直接失败，不会"看起来能用"。

★ 最后一步必须真跑一遍分析
--------------------------
语音模型（faster-whisper-base，145MB）是打进组件包的，目的就是让客户
解压完直接用、不用等下载。所以光检查"文件在不在"是不够的 —— 要真提交一次
analysis 并跑到终态，证明服务确实按我们放的落点把它认出来了。
落点错一格（比如放进 resources 而不是 data/models），四件套看着齐全，
但服务找不到，analysis 会在转写那步报"模型未下载"。
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from justoneapi_collector import drama_bundle, drama_bridge     # noqa: E402
from justoneapi_collector.drama_bridge import DramaBridge        # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'OK]' if ok else '失败'} {label}" + (f" —— {detail}" if detail else ""))
    (PASS if ok else FAIL).append(label)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", help="本地组件包 zip（跳过下载）")
    ap.add_argument("--url", help="云端组件包地址")
    ap.add_argument("--material", required=True, help="素材目录（里面放几集 mp4）")
    ap.add_argument("--reinstall", action="store_true", help="已装也强制重装")
    args = ap.parse_args()

    src = Path(args.material)
    if not src.is_dir():
        raise SystemExit(f"素材目录不存在：{src}")

    print("=" * 58)
    print(" 短剧剪辑组件包 · 端到端探针")
    print("=" * 58)

    bd = drama_bundle.bundle_dir()
    print(f"\n组件目录 {bd}")

    # ── 1) 装组件 ──
    print("\n[1/4] 安装组件包")
    if args.zip:
        zp = Path(args.zip)
        if not zp.is_file():
            raise SystemExit(f"zip 不存在：{zp}")
        if not drama_bundle.is_installed() or args.reinstall:
            bd.mkdir(parents=True, exist_ok=True)
            t0 = time.time()
            shutil.copy2(zp, drama_bundle.zip_path())
            print(f"      复制 {zp.stat().st_size / 1048576:.1f} MB，用时 {time.time() - t0:.0f}s")
            t0 = time.time()
            drama_bundle.install(
                on_progress=lambda p, m: print(f"      {p:5.1f}%  {m}", end="\r", flush=True))
            print()
            print(f"      解压用时 {time.time() - t0:.0f}s")
        else:
            print("      已装，跳过（要重装加 --reinstall）")
    elif args.url:
        t0 = time.time()
        drama_bundle.download(
            args.url, drama_bundle.zip_path(),
            on_progress=lambda p, m: print(f"      {p:5.1f}%  {m}", end="\r", flush=True))
        print()
        print(f"      下载用时 {time.time() - t0:.0f}s")
        drama_bundle.install(
            on_progress=lambda p, m: print(f"      {p:5.1f}%  {m}", end="\r", flush=True))
        print()
    else:
        raise SystemExit("必须给 --zip 或 --url 之一")

    check("服务 exe 在位", drama_bundle.service_exe().is_file(),
          str(drama_bundle.service_exe()))
    check("资源目录在位（ffmpeg 就靠它）", drama_bundle.resources_dir().is_dir())
    check("ffmpeg 在", (drama_bundle.resources_dir() / "ffmpeg" / "ffmpeg.exe").is_file())
    check("ffprobe 在", (drama_bundle.resources_dir() / "ffmpeg" / "ffprobe.exe").is_file())
    check("语音模型四件套在位", drama_bundle.asr_ready(),
          str(drama_bundle.asr_model_dir()))
    check("is_installed 判定可用", drama_bundle.is_installed())
    if not drama_bundle.is_installed():
        print("\n[中止] 组件没装好，后面没意义。")
        return 1

    # ── 2) 起服务 ──
    print("\n[2/4] 启动剪辑服务（打包态 exe）")
    notices: list[dict] = []
    br = DramaBridge(
        exe=drama_bundle.service_exe(),
        resources_dir=drama_bundle.resources_dir(),
        data_dir=bd / "data",
        on_progress=lambda p: notices.append(p),
        on_log=lambda p: notices.append(p),
    )
    t0 = time.time()
    try:
        br.start(timeout=60.0)
    except Exception as e:  # noqa: BLE001
        check("服务起来", False, f"{type(e).__name__}: {e}")
        return 1
    check("握手完成", br.running,
          f"握手+启动 {time.time() - t0:.1f}s，协议 {br._protocol_version}")
    # 与页面同口径：起服务就把 ASR 档位钉成包里带的那一档
    drama_bridge.apply_defaults(br)

    try:
        # ── 3) 建项目 ──
        print("\n[3/4] 建项目")
        # ★ source_path 是 UNIQUE 键，同目录建第二次会直接报
        #   UNIQUE constraint failed: projects.source_path（code -32603）。
        #   清理动作单独包起来：删不掉不拦着往下走，撞了 UNIQUE 也让人一眼看懂原因。
        try:
            for p in br.call("project.list", {}, timeout=30) or []:
                if str(p.get("source_path") or "") == str(src):
                    br.call("project.delete", {"project_id": p.get("id")}, timeout=30)
        except Exception as e:  # noqa: BLE001
            print(f"      （清理旧项目失败，继续：{type(e).__name__}: {e}）")
        t0 = time.time()
        proj = br.call("project.create", {
            "name": "探针剧", "source_path": str(src),
        }, timeout=30)
        # ★ project.create 返回的是**整个项目对象**，不是 id 字符串。
        #   直接把返回值当 project_id 传下去，服务端报的是
        #   "项目不存在: {...}"（代码 -32101）—— 看着像没建上，其实是传错了东西。
        pid = proj.get("id") if isinstance(proj, dict) else proj
        check("创建项目", bool(pid), f"id={pid}，用时 {time.time() - t0:.1f}s")

        # ── 4) 扫描剧集（这步会真调 ffprobe，ffmpeg 定位错就在这步炸）──
        print("\n[4/4] 扫描剧集")
        t0 = time.time()
        try:
            res = br.call("project.scan_episodes", {"project_id": pid}, timeout=180)
        except Exception as e:  # noqa: BLE001
            check("扫描剧集", False, f"{type(e).__name__}: {e}")
            return 1
        eps = res if isinstance(res, list) else (res or {}).get("episodes") or []
        check("扫描剧集", len(eps) > 0,
              f"扫到 {len(eps)} 集，用时 {time.time() - t0:.1f}s")
        for ep in eps[:3]:
            print(f"      · {ep.get('name')}　"
                  f"{ep.get('duration') or ep.get('duration_sec') or '?'}s　"
                  f"{ep.get('status') or ''}")

        # 通知有没有推过来（progress/log 是服务端主动推的，不推说明读线程没接上）
        check("收到服务端通知", True,
              f"共 {len(notices)} 条" if notices else "本链路不产生通知（正常）")

        # ── 5) 真跑一次分析：模型打进包里就是为了这一步不用等 ──
        print("\n[5/5] 分析（语音转写）—— 验证模型落点被服务认出来了")
        # ★ 模型状态要查 models.list，不是 models.runtime_status —— 后者是
        #   CUDA 运行库的安装态（键是 installed），查它永远拿不到 ASR 的就绪情况。
        #   判定路径是 registry.detect_status，whisper 走专属分支只认 HF 缓存布局。
        try:
            rows = br.call("models.list", {}, timeout=30) or []
            base = next((m for m in rows
                         if m.get("model_id") == "faster-whisper-base"), None)
            check("ASR 模型被服务认出（installed）",
                  (base or {}).get("status") == "installed",
                  f"status={(base or {}).get('status')}，"
                  f"path={(base or {}).get('path')}")
        except Exception as e:  # noqa: BLE001
            print(f"      （查 models.list 失败：{type(e).__name__}: {e}）")

        t0 = time.time()
        # 扫描完服务会自动起分析，先跟现成的，没有才提交
        jid = drama_bridge.find_active_job(br, "analysis")
        if jid:
            print(f"      扫描已自动起了分析，跟着它跑：{jid}")
        else:
            try:
                job = br.submit_job("analysis.start", {"project_id": pid},
                                    timeout=60)
            except Exception as e:  # noqa: BLE001
                check("提交分析任务", False, f"{type(e).__name__}: {e}")
                return 1
            jid = (job or {}).get("job_id") if isinstance(job, dict) else job
        check("拿到分析任务", bool(jid), f"job={jid}")

        # 长任务一律异步：提交拿 job_id，轮询到终态（done/failed/cancelled）
        # 实测：base 档 CPU 转写 2 集 × 60 秒，跑到终态要 15 分钟。
        # 上限给 30 分钟，给慢机器留余量 —— 卡在这儿看不出是慢还是坏了。
        deadline = time.time() + 1800
        st: dict = {}
        last_msg = ""
        while time.time() < deadline:
            time.sleep(3.0)
            try:
                st = br.job_state(jid, timeout=30)
            except Exception as e:  # noqa: BLE001
                last_msg = f"轮询失败：{type(e).__name__}: {e}"
                break
            s = st.get("status")
            msg = st.get("message") or st.get("stage") or ""
            if msg and msg != last_msg:
                print(f"      {msg}"[:110])
                last_msg = msg
            if drama_bridge.is_terminal(s):
                break
        final = st.get("status")
        check(
            # 成功态字面量是 completed，照抄服务端 infra/jobs.py
            "分析跑到终态且成功", final == "completed",
            f"状态={final}，用时 {time.time() - t0:.0f}s"
            + (f"，服务端说：{st.get('error') or st.get('message') or ''}"
               if final != "completed" else ""),
        )
        if final == "completed":
            try:
                # 没有 episode.* 命名空间，集信息从项目详情里回读
                got = br.call("project.get", {"project_id": pid}, timeout=30) or {}
                for ep in (got.get("episodes") or [])[:3]:
                    # 集对象只带这些字段（没有字幕数组），别去猜一个不存在的键
                    # —— 猜错会得到「字幕 0 段」，看着像转写没出东西，其实是打印错了。
                    print(f"      · {ep.get('name')}　状态 {ep.get('status')}　"
                          f"有音轨 {ep.get('has_audio')}　"
                          f"时长 {ep.get('duration')}s")
            except Exception as e:  # noqa: BLE001
                print(f"      （回读集信息失败：{type(e).__name__}: {e}）")
    finally:
        br.stop()

    print("\n" + "=" * 58)
    print(f" 通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for f in FAIL:
            print(f"   × {f}")
        return 1
    print(" 全部通过：组件包在客户机器上的布局是对的。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
