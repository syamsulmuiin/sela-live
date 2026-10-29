"""Authentication regression tests; all secrets are ephemeral fixtures."""
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from test_server import Provider, Speech

ORIGIN = 'https://voice.kasirdigital.web.id'

class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def app(self):
        from voice_app.server import create_app
        return create_app(provider=Provider(), speech=Speech(), data_dir=Path(self.tmp.name), public_origin=ORIGIN)

    def bootstrap(self, c):
        token = (Path(self.tmp.name) / 'bootstrap-token').read_text().strip()
        body = {'token':token, 'username':'owner', 'password':'ephemeral fixture passphrase'}
        self.assertEqual(c.post('/api/setup', json=body, headers={'origin':ORIGIN}).status_code, 201)
        return {k:v for k,v in body.items() if k != 'token'}

    def test_failed_attempt_global_bound_and_expiry_are_persistent(self):
        from voice_app.auth import AuthStore
        import sqlite3
        from contextlib import closing
        auth = AuthStore(self.tmp.name)
        for index in range(100):
            self.assertIsNotNone(auth.reserve_attempt('fixture-' + str(index)))
        self.assertIsNone(auth.reserve_attempt('new-source'))
        self.assertIsNone(AuthStore(self.tmp.name).reserve_attempt('another-source'))
        with closing(sqlite3.connect(auth.path)) as db, db:
            self.assertEqual(db.execute('SELECT count(*) FROM attempts').fetchone()[0], 100)
            db.execute('UPDATE attempts SET expires=0')
        self.assertIsNotNone(auth.reserve_attempt('new-source'))

    def test_owner_cli_renews_expired_bootstrap_but_never_overwrites_account(self):
        self.app()
        path = Path(self.tmp.name) / 'bootstrap-token'
        old = path.read_text().strip()
        import subprocess, sys
        command = [sys.executable, '-m', 'voice_app.auth', '--renew-bootstrap', '--data-dir', self.tmp.name]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        new = path.read_text().strip()
        self.assertTrue(old != new, 'owner renewal must rotate the secret')
        self.assertNotIn(old, result.stdout + result.stderr)
        self.assertNotIn(new, result.stdout + result.stderr)
        with TestClient(self.app(), base_url=ORIGIN) as c:
            body = {'token':old,'username':'owner','password':'ephemeral fixture passphrase'}
            self.assertEqual(c.post('/api/setup', json=body, headers={'origin':ORIGIN}).status_code, 403)
            self.bootstrap(c)
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(path.exists())

    def test_auth_pages_and_headers_are_safe_and_static_secrets_unreachable(self):
        with TestClient(self.app(), base_url=ORIGIN) as c:
            for path in ('/login','/setup'):
                response = c.get(path)
                self.assertIn('id="auth-form"', response.text)
                self.assertIn('src="/auth.js"', response.text)
                self.assertNotIn('ephemeral', response.text)
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertEqual(response.headers['referrer-policy'], 'no-referrer')
                self.assertIn("frame-ancestors 'none'", response.headers['content-security-policy'])
            body = self.bootstrap(c)
            self.assertEqual(c.post('/api/login', json=body, headers={'origin':ORIGIN}).status_code, 200)
            for path in ('/data/auth.sqlite3','/.env','/../data/bootstrap-token'):
                self.assertEqual(c.get(path).status_code, 404)
            for path in ('/api/login','/api/logout'):
                for origin in ('https://evil.example', ''):
                    self.assertEqual(c.post(path, json=body, headers={'origin':origin}).status_code, 403)
            self.assertIn(c.get('/api/logout').status_code, (404, 405))
            self.assertEqual(c.get('/api/config').status_code, 200)
            self.assertEqual(c.get('/health', headers={'host':'evil.example','x-forwarded-host':'voice.kasirdigital.web.id'}).status_code, 400)
            self.assertEqual(c.post('/api/login', json=body, headers={'origin':'http://voice.kasirdigital.web.id','x-forwarded-proto':'https'}).status_code, 403)

    def test_websocket_requires_auth_origin_and_rechecks_after_logout(self):
        from starlette.websockets import WebSocketDisconnect
        app = self.app()
        with TestClient(app, base_url=ORIGIN) as c:
            for origin in (ORIGIN, 'https://evil.example'):
                with self.assertRaises(WebSocketDisconnect):
                    with c.websocket_connect('wss://voice.kasirdigital.web.id/ws', headers={'host':'voice.kasirdigital.web.id','origin':origin}):
                        pass
            body = self.bootstrap(c)
            self.assertEqual(c.post('/api/login', json=body, headers={'origin':ORIGIN}).status_code, 200)
            for origin in ('https://evil.example', 'http://voice.kasirdigital.web.id', ''):
                with self.assertRaises(WebSocketDisconnect):
                    with c.websocket_connect('wss://voice.kasirdigital.web.id/ws', headers={'host':'voice.kasirdigital.web.id','origin':origin}):
                        pass
            with c.websocket_connect('wss://voice.kasirdigital.web.id/ws', headers={'host':'voice.kasirdigital.web.id','origin':ORIGIN}) as ws:
                self.assertEqual(ws.receive_json()['type'], 'state')
                self.assertEqual(c.post('/api/logout', headers={'origin':ORIGIN}).status_code, 200)
                ws.send_json({'type':'text','text':'Must never reach provider'})
                with self.assertRaises(WebSocketDisconnect):
                    ws.receive_json()

    def test_setup_validation_expiry_csrf_and_failed_attempt_throttle(self):
        app = self.app()
        with TestClient(app, base_url=ORIGIN) as c:
            token = (Path(self.tmp.name) / 'bootstrap-token').read_text().strip()
            body = {'token':token,'username':'owner','password':'short'}
            self.assertEqual(c.post('/api/setup', json=body, headers={'origin':ORIGIN}).status_code, 403)
            body['password'] = 'ephemeral fixture passphrase'
            for origin in ('https://evil.example', 'http://voice.kasirdigital.web.id', ''):
                self.assertEqual(c.post('/api/setup', json=body, headers={'origin':origin}).status_code, 403)
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(Path(self.tmp.name) / 'auth.sqlite3')) as db, db:
                db.execute('UPDATE bootstrap SET expires=0')
            self.assertEqual(c.post('/api/setup', json=body, headers={'origin':ORIGIN}).status_code, 403)
            for _ in range(3):
                self.assertEqual(c.post('/api/login', json={}, headers={'origin':ORIGIN}).status_code, 401)
            self.assertEqual(c.post('/api/login', json={}, headers={'origin':ORIGIN}).status_code, 429)
            self.assertEqual(c.post('/api/setup', json=body, headers={'origin':ORIGIN}).status_code, 429)

    def test_login_cookie_expiry_logout_and_hash_at_rest(self):
        app = self.app()
        with TestClient(app, base_url=ORIGIN) as c:
            body = self.bootstrap(c)
            for bad in ({**body,'password':'incorrect'}, {**body,'username':'missing'}):
                self.assertEqual(c.post('/api/login', json=bad, headers={'origin':ORIGIN}).status_code, 401)
            response = c.post('/api/login', json=body, headers={'origin':ORIGIN})
            self.assertEqual(response.status_code, 200)
            cookie = response.headers['set-cookie'].lower()
            for flag in ('httponly', 'secure', 'samesite=strict', 'path=/'):
                self.assertIn(flag, cookie)
            token = c.cookies.get('sela_session')
            self.assertEqual(c.get('/api/config').status_code, 200)
            self.assertEqual(c.get('/').status_code, 200)
            self.assertNotIn(token.encode(), (Path(self.tmp.name) / 'auth.sqlite3').read_bytes())
            self.assertEqual(c.post('/api/logout', headers={'origin':ORIGIN}).status_code, 200)
            c.cookies.set('sela_session', token)
            self.assertEqual(c.get('/api/config').status_code, 401)
            c.cookies.clear()
            self.assertEqual(c.post('/api/login', json=body, headers={'origin':ORIGIN}).status_code, 200)
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(Path(self.tmp.name) / 'auth.sqlite3')) as db, db:
                db.execute('UPDATE sessions SET expires=0')
            self.assertEqual(c.get('/api/config').status_code, 401)

    def test_setup_is_secret_gated_once_only_and_persistent(self):
        app = self.app()
        path = Path(self.tmp.name) / 'bootstrap-token'
        self.assertTrue(path.is_file(), 'bootstrap secret must exist')
        token = path.read_text().strip()
        self.assertGreaterEqual(len(token), 43)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with TestClient(app, base_url=ORIGIN) as c:
            body = {'token':'invalid', 'username':'owner', 'password':'ephemeral fixture passphrase'}
            self.assertEqual(c.post('/api/setup', json=body, headers={'origin':ORIGIN}).status_code, 403)
            body['token'] = token
            self.assertEqual(c.post('/api/setup', json=body, headers={'origin':ORIGIN}).status_code, 201)
            self.assertEqual(c.post('/api/setup', json=body, headers={'origin':ORIGIN}).status_code, 403)
            self.assertEqual(c.get('/', follow_redirects=False).headers['location'], '/login')
        self.assertFalse(path.exists())
        with TestClient(self.app(), base_url=ORIGIN) as c:
            self.assertEqual(c.get('/', follow_redirects=False).headers['location'], '/login')
        self.assertFalse(path.exists(), 'restart must not recreate bootstrap after setup')
        db = Path(self.tmp.name) / 'auth.sqlite3'
        self.assertEqual(db.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(body['password'].encode(), db.read_bytes())
        self.assertNotIn(token.encode(), db.read_bytes())

    def test_unconfigured_public_app_never_exposes_config_or_ui(self):
        with TestClient(self.app(), base_url=ORIGIN) as c:
            self.assertEqual(c.get('/', follow_redirects=False).headers['location'], '/setup')
            self.assertEqual(c.get('/api/config').status_code, 401)
            self.assertEqual(c.get('/app.js', follow_redirects=False).status_code, 303)
            self.assertEqual(c.get('/setup').status_code, 200)
            self.assertEqual(c.get('/health').json(), {'status':'ok'})
            self.assertEqual(c.get('/.env', follow_redirects=False).status_code, 303)
