"""Persistent single-account authentication. Never log credentials or tokens."""
import os
import hashlib
import hmac
import secrets
import time
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

class AuthStore:
    def __init__(self, data_dir):
        self.directory = Path(data_dir).resolve()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        self.path = self.directory / 'auth.sqlite3'
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY CHECK(id=1), username TEXT, salt BLOB, password BLOB)')
            db.execute('CREATE TABLE IF NOT EXISTS bootstrap (id INTEGER PRIMARY KEY CHECK(id=1), digest BLOB, expires REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS sessions (digest BLOB PRIMARY KEY, expires REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS attempts (id INTEGER PRIMARY KEY, ip TEXT, expires REAL)')
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM account').fetchone() and not db.execute('SELECT 1 FROM bootstrap').fetchone():
                token = secrets.token_urlsafe(32)
                secret_path = self.directory / 'bootstrap-token'
                fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as f:
                    f.write(token + '\n')
                db.execute('INSERT INTO bootstrap VALUES (1,?,?)', (self.digest(token), time.time() + 1800))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def configured(self):
        with self.db() as db:
            return db.execute('SELECT 1 FROM account').fetchone() is not None

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode('utf-8')).digest()

    @staticmethod
    def password_hash(password, salt):
        return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=32768, r=8, p=1, dklen=32, maxmem=64*1024*1024)

    def setup(self, token, username, password):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}', username) or not 12 <= len(password) <= 256 or not password.strip():
            return False
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT digest, expires FROM bootstrap WHERE id=1').fetchone()
            if db.execute('SELECT 1 FROM account').fetchone() or not row or row[1] <= time.time() or not hmac.compare_digest(row[0], self.digest(token)):
                return False
            salt = secrets.token_bytes(16)
            db.execute('INSERT INTO account VALUES (1,?,?,?)', (username, salt, self.password_hash(password, salt)))
            db.execute('DELETE FROM bootstrap')
        (self.directory / 'bootstrap-token').unlink(missing_ok=True)
        return True

    def renew_bootstrap(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM account').fetchone():
                raise ValueError('Account already configured; bootstrap renewal forbidden')
            token = secrets.token_urlsafe(32)
            path = self.directory / 'bootstrap-token'
            temporary = self.directory / ('.bootstrap-' + secrets.token_hex(8))
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, 'w') as f:
                    f.write(token + '\n')
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temporary, path)
                db.execute('INSERT OR REPLACE INTO bootstrap VALUES (1,?,?)', (self.digest(token), time.time()+1800))
            finally:
                temporary.unlink(missing_ok=True)
        return path

    def reserve_attempt(self, ip):
        # Reserve before expensive hashing: concurrent attempts cannot evade limits.
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM attempts WHERE expires<=?', (time.time(),))
            if db.execute('SELECT count(*) FROM attempts').fetchone()[0] >= 100 or db.execute('SELECT count(*) FROM attempts WHERE ip=?', (ip,)).fetchone()[0] >= 5:
                return None
            return db.execute('INSERT INTO attempts(ip,expires) VALUES (?,?)', (ip[:128], time.time()+300)).lastrowid

    def clear_attempt(self, attempt):
        with self.db() as db:
            db.execute('DELETE FROM attempts WHERE id=?', (attempt,))

    def login(self, username, password):
        with self.db() as db:
            row = db.execute('SELECT username, salt, password FROM account WHERE id=1').fetchone()
            salt = row[1] if row else b'fixed-dummy-salt!'
            expected = row[2] if row else b'\0' * 32
            actual = self.password_hash(password, salt)
            password_ok = hmac.compare_digest(actual, expected)
            username_ok = hmac.compare_digest(username.encode(), (row[0] if row else 'missing-account').encode())
            if not row or not (password_ok and username_ok):
                return None
            token = secrets.token_urlsafe(32)
            db.execute('DELETE FROM sessions WHERE expires<=?', (time.time(),))
            # Bound live sessions; a new login may evict the oldest of 16.
            db.execute('DELETE FROM sessions WHERE digest IN (SELECT digest FROM sessions ORDER BY expires DESC LIMIT -1 OFFSET 15)')
            db.execute('INSERT INTO sessions VALUES (?,?)', (self.digest(token), time.time() + 43200))
            return token

    def valid_session(self, token):
        if not isinstance(token, str) or len(token) != 43:
            return False
        with self.db() as db:
            return db.execute('SELECT 1 FROM sessions WHERE digest=? AND expires>?', (self.digest(token), time.time())).fetchone() is not None

    def logout(self, token):
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE digest=?', (self.digest(token),))


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Owner-only bootstrap renewal; never prints the secret.')
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--renew-bootstrap', action='store_true', required=True)
    args = parser.parse_args()
    try:
        path = AuthStore(args.data_dir).renew_bootstrap()
    except ValueError as exc:
        parser.exit(1, str(exc) + '\n')
    print(str(path))


if __name__ == '__main__':
    main()

