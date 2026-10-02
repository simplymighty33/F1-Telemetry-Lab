# ADR-0001: Preserve Raw and resumable derived data

Date: 2026-10-02
Status: Accepted — retrospective record of existing behavior, not a new implementation decision.

## Context
UDP input cannot be requested again. Protocol decoding and analysis can fail or fall behind; Flashback can invalidate previously useful observations. The existing project therefore separates original capture from derived data.

## Decision
Preserve original datagrams and source metadata in Raw. Decode/display/background-analysis failures do not justify deleting original evidence. Raw write failures and queue overload remain visible failures. Prioritize Raw shutdown, publish safe readable ranges, and commit derived rows with recovery progress transactionally. Mark superseded Flashback branches without deleting Raw; default analysis uses the effective timeline. Preserve old archive formats and read-only browsing of existing analysis databases.

## Alternatives
Store decoded values only: smaller surface, but loses unsupported fields and replay evidence. Couple analysis to reception: simpler scheduling, but analysis stalls threaten capture. Delete superseded Raw: less storage, but prevents audit and changed decoding.

## Consequences
Storage and caches need separate version/recovery contracts. Tests must distinguish capture failure from background pause, physical from logical offsets, and original observation from resampled data. Sudden power loss may still lose queued or uncommitted tail data; do not promise zero loss.

## Evidence
- ../RAW_ARCHIVE_FORMAT.md — CRC, offsets, fsync, recovery and conversion.
- ../FOUNDATION_v0.8.0.md — safe watermarks, transactions and Flashback.
- ../INCREMENTAL_v0.10.0.md — incremental progress and shutdown.
- ../../collector/pipeline.py, ../../storage/foundation.py, ../../analysis/incremental.py.
