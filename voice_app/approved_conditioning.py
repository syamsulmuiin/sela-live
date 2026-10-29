"""Immutable app-owned listener-selected style; stdlib only, no overrides."""
import hashlib
from pathlib import Path

PROFILE_SHA256 = '4f52698e957ae4d15d23c7208c26b298d56f70647083d61c9ff518c4d374d828'
MANIFEST_SHA256 = '08a34381da17292b652d384b1316365218f1c4d9fc0ae22405fe8909bcb55cfc'
MODEL_MANIFEST_SHA256 = '0fd578199ae89d38c94b57935ea1f92f7c5aab26ca918cd17e116276953cfe47'
BASE_SHA256 = 'e35604687f5d23694b8e91593a93eec0e4eca6c0b02bb8ed69139ab2ea6b0a5b'


def _read_pinned(path, expected):
    """Bounded regular files only; reject symlinks in every path component."""
    import os
    import stat
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('asset_error')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('asset_error')
        content = stream.read(1_000_001)
    if len(content) > 1_000_000 or hashlib.sha256(content).hexdigest() != expected:
        raise ValueError('asset_error')
    return content


def validate_profile(profile, base):
    """Validate native JSON tensor representation and unchanged duration style."""
    import math
    try:
        if type(profile) is not dict or set(profile) != {'style_ttl', 'style_dp', 'metadata'}:
            raise ValueError
        for key, shape in (('style_ttl', [1, 50, 256]), ('style_dp', [1, 8, 16])):
            value = profile[key]
            if type(value) is not dict or set(value) != {'data', 'dims', 'type'} or value['dims'] != shape or value['type'] != 'float32':
                raise ValueError
            def tensor(data, dims):
                if not dims:
                    if type(data) not in (int, float) or not math.isfinite(data) or abs(data) > 16:
                        raise ValueError
                elif type(data) is not list or len(data) != dims[0]:
                    raise ValueError
                else:
                    for row in data:
                        tensor(row, dims[1:])
            tensor(value['data'], shape)
        if profile['style_dp'] != base['style_dp'] or profile['metadata'] != base['metadata'] or base['metadata']['source_file'] != 'M1.wav':
            raise ValueError
    except (ValueError, KeyError, TypeError, OverflowError):
        raise ValueError('asset_error') from None


def approved_profile(root, manifest):
    """Resolve beside this installed application, never an experiment path.

    Both manifests, the base M1 and the selected profile are independently
    immutable. The official full model validation remains the worker's job.
    """
    import json
    try:
        directory = Path(__file__).absolute().parent.parent / 'voice_assets/conditioning-b'
        path = directory / 'profile.json'
        _read_pinned(directory / 'manifest.json', MANIFEST_SHA256)
        _read_pinned(manifest, MODEL_MANIFEST_SHA256)
        base = json.loads(_read_pinned(Path(root) / 'voice_styles/M1.json', BASE_SHA256))
        profile = json.loads(_read_pinned(path, PROFILE_SHA256))
        validate_profile(profile, base)
        return path
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError('asset_error') from None
