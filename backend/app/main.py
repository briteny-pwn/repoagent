"""Authenticated HTTP API. Workers alone perform durable external mutations."""
import asyncio
import hmac
import json
import secrets
import time
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from .config import Config
from .store import Store, now, uid
from .security import password_hash, token_hash, check_https, validate_proxy, proxy_for
from .integrations import IntegrationError, parse_repository, GitHubClient, ModelClient


class Login(BaseModel):
    password: str = Field(max_length=1024)


class TaskInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    request_id: str = Field(min_length=1, max_length=128)


class DraftInput(BaseModel):
    version: int = Field(ge=0)
    title: str | None = Field(default=None, max_length=256)
    body: str | None = Field(default=None, max_length=30000)


def public_task(task):
    return {k: v for k, v in task.items() if k not in ('snapshot', 'payload', 'checkpoint', 'lease', 'owner')}


def public_settings(store):
    s = store.settings()
    proxy = urlsplit(s['network'].get('proxy_url', ''))
    safe_proxy = ''
    if proxy.hostname:
        host = f'[{proxy.hostname}]' if ':' in proxy.hostname else proxy.hostname
        safe_proxy = f'{proxy.scheme}://{host}:{proxy.port}'
    return {
        'model': {k: s['model'][k] for k in ('base_url', 'model', 'max_steps')} | {'api_key_configured': bool(s['model']['api_key'])},
        'github': {k: s['github'][k] for k in ('repository_url', 'repository')} | {'token_configured': bool(s['github']['token'])},
        'network': {'mode': s['network']['mode'], 'proxy_url': safe_proxy, 'proxy_configured': bool(s['network']['proxy_url'])},
        'subscription': {k: s['subscription'][k] for k in ('selected_node', 'updated_at', 'status')} | {'configured': bool(s['subscription']['url']), 'nodes': [{'name': n['name'], 'type': n['type']} for n in s['subscription']['nodes']]},
        'runtime': {'worker_alive': store.worker_alive()},
    }


def public_project(store, project, detail=False):
    value = {k:v for k,v in project.items() if k != 'context'}
    if detail:
        saved = store.unseal(project['context']) if project.get('context') else {}
        value.update(summary=saved.get('summary'), snapshot=saved.get('snapshot'))
    return value


