# F1 Telemetry Lab

A Windows telemetry collector and lap analysis tool for **EA SPORTS F1 23, F1 24, and F1 25**.

Built by a hobbyist developer for players who want to review their driving after a few practice laps or after a session. The current application focuses on reliable recording and foundational lap analysis—not an overlay, automated race engineer, or AI driving coach.

**Application version:** 1.0.3

**Interface language:** Chinese. This repository provides English documentation; the application interface has not yet been translated.

Version 1.0.3 consolidates the local a/b/c milestones, practice-review improvements, Session-based archives, and the completed reliability/performance review. Those suffixes were not public GitHub releases. Previous releases remain available.

## Features

- **Automatic protocol detection:** F1 23, F1 24, F1 25, and F1 25's 2026 Season Pack formats. Customize the UDP port in the application.
- **Lap history:** lap and sector times, compound, end-of-lap tyre wear, and independent tyre-set lap numbering. Newest laps appear first.
- **Lap comparison:** distance-aligned speed, throttle, brake, and cumulative time difference, with braking and gear-shift events and basic driving metrics.
- **Time-gain/loss regions:** linked chart zoom and common-boundary driving comparisons, with unsupported timing residuals shown separately rather than forced into a driving explanation.
- **Quality evidence:** gap/cadence and essential-field checks, per-lap quality details, Raw logical offsets, and pause/session/Flashback context. Unreliable gaps are not bridged by analysis curves or events.
- **Capture protection:** Raw-first resource priorities, background pause/resume under queue pressure, disk-space warnings and graceful stopping, plus explicit backlog/overflow reporting.
- **Background analysis:** completed laps are processed incrementally. Reuse existing results instead of replaying the entire recording each time, and filter by driving segment.
- **Practice review:** driving-segment summaries, comparison-condition evidence, repeated observations, and privacy-conscious diagnostic summaries.
- **Session archives:** route recordings by protocol and game Session UID, with track/stage information in folder names and lap labels; late packets return to their matching archive.
- **Responsive browsing:** bounded background comparisons, preserved chart focus and lap-list scroll, and safe cancellation/progress for offline jobs.
- **Flashback awareness:** keep the original packets while withdrawing affected analysis and updating results from the final valid timeline.
- **Lossless Raw archives:** independently compressed blocks, checksums, and offline Replay. Recorded packet bytes and Headers can be read again without the game running.
- **Graceful shutdown:** both the close button and the window's X wait for recording and background tasks to close safely.
- **Standalone Windows build:** no separate Python installation required. Keep the EXE and its `_internal` folder together.

## Getting started

