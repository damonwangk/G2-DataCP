"""Parser regression tests: malformed inputs must fail before conversion."""
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from r2_raw import read_bundles, varint, wire, _tensor


def vint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def fld(number, kind, value):
    data = vint(number * 8 + kind)
    return data + (vint(value) if kind == 0 else vint(len(value)) + value)


def tensor(shape=b'\x01', dtype=3, data=struct.pack('<d', 0.5)):
    return fld(1, 2, shape) + fld(2, 0, dtype) + fld(3, 2, data)


def row(timestamp=100, key=b'state__joint__position'):
    entry = fld(1, 2, key) + fld(2, 2, tensor())
    return fld(2, 0, timestamp) + fld(3, 2, entry)


class RawReaderTests(unittest.TestCase):
    def read(self, payloads, declared=None, tail=b''):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.pbdat'
            data = struct.pack('<q', len(payloads) if declared is None else declared)
            for index, payload in enumerate(payloads):
                data += struct.pack('<5q', 200 + index, 0, 0, 0, len(payload)) + payload
            path.write_bytes(data + tail)
            return read_bundles(path)

    def test_decodes_tensor_and_keeps_missing_action_absent(self):
        rows = self.read([row()])
        np.testing.assert_array_equal(rows[0].values['state__joint__position'], [0.5])
        self.assertNotIn('action__joint__position', rows[0].values)
        self.assertEqual(rows[0].timestamp_ns, 100)

    def test_varint_overflow_and_truncation(self):
        for data in [b'\x80', b'\xff' * 10 + b'\x00']:
            with self.assertRaises(ValueError):
                varint(data)

    def test_rejects_truncated_or_unsupported_wire(self):
        for data in [b'\x0a\x05x', b'\x0b', b'\x09x']:
            with self.assertRaises(ValueError):
                wire(data)

    def test_rejects_unsupported_dtype_and_shape_mismatch(self):
        for data in [tensor(dtype=1), tensor(shape=b'\x02'), tensor(shape=vint(8_000_001))]:
            with self.assertRaises(ValueError):
                _tensor(data)

    def test_rejects_unknown_or_duplicate_root_fields(self):
        for payload in [row() + fld(1, 0, 1), row() + fld(2, 0, 101), row() + fld(3, 2, fld(1, 2, b'state__joint__position') + fld(2, 2, tensor()))]:
            with self.assertRaises(ValueError):
                self.read([payload])

    def test_rejects_timestamp_reset_count_mismatch_and_tail(self):
        for call in [lambda: self.read([row(100), row(99)]), lambda: self.read([row()], declared=2), lambda: self.read([row()], tail=b'x')]:
            with self.assertRaises(ValueError):
                call()


if __name__ == '__main__':
    unittest.main()
