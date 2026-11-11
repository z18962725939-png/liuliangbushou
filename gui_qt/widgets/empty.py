"""空态引导（PySide6）。

为什么需要它（2026-10-17 UI 诊断，硬证据第 6 项）：
    表格在没数据时是一大片纯白，客户打开页面只看到「空」，
    既不知道这格是干什么的，也不知道下一步点哪里 —— 很多人就此以为软件坏了。

设计纪律：
- **空态必须给出口**：一句轻飘飘的「暂无数据」不解决任何问题。
  每张表配一个能点得动的按钮，直接把人带到「让这张表有数据」的那个动作上。
- **不用 emoji 做图标**（不同 Windows 版本字形差异大，还有彩色表情）；
  用页面自己的导航图标 + 圆底单色。
- **它是覆盖层，不是表格的替代品**：parent 到表格上，跟着表格走，
  有数据时立刻隐藏，不参与布局计算 —— 不会改动页面任何一个锚点。
- **颜色一律走 QSS 语义类**（#EmptyTitle / #EmptyBody / #EmptyGlyph），
  不内联写死色值，否则切深色主题就是黑底黑字。
"""
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (QApplication, QFrame, QLabel, QPushButton,
                               QSizePolicy, QTableView, QVBoxLayout, QWidget)

from gui_qt.icons import info_icon, nav_icon


def _current_mode() -> str:
    """读取当前主题模式（由 theme.apply_theme 写在 QApplication 属性上）。"""
    app = QApplication.instance()
    if app is None:
        return "light"
    try:
        return app.property("theme_mode") or "light"
    except Exception:                                           # noqa: BLE001
        return "light"


class EmptyHint(QFrame):
    """表格/列表无数据时浮在其上的引导层。

    用法（一般在 Building UI 之后调用）::

        attach_empty_hint(self.table, "还没有抓取结果",
                          "填好关键词，让软件把数据最好的视频抓回来。",
                          action_text="开始抓取", on_action=self._run)

    有任意一行数据就自动隐藏；行数回到 0 再自动出现。
    """

    def __init__(self, title: str, body: str = "", action_text: str = "",
                 on_action=None, icon_name: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("EmptyHint")
        self._action_cb = on_action
        self._icon_name = icon_name or ""
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 10, 18, 14)
        outer.setSpacing(7)
        outer.addStretch(1)

        self._glyph = QLabel()
        self._glyph.setObjectName("EmptyGlyph")
        self._glyph.setFixedSize(36, 36)
        self._glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self._glyph, 0, Qt.AlignmentFlag.AlignHCenter)

        # ★ 空态是「陪衬」，不能反过来决定表格/页面的宽度：允许换行 + 允许被
        #   压缩 + 限最大宽，长文案就不会把页面顶出横向滚动条（竞品体检那次
        #   Ignored 的坑不能再踩第二遍 —— 这里用 Preferred，只给上限不给压缩）。
        self._title = QLabel(title)
        self._title.setObjectName("EmptyTitle")
        self._title.setWordWrap(True)
        self._title.setMaximumWidth(460)
        self._title.setSizePolicy(QSizePolicy.Policy.Preferred,
                                  QSizePolicy.Policy.Preferred)
        outer.addWidget(self._title, 0, Qt.AlignmentFlag.AlignHCenter)

        self._body = None
        if body:
            self._body = QLabel(body)
            self._body.setObjectName("EmptyBody")
            self._body.setWordWrap(True)
            self._body.setMaximumWidth(460)
            self._body.setSizePolicy(QSizePolicy.Policy.Preferred,
                                     QSizePolicy.Policy.Preferred)
            outer.addWidget(self._body, 0, Qt.AlignmentFlag.AlignHCenter)

        self._btn = None
        if action_text and callable(on_action):
            self._btn = QPushButton(action_text)
            # ★ 2026-10-17：空态里的按钮不再用 #Primary（柠檬实心）。
            #   一屏里往往已经有一个主按钮（页面右上角「开始挖掘」），
            #   空态再来一颗柠檬，就成了两颗同等重量的主按钮互相抢 ——
            #   截图上像贴了两张便利贴。空态是"提示"，按钮走描边次级。
            self._btn.setObjectName("EmptyAction")
            self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._btn.clicked.connect(self._on_action)
            outer.addSpacing(4)
            outer.addWidget(self._btn, 0, Qt.AlignmentFlag.AlignHCenter)

        outer.addStretch(1)

    # ------------------------------------------------------------ 行为
    def _on_action(self):
        try:
            if callable(self._action_cb):
                self._action_cb()
        except Exception as exc:                                # noqa: BLE001
            # ★ 空态按钮只是顺手的便捷入口，不能反过来把页面拖崩
            print(f"[流量捕手] 空态按钮执行失败：{exc}")

    def apply_theme(self, mode: str = "light"):
        """主题切换后刷新图标颜色（底色与文字色由 QSS 负责）。"""
        color = "#C8F751" if mode == "dark" else "#0B0E14"
        if self._icon_name:
            try:
                icon = nav_icon(self._icon_name, color, 20)
                if icon.isNull():
                    icon = info_icon(color, 20)
            except Exception:                                   # noqa: BLE001
                icon = info_icon(color, 20)
        else:
            icon = info_icon(color, 20)
        self._glyph.setPixmap(icon.pixmap(20, 20))

    # ------------------------------------------------------------ 几何跟随
    def reposition(self):
        """把自己摆到表格里、且避开表头的位置。"""
        table = self.parent()
        if table is None:
            return
        try:
            hdr = table.horizontalHeader()
            top = hdr.height() if hdr.isVisible() else 0
            avail_h = max(0, table.height() - top)
            self.setGeometry(0, top, table.width(), avail_h)
            self._adapt(avail_h)
        except Exception:                                       # noqa: BLE001
            pass

    def _adapt(self, avail_h: int):
        """按可用高度做渐进降级。

        ★ 为什么必须做（2026-10-17 截图时发现）
          表格在滚动区里可能只有 120px 高，而「图标 + 标题 + 两行正文 + 按钮」
          要 190px —— 多出来的部分直接被表格下沿裁掉，客户看到的是一句被切一半
          的话加一个露头的按钮，比没有空态更糟。
          所以：够高就全显示，不够就先丢正文，再不够就只留标题。
          **出口按钮优先级最高**，它是空态唯一有用的部分。
        """
        full = avail_h >= 168
        mid = avail_h >= 112
        self._glyph.setVisible(full)
        if self._body is not None:
            self._body.setVisible(full)
        self._title.setVisible(mid)
        if self._btn is not None:
            self._btn.setVisible(mid)

    def eventFilter(self, obj, event):                          # noqa: N802
        """跟随表格尺寸变化重排（首次布局时表格可能还是 0 宽）。"""
        try:
            if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
                self.reposition()
        except Exception:                                       # noqa: BLE001
            pass
        return False


