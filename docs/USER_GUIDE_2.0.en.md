# RaceTrace Lab 2.0 — User guide

The interface is currently Chinese. This guide names important controls in Chinese and explains their purpose in English.

## Install and connect

Download `racetrace2.0.zip` from [Release v2.0.0](https://github.com/simplymighty33/F1-Telemetry-Lab/releases/tag/v2.0.0), extract the complete folder to a new writable location, and run `RaceTraceLab.exe`. Keep `_internal/`, `licenses/` and the accompanying notices. No Python installation is required. Do not overwrite an old installation. `Guide.md` and `Release.md` beside the EXE provide Chinese instructions.

Enable UDP telemetry in the game, normally at 60 Hz. On the same PC, send to `127.0.0.1`; on another PC, use the collector PC's LAN IPv4 and permit the traffic on a trusted private network. Match the game's destination port to the application (default `20777`) and avoid competing listeners. Supported native F1 23/24/25 formats, including the implemented 2026 Season Pack layout, are detected automatically; the application does not change game settings.

Use **来源与工具 → 连接与存储设置** to change the UDP port and output directory. Confirmation safely stops and restarts listening; reception briefly pauses. First launch creates local configuration, data and log directories.

## Live recording

The recording page shows game, track, stage and latest-first lap history with sector times, validity, compound and tyre wear. Wear means **used percentage**, not remaining life. Unknown information remains unknown. Labels use “第N圈” without the former “总” prefix. Sprint/Race remains uncertain when evidence is insufficient.

Raw status is separate from foundation/analysis status. A background decoder rejection does not necessarily mean Raw loss: keep the original archive for future reparsing. Header success alone does not prove complete packet-body decoding. Switching pages does not stop capture; the tools menu can stop capture while leaving browsing available.

New archives are routed by `(packetFormat, sessionUID)`, not lap count, tyre change, garage visit or Flashback. Names contain first receipt time, game, track, first confirmed stage and UID; late packets return to the matching archive. Old recordings are not renamed. Raw v1/v2 remain readable; original bytes, envelopes and complete car arrays remain authoritative.

## Current and historical analysis

**分析当前录制** follows the current recording's committed background results, without manual Raw import each lap. Completed laps appear after processing commits. **打开分析数据库** browses an existing database read-only and does not follow later game sessions. Choose **分析当前录制** to reconnect.

For an older recording, select its entire Session directory through the tools menu's build/continue action. Initial use builds a reusable foundation; compatible work resumes it. Independent rebuild creates fresh results without overwriting old ones. 2.0 uses `*.foundation-v3.db` and `analysis_v2.0/`.

**F1 25 migration:** when an old analysis reports “缺少运动” or “样本不足”, independently rebuild its original Session from Raw. Merely opening an old database does not repair it. This cannot invent missing packets or repair corrupted bytes.

Choose session, driving segment, reference and comparison lap. Blue solid lines are reference; amber dashed lines are comparison. Delta is comparison minus reference: negative official delta means a faster comparison lap. Official delta, supported interval contributions and unassigned remainder remain separate, not automatic coaching.

Time delta, speed, throttle and brake use separate distance-linked plots. Hover inspects original sample values; focus a plot and use Left/Right to step through samples, Esc to clear and Ctrl+0 to restore the full lap. Scroll vertically to lower charts. Pan/zoom changes their shared distance range. Unsupported gaps are not bridged or shown as reliable values.

On narrow windows, **区间 / 质量面板** and **返回图表** switch between chart and detail views. Details provide intervals, whole-lap metrics and quality evidence. Selecting an interval focuses its range; selecting quality evidence shows details. Frame gaps are not measured UDP-loss percentages.

## Practice review and offline tools

Generate a practice report for the selected scope to review lap/sector statistics, tyre sets, fuel/temperature/ERS condition evidence and repeated loss intervals. ERS condition statistics are not energy-deployment curves or an energy-consumption analysis. Unknown channels remain unavailable, not zero. Supported repeated-loss rows can return to comparison; these intervals are not corner numbers or proven driving causes.

The tools menu provides Session build/continue, independent rebuild, lossless Raw compression and read-only auditing. Compression verifies a new copy and preserves its source. Do not compress, independently rebuild or fully audit an active recording. A confirmed incomplete tail can yield a marked verified prefix; CRC corruption is not silently skipped.

Jobs show progress and support cooperative cancellation at safe boundaries. Committed progress remains reusable; current I/O/calculations may take time to finish. Derived databases add disk usage. Keep space for Raw, temporary copies and caches.

## Close, copy and report problems

Use **安全停止并关闭** or X. Both show a waiting dialog while Raw and tasks drain/close safely. Do not force-kill the process. Safe closing cannot protect unwritten buffers against power loss or forced termination.

After recording and processing stop, copy the entire Session, including metadata and any remaining SQLite `-wal`/`-shm` files. Do not copy only an active database or delete active locks/caches.

For feedback, include app version, game/protocol, Windows version and reproduction steps. Do not publicly attach full recordings, player identities, IP addresses, private paths or unredacted logs; use redacted summaries.

The map test application remains independent. Maps, ERS deployment curves and AI setup/driving coaching are not new main-app features. Saved-Raw replay and local UDP tests are not in-game endurance testing. See [release notes](RELEASE_2.0.en.md) for validation limits.
