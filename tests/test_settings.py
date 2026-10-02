from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collector.settings import ConfigurationError, load_settings, parse_user_udp_port, save_udp_port


class SettingsTests(unittest.TestCase):
    def test_nonfinite_and_excessive_timing_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'settings.json'
            for key in ('receiver_timeout_seconds', 'queue_put_timeout_seconds', 'status_interval_seconds'):
                for value in (float('nan'), float('inf'), 1e300):
                    with self.subTest(key=key, value=value):
                        path.write_text(json.dumps({key: value}), encoding='utf-8')
                        with self.assertRaises(ConfigurationError):
                            load_settings(path)
    def test_user_port_validation(self) -> None:
        for text, expected in (("20777", 20777), (" 5000 ", 5000), ("65535", 65535), ("1", 1)):
            self.assertEqual(parse_user_udp_port(text), expected)
        for text in ("", "0", "65536", "-1", "20.5", "abc", "２３", "9" * 5000):
            with self.subTest(text=text[:20]), self.assertRaises(ConfigurationError):
                parse_user_udp_port(text)

    def test_save_port_preserves_other_and_unknown_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            values = {"udp_port": 20777, "bind_host": "127.0.0.1", "foundation_enabled": False,
                      "future_option": {"keep": "保留"}, "raw_compression_level": 3}
            path.write_text(json.dumps(values), encoding="utf-8")
            saved = save_udp_port(path, 32001)
            self.assertEqual(saved.udp_port, 32001)
            values["udp_port"] = 32001
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), values)
            self.assertEqual(load_settings(path).udp_port, 32001)

    def test_save_failure_leaves_original_config_and_no_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            load_settings(path)
            before = path.read_bytes()
            with patch("collector.settings.os.replace", side_effect=PermissionError("read-only")):
                with self.assertRaises(PermissionError):
                    save_udp_port(path, 32001)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_bad_config_cannot_be_overwritten_by_port_save(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            for original in ("broken json", "[]", '{"raw_compression_level": 99}'):
                path.write_text(original, encoding="utf-8")
                with self.assertRaises(ConfigurationError):
                    save_udp_port(path, 32001)
                self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_first_run_creates_valid_default_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config" / "settings.json"
            settings = load_settings(path)
            self.assertTrue(path.is_file())
            self.assertEqual(settings.udp_port, 20777)
            self.assertEqual(settings.log_directory, "logs")
            self.assertEqual(settings.raw_compression, "zlib")
            self.assertEqual(settings.raw_compression_level, 1)

    def test_partial_settings_inherit_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            path.write_text(json.dumps({"udp_port": 0}), encoding="utf-8")
            settings = load_settings(path)
            self.assertEqual(settings.udp_port, 0)
            self.assertEqual(settings.queue_capacity, 8192)

    def test_invalid_port_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            path.write_text(json.dumps({"udp_port": 70000}), encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_settings(path)

    def test_foundation_option_is_a_boolean_and_can_be_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            path.write_text(json.dumps({"foundation_enabled": False}), encoding="utf-8")
            self.assertFalse(load_settings(path).foundation_enabled)
            path.write_text(json.dumps({"foundation_enabled": "false"}), encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_settings(path)


if __name__ == "__main__":
    unittest.main()
