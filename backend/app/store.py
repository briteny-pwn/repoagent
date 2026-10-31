"""Small transactional store and durable job queue for one-server deployment."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from cryptography.fernet import Fernet


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


DEFAULTS = {
    'model': {'base_url': '', 'model': '', 'api_key': '', 'max_steps': 8},
    'github': {'token': '', 'repository_url': '', 'repository': None},
    'network': {'mode': 'direct', 'proxy_url': ''},
    'subscription': {'url': '', 'nodes': [], 'selected_node': None, 'updated_at': None, 'status': '未配置'},
    'generations': {'model': 0, 'github': 0, 'proxy': 0, 'subscription': 0},
}


class Store:
    def __init__(self, config):
        Path(config.data_dir).mkdir(parents=True, exist_ok=True)
        self.path = str(Path(config.data_dir) / 'steward.db')
        self.cipher = Fernet(config.encryption_key.encode())
        with sqlite3.connect(self.path) as db:
            db.execute('PRAGMA journal_mode=WAL')
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS records (bucket TEXT, id TEXT, value TEXT NOT NULL, PRIMARY KEY(bucket,id))')
            c.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, kind TEXT, message TEXT, created_at TEXT)')
        if not self.get('config', 'settings'):
            self.put('config', 'settings', {'encrypted': self.seal(DEFAULTS)})

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            c.execute('BEGIN IMMEDIATE')
            yield c
            c.execute('COMMIT')
        except Exception:
            c.execute('ROLLBACK')
            raise
        finally:
            c.close()

    def seal(self, value):
        return self.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()

    def unseal(self, value):
        return json.loads(self.cipher.decrypt(value.encode()))

    def get(self, bucket, ident, conn=None):
        if conn is None:
            with self.connect() as c:
                return self.get(bucket, ident, c)
        row = conn.execute('SELECT value FROM records WHERE bucket=? AND id=?', (bucket, ident)).fetchone()
        return json.loads(row['value']) if row else None

    def put(self, bucket, ident, value, conn=None):
        if conn is None:
            with self.connect() as c:
                return self.put(bucket, ident, value, c)
        conn.execute('INSERT INTO records VALUES (?,?,?) ON CONFLICT(bucket,id) DO UPDATE SET value=excluded.value', (bucket, ident, json.dumps(value, ensure_ascii=False)))

    def delete(self, bucket, ident, conn=None):
        if conn is None:
            with self.connect() as c:
                return self.delete(bucket, ident, c)
        conn.execute('DELETE FROM records WHERE bucket=? AND id=?', (bucket, ident))

    def all(self, bucket, conn=None):
        if conn is None:
            with self.connect() as c:
                return self.all(bucket, c)
        return [json.loads(r[0]) for r in conn.execute('SELECT value FROM records WHERE bucket=? ORDER BY rowid DESC', (bucket,))]

    def settings(self, conn=None):
        return self.unseal(self.get('config', 'settings', conn)['encrypted'])

    def save_settings(self, settings, conn=None):
        self.put('config', 'settings', {'encrypted': self.seal(settings)}, conn)

    def event(self, task_id, kind, message, conn=None):
        if conn is None:
            with self.connect() as c:
                return self.event(task_id, kind, message, c)
        conn.execute('INSERT INTO events(task_id,kind,message,created_at) VALUES (?,?,?,?)', (task_id, kind, message, now()))

    def events(self, task_id, after=0):
        with self.connect() as c:
            return [dict(r) for r in c.execute('SELECT id,kind,message,created_at FROM events WHERE task_id=? AND id>? ORDER BY id LIMIT 500', (task_id, after))]

    def audit(self, action, target, conn=None):
        ident = uid()
        self.put('audit', ident, {'id': ident, 'action': action, 'target': target, 'created_at': now()}, conn)

    def worker_alive(self):
        h = self.get('runtime', 'worker') or {}
        return time.time() - h.get('heartbeat', 0) < 30

    def enqueue(self, kind, prompt='', request_id=None, payload=None):
        with self.connect() as c:
            if request_id:
                existing = self.get('requests', request_id, c)
                if existing:
                    return self.get('tasks', existing['id'], c)
            settings = self.settings(c)
            ident = uid()
            task = dict(id=ident, kind=kind, prompt=prompt, status='queued', repository=settings['github']['repository'], created_at=now(), updated_at=now(), error=None, result=None, snapshot=self.seal(settings), payload=payload or {}, deadline=time.time()+3600)
            self.put('tasks', ident, task, c)
            if request_id:
                self.put('requests', request_id, {'id': ident}, c)
            self.event(ident, 'queued', '任务已加入后台队列', c)
            return task
