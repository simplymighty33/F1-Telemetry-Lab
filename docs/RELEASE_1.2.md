# F1 Telemetry Lab 1.2 — capture protection and analysis evidence

Four focused changes, no third-party runtime dependency and no external code copied:

1. Read-only comparisons add actual supported common endpoints and gap edges to the existing distance grid. No extrapolation or synthetic driver inputs. The official delta is conserved as supported region delta plus residual components (start, unsupported interior, finish). Components are arithmetic quantities, not proven causes.
2. Derived schema 4 adds compact lap quality details, incomplete frame evidence, observed frame Raw logical-offset ranges and sparse game context transitions. Missing Lap Data has only approximate frame-range lap association. Flashback supersedes frame evidence as well as player samples. Detail lists are capped; complete frame evidence stays in its indexed table.
3. Stable driving uses a local game-clock median over adjacent intervals (at least eight positive intervals), with a warning threshold of max(120 ms, 3.5 × median). Wall-clock silence is not packet loss. Pit/driver transitions are excluded from cadence inference. Known incomplete frames and suspicious cadence intervals are not bridged by plots, interpolation or event extraction; small gaps do not alone reject the whole lap. Coarse 50 m/1500 ms gates remain.
4. GUI resource checks run independently of reception: 60% Raw queue pressure pauses derived work at batch boundaries; recovery below 25% resumes. Less than 1 GiB free pauses derived work and warns; less than 128 MiB stops reception gracefully. Lag of at least 10,000 packets warns. UI history snapshots coalesce without discarding Raw or authoritative lap history. Derived transactions and resume checkpoints remain durable.

## Compatibility and limitations

Opening old analysis databases is read-only. Continuing old incremental analysis performs an additive derived migration and recalculates completed laps from stored samples, without replaying historical packets. Previously unrecorded incomplete frames and context transitions cannot be recreated by migration; use a separate full Raw rebuild for those. Raw and foundation formats are unchanged, as is `analysis_v0.10.0`.

Resource checks are best-effort, not a disk-failure or network-loss guarantee. Already-running analysis transactions are not forcibly interrupted. Queue overflow stops explicitly and counts unqueued data. Disk exhaustion, power loss, forced termination and packets lost before reception cannot be solved by interpolation. At coarse gaps a lap is rejected; at finer gaps metrics describe only observed portions.

GUI recording metadata retains sampled resource-pressure counters, minimum free space and derived backlog peaks in `summary.resource_health`. The pressure check count is neither a dropped-packet count nor an exact pause duration.

## Verification

139 automated regression tests passed. Isolated historical Raw/cache/incremental replay results are saved in `outputs/quality_1.2_long/verification.json`; final frozen executable verification is in `outputs/release_1.2_final_checked/release_verification.json`. All 16 checked result tables match across the three paths for 624,609 packets, 12 Flashbacks and 11 completed laps. All 55 lap pairs conserve the official delta. Median comparison time was 0.245 seconds on this machine (not initial rebuild time). Raw hash remained unchanged. These are local repeatable tests, not a new live-game endurance test.

In the previously discussed lap 1 vs lap 2 pair, extending supported boundaries changed residual +26.911 ms to -8.589 ms. This is a smaller unresolved boundary/timing quantity, not proof that all timing errors are solved. Raw size and bytes are unchanged; the example analysis database grew about 8.2 MB for provenance and quality data.
