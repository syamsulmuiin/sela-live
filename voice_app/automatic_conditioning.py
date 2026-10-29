"""Bounded activation selection, not learned emotion/valence synthesis.

Routing is app-owned; request metadata cannot supply native tensors or paths.
"""
import json
from pathlib import Path
from types import MappingProxyType

PROFILE_IDS = ('conditioning_b',) + tuple(
    f'conditioning_b_{activation}_{intensity}'
    for activation in ('calm', 'active') for intensity in ('low', 'medium', 'high'))


MANIFEST_SHA256 = 'ba9257f9c745accddcbc64c017c4638d39f5c1255f6923f5f499acc099aaf403'
APPROVED_HELPER_SHA256 = '8e0c3ddc556206b4f71baaf2139346e7e12b9d9c967ed6cc1ba29276917974ca'


def profile_path(root, manifest, identifier):
    """Pinned app-relative native JSON, usable from isolated python -I workers.

    Fixed B continues through its original resolver. All six new levels require
    this pinned manifest; missing/corrupt assets are never repaired or replaced.
    """
    if type(identifier) is not str or identifier not in PROFILE_IDS:
        raise ValueError('asset_error')
    import hashlib, importlib.util
    try:
        helper = Path(__file__).absolute().with_name('approved_conditioning.py')
        # Bootstrap the existing bounded regular-file validator without executing
        # an unchecked sibling module; the worker separately pins this module.
        import os, stat
        if any(p.is_symlink() for p in (helper, *helper.parents)):
            raise ValueError
        with os.fdopen(os.open(helper, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError
            content = stream.read(1000001)
        if hashlib.sha256(content).hexdigest() != APPROVED_HELPER_SHA256:
            raise ValueError
        spec = importlib.util.spec_from_file_location('_automatic_approved_validator', helper)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        directory = Path(__file__).absolute().parent.parent/'voice_assets/automatic-conditioning'
        data = json.loads(module._read_pinned(directory/'manifest.json', MANIFEST_SHA256))
        module._read_pinned(manifest, module.MODEL_MANIFEST_SHA256)
        base = json.loads(module._read_pinned(Path(root)/'voice_styles/M1.json', module.BASE_SHA256))
        if (set(data['profiles']) != set(PROFILE_IDS) or type(data['version']) is not int
                or data['version'] != 1 or data['native_emotion_conditioning'] is not False):
            raise ValueError
        row = data['profiles'][identifier]
        if row['path'] != identifier+'.json':
            raise ValueError
        path = directory/row['path']
        profile = json.loads(module._read_pinned(path, row['sha256']))
        module.validate_profile(profile, base)
        import math
        for vector in profile['style_ttl']['data'][0]:
            if abs(math.sqrt(sum(x*x for x in vector))-1) > 1e-5:
                raise ValueError
        return path
    except (OSError, ValueError, KeyError, TypeError, ImportError, AttributeError, RecursionError):
        raise ValueError('asset_error') from None


class Router:
    def __init__(self, version, routes, registry):
        self.version = version
        self._routes = MappingProxyType(dict(routes))
        self._registry = registry
        self.emotions = tuple(routes)

    def select(self, delivery):
        from .delivery import DeliverySpec
        if type(delivery) is not DeliverySpec:
            raise ValueError('Invalid conditioning metadata')
        # Frozen does not itself guarantee validated values: reject forged specs,
        # bad types, nonfinite numbers and stale registry versions, never repair.
        if (type(delivery.emotion) is not str or type(delivery.intensity) is not str
                or type(delivery.registry_version) is not int
                or type(delivery.support_level) is not str
                or type(delivery.speed) not in (int, float)
                or type(delivery.silence_duration) not in (int, float)):
            raise ValueError('Invalid conditioning metadata')
        expected = self._registry.resolve(delivery.emotion, delivery.intensity)
        if delivery != expected:
            raise ValueError('Invalid conditioning metadata')
        activation = self._routes.get(delivery.emotion)
        supported = activation is not None
        activation = activation or 'base'
        result = dict(profile_id='conditioning_b' if activation == 'base' else
                      f'conditioning_b_{activation}_{delivery.intensity}',
                      activation=activation, intended_emotion=delivery.emotion,
                      intensity=delivery.intensity, supported=supported)
        if not supported:
            result['reason_code'] = 'unmapped_registered_emotion'
        return result


def load_router(path=None, *, registry=None):
    from .delivery import load_registry, _json, _ID
    registry = load_registry() if registry is None else registry
    path = Path(path) if path is not None else Path(__file__).with_name('automatic_conditioning_rules.json')
    with path.open('rb') as stream:
        raw = stream.read(32769)
    if len(raw) > 32768:
        raise ValueError('Conditioning rules size limit')
    data = _json(raw)
    if (type(data) is not dict or set(data) != {'version', 'routes'}
            or type(data['version']) is not int or not 1 <= data['version'] <= 2147483647
            or type(data['routes']) is not dict or not 1 <= len(data['routes']) <= 64
            or data['routes'].get('neutral') != 'base'):
        raise ValueError('Invalid conditioning rules')
    for identifier, activation in data['routes'].items():
        if (type(identifier) is not str or not _ID.fullmatch(identifier)
                or identifier not in registry.ids or type(activation) is not str
                or activation not in ('base', 'calm', 'active')
                or (identifier != 'neutral' and activation == 'base')):
            raise ValueError('Invalid conditioning route')
    return Router(data['version'], data['routes'], registry)
