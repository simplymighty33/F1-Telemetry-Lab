# ADR-0002: Per-game-session Raw archives with stable analysis paths

Date: 2026-10-02
Status: Accepted for the user-requested Session-based recording and directory naming change.

## Decision

Route received datagrams on the single disk writer by `(packetFormat, sessionUID)`. Keep unknown input in an unassigned archive. Park inactive Raw/index handles and retain writer state, logical offsets and the capture lease. Late packets resume that unchanged archive instead of creating another file or contaminating a new UID. New runs always create unique directories; no historical migration is attempted.

Use timestamp, compact game label, localized track/stage and full hexadecimal UID in the directory name. Label provisional directories before consumers start, then keep paths fixed. Persist identity, original names, mode, link identifiers and context history in metadata. New self-contained incremental caches bind to UUID identity and relative paths so a complete directory can be copied without treating an unrelated Raw as the same source. Legacy cache guards remain unchanged.

A background manager closes/restarts one pair of derived consumers independently of UDP and Raw writing. Retired-session lag is resumable offline. Suspended sessions keep their live lease until shutdown because late packets are still possible.

## Trade-offs

Renaming on every context update would break open Windows handles and cache paths. Waiting in RAM for the first Session packet would threaten Raw-first capture. A single ever-growing Raw is simpler but makes historical stages hard to locate. Keeping all Raw/index handles and analysis workers open would grow resource usage across many sessions.

The selected approach keeps incoming bytes, stable derived sources and a bounded active worker set. Each known session retains small routing/counter state and one capture lease. The file name represents the first confirmed context, not proof that every game phase receives a different UID. Unexpected I/O failures are fatal and visible; metadata naming/decoder failures do not authorize dropping packets.

## Verification

Public SessionCapture, CapturePipeline and CollectorService tests cover UID separation, late packets, unassigned bytes, protocol separation, collisions, locks, decoding faults, name completion, port switching, relocated new caches and full Raw Replay with Flashback. Real-game endurance is a separate acceptance step.
