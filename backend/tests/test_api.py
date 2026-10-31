import json
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


def make_client(tmp_path):
    from app.config import Config
    from app.main import create_app
    cfg = Config(data_dir=str(tmp_path), encryption_key=Fernet.generate_key().decode(), admin_password='correct-horse-long-pass', secure_cookie=False)
    app = create_app(cfg)
    return TestClient(app), app


def login(client):
    response = client.post('/api/auth/login', json={'password':'correct-horse-long-pass'})
    assert response.status_code == 200
    client.headers['X-CSRF-Token'] = response.json()['csrf_token']


def test_auth_and_csrf(tmp_path):
    c, _ = make_client(tmp_path)
    assert c.get('/api/settings').status_code == 401
    assert c.post('/api/auth/login', json={'password':'wrong'}).status_code == 401
    login(c)
    assert c.get('/api/settings').status_code == 200
    c.headers.pop('X-CSRF-Token')
    assert c.put('/api/settings/github', json={'token':'secret'}).status_code == 403


def test_secret_is_encrypted_and_not_returned(tmp_path):
    c, app = make_client(tmp_path); login(c)
    secret = 'sk-test-sensitive-12345'
    r = c.put('/api/settings/model', json={'base_url':'https://model.example/v1','model':'test-model','api_key':secret})
    assert r.status_code == 200
    assert r.json()['model']['api_key_configured'] is True
    assert secret not in r.text and secret not in c.get('/api/settings').text
    assert secret.encode() not in (tmp_path / 'steward.db').read_bytes()
    c.put('/api/settings/model', json={'base_url':'https://model.example/v1','model':'test-model','api_key':''})
    assert app.state.store.settings()['model']['api_key'] == secret
    assert c.delete('/api/settings/credentials/model').status_code == 200
    assert not c.get('/api/settings').json()['model']['api_key_configured']


def test_changing_model_origin_requires_new_key(tmp_path):
    c, _ = make_client(tmp_path); login(c)
    c.put('/api/settings/model', json={'base_url':'https://model.example/v1','model':'test','api_key':'secret'})
    r=c.put('/api/settings/model', json={'base_url':'https://other.example/v1','model':'test'})
    assert r.status_code == 400


@pytest.mark.parametrize('url',['https://gitlab.com/a/b','http://github.com/a/b','https://github.com/a/b/issues','https://x@github.com/a/b'])
def test_rejects_invalid_repo(tmp_path,url):
    c,_=make_client(tmp_path); login(c)
    assert c.put('/api/settings/github',json={'repository_url':url}).status_code == 400


def test_task_requires_configuration(tmp_path):
    c,_=make_client(tmp_path); login(c)
    assert c.post('/api/tasks',json={'prompt':'总结','request_id':'1'}).status_code == 400


def test_versioned_confirmation_is_idempotent(tmp_path):
    c,app=make_client(tmp_path); login(c)
    app.state.store.put('tasks','task',{'id':'task','status':'waiting_confirmation','snapshot':app.state.store.seal({'github':{'token':'old'}})})
    app.state.store.put('drafts','draft',{'id':'draft','task_id':'task','kind':'issue','repository':'a/b','title':'Title','body':'Body','version':1,'status':'draft'})
    assert c.patch('/api/drafts/draft',json={'version':0,'title':'New','body':'New'}).status_code == 409
    assert c.patch('/api/drafts/draft',json={'version':1,'title':'New','body':'New'}).json()['version'] == 2
    assert c.post('/api/drafts/draft/confirm',json={'version':1}).status_code == 409
    first=c.post('/api/drafts/draft/confirm',json={'version':2}); assert first.status_code == 200
    assert first.json()['status'] == 'pending'
    assert c.post('/api/drafts/draft/confirm',json={'version':2}).json()['status'] == 'pending'
    assert c.patch('/api/drafts/draft',json={'version':2,'body':'changed'}).status_code == 409


