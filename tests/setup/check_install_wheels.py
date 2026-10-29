#!/usr/bin/env python3
"""Opt-in installer integration: temp venvs, real locked wheels, NO models/inference.

Run with Python3.12. Speech wheels are read-only inputs from --dependency-root.
--fetch-web permits public binary transport wheel downloads into a temporary tree.
No changes are made to dependency-root; temporary environments are removed.
"""
import sys
sys.dont_write_bytecode = True
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import bootstrap as b
from setup_common import safe_path,verify_file

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dependency-root',type=Path,required=True)
    p.add_argument('--fetch-web',action='store_true')
    args=p.parse_args()
    if sys.version_info[:2] != (3,12):
        p.error('requires Python3.12')
    plan=b.asset_plan(ROOT)
    report={'fresh_host_install_verified':False,'provider_calls':0,'models_loaded':False,'environments':[]}
    with tempfile.TemporaryDirectory(prefix='wheel-integration-',dir=ROOT/'tests/setup') as temporary:
        target=Path(temporary)
        for prefix,folder,modules in (('deploy','web-venv',['fastapi','uvicorn','httpx','websockets']),('autonomous/zipformer','venv',['numpy','sherpa_onnx']),('autonomous/expressive-tts','venv',['numpy','onnxruntime','supertonic'])):
            rows=[r for r in plan if r['path'].startswith(prefix+'/wheels/')]
            for row in rows:
                if prefix=='deploy' and args.fetch_web:
                    b.download(row,target)
                else:
                    source=safe_path(args.dependency_root,row['path'])
                    verify_file(source,row)
                    path=safe_path(target,row['path'])
                    path.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(source,path)
            venv=b.install_venv(target,prefix,folder,rows)
            code='import '+','.join(modules)
            subprocess.run([str(venv/'bin/python'),'-I','-c',code],check=True,env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent','PYTHONDONTWRITEBYTECODE':'1'},stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
            report['environments'].append({'environment':prefix,'wheel_count':len(rows),'pip_check':'passed','native_import_probe':'passed','inference':False})
        # GET and SHA256-check every small archived Git asset; no weight download.
        for row in plan:
            if '/assets/' in row['path'] and not row['path'].endswith('.onnx'):
                b.download(row,target)
        report['small_archived_assets_sha256_verified']=sum('/assets/' in r['path'] and not r['path'].endswith('.onnx') for r in plan)
    print(json.dumps(report,indent=2))
    (ROOT/'config/wheel-install-integration.json').write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__':
    main()
