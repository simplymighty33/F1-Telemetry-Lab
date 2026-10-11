# 0004 — Qt 竞速指挥台视图层迁移

日期：2026-10-06。状态：实施候选，真实游戏 / 混合 DPI 实机验收未完成。

## 原因与决策

用户正式采用《竞速指挥台 UI 大改技术指令》，授权 PySide6 Qt Widgets + pyqtgraph。
已有原生原型和高 DPI / AA 要求是增加 GUI 依赖的具体依据。
只替换视图层；采集、解码、Raw v1/v2、Flashback、SQLite 与增量分析算法不改。
ADR 0001–0003 持续有效。Qt 初始化前采用 PassThrough，保留系统原生标题栏。

## 依赖与入口

核心 CLI 保持 Python >=3.10 与标准库安装；`[gui]` 是明确的可选依赖。
本次 GUI / Windows 打包验证使用 Python 3.13.15，不宣称最新 NumPy 在 Python 3.10 可用。
锁定版本在 packaging/requirements-qt-lock.txt；Pillow、psutil 为验收工具，不进入应用。
新入口 `python -m collector.qt_ui` / `f1-telemetry-qt`。
已有 GUI 命令暂不静默切换：`f1-telemetry-lab` / `f1-telemetry-ui` 仍是 Tk，
新增明确回退别名 `f1-telemetry-tk`。Qt 包只启动 Qt，不运行 Tk。
正式默认切换须有游戏电脑与混合 DPI 验收证据后同步更新所有别名、README 和构建入口。

## 状态与安全

三页共用服务，切页不停止采集。实时来源与手动历史来源隔离。
后台查询沿用 LatestTask；应用结果须匹配来源代号、数据库、Session、驾驶段、圈对和 revision。
Raw 构建、压缩与审计使用可取消独立任务；所有 Qt 更新在主线程。
关闭时取消提交、请求所有任务结束、service.stop，再用计时器检查 wait(0) / ready；不强杀或无限 UI wait。
设置重启先排空旧服务；模态返回后再次检查 closing / restarting。

## 绘制与能力边界

米→km、ms→s、输入→百分比只在适配层变换。
连接按原邻接与当前点连前点语义移位，独立通道有限性；不做滤波 / 缺口插值 / 官方差强制对齐。
图线显式 AA，cosmetic pen 从 1.6 DIP 与当前 DPR 重算；抽稀关闭。
地图与 ERS 曲线缺乏当前只读比较模型，显示可折叠未接入提示，不冒充独立地图 POC 已集成。

## 验收与代价

Qt onedir 比标准库 Tk 包大，许可通知与可替换动态库必须随分发保留。
源码基线 / 验收截图 / 测试日志单独记录。离屏缩放不证明真实跨屏，模拟 UDP 不证明游戏长测。
不以视觉相似度抵消功能缺失；性能代理指标不冒充物理屏幕延迟。
