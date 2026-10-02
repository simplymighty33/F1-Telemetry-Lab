# F1 Telemetry Lab 1.0.3c

1.0.3c 是本地 Session 分档测试版：新采集按游戏 UID 与协议年份分别保存 Raw，目录包含开始时间、游戏、赛道和环节，便于按比赛进程查找。旧 UID 的迟到包仍归回原文件，后台分析分别保存；新目录整体复制后可恢复增量分析。旧目录不迁移、不批量改名。程序位于 `dist/F1TelemetryLab-1.0.3c/`，详见 [docs/RELEASE_1.0.3c.md](docs/RELEASE_1.0.3c.md)。真实游戏长测尚待执行；此次未发布到 GitHub。下面保留历史版本说明。

1.0.3b在a版基础上统一赛道/比赛环节标题、总圈号和轮胎磨损显示，完善赛中分析连接说明与录制切换；不需要手动导入Raw即可查看当前录制。正赛/冲刺赛证据不足时明确待确认，不靠圈数猜测。程序位于 `dist/F1TelemetryLab-1.0.3b/`，详见 [docs/RELEASE_1.0.3b.md](docs/RELEASE_1.0.3b.md)。下面保留a版及更早版本历史。

本地开发/验收版新增练习段汇总、比较条件来源核验、固定距离多圈重复观察和只读长测审计。程序位于 `dist/F1TelemetryLab-1.0.3a/`，使用入口为单圈分析窗口“练习复盘 / 审计”；详见 [docs/RELEASE_1.0.3a.md](docs/RELEASE_1.0.3a.md) 与 [docs/ENDURANCE_1.0.3a.md](docs/ENDURANCE_1.0.3a.md)。真实游戏长测待执行，不提前宣称通过；GitHub正式版暂保持1.0.2。下方保留历史说明。

统一公开发行版 1.0.2 包含此前本地开发阶段 1.1、1.2 的全部功能，并非回退。程序位于 `dist/F1TelemetryLab-1.0.2/`，更新说明见 [docs/RELEASE_1.0.2.en.md](docs/RELEASE_1.0.2.en.md)。下方 1.1、1.2 内容保留作为开发历史。

1.2 新增实际首尾边界补点、未归属差额的保守算术分解、逐圈质量明细和缺包帧回查；结合局部游戏时钟采样节奏识别细小缺口，不以帧跳跃宣称UDP丢包率。GUI采集新增写盘压力下的后台暂缓、磁盘预警/安全停止、分析积压提示及最新状态合并。旧分析库直接浏览保持只读；继续旧增量分析时只补建派生质量数据，不重放全部Raw。无新增运行依赖。程序位于 `dist/F1TelemetryLab-1.2/`，详见 [docs/RELEASE_1.2.md](docs/RELEASE_1.2.md)。

1.1 新增时间获益/损失区间、区间联动放大和驾驶操作对照；补强原始采样间隙、关键字段与圈内时序检查。打开旧分析数据库时仅在内存中核验，不修改数据库；官方整圈时间差与无法归属到有效区间的差额分别展示。无新增运行依赖。新程序位于 `dist/F1TelemetryLab-1.1/`，更新说明见 [docs/RELEASE_1.1.md](docs/RELEASE_1.1.md)。

1.0 首次更名：主窗口、分析窗口、程序文件名及 Windows 属性统一为 F1 Telemetry Lab。发布路径 `dist/F1TelemetryLab-1.0/F1TelemetryLab.exe`；必须复制整个目录，中文使用说明同目录附带。单圈分析窗口扩大初始尺寸，顶部工具栏可换行，状态栏固定保留空间，空状态表不占高度，质量明细可折叠，驾驶指标可双向滚动。原始格式、分析算法及 `analysis_v0.10.0` 缓存路径保留，兼容旧档案。旧命令行入口也保留，新增 `f1-telemetry-lab` 图形入口。升级和布局说明见 [docs/RELEASE_1.0.md](docs/RELEASE_1.0.md)。

