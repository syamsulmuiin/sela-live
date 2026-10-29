#!/usr/bin/env python3
"""Fresh-host installer. No arguments/--check are offline and read-only."""
import sys
sys.dont_write_bytecode = True
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import urllib.request
from urllib.parse import urlsplit, quote
from setup_common import ROOT, CANONICAL, safe_path, verify_file

TTS_REV = '3cadd1ee6394adea1bd021217a0e650ede09a323'
ZIP_REV = '4e5a13cbe3e9cd4e3775447d86178ef51759096f'
TTS_REPO = 'supertone-oss-archive/supertonic-3'
# Original revision is now 404 in the moved namespace. This immutable archive
# revision has identical Git blob IDs, sizes and LFS SHA256s for ALL ten assets.
# Never change the runtime manifest revision; download SHA256 is still original.
TTS_DOWNLOAD_REV = 'aafc6e32416a594460b32413efc49d7fe4ce6d46'
ZIP_REPO = 'spacewave/sherpa-onnx-streaming-zipformer2-id'
# Ignore proxy/auth/netrc settings. No provider endpoint is contacted here.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def asset_plan(root=ROOT):
    root = Path(root)
    tts = json.loads(safe_path(root, 'autonomous/expressive-tts/assets-manifest.json').read_text())
    z = json.loads(safe_path(root, 'autonomous/zipformer/artifact-verification.json').read_text())
    if tts['revision'] != TTS_REV or z['revision'] != ZIP_REV:
        raise ValueError('source revision changed')
    rows = []
    for row in tts['files']:
        rows.append(dict(row, original_revision=TTS_REV, path='autonomous/expressive-tts/assets/'+row['path'], url=f'https://huggingface.co/{TTS_REPO}/resolve/{TTS_DOWNLOAD_REV}/'+quote(row['path'])))
    for row in z['artifacts']:
        url = row.get('url') or f'https://huggingface.co/{ZIP_REPO}/resolve/{ZIP_REV}/'+quote(row['upstream']['rfilename'])
        rows.append(dict(row, path='autonomous/zipformer/'+row['path'], url=url))
    wheels = json.loads(safe_path(root, 'autonomous/expressive-tts/wheels-manifest.json').read_text())
    for row in wheels:
        rows.append(dict(row, path='autonomous/expressive-tts/wheels/'+row['file']))
    for row in json.loads(safe_path(root, 'config/web-wheels.json').read_text()):
        rows.append(dict(row, path='deploy/wheels/'+row['file']))
    paths = set()
    for row in rows:
        safe_path(root, row['path'])
        if row['path'] in paths or not __import__('re').fullmatch('[a-f0-9]{64}', row['sha256']):
            raise ValueError('invalid artifact manifest')
        paths.add(row['path'])
        u = urlsplit(row['url'])
        if u.scheme != 'https' or u.hostname not in ('huggingface.co', 'files.pythonhosted.org') or u.username or u.password or u.query or u.fragment:
            raise ValueError('untrusted artifact source')
        if not row['path'].endswith('.whl') and 'upstream' in row:
            upstream = row['upstream']
            if upstream.get('lfs',{}).get('sha256',row['sha256']) != row['sha256']:
                raise ValueError('upstream hash mismatch')
    return rows

def verify_assets(dependency_root, root=ROOT):
    results = []
    for row in asset_plan(root):
        # Transport wheels are separate from the speech dependency readiness check.
        if row['path'].startswith('deploy/'):
            continue
        path = safe_path(Path(dependency_root), row['path'])
        try:
            verify_file(path, row)
            status = 'verified'
        except (OSError, ValueError):
            status = 'missing_or_corrupt'
        results.append({'path':row['path'], 'status':status})
    return results

