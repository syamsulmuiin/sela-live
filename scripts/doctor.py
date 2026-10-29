#!/usr/bin/env python3
"""Offline, read-only readiness checks. Never imports app, refreshes OAuth or calls LLM."""
import sys
sys.dont_write_bytecode = True
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
from setup_common import ROOT, CANONICAL, load_env, safe_path

CLEAN = {'PATH':'/usr/bin:/bin','HOME':'/nonexistent','PYTHONDONTWRITEBYTECODE':'1'}

def namespace_available():
    code = 'import ctypes,sys;sys.exit(0 if ctypes.CDLL(None,use_errno=True).unshare(0x40000000)==0 else 1)'
    try:
        return subprocess.run([sys.executable,'-I','-c',code],env=CLEAN,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5).returncode == 0
    except (OSError,subprocess.TimeoutExpired):
        return False

def module_probe(python, modules):
    if not python or not Path(python).is_file():
        return False
    code = 'import importlib.util,sys;sys.exit(0 if all(importlib.util.find_spec(n) is not None for n in '+repr(modules)+') else 1)'
    try:
        return subprocess.run([python,'-I','-c',code],env=CLEAN,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15).returncode == 0
    except (OSError,subprocess.TimeoutExpired):
        return False

def auth_probe(python):
    # Import only stdlib auth dependencies, never application or native speech.
    try:
        return subprocess.run([python,'-I','-c','import sqlite3, hashlib, secrets'],env=CLEAN,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15).returncode == 0
    except (OSError,subprocess.TimeoutExpired):
        return False

def helper_pins_valid(root=ROOT):
    # AST inspection only. No runtime import, auth DB or inference allocation.
    for source,constant,target in (('voice_app/supertonic3_worker.py','AUTOMATIC_HELPER_SHA256','voice_app/automatic_conditioning.py'),('voice_app/automatic_conditioning.py','APPROVED_HELPER_SHA256','voice_app/approved_conditioning.py')):
        tree = ast.parse(safe_path(root,source).read_text())
        pins = [n.value.value for n in tree.body if isinstance(n,ast.Assign) and isinstance(n.value,ast.Constant) and any(isinstance(t,ast.Name) and t.id==constant for t in n.targets)]
        if len(pins)!=1 or hashlib.sha256(safe_path(root,target).read_bytes()).hexdigest()!=pins[0]:
            return False
    return True

def packaged_speech_valid(config):
    from configure import make_config
    expected = make_config('api','validation-only','https://example.test/v1','',ROOT,{})
    flexible = {'VOICE_AI_BACKEND','VOICE_MODEL','VOICE_DATA_DIR','VOICE_PUBLIC_ORIGIN','VOICE_API_BASE_URL','SELA_WEB_PYTHON'}
    try:
        for key,value in expected.items():
            if key in flexible:
                continue
            if config.get(key) != value:
                return False
            if key.endswith(('_PATH','_MANIFEST','_PYTHON')):
                # Venv Python itself is normally a symlink; its parents must not be.
                relative = Path(value).relative_to(ROOT)
                safe_path(ROOT,relative.parent if key.endswith('_PYTHON') else relative)
        return True
    except (OSError,ValueError):
        return False

def inspect_runtime(config, dependency_root=ROOT):
    from bootstrap import verify_assets
    from run import build_launch
    checks = {}
    checks['canonical_checkout'] = ROOT == CANONICAL and ROOT.resolve() == CANONICAL
    checks['network_namespace_capability'] = namespace_available()
    checks['helper_source_pins'] = helper_pins_valid()
    checks['packaged_speech_configuration'] = packaged_speech_valid(config)
    rows = verify_assets(ROOT) if checks['packaged_speech_configuration'] else []
    checks['speech_asset_sha256'] = bool(rows) and all(r['status']=='verified' for r in rows)
    dependency_rows = rows if Path(dependency_root)==ROOT and rows else verify_assets(dependency_root)
    dependency_verification = {'root':str(dependency_root),'all_verified':bool(dependency_rows) and all(r['status']=='verified' for r in dependency_rows),'launch_readiness_substitute':False}
    checks['zipformer_python_modules'] = module_probe(config.get('VOICE_ZIPFORMER_PYTHON'),['numpy','sherpa_onnx'])
    checks['supertonic_python_modules'] = module_probe(config.get('VOICE_SUPERTONIC3_PYTHON'),['numpy','onnxruntime','supertonic'])
    checks['auth_capability'] = False
    try:
        command, _ = build_launch(config)
        checks['auth_capability'] = auth_probe(command[0])
        checks['launch_configuration'] = True
        checks['web_python_modules'] = module_probe(command[0],['fastapi','uvicorn','httpx','websockets'])
    except ValueError:
        checks['launch_configuration'] = False
        checks['web_python_modules'] = False
    notes = ['Third-party module checks are find_spec presence only; native imports/inference are not tested. Auth stdlib imports are tested on the selected launch interpreter.']
    if config.get('VOICE_AI_BACKEND')=='hermes':
        checks['hermes_adapter_modules'] = module_probe(config.get('SELA_HERMES_PYTHON'),['agent','hermes_cli','openai'])
        home = Path(os.environ.get('HERMES_HOME',str(Path.home()/'.hermes')))
        checks['hermes_auth_file_present'] = (home/'auth.json').is_file()
        notes.append('Hermes auth presence only; OAuth validity and model availability NOT tested; use separately installed Hermes login.')
    try:
        data = safe_path(ROOT,'data')
        checks['auth_data_safe'] = Path(config.get('VOICE_DATA_DIR','')) == data
        if not data.exists():
            notes.append('Fresh auth enrollment required after first start; runtime creates private data/bootstrap-token, valid 30 minutes. Do not publish token.')
    except ValueError:
        checks['auth_data_safe'] = False
    notes.append('No provider call, OAuth refresh, app import, inference, weights allocation, service start or auth data write.')
    return {'read_only':True,'ready':all(checks.values()),'provider_called':False,'application_imported':False,'checks':checks,'dependency_verification':dependency_verification,'module_presence_only':True,'notes':notes}

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dependency-root',type=Path,default=ROOT,help='read-only alternative dependency app root for checksum verification')
    args = p.parse_args(argv)
    try:
        try:
            config = load_env(safe_path(ROOT,'.env'))
            private_env_file = True
        except (OSError,ValueError):
            config = {}
            private_env_file = False
        report = inspect_runtime(config,args.dependency_root)
        report['checks']['private_env_file'] = private_env_file
        if not private_env_file:
            report['ready'] = False
            report['notes'].insert(0,'Missing or unsafe private .env; configure explicitly on fresh canonical host. No app imported.')
        print(json.dumps(report,indent=2))
        return 0 if report['ready'] else 1
    except Exception:
        print(json.dumps({'ready':False,'read_only':True,'provider_called':False,'application_imported':False,'error':'runtime verification failed; no configuration values logged'}))
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
