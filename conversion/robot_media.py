"""CPU-bounded video encoding for the pinned LeRobot v2.1 writer."""
from pathlib import Path

import av
import numpy as np
from PIL import Image


def _validate_codec(codec, threads):
    if codec not in ("h264", "libsvtav1"):
        raise ValueError(f"Unsupported video codec: {codec}")
    if not isinstance(threads, int) or isinstance(threads, bool) or threads < 1:
        raise ValueError("Encoder thread count must be a positive integer")
    # Resolve before writing output: unavailable codecs must never be replaced.
    try:
        av.Codec("libx264" if codec == "h264" else codec, "w")
    except (ValueError, av.error.FFmpegError) as exc:
        raise RuntimeError(f"Video encoder unavailable: {codec}") from exc


def encode_images(imgs_dir, video_path, fps, codec="h264", threads=1, progress=None):
    """Encode official frame_NNNNNN.png files with the selected PyAV codec."""
    _validate_codec(codec, threads)
    if fps <= 0:
        raise ValueError("fps must be positive")
    images = sorted(Path(imgs_dir).glob("frame_[0-9][0-9][0-9][0-9][0-9][0-9].png"))
    if not images:
        raise ValueError(f"No image frames in {imgs_dir}")

    def load(path):
        with Image.open(path) as image:
            array = np.asarray(image)
        if array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] != 3:
            raise ValueError(f"Expected uint8 RGB image: {path}")
        height, width = array.shape[:2]
        if height == 0 or width == 0 or height % 2 or width % 2:
            raise ValueError(f"Image dimensions must be positive and even: {path}")
        return array

    first = load(images[0])
    height, width = first.shape[:2]
    video_path = Path(video_path)
    video_path.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(video_path), mode="w") as output:
        stream = output.add_stream("libx264" if codec == "h264" else codec, rate=fps,
                                   options={"g": "2", "crf": "23"})
        stream.width, stream.height = width, height
        stream.pix_fmt = "yuv420p"
        stream.thread_count = threads
        for index, path in enumerate(images):
            array = first if index == 0 else load(path)
            if array.shape != first.shape:
                raise ValueError(f"Image dimensions differ: {path}")
            frame = av.VideoFrame.from_ndarray(array, format="rgb24")
            for packet in stream.encode(frame):
                output.mux(packet)
            if progress:progress(index+1,len(images))
        for packet in stream.encode():
            output.mux(packet)


def bounded_dataset_class(base_class, codec="h264", threads=1, progress=None):
    """Return a writer subclass with the pinned encoder method's contract."""
    _validate_codec(codec, threads)

    class BoundedDataset(base_class):
        def encode_episode_videos(self, episode_index):
            video_paths = {}
            for camera_index,key in enumerate(self.meta.video_keys):
                video_path = self.root / self.meta.get_video_file_path(episode_index, key)
                video_paths[key] = str(video_path)
                if video_path.is_file():
                    continue
                image_dir = self._get_image_file_path(
                    episode_index=episode_index, image_key=key, frame_index=0
                ).parent
                callback=(lambda done,total:progress((camera_index+done/total)/len(self.meta.video_keys),key)) if progress else None
                encode_images(image_dir, video_path, self.fps, codec, threads, callback)
            return video_paths

    return BoundedDataset
