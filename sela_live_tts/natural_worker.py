"""Isolated natural-v1 candidate; the pinned native guard remains authoritative."""
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import sys
sys.dont_write_bytecode = True

HERE = Path(__file__).absolute().parent
MANIFEST_SHA = '77308488f444ecf7a9e0f7b5cd71408a165e3acd0536cbc119b7c575ef4cebdc'
PIN_HELPER_SHA = '9436ec03105e5a7a787cdfc8cb87e4e77d10670a58656b1561fa3218a59dccc0'
GUARD_SHA = 'eb0e6c9cd8655ff1eb2cb5c64f5379945295f86b5c971a79d90ed3ccda03d625'
PROFILES = ('conditioning_b',) + tuple(
    f'conditioning_b_{a}_{i}' for a in ('calm', 'active') for i in ('low', 'medium', 'high'))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError('asset_error')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_native():
    helper = HERE / 'source_pins.py'
    if any(p.is_symlink() for p in (helper, *helper.parents)):
        raise ValueError('asset_error')
    with os.fdopen(os.open(helper, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('asset_error')
        raw = stream.read(1_000_001)
    if hashlib.sha256(raw).hexdigest() != PIN_HELPER_SHA:
        raise ValueError('asset_error')
    pins = load_module('_natural_fixed_source_pins', helper)
    data = pins.verify_sources(HERE / 'upstream-manifest.json', MANIFEST_SHA)
    if data['guard_python_files']['supertonic3_worker.py'] != GUARD_SHA:
        raise ValueError('asset_error')
    required = {'sela_live/__init__.py', 'sela_live/audio/__init__.py',
                'sela_live/audio/stt.py', 'sela_live/audio/tts.py', 'voice_app/zipformer_state.py'}
    if (data.get('core_root') != str(HERE.parent)
            or set(data.get('core_python_files', {})) != required):
        raise ValueError('asset_error')
    for relative, expected in data['core_python_files'].items():
        if hashlib.sha256(pins._read(HERE.parent / relative)).hexdigest() != expected:
            raise ValueError('asset_error')
    # All manifest/SDK/helper/core/native-guard hashes checked before import.
    guard = load_module('_natural_fixed_native_guard', Path(data['guard_root']) / 'supertonic3_worker.py')
    return data, guard


def parse_cli(argv):
    if (len(argv) != 5 or argv[0] not in ('check', 'tts')
            or argv[3] not in PROFILES or argv[4] != '12'):
        raise ValueError('invalid_request')
    action, assets, manifest, profile, _ = argv
    for value in (assets, manifest):
        path = Path(value)
        if not path.is_absolute() or '..' in path.parts:
            raise ValueError('absolute_assets_required')
    return action, assets, manifest, profile


def _owned_tts():
    # verify_native checks every core byte before any owned module import,
    # including direct compatibility calls made outside main().
    data, _ = verify_native()
    return load_module('_sela_live_verified_tts', Path(data['core_root']) / 'sela_live/audio/tts.py')


def validate_text(text):
    return _owned_tts().validate_text(text)


def controlled_runtime(guard, chunker):
    return _owned_tts().controlled_runtime(guard, chunker)


def sdk_chunker(text, maximum):
    # Lazy: unchanged guard.main isolates and validates assets before TTS import.
    from supertonic.pipeline import chunk_text
    return chunk_text(text, maximum)


def main():
    action, assets, manifest, profile = parse_cli(sys.argv[1:])
    data, guard = verify_native()
    original_validate = guard.validate_request

    def text_only(request):
        if type(request) is not dict or set(request) != {'text'}:
            raise ValueError('request_fields')
        validate_text(request['text'])
        return original_validate(request)

    guard.validate_request = text_only
    guard.Runtime = controlled_runtime(guard, sdk_chunker)
    sys.path.insert(0, str(Path(data['sdk_root']).parent))
    sys.argv = [str(Path(data['guard_root']) / 'supertonic3_worker.py'), action, assets, manifest, profile]
    try:
        guard.main()
    except Exception as exc:
        guard.emit_failure(guard.failure_reason(exc))
        raise SystemExit(1)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        os.write(2, b'supertonic3_failure=asset_error\n')
        raise SystemExit(1)