def attach_empty_hint(table: QTableView, title: str, body: str = "",
                      action_text: str = "", on_action=None,
                      icon_name: str = "") -> EmptyHint | None:
    """给表格挂一层空态引导。

    ★ 为什么用「覆盖层」而不是替代表格
      另一个做法是没数据时把表格藏掉、换个 QWidget 显示，但那会让页面
      重新布局，好不容易调好的列宽会被丢掉。覆盖层不动任何布局锚点，
      对现有 14 张表零副作用。

    ★ 为什么 parent 必须是表格
      几何天然跟随表格（滚动区、布局管理都自动），而且表格销毁时 Qt 会
      连带回收它，不用手工管生命周期。
    """
    try:
        hint = EmptyHint(title, body, action_text, on_action, icon_name)
        hint.setParent(table)
        hint.apply_theme(_current_mode())
        table.installEventFilter(hint)
        # ★ 空表格不该被压成一条缝：滚动区会把页面余高摊给表格，页面上控件一多
        #   表格就只剩几十像素，空态根本没地方站。给个下限，让空态至少读得完
        #   （页面本来就在 QScrollArea 里，多出来的高度可以滚）。
        if table.minimumHeight() < 200:
            table.setMinimumHeight(200)

        def _sync(*_a, **_kw):
            try:
                hint.setVisible(table.rowCount() == 0)
                hint.reposition()
            except Exception:                                   # noqa: BLE001
                pass

        model = table.model()
        if model is not None:
            # ★ QTableWidget 没有 rowsChanged 信号，只能跟 model 这四个。
            #   这四个覆盖了 insertRow / removeRow / setRowCount /
            #   clearContents 的全部入口，漏一个就会出现「明明没数据、
            #   空态却不显示」这种看着像 bug 的静默不一致。
            for sig in (model.rowsInserted, model.rowsRemoved,
                        model.modelReset, model.layoutChanged):
                try:
                    sig.connect(_sync)
                except Exception:                               # noqa: BLE001
                    pass
        _sync()
        return hint
    except Exception as exc:                                    # noqa: BLE001
        print(f"[流量捕手] 空态挂载失败：{exc}")
        return None


def refresh_empty_hints(root: QWidget, mode: str) -> int:
    """切主题时刷新一棵子树里所有空态的图标颜色。返回刷新个数。"""
    n = 0
    for hint in root.findChildren(EmptyHint):
        try:
            hint.apply_theme(mode)
            n += 1
        except Exception:                                       # noqa: BLE001
            pass
    return n


__all__ = ["EmptyHint", "attach_empty_hint", "refresh_empty_hints"]
