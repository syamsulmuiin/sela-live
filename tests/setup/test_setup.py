import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

class SetupTests(unittest.TestCase):
    def fixture_checkout(self, root):
        import shutil
        paths = ('autonomous/expressive-tts/assets-manifest.json', 'autonomous/expressive-tts/wheels-manifest.json', 'autonomous/zipformer/artifact-verification.json', 'config/web-wheels.json', 'voice_app/supertonic3_worker.py', 'voice_app/automatic_conditioning.py', 'voice_app/approved_conditioning.py')
        for name in paths:
            target=root/name
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/name,target)
        (root/'scripts').mkdir()
        for source in (ROOT/'scripts').glob('*.py'):
            shutil.copyfile(source,root/'scripts'/source.name)

    def test_env_is_data_not_shell(self):
        self.assertIsNotNone(importlib.util.find_spec('setup_common'), 'missing setup utility')
        import setup_common as s
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / '.env'
            secret = 'a $HOME $(touch NEVER) ; # " secret'
            s.write_env(p, {'VOICE_API_KEY': secret, 'VOICE_AI_BACKEND': 'api'})
            self.assertEqual(s.load_env(p)['VOICE_API_KEY'], secret)
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            self.assertFalse((Path(td) / 'NEVER').exists())
            p.write_text('export VOICE_API_KEY=bad\n')
            with self.assertRaises(ValueError):
                s.load_env(p)

    def test_paths_and_corruption_fail_closed(self):
        import setup_common as s
        self.assertTrue(hasattr(s, 'verify_file'), 'missing checksum verifier')
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root/'x'
            p.write_bytes(b'hello')
            with self.assertRaises(ValueError):
                s.verify_file(p, {'sha256':'0'*64, 'bytes':5})
            for path in ('../escape', '/etc/passwd', 'x/../../escape'):
                with self.assertRaises(ValueError):
                    s.safe_path(root, path)
            (root/'link').symlink_to(root)
            with self.assertRaises(ValueError):
                s.safe_path(root, 'link/x')

    def test_bootstrap_default_is_readonly_and_install_refuses_snapshot(self):
        p = ROOT/'scripts/bootstrap.py'
        self.assertTrue(p.is_file(), 'missing bootstrap')
        before = {str(p.relative_to(ROOT)) for p in ROOT.rglob('*')}
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, str(p)], capture_output=True, text=True, env=env)
        self.assertIn('read_only', result.stdout)
        self.assertEqual(before, {str(p.relative_to(ROOT)) for p in ROOT.rglob('*')})
        with tempfile.TemporaryDirectory() as td:
            self.fixture_checkout(Path(td))
            result = subprocess.run([sys.executable, str(Path(td)/'scripts/bootstrap.py'), '--install'], capture_output=True, text=True, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('canonical', result.stderr)

    def test_source_pins_and_plan(self):
        self.assertIsNotNone(importlib.util.find_spec('bootstrap'), 'missing bootstrap')
        import bootstrap as b
        plan = b.asset_plan(ROOT)
        self.assertGreater(len(plan), 15)
        for row in plan:
            self.assertRegex(row['sha256'], r'^[0-9a-f]{64}$')
            self.assertNotIn('/main/', row['url'])
            self.assertNotIn('llama', row['url'])
            self.assertFalse(Path(row['path']).is_absolute())
        tts = [p for p in plan if '/assets/' in p['path']]
        self.assertTrue(tts)
        for row in tts:
            self.assertEqual(row.get('original_revision'), '3cadd1ee6394adea1bd021217a0e650ede09a323')
            self.assertIn('aafc6e32416a594460b32413efc49d7fe4ce6d46', row['url'])
            self.assertIn('supertone-oss-archive', row['url'])

    def test_configuration_defaults_and_secret_isolation(self):
        self.assertIsNotNone(importlib.util.find_spec('configure'), 'missing configure')
        import configure as c
        import setup_common as s
        with tempfile.TemporaryDirectory() as td:
            env = c.make_config('api', 'remote-model', 'https://api.example.test/v1', '', Path(td), {'VOICE_API_KEY':'hidden-value'})
            self.assertEqual(env['VOICE_TTS_BACKEND'], 'supertonic3')
            self.assertEqual(env['VOICE_LOCAL_ASR_ENGINE'], 'zipformer_id_int8')
            self.assertEqual(env['VOICE_AUTOMATIC_CONDITIONING'], '1')
            self.assertEqual(env['VOICE_EXPERIMENTAL_DELIVERY_PROSODY'], '0')
            self.assertEqual(env['VOICE_SUPERTONIC3_STYLE_PROFILE'], 'conditioning_b')
            self.assertEqual(env['VOICE_API_KEY'], 'hidden-value')
            self.assertEqual(env['VOICE_PUBLIC_ORIGIN'], '')
            with self.assertRaises(ValueError):
                c.make_config('api','m','https://key:secret@example.test/v1','',Path(td),{})
        result = subprocess.run([sys.executable,str(ROOT/'scripts/configure.py'),'--backend','api','--api-key','hidden-value'],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('hidden-value',result.stdout+result.stderr)

    def test_launcher_backend_selects_python_and_filters_environment(self):
        self.assertIsNotNone(importlib.util.find_spec('run'), 'missing launcher')
        import run as r
        config = {'VOICE_AI_BACKEND':'api','VOICE_MODEL':'m','VOICE_API_BASE_URL':'https://api.example.test/v1','VOICE_API_KEY':'private','SELA_WEB_PYTHON':sys.executable}
        command, env = r.build_launch(config, ROOT, {'PATH':'/usr/bin:/bin', 'HOME':'/root','GITHUB_TOKEN':'not-for-app','VOICE_AI_BACKEND':'hermes','VOICE_DRAFT_MODEL_PATH':'bad'})
        self.assertEqual(command[0],sys.executable)
        self.assertEqual(command[-4:], ['--host','127.0.0.1','--port','8091'])
        self.assertEqual(env['VOICE_AI_BACKEND'],'api')
        self.assertNotIn('GITHUB_TOKEN',env)
        self.assertNotIn('VOICE_DRAFT_MODEL_PATH',env)
        config['VOICE_AI_BACKEND']='hermes'
        with self.assertRaises(ValueError):
            r.build_launch(config, ROOT, {})
        config['SELA_HERMES_PYTHON']=sys.executable
        self.assertEqual(r.build_launch(config,ROOT,{})[0][0],sys.executable)
        for backend in ('api','hermes'):
            config['VOICE_AI_BACKEND']=backend
            command,_=r.build_launch(config,ROOT,{})
            self.assertEqual(command[command.index('--ws-max-size')+1],'860000')
            self.assertEqual(command[command.index('--limit-concurrency')+1],'32')
            for flag in ('--no-access-log','--factory','--no-proxy-headers'):
                self.assertEqual(command.count(flag),1)

    def test_effective_api_url_private_env_rejected_without_echo(self):
        import run as r
        import configure as c
        import setup_common as s
        import contextlib, io
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            config=c.make_config('api','arbitrary-model','https://api.example.test/v1','',root,{'VOICE_API_KEY':'synthetic-secret'})
            config['SELA_WEB_PYTHON']=sys.executable
            invalid=('not-a-url','ftp://example.test','https:///v1','https://user:synthetic-secret@example.test/v1','https://example.test/v1?synthetic-secret','https://example.test/#synthetic-secret','https://[broken')
            for url in invalid:
                with self.subTest(url_kind=invalid.index(url)):
                    config['VOICE_API_BASE_URL']=url
                    (root/'.env').unlink(missing_ok=True)
                    s.write_env(root/'.env',config)
                    loaded=s.load_env(root/'.env')
                    with self.assertRaises(ValueError) as caught:
                        r.build_launch(loaded,root,{})
                    self.assertNotIn('synthetic-secret',str(caught.exception))
                    stdout,stderr=io.StringIO(),io.StringIO()
                    with patch.object(r,'ROOT',root),patch.object(r,'CANONICAL',root),contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
                        self.assertEqual(r.main(['--check']),1)
                    self.assertNotIn('synthetic-secret',stdout.getvalue()+stderr.getvalue())
                    self.assertNotIn(url,stdout.getvalue()+stderr.getvalue())
                    (root/'.env').unlink()
            for url in ('http://localhost:9000/v1','https://custom.example.test/custom'):
                config['VOICE_API_BASE_URL']=url
                self.assertEqual(r.build_launch(config,root,{})[1]['VOICE_API_BASE_URL'],url)

    def test_doctor_effective_speech_selection_and_dependency_separation(self):
        import doctor as d, bootstrap as b, configure as c
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            config=c.make_config('api','custom-model','https://api.example.test/v1','',root,{'VOICE_API_KEY':'synthetic'})
            config.update(SELA_WEB_PYTHON=sys.executable,VOICE_PUBLIC_ORIGIN='https://custom.example.test')
            def verified(where):
                return [{'status':'verified' if where==root else 'missing_or_corrupt'}]
            with patch.object(d,'ROOT',root),patch.object(d,'CANONICAL',root),patch.object(d,'namespace_available',return_value=True),patch.object(d,'helper_pins_valid',return_value=True),patch.object(d,'module_probe',return_value=True),patch.object(b,'verify_assets',side_effect=verified):
                self.assertTrue(d.inspect_runtime(config,root)['ready'])
                # Every packaged selector/path must be explicit and exact, not just the defaults on disk.
                keys=[k for k in config if k not in ('VOICE_AI_BACKEND','VOICE_MODEL','VOICE_DATA_DIR','VOICE_PUBLIC_ORIGIN','VOICE_API_BASE_URL','VOICE_API_KEY','SELA_WEB_PYTHON')]
                for key in keys:
                    for value in ('/does-not-exist/fixture' if 'PATH' in key or 'MANIFEST' in key or 'PYTHON' in key else 'unsupported',None):
                        bad=dict(config)
                        if value is None: bad.pop(key)
                        else: bad[key]=value
                        with self.subTest(key=key,value_missing=value is None):
                            report=d.inspect_runtime(bad,root)
                            self.assertFalse(report['ready'])
                            self.assertFalse(report['checks']['packaged_speech_configuration'])
                            self.assertFalse(report['checks']['speech_asset_sha256'])
                # A verified alternative root never substitutes for missing launch-root assets.
                with patch.object(b,'verify_assets',side_effect=lambda where:[{'status':'verified' if where!=root else 'missing_or_corrupt'}]):
                    report=d.inspect_runtime(config,root/'alternative')
                    self.assertTrue(report['dependency_verification']['all_verified'])
                    self.assertFalse(report['checks']['speech_asset_sha256'])
                    self.assertFalse(report['ready'])
                (root/'autonomous').symlink_to(root/'elsewhere')
                self.assertFalse(d.inspect_runtime(config,root)['ready'])

    def test_doctor_sanitizes_both_env_branches(self):
        import json
        import setup_common as s
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            self.fixture_checkout(root)
            link=root/'linked-dependencies'
            link.symlink_to(root)
            for configured in (False,True):
                if configured: s.write_env(root/'.env',{'VOICE_AI_BACKEND':'api'})
                for corrupt in (False,True):
                    manifest=root/'autonomous/expressive-tts/assets-manifest.json'
                    if corrupt: manifest.write_text('{synthetic-sensitive-manifest')
                    result=subprocess.run([sys.executable,'-B',str(root/'scripts/doctor.py'),'--dependency-root',str(root if corrupt else link)],capture_output=True,text=True)
                    with self.subTest(configured=configured,corrupt=corrupt):
                        self.assertEqual(result.returncode,1)
                        self.assertFalse(json.loads(result.stdout)['ready'])
                        self.assertEqual(result.stderr,'')
                        self.assertNotIn('synthetic-sensitive',result.stdout)
                        self.assertNotIn('Traceback',result.stdout)
                    if corrupt:
                        import shutil
                        shutil.copyfile(ROOT/'autonomous/expressive-tts/assets-manifest.json',manifest)

    def test_doctor_auth_uses_selected_interpreter_and_presence_is_labeled(self):
        import doctor as d, bootstrap as b, configure as c
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            config=c.make_config('api','m','https://api.example.test/v1','',root,{'VOICE_API_KEY':'synthetic'})
            selected=root/'selected-python'
            selected.symlink_to(sys.executable)
            config['SELA_WEB_PYTHON']=str(selected)
            for backend in ('api','hermes'):
                config.update(VOICE_AI_BACKEND=backend,SELA_HERMES_PYTHON=str(selected))
                with patch.object(d,'ROOT',root),patch.object(d,'namespace_available',return_value=True),patch.object(d,'helper_pins_valid',return_value=True),patch.object(b,'verify_assets',return_value=[{'status':'verified'}]),patch.object(d,'module_probe',return_value=True),patch.object(d.subprocess,'run',return_value=subprocess.CompletedProcess([],0)) as probe:
                    report=d.inspect_runtime(config,root)
                    calls=[call for call in probe.call_args_list if 'sqlite3' in str(call)]
                    self.assertEqual(len(calls),1)
                    self.assertEqual(calls[0].args[0][0],str(selected))
                    self.assertIn('import sqlite3',calls[0].args[0][-1])
                    self.assertIn('module_presence_only',report)
                    self.assertTrue(report['module_presence_only'])
                    probe.return_value=subprocess.CompletedProcess([],1)
                    self.assertFalse(d.inspect_runtime(config,root)['checks']['auth_capability'])
                config['SELA_WEB_PYTHON']=''
                config['SELA_HERMES_PYTHON']=''
                with patch.object(d,'ROOT',root),patch.object(d,'namespace_available',return_value=True),patch.object(d,'helper_pins_valid',return_value=True),patch.object(b,'verify_assets',return_value=[]),patch.object(d,'module_probe',return_value=True):
                    self.assertFalse(d.inspect_runtime(config,root)['checks']['auth_capability'])
                config['SELA_WEB_PYTHON']=str(selected)

    def test_helper_source_pins_reject_tampering_without_import(self):
        import doctor as d
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            self.fixture_checkout(root)
            self.assertTrue(d.helper_pins_valid(root))
            target=root/'voice_app/approved_conditioning.py'
            target.write_text(target.read_text()+'\n# tampered fixture\n')
            self.assertFalse(d.helper_pins_valid(root))

    def test_doctor_never_imports_application_or_calls_provider(self):
        p = ROOT/'scripts/doctor.py'
        self.assertTrue(p.is_file(),'missing doctor')
        with tempfile.TemporaryDirectory() as td:
            self.fixture_checkout(Path(td))
            result = subprocess.run([sys.executable,str(Path(td)/'scripts/doctor.py')],capture_output=True,text=True,env={'PATH':os.environ['PATH'],'PYTHONDONTWRITEBYTECODE':'1'})
            self.assertNotEqual(result.returncode,0)
            self.assertIn('"provider_called": false',result.stdout)
            self.assertIn('"application_imported": false',result.stdout)
            self.assertFalse((Path(td)/'data').exists())

    def test_download_corrupt_sha_leaves_no_installed_artifact(self):
        import bootstrap as b
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source=root/'source.whl'
            source.write_bytes(b'corrupt fixture, never an executable wheel')
            row={'path':'cache/x.whl','url':source.as_uri(),'sha256':'0'*64,'bytes':source.stat().st_size}
            with self.assertRaises(ValueError):
                b.download(row,root)
            self.assertFalse((root/'cache/x.whl').exists())
            self.assertFalse((root/'cache/x.whl.partial').exists())

    def test_production_and_symlink_scope_guards(self):
        import bootstrap as b
        with tempfile.TemporaryDirectory() as td:
            from unittest.mock import patch
            root=Path(td)
            (root/'data').mkdir()
            with patch.object(b,'CANONICAL',root), self.assertRaises(ValueError):
                b.assert_fresh(root)
            (root/'data').rmdir()
            (root/'autonomous').symlink_to('/root/voice-lab/autonomous')
            with patch.object(b,'CANONICAL',root), self.assertRaises(ValueError):
                b.assert_fresh(root)

    def test_manifest_traversal_and_modified_revision_are_rejected(self):
        import bootstrap as b
        import json
        import shutil
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            self.fixture_checkout(root)
            p=root/'autonomous/expressive-tts/assets-manifest.json'
            original=json.loads(p.read_text())
            changed=dict(original,revision='0'*40)
            p.write_text(json.dumps(changed))
            with self.assertRaises(ValueError): b.asset_plan(root)
            original['files'][0]['path']='../../../../escape'
            p.write_text(json.dumps(original))
            with self.assertRaises(ValueError): b.asset_plan(root)

    def test_service_is_manual_loopback_without_external_origin(self):
        p=ROOT/'deploy/sela-voice.service'
        self.assertTrue(p.is_file(),'missing service template')
        text=p.read_text()
        self.assertIn('scripts/run.py',text)
        self.assertNotIn('EnvironmentFile=',text)
        self.assertNotIn('0.0.0.0',text)
        self.assertNotIn('cloudflare',text.lower())
        self.assertIn('User=root',text)
        self.assertIn('PrivateTmp=yes',text)
        self.assertIn('ProtectSystem=full',text)

    def test_offline_venv_installer_is_available_and_blocks_source_archives(self):
        import bootstrap as b
        self.assertTrue(hasattr(b,'install_venv'), 'missing isolated binary wheel installer')
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                b.install_venv(Path(td),'deploy','web-venv',[{'path':'deploy/wheels/payload.tar.gz','sha256':'0'*64}])
            self.assertFalse((Path(td)/'deploy/web-venv').exists())

    def test_secret_prompt_requires_hidden_tty_and_never_echoes(self):
        import configure as c
        self.assertTrue(hasattr(c,'api_key'), 'missing safe hidden prompt')
        from unittest.mock import patch
        with patch.object(c.sys.stdin,'isatty',return_value=False):
            self.assertEqual(c.api_key({'VOICE_API_KEY':'from-environment'}),'from-environment')
            with self.assertRaises(ValueError): c.api_key({})

    def test_env_rejects_executable_duplicate_and_existing_files(self):
        import setup_common as s
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'.env'
            s.write_env(p,{'VOICE_AI_BACKEND':'api'})
            with self.assertRaises(FileExistsError): s.write_env(p,{'VOICE_AI_BACKEND':'hermes'})
            p.chmod(0o700)
            with self.assertRaises(ValueError): s.load_env(p)
            p.chmod(0o600)
            p.write_text('VOICE_AI_BACKEND="api"\nVOICE_AI_BACKEND="hermes"\n')
            with self.assertRaises(ValueError): s.load_env(p)

if __name__ == '__main__':
    unittest.main()
