"""Read-only robot-local MCAP file browsing, independent of capture settings."""
import json
from pathlib import Path


def resolve_mcap(value, default_root):
    root = Path(default_root).resolve()
    if not value:
        raise ValueError('请选择一个 MCAP 文件')
    path = Path(value).expanduser()
    if not path.is_absolute():
        if path.name != str(value):
            raise ValueError('请输入机器人上的 MCAP 绝对路径')
        aliases = root / '.episode_aliases.json'
        name = json.loads(aliases.read_text()).get(str(value), str(value)) if aliases.exists() else str(value)
        path = root / name
        if path.resolve().parent != root:
            raise ValueError('episode 不在当前目录中')
    path = path.resolve()
    if path.is_dir():
        path = (path/'raw.mcap').resolve()
    if path.suffix != '.mcap' or not path.is_file():
        raise ValueError('找不到已保存的 MCAP 文件，请选择 .mcap 文件或包含 raw.mcap 的 episode 目录')
    return path


def row(path):
    path = Path(path).resolve()
    try:
        report = json.loads(path.with_suffix('.json').read_text())
    except (OSError, ValueError):
        report = {}
    name = path.parent.name if path.name == 'raw.mcap' else path.name
    return {'file': name, 'id': str(path), 'path': str(path), 'episode_dir': str(path.parent),
            'label': report.get('label', name), 'bytes': path.stat().st_size,
            'modified_ns': path.stat().st_mtime_ns, 'duration_s': report.get('duration_s')}


def listing(value, default_root):
    path = Path(value).expanduser() if value else Path(default_root)
    if not path.is_absolute():
        raise ValueError('请输入机器人上的绝对文件或目录路径')
    path = path.resolve()
    if path.is_file() or (path/'raw.mcap').is_file():
        file = resolve_mcap(str(path), default_root)
        return {'root': str(file.parent), 'path': str(file), 'files': [row(file)]}
    if not path.is_dir():
        raise ValueError('MCAP 目录不存在')
    candidates = list(path.glob('*.mcap')) + list(path.glob('*/raw.mcap'))
    files = [row(p) for p in candidates if p.is_file() and not p.name.startswith('.')
             and not p.parent.name.startswith('.')]
    return {'root': str(path), 'path': str(path),
            'files': sorted(files, key=lambda x:x['modified_ns'], reverse=True)}


def browse(value, default_root):
    path = Path(value).expanduser() if value else Path(default_root)
    if not path.is_absolute():
        raise ValueError('请输入机器人上的绝对目录路径')
    path = path.resolve()
    if path.is_file():
        path = path.parent
    if not path.is_dir():
        raise ValueError('目录不存在')
    folders = [{'name':p.name, 'path':str(p)} for p in sorted(path.iterdir())
               if p.is_dir() and not p.name.startswith('.')]
    files = [row(p) for p in sorted(path.glob('*.mcap')) if p.is_file() and not p.name.startswith('.')]
    return {'path':str(path), 'parent':str(path.parent), 'folders':folders, 'files':files}
