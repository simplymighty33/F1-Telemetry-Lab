# ADR-0003: Identity-safe analysis, semantic revisions and bounded UI work

Date: 2026-10-02. Status: Accepted for 1.0.3 review remediation.

Frame assembly retains existing single-player database keys, but rejects mixed protocol/player/frame identities rather than combining them. Fallbacks bind to UID/protocol/player and cannot use future game state. Raw and foundation evidence remain unchanged.

Incremental publication stores a transactional content revision. Repeated history updates preserve the latest receipt stamp with one UID-scoped update rather than rewriting every lap/tyre record; unaffected UIDs, metrics and segment rows are not rebuilt. Session countdown is not a semantic comparison revision.

The GUI uses that revision instead of packet counts. A single worker has at most one latest pending request and one result; it caches four comparisons and ignores stale results. Selection/viewport survives unchanged identities. Different selections clear old traces while waiting. All Tk mutations remain on the UI thread; repository comparisons retain a single SQLite snapshot.

Full/incremental analysis share decoder/schema/engine/event/threshold/parameter vocabulary. Quality-version upgrades remain an explicit derived-data upgrade path. Missing/mismatched algorithm contracts are refused for writes; old results remain read-only. Default 1.0.3 output is separate. For moved legacy caches, independent reconstruction verifies Raw into fresh foundation/results rather than relaxing identity guards. New archive identity/relative paths remain supported.

Status readers use single-writer published references, never its disk I/O lock. Provisional/rotating state may report waiting, but derived consumers start only after immutable paths are ready. Activity is published before new-session I/O so prior progress cannot masquerade as new-session progress.

Offline cancellation occurs between safe records/batches/laps. Incremental cancellation rolls back the active transaction and retains prior progress; compression cancels before publication and keeps its source. Filesystem operations and individual computations are not forcibly interrupted.

Trade-offs: first new-algorithm processing and independent reconstruction have real cost; snapshots are not game-endurance proof; cache identity is not cryptographic tamper authentication. No framework/runtime dependency, raw format or capture-filtering change is needed.