def check_sources(root=ROOT):
    results = []
    # Check pinned API metadata for BOTH large weights and small Git blobs.
    # Probe the original revision first, but do not require a moved commit to
    # exist when all original content identities match the pinned archive.
    original_url = f'https://huggingface.co/api/models/{TTS_REPO}/revision/{TTS_REV}?blobs=true'
    try:
        with OPENER.open(original_url, timeout=45) as response:
            original_status = response.status
    except __import__('urllib.error', fromlist=['HTTPError']).HTTPError as exc:
        original_status = exc.code
        if exc.code not in (404, 410):
            raise
    results.append({'repository':TTS_REPO, 'revision':TTS_REV, 'metadata_http_status':original_status})
    for repo, rev, manifest, key in ((TTS_REPO,TTS_DOWNLOAD_REV,'autonomous/expressive-tts/assets-manifest.json','files'), (ZIP_REPO,ZIP_REV,'autonomous/zipformer/artifact-verification.json','artifacts')):
        url = f'https://huggingface.co/api/models/{repo}/revision/{rev}?blobs=true'
        with OPENER.open(url, timeout=45) as r:
            metadata = json.load(r)
        if metadata.get('sha') != rev:
            raise ValueError('remote revision mismatch')
        siblings = {r['rfilename']:r for r in metadata['siblings']}
        for row in json.loads(safe_path(root,manifest).read_text())[key]:
            upstream = row.get('source') or row.get('upstream')
            if not upstream:
                continue
            live = siblings.get(upstream['rfilename'])
            if live is None or any(live.get(k) != upstream.get(k) for k in ('blobId','size')):
                raise ValueError('remote pinned metadata mismatch')
            if upstream.get('lfs') and live.get('lfs',{}).get('sha256') != row['sha256']:
                raise ValueError('remote pinned LFS hash mismatch')
        results.append({'repository':repo, 'revision':rev, 'status':'pinned_metadata_verified'})
    def probe(row):
        request = urllib.request.Request(row['url'], method='HEAD')
        with OPENER.open(request, timeout=45) as response:
            if response.status != 200:
                raise ValueError('artifact unavailable')
        return {'path':row['path'], 'status':'HEAD_200'}
    with ThreadPoolExecutor(max_workers=5) as pool:
        results.extend(pool.map(probe,asset_plan(root)))
    return results

def assert_fresh(root=ROOT):
    root = Path(root)
    if root != CANONICAL or root.resolve() != CANONICAL:
        raise ValueError('install requires a fresh canonical /root/voice-lab checkout')
    safe_path(root,'deploy')
    forbidden = ('data','.env','.setup-installing','deploy/web-venv','deploy/wheels', 'autonomous/zipformer/venv','autonomous/zipformer/wheels','autonomous/zipformer/model', 'autonomous/expressive-tts/venv','autonomous/expressive-tts/wheels','autonomous/expressive-tts/assets', 'audio-runtime')
    if any(safe_path(root,p).exists() for p in forbidden):
        raise ValueError('existing installation/data detected; refusing to overwrite')
    for name in ('voice-lab.service','sela-voice.service'):
        for base in ('/etc/systemd/system','/usr/lib/systemd/system','/root/.config/systemd/user'):
            if (Path(base)/name).exists() or (Path(base)/name).is_symlink():
                raise ValueError('existing service detected; refusing installation')
        if shutil.which('systemctl'):
            state = subprocess.run(['systemctl','show',name,'--property=FragmentPath,ActiveState'],capture_output=True,text=True,timeout=10)
            if any(line.startswith('FragmentPath=') and line != 'FragmentPath=' for line in state.stdout.splitlines()) or 'ActiveState=active' in state.stdout:
                raise ValueError('existing service detected; refusing installation')
    with socket.socket() as s:
        try:
            s.bind(('127.0.0.1',8091))
        except OSError:
            raise ValueError('loopback port 8091 already in use') from None

def download(row, root):
    target = safe_path(root,row['path'])
    target.parent.mkdir(parents=True,exist_ok=True)
    # Existing verified artifacts can only occur during this one installation.
    if target.exists():
        verify_file(target,row)
        return
    temporary = safe_path(root,row['path']+'.partial')
    try:
        fd = os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as output, OPENER.open(row['url'],timeout=90) as source:
            total = 0
            cap = row.get('bytes', 150_000_000)
            while block := source.read(1024*1024):
                total += len(block)
                if total > cap:
                    raise ValueError('download exceeds expected size')
                output.write(block)
        verify_file(temporary,row)
        os.replace(temporary,target)
    finally:
        temporary.unlink(missing_ok=True)

