"""Download completed robot files unchanged, individually or as a streaming TAR."""
from pathlib import Path
import math
import tarfile
import uuid
from urllib.parse import quote
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from storage import file_path, data_dir
import library

router = APIRouter(prefix='/api/export')
exports = {}

def completed_files(name):
    raw = file_path(name)
    if raw.parent == data_dir().resolve():
        candidates = [raw, raw.with_suffix('.json')]
        folder = raw.stem
    else:
        candidates = sorted(raw.parent.iterdir())
        folder = raw.parent.name
    return [(p, folder + '/' + p.name) for p in candidates
            if p.is_file() and p.suffix in ('.mcap', '.json') and not p.name.startswith('.')]

@router.get('/client')
def client(request: Request):
    return {'server': 'robot', 'robot_data_dir': str(data_dir()),
            'peer_ip': request.client.host if request.client else '',
            'destination': 'current_browser_computer',
            'lan_url': 'http://10.42.1.101:18769/'}

@router.post('/prepare')
def prepare(body: dict = Body(...)):
    if 'episode_paths' in body:
        chosen = library.resolve_episodes(body['episode_paths'])
        names = [p.name for p in chosen]
        paths = library.export_files([str(p) for p in chosen])
    else:
        names = list(dict.fromkeys(body.get('files', [])))
        if not names:
            raise ValueError('请至少勾选一条已完成的记录')
        paths = [entry for name in names for entry in completed_files(name)]
    identity = uuid.uuid4().hex
    entries = []
    for i, (path, archive_path) in enumerate(paths):
        size = path.stat().st_size
        info = tarfile.TarInfo(archive_path)
        info.size = size
        info.mtime = int(path.stat().st_mtime)
        info.mode = 0o644
        entries.append({'path': path, 'relative_path': archive_path, 'bytes': size,
                        'header': info.tobuf(format=tarfile.PAX_FORMAT),
                        'url': f'/api/export/{identity}/files/{i}'})
    archive_name = (names[0] if len(names) == 1 else 'robot_episodes_' + str(len(names))) + '.tar'
    exports[identity] = {'entries': entries, 'filename': archive_name}
    return {'id': identity, 'filename': archive_name, 'total_bytes': sum(e['bytes'] for e in entries),
            'files': [{k: e[k] for k in ('relative_path', 'bytes', 'url')} for e in entries],
            'archive_url': f'/api/export/{identity}/archive'}

def get_export(identity):
    if identity not in exports:
        raise HTTPException(404, '导出任务不存在，请重新选择记录')
    return exports[identity]

@router.get('/{identity}/files/{index}')
def download_file(identity: str, index: int):
    entries = get_export(identity)['entries']
    if not 0 <= index < len(entries):
        raise HTTPException(404, '文件不存在')
    e = entries[index]
    return FileResponse(e['path'], filename=e['path'].name, media_type='application/octet-stream')

def tar_stream(entries):
    for entry in entries:
        yield entry['header']
        remaining = entry['bytes']
        with entry['path'].open('rb') as source:
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise OSError('源文件在导出期间被移除或截短')
                remaining -= len(chunk)
                yield chunk
        yield b'\0' * (-entry['bytes'] % 512)
    yield b'\0' * 1024

@router.get('/{identity}/archive')
def download_archive(identity: str):
    job = get_export(identity)
    length = 1024 + sum(len(e['header']) + math.ceil(e['bytes'] / 512) * 512 for e in job['entries'])
    return StreamingResponse(tar_stream(job['entries']), media_type='application/x-tar',
        headers={'Content-Length': str(length),
                 'Content-Disposition': "attachment; filename*=UTF-8''" + quote(job['filename']),
                 'Cache-Control': 'no-store'})