v0.10.0 新增后台完成圈增量分析、驾驶段筛选和逐圈质量状态。采集、基础整理、单圈分析分属独立任务；退出时先保全 Raw，再提交分析进度。结果保存在 `analysis_v0.10.0/telemetry_analysis.db`，重新打开只处理新增数据，不覆盖旧数据库。驾驶段以车库出入为边界，不等同于胎组。设计、边界及验收见 [docs/INCREMENTAL_v0.10.0.md](docs/INCREMENTAL_v0.10.0.md)。

v0.9.1 新增逐圈轮胎配方、圈末磨损与胎组独立圈号；单圈选择显示“软胎 第1圈（圈速）”，并保留会话圈号。默认分析输出为 `analysis_v0.9.1/`，旧数据库保持可读。发布目录 `dist/F1TelemetryCollector-v0.9.1/`，中文使用说明随打包自动放在 EXE 同目录。数据规则见 [docs/TYRES_v0.9.1.md](docs/TYRES_v0.9.1.md)。

v0.9.0 原生支持 F1 23、F1 24、F1 25，以及 F1 25 的 2026 Season Pack UDP 协议。程序直接读取公共 Header 中的 `packetFormat` 自动选择包体结构；无需用户在应用内选择游戏。主界面同时显示检测到的游戏和模式。完整兼容范围见 [docs/MULTI_GAME_v0.9.0.md](docs/MULTI_GAME_v0.9.0.md)。发布文件夹为 `dist/F1TelemetryCollector-v0.9.0/`。

v0.8.1 的 UDP 端口编辑、保存并安全重新监听、统一安全关窗和等待弹窗继续保留。相关保护边界见 [docs/GUI_v0.8.1.md](docs/GUI_v0.8.1.md)。

## v0.8.0 基础层说明

新增实时后台基础整理、可恢复进度、Flashback 有效分支与驾驶段基础记录。旧 Raw 首次转换后可复用；分析结果相同进度与参数下自动复用。原 Raw 格式和旧分析数据库保持兼容，默认新分析写入 `analysis_v0.8.0/`，不覆盖旧 `analysis/`。

这是基础层阶段，不是完整分段反馈或实时驾驶诊断版。基础缓存会增加磁盘占用；本机长测样本约新增 173 MB。可通过 `foundation_enabled: false` 关闭采集时后台整理。默认不自动生成重复 CSV，命令行 `--export` 可启用。

完整架构、使用方法、恢复边界和验收数据见 [docs/FOUNDATION_v0.8.0.md](docs/FOUNDATION_v0.8.0.md)。Windows 发布文件夹为 `dist/F1TelemetryCollector-v0.8.0/`，部署时必须复制整个文件夹。

以下章节保留历史功能和用法；新版本数据路径和导出行为以上述文档为准。

这是一个面向 **EA Sports F1 23 / F1 24 / F1 25** 的 raw-first UDP Telemetry 采集与离线分析项目。它采集原始包、建立索引，按协议版本完整解码 14 至 17 类 UDP Packet，并可从 Raw Replay 建立 Flashback 安全的玩家遥测时间线和固定距离单圈数据。

## 功能

