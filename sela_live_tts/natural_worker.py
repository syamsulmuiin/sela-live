"""Isolated natural-v1 candidate; the pinned native guard remains authoritative."""
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import sys
sys.dont_write_bytecode = True

HERE = Path(__file__).absolute().parent
MANIFEST_SHA = 'f91cc185c59c19cb0519977811762d30b635cfd609a8336083c5d8dd77055f74'
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
    # All manifest/SDK/helper/native-guard hashes checked before guard/native import.
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


def validate_text(text):
    if (type(text) is not str or not text.strip() or len(text) > 800
            or len(text.encode('utf8')) > 3200):
        raise ValueError('text_limit')
    if '<' in text or '>' in text:
        raise ValueError('unsupported_tag')
    if any(ord(c) < 32 and c not in '\n\t' for c in text):
        raise ValueError('text_control')
    return text


def controlled_runtime(guard, chunker):
    class ControlledRuntime(guard.Runtime):
        def synthesize(self, request):
            if type(request) is not dict or set(request) != {'text'}:
                raise ValueError('request_fields')
            validate_text(request['text'])
            engine = self.engine
            original = engine.synthesize
            had_override = 'synthesize' in engine.__dict__
            previous = engine.__dict__.get('synthesize')

            def checked(text, **kwargs):
                import numpy as np
                if (type(text) is not str or text != request['text']
                        or set(kwargs) != {'voice_style', 'lang', 'total_steps', 'verbose', 'speed'}
                        or kwargs['voice_style'] is not self.style
                        or type(kwargs['lang']) is not str or kwargs['lang'] != 'id'
                        or type(kwargs['total_steps']) is not int or kwargs['total_steps'] != 8
                        or kwargs['verbose'] is not False
                        or type(kwargs['speed']) is not float or kwargs['speed'] != 1.05):
                    raise ValueError('delivery_controls')
                chunks = chunker(text, 800)
                if (type(chunks) is not list or len(chunks) != 1
                        or type(chunks[0]) is not str
                        or ' '.join(chunks[0].split()) != ' '.join(text.split())):
                    raise ValueError('text_limit')
                validate_text(chunks[0])
                # Match the exact context the pinned high-level chunker will pass to core.
                ids, mask = engine.model.text_processor([chunks[0]], 'id')
                if (not isinstance(ids, np.ndarray) or ids.dtype != np.int64
                        or ids.ndim != 2 or ids.shape[0] != 1
                        or not 1 <= ids.shape[1] <= 512 or not (ids >= 0).all()
                        or not isinstance(mask, np.ndarray) or mask.dtype != np.float32
                        or mask.shape != (1, 1, ids.shape[1]) or not (mask == 1).all()):
                    raise ValueError('text_limit')
                duration, *_ = engine.model.dp_ort.run(None, {
                    'text_ids': ids, 'style_dp': self.style.dp, 'text_mask': mask})
                if (not isinstance(duration, np.ndarray) or duration.shape != (1,)
                        or duration.dtype.kind != 'f'):
                    raise ValueError('duration_limit')
                # Use genuine selected-style DP, and the SDK's dtype-preserving division.
                duration = duration / 1.05
                if not np.isfinite(duration).all() or not 0 < float(duration[0]) <= 28:
                    raise ValueError('duration_limit')
                return original(text, **dict(kwargs, total_steps=12, max_chunk_length=800))

            engine.synthesize = checked
            try:
                return super().synthesize(request)
            finally:
                if had_override:
                    engine.synthesize = previous
                else:
                    del engine.synthesize
    return ControlledRuntime


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