**[Download F1 Telemetry Lab 1.0.3 for Windows](https://github.com/simplymighty33/F1-Telemetry-Lab/releases/download/v1.0.3/F1TelemetryLab-1.0.3-Windows-x64.zip)**. See the [1.0.3 release page](https://github.com/simplymighty33/F1-Telemetry-Lab/releases/tag/v1.0.3) for release information and checksums.

Download `F1TelemetryLab-1.0.3-Windows-x64.zip` from the release assets. GitHub's **Code → Download ZIP** and automatically generated **Source code** archives are not packaged Windows applications.

ZIP SHA-256:

```text
6B7973D79914D112468F9992E77A0D19F95B0B0B6471DE0FEDFE361D460ABD40
```

Once you have the complete Windows distribution:

1. Extract the whole `F1TelemetryLab-1.0.3` folder into a writable location.
2. Enable UDP Telemetry in the game. Use its native UDP format, a matching port (default `20777`), and preferably a 60 Hz send rate.
3. For the same PC, send UDP to `127.0.0.1`. For another PC, use the collector PC's LAN IPv4 address.
4. Run `F1TelemetryLab.exe`, complete a few laps, and open the lap analysis window.
5. Close using the safe-stop button or X, and wait for the saving dialog to finish.

See the [English user guide](docs/USER_GUIDE.en.md) and [1.0.3 release notes](docs/RELEASE_1.0.3.en.md). Both Chinese and English guides are included in the Windows distribution.

## Reliability and limitations

The 1.0.3 implementation passed **192 regression tests** in both the local release tree and the independent public source copy, including frame identity, cache compatibility, cancellation, UI-state preservation, Session routing, and shutdown fault cases. Packaged EXE checks covered help, offline audit and compression of a **35-packet synthetic recording**; replayed bytes, receipt times, source IPs and CRC values matched. The release ZIP passed CRC and per-file SHA-256 checks. Earlier F1 23 long testing covered a 624,609-packet recording; that historical result is not a new endurance test of 1.0.3.

These checks are evidence for specific tested recordings and fixtures, **not a guarantee of zero UDP loss on every machine**. UDP delivery, storage speed, network configuration, and forced termination can still affect a recording. Real-game long-duration validation has been performed for F1 23; the other protocol formats have fixture-based checks and need further real-game testing.

Other current boundaries:

- Enabling UDP and setting the destination IP/port in the game remain the user's responsibility.
- First-time processing of an older Raw archive can take longer because its analysis foundation must be built.
- Only sufficiently complete laps are eligible for comparison. Incomplete or invalid laps may have a status but no comparison curves.
- Derived databases add storage overhead even though Raw data is compressed.
- Fine-gap checks cannot measure exact packet loss or recover missing packets. Unsupported timing components are arithmetic quantities, not proven causes; observed driving differences are not automatic coaching.
- Older analysis databases remain readable. New incremental output uses `analysis_v1.0.3/` with an algorithm compatibility signature. Incompatible/moved caches offer an independent rebuild into a new directory, without overwriting the old result. First reconstruction may require a full Raw read; later work reuses compatible progress.
- Mixed player/protocol/frame channels are reported as quality issues, not used as valid merged telemetry. Missing evidence is not invented. CRC/source identity are not cryptographic tamper protection.
- Sprint/Race classification remains explicitly uncertain when available evidence is insufficient; labelled sprint-weekend recordings are still needed.
- Cancellation is cooperative; it does not forcibly interrupt an ongoing system I/O, flush, or individual lap calculation.
- Graceful closing cannot protect unwritten buffers against a power failure or forced process termination. A verified archive prefix may be recoverable.
- This release does not include a packaged track map library, setup recommendations, or automatic driving diagnosis.

## Feedback

Bug reports and suggestions are welcome through [Issues](https://github.com/simplymighty33/F1-Telemetry-Lab/issues). Please include the application version, game/protocol, Windows version, steps to reproduce, and a screenshot if helpful. **Do not publicly upload full recordings, personal configuration, IP addresses, player identities, or unredacted logs.**

## Project status and licensing

The 1.0.3 source code, tests, and Windows packaging configuration are included in this repository. The packaged Windows application is available in [Release v1.0.3](https://github.com/simplymighty33/F1-Telemetry-Lab/releases/tag/v1.0.3). Previous v1.0.0 and v1.0.2 releases remain available.

### Run from source

Python 3.10+ is required. Runtime code uses the standard library; PyInstaller is only a build dependency.

```powershell
python -m collector.gui_main
```

Run the regression suite:

```powershell
python -m unittest discover -q
```

Build a Windows distribution on Windows:

```powershell
.\build_windows.ps1
```

The build produces `dist/F1TelemetryLab-1.0.3/`. Preserve the entire folder when distributing it. The packaging specification retains its historical `F1TelemetryCollector.spec` name for compatibility.

Source modules: `collector/` (application and workers), `storage/` (Raw archives and foundation), `decoder/` (protocols), and `analysis/` (lap reconstruction and comparison). See [CONTRIBUTING.md](CONTRIBUTING.md) for feedback and development guidelines.

No open-source license has been selected. Public visibility should not be interpreted as a license granting unrestricted reuse.

This is an independent, unofficial hobby project, not affiliated with or endorsed by EA SPORTS, Codemasters, Formula 1, or SimHub.
