import lzma
import unittest
from unittest.mock import Mock, patch

from deadlock_tools.weapons import _decompress


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
