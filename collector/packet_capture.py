"""Session lifecycle and coordinated raw/indexed packet capture."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

from collector.udp_receiver import ReceivedDatagram
from decoder.header import HeaderDecodeError, decode_header
from decoder.packet_parser import packet_name
from decoder.protocol import game_label
from storage.database import TelemetryDatabase
from storage.raw_writer import RawPacketWriter
from storage.lease import FileLease


def _utc_iso(timestamp_ns: int | None = None) -> str:
    timestamp = (
        datetime.now(timezone.utc)
        if timestamp_ns is None
        else datetime.fromtimestamp(timestamp_ns / 1_000_000_000, timezone.utc)
    )
    return timestamp.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _create_session_directory(root: Path, local_now: datetime | None = None,
                              base_name: str | None = None) -> Path:
    now = local_now or datetime.now().astimezone()
    base_name = base_name or f"session_{now:%Y%m%d_%H%M%S}"
    root.mkdir(parents=True, exist_ok=True)
    for suffix in range(10_000):
        name = base_name if suffix == 0 else f"{base_name}_{suffix:02d}"
        candidate = root / name
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError("could not allocate a unique session directory")


class PacketCapture:
    """Persist every datagram before adding its decoded header to SQLite."""

    def __init__(
        self,
        data_directory: Path,
        udp_port: int,
        track: str | None = None,
        session_type: str | None = None,
        database_batch_size: int = 256,
        raw_flush_every: int = 256,
        metadata_checkpoint_every: int = 1000,
        raw_compression: str = "zlib",
        raw_compression_level: int = 1,
        raw_block_bytes: int = 256 * 1024,
        directory_name: str | None = None,
        game_session_uid: int | None = None,
    ) -> None:
        self.session_directory = _create_session_directory(data_directory, base_name=directory_name)
        self.session_id = self.session_directory.name
        self.archive_identity = str(uuid4())
        self.start_time_ns = time.time_ns()
        self.start_monotonic_ns = time.monotonic_ns()
        self.end_time_ns: int | None = None
        self.udp_port = udp_port
        self.track = track
        self.session_type = session_type
        self.metadata_checkpoint_every = max(1, metadata_checkpoint_every)
        self.packet_counts: Counter[int | None] = Counter()
        self.packet_time_bounds: dict[int | None, list[int]] = {}
        self.format_counts: Counter[int] = Counter()
        self.game_counts: Counter[str] = Counter()
        self.packet_count = 0
        self.parse_error_count = 0
        self.software_drop_count = 0
        self.resource_health: dict[str, Any] = {}
        self.first_packet_monotonic_ns: int | None = None
        self.last_packet_monotonic_ns: int | None = None
        self._closed = False
        self._suspended = False
        self.game_session_uid = game_session_uid
        self.context_ready = False
        self.game_mode: str | None = None
        self.context_history: list[dict[str, Any]] = []
        self.naming_error: str | None = None
        self.context_error: str | None = None

        self._lease = FileLease(self.session_directory / "capture.lock")
        try:
            self.raw_writer = RawPacketWriter(
                self.session_directory / "raw_packets.bin", flush_every=raw_flush_every,
                compression=raw_compression, compression_level=raw_compression_level,
                block_bytes=raw_block_bytes,
            )
        except BaseException:
            self._lease.close()
            raise
        try:
            self.database = TelemetryDatabase(
                self.session_directory / "telemetry.db", batch_size=database_batch_size
            )
            self.database.begin_session(
                self.session_id,
                _utc_iso(self.start_time_ns),
                "raw_packets.bin",
                track,
                session_type,
            )
        except Exception:
            self.raw_writer.close()
            self._lease.close()
            raise
        try:
            self._write_metadata("recording")
        except BaseException:
            self.raw_writer.close()
            self.database.close()
            self._lease.close()
            raise

    def process(self, datagram: ReceivedDatagram) -> None:
        if self._closed:
            raise RuntimeError("capture session is closed")

        # Raw bytes are appended first. Header decoding must never gate capture.
        raw_reference = self.raw_writer.write(
            datagram.payload,
            datagram.received_at_ns,
            datagram.monotonic_ns,
            datagram.source_ip,
            datagram.source_port,
        )
        header = None
        parse_error = None
        try:
            header = decode_header(datagram.payload)
        except HeaderDecodeError as exc:
            parse_error = str(exc)
            self.parse_error_count += 1

        packet_id = header.packet_id if header else None
        self.packet_counts[packet_id] += 1
        bounds = self.packet_time_bounds.get(packet_id)
        if bounds is None:
            self.packet_time_bounds[packet_id] = [
                datagram.monotonic_ns,
                datagram.monotonic_ns,
            ]
        else:
            bounds[1] = datagram.monotonic_ns
        if header:
            self.format_counts[header.packet_format] += 1
            try:
                self.game_counts[game_label(header)] += 1
            except ValueError:
                self.game_counts[f"Unsupported format {header.packet_format}"] += 1
        self.packet_count += 1
        if self.first_packet_monotonic_ns is None:
            self.first_packet_monotonic_ns = datagram.monotonic_ns
        self.last_packet_monotonic_ns = datagram.monotonic_ns

        # Never commit an index batch whose raw block is still only in RAM.
        if self.database.pending_count + 1 >= self.database.batch_size:
            self.raw_writer.flush()
        self.database.add_packet(
            self.session_id,
            datagram.received_at_ns,
            datagram.source_ip,
            datagram.source_port,
            len(datagram.payload),
            raw_reference,
            header,
            parse_error,
        )
        if self.packet_count % self.metadata_checkpoint_every == 0:
            self.checkpoint()

    @property
    def has_pending(self) -> bool:
        return self.raw_writer.has_pending

    def suspend(self) -> None:
        if self._closed or self._suspended:
            return
        self.checkpoint()
        self.raw_writer.suspend()
        self.database.close()
        self._suspended = True

    def resume(self) -> None:
        if self._closed:
            raise RuntimeError("capture session is closed")
        if not self._suspended:
            return
        try:
            self.raw_writer.resume()
            self.database.resume()
        except BaseException:
            try:
                self.raw_writer.close()
            finally:
                self._lease.close()
            raise
        self._suspended = False

    def relocate(self, target: Path) -> None:
        """Label a provisional archive before any background consumer starts."""
        if self.context_ready:
            raise RuntimeError("cannot move an archive with active consumers")
        self.suspend()
        self._lease.close()  # Windows cannot rename a directory with this handle open.
        try:
            self.session_directory.rename(target)
            self.session_directory = target
            self.raw_writer.path = target / "raw_packets.bin"
            self.database.path = target / "telemetry.db"
        finally:
            self._lease = FileLease(self.session_directory / "capture.lock")
            self.resume()

    def _capture_duration_seconds(self) -> float:
        if self.first_packet_monotonic_ns is None or self.last_packet_monotonic_ns is None:
            return 0.0
        return max(
            0.0,
            (self.last_packet_monotonic_ns - self.first_packet_monotonic_ns) / 1_000_000_000,
        )

    def summary(self, status: str) -> dict[str, Any]:
        duration = self._capture_duration_seconds()
        packet_types: dict[str, dict[str, int | float]] = {}
        for packet_id, count in sorted(
            self.packet_counts.items(), key=lambda item: (-1 if item[0] is None else item[0])
        ):
            first_ns, last_ns = self.packet_time_bounds[packet_id]
            type_duration = max(0.0, (last_ns - first_ns) / 1_000_000_000)
            packet_types[packet_name(packet_id)] = {
                "packet_id": -1 if packet_id is None else packet_id,
                "count": count,
                "average_hz": (
                    round((count - 1) / type_duration, 3) if type_duration > 0 else 0.0
                ),
            }
        return {
            "status": status,
            "duration_seconds": round(duration, 6),
            "packet_count": self.packet_count,
            "parse_error_count": self.parse_error_count,
            "software_drop_count": self.software_drop_count,
            "resource_health": dict(self.resource_health),
            "average_total_packets_per_second": (
                round((self.packet_count - 1) / duration, 3) if duration > 0 else 0.0
            ),
            "packet_formats": {str(key): value for key, value in sorted(self.format_counts.items())},
            "detected_games": dict(self.game_counts.most_common()),
            "packet_types": packet_types,
            "storage": self.raw_writer.statistics(),
        }

    def _metadata(self, status: str) -> dict[str, Any]:
        detected_game = self.game_counts.most_common(1)[0][0] if self.game_counts else "Auto detect"
        detected_format = self.format_counts.most_common(1)[0][0] if self.format_counts else None
        return {
            "game": detected_game,
            "format": detected_format,
            "supported_formats": [2023, 2024, 2025, 2026],
            "session_id": self.session_id,
            "game_session_uid": str(self.game_session_uid) if self.game_session_uid is not None else None,
            "game_mode": self.game_mode,
            "archive_directory": self.session_directory.name,
            "archive_identity": self.archive_identity,
            "context_history": self.context_history,
            "naming_error": self.naming_error,
            "context_error": self.context_error,
            "start_time": _utc_iso(self.start_time_ns),
            "end_time": _utc_iso(self.end_time_ns) if self.end_time_ns else None,
            "udp_port": self.udp_port,
            "track": self.track,
            "session_type": self.session_type,
            "packet_count": self.packet_count,
            "raw_archive": "raw_packets.bin",
            "database": "telemetry.db",
            "raw_storage": self.raw_writer.statistics(),
            "summary": self.summary(status),
        }

    def _write_metadata(self, status: str) -> None:
        target = self.session_directory / "metadata.json"
        temporary = self.session_directory / "metadata.json.tmp"
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(self._metadata(status), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)

    def checkpoint(self) -> None:
        self.raw_writer.flush()
        self.database.flush()
        self.database.update_session_context(self.session_id, self.track, self.session_type)
        self._write_metadata("recording")

    def close(self, status: str = "complete") -> dict[str, Any]:
        if self._closed:
            return self.summary(status)
        self.resume()
        self.end_time_ns = time.time_ns()
        failure: BaseException | None = None
        try:
            self.raw_writer.flush()
            self.database.update_session_context(self.session_id, self.track, self.session_type)
            self.database.end_session(
                self.session_id, _utc_iso(self.end_time_ns), self.packet_count, status
            )
            self._write_metadata(status)
        except BaseException as exc:
            failure = exc
        finally:
            for resource in (self.raw_writer, self.database):
                try:
                    resource.close()
                except BaseException as exc:
                    if failure is None:
                        failure = exc
            self._closed = True
            self._lease.close()
        if failure is not None:
            raise failure
        return self.summary(status)

