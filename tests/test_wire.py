import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gah.wire import WireError, decode_request


class WireTests(unittest.TestCase):
    def test_exponent_and_float_never_enter_runtime(self):
        for raw in [b'{"n":1e9999}', b'{"n":1.0}', b'{"n":1e0}', b'{"n":-Infinity}']:
            with self.subTest(raw=raw), self.assertRaises(WireError):
                decode_request(raw)

    def test_invalid_utf8_bom_and_trailing_data(self):
        for raw in [b'\xff', b'\xef\xbb\xbf{}', b'{} {}', b'null']:
            with self.subTest(raw=raw), self.assertRaises(WireError):
                decode_request(raw)

    def test_nested_duplicate_and_depth_limits(self):
        for raw in [b'{"x":{"n":1,"n":2}}', b'{"x":' + b'[' * 17 + b'0' + b']' * 17 + b'}']:
            with self.subTest(raw=raw), self.assertRaises(WireError):
                decode_request(raw)


if __name__ == "__main__":
    unittest.main()
