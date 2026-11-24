import lzma
import struct
import unittest
from unittest.mock import Mock, patch

from deadlock_tools.weapons import _decompress, parse_bundle, xor_bytes


class BundleCompressionTest(unittest.TestCase):
    def test_lzma_retries_stream_with_end_marker(self):
        data = b"UnityFS data block" * 128
        alone = lzma.compress(data, format=lzma.FORMAT_ALONE)
        unity_block = alone[:5] + alone[13:]
        decoder_factory = lzma.LZMADecompressor

        for block, size, valid in (
            (unity_block, len(data), True),
            (unity_block[:-5], len(data), False),
            (unity_block, len(data) - 1, False),
        ):
            with self.subTest(size=size, compressed_length=len(block)):
                windows_decoder = Mock()
                windows_decoder.decompress.side_effect = lzma.LZMAError("Corrupt input data")
                fallback = decoder_factory(format=lzma.FORMAT_ALONE)
                with patch.object(lzma, "LZMADecompressor", side_effect=[windows_decoder, fallback]) as factory:
                    if valid:
                        self.assertEqual(_decompress(block, size, 1), data)
                    else:
                        with self.assertRaises(lzma.LZMAError):
                            _decompress(block, size, 1)
                    self.assertEqual(factory.call_count, 2)

    def test_v6_block_table_at_end(self):
        payload = b"Orion data"
        name = b"orion"
        blocks = (bytes(16) + struct.pack(">iIIHi", 1, len(payload), len(payload), 0, 1)
                  + struct.pack(">qqI", 0, len(payload), 4) + name + b"\0")
        header = b"UnityFS\0" + struct.pack(">I", 6) + b"5.x.x\0" + b"2018.4.3f1\0"
        size = len(header) + 20 + len(blocks) + len(payload)
        for flags, body in ((0x40, blocks + payload), (0xc0, payload + blocks)):
            bundle = header + struct.pack(">qIII", size, len(blocks), len(blocks), flags) + body
            with self.subTest(flags=flags):
                result = parse_bundle(xor_bytes(bundle))
                self.assertEqual(result["data"], payload)
                self.assertEqual(result["nodes"], [(0, len(payload), 4, name.decode())])
                with self.assertRaisesRegex(ValueError, "size"):
                    parse_bundle(bundle[:-1])