def test_unsafe_proxy_denied(tmp_path):
    c,_=make_client(tmp_path);login(c)
    assert c.put('/api/settings/network',json={'mode':'proxy','proxy_url':'http://169.254.169.254:80'}).status_code == 400


def test_task_detail_never_leaks_snapshot(tmp_path):
    c,app=make_client(tmp_path);login(c)
    app.state.store.put('tasks','t',{'id':'t','snapshot':app.state.store.seal({'api_key':'TOPSECRET'}),'prompt':'x'})
    assert 'snapshot' not in c.get('/api/tasks/t').json()
    assert 'TOPSECRET' not in c.get('/api/tasks/t').text


def test_validation_error_does_not_echo_password(tmp_path):
    c,_=make_client(tmp_path)
    secret='sensitive'*200
    r=c.post('/api/auth/login',json={'password':secret})
    assert r.status_code==422
    assert secret not in r.text


def test_draft_details_hide_execution_credentials(tmp_path):
    c,app=make_client(tmp_path);login(c)
    app.state.store.put('tasks','t',{'id':'t','status':'waiting_confirmation'})
    app.state.store.put('drafts','d',{'id':'d','task_id':'t','credential':'ENCRYPTEDSECRET','credential_generation':1})
    assert 'credential' not in c.get('/api/tasks/t').text


def test_persistence_and_request_deduplication(tmp_path):
    c,app=make_client(tmp_path);login(c)
    c.put('/api/settings/model',json={'base_url':'https://model.example/v1','model':'m','api_key':'secret'})
    c.put('/api/settings/github',json={'repository_url':'https://github.com/a/b'})
    a=c.post('/api/tasks',json={'prompt':'summarize','request_id':'same'}).json()
    b=c.post('/api/tasks',json={'prompt':'summarize','request_id':'same'}).json()
    assert a['id']==b['id']
    from app.store import Store
    reopened=Store(app.state.config)
    assert reopened.settings()['model']['model']=='m'
    assert len(reopened.all('tasks'))==1


def test_revoked_github_snapshot_cannot_authorize_publish(tmp_path):
    c,app=make_client(tmp_path);login(c)
    settings=app.state.store.settings();settings['github']['token']='revoked-token'
    app.state.store.put('tasks','t',{'id':'t','status':'waiting_confirmation','snapshot':app.state.store.seal(settings)})
    app.state.store.put('drafts','d',{'id':'d','task_id':'t','kind':'issue','repository':'a/b','title':'x','body':'y','version':1,'status':'draft'})
    c.delete('/api/settings/credentials/github')
    assert c.post('/api/drafts/d/confirm',json={'version':1}).status_code==400


def test_delete_subscription_queues_sidecar_cleanup(tmp_path):
    c,app=make_client(tmp_path);login(c)
    cfg=app.state.store.settings();cfg['subscription'].update(url='https://example.com/sub',nodes=[{'name':'a','type':'trojan'}],selected_node='a');app.state.store.save_settings(cfg)
    c.delete('/api/settings/credentials/subscription')
    assert app.state.store.get('runtime','proxy_cleanup')['pending'] is True


def test_successful_login_does_not_exhaust_failure_budget(tmp_path):
    c,_=make_client(tmp_path)
    for _ in range(12):
        assert c.post('/api/auth/login',json={'password':'correct-horse-long-pass'}).status_code==200


@pytest.mark.parametrize('selected', [None, 'one'])
def test_subscription_refresh_updates_settings_without_creating_task(tmp_path, monkeypatch, selected):
    import app.subscriptions as subs
    c, app = make_client(tmp_path); login(c)
    c.put('/api/settings/subscription', json={'url':'https://example.com/sub'})
    before=app.state.store.settings()
    before['subscription']['selected_node']=selected
    app.state.store.save_settings(before)
    async def download(*args, **kwargs):
        return [{'name':'one','type':'trojan','password':'private-node-secret','server':'example.com','port':443}]
    monkeypatch.setattr(subs,'download_subscription',download)
    r=c.post('/api/settings/subscription/refresh')
    assert r.status_code==200
    assert r.json()['subscription']['nodes']==[{'name':'one','type':'trojan'}]
    assert 'private-node-secret' not in r.text
    assert app.state.store.all('tasks')==[]
    assert app.state.store.settings()['network']==before['network']
    assert app.state.store.settings()['subscription']['selected_node'] == selected


