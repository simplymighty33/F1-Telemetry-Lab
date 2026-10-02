# F1 Telemetry Lab 1.0.3

This release consolidates the local 1.0.3a/b/c milestones and the completed code-review improvements. Previous public releases remain available. The interface is still Chinese; English and Chinese user guides are included. This is an unofficial enthusiast project, not affiliated with EA SPORTS, Codemasters, Formula 1, or SimHub.

## Features and fixes

- Practice-review summaries, driving-segment filters, comparison-condition evidence, repeated observations and privacy-conscious diagnostic summaries.
- Track/stage/session-lap context in lap labels, alongside tyre compound/wear. Sprint/Race remains explicitly uncertain when evidence is insufficient.
- Raw archives routed by `(packetFormat, sessionUID)`, named with first receipt time, game, first confirmed track/stage and UID. Late packets return to the matching archive; unidentified input remains saved. Old recordings are not renamed or migrated.
- Frame assembly checks player index, protocol and game frame ID. Mixed identities become quality issues, not effective telemetry samples. Low-frequency state is isolated by identity and cannot fill from future frame/time evidence.
- Display faults no longer stop otherwise healthy Raw capture; persistent warnings remain visible. Disk-write failure and queue overload still stop capture explicitly.
- Slow Raw writes no longer hold the status-read lock. A newly opening Session does not show the previous Session's background progress. Derived shutdown faults are reported without deleting committed Raw.
- Incremental publication skips repeated historical lap inserts and updates affected Sessions/laps/segments. A semantic revision distinguishes actual result changes from packet counts or countdown changes.
- Lap comparisons run on one bounded worker with one latest pending request and up to four cached results. Stale results cannot replace a newer selection. Same-lap refreshes retain chart focus and matching region selection; the lap list retains browsing position.
- Algorithm compatibility signatures and independent `analysis_v1.0.3/` output prevent mixed-version writes. Incompatible or moved legacy caches offer confirmed independent reconstruction into a new directory, preserving old results.
- Database input is validated before replacing the current view. Versioned analysis discovery is supported; non-finite/excessive wait settings are rejected.
- Offline foundation/analysis/compression jobs expose stage/count progress and cooperative cancellation. Uncommitted analysis rolls back; committed progress remains resumable. Cancelled compression preserves its source and does not publish an unverified copy.
- Offline event exports stream to disk; error totals remain exact while detailed error samples are bounded to 200 entries.

No new third-party runtime dependencies, deliberate Raw downsampling, deletion of AI arrays, map assets, overlay, automatic coach or setup recommendations were added. Raw v1/v2 and Flashback branch semantics remain supported.

## Verification

The local release source and the independent public source copy each passed **192 regression tests**. Tests include mixed identity, checkpoint recovery, repeated history, legacy-cache rebuilding, cancellation, view preservation, Session archive routing and shutdown fault cases.

The packaged EXE completed help, offline audit and compression checks on a **35-packet synthetic recording**. Replayed payload bytes, receipt times, source IPs and CRC values matched the source. ZIP CRC and per-file SHA-256 checks passed. These are scoped fixture results, **not a fresh real-game endurance test** of 1.0.3. Real-game testing of F1 24/25/2026 remains needed; forced termination, power loss and disk hardware failure are not covered by a zero-loss guarantee.

On the same machine, five repeated-history-only synthetic updates averaged approximately 10.30 → 6.70 ms with 100 completed laps and 22.65 → 8.61 ms with 500 laps. At 500 laps, SQL statements per update fell from 1,037 to 14 and historical INSERT statements from 1,000 to zero. Necessary timestamp updates remain. This does not measure full Raw reconstruction throughput or game FPS. One unchanged-content UI callback measurement fell from 277.757 to 0.681 ms with chart focus retained; it is one observation, not a general speedup guarantee.

## Download and upgrade

Download **F1TelemetryLab-1.0.3-Windows-x64.zip**, extract the entire folder, and run **F1TelemetryLab.exe**. Keep `_internal/` beside it. Python is not required. GitHub's automatic Source code archives are not Windows applications.

Keep previous program folders and recordings. Copy settings only with both versions stopped; do not run competing listeners on one port. Stop all capture and processing before copying an entire Session, including metadata and any remaining SQLite companion files.

Existing databases remain readable. New incompatible algorithm results are not mixed into old caches. First reconstruction may require a full Raw read; later processing reuses compatible progress. Cancellation waits for safe boundaries and may need time for an ongoing I/O/flush/lap calculation. Raw is authoritative; checksums and cache identity are not cryptographic tamper protection.

ZIP size: **11,157,890 bytes** (approximately 10.6 MiB).

ZIP SHA-256:

```text
6B7973D79914D112468F9992E77A0D19F95B0B0B6471DE0FEDFE361D460ABD40
```

EXE SHA-256 (inside the extracted folder):

```text
E8D6CC7A42160855937361B32973D8CAB3E6E760A8A1640979B641B5BF8A234A
```

Private recordings, logs, personal configuration, backups and machine-specific agent settings are excluded. Public visibility is not a license grant; no open-source license has been selected. Feedback is welcome through Issues, with identities, IP addresses and private paths removed.
