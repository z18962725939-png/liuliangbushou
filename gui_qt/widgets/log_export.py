# -*- coding: utf-8 -*-
"""一键导出日志（2026-11-15 新增）：任何页面都能一键把「诊断报告 + 最近日志」
打成 zip 放到桌面，供客户微信发给作者。

为什么要单独一个模块
--------------------
设置页早就有「一键发日志」，但埋在「系统」档卡片里 —— 客户遇到问题时
（比如 TikTok 录制失败）根本不知道要去那儿点。这次改版把出口搬到两个
客户一眼能看到的位置：

1. 主窗口侧栏底部：常驻「导出日志」按钮，与「使用教程」平级；
2. 直播页录制失败详情窗：出问题的现场直接给「📋 一键导出日志」按钮。

实现上只做这一件事：调 `justoneapi_collector.support.make_support_bundle()`
（诊断报告 + 最近 6 份轮转日志的 zip，见该模块的零服务器方案说明），
Worker 后台跑避免卡 UI，成功后打开桌面定位并弹窗告知。

★ 调用约定（防止再踩 settings_page 踩过的坑）：
    make_support_bundle 的参数名是 **include_logs**（不是 with_logs）。
    本模块集中这一处调用，别的页面一律 `from gui_qt.widgets.log_export
    import export_logs / show_failure_dialog`，不要再各自手写 support 调用。

★ 为什么不用 QMessageBox 承载「导出日志」按钮（2026-11-15 实拍）
  QMessageBox 里的「Show Details...」是 Qt 硬编码英文，客户看不懂；
  自定义按钮的布局要等窗口管理器介入才正确，离屏/异常尺寸下按钮会被挤到
  顶部与正文重叠。所以这里自绘 LogDialog：中文按钮、详情可选中复制、
  布局完全可控，浅色/深色都跟着主题走。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QVBoxLayout)

from gui_qt.pages._base import Worker
from gui_qt import diagnostics

# 每档：窗口底 / 边线 / 标题字 / 正文字 / 次要字 / 详情区底与字
_THEME = {
    "light": dict(bg="#FFFFFF", border="#E5E7EB", title="#111827",
                  body="#374151", muted="#6B7280",
                  detail_bg="#F9FAFB", detail_fg="#374151"),
    "dark": dict(bg="#161A22", border="#2A2F3A", title="#E5E7EB",
                 body="#C7CBD3", muted="#9AA4B2",
                 detail_bg="#0F1319", detail_fg="#C7CBD3"),
}


def _colors() -> dict:
    """按当前主题取色（与 guide_dialog 一样读 QApplication 的 theme_mode）。"""
    mode = "light"
    app = QApplication.instance()
    if app is not None:
        try:
            mode = str(app.property("theme_mode") or "light")
        except Exception:                                    # noqa: BLE001
            mode = "light"
    return _THEME["dark" if mode == "dark" else "light"]


# ----------------------------------------------------------------------
#  自绘提示窗
# ----------------------------------------------------------------------

class LogDialog(QDialog):
    """带「一键导出日志」出口的提示窗（失败详情 / 导出成功共用）。

    summary ：一句话结论（加粗）
    body    ：补充说明（可换行）
    detail  ：完整原因（多行技术细节）；非空时出现「显示详细信息」开关，
              展开后是只读文本框，客户可以全选复制
    note    ：底部灰色小字（比如作者微信在哪看）
    """

    def __init__(self, parent=None, title: str = "提示",
                 summary: str = "", body: str = "", detail: str = "",
                 note: str = "", show_export: bool = True,
                 kind: str = "录制失败", tone: str = "warn"):
        super().__init__(parent)
        self.setObjectName("LogDialog")
        self.setWindowTitle(title)
        self._detail_text = (detail or "").strip()
        self._expanded = False
        self._kind = kind
        self._tone = "success" if tone == "success" else "warn"
        self._ctx = getattr(parent, "ctx", None)
        self._status = getattr(parent, "status", None)
        self._build(title, summary, body, note, show_export)
        self._apply_theme()

    # ---------------------------------------------------------- 构建
    def _build(self, title, summary, body, note, show_export):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 16)
        root.setSpacing(10)
        self.setMinimumWidth(520)

        head = QHBoxLayout()
        head.setSpacing(8)
        # 成功是「✓ 绿」，失败是「! 黄」—— 成功窗挂感叹号会让客户以为又出错了
        badge = QLabel("✓" if self._tone == "success" else "!")
        badge.setFixedSize(22, 22)
        badge.setAlignment(Qt.AlignCenter)
        badge.setObjectName("LogBadge")
        head.addWidget(badge, 0, Qt.AlignTop)
        self._title = QLabel(title)
        self._title.setObjectName("LogTitle")
        head.addWidget(self._title, 1)
        root.addLayout(head)

        self._summary = QLabel(summary or "")
        self._summary.setWordWrap(True)
        self._summary.setObjectName("LogSummary")
        if summary:
            root.addWidget(self._summary)

        self._body = QLabel(body or "")
        self._body.setWordWrap(True)
        self._body.setObjectName("LogBody")
        if body:
            root.addWidget(self._body)

        # 详情区：默认隐藏，展开后给只读文本框（可全选复制，比 QMessageBox
        # 的 details 区更容易操作）
        self._detail_box = QPlainTextEdit()
        self._detail_box.setReadOnly(True)
        self._detail_box.setPlainText(self._detail_text)
        self._detail_box.setMinimumHeight(140)
        self._detail_box.setVisible(False)
        root.addWidget(self._detail_box)

        if note:
            self._note = QLabel(note)
            self._note.setWordWrap(True)
            self._note.setObjectName("LogNote")
            root.addWidget(self._note)

        root.addSpacing(4)
        foot = QHBoxLayout()
        foot.setSpacing(8)
        if show_export:
            self._export_btn = QPushButton("📋 一键导出日志")
            self._export_btn.setObjectName("Primary")
            self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._export_btn.clicked.connect(lambda *_: self._do_export())
            foot.addWidget(self._export_btn)
        if self._detail_text:
            self._detail_btn = QPushButton("显示详细信息")
            self._detail_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._detail_btn.clicked.connect(lambda *_: self._toggle_detail())
            foot.addWidget(self._detail_btn)
        foot.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        foot.addWidget(close_btn)
        root.addLayout(foot)
        self._foot = foot                      # 成功窗要往这行插「打开所在文件夹」

    # ---------------------------------------------------------- 主题
    def _apply_theme(self):
        """用 palette 上底色而不是 QSS：给父窗口设样式表会连带重置子控件样式
        （项目里踩过 QWidget{background} 通配的坑），palette 不经过 QSS，最稳。"""
        c = _colors()
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor(c["bg"]))
        pal.setColor(QPalette.WindowText, QColor(c["body"]))
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        # ★ 只对自身设极少量内联样式（不写 QWidget 通配），其余交给全局 QSS
        if self._tone == "success":
            badge_qss = ("QLabel#LogBadge{border-radius:11px;font-weight:700;"
                         "font-size:13px;color:#065F46;background:#D1FAE5;}")
        else:
            badge_qss = ("QLabel#LogBadge{border-radius:11px;font-weight:700;"
                         "font-size:13px;color:#92400E;background:#FDE68A;}")
        self.setStyleSheet(
            f"QDialog#LogDialog{{border:1px solid {c['border']};}}"
            f"QLabel#LogTitle{{font-size:15px;font-weight:700;color:{c['title']};"
            f"background:transparent;}}"
            f"QLabel#LogSummary{{font-size:13px;font-weight:600;color:{c['title']};"
            f"background:transparent;}}"
            f"QLabel#LogBody{{font-size:12px;color:{c['body']};background:transparent;}}"
            f"QLabel#LogNote{{font-size:12px;color:{c['muted']};background:transparent;}}"
            + badge_qss
        )
        self._detail_box.setStyleSheet(
            f"QPlainTextEdit{{background:{c['detail_bg']};color:{c['detail_fg']};"
            f"border:1px solid {c['border']};border-radius:6px;"
            f"font-family:Consolas,'Microsoft YaHei';font-size:12px;padding:6px;}}"
        )

    # ---------------------------------------------------------- 行为
    def _toggle_detail(self):
        self._expanded = not self._expanded
        self._detail_box.setVisible(self._expanded)
        self._detail_btn.setText("收起详情" if self._expanded else "显示详细信息")
        self.adjustSize()

    def _do_export(self):
        export_logs(self, self._ctx, kind=self._kind, status=self._status)

    def show_nonmodal(self):
        self.setModal(False)
        self.adjustSize()
        self.show()
        return self


# ----------------------------------------------------------------------
#  打包
# ----------------------------------------------------------------------

def export_logs(parent=None, ctx=None, kind: str = "日志",
                note: str = "", status=None,
                success_note: str = "") -> None:
    """把诊断报告 + 最近日志打包成 zip 放到桌面，并打开定位。

    parent ：父窗口（弹窗居中用，可为 None）
    ctx    ：AppContext（取当前 Token 进诊断报告，可为 None）
    kind   ：进 zip 文件名与「反馈内容.txt」的类型标记
    note   ：用户描述（侧栏一键导出时为空）
    status ：可选 StatusLine，导出期间显示「正在打包…」、成功/失败变色
    success_note：成功弹窗的额外说明（帮客户知道下一步发给谁）

    ★ 卡 UI 问题：environment_report 会调 nvidia-smi 检测显卡、zip 最多
      压 6×5MB 日志 —— 主线程跑会有可感知的顿挫，所以丢 Worker。
    """
    # parent 可能是页面也可能是主窗口，token 都在 AppContext.settings 里
    if ctx is None:
        ctx = getattr(parent, "ctx", None)
    token = ""
    try:
        token = str((ctx.settings.get("token") if ctx is not None else "") or "")
    except Exception:                                        # noqa: BLE001
        pass

    if status is not None:
        try:
            status.setText("正在打包日志（含诊断报告），请稍候…")
            status.mark("idle")
        except Exception:                                    # noqa: BLE001
            pass

    def run(worker):
        from justoneapi_collector import support
        return support.make_support_bundle(
            kind=kind, note=note, include_logs=True,
            current_token=token)

    w = Worker(run)

    def _on_done(path):
        from justoneapi_collector import support
        try:
            support.open_in_explorer(path)
        except Exception:                                    # noqa: BLE001
            pass
        if status is not None:
            try:
                status.setText(f"日志已导出到桌面：{path.name}，请发给作者")
                status.mark("ok")
            except Exception:                                # noqa: BLE001
                pass
        show_result_dialog(parent, path, success_note)

    def _on_error(text):
        # P0：打包日志本身就是「客户已经遇到问题」的强信号，记一条脱敏摘要。
        # 只记固定枚举，绝不把异常文本 / 路径塞进事件。
        try:
            diagnostics.record_event(ctx, "log_export_failed",
                                     step="pack", result="fail")
        except Exception:                                    # noqa: BLE001
            pass
        if status is not None:
            try:
                status.setText(f"日志导出失败：{text}")
                status.mark("error")
            except Exception:                                # noqa: BLE001
                pass
        try:
            LogDialog(
                parent, title="日志导出失败",
                summary="打包日志时出错了，没能生成压缩包。",
                body="可以先点「设置 → 系统档 → 反馈与日志」里的"
                     "「打开日志目录」，把整个文件夹压缩后手动发给我们。",
                detail=str(text or ""), show_export=False,
            ).show_nonmodal()
        except Exception:                                    # noqa: BLE001
            box = QMessageBox(parent) if parent else QMessageBox()
            box.setIcon(QMessageBox.Critical)
            box.setWindowTitle("日志导出失败")
            box.setText(f"打包日志时出错：{text}")
            box.setModal(False)
            box.show()

    w.done.connect(_on_done)
    w.error.connect(_on_error)
    w.start()


def show_result_dialog(parent=None, path=None, success_note: str = ""):
    """导出成功提示：路径已复制到剪贴板，可直接去微信粘贴发送。"""
    try:
        QApplication.clipboard().setText(str(path))
    except Exception:                                        # noqa: BLE001
        pass
    name = getattr(path, "name", str(path))
    try:
        dlg = LogDialog(
            parent, title="日志已导出",
            summary=f"诊断包已放到桌面：{name}",
            body="完整路径已复制到剪贴板（打开微信直接粘贴即可）。"
                 "压缩包里是运行日志 + 环境信息，作者拿到就能精准定位问题。",
            note=success_note,
            show_export=False,
            tone="success",
        )
        # 「打开所在文件夹」放在底部（导出成功时客户最想干的就是看一眼文件）
        try:
            from justoneapi_collector import support
            btn = QPushButton("打开所在文件夹")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda *_: support.open_in_explorer(path))
            dlg._foot.insertWidget(dlg._foot.count() - 1, btn)
        except Exception:                                    # noqa: BLE001
            pass
        dlg.show_nonmodal()
        return dlg
    except Exception:                                        # noqa: BLE001
        box = QMessageBox(parent) if parent else QMessageBox()
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("日志已导出")
        box.setText(f"诊断包已放到桌面：\n{name}")
        box.setModal(False)
        box.show()
        return box


def show_failure_dialog(parent=None, title: str = "出错了",
                        summary: str = "", body: str = "",
                        detail: str = "", kind: str = "录制失败",
                        note: str = "") -> LogDialog | None:
    """失败详情窗：结论 + 完整原因（可复制）+「📋 一键导出日志」。

    live_page 的录制失败场景用它替换原来的 QMessageBox —— 原窗的
    「Show Details...」是英文、没有关闭按钮、导出入口也无处安放。

    ★ P0（2026-11-15）：详情末尾自动附带一份「运行体检结论」——版本、
      授权状态、凭证有无、出口模式、ffmpeg、日志目录可写。客户不必再
      描述「我用不了」，作者也不用靠猜。结论不含机器码/账号/路径。
      同时记一条匿名故障摘要（默认只落本机，开关关闭时绝不联网）。
    """
    ctx = getattr(parent, "ctx", None)
    try:
        detail = (detail or "") + diagnostics.quick_health_block(ctx)
    except Exception:                                        # noqa: BLE001
        pass
    try:
        diagnostics.record_event(ctx, "failure", step="failure_dialog",
                                 code=kind, result="fail")
    except Exception:                                        # noqa: BLE001
        pass
    try:
        dlg = LogDialog(parent, title=title, summary=summary, body=body,
                        detail=detail, note=note, show_export=True, kind=kind)
        dlg.show_nonmodal()
        return dlg
    except Exception:                                        # noqa: BLE001
        return None