def test_subscription_refresh_failure_keeps_existing_settings(tmp_path, monkeypatch):
    import app.subscriptions as subs
    from app.integrations import IntegrationError
    c, app = make_client(tmp_path); login(c)
    cfg=app.state.store.settings();cfg['subscription'].update(url='https://example.com/sub',nodes=[{'name':'old','type':'trojan'}]);app.state.store.save_settings(cfg)
    async def download(*args, **kwargs): raise IntegrationError('订阅连接超时',kind='network')
    monkeypatch.setattr(subs,'download_subscription',download)
    r=c.post('/api/settings/subscription/refresh')
    assert r.status_code==400
    assert r.json()['detail']=='订阅连接超时'
    assert app.state.store.settings()==cfg
    assert app.state.store.all('tasks')==[]


@pytest.mark.parametrize('change', ['revoke','replace'])
def test_subscription_refresh_rejects_stale_download(tmp_path, monkeypatch, change):
    import app.subscriptions as subs
    c, app = make_client(tmp_path); login(c)
    c.put('/api/settings/subscription',json={'url':'https://example.com/sub'})
    async def download(*args, **kwargs):
        cfg=app.state.store.settings()
        if change=='revoke':
            cfg['subscription']['url']='';cfg['generations']['subscription']+=1
        else: cfg['subscription']['url']='https://example.com/other'
        app.state.store.save_settings(cfg)
        return [{'name':'stale','type':'trojan'}]
    monkeypatch.setattr(subs,'download_subscription',download)
    assert c.post('/api/settings/subscription/refresh').status_code==409
    assert app.state.store.settings()['subscription']['nodes']==[]
    assert app.state.store.all('tasks')==[]


def test_task_list_excludes_legacy_subscription_refresh(tmp_path):
    c, app=make_client(tmp_path);login(c)
    legacy=app.state.store.enqueue('subscription')
    analysis=app.state.store.enqueue('analysis','analyze')
    assert [t['id'] for t in c.get('/api/tasks').json()]==[analysis['id']]
    assert app.state.store.get('tasks',legacy['id']) is not None


@pytest.mark.asyncio
async def test_subscription_refresh_rejects_overlapping_requests(tmp_path, monkeypatch):
    import asyncio
    import httpx
    import app.subscriptions as subs
    c, app=make_client(tmp_path);login(c)
    c.put('/api/settings/subscription',json={'url':'https://example.com/sub'})
    started, release=asyncio.Event(),asyncio.Event()
    async def download(*args, **kwargs):
        started.set();await release.wait()
        return [{'name':'one','type':'trojan'}]
    monkeypatch.setattr(subs,'download_subscription',download)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver',cookies=c.cookies,headers={'X-CSRF-Token':c.headers['X-CSRF-Token']}) as client:
        first=asyncio.create_task(client.post('/api/settings/subscription/refresh'))
        await asyncio.wait_for(started.wait(),2)
        try:
            assert (await client.post('/api/settings/subscription/refresh')).status_code==409
        finally: release.set()
        assert (await first).status_code==200
    assert app.state.store.all('tasks')==[]


def test_subscription_refresh_total_timeout_keeps_old_data(tmp_path, monkeypatch):
    import asyncio
    import app.main as main
    c, app=make_client(tmp_path);login(c)
    c.put('/api/settings/subscription',json={'url':'https://example.com/sub'})
    before=app.state.store.settings()
    async def timeout(awaitable, timeout):
        awaitable.close()
        raise asyncio.TimeoutError()
    monkeypatch.setattr(main.asyncio,'wait_for',timeout)
    assert c.post('/api/settings/subscription/refresh').status_code==504
    assert app.state.store.settings()==before
    assert app.state.store.all('tasks')==[]
