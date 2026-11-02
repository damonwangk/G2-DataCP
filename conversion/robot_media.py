# 使用说明
# 用途：历史图像写入/CPU有界视频兼容辅助，供兼容调用与离线测试使用。
# 使用：作为库导入；当前MP4主链使用 video_mp4.py，不直接运行本文件。
# 多文件使用说明：README.md 的文件职责与历史兼容模块。
#
"""CPU-bounded video encoding for the pinned LeRobot v2.1 writer."""
from pathlib import Path

import av
import numpy as np
from PIL import Image
from concurrent.futures import ThreadPoolExecutor
from collections import deque
import threading

def fast_png(image,fpath):
    if isinstance(image,np.ndarray):image=Image.fromarray(image)
    image.save(fpath,format='PNG',compress_level=1)

class ImageWriter:
    def __init__(self,threads):
        self.pool=ThreadPoolExecutor(max_workers=threads);self.pending=deque();self.limit=threads*2
    def save_image(self,image,fpath):
        self.pending.append(self.pool.submit(fast_png,image,fpath))
        if len(self.pending)>=self.limit:self.pending.popleft().result()
    def wait_until_done(self):
        while self.pending:self.pending.popleft().result()
    def stop(self):
        try:self.wait_until_done()
        finally:self.pool.shutdown()


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
        def _save_image(self,image,fpath):
            # These PNGs are temporary video inputs. Compression changes bytes,
            # not pixels; avoid spending CPU on files discarded after encoding.
            if self.image_writer is None and threads>1:self.image_writer=ImageWriter(threads)
            if self.image_writer is None:
                if isinstance(image,np.ndarray) and image.dtype==np.uint8 and image.ndim==3 and image.shape[2]==3:
                    Image.fromarray(image).save(fpath,format='PNG',compress_level=1)
                    return
                if isinstance(image,Image.Image):
                    image.save(fpath,format='PNG',compress_level=1)
                    return
            return super()._save_image(image,fpath)

        def encode_episode_videos(self, episode_index):
            video_paths = {}
            fractions={i:0. for i,_ in enumerate(self.meta.video_keys)};progress_lock=threading.Lock()
            def notify(index,done,total,key):
                if progress:
                    with progress_lock:
                        fractions[index]=done/total
                        progress(sum(fractions.values())/len(fractions),key)
            def encode(item):
                camera_index,key=item
                video_path = self.root / self.meta.get_video_file_path(episode_index, key)
                if video_path.is_file():
                    return key,str(video_path)
                image_dir = self._get_image_file_path(
                    episode_index=episode_index, image_key=key, frame_index=0
                ).parent
                callback=lambda done,total:notify(camera_index,done,total,key)
                encode_images(image_dir, video_path, self.fps, codec, 1 if threads>1 else threads, callback)
                return key,str(video_path)
            if threads>1:
                with ThreadPoolExecutor(max_workers=threads) as pool:video_paths.update(pool.map(encode,enumerate(self.meta.video_keys)))
            else:
                for item in enumerate(self.meta.video_keys):
                    key,path=encode(item);video_paths[key]=path
            return video_paths

    return BoundedDataset
