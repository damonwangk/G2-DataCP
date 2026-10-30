#!/usr/bin/env python3
"""Read output through the exact LeRobot API used by OpenPI; no model download."""
import argparse
import json
from pathlib import Path

import numpy as np

from convert_r2 import NAMES, vector
from r2_raw import read_bundles


def validate(root, repo_id, horizon=50):
    from lerobot.common.datasets.lerobot_dataset import LeRobotDataset
    if horizon < 1:
        raise ValueError("horizon must be positive")
    fps = json.loads((root / "meta/info.json").read_text())["fps"]
    dataset = LeRobotDataset(repo_id, root=root, video_backend="pyav",
                            delta_timestamps={"action": [i / fps for i in range(horizon)]})
    trace = [json.loads(line) for line in (root / "provenance/frames.jsonl").read_text().splitlines()]
    if len(trace) != len(dataset):
        raise ValueError("Provenance row count differs from official dataset")
    if dataset.features["action"]["names"] != NAMES:
        raise ValueError("Unexpected feature names/order")
    # Compare every numeric row with the original bundle; videos are sampled at
    # each episode's first/middle/last row via the official decoder.
    cached_source, bundles = None, None
    stored_columns = dataset.hf_dataset.with_format(None).select_columns(["observation.state", "action"]).to_dict()
    stored_arrays = {name: np.asarray(value, dtype=np.float32) for name, value in stored_columns.items()}
    raw_actions = []
    for index, row in enumerate(trace):
        if row["source_episode"] != cached_source:
            cached_source = row["source_episode"]
            bundles = read_bundles(Path(cached_source) / "record/-genie-bundle_data.pbdat")
        values = bundles[row["bundle_index"]].values
        for name, prefix in (("observation.state", "state"), ("action", "action")):
            stored = stored_arrays[name][index]
            if not np.array_equal(stored, vector(values, prefix)):
                raise ValueError(f"Original {prefix} differs at row {index}")
        raw_actions.append(vector(values, "action"))
    image_samples, boundary_chunks = 0, 0
    for episode in range(dataset.num_episodes):
        start = int(dataset.episode_data_index["from"][episode])
        end = int(dataset.episode_data_index["to"][episode])
        for index in sorted({start, (start + end - 1) // 2, end - 1}):
            sample = dataset[index]
            for key in dataset.meta.camera_keys:
                value = sample[key].numpy()
                if value.ndim != 3 or value.shape[0] != 3 or not np.isfinite(value).all():
                    raise ValueError(f"Cannot decode {key} at row {index}")
                image_samples += 1
            selected = np.minimum(np.arange(index, index + horizon), end - 1)
            if not np.array_equal(sample["action"].numpy(), np.asarray(raw_actions)[selected]):
                raise ValueError("Action chunk crossed a boundary or differs from source")
            expected_pad = np.arange(index, index + horizon) >= end
            if not np.array_equal(sample["action_is_pad"].numpy(), expected_pad):
                raise ValueError("Unexpected episode-boundary padding mask")
            boundary_chunks += 1
    result = {"official_loader": "LeRobot v2.1 / OpenPI-pinned", "episodes": dataset.num_episodes,
              "frames": len(dataset), "all_numeric_rows_match_original": True,
              "decoded_image_samples": image_samples, "verified_action_chunks": boundary_chunks,
              "horizon": horizon, "training_or_robot_execution": False}
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--horizon", type=int, default=50)
    args = parser.parse_args()
    validate(args.root, args.repo_id, args.horizon)
