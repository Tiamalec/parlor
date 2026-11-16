from __future__ import annotations

import asyncio
import struct
import unittest

from shared.protocol import FrameDecoder, ProtocolError, encode_frame, envelope, read_frame


class ProtocolTests(unittest.TestCase):
    def test_incremental_decoder_handles_split_and_coalesced_frames(self) -> None:
        first = envelope("one", {"text": "你好"}, request_id="1")
        second = envelope("two", {"value": 2}, request_id="2")
        payload = encode_frame(first) + encode_frame(second)
        decoder = FrameDecoder()
        output = []
        for byte in payload:
            output.extend(decoder.feed(bytes([byte])))
        self.assertEqual([item["type"] for item in output], ["one", "two"])
        self.assertEqual(output[0]["payload"]["text"], "你好")

    def test_decoder_rejects_oversized_frame(self) -> None:
        decoder = FrameDecoder()
        with self.assertRaises(ProtocolError):
            decoder.feed(struct.pack("!I", 4 * 1024 * 1024 + 1))

    def test_async_reader_rejects_invalid_json(self) -> None:
        async def run() -> None:
            reader = asyncio.StreamReader()
            reader.feed_data(struct.pack("!I", 3) + b"bad")
            reader.feed_eof()
            with self.assertRaises(ProtocolError):
                await read_frame(reader)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

