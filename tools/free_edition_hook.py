# -*- coding: utf-8 -*-
"""PyInstaller runtime hook —— 把 exe 标记为「免费版」（2026-10-23）。

为什么用 runtime hook 而不是打包前改源码：
    branding.py 里 APP_IS_FREE 决定导航、标题、授权区、是否碰接口，全套
    行为都挂在它上面。最直觉的做法是"打包免费版之前把 branding.py 改成
    free，打完再改回来"——但那是个定时炸弹：只要有一次忘了还原，下一次
    发布出去的就是「文件名写着完整版、里面其实只有两个功能」的包，而且
    编译期不会有任何报错，要等客户投诉才发现。

    runtime hook 是 PyInstaller 在 exe 启动时、**早于任何业务模块 import**
    执行的脚本。在这里塞一个环境变量，branding.py 一 import 就读到它，
    源码文件永远不动，所以永远不存在"忘了还原"这回事。

只在 spec 里被挂载（见 justoneapi_collector.spec 的 _runtime_hooks）：
    TRAFFIC_EDITION=free 打包 → 挂这个 hook → 运行时 APP_IS_FREE=True
    TRAFFIC_EDITION 未设     → 不挂 hook      → 运行时 APP_IS_FREE=False
"""
import os

os.environ["TRAFFIC_EDITION"] = "free"
