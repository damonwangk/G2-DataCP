"""Producer-declared collection finalization marker; never infer writer closure.

Only a collector who has closed all writers may explicitly create the marker.
This module never attaches to processes, decodes recordings, or marks automatically.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import tempfile

MARKER = '.lerobot-ready.json'
RELATIVE_FILES = ('record/-genie-bundle_data.pbdat',) + tuple(
    f'camera/{camera}/{camera}.{extension}'
    for camera in ('head_color', 'hand_left_color', 'hand_right_color')
    for extension in ('txt', 'h265')
)


def required_files(ep: str | Path) -> list[Path]:
    return [Path(ep) / name for name in RELATIVE_FILES]


def _safe_stat(ep: Path, relative: str) -> os.stat_result:
    """Reject symlink components and require a regular file beneath the episode."""
    if ep.is_symlink() or not ep.is_dir():
        raise ValueError('episode must be a non-symlink directory')
    current = ep
    for component in Path(relative).parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(f'symlink forbidden: {relative}')
    result = current.stat()
    if not stat.S_ISREG(result.st_mode):
        raise ValueError(f'not a regular file: {relative}')
    try:
        current.resolve().relative_to(ep.resolve())
    except ValueError:
        raise ValueError(f'path outside episode: {relative}')
    return result


def verify_ready(ep: str | Path) -> tuple[bool, str]:
    ep = Path(ep)
    try:
        marker = ep / MARKER
        if marker.is_symlink() or not marker.is_file():
            return False, 'ready marker missing or symlink'
        data = json.loads(marker.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data['schema_version'] != 1 or data.get('finalized') is not True:
            return False, 'invalid marker schema or finalized flag'
        files = data.get('files')
        if not isinstance(files, list) or len(files) != len(RELATIVE_FILES):
            return False, 'invalid file inventory'
        entries = {}
        for item in files:
            if not isinstance(item, dict) or set(item) != {'path', 'size', 'mtime_ns'}:
                return False, 'invalid file entry'
            name = item['path']
            if not isinstance(name, str) or name not in RELATIVE_FILES or name in entries:
                return False, 'unexpected or duplicate file path'
            if type(item['size']) is not int or item['size'] < 0 or type(item['mtime_ns']) is not int or item['mtime_ns'] < 0:
                return False, 'invalid file stat'
            entries[name] = item
        if set(entries) != set(RELATIVE_FILES):
            return False, 'incomplete file inventory'
        for name in RELATIVE_FILES:
            info = _safe_stat(ep, name)
            if info.st_size != entries[name]['size'] or info.st_mtime_ns != entries[name]['mtime_ns']:
                return False, f'file changed: {name}'
        return True, 'ready'
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        return False, f'invalid or inaccessible marker/files: {exc}'


def create_marker(ep: str | Path, *, confirm_finalized: bool = False) -> Path:
    if confirm_finalized is not True:
        raise ValueError('collector must explicitly confirm all writers are closed')
    ep = Path(ep)
    files = []
    for name in RELATIVE_FILES:
        info = _safe_stat(ep, name)
        files.append({'path': name, 'size': info.st_size, 'mtime_ns': info.st_mtime_ns})
    marker = ep / MARKER
    if marker.is_symlink():
        raise ValueError('ready marker must not be a symlink')
    payload = {'schema_version': 1, 'finalized': True, 'files': files}
    fd, temp = tempfile.mkstemp(prefix='.lerobot-ready.', suffix='.tmp', dir=ep)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(payload, out, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, marker)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return marker


def main() -> None:
    parser = argparse.ArgumentParser(description='Create producer-declared ready marker after all recording writers are closed.')
    parser.add_argument('episode', type=Path)
    parser.add_argument('--confirm-finalized', action='store_true', required=True, help='Confirm all writers are closed and collection is final')
    args = parser.parse_args()
    print(create_marker(args.episode, confirm_finalized=args.confirm_finalized))


if __name__ == '__main__':
    main()