- 监听 UDP `20777`（地址、端口均可配置）
- UDP 接收与磁盘写入使用有界队列解耦；存储跟不上时明确报错，不静默丢包
- 对每个 datagram 记录 UTC 接收时间、单调时钟、来源 IP/端口、长度和原始字节
- 解析 F1 23/24/25 共用的 29-byte Header，并自动选择 2023/2024/2025/2026 包体；解析失败不影响原始包保存
- 创建每次运行独立的 `session_YYYYMMDD_HHMMSS` 目录
- 写入自描述、带 CRC32 的 `raw_packets.bin`
- 新采集默认采用 256 KiB 独立 zlib 块无损压缩，保留每包原始字节和接收信息
- 旧 v1 Raw 自动兼容；压缩块有头校验、块校验、逐包校验和有界解压
- 写入 `telemetry.db`，便于按 packet ID、frame 和 session 查询
- 原子更新 `metadata.json`，正常结束时生成包数量、类型与采样率摘要
- 同时输出控制台状态和滚动文件日志 `logs/collector.log`
- 首次运行自动创建 `config/`、`data/` 和 `logs/`
- 支持源码、安装后的命令行入口和 PyInstaller Windows EXE
- Windows EXE 默认使用图形界面，显示游戏模式、比赛阶段、赛道以及玩家圈速与三个赛段时间
- 默认窗口按屏幕大小和字体缩放调整；底部操作按钮独立成行，缩小时优先压缩圈速表格区域
- 圈速表以游戏 Session History 为准，最新圈置顶；Flashback 后以回放后的最终历史覆盖旧记录
- 采集错误会留在界面中显示，窗口只会在用户手动关闭后退出
- 支持完整扫描原始档案并校验 CRC
- 支持将每一个原始包完整解码为压缩 JSON Lines，并导出 Session、事件和最终玩家逐圈 CSV
- 支持把 Motion、Lap Data、Car Telemetry、Car Status、Car Damage 和 Motion Ex 按帧同步
- Flashback 后保留原始分支但将其标记为 superseded，分析只使用最终有效时间线
- 将完整有效圈按默认每 5 米重采样，输出独立 SQLite 和压缩 CSV
- 从标准化单圈提取制动区、油门重新介入和升降挡事件
- 计算每圈全油门、制动、滑行、刹车油门重叠、方向修正和输入变化等指标
- 汇总最佳圈、最佳赛段与理论最佳圈，供后续驾驶风格和圈速对比使用
- EXE 内置离线单圈分析窗口，可比较两圈的时间差、速度、油门、刹车和驾驶指标
- 可在图形界面中直接选择已完成的 Session，从 Raw Replay 后台生成分析数据
- 实时圈速界面先筛选玩家历史再解码；未变化的控件不反复更新

## 运行要求

- Python 3.10+
- 无第三方运行依赖

在项目根目录运行：

```powershell
python -m collector.main
```

源码方式启动图形界面：

```powershell
python -m collector.gui_main
```

按 `Ctrl+C` 会完成缓冲区写入、关闭 Session 并打印汇总。也可使用：

```powershell
python -m collector.main --track "Bahrain" --session-type "Time Trial"
python -m collector.main --host 0.0.0.0 --port 20777 --duration 60
python -m collector.main --data-dir D:\F1Data --log-dir D:\F1Logs
```

所有相对路径均以程序根目录为基准。打包后，程序根目录是
`F1TelemetryCollector.exe` 所在目录，而不是 PyInstaller 的临时解包目录。

游戏中仍需启用 UDP Telemetry，并确保游戏端口与应用界面端口一致（默认 `20777`）。F1 23、F1 24、F1 25 使用各自默认 UDP Format 即可，应用会自动识别；F1 25 安装 2026 Season Pack 后默认的 2026 协议也支持。如果游戏和采集器不在同一台机器，还要把 UDP IP Address 设置为采集器所在电脑的局域网 IP。

## Session 产物

```text
data/
└── session_YYYYMMDD_HHMMSS/
    ├── raw_packets.bin
    ├── telemetry.db
    └── metadata.json
```

`raw_packets.bin` 是数据事实源。每条记录都包含 framing、接收时间、来源地址、payload 长度、CRC32 和 payload。SQLite 的 `raw_reference`、`raw_offset`、`raw_length` 指向对应原始记录。`telemetry_raw` 仅预留给 Phase 2，Phase 1 不向其中写入分析字段。

Session 信息可直接查看 `metadata.json`，也可以运行：

```powershell
python -m collector.inspect_session data\session_YYYYMMDD_HHMMSS --verify-raw
```

`--verify-raw` 会顺序读取全部原始记录、验证每条 CRC，并比较 raw 与 SQLite 的包数量。在异常断电场景下，raw 可能比最后一次 SQLite 批量提交多；原始数据仍可用于后续恢复索引。

## Raw 无损压缩与旧数据转换

v0.7 默认设置如下，旧配置文件缺少这些项目时自动应用默认值：

```json
{
  "raw_compression": "zlib",
  "raw_compression_level": 1,
  "raw_block_bytes": 262144
}
```

