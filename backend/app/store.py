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
        self.migrate_projects()

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

    def create_project(self, repository, conn=None):
        if conn is None:
            with self.connect() as c:
                return self.create_project(repository, c)
        for project in self.all('projects', conn):
            if project['repository'].lower() == repository.lower():
                return project, False
        ident = uid()
        project = dict(id=ident, repository=repository, name=repository.split('/')[-1],
                       status='pending', initialization_task_id=None, initialized_at=None,
                       created_at=now(), updated_at=now(), last_error=None, context=None)
        self.put('projects', ident, project, conn)
        return project, True

    def migrate_projects(self):
        # Idempotent, local-only migration; never enqueue work or change task snapshots.
        with self.connect() as c:
            for task in self.all('tasks', c):
                if task.get('kind') == 'analysis' and task.get('repository') and not task.get('project_id'):
                    project, _ = self.create_project(task['repository'], c)
                    task['project_id'] = project['id']
                    self.put('tasks', task['id'], task, c)

    def project_prompt_context(self, project, conn):
        saved = self.unseal(project['context']) if project.get('context') else {}
        snapshot = saved.get('snapshot') or {}
        # Bounded immutable context. Full captured data remains available on the project.
        context = {'repository': project['repository'], 'captured_at': snapshot.get('captured_at'),
                   'summary': saved.get('summary'), 'metadata': snapshot.get('metadata'),
                   'limitations': snapshot.get('limitations', []), 'history': []}
        files = ([snapshot['readme']] if snapshot.get('readme') else []) + snapshot.get('files', [])
        context['directory'] = [{k:item.get(k) for k in ('path','type','html_url')} for item in snapshot.get('directory', [])[:200]]
        if len(snapshot.get('directory', [])) > 200:
            context['limitations'] = [*context['limitations'], '模型上下文仅含前200项根目录。']
        context['files'] = [{**{k:f.get(k) for k in ('path','html_url','sha')}, 'text':str(f.get('text',''))[:3000]} for f in files[:6]]
        for group in ('issues','pull_requests'):
            data = snapshot.get(group) or {}
            context[group] = {'coverage': data.get('coverage'), 'items': [
                {**{k:item.get(k) for k in ('number','title','state','html_url')}, 'body':str(item.get('body') or '')[:400]}
                for item in data.get('items', [])[:15]]}
        for task in self.all('tasks', conn):
            if (task.get('project_id') == project['id'] and task.get('kind') == 'analysis'
                    and task.get('result') and task['status'] in ('completed','waiting_confirmation')):
                context['history'].append({'prompt':task['prompt'][:1000], 'report':task['result']['report'][:4000],
                                           'created_at':task['created_at'], 'status':task['status']})
                if len(context['history']) == 6: break
        context['history'].reverse()
        return context

    def enqueue(self, kind, prompt='', request_id=None, payload=None, project_id=None, conn=None):
        if conn is None:
            with self.connect() as c:
                return self.enqueue(kind, prompt, request_id, payload, project_id, c)
        c = conn
        settings = self.settings(c)
        project = self.get('projects', project_id, c) if project_id else None
        if project_id and not project:
            raise ValueError('Project does not exist')
        if not project and kind == 'analysis' and settings['github']['repository']:
            project, _ = self.create_project(settings['github']['repository'], c)
            project_id = project['id']
        request_key = f'{project_id}:{request_id}' if project_id and request_id else request_id
        if request_key:
            existing = self.get('requests', request_key, c)
            if existing:
                return self.get('tasks', existing['id'], c)
        if project:
            settings['github']['repository'] = project['repository']
            settings['github']['repository_url'] = 'https://github.com/' + project['repository']
            if kind == 'analysis':
                settings['project_context'] = self.project_prompt_context(project, c)
        ident = uid()
        task = dict(id=ident, kind=kind, prompt=prompt, project_id=project_id, status='queued',
                    repository=settings['github']['repository'], created_at=now(), updated_at=now(),
                    error=None, result=None, snapshot=self.seal(settings), payload=payload or {}, deadline=time.time()+3600)
        self.put('tasks', ident, task, c)
        if request_key:
            self.put('requests', request_key, {'id': ident}, c)
        self.event(ident, 'queued', '任务已加入后台队列', c)
        return task

    def sync_project_task(self, task, conn):
        if task.get('kind') != 'initialization': return
        project = self.get('projects', task.get('project_id'), conn)
        if not project or project.get('initialization_task_id') != task['id']: return
        state = task['status']
        if state == 'completed': return
        project.update(status={'queued':'pending','running':'initializing'}.get(state,'failed'),
                       last_error=task.get('error') or ('初始化已取消' if state == 'cancelled' else None),
                       updated_at=now())
        self.put('projects', project['id'], project, conn)

    def initialize_project(self, project_id, conn=None):
        if conn is None:
            with self.connect() as c:
                return self.initialize_project(project_id, c)
        project = self.get('projects', project_id, conn)
        if not project: raise ValueError('Project does not exist')
        prior = self.get('tasks', project.get('initialization_task_id'), conn)
        if prior and prior['status'] in ('queued','running'):
            return prior
        if prior and prior['status'] in ('waiting_network','waiting_rate_limit'):
            prior.update(status='cancelled', updated_at=now())
            self.put('tasks', prior['id'], prior, conn)
        task = self.enqueue('initialization', '初始化仓库：阅读关键内容、Issue 和 PR，建立项目资料与管理概览。', project_id=project_id, conn=conn)
        project.update(status='pending', initialization_task_id=task['id'], updated_at=now(), last_error=None)
        self.put('projects', project_id, project, conn)
        return task
