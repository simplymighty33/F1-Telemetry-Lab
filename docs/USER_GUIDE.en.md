# F1 Telemetry Lab 1.0.3 — User guide

## Start

Extract the entire package and run `F1TelemetryLab.exe`; keep `_internal/` beside it. Python is not required. Use a writable folder with enough space. `config/`, `data/`, and `logs/` are created automatically.

Enable game UDP telemetry, normally 60 Hz, and match the application port (default 20777). Same PC: destination `127.0.0.1`; separate PC: collector's LAN IPv4, with the trusted private-network firewall configured. Avoid competing listeners. F1 23/24/25 and the implemented F1 25 2026 Season Pack formats are detected automatically; the application does not change game settings.

**保存并重新监听** saves a port from 1–65535 and safely restarts listening. Reception briefly pauses; synchronize the game's port between runs.

## Record and locate data

Check receiving status and an advancing recent-data time. The latest-first table shows official lap/sectors, validity, total/session lap, compound, wear and tyre-set lap. Wear is used percentage, not remaining life; unavailable evidence stays unknown. Browsing old rows preserves scroll. Flashback supersedes prior results. Sprint/Race remains explicitly uncertain when evidence is insufficient.

New archives are routed by `(packetFormat, sessionUID)`, not time, lap count, pits, tyres or Flashback. Example:

`session_20261002_153000_F1-23_澳大利亚_练习赛2_UID-000000000000004D`

Names use first receipt time and first confirmed context; later changes remain in metadata. Active analysis paths do not repeatedly move. Late packets return to the matching archive; unidentifiable input is saved separately. New runs/collisions do not overwrite old data.

Each archive normally has authoritative `raw_packets.bin`, packet index `telemetry.db`, `metadata.json`, `foundation/` and `analysis_v1.0.3/`. Lossless compression preserves all received bytes, envelopes and full car arrays. Derived caches add disk usage, not replace Raw. Old folders are not migrated; Raw v1/v2 remain readable.

## Analyze

Open **单圈分析** from the collector to connect automatically, without manual Raw import. Completed results appear after background commits. New game sessions wait for their own results; manual historical selection remains available.

- **打开分析数据库**: read-only browsing of existing `telemetry_analysis.db`, including old results.
- **从 Session 生成分析**: select the whole directory containing Raw. First use prepares reusable foundation data; later use continues progress. During recording it reads only committed snapshots.
- Incompatible/moved legacy caches offer independent reconstruction after capture stops, into `analysis_v1.0.3_rebuild_...` with fresh foundation. Existing results remain intact. First-time processing of large Raw still takes time.

Choose session, driving segment, reference and comparison laps. Labels include track/stage, session lap, compound/wear and official time. Distance traces, events and time intervals are observations, not causal coaching. Positive delta means comparison-lap loss; negative means gain. Official lap delta, observed interval delta and unassigned remainder stay separate. Ordinary packet progress preserves focus; heavy comparisons run in the background.

Expand/double-click quality rows for coverage, gaps, channel/identity problems and source evidence. Frame gaps are not measured UDP-loss percentages. Practice review includes driving-segment summaries, comparison-condition evidence, repeatability and read-only audits. Diagnostic summaries omit identity, IP and private paths by default.

## Jobs and closing

Offline jobs show stage and processed count; a total appears only when known. **取消后台任务** requests safe cancellation at record/batch/lap boundaries. Independent Raw reception continues. Committed analysis progress remains resumable; unfinished transactions roll back. An ongoing I/O/fsync or lap computation may take time to finish.

**压缩旧 Raw** creates a new verified copy of a stopped archive. Select an unused target name. Bytes, envelopes, CRC and logical offsets are compared before publication. The source is preserved; cancellation publishes no unverified copy. Keep space for source, temporary copy and caches.

Use **安全停止并关闭** or X and wait for the saving dialog. Raw is drained first; derived consumers commit/close and offline jobs receive cancellation. Display/analysis faults stay visible without silently stopping capture. Storage failures and queue overflow are explicit failures. Resource pressure can pause derived work; critically low disk space safely stops capture.

After all capture/processing stops, copy the entire Session, including metadata and any remaining SQLite `-wal`/`-shm` files. Do not copy only an active main database or delete active locks/caches. New caches use archive identity/relative paths, not cryptographic tamper protection. If content may have changed while retaining size/timestamps, choose independent reconstruction or full developer source verification.

Confirmed truncated tails can yield a marked verified-prefix result; CRC corruption is not skipped. Raw cannot restore network packets never received. Forced termination/power loss can lose queued or uncommitted tails. Automated/synthetic tests do not prove real-game endurance. Maps, reliable Sprint identification and automatic coaching remain future work. Current release notes/test checklist ship beside the EXE; historical guides remain in source `docs/history/`.