`raw_compression` 改为 `none` 可生成旧 v1 格式。无需安装第三方压缩库。`metadata.json` 的 `raw_storage` 保存格式版本、已写入字节、压缩比和持久化包数。索引提交前会先确保其 Raw 数据已刷盘。

图形界面中进入“离线圈速分析”，点击“压缩旧 Raw”，选择已完成的原始文件和一个新的目标文件名。工具会逐包比较压缩前后字节、时间和来源，通过校验后保存压缩副本；原文件保留。源码也可使用：

```powershell
python -m collector.compress_archive data\session_YYYYMMDD_HHMMSS --output D:\F1Archive\session.compressed.bin
python -m collector.compress_archive D:\F1Archive\session.compressed.bin --restore --output D:\F1Archive\session.restored.bin
```

截断尾部恢复必须明确加入 `--recover-tail`，结果返回 `recovered_prefix`，不会把恢复前缀当作完整会话。CRC 损坏会报错。v0.6 及更早程序不支持 v2，可用 `--restore` 生成兼容副本。

已对 624,609 个长测 Packet 完成逐包、逐 Header、完整还原以及分析表一致性验收。实际压缩 Raw 减少约 78%，保留完整索引时整套采集产物减少约 62%。资源测量和进一步精简优先级见[验收与资源研究](docs/STORAGE_AND_RESOURCE_AUDIT_v0.7.md)。

文件名继续为 `raw_packets.bin`，所有 Replay、完整解析、分析入口自动识别版本。v2 的 `raw_offset` 是逻辑偏移，不是磁盘 seek 位置；实际读取使用新索引中的物理块位置和块内位置。旧数据转换不会重写原数据库，压缩副本应通过新程序独立读取，不能直接替换旧 Raw 后继续使用旧物理偏移。[格式说明](docs/RAW_ARCHIVE_FORMAT.md)包含完整布局和恢复边界。

完整解析一个 Session：

```powershell
python -m collector.decode_session data\session_YYYYMMDD_HHMMSS
```

