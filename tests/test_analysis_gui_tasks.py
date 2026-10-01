from __future__ import annotations

from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from collector.analysis_gui import AnalysisWindow
from storage.raw_writer import RawPacketWriter, iter_archive
from storage.raw_archive import ArchiveReadError


class AnalysisGuiTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.session = Path(self.temporary.name)
        self.raw = self.session / "raw_packets.bin"
        self.raw.write_bytes(b"test input")
        self.database = self.session / "analysis" / "telemetry_analysis.db"
        self.root = tk.Tk()
        self.root.withdraw()
        self.window = AnalysisWindow(self.root, self.session)

    def tearDown(self) -> None:
        for after_id in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.after_cancel(after_id)
        self.root.destroy()
        self.temporary.cleanup()

    def _receive_worker_result(self) -> None:
        # Exercise the real background thread and its window-owned result queue.
        result = self.window._build_results.get(timeout=5)
        self.window._build_results.put(result)
        self.window._poll_build_result()

    def test_session_generation_delivers_result_and_restores_buttons(self) -> None:
        summary = {"analysis_database": str(self.database), "laps_resampled": 2,
                   "resampled_points": 100, "decode_error_count": 0}
        with patch("collector.analysis_gui.filedialog.askdirectory", return_value=str(self.session)), patch(
            "collector.analysis_gui.build_analysis", return_value=summary
        ) as builder, patch.object(self.window, "load_database") as loader:
            self.window.choose_session()
            self._receive_worker_result()
            builder.assert_called_once_with(self.session, progress_every=0)
            loader.assert_called_once_with(self.database)
        self.assertEqual(self.window.build_button.cget("state"), "normal")
        self.assertEqual(self.window.compress_button.cget("state"), "normal")
        self.assertIn("分析完成", self.window.status_var.get())
        self.assertFalse(hasattr(self.window.chart, "_build_results"))

    def test_session_failure_is_shown_and_buttons_are_restored(self) -> None:
        with patch("collector.analysis_gui.filedialog.askdirectory", return_value=str(self.session)), patch(
            "collector.analysis_gui.build_analysis", side_effect=ValueError("invalid raw")
        ), patch("collector.analysis_gui.messagebox.showerror") as dialog:
            self.window.choose_session()
            self._receive_worker_result()
            self.assertIn("invalid raw", dialog.call_args.args[1])
        self.assertEqual(self.window.build_button.cget("state"), "normal")
        self.assertEqual(self.window.compress_button.cget("state"), "normal")

    def test_compression_delivers_result_and_restores_buttons(self) -> None:
        target = self.session / "new.compressed.bin"
        result = {"saved_percent": 78, "verified_packet_count": 10}
        with patch("collector.analysis_gui.filedialog.askopenfilename", return_value=str(self.raw)), patch(
            "collector.analysis_gui.filedialog.asksaveasfilename", return_value=str(target)
        ), patch("collector.analysis_gui.convert_archive", return_value=result) as converter:
            self.window.choose_compress()
            self._receive_worker_result()
            converter.assert_called_once_with(self.raw, target)
        self.assertEqual(self.window.build_button.cget("state"), "normal")
        self.assertEqual(self.window.compress_button.cget("state"), "normal")
        self.assertIn("压缩副本已保存", self.window.status_var.get())

    def test_compression_failure_is_shown(self) -> None:
        with patch("collector.analysis_gui.filedialog.askopenfilename", return_value=str(self.raw)), patch(
            "collector.analysis_gui.filedialog.asksaveasfilename", return_value=str(self.session / "new.bin")
        ), patch("collector.analysis_gui.convert_archive", side_effect=ValueError("CRC mismatch")), patch(
            "collector.analysis_gui.messagebox.showerror"
        ) as dialog:
            self.window.choose_compress()
            self._receive_worker_result()
            self.assertIn("CRC mismatch", dialog.call_args.args[1])
        self.assertEqual(self.window.compress_button.cget("state"), "normal")

    def test_truncated_tail_retry_requires_user_confirmation(self) -> None:
        self.window._active_task = "analysis"
        self.window._last_build_session = self.session
        with patch("collector.analysis_gui.messagebox.askyesno", return_value=True) as question, patch.object(
            self.window, "_start_session_build"
        ) as retry:
            self.window._build_failed(ArchiveReadError("truncated", truncated=True))
        question.assert_called_once()
        retry.assert_called_once_with(self.session, recover_tail=True)

    def test_crc_error_never_offers_truncated_tail_recovery(self) -> None:
        self.window._active_task = "analysis"
        self.window._last_build_session = self.session
        with patch("collector.analysis_gui.messagebox.askyesno") as question, patch(
            "collector.analysis_gui.messagebox.showerror"
        ) as dialog:
            self.window._build_failed(ArchiveReadError("CRC mismatch"))
        question.assert_not_called()
        dialog.assert_called_once()

    def test_real_archive_conversion_through_gui_worker(self) -> None:
        source = self.session / "input.bin"
        target = self.session / "compressed.bin"
        with RawPacketWriter(source, flush_every=1) as writer:
            for index in range(3):
                writer.write(bytes([index]) * 1000, 1000 + index, index, "127.0.0.1", 20777)
        with patch("collector.analysis_gui.filedialog.askopenfilename", return_value=str(source)), patch(
            "collector.analysis_gui.filedialog.asksaveasfilename", return_value=str(target)
        ):
            self.window.choose_compress()
            self._receive_worker_result()
        self.assertTrue(source.is_file())
        self.assertEqual(list(iter_archive(source)), [
            # Physical compressed block references differ; compare preserved data.
            type(packet)(packet.offset, packet.received_at_ns, packet.monotonic_ns,
                         packet.source_ip, packet.source_port, packet.payload, packet.crc32)
            for packet in iter_archive(target)
        ])
        self.assertEqual(self.window.compress_button.cget("state"), "normal")


if __name__ == "__main__":
    unittest.main()
