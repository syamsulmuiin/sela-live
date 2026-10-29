#!/usr/bin/env python3
"""Select remote Hermes Codex or an OpenAI-compatible API. No secret CLI flags."""
import sys
sys.dont_write_bytecode = True
import argparse
import getpass
import os
from pathlib import Path
from urllib.parse import urlsplit
from setup_common import ROOT, CANONICAL, safe_path, write_env

class PrivateParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes unrecognised args, including accidental keys.
        self.print_usage(sys.stderr)
        self.exit(2, 'invalid arguments; secrets must use environment or hidden prompt\n')

def make_config(backend, model, base_url, hermes_python, root=ROOT, environ=None):
    environ = os.environ if environ is None else environ
    if backend not in ('hermes','api') or not model.strip() or any(c in model for c in '\n\r\0'):
        raise ValueError('choose hermes/api and a nonempty model')
    root = Path(root)
    if backend == 'api':
        u = urlsplit(base_url)
        if u.scheme not in ('https','http') or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('API base URL must be HTTP(S) without credentials, query or fragment')
    elif not hermes_python or not Path(hermes_python).is_absolute():
        raise ValueError('Hermes requires an explicit absolute --hermes-python (installed separately)')
    values = {
        'VOICE_AI_BACKEND':backend, 'VOICE_MODEL':model,
        'VOICE_DATA_DIR':str(root/'data'), 'VOICE_PUBLIC_ORIGIN':'',
        'VOICE_STT_BACKEND':'local', 'VOICE_LOCAL_ASR_ENGINE':'zipformer_id_int8',
        'VOICE_LOCAL_PREVIEW':'1', 'VOICE_ZIPFORMER_STATEFUL':'1',
        'VOICE_ALLOW_BROWSER_STT':'0',
        'VOICE_ZIPFORMER_PYTHON':str(root/'autonomous/zipformer/venv/bin/python'),
        'VOICE_ZIPFORMER_MODEL_PATH':str(root/'autonomous/zipformer/model'),
        'VOICE_TTS_BACKEND':'supertonic3',
        'VOICE_SUPERTONIC3_PYTHON':str(root/'autonomous/expressive-tts/venv/bin/python'),
        'VOICE_SUPERTONIC3_MODEL_PATH':str(root/'autonomous/expressive-tts/assets'),
        'VOICE_SUPERTONIC3_MANIFEST':str(root/'autonomous/expressive-tts/assets-manifest.json'),
        'VOICE_SUPERTONIC3_STYLE_PROFILE':'conditioning_b',
        'VOICE_AUTOMATIC_CONDITIONING':'1', 'VOICE_EXPERIMENTAL_DELIVERY_PROSODY':'0',
        'SELA_WEB_PYTHON':str(root/'deploy/web-venv/bin/python'),
    }
    if backend == 'api':
        values['VOICE_API_BASE_URL'] = base_url
        if environ.get('VOICE_API_KEY'):
            values['VOICE_API_KEY'] = environ['VOICE_API_KEY']
    else:
        values['SELA_HERMES_PYTHON'] = hermes_python
    return values

def api_key(environ=None):
    environ = os.environ if environ is None else environ
    if environ.get('VOICE_API_KEY','').strip():
        return environ['VOICE_API_KEY']
    if not sys.stdin.isatty():
        raise ValueError('hidden terminal or VOICE_API_KEY environment required')
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        try:
            key=getpass.getpass('Remote API key (hidden): ')
        except getpass.GetPassWarning:
            raise ValueError('hidden input unavailable') from None
    if not key.strip():
        raise ValueError('API key required')
    return key

def main(argv=None):
    p = PrivateParser(description=__doc__)
    p.add_argument('--backend',choices=('api','hermes'),required=True)
    p.add_argument('--model',required=True,help='remote model identifier, not a filesystem path')
    p.add_argument('--base-url',default='',help='OpenAI-compatible API base URL, normally ending /v1; never credentials')
    p.add_argument('--hermes-python',default='',help='absolute separately installed Hermes interpreter; must also have web dependencies')
    p.add_argument('--write',action='store_true',help='create canonical .env mode0600; refuses existing .env/data. Default validates only')
    args = p.parse_args(argv)
    try:
        values = make_config(args.backend,args.model,args.base_url,args.hermes_python)
        if args.write:
            if ROOT != CANONICAL or ROOT.resolve() != CANONICAL:
                raise ValueError('configuration write requires fresh canonical /root/voice-lab')
            if safe_path(ROOT,'data').exists():
                raise ValueError('existing auth/data directory; refusing reconfiguration')
            if args.backend == 'api' and not values.get('VOICE_API_KEY'):
                values['VOICE_API_KEY'] = api_key()
            write_env(ROOT/'.env',values)
        print('configuration '+('written privately' if args.write else 'validated (read-only)')+'; backend='+args.backend+'; no provider call')
        return 0
    except (ValueError,OSError,EOFError):
        print('configuration refused; check fresh canonical root, options, private paths and environment (no values logged)',file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
