# RaceTrace Lab 2.0

## [⬇ 下载 Windows 版 / Download for Windows](https://github.com/simplymighty33/F1-Telemetry-Lab/releases/download/v2.0.0/racetrace2.0.zip)

**2.0 · Windows x64 · 53.6 MiB · 无需安装 Python / No Python installation required**

[Release notes / 版本说明](https://github.com/simplymighty33/F1-Telemetry-Lab/releases/tag/v2.0.0) · [历史版本 / All releases](https://github.com/simplymighty33/F1-Telemetry-Lab/releases) · [中文使用说明](docs/qt-command-center-guide.md) · [English user guide](https://github.com/simplymighty33/F1-Telemetry-Lab/blob/main/docs/USER_GUIDE_2.0.en.md)

完整解压后运行 `racetrace2.0/RaceTraceLab.exe`，保留 `_internal/` 和许可文件。界面目前为中文。请解压到新目录，不要覆盖旧安装。

> Use the Windows download above. GitHub's **Code → Download ZIP** and automatic **Source code** archives are source, not runnable Windows applications. The application interface is currently Chinese.

Raw-first Windows telemetry capture and lap analysis for F1 23/24/25, with automatic protocol detection. The implemented F1 25 2026 Season Pack format is also supported. This independent enthusiast project is not an EA/Codemasters product.

Extract the entire `racetrace2.0.zip` into a new writable directory and launch `racetrace2.0/RaceTraceLab.exe`. Keep `_internal/` and licenses beside the EXE; Python is not required. Do not overwrite an older installation.

- [中文使用说明](docs/qt-command-center-guide.md).
- [2.0 changes and migration](docs/RELEASE_2.0.md) / [Third-party notices](docs/qt-third-party-notices.md).

## Capabilities

- Lossless compressed Raw, complete packet/header inspection and offline Replay; unsupported bytes remain captured.
- Session archives routed by protocol and UID, named by start time, game, track, first confirmed stage and UID. Late packets return to their archive.
- Latest-first lap history with sectors, validity, compound/wear and tyre-set lap counts; Flashback supersedes old results.
- Independent resumable foundation/incremental analysis, automatically connected during recording.
- Distance-aligned comparison, driving events, time-gain/loss intervals, quality details and unassigned-delta reporting.
- Driving-segment practice review, comparison-condition evidence, repeated observations and privacy-conscious audits.
- Editable UDP port, safe closing, resource-pressure warnings, cancellable offline processing and verified compression copies.

## What's new in 2.0

- RaceTrace Lab branding and a redesigned, high-DPI-aware Qt interface: live recording, lap comparison and practice review.
- Separate distance-aligned throttle/brake plots, shared cursor inspection, clearer lap/sector/tyre-wear context and responsive scrolling.
- Correct F1 25 updated Motion and Car Telemetry layouts, including 2026-format packets emitted while driving pre-2026-regulation cars.
- Match completed time-trial attempts to official history after returning to the garage or restarting a lap; abandoned attempts no longer replace the matching completed lap.
- Independent revised caches, visible decoder failures and shorter verified package paths. Original Raw and older analysis results are preserved.

Validation: **255 regression tests passed**; a saved **230,335-packet F1 25 recording** replayed successfully and all three completed laps became analyzable. Synthetic packaged-EXE checks covered UDP reception, Raw/Header replay and safe closing. These checks are not a new in-game endurance test or a zero-loss guarantee. New-EXE endurance, clean-Windows and physical mixed-DPI validation remain pending.

## Safety and compatibility

Raw v1/v2 remain readable. Existing recordings/results are not renamed or overwritten. 2.0 uses `foundation/*foundation-v3.db` and `analysis_v2.0/`; old databases remain readable. An old database reporting insufficient samples will not repair itself when opened: use Session build/rebuild with the original Raw. Unknown layouts remain preserved and reported, not guessed. Supported formats: 2023/2024/2025/2026, packet version 1; a pre-2026 car in updated F1 25 can emit 2026-format packets.

Stop capture and processing before copying an entire Session, including metadata and any remaining SQLite companion files. Derived caches add disk usage; Raw remains authoritative. Do not remove active locks/caches. There is no zero-loss guarantee for packets never received, overload, forced termination or power loss. Frame gaps are not measured UDP-loss percentages. Sprint/Race remains explicitly uncertain when evidence is insufficient. Maps and automatic coaching are future work.

## Develop

Core CLI: Python >=3.10 and standard library. GUI/build tested with Windows x64 Python 3.13.15 and the locked Qt environment; no new dependency or third-party font was added in 2.0.

```powershell
python -m collector.main --help
python -m pip install -r packaging/requirements-qt-lock.txt
python -m collector.qt_ui
python -m unittest discover -s tests -v
python -m collector.prepare_session SESSION --verify
python -m collector.analyze_session SESSION
python -m collector.analyze_session SESSION --force-rebuild --export
python packaging/build_qt.py --output build-qt
python packaging/package_qt.py --source build-qt/dist/RaceTraceLab --output release
```

Contracts: `docs/RAW_ARCHIVE_FORMAT.md`, `docs/FOUNDATION_v0.8.0.md`, `docs/INCREMENTAL_v0.10.0.md`, `docs/adr/`. Current release notes override historical defaults. Pre-1.0.3 combined guides remain in `docs/history/`.

[Project repository](https://github.com/simplymighty33/F1-Telemetry-Lab). No application source-code license has been selected; public visibility is not a grant of unrestricted reuse. Third-party component licenses are supplied separately. This unofficial project is not affiliated with EA SPORTS, Codemasters, Formula 1 or SimHub.
# Qt desktop interface

2.0 采用已批准的 PySide6 Widgets + pyqtgraph 界面与修复后的解码/分析核心。
源码：Python 3.13 环境安装 `.[gui]` 后运行 `python -m collector.qt_ui`；
`--analysis` / `--analysis-db PATH` 可独立离线使用，不启动 UDP。
Windows 运行 `RaceTraceLab.exe`，保留整个 onedir 文件夹。名称及应用图标已更新；录制路径、配置和历史数据格式不变。
构建：`build_windows_qt.ps1 -Python <3.13环境python>`，锁定文件位于 packaging/。
GUI 命令 `racetrace-lab`、`f1-telemetry-lab`、`f1-telemetry-ui`、`f1-telemetry-qt` 统一启动 Qt（需安装 `[gui]`），明确回退命令为 `f1-telemetry-tk`。CLI 与原模块路径保留。
详见 [中文使用说明](docs/qt-command-center-guide.md)、[UI 决策](docs/adr/0004-qt-command-center.md)。
地图测试程序保持独立，地图 / ERS 曲线未接入主程序。保存 Raw 回放不等于新版游戏长测，真实游戏长测、干净 Windows 与混合 DPI 跨屏验收仍需进行。

