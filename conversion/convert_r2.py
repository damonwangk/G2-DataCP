#!/usr/bin/env python3
"""Convert unpacked G2/R2 factory recordings to OpenPI's pinned LeRobot v2.1."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path

import numpy as np

from r2_raw import read_bundles

CAMERAS = ("head_color", "hand_left_color", "hand_right_color")
ARM_NAMES = [f"idx{21 + i}_arm_l_joint{i + 1}" for i in range(7)] + [
    f"idx{61 + i}_arm_r_joint{i + 1}" for i in range(7)
]
NAMES = ARM_NAMES[:7] + ["idx31_gripper_l_inner_joint1"] + ARM_NAMES[7:] + [
    "idx71_gripper_r_inner_joint1"
]
LEROBOT_REV = "0cf864870cf29f4738d3ade893e6fd13fbd7cdb5"


def discover(inputs):
    found = set()
    for value in inputs:
        path = Path(value).expanduser().resolve()
        if path.is_file() and path.suffix == '.mcap':
            found.add(path)
        elif path.is_dir():
            if (path / 'raw.mcap').is_file(): found.add(path / 'raw.mcap')
            elif (path / 'record/-genie-bundle_data.pbdat').is_file(): found.add(path)
            else:
                found.update(p for p in path.rglob('*.mcap') if not any(x.startswith('.') for x in p.relative_to(path).parts))
                found.update(p.parent.parent for p in path.rglob('record/-genie-bundle_data.pbdat'))
        else: raise ValueError(f'Expected MCAP, episode or parent directory: {path}')
    if not found: raise ValueError('No completed MCAP or factory bundle recording found')
    return sorted(found)


def episode_name(ep):
    return ep.parent.name if ep.name == 'raw.mcap' else ep.stem if ep.is_file() else ep.name


def source_cameras(ep):
    if ep.suffix == '.mcap':
        from mcap_input import available_cameras
        return available_cameras(ep)
    return CAMERAS


def source_files(ep):
    if ep.suffix == '.mcap': return [ep]
    paths = [ep / 'record/-genie-bundle_data.pbdat']
    paths += [ep / f'camera/{cam}/{cam}.{suffix}' for cam in CAMERAS for suffix in ('txt', 'h265')]
    if (ep / 'meta_info.json').exists(): paths.append(ep / 'meta_info.json')
    return paths


def open_camera(ep, cam, count, size, threads):
    if ep.suffix == '.mcap':
        from mcap_input import CameraReader as McapCameraReader
        return McapCameraReader(ep, cam, count, size, threads)
    return CameraReader(ep / f'camera/{cam}/{cam}.h265', count, size, threads)


def source_ready(ep):
    if ep.suffix == '.mcap': return ep.is_file(), 'completed MCAP filename required'
    from episode_ready import verify_ready
    return verify_ready(ep)


def camera_index(path):
    timestamps = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        fields = line.split()
        if len(fields) != 2 or fields[1] not in ("I", "P", "B"):
            raise ValueError(f"{path}:{number}: expected nanosecond timestamp and I/P/B")
        timestamps.append(int(fields[0]))
    result = np.asarray(timestamps, dtype=np.int64)
    if not len(result) or np.any(result <= 0) or np.any(np.diff(result) <= 0):
        raise ValueError(f"{path}: camera timestamps must be positive and strictly increasing")
    return result


def vector(values, prefix):
    """Validate native order rather than relying on unnamed array positions."""
    if list(values["state__joint__name"]) != ARM_NAMES:
        raise ValueError("Unsupported arm name/order; update the explicit robot contract")
    for side, expected in (("left", NAMES[7]), ("right", NAMES[15])):
        if list(values[f"state__{side}_effector__name"]) != [expected]:
            raise ValueError(f"Unsupported {side} gripper name/order")
    if prefix + "__joint__name" in values and list(values[prefix + "__joint__name"]) != ARM_NAMES:
        raise ValueError("Action joint names differ from state names")
    for side, expected in (("left", NAMES[7]), ("right", NAMES[15])):
        key = f"{prefix}__{side}_effector__name"
        if key in values and list(values[key]) != [expected]:
            raise ValueError("Action gripper name differs from state name")
    arms = values[prefix + "__joint__position"]
    left = values[prefix + "__left_effector__position"]
    right = values[prefix + "__right_effector__position"]
    if arms.shape != (14,) or left.shape != (1,) or right.shape != (1,):
        raise ValueError("Expected 14 arm positions and one position for each gripper")
    result = np.concatenate((arms[:7], left, arms[7:], right)).astype(np.float32)
    if not np.isfinite(result).all():
        raise ValueError("Non-finite state/action position")
    return result


def make_plan(episode, fps=30, max_offset_ms=20.0, max_camera_age_ms=50.0, max_gap_ms=50.0, progress=None):
    if episode.suffix == ".mcap":
        from mcap_input import make_plan as plan_mcap
        return plan_mcap(episode, fps, max_offset_ms, max_camera_age_ms, max_gap_ms, progress=progress)
    bundles = read_bundles(episode / "record/-genie-bundle_data.pbdat")
    if not bundles:
        raise ValueError(f"Empty bundle stream: {episode}")
    indices = {cam: camera_index(episode / f"camera/{cam}/{cam}.txt") for cam in CAMERAS}
    lookups = {cam: {int(t): i for i, t in enumerate(ts)} for cam, ts in indices.items()}
    # In this observed schema, internal bundle time is the head camera anchor.
    # Future incompatible schemas must fail, rather than silently reinterpret time.
    times = np.asarray([b.timestamp_ns for b in bundles], dtype=np.int64)
    prepared, invalid = [], Counter()
    for bundle in bundles:
        values = bundle.values
        try:
            state, action = vector(values, "state"), vector(values, "action")
        except KeyError as exc:
            invalid[f"missing:{exc.args[0]}"] += 1
            prepared.append(None)
            continue
        camera_rows = {}
        reason = None
        for cam in CAMERAS:
            key = f"timestamp__camera__{cam}"
            if key not in values or values[key].shape != (1,):
                reason = f"missing_camera_time:{cam}"
                break
            timestamp = int(values[key][0])
            age = bundle.timestamp_ns - timestamp
            if cam == "head_color" and timestamp != bundle.timestamp_ns:
                raise ValueError("Bundle internal timestamp differs from head camera anchor")
            if timestamp not in lookups[cam] or not 0 <= age <= max_camera_age_ms * 1e6:
                reason = f"unindexed_or_stale_camera:{cam}"
                break
            camera_rows[cam] = {"index": lookups[cam][timestamp], "timestamp_ns": timestamp, "age_ns": age}
        if reason:
            invalid[reason] += 1
            prepared.append(None)
        else:
            prepared.append((state, action, camera_rows))

    # Retrospective nearest-bundle sampling onto an integer 30 Hz grid. Never
    # concatenate dropped timesteps into apparently continuous training data.
    count = int((int(times[-1]) - int(times[0])) * fps // 1_000_000_000) + 1
    grid = int(times[0]) + np.rint(np.arange(count) * (1_000_000_000 / fps)).astype(np.int64)
    segments, current, grid_drops = [], [], Counter()
    previous_bundle = None
    for grid_index, timestamp in enumerate(grid):
        at = int(np.searchsorted(times, timestamp))
        choices = [i for i in (at - 1, at) if 0 <= i < len(times)]
        source = min(choices, key=lambda i: (abs(int(times[i]) - int(timestamp)), i))
        offset = int(times[source]) - int(timestamp)
        reason = None
        if abs(offset) > max_offset_ms * 1e6:
            reason = "bundle_outside_grid_tolerance"
        elif prepared[source] is None:
            reason = "invalid_source_bundle"
        elif previous_bundle is not None and source == previous_bundle:
            reason = "duplicate_source_bundle"
        if reason:
            grid_drops[reason] += 1
            if current:
                segments.append(current)
                current = []
            previous_bundle = None
            continue
        # Also split if a low requested fps would jump across invalid bundles.
        if current and (np.any(np.diff(times[previous_bundle:source + 1]) > max_gap_ms * 1e6)
                        or any(p is None for p in prepared[previous_bundle:source + 1])):
            segments.append(current)
            current = []
        state, action, camera_rows = prepared[source]
        current.append({"grid_index": grid_index, "grid_timestamp_ns": int(timestamp),
                        "bundle_index": source, "bundle_timestamp_ns": int(times[source]),
                        "record_timestamp_ns": bundles[source].record_timestamp_ns,
                        "alignment_offset_ns": offset, "cameras": camera_rows,
                        "state": state, "action": action})
        previous_bundle = source
    if current:
        segments.append(current)
    singleton_frames = sum(len(s) for s in segments if len(s) < 2)
    segments = [s for s in segments if len(s) >= 2]
    report = {"source": str(episode), "bundles": len(bundles),
              "valid_original_bundles": sum(p is not None for p in prepared),
              "invalid_bundle_reasons": dict(invalid), "grid_frames": len(grid),
              "grid_drop_reasons": dict(grid_drops), "discarded_singleton_frames": singleton_frames,
              "segment_lengths": [len(s) for s in segments],
              "export_frames": sum(map(len, segments)),
              "camera_index_counts": {cam: len(ts) for cam, ts in indices.items()}}
    return segments, report


class CameraReader:
    """Sequential decode in display order, using the raw .txt index, never preview MP4."""
    def __init__(self, path, count, size, threads=1):
        import av
        av.logging.set_level(av.logging.ERROR)
        self.container = av.open(str(path))
        self.container.streams.video[0].thread_count = threads
        self.frames = iter(self.container.decode(video=0))
        self.count, self.size, self.index, self.image = count, size, -1, None

    def get(self, wanted):
        from PIL import Image, ImageOps
        if wanted < self.index:
            raise ValueError("Camera source frame index moved backwards")
        while self.index < wanted:
            try:
                frame = next(self.frames)
            except StopIteration as exc:
                raise ValueError("H.265 has fewer decoded frames than timestamp index") from exc
            self.index += 1
            self.image = Image.fromarray(frame.to_ndarray(format="rgb24"))
        fitted = ImageOps.pad(self.image, (self.size, self.size), method=Image.Resampling.BILINEAR,
                              color=(0, 0, 0), centering=(0.5, 0.5))
        return np.asarray(fitted, dtype=np.uint8)

    def finish(self):
        for _frame in self.frames:
            self.index += 1
        if self.index + 1 != self.count:
            raise ValueError(f"Decoded frames {self.index + 1} != timestamp rows {self.count}")

    def close(self):
        self.container.close()


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def convert(args):
    from batch_io import append_json, begin, commit, completed, json_write, recover
    from conversion_progress import Progress
    progress=getattr(args,"progress_reporter",None) or Progress()
    episodes = discover(args.input)
    ready_only = getattr(args, "ready_only", False)
    if ready_only:
        verify_ready = source_ready
        ready = []
        for ep in episodes:
            accepted, reason = verify_ready(ep)
            if accepted:
                ready.append(ep)
            else:
                print(json.dumps({"source": str(ep), "deferred": reason}), flush=True)
        episodes = ready
        if not episodes:
            print(json.dumps({"ready_episodes": 0, "converted": 0}), flush=True)
            return
    progress.update(total_sources=len(episodes),force=True)
    if args.inspect:
        for ep in episodes:
            _, report = make_plan(ep, args.fps, args.max_offset_ms, args.max_camera_age_ms, args.max_gap_ms)
            print(json.dumps(report, ensure_ascii=False), flush=True)
        return
    if not args.output or not args.repo_id or not (args.task or args.tasks_json):
        raise ValueError("Conversion requires --output, --repo-id and --task or --tasks-json")
    tasks = json.loads(Path(args.tasks_json).read_text()) if args.tasks_json else {}
    if not isinstance(tasks, dict):
        raise ValueError("--tasks-json must contain a JSON object")
    for ep in episodes:
        task = args.task or tasks.get(episode_name(ep))
        if not isinstance(task, str) or not task.strip():
            raise ValueError(f"Missing nonempty task instruction for {episode_name(ep)}")
    output = Path(args.output).expanduser().resolve()
    for ep in episodes:
        if output.is_relative_to(ep) or ep.is_relative_to(output):
            raise ValueError("Output and original episode directories must not contain each other")
    cameras=source_cameras(episodes[0])
    existing_info=output/'meta/info.json'
    if args.resume and existing_info.exists():
        saved_info=json.loads(existing_info.read_text())
        if saved_info.get('total_episodes',0):
            cameras=tuple(k.removeprefix('observation.images.') for k in saved_info['features'] if k.startswith('observation.images.'))
    progress.update('准备 LeRobot 写入器',force=True)
    import torch
    threads = getattr(args, "threads", 1)
    codec = getattr(args, "codec", "h264")
    torch.set_num_threads(threads)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass  # A caller may already have initialized the interop thread pool.
    from lerobot.common.datasets.lerobot_dataset import CODEBASE_VERSION, LeRobotDataset as OfficialDataset
    if args.media == "video":
        from robot_media import bounded_dataset_class
        LeRobotDataset = bounded_dataset_class(OfficialDataset, codec, threads,
            progress=lambda fraction,key:progress.update("保存视频",fraction=.80+.19*fraction,camera=key))
    else:
        LeRobotDataset = OfficialDataset
    if CODEBASE_VERSION != "v2.1":
        raise RuntimeError(f"Use OpenPI-pinned LeRobot {LEROBOT_REV}, found {CODEBASE_VERSION}")
    features = {"observation.state": {"dtype": "float32", "shape": (16,), "names": NAMES},
                "action": {"dtype": "float32", "shape": (16,), "names": NAMES}}
    for cam in cameras:
        features[f"observation.images.{cam}"] = {
            "dtype": args.media, "shape": (args.image_size, args.image_size, 3),
            "names": ["height", "width", "channels"]}
    settings = {"repo_id": args.repo_id, "fps": args.fps, "image_size": args.image_size,
                "media": args.media, "max_offset_ms": args.max_offset_ms,
                "max_camera_age_ms": args.max_camera_age_ms, "max_gap_ms": args.max_gap_ms,
                "lerobot_commit": LEROBOT_REV, "names": NAMES}
    # A resumed dataset must retain its codec; threads may vary between runs.
    settings["video_codec"] = codec if args.media == "video" else None
    provenance = output / "provenance"
    def load_writer():
        # Rehydrate only the pinned official writer state, including an empty
        # dataset after interruption before its first save. Do not load all
        # Parquets or query the Hub when resuming local append-only conversion.
        from lerobot.common.datasets.lerobot_dataset import LeRobotDatasetMetadata
        writer = LeRobotDataset.__new__(LeRobotDataset)
        writer.meta = LeRobotDatasetMetadata.__new__(LeRobotDatasetMetadata)
        writer.meta.repo_id, writer.meta.root, writer.meta.revision = args.repo_id, output, "v2.1"
        from lerobot.common.datasets.utils import load_info
        writer.meta.info = load_info(output)
        if writer.meta.info["total_episodes"] == 0:
            writer.meta.tasks, writer.meta.task_to_task_index = {}, {}
            writer.meta.episodes, writer.meta.episodes_stats, writer.meta.stats = {}, {}, {}
        else:
            writer.meta.load_metadata()
        writer.repo_id, writer.root, writer.revision = args.repo_id, output, None
        writer.episodes, writer.image_writer = None, None
        writer.image_transforms, writer.delta_timestamps = None, None
        writer.delta_indices, writer.episode_data_index = None, None
        writer.tolerance_s, writer.video_backend = 1e-4, "pyav"
        writer.hf_dataset = writer.create_hf_dataset()
        writer.episode_buffer = writer.create_episode_buffer()
        return writer
    output.parent.mkdir(parents=True, exist_ok=True)
    import fcntl
    lock = (output.parent / ("." + output.name + ".r2.lock")).open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError("Another converter is writing this output")
    # Keep lock open until the process exits; an OS crash automatically releases it.
    with lock:
        if output.exists():
            if not args.resume:
                raise FileExistsError(f"Refusing to overwrite {output}; use --resume for this converter's output")
            config_path = provenance / "settings.json"
            stored_settings = json.loads(config_path.read_text()) if config_path.exists() else None
            if stored_settings is not None:
                stored_settings.setdefault("video_codec", "libsvtav1" if stored_settings.get("media") == "video" else None)
            if stored_settings != settings:
                raise ValueError("Resume requires the same converter settings and owned output")
            recover(output)
            old_info=json.loads(existing_info.read_text())
            old_cameras=tuple(k.removeprefix('observation.images.') for k in old_info['features'] if k.startswith('observation.images.'))
            if old_info.get('total_episodes',0)==0 and old_cameras!=cameras:
                # Failed empty outputs contain no episodes to migrate. Preserve the
                # old metadata and refresh only the schema for this source.
                import time
                json_write(provenance/('empty_schema_before_'+str(time.time_ns())+'.json'),old_info)
                old_info['features']={k:v for k,v in old_info['features'].items() if not k.startswith('observation.images.')}
                old_info['features'].update(features)
                old_info['video_path']=('videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4'
                                        if cameras and args.media=='video' else None)
                json_write(existing_info,old_info)
            dataset = load_writer()
        else:
            # Publish ownership settings and empty official metadata together;
            # interruption during initialization never leaves an unowned output.
            import tempfile
            with tempfile.TemporaryDirectory(prefix="." + output.name + ".r2-initializing-", dir=output.parent) as temporary:
                staged = Path(temporary) / "dataset"
                dataset = LeRobotDataset.create(repo_id=args.repo_id, root=staged, fps=args.fps,
                                                robot_type="g2a_bimanual", features=features,
                                                use_videos=args.media == "video" and bool(cameras), video_backend="pyav")
                (staged / "provenance").mkdir()
                json_write(staged / "provenance/settings.json", settings)
                staged.replace(output)
                dataset.root, dataset.meta.root = output, output
        (provenance / "SUCCESS").unlink(missing_ok=True)
        done = completed(output)
        if len(done) != dataset.meta.total_episodes:
            raise ValueError("Segment ledger differs from official metadata; refusing unsafe append")
        done_by_key = {(row["source"], row["segment"]): row for row in done}
        reports, failures, errors = [], 0, getattr(args,"errors_out",[])
        for number, ep in enumerate(episodes, 1):
            progress.source(number,episode_name(ep),ep.stat().st_size if ep.is_file() else None)
            try:
                available=source_cameras(ep)
                if set(available)!=set(cameras):
                    raise ValueError('该源相机字段 '+str(list(available))+' 与当前数据集 '+str(list(cameras))+' 不同；请分别转换到不同数据集，原始文件和已完成episode保留')
                if ready_only:
                    accepted, reason = verify_ready(ep)
                    if not accepted:
                        raise ValueError(f"Finalized source changed before conversion: {reason}")
                min_free_gb = getattr(args, "min_free_gb", 1.0)
                import shutil
                if shutil.disk_usage(output).free < min_free_gb * 1024 ** 3:
                    raise RuntimeError(f"Insufficient output disk space; reserve {min_free_gb} GiB")
                segments, report = make_plan(ep, args.fps, args.max_offset_ms, args.max_camera_age_ms, args.max_gap_ms, progress=progress)
                if not segments:
                    raise ValueError("No valid contiguous segment")
                # The official writer stages PNGs for one segment. Reserve an
                # uncompressed RGB upper estimate plus the operator's free margin.
                scratch_bytes = int(max(map(len, segments)) * args.image_size ** 2 * 3 * len(cameras) * 1.2)
                progress.update("写入数据帧",fraction=.35,force=True,total_frames=report["export_frames"])
                processed_frames=0
                if shutil.disk_usage(output).free < min_free_gb * 1024 ** 3 + scratch_bytes:
                    raise RuntimeError("Insufficient space for one segment's temporary images and reserve")
                task = (args.task or tasks[episode_name(ep)]).strip()
                sources = source_files(ep)
                manifest = [{"path": str(p), "bytes": p.stat().st_size, **({"mtime_ns": p.stat().st_mtime_ns} if ep.suffix == ".mcap" else {"sha256": sha256(p)})} for p in sources]
                fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
                pending_segments = []
                for segment_index, segment in enumerate(segments):
                    previous = done_by_key.get((str(ep), segment_index))
                    if previous:
                        if (previous["fingerprint"] != fingerprint or previous["task"] != task
                                or previous["frames"] != len(segment)):
                            raise ValueError("Previously converted source or task changed")
                    else:
                        pending_segments.append((segment_index, segment))
                if pending_segments:
                    with ExitStack() as stack:
                        readers = {}
                        for cam in cameras:
                            reader = open_camera(ep, cam, report["camera_index_counts"][cam], args.image_size, threads)
                            stack.callback(reader.close)
                            readers[cam] = reader
                        # Verify complete decode/index correspondence before any dataset mutation.
                        for reader in readers.values():
                            if ep.suffix != ".mcap": reader.finish()
                        for cam, reader in list(readers.items()):
                            reader.close()
                            readers[cam] = open_camera(ep, cam, report["camera_index_counts"][cam], args.image_size, threads)
                            stack.callback(readers[cam].close)
                        for segment_index, segment in pending_segments:
                            if ready_only:
                                accepted, reason = verify_ready(ep)
                                if not accepted:
                                    raise ValueError(f"Source is no longer finalized: {reason}")
                            begin(dataset)
                            episode_index = dataset.meta.total_episodes
                            with (provenance / "frames.jsonl").open("a") as trace:
                                for frame_index, row in enumerate(segment):
                                    frame = {"observation.state": row["state"], "action": row["action"], "task": task}
                                    for cam in cameras:
                                        frame[f"observation.images.{cam}"] = readers[cam].get(row["cameras"][cam]["index"])
                                    dataset.add_frame(frame)
                                    processed_frames+=1
                                    progress.update("写入图像与数据帧" if cameras else "写入状态与动作",fraction=.35+.45*processed_frames/report["export_frames"],processed_frames=processed_frames)
                                    detail = {k: v for k, v in row.items() if k not in ("state", "action")}
                                    detail.update(source_episode=str(ep), episode_index=episode_index, frame_index=frame_index)
                                    trace.write(json.dumps(detail) + "\n")
                            progress.update("保存 episode",fraction=.80,force=True,processed_frames=processed_frames)
                            dataset.save_episode()
                            if ready_only:
                                accepted, reason = verify_ready(ep)
                                if not accepted:
                                    raise ValueError(f"Source changed during segment: {reason}")
                            entry = {"source": str(ep), "segment": segment_index, "episode_index": episode_index,
                                     "fingerprint": fingerprint, "frames": len(segment), "task": task}
                            append_json(provenance / "segments.jsonl", entry)
                            commit(output)
                            done_by_key[(str(ep), segment_index)] = entry
                            dataset.hf_dataset = dataset.create_hf_dataset()
                    for source in manifest:
                        if "sha256" in source and sha256(Path(source["path"])) != source["sha256"]:
                            raise RuntimeError(f"Source changed during conversion: {source['path']}")
                report.update(source_fingerprint=fingerprint)
                json_write(provenance / (hashlib.sha256(str(ep).encode()).hexdigest()[:16] + ".json"),
                           {"report": report, "source_manifest": manifest})
                reports.append(report)
                progress.update(processed_frames=report["export_frames"],total_frames=report["export_frames"])
                progress.finish_source(True)
                print(json.dumps({"progress": f"{number}/{len(episodes)}", "source": episode_name(ep),
                                  "segments": len(segments), "frames": report["export_frames"],
                                  "new_segments": len(pending_segments)}, ensure_ascii=False), flush=True)
            except Exception as exc:
                failures += 1
                detail={"source":str(ep),"error":f"{type(exc).__name__}: {exc}"};errors.append(detail)
                progress.finish_source(False,detail["error"])
                append_json(provenance / "errors.jsonl", detail)
                if (output / ".r2-transaction").exists():
                    recover(output)
                    dataset = load_writer()
                if not args.skip_errors:
                    raise
                print(json.dumps({"source": str(ep), "failed": str(exc)}), flush=True)
        contract = {**settings, "schema": "G2 factory bundle or platform MCAP -> LeRobot v2.1", "state_dim": 16, "action_dim": 16,
                    "action_source": "factory bundle action; MCAP original command preferred, otherwise corresponding recorded state trajectory; per-frame action_sources records provenance",
                    "units": ["rad"] * 7 + ["native_dahuan_unit_unverified"] + ["rad"] * 7 + ["native_dahuan_unit_unverified"],
                    "action_name_mapping": "MCAP: exact joint names on each channel; factory bundle: explicit same-order contract checked against state names",
                    "alignment": "MCAP: nearest samples on log_time grid, one MCAP per episode, no gap/offset/range filters; factory bundle retains its original alignment",
                    "missing_action_policy": "MCAP: use recorded corresponding state trajectory; factory bundle retains drop/split behavior",
                    "terminal_chunk_policy": "official loader repeats the last action within episode and emits action_is_pad",
                    "camera_timing": "MCAP: embedded JPEG message index and log_time/publish_time; factory: per-camera txt index",
                    "image_processing": f"RGB, aspect-preserving pad to {args.image_size} square",
                    "unexported_modalities": "head/waist/base, other cameras, depth, force and tactile retained in raw source",
                    "input_directories_this_run": len(episodes), "failed_inputs_this_run": failures,
                    "total_episodes": dataset.meta.total_episodes, "total_frames": dataset.meta.total_frames,
                    "source_reports_this_run": reports,"errors":errors,"dataset_cameras":list(cameras)}
        json_write(provenance / "conversion.json", contract)
        if not failures:
            (provenance / "SUCCESS").write_text("All requested sources converted using official save_episode.\n")
        print(json.dumps({"output": str(output), "episodes": dataset.meta.total_episodes,
                          "frames": dataset.meta.total_frames, "failed": failures}, indent=2))
        progress.finish(not failures,"; ".join(e["source"]+": "+e["error"] for e in errors))
        if failures:
            raise RuntimeError(f"{failures} inputs failed: "+"; ".join(e["source"]+": "+e["error"] for e in errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True, help="MCAP file, episode or parent directory; repeatable")
    parser.add_argument("--inspect", action="store_true", help="Inspect schema/alignment and plan without writing a dataset")
    parser.add_argument("--output")
    parser.add_argument("--repo-id", help="Local logical ID, e.g. local/g2_r2_tasks; no upload is performed")
    task = parser.add_mutually_exclusive_group()
    task.add_argument("--task", help="Same natural language task for all input recordings")
    task.add_argument("--tasks-json", help="JSON object mapping recording directory names to task text")
    parser.add_argument("--media", choices=("video", "image"), default="video", help="MP4 videos for bulk conversion; image embeds lossless RGB in Parquet")
    parser.add_argument("--resume", action="store_true", help="Resume this converter output, skipping committed segments")
    parser.add_argument("--skip-errors", action="store_true", help="Log a failed input and continue; final exit is nonzero if any failed")
    parser.add_argument("--ready-only", action="store_true", help="Only process producer-finalized .lerobot-ready.json directories")
    parser.add_argument("--threads", type=int, default=1, help="Decode/encode/PyTorch thread limit, default 1")
    parser.add_argument("--codec", choices=("h264", "libsvtav1"), default="h264", help="Explicit video codec; no GPU or fallback")
    parser.add_argument("--min-free-gb", type=float, default=1.0, help="Minimum free GiB on output filesystem before each episode")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--image-size", type=int, default=256)
    parser.add_argument("--max-offset-ms", type=float, default=20)
    parser.add_argument("--max-camera-age-ms", type=float, default=50)
    parser.add_argument("--max-gap-ms", type=float, default=50)
    args = parser.parse_args()
    if not 1 <= args.fps <= 120 or args.image_size < 32 or args.image_size % 2:
        parser.error("fps must be 1..120 and image-size must be even and >= 32")
    if args.threads < 1 or not np.isfinite(args.min_free_gb) or args.min_free_gb < 0:
        parser.error("threads must be positive and min-free-gb must be finite and nonnegative")
    if any(not np.isfinite(v) or v <= 0 for v in (args.max_offset_ms, args.max_camera_age_ms, args.max_gap_ms)):
        parser.error("All time tolerances must be positive finite values")
    try:
        convert(args)
    except (ValueError, KeyError, FileExistsError, RuntimeError) as exc:
        parser.exit(1, f"Conversion failed: {exc}\n")


if __name__ == "__main__":
    main()
