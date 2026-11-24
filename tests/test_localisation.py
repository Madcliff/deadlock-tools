"""Localisation asset splicing. No game files required."""
import struct
import unittest

from deadlock_tools.localisation import Localisation


def resources_file(blobs):
    """Build a header plus object directory and return (bytes, object records)."""
    offset = 128
    data = bytearray(offset)
    struct.pack_into(">IIII", data, 0, 0, 0, 17, offset)
    objects = []
    cursor = offset
    metadata = 32
    for pid, blob in blobs:
        cursor += 4  # a gap that must survive splicing
        data.extend(b"\x00" * (cursor - len(data)))
        start = cursor
        data.extend(blob)
        struct.pack_into("<qIII", data, metadata, pid, start - offset, len(blob), 0)
        objects.append((pid, start, len(blob), 49, metadata))
        metadata += 24
        cursor = start + len(blob)
    data.extend(b"TAIL")
    struct.pack_into(">I", data, 4, len(data))
    return bytes(data), objects


class ReplaceObjectsTest(unittest.TestCase):
    def catalogue(self, blobs):
        data, objects = resources_file(blobs)
        catalogue = Localisation.__new__(Localisation)
        catalogue.data = data
        catalogue.offset = 128
        catalogue.objects = objects
        return catalogue

    def test_two_replacements_keep_gaps_and_directory(self):
        catalogue = self.catalogue((
            (1, b"AAAA" * 4),
            (2, b"BBBB" * 4),
            (3, b"CCCC" * 4),
        ))
        longer = b"english-row\n" * 3
        shorter = b"fr"
        output = catalogue._replace_objects({1: longer, 3: shorter})

        self.assertEqual(struct.unpack_from(">I", output, 4)[0], len(output))
        self.assertEqual(output.count(longer), 1)
        self.assertEqual(output.count(shorter), 1)
        self.assertIn(b"BBBB" * 4, output)
        self.assertTrue(output.endswith(b"TAIL"))

        shift = 0
        for pid, start, size, _class_id, metadata in catalogue.objects:
            payload = {1: longer, 3: shorter}.get(pid)
            recorded = struct.unpack_from("<I", output, metadata + 8)[0]
            self.assertEqual(recorded, start - catalogue.offset + shift)
            if payload is None:
                self.assertEqual(output[catalogue.offset + recorded:catalogue.offset + recorded + size],
                                 catalogue.data[start:start + size])
                continue
            self.assertEqual(struct.unpack_from("<I", output, metadata + 12)[0], len(payload))
            self.assertEqual(output[catalogue.offset + recorded:catalogue.offset + recorded + len(payload)], payload)
            shift += (len(payload) - size + 7) // 8 * 8
