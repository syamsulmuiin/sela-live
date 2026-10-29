#!/usr/bin/env python3
"""Run authenticated Sela on loopback:8091; .env is parsed as data, never sourced."""
import sys
sys.dont_write_bytecode = True
import argparse
import json
import os
from pathlib import Path
from setup_common import ROOT, CANONICAL, load_env, safe_path

def build_launch(config, root=ROOT, environ=None):
    inherited = os.environ if environ is None else environ
    # Do not inherit unrelated secrets, proxies or stale VOICE_* settings.
    env = {k:v for k,v in inherited.items() if k in ('PATH','HOME','LANG','LC_ALL','TMPDIR','HERMES_HOME')}
    env.update({k:v for k,v in config.items() if k.startswith('VOICE_')})
    # Runtime API key can be injected without saving it to disk.
    if inherited.get('VOICE_API_KEY'):
        env['VOICE_API_KEY'] = inherited['VOICE_API_KEY']
    backend = env.get('VOICE_AI_BACKEND')
    if backend not in ('hermes','api'):
        raise ValueError('explicit backend required')
    if backend == 'api':
        if not all(env.get(k,'').strip() for k in ('VOICE_API_BASE_URL','VOICE_API_KEY','VOICE_MODEL')):
            raise ValueError('API backend needs URL/model and key from private env file or process environment')
        from urllib.parse import urlsplit
        try:
            url = urlsplit(env['VOICE_API_BASE_URL'])
            if (url.scheme not in ('https','http') or not url.hostname
                    or url.username or url.password or url.query or url.fragment):
                raise ValueError
        except ValueError:
            raise ValueError('API base URL must be HTTP(S) without credentials, query or fragment') from None
        python = config.get('SELA_WEB_PYTHON') or str(root/'deploy/web-venv/bin/python')
    else:
        python = config.get('SELA_HERMES_PYTHON','')
    if not python or not Path(python).is_absolute() or not Path(python).is_file() or not os.access(python,os.X_OK):
        raise ValueError('explicit executable Python unavailable; Hermes must be installed separately')
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    # Authenticated factory, bounded transport, private logs and loopback only.
    command = [python,'-m','uvicorn','voice_app.server:create_app','--factory','--no-proxy-headers','--ws-max-size','860000','--limit-concurrency','32','--no-access-log','--host','127.0.0.1','--port','8091']
    return command, env

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--check',action='store_true',help='read-only launch validation, no app import/start/inference')
    args = p.parse_args(argv)
    try:
        if ROOT != CANONICAL or ROOT.resolve() != CANONICAL:
            raise ValueError('runtime requires canonical /root/voice-lab; alternative checkouts are verification-only')
        config = load_env(safe_path(ROOT,'.env'))
        command,env = build_launch(config)
        if Path(config.get('VOICE_DATA_DIR','')) != ROOT/'data':
            raise ValueError('auth data must remain local to checkout/data')
        safe_path(ROOT,'data')
        if args.check:
            print(json.dumps({'validated':True,'backend':config['VOICE_AI_BACKEND'],'host':'127.0.0.1','port':8091,'provider_called':False}))
            return 0
        from doctor import inspect_runtime
        report = inspect_runtime(config)
        if not report['ready']:
            raise ValueError('doctor failed; run scripts/doctor.py for safe diagnostics')
        os.chdir(ROOT)
        os.execve(command[0],command,env)
    except Exception:
        print('launch refused; canonical runtime, private configuration, data scope or doctor requirements failed',file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
