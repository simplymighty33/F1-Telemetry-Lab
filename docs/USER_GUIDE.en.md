# F1 Telemetry Lab 1.0 — User Guide

The current Windows interface is Chinese. The English descriptions below include the relevant Chinese button labels.

## 1. Installation

Use a complete packaged Windows distribution, not GitHub's source ZIP. Extract the whole `F1TelemetryLab-1.0` folder and keep `F1TelemetryLab.exe` beside `_internal`. Python is not required for the packaged application.

Choose a writable folder with enough free disk space. On first startup, the application creates `config/`, `data/`, and `logs/` beside the EXE. Download the [complete Windows ZIP](https://github.com/simplymighty33/F1-Telemetry-Lab/releases/download/v1.0.0/F1TelemetryLab-1.0-Windows.zip) from [Release v1.0.0](https://github.com/simplymighty33/F1-Telemetry-Lab/releases/tag/v1.0.0).

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
- `analysis_v0.10.0/telemetry_analysis.db`: incremental lap-analysis results. The historical directory name is intentionally retained in 1.0 for compatibility.

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
