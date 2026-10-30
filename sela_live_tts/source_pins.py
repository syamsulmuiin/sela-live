"""Hash-check native source before import; no model downloads or source edits."""
import hashlib,json,stat
from pathlib import Path,PurePosixPath


def _read(path):
    path=Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('Unsafe pinned path')
    if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size>1_000_000:
        raise ValueError('Invalid pinned source')
    return path.read_bytes()


def verify_sources(manifest,expected_sha256):
    raw=_read(manifest)
    if hashlib.sha256(raw).hexdigest()!=expected_sha256:raise ValueError('Source manifest drift')
    data=json.loads(raw)
    for group in ('sdk','guard','live'):
        root=Path(data[group+'_root'])
        if not root.is_absolute() or any(p.is_symlink() for p in (root,*root.parents)):
            raise ValueError('Unsafe pinned root')
        files=data[group+'_python_files']
        if type(files) is not dict or not files:raise ValueError('Empty pinned source set')
        for relative,expected in files.items():
            parts=PurePosixPath(relative)
            if parts.is_absolute() or '..' in parts.parts or not relative.endswith('.py'):
                raise ValueError('Invalid pinned relative source')
            if hashlib.sha256(_read(root/relative)).hexdigest()!=expected:
                raise ValueError('Pinned source drift')
        if group=='sdk':
            actual={str(p.relative_to(root)) for p in root.rglob('*.py')}
            if actual!=set(files):raise ValueError('SDK source inventory drift')
    return data