def install_venv(root, prefix, folder, plan):
    rows = [r for r in plan if r['path'].startswith(prefix+'/wheels/')]
    if not rows or any(not r['path'].endswith('.whl') for r in rows):
        raise ValueError('only locked binary wheels may be installed')
    venv = safe_path(root,prefix+'/'+folder)
    if venv.exists():
        raise ValueError('refusing existing virtual environment')
    for row in rows:
        verify_file(safe_path(root,row['path']),row)
    clean = {'PATH':'/usr/bin:/bin','HOME':'/nonexistent','PYTHONDONTWRITEBYTECODE':'1','PIP_CONFIG_FILE':os.devnull,'PIP_NO_CACHE_DIR':'1','TMPDIR':str(venv.parent)}
    subprocess.run([sys.executable,'-I','-m','venv',str(venv)],env=clean,check=True)
    req = safe_path(root,prefix+'/install-wheels.lock')
    with req.open('x') as output:
        output.write(''.join(safe_path(root,r['path']).as_uri()+' --hash=sha256:'+r['sha256']+'\n' for r in rows))
    subprocess.run([str(venv/'bin/python'),'-I','-m','pip','--isolated','install','--no-index','--no-deps','--no-cache-dir','--disable-pip-version-check','--only-binary=:all:','--require-hashes','-r',str(req)],env=clean,check=True)
    subprocess.run([str(venv/'bin/python'),'-I','-m','pip','--isolated','check'],env=clean,check=True)
    return venv

def install(root=ROOT):
    assert_fresh(root)  # First: reject current production BEFORE any write/network.
    if platform.system() != 'Linux' or platform.machine() != 'x86_64' or (sys.version_info.major,sys.version_info.minor) != (3,12):
        raise ValueError('requires Python 3.12 on Ubuntu 24.04 Linux x86_64')
    release = Path('/etc/os-release').read_text()
    if 'ID=ubuntu\n' not in release or 'VERSION_ID="24.04"' not in release:
        raise ValueError('only Ubuntu 24.04 is supported')
    plan = asset_plan(root)
    # Preflight venv/ensurepip and namespace; never load an inference engine.
    subprocess.run([sys.executable,'-I','-c','import venv, ensurepip'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    from doctor import namespace_available
    if not namespace_available():
        raise ValueError('CAP_SYS_ADMIN/network namespace unavailable; runtime guards require it')
    marker = safe_path(root,'.setup-installing')
    fd = os.open(marker,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    os.close(fd)
    # Keep marker on failure: no unsafe automatic resume/overwrite.
    check_sources(root)
    for row in plan:
        download(row,root)
    for prefix, folder in (('deploy','web-venv'),('autonomous/zipformer','venv'),('autonomous/expressive-tts','venv')):
        install_venv(root,prefix,folder,plan)
    marker.unlink()
    return {'installed':True,'started':False,'next':'configure.py then doctor.py; systemd installation is manual'}

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    modes = p.add_mutually_exclusive_group()
    modes.add_argument('--check',action='store_true',help='offline read-only readiness report (default)')
    modes.add_argument('--install',action='store_true',help='explicit fresh canonical Ubuntu 24.04 install; no service startup')
    modes.add_argument('--verify-assets',action='store_true',help='read-only SHA256/size verification of speech assets and wheels')
    modes.add_argument('--check-sources',action='store_true',help='opt-in public pinned metadata and HEAD requests; no inference')
    p.add_argument('--dependency-root',type=Path,default=ROOT,help='dependency app root for read-only verification only')
    args = p.parse_args(argv)
    try:
        if args.install:
            if args.dependency_root != ROOT:
                raise ValueError('--dependency-root is read-only, not an install override')
            result = install()
        elif args.check_sources:
            result = {'read_only':True,'sources':check_sources()}
        elif args.verify_assets:
            rows = verify_assets(args.dependency_root)
            result = {'read_only':True,'dependency_root':str(args.dependency_root),'all_verified':all(r['status']=='verified' for r in rows),'assets':rows}
        else:
            result = {'read_only':True,'canonical_root':str(CANONICAL),'checkout':str(ROOT),'python_3_12':sys.version_info[:2]==(3,12),'configured':(ROOT/'.env').is_file(),'install_requires':'fresh /root/voice-lab; Ubuntu 24.04; Python3.12; x86_64; network namespace capability; python3.12-venv'}
        print(json.dumps(result,indent=2))
        return 1 if result.get('all_verified') is False else 0
    except Exception as exc:
        # Do not print arbitrary exceptions/URLs/payloads: no secret leakage.
        print('setup failed: '+str(exc) if isinstance(exc,ValueError) else 'setup failed: '+type(exc).__name__,file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
