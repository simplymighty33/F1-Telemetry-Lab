# Qt 字体角色改造 — 2026-10-06

这次只改 Qt 界面的字体策略和验证，不改品牌、图标、窗口布局、业务算法或数据格式。传统 Tk 入口不变。

## 唯一角色表

字号是 Qt 逻辑像素，不乘 DPR。角色定义在 `collector/qt_ui/theme.py`，使用不可变 `TypeRole`；未知角色直接抛 `KeyError`，不保留旧的裸字号接口。

| 角色 | 字号 | 请求字重 | 首选字体 | tnum |
|---|---:|---:|---|---|
| caption | 12 | 400 | Microsoft YaHei UI | 否 |
| axis | 12 | 400 | Segoe UI | 是 |
| body | 14 | 400 | Microsoft YaHei UI | 否 |
| body_strong | 14 | 600 | Microsoft YaHei UI | 否 |
| summary | 16 | 400 | Microsoft YaHei UI | 否 |
| section | 16 | 600 | Microsoft YaHei UI | 否 |
| context | 18 | 600 | Microsoft YaHei UI | 否 |
| page_title | 20 | 600 | Microsoft YaHei UI | 否 |
| brand | 20 | 600 | Segoe UI | 否 |
| lap_value | 24 | 600 | Segoe UI | 是 |
| timing_compact | 50 | 600 | Segoe UI | 是 |
| timing_display | 68 | 600 | Segoe UI | 是 |
| log | 12 | 400 | Consolas | 否 |

`apply_type(widget, role)` 同时设置字体和 `typeRole` 属性。该属性只用于诊断，不参与来源身份、采集或分析逻辑。菜单、正文表格和选择器继承14px正文；Qt/Windows原生文件对话框继续使用平台字体。log 为纯技术日志预留，本次没有把含中文的业务表格改为等宽字体。

## 文件与控件

- `theme.py`：角色表、新字体工厂、PlainText 标签、显式 `tnum=1`；保留 PreferAntialias、颜色、QSS间距、缓存与曲线画笔算法。
- `pages.py`：三个页面主标题20；分区16、会话上下文18、统计摘要16 Regular；统一按钮14 DemiBold；紧凑/宽屏计时50/68并同步角色属性。
- `main_window.py`：品牌20 Segoe UI；来源、帮助、状态栏12；导航、来源工具和设置按钮14 DemiBold；关闭标题18，任务结果标题16。仅保存品牌控件引用方便验收。
- `tables.py`：正文14、表头14 DemiBold、行高34不变；自绘圈次卡标题14、圈速24、轮胎12。卡高86和三个绘制矩形不变。标题和磨损用字体度量省略，保留完整圈次tooltip，防止同一圈同时为基准/对比时标题裁切。
- `charts.py`：刻度使用 axis；标题字号取 section；轴标签仍12。AA、DPR画笔、断点、抽稀、原采样游标、单位和颜色身份不变。
- `app.py`：经用户额外授权，只把启动日志中 `font(digits=True)` 改为 `font('axis')`，没有修改入口控制流。
- `self_test.py`：保留原有 Raw、Header、PMv2、DPR、关闭耗时、历史来源证据，增加字体解析、字体OS/2字重、tnum和计时空间证据。
- `packaging/qt_acceptance.py`：十格矩阵证据与原始截图，新增可见PlainText标签的整形字形边界和缺字检查。
- `packaging/qt_startup_probe.py`：显式运行的普通入口字体检查，独立配置、日志和输出目录；不是正式启动依赖。
- `tests/test_qt_ui.py`：新增五项字体约束测试，保留原有17项Qt业务测试。另有7项适配器测试。

Qt 会在表头的父字体/样式传播中覆盖显式字重，因此增加了一个很小的 `TableHeader` 子类，在这些字体相关事件之后恢复它的角色。它调用原生事件实现，不接管表头绘制、排序、模型或行选择。测试和矩阵检查都验证最终控件字体仍请求600，不能只检查 `typeRole` 属性。

## 字体解析与平台差异

本机原生Windows/冻结EXE：Microsoft YaHei UI Regular=400；中文强调请求600，实际解析Bold=700。Segoe UI Semibold=600，用于品牌、圈速和计时；Segoe UI Regular=400用于坐标刻度。Consolas Regular=400。中文Bold映射是系统字体字形库存限制，不是偷偷把角色改为700。

离屏插件的 `QFontInfo.weight()` 对中文Bold可能返回请求值600，因此证据还记录实际字体OS/2的 `usWeightClass`（700），不把请求值当成实际字形库存。数字字体在原生和离屏均为Semibold600。

测试仅注册 `%WINDIR%/Fonts` 现有字体，包括 `seguisb.ttf`，没有复制或安装字体。正式应用不注册测试字体，打包不包含TTF/TTC/OTF/WOFF文件。依赖、PMv2 manifest、PassThrough策略和系统字体设置未改变。

## 验证与限制

Qt目标29项、完整unittest236项通过。十格1366×768/1920×1080 × DPR1/1.25/1.5/1.75/2的离屏检查通过，每格保存四种状态截图和证据。实际数值、来源和生命周期仍由现有模型提供，没有为了截图制造遥测。

1366宽度保留紧凑50px，1920宽度使用68px。这是现有响应式切换规则，不强行在小窗口塞入宽屏字号。十格数字宽度差均0 DIP；1366下三个实际计时的剩余宽度分别33/34/67 DIP，均大于8。1920下分别108/108/155 DIP。

冻结程序在原生Windows、本机DPR2下进行了隔离400包UDP收取、切页、历史单圈/练习加载、安全关闭和Raw/Header重放检查。十格模拟渲染不等于十格实机检查。本机只有一台显示器，未执行真实混合DPI跨屏拖动、100–175%实机切换或新的真实游戏长测。ClearType、显示器和其他Windows字体库存仍需在目标电脑人工确认。候选包不是新的正式版本或GitHub发布。
