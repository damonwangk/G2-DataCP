#!/usr/bin/env python3
"""Dependency and CPU codec preflight; no recording access or robot SDK imports."""
import importlib.metadata
import json
import os
import platform
import sys


def check():
    result = {"platform": sys.platform, "machine": platform.machine(),
              "python": platform.python_version(), "executable": sys.executable,
              "cpu_count": os.cpu_count(), "packages": {}}
    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10 or newer is required")
    for name in ["numpy", "av", "pillow", "pyarrow", "pandas", "datasets", "torch", "torchvision", "lerobot"]:
        result["packages"][name] = importlib.metadata.version(name)
    import av
    import torch
    import torchvision
    from lerobot.common.datasets.lerobot_dataset import CODEBASE_VERSION
    if CODEBASE_VERSION != "v2.1":
        raise RuntimeError("Expected the bundled OpenPI-pinned LeRobot v2.1")
    av.Codec("hevc", "r")
    av.Codec("libx264", "w")
    result["hevc_decoder"] = True
    result["h264_cpu_encoder"] = True
    result["lerobot_format"] = CODEBASE_VERSION
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    check()