结果写入该 Session 的 `decoded\`：`decoded_packets.jsonl.gz` 保存每条归档信息和完整包体，`sessions.json` 保存模式/阶段/赛道，`events.csv` 保存全部事件（包括 Flashback），`player_laps.csv` 保存最终有效历史，`decode_summary.json` 和 `decode_errors.json` 保存校验结论。

## Phase 2 离线分析

从 Raw Replay 建立分析数据：

```powershell
python -m collector.analyze_session data\session_YYYYMMDD_HHMMSS
```

默认将最终有效圈按每 5 米标准化。可以调整间隔或纳入游戏标记的无效圈：

```powershell
python -m collector.analyze_session data\session_YYYYMMDD_HHMMSS --distance-step 10
python -m collector.analyze_session data\session_YYYYMMDD_HHMMSS --include-invalid
```

结果写入 Session 的 `analysis\`：

```text
analysis/
├── telemetry_analysis.db
├── laps.csv
├── resampled_player_laps.csv.gz
├── braking_events.csv
├── throttle_events.csv
├── gear_shift_events.csv
├── lap_metrics.csv
├── session_metrics.csv
├── analysis_summary.json
└── analysis_errors.json
```

`telemetry_analysis.db` 同时保存原始帧级玩家样本和标准化单圈。旧的 Flashback 分支不会删除，而是以 `superseded=1` 保留审计痕迹；`resampled_lap_samples` 只来源于最终有效时间线。分析数据库是派生产物，可以随时从 `raw_packets.bin` 重建。

事件和指标均由最终有效时间线生成：`braking_events` 记录制动起点、峰值、最低速度和释放平顺度，`throttle_events` 记录出弯油门建立过程，`gear_shift_events` 记录升降挡位置；`lap_metrics` 与 `session_metrics` 提供单圈和会话级摘要。CSV 与数据库内容一致，方便直接检查或导入其他分析工具。

实时采集窗口中的“离线圈速分析”可打开分析界面。选择一个已完成的 Session 后，程序会在后台完成 Raw Replay 和分析构建；也可以直接选择已有的 `telemetry_analysis.db`。界面以最佳圈为默认基准，可切换任意两圈，并显示沿赛道距离的累计时间差、速度、油门、刹车、制动起点和单圈驾驶指标。

v0.7.2 修复了后台结果反馈队列位置错误，分析和压缩任务完成或失败后会恢复按钮并显示结果。“从 Session 生成分析”应选择含 `raw_packets.bin` 的文件夹；“打开分析数据库”应选择 `analysis\telemetry_analysis.db`，不能选择采集索引 `telemetry.db`。已生成的结果可直接打开，无需再次 Replay。

性能测量与下一步加速方案见 [离线分析性能研究](docs/ANALYSIS_PERFORMANCE_v0.7.2.md)。其中加速原型仅为研究材料，尚未包含在发布版中。

## 测试

```powershell
python -m unittest discover -v
```

测试覆盖全部 14 类 Packet 的固定布局、Flashback 圈速替换、Header 解码、UDP 接收、队列排空、配置校验、raw/SQLite 一致性和 UDP 到归档的端到端流程。

## 构建 Windows 应用

在 PowerShell 中运行：

```powershell
.\build_windows.ps1
```

脚本会安装项目的 `build` 可选依赖并生成：

```text
dist\F1TelemetryCollector-v0.7.2\
├── F1TelemetryCollector.exe
└── _internal\
```

这是推荐的 `onedir` 发布形式。整个 `F1TelemetryCollector` 文件夹可以复制到
另一台 Windows 10/11 电脑，不需要安装 Python。双击 EXE 会打开实时采集界面。
界面不会因采集完成或出错自动消失；点击“安全停止并关闭”或窗口右上角关闭按钮后，
程序会先排空写入队列、关闭数据库，再退出。首次运行后，EXE 同级会自动生成：

```text
config\settings.json
data\
logs\collector.log
```

如果构建环境已经安装好依赖，可使用：

```powershell
.\build_windows.ps1 -SkipInstall
```

## 代码结构

```text
collector/
├── main.py              # 稳定入口
├── app.py               # 生命周期编排
├── gui_main.py          # Windows EXE 入口
├── gui.py               # 实时采集与圈速界面
├── service.py           # 图形界面的后台采集服务
├── settings.py          # 配置创建与校验
├── runtime.py           # 源码/EXE 路径规则
├── logging_setup.py     # 控制台和滚动日志
├── pipeline.py          # 接收与存储队列
├── udp_receiver.py
└── packet_capture.py
decoder/                 # 多版本 Packet 解析
├── protocol.py          # 协议注册、尺寸与自动游戏识别
├── full_parser.py       # 2023/2024/2025/2026 完整字段解码
├── session_history.py   # Flashback 安全的圈速与赛段历史
├── lap_data.py          # 旧版 Lap Data 兼容解码
storage/                 # raw archive 与 SQLite
analysis/                # 帧同步、Flashback处理与距离重采样
packaging/               # PyInstaller 和 Windows 版本信息
tests/
```

建议的实车测试顺序：

1. 在每个目标游戏的 Time Trial 跑一圈，确认界面游戏名称正确，且 Header 的 `packet_format` 分别为 2023、2024、2025 或 2026。
2. Career FP1 跑 10 圈，结束后执行 `inspect_session --verify-raw`。
3. 正常比赛后检查 `player_car_index`；2023–2025 协议验证 0–21 号车辆数组，2026 Season Pack 验证 0–23 号车辆数组。

## 可靠性边界

采集器把 UDP socket 接收缓冲区请求为 4 MiB，并用有界队列将网络接收与磁盘 I/O
分离。队列耗尽会停止 Session、写入错误状态并记录软件侧丢包数，而不是继续静默运行。
UDP 协议本身不提供重传，因此任何软件都无法绝对保证网络层“零丢包”；应使用有线
局域网、避免 Wi-Fi 拥塞，并在长 Session 后校验采样率和 frame 连续性。Phase 1
保留了所有必要原始字段，后续可增加序列/帧缺口审计而不改变归档格式。
