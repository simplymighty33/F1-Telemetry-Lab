# RaceTrace Lab 2.0

Application version: **2.0.0**. Windows package: **racetrace2.0.zip**. Previously published as F1 Telemetry Lab; the repository address is unchanged. [中文完整更新说明](RELEASE_2.0.md).

## Main changes since 1.0.3

- **New identity and Qt interface:** RaceTrace Lab name/icon; live capture, lap comparison and practice-review pages; centralized Chinese/numeric typography, high-DPI drawing and responsive scrolling. Throttle and brake comparisons have separate plots with shared distance/cursor inspection. Lap labels show track/stage, lap number, tyre wear and time without the former “总” prefix.
- **F1 25 compatibility fix:** updated Motion and Car Telemetry layouts use 24-car arrays and the correct record sizes/units. Updated F1 25 can emit 2026-format packets even when the car uses 2025 regulations. Game, wire format and car regulations must not be conflated. Full-array and fast-player decoding share schema/conversion logic; unknown layouts remain Raw rather than being guessed.
- **Time-trial restart handling:** return-to-garage/restart attempts with the same lap number are split, then the latest complete attempt matching official history is selected. Garage teleport/abandoned attempts do not replace the matching completed lap; a trailing incomplete attempt does not erase an earlier completed result. Quality checks still reject genuine gaps and unreliable attempts.
- **Consistent downstream results:** comparisons, practice reports and quality provenance use the selected attempt. Decoder rejections are visible separately from Raw storage failures.
- **Safe cache revision:** fresh foundation v3 and `analysis_v2.0/` outputs avoid reusing earlier missing-channel results. Raw v1/v2, source identity, Flashback, safe draining and existing read-only analysis browsing are preserved.
- **Maintenance and packaging:** shared conversion/path logic, less repeated snapshot work, synchronized version/EXE metadata and default Qt entry points. Explicit CLI and Tk fallback remain available in source. Shorter package paths retain full license texts and their reversible filename/hash inventory.

## Download and upgrade

Download [racetrace2.0.zip](https://github.com/simplymighty33/F1-Telemetry-Lab/releases/download/v2.0.0/racetrace2.0.zip), extract the entire folder to a **new writable location**, and run `RaceTraceLab.exe`. Keep `_internal/` and the accompanying license files. The interface is currently Chinese; no Python installation is needed.

**Old F1 25 results are not repaired by opening their analysis database.** Keep the original Raw and select the original Session for build/rebuild; use independent reconstruction to exclude stale caches. Existing files/results are not overwritten. Truly missing packets, unknown layouts or corrupt bytes cannot be recovered by this decoder fix.

## Validation

- **255 unittest regression tests passed.**
- A saved F1 25 recording containing **230,335 packets**, with a pre-2026-regulation car emitting the updated 2026 format, fully decoded and replayed with matching Headers/Raw checks. **33,114 player frames**, no missing joined frames in that recording, and **three completed laps recovered as analyzable**. Original user data was unchanged; work used isolated copies.
- Full replay and incremental reconstruction matched across 17 persisted tables.
- Packaged Windows EXE checks for 2023 and 2026 synthetic fixtures each sent/received/persisted/decoded **400 packets**, with exact Raw/Header replay and safe closing.
- Logical layout matrix: 1366×768 and 1920×1080 at DPR 1/1.25/1.5/1.75/2, plus small-window and F1 25 states. Simulated scaling is not physical mixed-monitor acceptance.
- Windows ZIP: 347 files; archive CRC and per-file SHA-256 checked; longest archive entry 119 characters. No recordings, private settings/logs or font files are bundled.

## Boundaries

New-EXE in-game endurance, additional F1 24/25 modes, clean-Windows and physical mixed-DPI tests remain pending. This is not evidence of zero UDP loss on every machine or protection against power loss/force-kill. No measured gaming-FPS/CPU improvement is claimed.

Time-trial matching uses conservative coverage/end-position/clock consistency thresholds, not a promise of measurement precision. Unsupported quality evidence remains visible; real gaps are not smoothed away. Sprint/Race identification remains uncertain where evidence is insufficient.

The map test program remains **independent** and is not bundled/integrated here. Main-app track maps, ERS deployment curves and AI setup/driving recommendations are not part of this release.

No application source-code license has been selected. Included third-party components retain their own licenses; see [notices](qt-third-party-notices.md). This is an unofficial enthusiast project, not affiliated with EA SPORTS, Codemasters, Formula 1 or SimHub.
