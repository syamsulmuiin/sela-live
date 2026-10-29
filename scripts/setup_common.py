"""Setup-only helpers. .env is KEY=JSON-string data, NEVER shell input."""
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = Path('/root/voice-lab')
KEY = re.compile(r'(?:VOICE_[A-Z0-9_]+|SELA_HERMES_PYTHON|SELA_WEB_PYTHON)\Z')

def safe_path(root, relative):
    root = Path(root)
    rel = Path(relative)
    if not root.is_absolute() or rel.is_absolute() or not rel.parts or any(p in ('.', '..') for p in rel.parts):
        raise ValueError('unsafe path')
    result = root / rel
    for p in (root, *root.parents, result, *result.parents):
        if p.is_symlink():
            raise ValueError('symlink forbidden')
    if not result.is_relative_to(root):
        raise ValueError('path escaped root')
    return result

def verify_file(path, row):
    path = Path(path)
    safe_path(path.parent, path.name)
    expected = row['sha256']
    if not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('invalid checksum')
    if 'bytes' in row and path.stat().st_size != row['bytes']:
        raise ValueError('asset size mismatch')
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ValueError('asset SHA256 mismatch')
    return True

def write_env(path, values):
    path = Path(path)
    safe_path(path.parent, path.name)
    body = ''.join(k + '=' + json.dumps(v, ensure_ascii=True) + '\n' for k,v in sorted(values.items()) if KEY.fullmatch(k))
    if len(values) != sum(bool(KEY.fullmatch(k)) for k in values) or any(not isinstance(v,str) or '\n' in v or '\r' in v or '\0' in v for v in values.values()):
        raise ValueError('invalid env data')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(body)

def load_env(path):
    path = Path(path)
    safe_path(path.parent, path.name)
    info = path.stat()
    if info.st_mode & 0o177 or not __import__('stat').S_ISREG(info.st_mode) or info.st_size > 65536:
        raise ValueError('env file must be bounded private non-executable data (0600)')
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        key, sep, raw = line.partition('=')
        if not sep or not KEY.fullmatch(key) or key in values:
            raise ValueError('invalid env key')
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            raise ValueError('env values must be JSON strings') from None
        if not isinstance(value, str) or any(c in value for c in '\n\r\0'):
            raise ValueError('invalid env value')
        values[key] = value
    return values
