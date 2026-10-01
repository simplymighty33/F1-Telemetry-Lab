from __future__ import annotations

import unittest

from decoder.header import HEADER_STRUCT, HeaderDecodeError, decode_header


class HeaderTests(unittest.TestCase):
    def test_decodes_f1_23_header(self) -> None:
        payload = HEADER_STRUCT.pack(
            2023, 23, 1, 17, 2, 6, 0xFEDCBA9876543210, 12.5, 1234, 5678, 3, 255
        )
        header = decode_header(payload)
        self.assertEqual(header.packet_format, 2023)
        self.assertEqual(header.packet_id, 6)
        self.assertEqual(header.session_uid, 0xFEDCBA9876543210)
        self.assertEqual(header.frame_identifier, 1234)
        self.assertEqual(header.overall_frame_identifier, 5678)
        self.assertEqual(header.player_car_index, 3)
        self.assertEqual(header.secondary_player_car_index, 255)

    def test_rejects_short_packet(self) -> None:
        with self.assertRaises(HeaderDecodeError):
            decode_header(b"too short")


if __name__ == "__main__":
    unittest.main()

