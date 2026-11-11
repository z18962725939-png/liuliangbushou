"""音视频转文本页（PySide6）。

功能：设备选择 + 显卡检测 + 引擎一键安装 + 模型下载/导入、待转文件列表、
输出目录、开始/停止转写（后台）、运行日志与进度。业务逻辑复用 transcribe 模块。
"""
import sys
from pathlib import Path

from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog,
                               QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout, QWidget)
from PySide6.QtGui import QDesktopServices

# ★ 2026-10-31 重建补回：分档切换控件（Segmented）的 import 在源码重建中丢失，
#   页面一打开就 NameError —— 见 drama_page 的同款用法。
from gui_qt.widgets.kit import Segmented
from ._base import (StatusLine, Worker, append_log, connect_guarded, guide_banner,
                    log_panel, page_title, row_widget, vertical_card)

from justoneapi_collector.transcribe import (
    CUDA_BUNDLES, DEFAULT_MODEL, MODELS, SUPPORTED_AUDIO_EXT, Transcriber,
    TranscribeTask, bootstrap_engine, detect_gpu, engine_problem, human_size,
    import_engine_zip, import_model_file, install_engine, install_model,
    list_media_files, pick_cuda_bundle, scan_media_folder, speed_hint,
    status_text, whisper_home,
)

# ★ 2026-10-31 重建补回：下面这些 import 在源码反向重建时丢失，
#   页面一打开就 NameError（按 pyflakes 的 undefined name 定位）。
from PySide6.QtCore import Qt, QUrl
from gui_qt.widgets.progress import FlowProgressBar
from ._base import require_feature


