import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image

from robot_media import bounded_dataset_class, encode_images


class RobotMediaTests(unittest.TestCase):
    def test_h264_roundtrip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for i in range(4):
                Image.fromarray(np.full((32, 48, 3), i * 40, dtype=np.uint8)).save(
                    root / f"frame_{i:06d}.png"
                )
            output = root / "video.mp4"
            encode_images(root, output, 30, "h264", 1)
            with av.open(str(output)) as container:
                self.assertEqual(container.streams.video[0].average_rate, Fraction(30, 1))
                frames = list(container.decode(video=0))
                self.assertEqual(len(frames), 4)
                self.assertEqual((frames[0].width, frames[0].height), (48, 32))

    def test_rejects_odd_dimensions_and_bad_codec(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            Image.fromarray(np.zeros((31, 32, 3), dtype=np.uint8)).save(root / "frame_000000.png")
            with self.assertRaises(ValueError):
                encode_images(root, root / "out.mp4", 30)
            with self.assertRaises(ValueError):
                bounded_dataset_class(object, "unsupported", 1)
            with self.assertRaises(ValueError):
                bounded_dataset_class(object, "h264", 0)

    def test_override_skips_existing_and_returns_paths(self):
        class Meta:
            video_keys = ("camera",)

            def get_video_file_path(self, index, key):
                return Path(f"{index}-{key}.mp4")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            existing = root / "2-camera.mp4"
            existing.touch()
            cls = bounded_dataset_class(object)
            writer = cls()
            writer.root, writer.meta = root, Meta()
            self.assertEqual(writer.encode_episode_videos(2), {"camera": str(existing)})


if __name__ == "__main__":
    unittest.main()
