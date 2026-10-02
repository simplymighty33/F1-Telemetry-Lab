# F1 Telemetry Lab 1.0.2 — User Guide

Public release 1.0.2 includes all features developed under the local 1.1 and 1.2 milestone names. It is not a feature rollback; no separate 1.1/1.2 installation is needed.

The current Windows interface is Chinese. The English descriptions below include the relevant Chinese button labels.

## 1. Installation

Use a complete packaged Windows distribution, not GitHub's source ZIP. Extract the whole `F1TelemetryLab-1.0.2` folder and keep `F1TelemetryLab.exe` beside `_internal`. Python is not required for the packaged application.

Choose a writable folder with enough free disk space. On first startup, the application creates `config/`, `data/`, and `logs/` beside the EXE. Download the complete Windows ZIP from [Release v1.0.2](https://github.com/simplymighty33/F1-Telemetry-Lab/releases/tag/v1.0.2). Both English and Chinese guides are included beside the EXE.

## 2. Game settings

In the game's telemetry settings:

| Setting | Recommended value |
| --- | --- |
| UDP Telemetry | On |
| UDP Send Rate | 60 Hz |
| UDP Port | `20777`, or any port matching the application |
| UDP Format | The game's native format |
| UDP IP Address, same PC | `127.0.0.1` |
| UDP IP Address, separate PCs | Collector PC's LAN IPv4 address |

F1 23/24/25 and the F1 25 2026 Season Pack formats are selected from packet Headers automatically. This does **not** enable UDP in the game or change its destination settings. For LAN use, permit the application on the appropriate trusted private network and check the firewall if no data arrives.

Avoid running two collectors that compete for the same UDP port.

## 3. Recording

1. Launch `F1TelemetryLab.exe` and check the UDP port.
2. Enter a driving session in the game.
3. Confirm the detected game/session/track and that the recent-data timestamp keeps updating.
4. Complete laps. The lap table shows newest results first, including sectors, validity, compound, wear, and tyre-set lap number when the required data is available.
5. Stop with **安全停止并关闭** (safe stop and close), or click X. Wait for the saving/background-task dialog to finish before shutting down the PC.

To change ports, enter a value from 1 to 65535 and use **保存并重新监听** (save and restart listening). This safely ends the current recording and begins a new one. Reception pauses briefly; change the game's port too, preferably between sessions.

Tyre-set lap numbering is separate from session lap numbering. Wear describes tyre wear, not remaining tyre life. Missing or stale tyre information may be unavailable. Flashbacks withdraw affected old results; final lap history follows the valid timeline.

## 4. Files and backups

Each recording has a directory such as:

```text
data/session_YYYYMMDD_HHMMSS/
  raw_packets.bin
  telemetry.db
  metadata.json
  foundation/raw_packets.foundation-v2.db
  analysis_v0.10.0/telemetry_analysis.db
```

- `raw_packets.bin`: losslessly stored packet bytes and receipt information; the primary archive.
- `telemetry.db`: recording packet index, **not** a lap-analysis database.
- `metadata.json`: recording and storage summary.
- `foundation/`: reusable decoded foundation cache.
- `analysis_v0.10.0/telemetry_analysis.db`: incremental lap-analysis results. The historical directory name is intentionally retained for compatibility.

Foundation and analysis files are derived from Raw and may not exist immediately. Back up or move the **entire session folder after safe shutdown**, rather than copying a live database in isolation. Keep Raw even when derived results already exist.

## 5. Lap analysis

Open **圈速分析** (lap analysis).

### From a recording: 从 Session 生成分析

Choose the **session directory containing `raw_packets.bin`**, not the Raw file itself. The program creates or updates analysis and opens the result. The first conversion of an older archive may take longer; subsequent updates reuse committed progress.

With background analysis enabled, completed laps are processed while recording. Live results are committed snapshots and may briefly lag reception; they are not immediate packet-by-packet coaching.

### From existing results: 打开分析数据库

Choose `analysis_v0.10.0/telemetry_analysis.db` to view already-generated results. Older compatible analysis databases can also be opened. Do **not** choose the session-root `telemetry.db`.

Select a session, driving segment, reference lap, and comparison lap. The charts show cumulative time difference, speed, throttle, and brake against track distance. Available metrics include sectors, maximum speed, input usage, braking, gear changes, and steering corrections.

Driving segments describe garage/on-track boundaries; they are not necessarily identical to tyre stints. Sessions without comparable laps can show quality/status information but not complete comparison curves. Expand the lap-status table for details. Maximize the window for detailed charts, especially on small high-DPI screens.

### Time-gain/loss regions and quality details

The time-region tab splits reliable common coverage into gain, loss, and small-change regions. Select a region to zoom the charts and compare entry/minimum/exit speed, braking, throttle recovery, and gear over the same boundaries. These are observed differences, not automatic explanations of why a lap was faster.

The official lap-time difference is shown separately from the difference supported by reliable regions. The residual is split arithmetically into start, unsupported interior, and finish contributions; it is not forced to zero or presented as a proven driving loss. Endpoints are limited to actual supported data, with no extrapolation.

Double-click a lap in the quality/status table for coverage, sample cadence, missing-frame evidence, recorded game context, and Raw logical offsets. Logical offsets are not physical byte positions in a compressed file. Missing Lap Data can only be associated approximately by neighboring frame ranges. Evidence lists are capped; their counts may exceed the number of displayed entries.

Known incomplete frames and suspicious cadence gaps are not bridged by interpolation, lines, or driving-event extraction. Large gaps or invalid essential fields can exclude a lap; finer gaps leave metrics limited to observable portions. A warning does not measure exact UDP packet loss, and a pause in wall-clock reception does not alone prove network loss. Tyre-compound differences and wear differences of at least five percentage points prompt a comparison warning; fuel, weather, traffic, assists, and setup can also matter.

### Resource protection

Raw capture has priority. At 60% write-queue usage, derived background work pauses between batches and resumes below 25%. Below 1 GiB free disk space, it pauses and warns; below 128 MiB, reception stops gracefully and the window stays open. An analysis lag of at least 10,000 packets warns. Already-running transactions are not forcibly interrupted.

These are best-effort safeguards, not guarantees against a disk suddenly filling, hardware faults, forced termination, or packets lost before reception. Queue overflow is reported explicitly rather than hidden. The recording metadata includes sampled resource-pressure counters, free-space minimum, and backlog peaks; these are not packet-loss counts or exact pause durations. Retain the recording and use session analysis later to catch up.

## 6. Compressing older archives

New recordings are already losslessly compressed by default. **压缩旧 Raw** (compress old Raw) is intended mainly for older uncompressed recordings.

Stop recording first. Select the old Raw and a **new** output filename. The tool checks packet bytes and receipt information against the original. Keep the original; do not overwrite it or replace it beside an old index that depends on different offsets.

## 7. Troubleshooting

- **No UDP data:** check telemetry enabled, matching port, destination IP, firewall, and another application occupying the port.
- **Data arrives but no laps:** finish a full lap and check that Session History packets are being received.
- **No comparison curves:** inspect lap status. Invalid, partial, or insufficiently covered laps may not qualify.
- **Database won't open:** use `telemetry_analysis.db`, not `telemetry.db`.
- **Old Raw conversion is slow:** let the first foundation conversion finish; reopening can reuse it.
- **Missing application files:** restore the entire packaged folder, including `_internal`.
- **Background analysis paused:** Raw capture can continue independently. Safely close and retain the whole recording for diagnosis/rebuild.
- **Forced termination or power failure:** an incomplete tail may be recoverable only as a verified prefix. Checksum corruption is not silently ignored. Normal X/safe-stop closing is safer but cannot guarantee survival of unwritten data during abrupt termination.

When reporting issues, redact personal paths, player identities, source IPs, and any sensitive log content. Do not upload a complete private recording by default.

## 8. Upgrading

Keep older application folders and recordings. Extract the complete new distribution separately. With both versions closed, you may copy the old `config/settings.json` if you want the same settings. Select older session folders in place; do not copy large recordings unnecessarily. Do not run two versions on the same port.

Opening an older analysis database stays read-only. Continuing its incremental analysis adds derived quality tables and updates completed-lap metrics from stored samples without replaying all historical Raw. It cannot recreate evidence that an older version never stored. For full historical provenance, retain the old database and generate a separate full Raw rebuild using a separate output directory (available through the analysis command-line output option). Raw and foundation formats are unchanged; the derived analysis schema is now version 4.
