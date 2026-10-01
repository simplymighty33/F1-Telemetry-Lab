from __future__ import annotations

from contextlib import closing
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from collector.packet_capture import PacketCapture
from collector.udp_receiver import ReceivedDatagram
from storage.archive_tools import convert_archive, verify_archives
from storage.raw_writer import (
    ArchiveReadError, BLOCK_HEADER, FILE_HEADER, RawPacketWriter,
    RawReference, iter_archive, read_packet_at,
)


class CompressedArchiveTests(unittest.TestCase):
    def test_round_trip_bytes_envelopes_and_block_random_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "v1.bin"
            compressed = root / "v2.bin"
            restored = root / "restored.bin"
            randomizer = random.Random(12)
            payloads = [b"", b"telemetry" * 100, randomizer.randbytes(65_535)]
            payloads *= 5
            references = []
            with RawPacketWriter(legacy) as old, RawPacketWriter(
                compressed, compression="zlib", flush_every=4, block_bytes=65_585,
            ) as new:
                for index, payload in enumerate(payloads):
                    address = "2001:db8::1" if index % 2 else "127.0.0.1"
                    args = (payload, 1_800_000_000_000_000_000 + index, index, address, 20777)
                    old.write(*args)
                    references.append(new.write(*args))
            verified = verify_archives(legacy, compressed)
            self.assertEqual(verified["verified_packet_count"], len(payloads))
            self.assertEqual([read_packet_at(compressed, ref).payload for ref in references], payloads)
            self.assertTrue(all(ref.block_offset is not None for ref in references))
            convert_archive(compressed, restored, compression="none")
            self.assertEqual(legacy.read_bytes(), restored.read_bytes())
            with self.assertRaises(FileExistsError):
                convert_archive(legacy, compressed)

    def test_truncated_tail_recovery_is_explicit_and_corruption_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.bin"
            with RawPacketWriter(source, compression="zlib", flush_every=2) as writer:
                for index in range(6):
                    writer.write(bytes([index]) * 100, index, index, "127.0.0.1", 20777)
            data = source.read_bytes()
            truncated = root / "truncated.bin"
            truncated.write_bytes(data[:-3])
            with self.assertRaises(ArchiveReadError):
                list(iter_archive(truncated))
            recovered = root / "recovered.bin"
            report = convert_archive(truncated, recovered, recover_tail=True)
            self.assertEqual(report["status"], "recovered_prefix")
            self.assertEqual(report["verified_packet_count"], 4)
            self.assertEqual(len(list(iter_archive(recovered))), 4)
            damaged = root / "damaged.bin"
            corrupt = bytearray(data)
            corrupt[FILE_HEADER.size + BLOCK_HEADER.size + 2] ^= 0x10
            damaged.write_bytes(corrupt)
            with self.assertRaises(ArchiveReadError):
                convert_archive(damaged, root / "bad_output.bin", recover_tail=True)
            self.assertFalse((root / "bad_output.bin").exists())

    def test_block_header_corruption_is_rejected_before_allocation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "raw.bin"
            with RawPacketWriter(path, compression="zlib") as writer:
                writer.write(b"test" * 100, 0, 0, "::1", 20777)
            data = bytearray(path.read_bytes())
            data[FILE_HEADER.size + 8] ^= 0x80
            path.write_bytes(data)
            with self.assertRaisesRegex(ArchiveReadError, "header CRC"):
                list(iter_archive(path))

    def test_empty_archive_and_incompressible_block(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            empty = root / "empty.bin"
            with RawPacketWriter(empty, compression="zlib"):
                pass
            self.assertEqual(list(iter_archive(empty)), [])
            source = root / "noise.bin"
            payload = random.Random(1).randbytes(65_535)
            with RawPacketWriter(source, compression="zlib", compression_level=0) as writer:
                writer.write(payload, 1, 2, "127.0.0.1", 1)
            self.assertEqual(list(iter_archive(source))[0].payload, payload)
            header = BLOCK_HEADER.unpack_from(source.read_bytes(), FILE_HEADER.size)
            self.assertEqual(header[2], 0)  # Stored block fallback, no compressed expansion.

    def test_index_commit_waits_for_raw_and_references_can_be_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            capture = PacketCapture(
                Path(temporary), 20777, database_batch_size=1, raw_flush_every=256,
            )
            capture.process(ReceivedDatagram(1, 2, "127.0.0.1", 20777, b"short"))
            path = capture.session_directory / "raw_packets.bin"
            self.assertEqual(len(list(iter_archive(path))), 1)
            with closing(sqlite3.connect(capture.session_directory / "telemetry.db")) as connection:
                row = connection.execute(
                    "SELECT raw_offset,raw_length,size,crc32,archive_version,raw_block_offset,raw_block_record_offset FROM packets"
                ).fetchone()
            reference = RawReference(*row)
            self.assertEqual(read_packet_at(path, reference).payload, b"short")
            summary = capture.close()
            self.assertEqual(summary["storage"]["pending_packet_count"], 0)

    def test_failed_flush_is_not_retried_during_close(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            writer = RawPacketWriter(Path(temporary) / "raw.bin", compression="zlib")
            writer.write(b"a", 1, 1, "127.0.0.1", 1)
            with patch("storage.raw_archive.os.fsync", side_effect=OSError("disk failure")) as mocked:
                with self.assertRaises(OSError):
                    writer.flush()
                writer.close()
                self.assertEqual(mocked.call_count, 1)


if __name__ == "__main__":
    unittest.main()
