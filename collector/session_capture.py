"""Single Raw writer routing complete datagrams into game-session archives."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
import re
import threading

from collector.packet_capture import PacketCapture
from collector.udp_receiver import ReceivedDatagram
from decoder.header import HeaderDecodeError, decode_header
from decoder.full_parser import decode_packet
from decoder.display import track_label, stage_label
from decoder.protocol import PROTOCOLS
from decoder.packet_parser import packet_name


def _component(value, fallback):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", str(value or fallback)).strip(" .")
    return value[:32] or fallback


def _game_tag(header):
    year = header.game_year if header.game_year in {23, 24, 25, 26} else header.packet_format % 100
    if year == 26:
        return "F1-25-S2026"
    tag = f"F1-{year}"
    return tag if header.packet_format % 100 == year else f"{tag}-P{header.packet_format}"


class SessionCapture:
    """One directory per (protocol format, sessionUID), including late packets.

    Idle Raw/index handles are closed. Header failures go to a separate
    unassigned archive, never into a guessed game session. Routing is not filtering.
    """

    def __init__(self, data_directory: Path, udp_port: int, **options) -> None:
        self.root = data_directory
        self.port = udp_port
        self.options = options
        self._captures = {}
        self._active = None
        self._open = None
        self._lock = threading.RLock()
        self.packet_count = 0
        self.software_drop_count = 0
        self.resource_health = {}
        self._closed = False
        # A startup archive preserves shutdown diagnostics even before game data.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        waiting = PacketCapture(self.root, self.port, **self.options,
                                directory_name=f"session_{stamp}_未归属_等待游戏")
        waiting.archive_stamp = stamp
        self._captures[None] = waiting
        self._active = self._open = waiting

    @property
    def session_directory(self) -> Path | None:
        capture = self._active
        return capture.session_directory if capture else None

    @property
    def has_pending(self) -> bool:
        with self._lock:
            return self._open.has_pending if self._open else False

    def active_capture(self, *, ready_only: bool = False) -> PacketCapture | None:
        # Single writer publishes stable object references. Status readers must
        # never wait on its fsync/rename lock while holding the receiver lock.
        # A provisional/just-rotated reference may briefly report waiting; paths
        # become immutable before context_ready enables derived consumers.
        capture = self._active
        return capture if capture and (not ready_only or capture.context_ready) else None

    def directory_for(self, uid: int, packet_format: int) -> Path | None:
        capture = self._captures.get((packet_format, uid))
        return capture.session_directory if capture else None

    def process(self, datagram: ReceivedDatagram) -> None:
        try:
            header = decode_header(datagram.payload)
            profile = PROTOCOLS.get(header.packet_format)
            if profile is None or header.packet_id not in profile.packet_sizes:
                header = None
        except (HeaderDecodeError, ValueError):
            header = None
        key = (header.packet_format, header.session_uid) if header else None
        with self._lock:
            if self._closed:
                raise RuntimeError("capture session is closed")
            capture = self._captures.get(key)
            if capture is None:
                stamp = datetime.fromtimestamp(datagram.received_at_ns / 1e9).strftime("%Y%m%d_%H%M%S")
                label = (f"{_game_tag(header)}_赛道待识别_环节待识别_UID-{header.session_uid:016X}"
                         if header else "未归属_无有效Header")
                waiting = self._captures.get(None)
                if header and waiting and waiting.packet_count == 0:
                    capture = self._captures.pop(None)
                    capture.game_session_uid = header.session_uid
                    capture.archive_stamp = stamp
                    self._rename(capture, f"session_{stamp}_{label}")
                else:
                    capture = PacketCapture(self.root, self.port, **self.options,
                                            directory_name=f"session_{stamp}_{label}",
                                            game_session_uid=header.session_uid if header else None)
                    capture.archive_stamp = stamp
                self._captures[key] = capture
            if header or self._active is None:
                self._active = capture
            if capture is not self._open:
                if self._open:
                    self._open.suspend()
                capture.resume()
                self._open = capture
            capture.process(datagram)
            self.packet_count += 1
            if header and header.packet_id == 1:
                self._context(capture, datagram, header)

    def _context(self, capture, datagram, header):
        # Full decoding follows Raw persistence. A failed decoder cannot gate it.
        try:
            context = decode_packet(datagram.payload)
            label = {key: context[key] for key in (
                "track_name", "session_type_name", "game_mode_name", "season_link_identifier",
                "weekend_link_identifier", "session_link_identifier")}
        except Exception as exc:
            capture.context_error = f"{type(exc).__name__}: {exc}"
            return
        capture.context_error = None
        capture.track = context["track_name"]
        capture.session_type = context["session_type_name"]
        capture.game_mode = context["game_mode_name"]
        if not capture.context_history or any(capture.context_history[-1].get(k) != v for k, v in label.items()):
            capture.context_history.append({**label, "received_at_ns": datagram.received_at_ns})
        if capture.context_ready:
            return  # Do not move files after consumers have started reading them.
        # First received packet defines sorting order, even if Session arrives late.
        prefix = f"session_{capture.archive_stamp}"
        base = "_".join((prefix, _game_tag(header), _component(track_label(capture.track), "赛道未知"),
                         _component(stage_label(capture.session_type), "环节未知"),
                         f"UID-{header.session_uid:016X}"))
        self._rename(capture, base)
        capture.context_ready = True

    def _rename(self, capture, base):
        capture.suspend()
        try:
            for number in range(10_000):
                target = self.root / (base if number == 0 else f"{base}_{number:02d}")
                if target.exists():
                    continue
                try:
                    capture.relocate(target)
                except FileExistsError:
                    continue
                break
            else:
                raise OSError("no free archive directory name")
        except OSError as exc:
            capture.naming_error = f"{type(exc).__name__}: {exc}"
        finally:
            capture.resume()
        capture.checkpoint()

    def checkpoint(self) -> None:
        with self._lock:
            if self._open:
                self._open.checkpoint()

    def summary(self, status: str) -> dict:
        counts = Counter()
        games = Counter()
        formats = Counter()
        errors = 0
        bounds = {}
        for capture in self._captures.values():
            counts.update(capture.packet_counts)
            games.update(capture.game_counts)
            formats.update(capture.format_counts)
            errors += capture.parse_error_count
            for pid, (first, last) in capture.packet_time_bounds.items():
                left, right = bounds.get(pid, (first, last))
                bounds[pid] = (min(left, first), max(right, last))
        first = min((b[0] for b in bounds.values()), default=0)
        last = max((b[1] for b in bounds.values()), default=0)
        duration = max(0, (last - first) / 1e9)
        rates = {pid: ((count - 1) * 1e9 / (bounds[pid][1] - bounds[pid][0])
                       if bounds[pid][1] > bounds[pid][0] else 0.0) for pid, count in counts.items()}
        return {"status": status, "packet_count": self.packet_count,
                "parse_error_count": errors, "software_drop_count": self.software_drop_count,
                "duration_seconds": round(duration, 6),
                "average_total_packets_per_second": round((self.packet_count - 1) / duration, 3) if duration else 0.0,
                "detected_games": dict(games), "packet_formats": dict(formats),
                "packet_types": {packet_name(pid): {"count": count, "average_hz": rates[pid]}
                                 for pid, count in counts.items()},
                "archive_count": len(self._captures),
                "archives": [str(c.session_directory) for c in self._captures.values()]}

    def close(self, status: str = "complete") -> dict:
        with self._lock:
            if self._closed:
                return self.summary(status)
            failure = None
            if self._active:
                self._active.software_drop_count = self.software_drop_count
            for capture in self._captures.values():
                try:
                    capture.resource_health = dict(self.resource_health)
                    capture.close(status)
                except BaseException as exc:
                    failure = failure or exc
            self._closed = True
            if failure:
                raise failure
            return self.summary(status)
