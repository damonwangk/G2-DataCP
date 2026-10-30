"""Regression checks for action provenance and temporal episode boundaries."""

from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

import convert_r2 as converter
from r2_raw import Bundle


def bundles(count=10, offsets=None):
    if offsets is None:
        offsets = np.rint(np.arange(count) * 1_000_000_000 / 30).astype(np.int64)
    result = []
    for i, offset in enumerate(offsets):
        timestamp = 1_000_000_000 + int(offset)
        values = {
            "state__joint__name": np.asarray(converter.ARM_NAMES),
            "state__left_effector__name": np.asarray([converter.NAMES[7]]),
            "state__right_effector__name": np.asarray([converter.NAMES[15]]),
        }
        for prefix, base in (("state", i), ("action", i + 100)):
            values[prefix + "__joint__position"] = np.arange(14, dtype=float) + base
            values[prefix + "__left_effector__position"] = np.asarray([base + 20.0])
            values[prefix + "__right_effector__position"] = np.asarray([base + 30.0])
        for camera in converter.CAMERAS:
            values[f"timestamp__camera__{camera}"] = np.asarray([timestamp], dtype=np.uint64)
        result.append(Bundle(i, timestamp + 10, timestamp, values))
    return result


class PlanningTests(unittest.TestCase):
    def plan(self, records, **options):
        timestamps = np.asarray([b.timestamp_ns for b in records], dtype=np.int64)
        with patch.object(converter, "read_bundles", return_value=records), patch.object(
            converter, "camera_index", return_value=timestamps
        ):
            return converter.make_plan(Path("/unpacked/episode"), **options)

    def test_uniform_grid_preserves_distinct_source_actions(self):
        segments, report = self.plan(bundles(10))
        self.assertEqual(report["export_frames"], 10)
        self.assertEqual(report["segment_lengths"], [10])
        for i, row in enumerate(segments[0]):
            self.assertEqual(row["bundle_index"], i)
            self.assertEqual(row["grid_index"], i)
            self.assertEqual(row["action"][0], i + 100)
            self.assertEqual(row["state"][0], i)
            self.assertEqual(row["action"].dtype, np.float32)

    def test_missing_action_drops_timestep_and_splits_episode(self):
        records = bundles(9)
        del records[4].values["action__joint__position"]
        segments, report = self.plan(records)
        self.assertEqual(report["segment_lengths"], [4, 4])
        self.assertEqual(report["invalid_bundle_reasons"], {"missing:action__joint__position": 1})
        self.assertEqual([[r["bundle_index"] for r in s] for s in segments], [[0, 1, 2, 3], [5, 6, 7, 8]])

    def test_low_fps_cannot_bridge_an_unselected_missing_action(self):
        records = bundles(13)
        del records[4].values["action__right_effector__position"]
        segments, _ = self.plan(records, fps=10)
        self.assertEqual([[r["bundle_index"] for r in s] for s in segments], [[0, 3], [6, 9, 12]])

    def test_recording_gap_cannot_be_compressed_into_contiguous_episode(self):
        records = bundles(offsets=[0, 33_333_333, 66_666_667, 200_000_000, 233_333_333, 266_666_667])
        segments, report = self.plan(records)
        self.assertEqual(report["segment_lengths"], [3, 3])
        self.assertEqual([s[0]["grid_index"] for s in segments], [0, 6])

    def test_stale_or_future_camera_is_excluded(self):
        for replacement in (1_000_000_000, 1_200_000_000):
            with self.subTest(replacement=replacement):
                records = bundles(9)
                records[4].values["timestamp__camera__hand_left_color"] = np.asarray([replacement], dtype=np.uint64)
                segments, report = self.plan(records)
                self.assertEqual(report["segment_lengths"], [4, 4])
                self.assertNotIn(4, [r["bundle_index"] for s in segments for r in s])

    def test_head_anchor_schema_change_fails(self):
        records = bundles(3)
        records[1].values["timestamp__camera__head_color"] += 1
        with self.assertRaisesRegex(ValueError, "head camera anchor"):
            self.plan(records)

    def test_vector_rejects_unknown_order_and_nonfinite_values(self):
        values = bundles(1)[0].values
        values["state__joint__name"] = values["state__joint__name"][::-1]
        with self.assertRaisesRegex(ValueError, "name/order"):
            converter.vector(values, "state")
        values = bundles(1)[0].values
        values["action__joint__position"][0] = np.nan
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            converter.vector(values, "action")


class FakeFrame:
    def __init__(self, value):
        self.value = value

    def to_ndarray(self, format):
        assert format == "rgb24"
        return np.full((4, 8, 3), self.value, dtype=np.uint8)


class CameraReaderTests(unittest.TestCase):
    def reader(self, actual=4, declared=4):
        reader = converter.CameraReader.__new__(converter.CameraReader)
        reader.frames = iter(FakeFrame(i + 1) for i in range(actual))
        reader.count, reader.size, reader.index, reader.image = declared, 8, -1, None
        return reader

    def test_forward_display_order_and_reused_frame(self):
        reader = self.reader()
        first = reader.get(0)
        np.testing.assert_array_equal(reader.get(0), first)
        later = reader.get(2)
        self.assertEqual(int(later[4, 4, 0]), 3)
        self.assertEqual(later.shape, (8, 8, 3))
        with self.assertRaisesRegex(ValueError, "backwards"):
            reader.get(1)
        reader.finish()

    def test_decode_index_count_mismatch_fails(self):
        reader = self.reader(actual=3, declared=4)
        with self.assertRaisesRegex(ValueError, "fewer decoded"):
            reader.get(3)
        reader = self.reader(actual=5, declared=4)
        with self.assertRaisesRegex(ValueError, "timestamp rows"):
            reader.finish()


if __name__ == "__main__":
    unittest.main()