def create_app(config=None):
    config = config or Config()
    config.validate()
    store = Store(config)
    app = FastAPI(title='Repository Steward', docs_url=None, redoc_url=None)
    app.state.store, app.state.config = store, config
    subscription_refresh_lock = asyncio.Lock()
    salt = secrets.token_bytes(16)
    expected_password = password_hash(config.admin_password, salt)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({'detail': '请求字段类型或长度不符合要求'}, status_code=422)

    @app.exception_handler(IntegrationError)
    async def integration_error(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=400)

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        return JSONResponse({'detail': '服务器内部错误，请检查脱敏后的服务日志'}, status_code=500)

    @app.middleware('http')
    async def security_headers(request, call_next):
        # Prevent oversized JSON configuration and prompt submissions.
        try:
            if int(request.headers.get('content-length', '0')) > 131072:
                return JSONResponse({'detail': '请求过大'}, status_code=413)
        except ValueError:
            return JSONResponse({'detail': '无效请求'}, status_code=400)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    def auth(request: Request):
        raw = request.cookies.get('steward_session', '')
        session = store.get('sessions', token_hash(raw)) if raw else None
        if not session or session['expires'] < time.time():
            raise HTTPException(401, '请先登录')
        if request.method not in ('GET', 'HEAD'):
            if not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), session['csrf']):
                raise HTTPException(403, '请求校验失败，请刷新页面')
        return session

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'worker_alive': store.worker_alive()}

    @app.post('/api/auth/login')
    def login(body: Login, request: Request):
        source = request.client.host if request.client else 'unknown'
        with store.connect() as c:
            attempts = store.get('login_limits', source, c) or {'start': time.time(), 'count': 0}
            if time.time() - attempts['start'] > 300:
                attempts = {'start': time.time(), 'count': 0}
            if attempts['count'] >= 10:
                raise HTTPException(429, '登录尝试过多，请五分钟后重试')
            attempts['count'] += 1
            store.put('login_limits', source, attempts, c)
        if not hmac.compare_digest(password_hash(body.password, salt), expected_password):
            raise HTTPException(401, '密码不正确')
        store.delete('login_limits', source)
        raw, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        store.put('sessions', token_hash(raw), {'csrf': csrf, 'expires': time.time()+43200})
        response = JSONResponse({'authenticated': True, 'csrf_token': csrf})
        response.set_cookie('steward_session', raw, httponly=True, secure=config.secure_cookie, samesite='strict', max_age=43200, path='/')
        return response

    @app.get('/api/auth/session')
    def session(s=Depends(auth)):
        return {'authenticated': True, 'csrf_token': s['csrf']}

    @app.post('/api/auth/logout')
    def logout(request: Request, s=Depends(auth)):
        store.delete('sessions', token_hash(request.cookies.get('steward_session', '')))
        response = JSONResponse({'ok': True})
        response.delete_cookie('steward_session', path='/')
        return response

    @app.get('/api/settings')
    def settings(s=Depends(auth)):
        return public_settings(store)

    def merge_model(current, body):
        model = current.copy()
        base = check_https(str(body.get('base_url', model['base_url'])).strip())
        if base != model['base_url'] and model['api_key'] and not body.get('api_key'):
            raise HTTPException(400, '更换服务地址需要重新输入对应 API Key')
        name = str(body.get('model', model['model'])).strip()
        if not name or len(name) > 200:
            raise HTTPException(400, '请输入有效模型名称')
        steps = body.get('max_steps', model['max_steps'])
        if not isinstance(steps, int) or not 1 <= steps <= 20:
            raise HTTPException(400, '最大步骤需在 1–20 之间')
        model.update(base_url=base, model=name, max_steps=steps)
        if body.get('api_key'):
            model['api_key'] = str(body['api_key'])
        return model

    @app.put('/api/settings/model')
    def model_settings(body: dict, s=Depends(auth)):
        with store.connect() as c:
            cfg = store.settings(c)
            cfg['model'] = merge_model(cfg['model'], body)
            store.save_settings(cfg, c)
            store.audit('model_settings', 'model', c)
        return public_settings(store)

    @app.post('/api/settings/model/test')
    async def model_test(body: dict = {}, s=Depends(auth)):
        model = merge_model(store.settings()['model'], body)
        if not model['api_key']:
            raise HTTPException(400, '请填写 API Key')
        async with ModelClient(model['base_url'], model['api_key'], model['model']) as client:
            await client.test()
        return {'ok': True, 'message': '模型连接测试成功'}

    @app.put('/api/settings/github')
    def github_settings(body: dict, s=Depends(auth)):
        with store.connect() as c:
            cfg = store.settings(c)
            if 'repository_url' in body:
                repo = parse_repository(str(body['repository_url']))
                cfg['github'].update(repository=repo, repository_url='https://github.com/'+repo)
            if body.get('token'):
                cfg['github']['token'] = str(body['token'])
            store.save_settings(cfg, c)
            store.audit('github_settings', cfg['github']['repository'], c)
        return public_settings(store)

    @app.post('/api/repositories/validate')
    async def validate_repository(body: dict = {}, s=Depends(auth)):
        cfg = store.settings()
        repo = parse_repository(body.get('url') or cfg['github']['repository_url'])
        async with GitHubClient(cfg['github']['token'], proxy_for(cfg, config)) as client:
            return await client.repository(repo)

    @app.put('/api/settings/network')
    def network_settings(body: dict, s=Depends(auth)):
        if body.get('mode') not in ('direct', 'proxy', 'auto'):
            raise HTTPException(400, '无效网络模式')
        with store.connect() as c:
            cfg = store.settings(c)
            proxy = body.get('proxy_url') or cfg['network']['proxy_url']
            if proxy:
                validate_proxy(proxy, config)
                # A displayed redacted endpoint must not erase saved proxy auth.
                old = cfg['network']['proxy_url']
                oldp, newp = urlsplit(old), urlsplit(proxy)
                if old and oldp.username and not newp.username and (oldp.scheme,oldp.hostname,oldp.port)==(newp.scheme,newp.hostname,newp.port):
                    proxy = old
            if body['mode'] == 'proxy' and not proxy:
                raise HTTPException(400, '指定代理模式需要代理地址')
            cfg['network'] = {'mode': body['mode'], 'proxy_url': proxy}
            store.save_settings(cfg, c)
            store.audit('network_settings', 'github', c)
        return public_settings(store)

    @app.put('/api/settings/subscription')
    def subscription_settings(body: dict, s=Depends(auth)):
        url = check_https(str(body.get('url', '')), allow_query=True)
        with store.connect() as c:
            cfg = store.settings(c)
            if cfg['subscription']['url'] != url:
                cfg['generations']['subscription'] += 1
            cfg['subscription'].update(url=url, status='已保存，等待导入')
            store.save_settings(cfg, c)
            store.audit('subscription_settings', 'subscription', c)
        return public_settings(store)

    @app.delete('/api/settings/credentials/{kind}')
    def revoke(kind: str, s=Depends(auth)):
        mapping = {'model': ('model','api_key'), 'github': ('github','token'), 'proxy': ('network','proxy_url'), 'subscription': ('subscription','url')}
        if kind not in mapping:
            raise HTTPException(404, '凭证不存在')
        with store.connect() as c:
            cfg = store.settings(c)
            group, key = mapping[kind]
            cfg[group][key] = ''
            cfg['generations'][kind] += 1
            if kind == 'subscription':
                cfg['subscription'].update(nodes=[], selected_node=None, status='已撤销，等待清理代理客户端')
                store.put('runtime','proxy_cleanup',{'pending':True,'retry_at':0},c)
                if cfg['network']['proxy_url'] == config.sidecar_proxy_url:
                    cfg['network']['proxy_url'] = ''
                    cfg['generations']['proxy'] += 1
            store.save_settings(cfg, c)
            store.audit('credential_revoked', kind, c)
        return public_settings(store)

    def queued(kind, payload=None):
        task = store.enqueue(kind, payload=payload)
        return {'id': task['id'], 'status': task['status']}

    @app.post('/api/settings/network/test')
    def network_test(s=Depends(auth)):
        return queued('diagnostic')

    @app.post('/api/settings/subscription/refresh')
    async def subscription_refresh(s=Depends(auth)):
        from .subscriptions import download_subscription
        if subscription_refresh_lock.locked():
            raise HTTPException(409, '订阅正在刷新，请等待完成')
        async with subscription_refresh_lock:
            snapshot = store.settings()
            url = snapshot['subscription']['url']
            if not url:
                raise HTTPException(400, '请先保存订阅')
            try:
                nodes = await asyncio.wait_for(
                    download_subscription(url, proxy_url=proxy_for(snapshot, config)), timeout=40)
            except asyncio.TimeoutError:
                raise HTTPException(504, '订阅刷新超时，原有节点已保留，请稍后重试') from None
            with store.connect() as c:
                current = store.settings(c)
                if (current['subscription']['url'] != url or
                        current['generations']['subscription'] != snapshot['generations']['subscription']):
                    raise HTTPException(409, '订阅已变更，本次刷新结果未保存，请重新刷新')
                current['subscription'].update(nodes=nodes, updated_at=now(), status=f'已刷新 {len(nodes)} 个节点；刷新不会切换当前代理')
                store.save_settings(current, c)
                store.audit('subscription_refreshed', 'subscription', c)
            return public_settings(store)

    @app.put('/api/settings/subscription/node')
    def select_node(body: dict, s=Depends(auth)):
        names = [n['name'] for n in store.settings()['subscription']['nodes']]
        if body.get('name') not in names:
            raise HTTPException(400, '节点不存在')
        return queued('node', {'name': body['name']})

    def require_project(ident, c=None):
        project = store.get('projects', ident, c)
        if not project:
            raise HTTPException(404, '项目不存在')
        return project

    def require_model(cfg):
        if not cfg['model']['api_key'] or not cfg['model']['model'] or not cfg['model']['base_url']:
            raise HTTPException(400, '请先在设置中配置模型地址、名称和 API Key')

    @app.get('/api/projects')
    def projects(s=Depends(auth)):
        return [public_project(store, p) for p in store.all('projects')]

    @app.post('/api/projects')
    async def import_project(body: dict, s=Depends(auth)):
        repo = parse_repository(str(body.get('repository_url', '')))
        existing = next((p for p in store.all('projects') if p['repository'].lower() == repo.lower()), None)
        if existing:
            return public_project(store, existing, True)
        cfg = store.settings()
        require_model(cfg)
        async with GitHubClient(cfg['github']['token'], proxy_for(cfg, config)) as client:
            metadata = await client.repository(repo)
        canonical = parse_repository('https://github.com/' + metadata.get('full_name', repo))
        with store.connect() as c:
            project, created = store.create_project(canonical, c)
            if created:
                store.initialize_project(project['id'], c)
                store.audit('project_imported', canonical, c)
            project = store.get('projects', project['id'], c)
        return public_project(store, project, True)

    @app.get('/api/projects/{ident}')
    def project_detail(ident: str, s=Depends(auth)):
        return public_project(store, require_project(ident), True)

    @app.post('/api/projects/{ident}/initialize')
    def initialize_project(ident: str, s=Depends(auth)):
        with store.connect() as c:
            require_project(ident, c)
            require_model(store.settings(c))
            task = store.initialize_project(ident, c)
        return {'id':task['id'], 'status':task['status']}

    @app.get('/api/projects/{ident}/tasks')
    def project_tasks(ident: str, s=Depends(auth)):
        require_project(ident)
        return [public_task(t) for t in store.all('tasks') if t.get('project_id') == ident and t['kind'] in ('analysis','initialization')]

    @app.post('/api/projects/{ident}/tasks')
    def project_command(ident: str, body: TaskInput, s=Depends(auth)):
        if not body.prompt.strip():
            raise HTTPException(400, '请输入项目指令')
        with store.connect() as c:
            project = require_project(ident, c)
            if project['status'] != 'ready':
                raise HTTPException(409, '请先完成项目初始化；失败时可在项目概览重试')
            require_model(store.settings(c))
            task = store.enqueue('analysis', body.prompt.strip(), body.request_id, project_id=ident, conn=c)
        return {'id':task['id'], 'status':task['status']}

    @app.post('/api/tasks')
    def create_task(body: TaskInput, s=Depends(auth)):
        cfg = store.settings()
        if not cfg['github']['repository'] or not cfg['model']['api_key'] or not cfg['model']['model']:
            raise HTTPException(400, '请先配置模型和 GitHub 目标仓库')
        task = store.enqueue('analysis', body.prompt.strip(), body.request_id)
        return {'id': task['id'], 'status': task['status']}

    @app.get('/api/tasks')
    def tasks(s=Depends(auth)):
        return [public_task(t) for t in store.all('tasks') if t['kind'] != 'subscription']

    def require_task(ident, c=None):
        task = store.get('tasks', ident, c)
        if task is None or task.get('kind') == 'subscription':
            raise HTTPException(404, '任务不存在')
        return task

    @app.get('/api/tasks/{ident}')
    def task_detail(ident: str, s=Depends(auth)):
        task = public_task(require_task(ident))
        task['events'] = store.events(ident)
        task['drafts'] = [safe_draft(d) for d in store.all('drafts') if d['task_id'] == ident]
        return task

    @app.get('/api/tasks/{ident}/events')
    async def events(ident: str, request: Request, after: int = 0, s=Depends(auth)):
        require_task(ident)
        async def stream():
            cursor = after
            for _ in range(30):
                if await request.is_disconnected():
                    break
                for event in store.events(ident, cursor):
                    cursor = event['id']
                    yield f'id: {cursor}\nevent: progress\ndata: {json.dumps(event, ensure_ascii=False)}\n\n'
                yield ': heartbeat\n\n'
                await asyncio.sleep(2)
        return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

    @app.post('/api/tasks/{ident}/cancel')
    def cancel_task(ident: str, s=Depends(auth)):
        with store.connect() as c:
            task = require_task(ident, c)
            if task['status'] in ('completed', 'failed', 'cancelled'):
                return {'ok': True}
            task.update(status='cancelled', updated_at=now())
            store.put('tasks', ident, task, c)
            store.sync_project_task(task, c)
            for d in store.all('drafts', c):
                if d['task_id'] == ident and d['status'] in ('draft', 'pending'):
                    d['status'] = 'discarded'
                    store.put('drafts', d['id'], d, c)
            store.event(ident, 'cancelled', '已取消后续步骤；已发出的发布仍需核实结果', c)
        return {'ok': True}

    @app.post('/api/tasks/{ident}/resume')
    def resume_task(ident: str, s=Depends(auth)):
        with store.connect() as c:
            task = require_task(ident, c)
            if task.get('status') != 'waiting_network':
                raise HTTPException(409, '该任务当前不可恢复')
            snapshot = store.unseal(task['snapshot'])
            cfg = store.settings(c)
            snapshot['network'] = cfg['network']
            snapshot['generations']['proxy'] = cfg['generations']['proxy']
            task.update(snapshot=store.seal(snapshot), status='queued', error=None, deadline=time.time()+3600, updated_at=now())
            store.put('tasks', ident, task, c)
            store.sync_project_task(task, c)
            store.event(ident, 'queued', '采用当前网络配置恢复任务', c)
        return {'ok': True}

    def draft_change(ident, body, operation):
        with store.connect() as c:
            draft = store.get('drafts', ident, c)
            if draft is None:
                raise HTTPException(404, '草稿不存在')
            if draft['version'] != body.version:
                raise HTTPException(409, '草稿已更新，请刷新后确认')
            if operation == 'confirm' and draft['status'] in ('pending','executing','succeeded','unknown'):
                return draft
            if draft['status'] != 'draft':
                raise HTTPException(409, '该草稿已锁定，无法修改或重复发布')
            task = require_task(draft['task_id'], c)
            if task['status'] == 'cancelled':
                raise HTTPException(409, '任务已取消')
            if operation == 'edit':
                if not body.body or not body.body.strip():
                    raise HTTPException(400, '正文不能为空')
                draft['body'] = body.body
                if body.title is not None:
                    draft['title'] = body.title
                if draft['kind'] == 'issue' and not draft['title'].strip():
                    raise HTTPException(400, 'Issue 标题不能为空')
                draft['version'] += 1
            elif operation == 'confirm':
                snapshot = store.unseal(task['snapshot'])
                cfg = store.settings(c)
                token = cfg['github']['token']
                if not token and cfg['generations']['github'] == snapshot.get('generations', {}).get('github', 0):
                    token = snapshot.get('github', {}).get('token')
                if not token:
                    raise HTTPException(400, '请先配置 GitHub 发布凭证')
                draft.update(status='pending', operation_id=uid(), confirmed_at=now())
                # Current credential may be added after analysis; never change target/content.
                draft['credential_generation'] = cfg['generations']['github']
                draft['credential'] = store.seal({'token': token})
                store.audit('confirm_publish', ident, c)
            else:
                draft['status'] = 'discarded'
            store.put('drafts', ident, draft, c)
            # Sensitive executor fields are stripped from every response below.
        return draft

    def safe_draft(draft):
        return {k:v for k,v in draft.items() if k not in ('credential','credential_generation','operation_id')}

    @app.patch('/api/drafts/{ident}')
    def edit_draft(ident: str, body: DraftInput, s=Depends(auth)):
        return safe_draft(draft_change(ident, body, 'edit'))

    @app.post('/api/drafts/{ident}/confirm')
    def confirm_draft(ident: str, body: DraftInput, s=Depends(auth)):
        return safe_draft(draft_change(ident, body, 'confirm'))

    @app.post('/api/drafts/{ident}/discard')
    def discard_draft(ident: str, body: DraftInput, s=Depends(auth)):
        return safe_draft(draft_change(ident, body, 'discard'))

    # Used by task detail to guarantee the same secret filtering as draft routes.
    app.state.safe_draft = safe_draft
    return app
