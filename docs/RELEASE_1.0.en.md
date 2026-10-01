# F1 Telemetry Lab 1.0

Internal version: **1.0.0**.

This release gives F1 Telemetry Collector its new name, **F1 Telemetry Lab**, and improves the lap-analysis window. It does not introduce a new telemetry protocol or driving-analysis algorithm.

## What's new in 1.0

- New application branding, window titles, and Windows product/file properties.
- New executable name: `F1TelemetryLab.exe`.
- Larger, screen-constrained initial analysis window, with an adaptive minimum size.
- Toolbar wrapping and more flexible session/lap/segment selectors.
- Protected space for top controls, chart instructions, and the bottom status area.
- Hidden empty quality tables before data is loaded, with an explicit expand/collapse control.
- Horizontal and vertical scrolling for driving metrics, and wrapping for long status messages.

Small high-DPI screens should keep controls accessible, but detailed chart inspection may still require maximizing the window. Unusual multi-monitor and taskbar configurations need further testing.

## Existing capabilities included

Automatic F1 23/24/25 protocol detection, support for the F1 25 2026 Season Pack format, configurable UDP ports, lap and sector history, tyre metadata and tyre-set lap numbering, losslessly compressed Raw storage, offline Replay, Flashback-aware timelines, distance-based lap comparisons, incremental background analysis, driving-segment filters, and graceful close dialogs.

The UI remains Chinese. English repository documentation does not imply an English application interface.

## Compatibility and upgrade

Raw formats, foundation and analysis schemas, and incremental-analysis semantics are unchanged. The `analysis_v0.10.0` cache path is deliberately retained. Existing recordings and compatible analysis databases remain usable without an automatic migration or forced rebuild.

Keep older releases. Copy the **whole** `F1TelemetryLab-1.0` folder, including `_internal`. New settings/data/logs live beside the new EXE. Copy old settings only while both versions are closed. Do not run two versions on the same port.

## Verification

- 102 regression tests passed for the local 1.0 build.
- Packaged-application checks covered branding, empty analysis startup, an older analysis database, port persistence, tyre display, and safe-close dialogs.
- A synthetic multi-format check recorded 472 packets, with matching Raw/foundation/incremental processing counts and no decode errors.
- Earlier F1 23 long-recording validation covered 624,609 packets and 12 Flashbacks; incremental results matched all 12 full-Replay reference analysis tables.

The synthetic protocol checks are not real-game long-duration tests for every supported game. Neither test success nor graceful shutdown guarantees zero network loss or protection from sudden power failure.

## Distribution status

**[Release v1.0.0](https://github.com/simplymighty33/F1-Telemetry-Lab/releases/tag/v1.0.0) is published.** Download [F1TelemetryLab-1.0-Windows.zip](https://github.com/simplymighty33/F1-Telemetry-Lab/releases/download/v1.0.0/F1TelemetryLab-1.0-Windows.zip) (11,290,783 bytes, approximately 10.8 MiB). Extract the entire folder before running the EXE. GitHub's automatically generated Source code archives are not packaged Windows applications.

The release tag points to the tested 1.0 source publication. Post-publication download-link updates are on `main`; the release tag and binary have not been rewritten.

SHA-256 of the **Windows ZIP**:

```text
1D3D086C5A09D5338FFED0E62968A45640A5DFD10A737203652180B4917CB79F
```

Expected full-folder name: `F1TelemetryLab-1.0`.

SHA-256 of the verified local **EXE**, not a ZIP:

```text
B43DFE35E4EB44B13C699C93F77315C0ADAF43DF9DE3EEF81243B83AAECB3792
```
