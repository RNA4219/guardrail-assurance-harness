from pathlib import Path
import sys
import sqlite3
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gah import read_checks as checks


class ReadChecksHotpathTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE sample(value INTEGER)")
        self.db.execute("INSERT INTO sample VALUES(1)")
        self.db.commit()

    def test_json_snapshot_hits_restore_independent_values_without_python_tree_copy(self):
        calls = []
        @checks.checked_read
        def read(db):
            calls.append(1)
            return {"nested": {"items": [1, True, None, 1.0]}, "text": "safe"}

        self.db.execute("BEGIN")
        with checks.scope(self.db):
            first = read(self.db)
            first["nested"]["items"].append("caller mutation")
            with patch.object(checks, "_copy", wraps=checks._copy) as clone, \
                    patch.object(checks.json, "loads", wraps=checks.json.loads) as decode:
                second = read(self.db)
                third = read(self.db)
            self.assertEqual(len(calls), 1)
            self.assertEqual(clone.call_count, 0)
            self.assertEqual(decode.call_count, 2)
            self.assertEqual(second["nested"]["items"], [1, True, None, 1.0])
            self.assertEqual(third, second)
            self.assertIsNot(second, third)
            self.assertIsNot(second["nested"], third["nested"])
        self.db.rollback()

    def test_root_tuple_of_json_values_restores_tuple_and_isolates_nested_mutation(self):
        @checks.checked_read
        def read(db):
            return ({"nested": [1, 2]}, {"enabled": True})

        self.db.execute("BEGIN")
        with checks.scope(self.db):
            first = read(self.db)
            first[0]["nested"].append(9)
            with patch.object(checks, "_copy", wraps=checks._copy) as clone:
                second = read(self.db)
                third = read(self.db)
            self.assertIs(type(second), tuple)
            self.assertEqual(second, ({"nested": [1, 2]}, {"enabled": True}))
            self.assertIsNot(second, third)
            self.assertIsNot(second[0], third[0])
            self.assertEqual(clone.call_count, 0)
        self.db.rollback()

    def test_nested_tuple_remains_on_legacy_copy_path(self):
        @checks.checked_read
        def read(db):
            return ({"value": 3}, (1, 2))

        self.db.execute("BEGIN")
        with checks.scope(self.db):
            first = read(self.db)
            first[0]["value"] = 99
            second = read(self.db)
            self.assertEqual(second, ({"value": 3}, (1, 2)))
            self.assertIs(type(second), tuple)
            self.assertIsNot(first, second)
        self.db.rollback()

    def test_unicode_and_surrogate_strings_preserve_exact_codepoints(self):
        values = {
            "café": "naïve",
            "astral-😀": "😀",
            "\ud83d\ude00": "\ud83d\ude00",
            "\ud800": "\udfff",
        }
        @checks.checked_read
        def read(db):
            return values

        self.db.execute("BEGIN")
        with checks.scope(self.db):
            first = read(self.db)
            with patch.object(checks, "_copy", wraps=checks._copy) as clone:
                second = read(self.db)
            self.assertEqual(second, values)
            self.assertEqual(tuple(map(ord, next(key for key in second if key == "\ud83d\ude00"))),
                             (0xD83D, 0xDE00))
            self.assertEqual(tuple(map(ord, second["\ud800"])), (0xDFFF,))
            self.assertEqual(second["astral-😀"], "😀")
            # At least the surrogate-bearing dictionary must use the legacy
            # path; the normalized astral codepoint is safe in strict UTF-8.
            self.assertGreater(clone.call_count, 0)
        self.db.rollback()

    def test_mixed_row_and_large_json_tuple_encodes_only_the_plain_json_slot(self):
        payload = {"items": [{"id": i, "enabled": i % 2 == 0} for i in range(600)]}
        calls = []

        @checks.checked_read
        def read(db):
            calls.append(1)
            row = db.execute("SELECT value FROM sample").fetchone()
            return row, payload

        self.db.row_factory = sqlite3.Row
        self.db.execute("BEGIN")
        with checks.scope(self.db):
            first = read(self.db)
            with patch.object(checks, "_copy", wraps=checks._copy) as legacy_clone:
                checks._copy(first)
            legacy_calls = legacy_clone.call_count
            first[1]["items"].clear()
            with patch.object(checks, "_copy", wraps=checks._copy) as clone:
                second = read(self.db)
                third = read(self.db)
            self.assertEqual(len(calls), 1)
            self.assertLessEqual(clone.call_count, 2)
            self.assertGreater(legacy_calls, clone.call_count * 100)
            self.assertIs(type(second), tuple)
            self.assertIs(second[0], first[0])  # sqlite3.Row remains immutable and shared.
            self.assertEqual(len(second[1]["items"]), 600)
            self.assertIsNot(second[1], third[1])
            self.assertIsNot(second[1]["items"], third[1]["items"])
            second[1]["items"].pop()
            self.assertEqual(len(third[1]["items"]), 600)
        self.db.rollback()

    def test_tuple_fallback_preserves_nested_custom_nonfinite_unicode_depth_and_marker_shapes(self):
        import math

        class Custom:
            def __init__(self, values):
                self.values = values

        deep = current = []
        for _ in range(checks.MAX_DEPTH + 4):
            child = []
            current.append(child)
            current = child
        collision = (checks._JSON_CACHE_MARKER, "json", b"not an internal cache entry")
        values = ( ( {"nested": [1]}, ), Custom([2]), float("nan"),
                   {"infinite": float("inf")}, "\ud800", deep, collision )

        @checks.checked_read
        def read(db):
            return values

        self.db.execute("BEGIN")
        with checks.scope(self.db):
            first = read(self.db)
            second = read(self.db)
            self.assertIs(type(second), tuple)
            self.assertEqual(second[0], ({"nested": [1]},))
            self.assertIsNot(first[0], second[0])
            self.assertEqual(second[1].values, [2])
            self.assertIsNot(first[1], second[1])
            self.assertTrue(math.isnan(second[2]))
            self.assertEqual(second[3]["infinite"], float("inf"))
            self.assertEqual(tuple(map(ord, second[4])), (0xD800,))
            self.assertEqual(len(str(second[5])), len(str(deep)))
            self.assertEqual(second[6][1:], collision[1:])
            self.assertIsNot(second[6][0], checks._JSON_CACHE_MARKER)
        self.db.rollback()

    def test_write_and_action_boundary_still_invalidate_json_snapshots(self):
        calls = []
        @checks.checked_read
        def read(db):
            calls.append(1)
            return {"value": db.execute("SELECT value FROM sample").fetchone()[0]}

        self.db.execute("BEGIN")
        with checks.scope(self.db):
            self.assertEqual(read(self.db), {"value": 1})
            self.assertEqual(read(self.db), {"value": 1})
            self.db.execute("UPDATE sample SET value=2")
            self.assertEqual(read(self.db), {"value": 2})
            self.assertEqual(read(self.db), {"value": 2})
        self.assertEqual(len(calls), 3)
        self.db.rollback()
        with checks.scope(self.db):
            self.assertEqual(read(self.db), {"value": 1})
        self.assertEqual(len(calls), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
