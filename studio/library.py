"""Robot-local episode folders. Inspect filenames/sidecars, never transform MCAP."""
import json
import os
import re
import shutil
from pathlib import Path
from native import ROOT
from storage import data_dir, dumps, record_category, remember_episode_number

SETTINGS = ROOT / 'config' / 'library.json'

def library_root():
    if SETTINGS.exists():
        return Path(json.loads(SETTINGS.read_text())['root']).resolve()
    return data_dir().resolve()

def select_root(path):
    p = Path(path).expanduser()
    if not p.is_absolute() or not p.is_dir():
        raise ValueError('请选择机器人上的已有绝对目录')
    p = p.resolve()
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_bytes(dumps({'root': str(p)}))
    return {'root': str(p)}

def include_capture_directory(path):
    p = Path(path).resolve()
    if not SETTINGS.exists() or not p.is_relative_to(library_root()):
        select_root(str(p))

def episode_files(p):
    if p.is_dir():
        return sorted(f for f in p.iterdir() if f.is_file() and not f.is_symlink()
                      and not f.name.startswith('.') and f.suffix in ('.mcap', '.json'))
    return [f for f in (p, p.with_suffix('.json')) if f.is_file() and not f.is_symlink()]

def total_bytes(p):
    if p.is_file():
        return sum(f.stat().st_size for f in episode_files(p))
    return sum(f.stat().st_size for f in p.rglob('*') if f.is_file() and not f.is_symlink())

def episode_row(p):
    raw = p / 'raw.mcap' if p.is_dir() else p
    try:
        report = json.loads(raw.with_suffix('.json').read_text())
    except (OSError, ValueError):
        report = {}
    return {'id': str(p), 'file': p.name, 'path': str(raw),
            'episode_dir': str(p if p.is_dir() else p.parent), 'legacy': p.is_file(),
            'category': record_category(p.name, report), 'bytes': raw.stat().st_size,
            'total_bytes': total_bytes(p), 'duration_s': report.get('duration_s'),
            'modified_ns': raw.stat().st_mtime_ns, 'label': report.get('label', p.name)}

def is_episode_file(p):
    return p.suffix == '.mcap' and not ('.aligned' in p.name or p.name.endswith(('_replay_trace.mcap', '_posture_trace.mcap')))

def tree(path=None):
    root = Path(path).expanduser() if path is not None else library_root()
    if not root.is_absolute() or not root.is_dir():
        raise ValueError('请选择机器人上的已有绝对目录')
    root = root.resolve()
    def visit(folder):
        groups, episodes = [], []
        with os.scandir(folder) as scan:
            entries=sorted(scan,key=lambda e:e.name)
        for entry in entries:
            if entry.name.startswith('.') or entry.is_symlink():
                continue
            p=folder/entry.name
            if entry.is_dir(follow_symlinks=False):
                if (p / 'raw.mcap').is_file():
                    episodes.append(episode_row(p))
                elif not (p / 'raw.mcap.partial').exists():
                    child = visit(p)
                    if child['count']:
                        groups.append(child)
            elif is_episode_file(p) and entry.is_file(follow_symlinks=False):
                episodes.append(episode_row(p))
        episodes.sort(key=lambda e: e['modified_ns'], reverse=True)
        return {'name': folder.name, 'path': str(folder), 'relative_path': str(folder.relative_to(root)),
                'groups': groups, 'episodes': episodes,
                'count': len(episodes) + sum(g['count'] for g in groups),
                'total_bytes': sum(e['total_bytes'] for e in episodes) + sum(g['total_bytes'] for g in groups)}
    return {'root': str(root), 'tree': visit(root)}

def resolve_episodes(paths):
    if not isinstance(paths, list) or not paths:
        raise ValueError('请至少选择一条已完成的记录')
    root = library_root()
    result = []
    for value in dict.fromkeys(paths):
        original = Path(value)
        p = original.resolve()
        if not original.is_absolute() or original.is_symlink() or not p.is_relative_to(root) or p == root:
            raise ValueError('请选择当前数据目录范围内的 episode')
        if any((parent/'raw.mcap').is_file() for parent in p.parents if parent != root and parent.is_relative_to(root)):
            raise ValueError('请选择整条 episode，不支持删除或导出其内部单独文件')
        if any(part.startswith('.') for part in p.relative_to(root).parts):
            raise ValueError('隐藏目录不在采集记录列表中')
        if p.is_dir():
            if not (p / 'raw.mcap').is_file() or (p / 'raw.mcap').is_symlink():
                raise ValueError('记录尚未保存完成或不存在：' + p.name)
        elif not p.is_file() or not is_episode_file(p):
            raise ValueError('记录尚未保存完成或不存在：' + p.name)
        result.append(p)
    return result

def export_files(paths):
    root = library_root()
    entries = []
    for p in resolve_episodes(paths):
        parent = p.parent.relative_to(root)
        # A single root prefix preserves distinct paths even when a child shares its name.
        group = Path(root.name) / parent
        folder = group / (p.name if p.is_dir() else p.stem)
        entries.extend((f, (folder / f.name).as_posix()) for f in episode_files(p))
    return entries

def delete_episodes(paths):
    deleted, freed = [], 0
    for p in paths:
        freed += total_bytes(p)
        match = re.match(r'^episode_(\d+)_', p.name)
        if match:
            remember_episode_number(p.parent, int(match.group(1)))
        if p.is_dir():
            shutil.rmtree(p)
        else:
            for f in episode_files(p):
                f.unlink()
        deleted.append(str(p))
    return {'deleted': deleted, 'deleted_count': len(deleted), 'freed_bytes': freed}
