# F1 Telemetry Lab 1.0.2

This public release consolidates **all changes from the local 1.1 and 1.2 development milestones** into version **1.0.2**. Those milestone numbers were not GitHub releases. This is not a feature rollback, and the existing v1.0.0 release is retained.

The interface remains Chinese. English and Chinese user guides are included beside the Windows EXE. There are no new third-party runtime dependencies.

## Lap comparison

- Distance-based time-gain/loss regions, chart zoom linked to region selection, and common-boundary driving comparisons: entry/minimum/exit speed, braking, throttle recovery, and gear.
- Actual supported start/end points and gap edges supplement the distance grid. No extrapolated driver inputs or fabricated finish-line telemetry.
- Official lap delta remains distinct from reliable-region delta. Unattributed time is preserved and split into start, unsupported-interior, and finish quantities. These are arithmetic contributions, not confirmed causes or automatic coaching.
- Essential-field, coverage, game-clock, and cadence checks prevent unsupported gaps from being connected by plots, interpolation, or event extraction.
- Old databases are revalidated from source samples for comparison, using one read snapshot. Tyre-condition differences produce warnings rather than causal claims.

## Evidence and capture protection

- Per-lap quality details, incomplete-frame evidence, Raw logical-offset ranges, and sparse pause/session/Flashback context. Double-click a quality row to inspect details without exposing player names, source IPs, or private paths.
- Flashbacks supersede affected sample and frame evidence. Missing Lap Data has only approximate neighboring-frame association; evidence lists are bounded.
- Background derived work pauses between batches under Raw write-queue pressure (60%, resuming below 25%) or low disk space (below 1 GiB). Below 128 MiB, reception stops gracefully and the window remains open.
- Analysis backlog warnings and coalesced UI snapshots reduce unnecessary work. Raw is not deliberately downsampled; queue overflow is explicitly reported.
- Recording metadata includes sampled resource-pressure and backlog summaries. These are not exact packet-loss counts or pause durations.

## Compatibility

Raw and foundation formats are unchanged. The existing `analysis_v0.10.0` directory name is retained. Derived analysis schema is version 4.

Opening an older analysis database is read-only. Continuing older incremental analysis adds quality tables and recalculates completed laps from stored samples, without replaying all historical Raw. Earlier unrecorded frame/context evidence cannot be recovered by migration; use a separate full Raw rebuild when that evidence is needed.

Keep old application folders and recordings. Extract the complete new folder, including `_internal`. Copy personal settings only with both versions closed; never run two collectors on the same port.

## Verification and limits

The consolidated 1.0.2 build passed **139 automated regression tests**. Packaged-application checks passed for version branding, collector and analysis windows, read-only opening of an existing analysis database, saved UDP-port reload, tyre-set numbering, and manual-close waiting dialogs. A synthetic multi-format check recorded **472 packets**, with matching Raw/foundation/incremental counts and no decode errors. That fixture has no complete comparable telemetry lap; region algorithms are covered by dedicated tests and the historical recording checks below.

The unchanged 1.2 analysis implementation was also verified against an F1 23 recording containing **624,609 packets, 12 Flashbacks, and 11 completed laps**: all 16 checked result tables matched across Raw Replay, foundation-cache analysis, and checkpointed incremental analysis; all 55 lap pairs conserved the official delta. Raw bytes and hash were unchanged.

The historical checks are not a new live-game endurance test. F1 24/25/2026 protocol coverage includes synthetic fixtures, not equivalent real-game long tests. Fine-gap detection is not an exact network-loss measurement. Resource checks and graceful shutdown cannot guarantee preservation of unwritten data after disk failure, power loss, or forced termination. Fine-gap metrics describe observable portions only. No track-map library, AI coach, setup recommendations, or overlay is included.

Implementation and tests were independently written. Other projects informed general analysis and boundary-case ideas; no third-party source code, map assets, or runtime libraries were imported for these changes.

## Download

Use the **Windows ZIP** attached to [Release v1.0.2](https://github.com/simplymighty33/F1-Telemetry-Lab/releases/tag/v1.0.2), not GitHub's automatically generated source ZIP. Extract the whole `F1TelemetryLab-1.0.2` folder before running `F1TelemetryLab.exe`. Release checksums are supplied on the release page.

Windows ZIP: `F1TelemetryLab-1.0.2-Windows.zip`, **11,330,847 bytes** (approximately 10.8 MiB).

ZIP SHA-256:

```text
4C8F877928B1E2ED8AF3571C809C0B7C1A2158ED3BE76AEC0BFA4BADD3EDD598
```

EXE SHA-256 (inside the extracted distribution, not the ZIP):

```text
616FC133675D6041A8A0017BF55BB75BF406960B05B78EAA4CC2BC7C2E1A37DD
```