class TranscribePage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.files = []          # list[Path]
        self._running = False
        # 正在下载引擎时持有 downloader，让「停止」能真正取消下载。
        # ★ v1.28.2：以前下载 439 MB CUDA 引擎期间停止按钮是灰的，
        #   客户只能对着卡住的进度条干等甚至强杀进程。
        self._engine_dl = None
        self._engine_stopped = False
        # 「添加本地目录」记住上次选的文件夹，下次对话框直接从那儿打开
        self._last_folder = ""
        self._build_ui()
        self._refresh_status()
        # 开箱即用：释放内置 CPU 引擎。
        # 延迟到事件循环启动后执行，避免在构造期 / 无头环境下启动后台线程导致崩溃。
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._bootstrap_engine)

    def _bootstrap_engine(self):
        Worker(lambda w: bootstrap_engine("cpu")).start()

    def _build_ui(self):
        # ★ v1.28.2：整页按钮统一走 connect_guarded。以前只有「停止」接了兜底，
        #   其余全是裸 connect —— 槽里一旦抛异常，PySide6 只把 traceback 打到
        #   stderr，打包后没控制台，客户看到的就是「按钮点了没反应」。
        #   self.status 在 lambda 执行时才取值，这里定义没问题。
        def wire(btn, slot, label):
            connect_guarded(btn.clicked, lambda *_: slot(),
                            on_error=lambda m: self.status.setText(m), label=label)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        root.addWidget(page_title("音视频转文本"))
        root.addWidget(guide_banner("音视频转文本", self))

        # ★ 2026-10-27：整页 1367px 分成两档 —— 「开始转写」/「引擎与模型」。
        #   原来引擎区、模型参数、文件列表、输出、日志五块平铺，客户每次
        #   开这一页都要先滚过 200px 的配置才能看到「添加文件」。
        #   而引擎和模型是一年配一次的，主流程是天天用的 —— 权重不该一样。
        # ★ 用 setVisible 切换而不是 QStackedWidget：后者高度取最高子页，
        #   切到矮的那档时高度照样被撑着，省不下来。
        seg_row = QHBoxLayout()
        seg_row.setSpacing(8)
        self.step_seg = Segmented(
            [("开始转写", "main"), ("引擎与模型", "setup")],
            mode="light", parent=self)
        self.step_seg.changed.connect(self._sync_step)
        seg_row.addWidget(self.step_seg)
        seg_row.addStretch()
        root.addLayout(seg_row)

        self._view_main = QWidget(self)
        main_l = QVBoxLayout(self._view_main)
        main_l.setContentsMargins(0, 0, 0, 0)
        main_l.setSpacing(14)
        self._view_setup = QWidget(self)
        setup_l = QVBoxLayout(self._view_setup)
        setup_l.setContentsMargins(0, 0, 0, 0)
        setup_l.setSpacing(14)
        self._view_setup.setVisible(False)     # 默认停在主流程档

        # 引擎区
        eng, eng_l = vertical_card("转写引擎（首次使用点「检测显卡并一键安装」）", self)
        dev_row = QHBoxLayout()
        self.device = QComboBox(); self.device.addItems(["CPU 转录（任何电脑都能跑，慢）",
                                                         "显卡转录（CUDA，需 N 卡，快 10 倍+）"])
        self.device.setCurrentIndex(0)
        self.device.currentIndexChanged.connect(self._on_change)
        dev_row.addWidget(self.device)
        self.gpu_label = QLabel("显卡：未检测")
        self.gpu_label.setStyleSheet("color:#6B7280;font-size:12px;")
        dev_row.addWidget(self.gpu_label)
        dev_row.addStretch()
        eng_l.addLayout(dev_row)

        btn_row = QHBoxLayout()
        b1 = QPushButton("🔍 检测显卡并一键安装")
        b2 = QPushButton("打开引擎目录")
        b3 = QPushButton("导入本地引擎包")
        # ★ 这三个原来只 addWidget、没接任何槽：点了完全没反应。
        #   「导入本地引擎包」更是上一轮慢速下载提示里许给客户的出口，必须真能用。
        wire(b1, self._detect_install, "检测显卡")
        wire(b2, self._open_engine_dir, "打开引擎目录")
        wire(b3, self._import_engine_zip, "导入本地引擎包")
        btn_row.addWidget(b1); btn_row.addWidget(b2); btn_row.addWidget(b3)
        btn_row.addStretch()
        eng_l.addLayout(btn_row)
        self.engine_status = QLabel("")
        self.engine_status.setStyleSheet("color:#6B7280;font-size:12px;")
        eng_l.addWidget(self.engine_status)
        # 引擎不完整时的详细原因（缺哪个库、体积多大、怎么修）。平时隐藏，
        # 只在这一刻才占位 —— 永远显示一行"一切正常"只会变成噪音。
        self._problem_label = QLabel("")
        self._problem_label.setWordWrap(True)
        self._problem_label.setStyleSheet("color:#B45309;font-size:12px;")
        self._problem_label.setVisible(False)
        eng_l.addWidget(self._problem_label)
        root.addWidget(eng)

        # 模型与参数
        mb, mb_l = vertical_card("模型与参数", self)
        m_row = QHBoxLayout()
        m_row.addWidget(QLabel("模型："))
        self.model = QComboBox()
        # ★ 下拉项里带了一长串说明，QComboBox 默认按最长项算 sizeHint，
        #   会把整页撑出横向滚动条（1180 宽窗口下卡片右侧被切）。
        #   限制内容长度按字符数算，完整说明在 tooltip 里照样能看到。
        self.model.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model.setMinimumContentsLength(22)
        self.model_keys = list(MODELS.keys())
        for k in self.model_keys:
            m = MODELS[k]
            self.model.addItem(f"{k}（{m['size_mb']}MB / 约 {m['vram_gb']}G 显存）")
            self.model.setItemData(self.model.count() - 1,
                                   f"{k}\n{m['desc']}", Qt.ItemDataRole.ToolTipRole)
        cur = self.ctx.get_setting("transcribe_model", DEFAULT_MODEL)
        self.model.setCurrentIndex(self.model_keys.index(cur) if cur in self.model_keys else 0)
        self.model.currentIndexChanged.connect(self._on_change)
        m_row.addWidget(self.model, 1)
        bm = QPushButton("下载模型")
        bmi = QPushButton("导入本地模型")
        wire(bm, self._install_model, "下载模型")
        wire(bmi, self._import_model, "导入本地模型")
        m_row.addWidget(bm); m_row.addWidget(bmi)
        mb_l.addLayout(m_row)

        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("语言："))
        self.lang = QComboBox(); self.lang.addItems(["zh", "auto", "en"])
        self.lang.setCurrentText(self.ctx.get_setting("transcribe_lang", "zh"))
        lang_row.addWidget(self.lang)
        self.srt = QCheckBox("同时生成 srt 字幕")
        self.srt.setChecked(bool(self.ctx.get_setting("transcribe_srt", False)))
        lang_row.addWidget(self.srt)
        lang_row.addStretch()
        mb_l.addLayout(lang_row)
        self.speed = QLabel("")
        self.speed.setStyleSheet("color:#6B7280;font-size:12px;")
        mb_l.addWidget(self.speed)
        root.addWidget(mb)

        # 待转文件
        fb, fb_l = vertical_card("待转写的音视频", self)
        fctrl = QHBoxLayout()
        # ★ v1.28.2 客户要的「本地目录」按钮：以前只能一个个选文件，或扫描
        #   软件自己的录制/下载目录；客户素材放在任意文件夹时得手动全选。
        #   现在选一次文件夹（含子文件夹）就能批量导入。
        self.add_folder_btn = QPushButton("添加本地目录")
        b_add = QPushButton("添加文件")
        b_scan = QPushButton("扫描录制/下载目录")
        b_rm = QPushButton("移除选中")
        b_clr = QPushButton("清空")
        wire(self.add_folder_btn, self._add_folder, "添加本地目录")
        wire(b_add, self._add_files, "添加文件")
        wire(b_scan, self._scan, "扫描目录")
        wire(b_rm, self._remove_selected, "移除选中")
        wire(b_clr, self._clear, "清空列表")
        fctrl.addWidget(self.add_folder_btn); fctrl.addWidget(b_add)
        fctrl.addWidget(b_scan); fctrl.addWidget(b_rm); fctrl.addWidget(b_clr)
        fctrl.addStretch()
        fb_l.addLayout(fctrl)
        self.file_list = QListWidget()
        # ★ 配「添加本地目录」：一次可能进来上百个文件，默认单选的话客户想
        #   删掉其中几个只能一个个点。允许 Ctrl / Shift 多选后，
        #   「移除选中」一次就能清掉一批。
        self.file_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        fb_l.addWidget(self.file_list, 1)
        # ★ _refresh_empty() 早就写好了，但它读的 file_empty 在源码重建时丢了，
        #   控件从没被创建、方法也从没被调用 —— 空列表就是一块纯白，客户不知道
        #   下一步点哪。用 QSS 语义类 Caption，别内联 color（深色会看不见）。
        self.file_empty = QLabel(
            "还没有待转写的音视频\n点「添加本地目录」可一次导入\n整个文件夹里的音视频")
        self.file_empty.setObjectName("Caption")
        self.file_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_empty.setWordWrap(True)
        fb_l.addWidget(self.file_empty, 1)
        root.addWidget(fb, 1)

        # 输出
        ob, ob_l = vertical_card("输出", self)
        orow = QHBoxLayout()
        orow.addWidget(QLabel("输出目录："))
        self.out_dir = QLineEdit(self.ctx.get_setting("transcribe_out_dir", "") or "")
        orow.addWidget(self.out_dir, 1)
        b_browse = QPushButton("浏览")
        b_open = QPushButton("打开输出目录")
        wire(b_browse, self._choose_out, "选择输出目录")
        wire(b_open, self._open_out_dir, "打开输出目录")
        orow.addWidget(b_browse); orow.addWidget(b_open)
        ob_l.addLayout(orow)
        ob_l.addWidget(QLabel("留空 = 与源视频放在同一目录"))
        root.addWidget(ob)

        # 执行
        ab = QHBoxLayout()
        self.start_btn = QPushButton("▶ 开始转写"); self.start_btn.setObjectName("Primary")
        wire(self.start_btn, self._start, "开始转写")
        self.stop_btn = QPushButton("■ 停止"); self.stop_btn.setEnabled(False)
        # ★ v1.28.2：这是下载 439MB 卡住时客户唯一的出口，槽里一旦抛异常，
        #   PySide6 只把 traceback 打到 stderr（打包后没控制台）⇒ 又变成「点了没反应」。
        #   clicked 会带一个 checked 布尔参，用 *_ 吃掉再转调无参的 _stop。
        connect_guarded(self.stop_btn.clicked, lambda *_: self._stop(),
                        on_error=lambda m: self.status.setText(m), label="停止按钮")
        ab.addWidget(self.start_btn); ab.addWidget(self.stop_btn)
        self.progress = FlowProgressBar(); self.progress.setValue(0)
        ab.addWidget(self.progress, 1)
        self.status = StatusLine("就绪")
        ab.addWidget(self.status)
        root.addLayout(ab)

        # 日志
        lb, lb_l = vertical_card("运行日志", self)
        self.log = log_panel()
        lb_l.addWidget(self.log, 1)
        root.addWidget(lb, 1)
        self._refresh_empty()

    # ---------------------------------------------------------------- 空态
    def _refresh_empty(self):
        """列表空 → 显示引导句；有文件 → 收起（两者互斥占同一块位置）。"""
        empty = self.file_list.count() == 0
        self.file_empty.setVisible(empty)
        self.file_list.setVisible(not empty)

    # ---------------------------------------------------------------- 分档
    def _sync_step(self, key: str):
        """两档互斥显示。key 取 'main' / 'setup'。"""
        self._view_main.setVisible(key != "setup")
        self._view_setup.setVisible(key == "setup")

    def apply_theme(self, mode: str):
        """分段控件是自绘的（不读 QSS），切主题必须转交。

        ★ 主窗口会遍历各页调 apply_theme，页面没有这个方法时那一页的
          自绘控件就会孤零零留在旧配色上（和下载页当初漏掉时同一个坑）。
        """
        self._mode = mode
        if getattr(self, "step_seg", None) is not None:
            self.step_seg.apply_theme(mode)
        if getattr(self, "log_fold", None) is not None:
            self.log_fold.apply_theme(mode)

    # ----------------------------------------------------------------
    def _cur_model(self) -> str:
        i = self.model.currentIndex()
        return self.model_keys[i] if 0 <= i < len(self.model_keys) else DEFAULT_MODEL

    def _on_change(self):
        self.ctx.settings["transcribe_device"] = "cuda" if self.device.currentIndex() == 1 else "cpu"
        self.ctx.settings["transcribe_model"] = self._cur_model()
        self.ctx.settings["transcribe_lang"] = self.lang.currentText()
        self.ctx.settings["transcribe_srt"] = self.srt.isChecked()
        self.ctx.settings["transcribe_out_dir"] = self.out_dir.text().strip()
        self._refresh_status()

    def _refresh_status(self):
        dev = "cuda" if self.device.currentIndex() == 1 else "cpu"
        self.engine_status.setText(status_text(dev, self._cur_model()))
        self.speed.setText(speed_hint(dev, self._cur_model()))
        # 引擎不完整时，状态栏下面补一句"到底缺什么、怎么修"
        problem = engine_problem(dev)
        if problem and getattr(self, "_problem_label", None) is not None:
            self._problem_label.setText(problem)
            self._problem_label.setVisible(True)
        elif getattr(self, "_problem_label", None) is not None:
            self._problem_label.setVisible(False)

    # ---------------------------------------------------------- 引擎安装
    def _detect_install(self):
        """先检测（后台）→ 主线程确认 → 再后台下载安装。

        ★ v1.14.6 改了三处：
          1. 不再一检测到显卡就闷头下 439 MB，先告诉用户体积让他决定；
          2. 驱动不支持 12.4 时不再装个跑不起来的包，直接切 CPU 并说明原因；
          3. 原来在 worker 线程里 `device.setCurrentIndex()` 改 UI，且
             `currentIndexChanged.disconnect()` 会永久断掉联动 —— 现在都回主线程做。
        """
        def probe(worker):
            gpu = detect_gpu()
            worker.emit_log(f"显卡检测：{gpu.summary() if gpu.found else gpu.reason}")
            return gpu

        w = Worker(probe)
        w.log.connect(lambda m: append_log(self.log, m))
        w.done.connect(self._after_gpu_probe)
        w.error.connect(lambda e: append_log(self.log, f"检测显卡出错：{e}"))
        w.start()

    def _after_gpu_probe(self, gpu):
        from PySide6.QtWidgets import QMessageBox

        kind = "cpu"
        if gpu is None or not gpu.found or not gpu.cuda:
            reason = (gpu.reason if gpu is not None else "") or "没检测到可用的 NVIDIA 显卡"
            QMessageBox.information(
                self, "将使用 CPU 转写",
                f"{reason}\n\nCPU 引擎很小（约 2 MB，随安装包自带），"
                f"现在装上，任何电脑都能转写，只是速度比显卡慢。")
            self.device.setCurrentIndex(0)
        else:
            self.gpu_label.setText(f"显卡：{gpu.summary()}")
            key, why = pick_cuda_bundle(gpu)
            if not key:
                QMessageBox.warning(self, "这张显卡暂时用不了显卡转写", why + "\n\n已切换到 CPU 转写。")
                self.device.setCurrentIndex(0)
            else:
                bundle = CUDA_BUNDLES[key]
                yes = QMessageBox.question(
                    self, "安装显卡转写引擎",
                    f"{why}\n\n"
                    f"要下载：{bundle['asset']}\n"
                    f"体积：约 {bundle['size_mb']} MB（{bundle['note']}）\n"
                    f"来源：GitHub 国内镜像，失败自动换源\n"
                    f"安装位置：{whisper_home()}\n\n"
                    f"只装一次，以后打开就能用，不用重复下载。\n"
                    f"（现在不装也不影响：转写时会自动改用随包自带的 CPU 引擎，"
                    f"结果一样，只是慢一些。）\n\n现在开始吗？")
                if yes != QMessageBox.StandardButton.Yes:
                    self.status.setText("已取消安装")
                    return
                kind = "cuda"
                self.device.setCurrentIndex(1)
        self._install_engine_async(kind)

    def _install_engine_async(self, kind: str):
        who = "显卡" if kind == "cuda" else "CPU"
        self.status.setText(f"正在下载{who}引擎…（不想等可点「停止」，切到 CPU 转录直接开始）")
        # ★ v1.28.2：下载期间把停止按钮点亮，并交出 downloader 供取消。
        #   439 MB 在只剩一条慢镜像时要十几分钟，没有出口是纯粹把客户困住。
        self._engine_stopped = False
        self.stop_btn.setEnabled(True)
        self.start_btn.setEnabled(False)
        self.progress.setValue(0)

        def job(worker):
            def prog(done, total):
                pct = (done * 100.0 / total) if total else 0.0
                worker.emit_progress(
                    pct, f"下载{who}引擎 {pct:.0f}%（{done / 1048576:.0f}/{(total or 0) / 1048576:.0f} MB）")

            def hand_over(dl):
                self._engine_dl = dl

            return install_engine(kind, progress=prog,
                                  log=lambda m: worker.emit_log(m),
                                  on_downloader=hand_over)

        w = Worker(job)
        # ★ Qt 槽抛异常＝静默卡死，一律走 connect_guarded
        connect_guarded(w.progress,
                        lambda p, m: (self.progress.setValue(p), self.status.setText(m)),
                        label="引擎下载进度")
        connect_guarded(w.log, lambda m: append_log(self.log, m), label="引擎下载日志")
        connect_guarded(w.done, lambda r: self._on_engine_installed(kind, r),
                        label="引擎安装完成")
        connect_guarded(w.error,
                        lambda e: (append_log(self.log, f"引擎安装失败：{e}"),
                                   self._on_engine_installed(kind, (False, str(e)))),
                        label="引擎安装异常")
        w.start()
        self._install_worker = w

    def _stop_engine_download(self) -> bool:
        """停止正在进行的引擎下载。返回是否确实停掉了一次下载。"""
        dl = getattr(self, "_engine_dl", None)
        if dl is None:
            return False
        try:
            dl.cancel()
        except Exception:                                  # noqa: BLE001
            pass
        self._engine_dl = None
        self._engine_stopped = True
        self.stop_btn.setEnabled(False)
        self.start_btn.setEnabled(True)
        self.progress.setValue(0)
        self.status.setText("已停止下载。把「设备」切到「CPU 转录」即可直接开始转写")
        append_log(self.log, "已停止下载引擎。CPU 引擎随软件自带、不用联网，"
                            "把「设备」切到「CPU 转录」就能直接转写（结果一样，只是慢一些）。")
        return True

    def _on_engine_installed(self, kind: str, result):
        from PySide6.QtWidgets import QMessageBox

        # 无论是成功、失败还是被停止，都要把按钮状态复位
        stopped = bool(getattr(self, "_engine_stopped", False))
        self._engine_stopped = False
        self._engine_dl = None
        self.stop_btn.setEnabled(False)
        self.start_btn.setEnabled(True)

        ok, msg = (result if isinstance(result, tuple) and len(result) == 2 else (False, str(result)))
        self.progress.setValue(100 if ok else 0)
        self._refresh_status()
        if ok:
            self.status.setText(f"{'显卡' if kind == 'cuda' else 'CPU'}引擎已就绪，可以开始转写")
            append_log(self.log, f"引擎安装成功：{msg}")
        else:
            self.status.setText("引擎未安装，可改用 CPU 转录")
            append_log(self.log, f"引擎安装失败：{msg}")
            if stopped:
                # 用户自己点的停止，不要再弹一个吓人的失败框
                return
            # ★ 失败弹窗要给可操作的下一步：显卡引擎不是转写的前置条件，
            #   随包自带 CPU 引擎，别让客户以为软件坏了。
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("引擎没装好，但可以照常转写")
            box.setText(str(msg))
            box.setInformativeText(
                "显卡引擎只影响速度，不影响能不能用。\n"
                "点「改用 CPU 转写」即可立刻开始（引擎随软件自带、不用联网，"
                "结果一样，只是慢一些）；也可以稍后再点「检测显卡并一键安装」。")
            box.addButton("改用 CPU 转写", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("知道了", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is box.buttons()[0]:
                bootstrap_engine("cpu")
                self.device.setCurrentIndex(0)
                self._refresh_status()
                self.status.setText("已切到 CPU 转录，添加文件后即可开始")
                append_log(self.log, "已切换到 CPU 转录：随软件自带的引擎已就绪，可直接开始转写。")

    def _install_model(self):
        key = self._cur_model()
        def run(worker):
            return install_model(key, progress=lambda p, m: worker.emit_log(m))
        w = Worker(run)
        w.log.connect(lambda m: append_log(self.log, m))
        w.done.connect(lambda r: append_log(self.log, f"模型安装：{r}"))
        w.error.connect(lambda e: append_log(self.log, f"模型安装失败：{e}"))
        w.start()

    # --------------------------------------------------- 文件列表
    def _add_files(self):
        # ★ 过滤器以前只列 6 种扩展名，而 list_media_files / 目录扫描支持 14 种，
        #   客户手里的 mkv / aac / ts 在「添加文件」里根本选不到。统一到同一集合。
        exts = " ".join(f"*{e}" for e in sorted(SUPPORTED_AUDIO_EXT))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择音视频", getattr(self, "_last_folder", "") or "",
            f"音视频 ({exts})")
        for p in paths:
            self._add_file(Path(p))
        self._refresh_empty()

    def _add_folder(self):
        """★ v1.28.2：选一个本地文件夹（含子文件夹），批量导入里面的音视频。

        扫描本身是同步的，但 scan_media_folder 会剪掉系统/缓存目录，并且有
        「遍历 3 万条 / 导入 1 千个」两道上限，普通素材目录都是毫秒级；
        先 processEvents 把「正在扫描」画出来，客户不会误以为又卡死了。
        """
        d = QFileDialog.getExistingDirectory(
            self, "选择本地目录（会连子文件夹一起找音视频）",
            getattr(self, "_last_folder", "") or "")
        if not d:
            return
        self._last_folder = d
        self.status.setText(f"正在扫描目录：{d}")
        QApplication.processEvents()
        found, note = scan_media_folder(Path(d))
        added = 0
        for p in found:
            if p in self.files:
                continue
            self._add_file(p)
            added += 1
        self._refresh_empty()
        if not found:
            msg = note or "这个目录里没找到可转写的音视频"
            self.status.setText(msg)
            append_log(self.log, f"添加本地目录：{d} → {msg}")
            return
        text = f"已从目录导入 {added} 个音视频（{Path(d).name}）"
        dup = len(found) - added
        if dup:
            text += f"，跳过已存在 {dup} 个"
        if note:
            text += f"；{note}"
        self.status.setText(text)
        append_log(self.log, f"添加本地目录：{d} → {text}")

    def _scan(self):
        dirs = []
        if self.ctx.downloader:
            d = getattr(self.ctx.downloader, "save_dir", None)
            if d:
                dirs.append(Path(d))
        root = Path(self.out_dir.text().strip() or ".")
        if root.exists():
            dirs.append(root)
        found = list_media_files(dirs) if dirs else []
        for p in found:
            self._add_file(p)
        append_log(self.log, f"扫描到 {len(found)} 个媒体文件")
        self._refresh_empty()

    def _add_file(self, p: Path):
        if p in self.files:
            return
        self.files.append(p)
        size = human_size(p.stat().st_size) if p.exists() else "-"
        item = QListWidgetItem(f"{p.name}  [{size}]")
        item.setData(1, str(p))
        self.file_list.addItem(item)

    def _remove_selected(self):
        for it in self.file_list.selectedItems():
            p = Path(it.data(1))
            self.files = [f for f in self.files if f != p]
            self.file_list.takeItem(self.file_list.row(it))
        self._refresh_empty()

    def _clear(self):
        self.files.clear()
        self.file_list.clear()
        self._refresh_empty()

    def _choose_out(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self.out_dir.setText(d)
            self._on_change()

    # --------------------------------------------- 本地目录 / 本地包
    def _open_folder(self, d: Path, tip: str) -> bool:
        """统一打开目录：不存在就先建，失败给人话，绝不「点了没反应」。"""
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception as e:                                  # noqa: BLE001
            self.status.setText(f"这个目录打不开：{e}")
            append_log(self.log, f"打开目录失败：{d} → {e}")
            return False
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))
        self.status.setText(tip)
        return True

    def _open_engine_dir(self):
        self._open_folder(whisper_home(), f"已打开引擎目录：{whisper_home()}")

    def _open_out_dir(self):
        raw = self.out_dir.text().strip()
        if raw:
            d = Path(raw)
            tip = f"已打开输出目录：{d}"
        elif self.files:
            # 输出目录留空时，转写结果本来就写在源视频旁边，打开那儿才找得到
            d = self.files[0].parent
            tip = "输出目录留空，已打开第一个源视频所在目录"
        else:
            self.status.setText(
                "还没选输出目录：转写结果会和源视频放在同一目录，添加文件后再点这里打开")
            append_log(self.log, "打开输出目录：尚未设置输出目录，也没有待转写文件。")
            return
        self._open_folder(d, tip)

    def _start_import(self, title: str, job, what: str):
        """本地包导入放后台：439MB 解压 / 1.6GB 模型复制不能把界面卡成白窗。"""
        self.status.setText(title)
        append_log(self.log, what + "（后台进行中，界面不会卡住）")
        w = Worker(job)
        connect_guarded(w.done, lambda r: self._on_import_done(what, r),
                        on_error=lambda m: self.status.setText(m), label="导入完成")
        connect_guarded(w.error,
                        lambda e: self._on_import_done(what, (False, str(e))),
                        on_error=lambda m: self.status.setText(m), label="导入异常")
        w.start()
        self._import_worker = w

    def _on_import_done(self, what: str, result):
        ok, msg = (result if isinstance(result, tuple) and len(result) == 2
                   else (False, str(result)))
        self._refresh_status()
        if ok:
            self.status.setText("导入并校验通过，可以开始转写")
            append_log(self.log, f"{what}：成功 → {msg}")
        else:
            self.status.setText(f"导入失败：{msg}")
            append_log(self.log, f"{what}：失败 → {msg}")

    def _import_engine_zip(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "选择已下载好的引擎压缩包",
            getattr(self, "_last_folder", "") or "", "引擎压缩包 (*.zip)")
        if not p:
            return
        kind = "cuda" if self.device.currentIndex() == 1 else "cpu"
        self._last_folder = str(Path(p).parent)
        self._start_import(
            f"正在导入{'显卡' if kind == 'cuda' else 'CPU'}引擎包…",
            lambda w: import_engine_zip(Path(p), kind),
            f"导入本地引擎包：{Path(p).name}")

    def _import_model(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "选择已下载好的 ggml 模型文件",
            getattr(self, "_last_folder", "") or "",
            "模型文件 (*.bin);;所有文件 (*)")
        if not p:
            return
        key = self._cur_model()
        self._last_folder = str(Path(p).parent)
        self._start_import(
            f"正在导入模型 {key}…",
            lambda w: import_model_file(Path(p), key),
            f"导入本地模型：{Path(p).name}")

    # --------------------------------------------------- 执行
    def _start(self):
        # ★ 付费闸门（v1.21.0）：转写是付费功能。放在最前面，
        #   免费用户点「开始转写」先看到付费须知，而不是装半天引擎再被拦。
        #   安装引擎 / 下载模型不拦：那是本机准备动作，先备好，激活后直接能用。
        if not require_feature(self, "transcribe", "音视频转文字"):
            return
        if not self.files:
            self.status.setText("请先添加要转写的音视频")
            return
        if self.ctx.demo:
            self.status.setText("演示模式：未连接转写引擎，请在实际环境运行")
            return
        device = "cuda" if self.device.currentIndex() == 1 else "cpu"
        # ★ 开跑前拦一道：显卡引擎缺运行库时，硬跑会弹出 Windows 的
        #   「由于找不到 cublas64_xx.dll，无法继续执行代码」系统框，进程卡在那儿，
        #   用户只能点「确定」然后看着任务失败。这里提前说清并改用 CPU。
        if device == "cuda":
            problem = engine_problem("cuda")
            if problem:
                from PySide6.QtWidgets import QMessageBox
                bootstrap_engine("cpu")     # 随包自带，先补一次再判断能不能降级
                if engine_problem("cpu"):
                    QMessageBox.warning(self, "显卡引擎不可用", problem)
                    self.status.setText("显卡引擎不可用，请先安装引擎")
                    return
                QMessageBox.information(
                    self, "本次改用 CPU 转写",
                    problem + "\n\n本次先用 CPU 转写（结果完全一样，只是慢一些）。"
                    "把显卡引擎装好后会自动走显卡。")
                self.device.setCurrentIndex(0)      # 触发 _on_change，偏好也一起更新
                device = "cpu"
        out = self.out_dir.text().strip() or None
        self._running = True
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress.setValue(0)

        def run(worker):
            from justoneapi_collector._ffmpeg import find_ffmpeg
            tr = Transcriber(ffmpeg=find_ffmpeg())
            total = len(self.files)
            ok_n = 0
            for i, p in enumerate(self.files):
                if not self._running:
                    worker.emit_log("用户已停止")
                    break
                task = TranscribeTask(src=p, model=self._cur_model(), device=device,
                                      lang=self.lang.currentText(),
                                      out_dir=Path(out) if out else None)
                worker.emit_progress((i) / total * 100, f"转写中 ({i+1}/{total})：{p.name}")
                worker.emit_log(f"开始：{p.name}")
                res = tr.run(task, progress=lambda pc, m: worker.emit_progress(
                    (i + pc / 100) / total * 100, m), log=lambda m: worker.emit_log(m))
                if getattr(res, "txt", None):
                    ok_n += 1
                    worker.emit_log(f"完成：{res.txt}")
                else:
                    worker.emit_log(f"失败：{getattr(res,'msg','')}")
            return ok_n, total

        w = Worker(run)
        w.progress.connect(lambda p, m: (self.progress.setValue(int(p)), self.status.setText(m)))
        w.log.connect(lambda m: append_log(self.log, m))
        w.done.connect(lambda r: (self._finish(r)))
        w.error.connect(lambda e: (append_log(self.log, f"错误：{e}"), self._finish((0, 0))))
        w.start()
        self._worker = w

    def _finish(self, result):
        self._running = False
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        ok_n, total = result
        self.progress.setValue(100)
        self.status.setText(f"转写完成：成功 {ok_n}/{total}")

    def _stop(self):
        # 同一个按钮两种含义：下载引擎时＝取消下载，转写时＝停止转写。
        # 优先判断下载，否则客户点「停止」只会看到一句「已发送停止信号」，
        # 而 439 MB 还在后台照下不误。
        if self._stop_engine_download():
            return
        self._running = False
        self.status.setText("已发送停止信号")
