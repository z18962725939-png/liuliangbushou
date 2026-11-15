# 流量捕手（TrafficCatcher）

一个 Windows 桌面客户端：面向抖音 / TikTok / 快手 / 小红书 / 视频号等平台的**内容采集与素材管理**工作台。用 PySide6 构建，单 exe 分发，内置免费版 / 完整版双形态。

## 功能一览

| 模块 | 能做什么 |
|---|---|
| 采集 | 按 UP 主 / 关键词批量采集视频信息与数据 |
| 选题挖掘 | 关键词热度和相关视频分析 |
| 批量下载 | 视频直链解析与批量下载（多平台） |
| 直播监控 | 抖音 / TikTok 直播状态监控与自动录制（ffmpeg） |
| 本地转录 | whisper.cpp GPU 转写，生成文案与字幕 |
| 数据导出 | 采集结果导出为表格 / 文案二次加工 |
| 短剧热榜 | 公开短剧热度榜单 |
| 采集报表 / 评论区 / 地域分析 | 运营向数据整理 |

## 技术栈

- **Python 3.10+**，GUI 用 **PySide6**
- **ffmpeg**：直播录制与视频处理
- **whisper.cpp**：本地 GPU 转写（CUDA）
- **PyInstaller + Inno Setup**：打包与安装器
- 发版链：COS 托管 + EdgeOne Pages + 腾讯云静态镜像 + 自动化门禁（`tools/_validate_*.py`，含 AST 与运行时打桩两层校验）

## 目录结构（节选）

```
justoneapi_collector/   # 核心业务层（采集/解析/监控/转录）
gui_qt/                 # PySide6 界面层（页面 / 组件 / 主题）
tools/                   # 发版脚本与门禁（build_site / release_all / _validate_*）
samples/                 # 界面截图与原型
```

## 说明

- 本仓库为**完整源码存档**，未附带 `README` 之外的任何文档承诺；未声明开源许可证，即默认**保留全部权利**。
- 运行需要自备各平台访问通道与 ffmpeg；密钥类配置（`tools/cos_config.json` 等）不入库，模板见 `*.example`。
- 采集功能仅供个人学习研究使用，请遵守各平台用户协议与当地法律法规。

## 版本

当前：v1.28.7（发布记录见提交历史与 `justoneapi_collector/branding.py`）
