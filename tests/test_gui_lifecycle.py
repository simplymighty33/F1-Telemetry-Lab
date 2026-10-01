from __future__ import annotations

import json
import gc
import logging
from pathlib import Path
import socket
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from collector.analysis_gui import AnalysisWindow
from collector.gui import CollectorWindow
from collector.service import CollectorService
from collector.settings import load_settings
from decoder.full_parser import PACKET_SIZES
from decoder.header import HEADER_STRUCT
from storage.foundation import foundation_path, read_progress
from storage.raw_archive import iter_archive


class GuiLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        gc.collect()  # Dispose prior Tk test instances on their owning thread.
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.config = self.directory / "settings.json"
        self.settings = load_settings(self.config)
        self.root = tk.Tk()
        self.root.withdraw()
        self.callback_errors = []
        self.root.report_callback_exception = lambda kind, value, trace: self.callback_errors.append(value)
        self.logger = logging.getLogger("gui-lifecycle")
        self.services = []
        self.releases = []

    def tearDown(self) -> None:
        for release in self.releases:
            release.set()
        for service in self.services:
            service.stop()
            service.wait(5)
        try:
            for after_id in self.root.tk.splitlist(self.root.tk.call("after", "info")):
                self.root.tk.call("after", "cancel", after_id)
            self.root.destroy()
        except tk.TclError:
            pass
        self.temporary.cleanup()
        gc.collect()
        self.assertEqual(self.callback_errors, [])

    def wait_until(self, predicate, timeout=5) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("GUI lifecycle did not reach expected state")
            self.root.update()
            time.sleep(0.01)

    def make_window(self) -> CollectorWindow:
        service = Mock(port=20777, settings=self.settings, host="127.0.0.1")
        service.snapshot.return_value.state = "listening"
        with patch.object(self.root, "after"):
            return CollectorWindow(self.root, service, self.logger, self.directory, self.config)

    def test_invalid_or_failed_port_edit_keeps_current_capture_running(self) -> None:
        window = self.make_window()
        window.port_var.set("0")
        with patch("collector.gui.messagebox.showerror") as dialog:
            window.apply_port()
            dialog.assert_called_once()
        window.service.stop.assert_not_called()
        window.port_var.set("32001")
        with patch("collector.gui.messagebox.askyesno", return_value=True), patch(
            "collector.gui.save_udp_port", side_effect=PermissionError("read-only")
        ), patch("collector.gui.messagebox.showerror"):
            window.apply_port()
        window.service.stop.assert_not_called()
        self.assertFalse(window._restarting)

    def test_cancel_port_edit_does_not_write_config(self) -> None:
        window = self.make_window()
        window.port_var.set("32001")
        before = self.config.read_bytes()
        with patch("collector.gui.messagebox.askyesno", return_value=False):
            window.apply_port()
        self.assertEqual(self.config.read_bytes(), before)
        window.service.stop.assert_not_called()

    def test_close_while_port_confirmation_is_open_does_not_apply_change(self) -> None:
        window = self.make_window()
        window.service.wait.return_value = False
        window.port_var.set("32001")
        before = self.config.read_bytes()

        def confirm(*_args, **_kwargs):
            window.request_close()
            return True

        with patch("collector.gui.messagebox.askyesno", side_effect=confirm), patch(
            "collector.gui.save_udp_port"
        ) as save:
            window.apply_port()
            save.assert_not_called()
        self.assertEqual(self.config.read_bytes(), before)

    def test_close_during_native_file_dialog_does_not_start_a_job(self) -> None:
        child = AnalysisWindow(tk.Toplevel(self.root), self.directory)

        def select(*_args, **_kwargs):
            child.prepare_close()
            return str(self.directory / "input.bin")

        with patch("collector.analysis_gui.filedialog.askopenfilename", side_effect=select), patch(
            "collector.analysis_gui.filedialog.asksaveasfilename"
        ) as save, patch("collector.analysis_gui.convert_archive") as convert:
            child.choose_compress()
            save.assert_not_called()
            convert.assert_not_called()
        self.assertIsNone(child._worker)

    def test_close_during_rebind_never_starts_new_service(self) -> None:
        window = self.make_window()
        window.service.wait.return_value = False
        window.port_var.set("32001")
        with patch("collector.gui.messagebox.askyesno", return_value=True):
            window.apply_port()
        window.request_close()
        with patch("collector.gui.CollectorService") as factory:
            window._finish_restart()
            factory.assert_not_called()
        self.assertTrue(window._closing)
        self.assertTrue(window._shutdown_dialog.window.winfo_exists())

    def test_window_close_is_idempotent_and_waits_for_service(self) -> None:
        window = self.make_window()
        window.service.wait.return_value = False
        protocol = self.root.protocol("WM_DELETE_WINDOW")
        self.root.tk.call(protocol)
        window.request_close()
        window.service.stop.assert_called_once()
        popup = window._shutdown_dialog.window
        self.root.tk.call(popup.protocol("WM_DELETE_WINDOW"))
        self.assertTrue(popup.winfo_exists())
        self.assertFalse(window._close_ready())
        self.assertEqual(window.analysis_button.cget("state"), "disabled")
        self.assertEqual(window.port_button.cget("state"), "disabled")
        with patch("collector.gui.close_logging") as close_logs, patch.object(self.root, "destroy") as destroy:
            window.service.wait.return_value = True
            window._shutdown_dialog._poll()
            close_logs.assert_called_once_with(self.logger)
            destroy.assert_called_once()

    def test_master_close_waits_for_analysis_and_compression_in_child_windows(self) -> None:
        window = self.make_window()
        window.service.wait.return_value = True
        for task in ("analysis", "compression"):
            child = AnalysisWindow(tk.Toplevel(self.root), self.directory)
            release = threading.Event()
            self.releases.append(release)
            child._worker = threading.Thread(target=lambda event=release: event.wait(5), daemon=False)
            child._worker.start()
            child._active_task = task
            window._analysis_windows.append(child)
        window.request_close()
        self.assertFalse(window._close_ready())
        for child in window._analysis_windows:
            self.assertTrue(child._closing)
            with patch("collector.analysis_gui.messagebox.showerror") as dialog:
                child._build_failed(ValueError("test failure during shutdown"))
                dialog.assert_not_called()
        self.releases[0].set()
        window._analysis_windows[0]._worker.join(1)
        self.assertFalse(window._close_ready())
        self.releases[1].set()
        window._analysis_windows[1]._worker.join(1)
        self.assertTrue(window._close_ready())

    def test_analysis_window_close_waits_for_real_compression_worker(self) -> None:
        child = AnalysisWindow(tk.Toplevel(self.root), self.directory)
        source = self.directory / "input.bin"
        source.write_bytes(b"placeholder")
        release = threading.Event()
        started = threading.Event()
        self.releases.append(release)

        def converter(*_args):
            started.set()
            release.wait(5)
            return {"saved_percent": 10.0, "verified_packet_count": 3}

        with patch("collector.analysis_gui.filedialog.askopenfilename", return_value=str(source)), patch(
            "collector.analysis_gui.filedialog.asksaveasfilename", return_value=str(self.directory / "new.bin")
        ), patch("collector.analysis_gui.convert_archive", side_effect=converter):
            child.choose_compress()
            self.assertTrue(started.wait(1))
            self.assertFalse(child._worker.daemon)
            self.root.tk.call(child.root.protocol("WM_DELETE_WINDOW"))
            self.assertFalse(child.shutdown_ready())
            self.assertTrue(child._shutdown_dialog.window.winfo_exists())
            self.root.update()
            release.set()
            self.wait_until(lambda: not child.root.winfo_exists())
            self.assertFalse(child._worker.is_alive())

    def test_real_port_switch_preserves_both_sessions_and_close_flushes_raw(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            new_port = probe.getsockname()[1]
        service = CollectorService(self.settings, self.directory / "data", self.logger, "127.0.0.1", 0)
        self.services.append(service)
        window = CollectorWindow(self.root, service, self.logger, self.directory / "data", self.config)
        self.wait_until(lambda: service.snapshot().state == "listening")
        first_session = service.snapshot().session_directory
        first_port = json.loads((first_session / "metadata.json").read_text(encoding="utf-8"))["udp_port"]

        def send(port, count):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                for frame in range(count):
                    payload = bytearray(PACKET_SIZES[6])
                    HEADER_STRUCT.pack_into(payload, 0, 2023, 23, 1, 0, 1, 6, 77,
                                            frame / 60, frame, frame, 0, 255)
                    sender.sendto(payload, ("127.0.0.1", port))

        send(first_port, 100)
        self.wait_until(lambda: service.snapshot().received_packets == 100)
        window.port_var.set(str(new_port))
        with patch("collector.gui.messagebox.askyesno", return_value=True):
            window.apply_port()
        self.wait_until(lambda: window.service is not service and window.service.snapshot().state == "listening")
        self.services.append(window.service)
        second_session = window.service.snapshot().session_directory
        self.assertNotEqual(first_session, second_session)
        self.assertEqual(load_settings(self.config).udp_port, new_port)
        self.assertEqual(service.snapshot().persisted_packets, 100)
        send(new_port, 250)
        self.wait_until(lambda: window.service.snapshot().received_packets == 250)
        self.root.tk.call(self.root.protocol("WM_DELETE_WINDOW"))
        # Keep the interpreter for assertions while exercising the real shutdown polling.
        with patch.object(self.root, "destroy") as destroy, patch("collector.gui.close_logging"):
            self.wait_until(lambda: destroy.called)
        for session, expected in ((first_session, 100), (second_session, 250)):
            raw = session / "raw_packets.bin"
            self.assertEqual(sum(1 for _ in iter_archive(raw)), expected)
            self.assertEqual(read_progress(foundation_path(raw))["packets"], expected)
            metadata = json.loads((session / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["summary"]["status"], "complete")
        self.assertFalse(window.service._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
