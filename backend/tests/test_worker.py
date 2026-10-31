import json
import time
import pytest
from cryptography.fernet import Fernet
from app.config import Config
from app.store import Store
from app.integrations import IntegrationError


class FakeGitHub:
    publications=[]
    ambiguous=False
    found=None
    def __init__(self,*args,**kwargs): pass
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass
    async def repository(self,repo): return {'full_name':repo,'private':False,'html_url':'https://github.com/'+repo}
    async def tool(self,repo,name,args): return {'items':[{'number':1,'title':'Fix docs','html_url':'https://github.com/a/b/issues/1'}], 'coverage':'first page'}
    async def publish(self,repo,kind,title,body,issue_number=None, before_write=None):
        if before_write: before_write()
        self.publications.append(body)
        if self.ambiguous: raise IntegrationError('unknown',ambiguous=True)
        return {'html_url':'https://github.com/a/b/issues/2'}
    async def find_operation(self,*a,**kw): return self.found
    async def diagnose(self,*a): return {'ok':True}


class FakeModel:
    count=0
    def __init__(self,*a,**kw): pass
    async def __aenter__(self): return self
    async def __aexit__(self,*a): pass
    async def complete(self,messages,tools=None):
        type(self).count+=1
        if self.count==1:
            return {'role':'assistant','content':None,'tool_calls':[{'id':'c1','type':'function','function':{'name':'search_issues','arguments':'{"query":"docs"}'}}]}
        return {'role':'assistant','content':json.dumps({'report':'文档待跟进','evidence':[{'title':'Issue 1','url':'https://github.com/a/b/issues/1'}],'limitations':[], 'drafts':[{'kind':'issue','title':'整理文档','body':'请补充使用说明。'}]})}


def setup(tmp_path):
    cfg=Config(data_dir=str(tmp_path),encryption_key=Fernet.generate_key().decode(),admin_password='long-secure-test-password',secure_cookie=False)
    store=Store(cfg)
    s=store.settings();s['model'].update(base_url='https://model.example/v1',model='m',api_key='secret');s['github'].update(repository='a/b',repository_url='https://github.com/a/b',token='gh-secret');store.save_settings(s)
    FakeGitHub.publications=[];FakeGitHub.ambiguous=False;FakeGitHub.found=None;FakeModel.count=0
    return cfg,store


@pytest.mark.asyncio
async def test_agent_tool_loop_and_confirmed_publication(tmp_path):
    from app.worker import Worker
    cfg,s=setup(tmp_path); w=Worker(cfg,store=s,github_factory=FakeGitHub,model_factory=FakeModel)
    task=s.enqueue('analysis','整理仓库')
    await w.tick()
    result=s.get('tasks',task['id'])
    assert result['status']=='waiting_confirmation'
    assert result['result']['report']=='文档待跟进'
    assert FakeGitHub.publications==[]
    draft=s.all('drafts')[0];draft.update(status='pending',operation_id='operation',credential=s.seal({'token':'gh-secret'}),credential_generation=0);s.put('drafts',draft['id'],draft)
    await w.tick();await w.tick()
    assert s.get('drafts',draft['id'])['status']=='succeeded'
    assert len(FakeGitHub.publications)==1
    assert 'operation' in FakeGitHub.publications[0]


@pytest.mark.asyncio
async def test_ambiguous_publish_not_retried(tmp_path):
    from app.worker import Worker
    cfg,s=setup(tmp_path);w=Worker(cfg,store=s,github_factory=FakeGitHub,model_factory=FakeModel)
    t=s.enqueue('analysis','x');await w.tick()
    d=s.all('drafts')[0];d.update(status='pending',operation_id='x',credential=s.seal({'token':'gh-secret'}),credential_generation=0);s.put('drafts',d['id'],d)
    FakeGitHub.ambiguous=True
    await w.tick();await w.tick()
    assert len(FakeGitHub.publications)==1
    assert s.get('drafts',d['id'])['status']=='unknown'


@pytest.mark.asyncio
async def test_cancelled_task_does_not_run(tmp_path):
    from app.worker import Worker
    cfg,s=setup(tmp_path);w=Worker(cfg,store=s,github_factory=FakeGitHub,model_factory=FakeModel)
    t=s.enqueue('analysis','x');t['status']='cancelled';s.put('tasks',t['id'],t)
    await w.tick()
    assert FakeModel.count==0


@pytest.mark.asyncio
async def test_rate_limit_persists_schedule(tmp_path):
    from app.worker import Worker
    class Limited(FakeGitHub):
        async def repository(self,repo):raise IntegrationError('限流',kind='rate_limit',retry_at=time.time()+120)
    cfg,s=setup(tmp_path);w=Worker(cfg,store=s,github_factory=Limited,model_factory=FakeModel)
    t=s.enqueue('analysis','x');await w.tick()
    assert s.get('tasks',t['id'])['status']=='waiting_rate_limit'
    assert s.get('tasks',t['id'])['retry_at']>time.time()+100
    await w.tick();assert FakeModel.count==0


@pytest.mark.asyncio
async def test_revoked_credentials_block_frozen_task(tmp_path):
    from app.worker import Worker
    cfg,s=setup(tmp_path);w=Worker(cfg,store=s,github_factory=FakeGitHub,model_factory=FakeModel)
    t=s.enqueue('analysis','x');settings=s.settings();settings['generations']['model']+=1;s.save_settings(settings)
    await w.tick()
    assert s.get('tasks',t['id'])['status']=='failed'
    assert FakeModel.count==0


@pytest.mark.asyncio
async def test_interrupted_publish_recovers_as_unknown_never_pending(tmp_path):
    from app.worker import Worker
    cfg,s=setup(tmp_path);w=Worker(cfg,store=s,github_factory=FakeGitHub,model_factory=FakeModel)
    s.put('drafts','d',{'id':'d','task_id':'missing','status':'executing','lease':0})
    await w.tick()
    assert s.get('drafts','d')['status']=='unknown'
    assert FakeGitHub.publications==[]


@pytest.mark.asyncio
async def test_old_queued_task_not_hidden_by_recent_completed_tasks(tmp_path):
    from app.worker import Worker
    cfg,s=setup(tmp_path);w=Worker(cfg,store=s,github_factory=FakeGitHub,model_factory=FakeModel)
    t=s.enqueue('analysis','old')
    for n in range(501):s.put('tasks',str(n),{'id':str(n),'status':'completed'})
    await w.tick()
    assert s.get('tasks',t['id'])['status']=='waiting_confirmation'


@pytest.mark.asyncio
async def test_cancel_during_publication_preflight_prevents_write(tmp_path):
    import httpx
    from app.worker import Worker
    from app.integrations import GitHubClient
    cfg,s=setup(tmp_path)
    t=s.enqueue('analysis','x')
    t['status']='waiting_confirmation';s.put('tasks',t['id'],t)
    d={'id':'d','task_id':t['id'],'kind':'issue','repository':'a/b','title':'x','body':'x','status':'pending','operation_id':'op','credential':s.seal({'token':'secret'}),'credential_generation':0}
    s.put('drafts','d',d)
    writes=[]
    def handler(request):
        if request.method=='GET':
            current=s.get('tasks',t['id']);current['status']='cancelled';s.put('tasks',t['id'],current)
            return httpx.Response(200,json={'private':False,'full_name':'a/b'})
        writes.append(request)
        return httpx.Response(201,json={'html_url':'https://github.com/a/b/issues/1'})
    factory=lambda *args: GitHubClient(transport=httpx.MockTransport(handler))
    await Worker(cfg,store=s,github_factory=factory).tick()
    assert writes==[]
    assert s.get('drafts','d')['status']=='discarded'


@pytest.mark.asyncio
async def test_subscription_cancel_during_probe_rolls_back_before_commit(tmp_path,monkeypatch):
    import app.subscriptions as subs
    from app.worker import Worker
    cfg,s=setup(tmp_path)
    current=s.settings();current['subscription'].update(url='https://example.com/sub',nodes=[{'name':'a','type':'trojan','server':'example.com','port':443,'password':'x'}]);s.save_settings(current)
    t=s.enqueue('node',payload={'name':'a'})
    live={'selected':'old'}
    async def activate(*args,before_commit=None):
        live['selected']='a'
        task=s.get('tasks',t['id']);task['status']='cancelled';s.put('tasks',t['id'],task)
        try:
            if before_commit: before_commit()
        except Exception:
            live['selected']='old'
            raise
        return {'ok':True}
    monkeypatch.setattr(subs,'activate_mihomo',activate)
    await Worker(cfg,store=s).tick()
    assert live['selected']=='old'
    assert s.settings()['subscription']['selected_node'] is None


@pytest.mark.asyncio
async def test_proxy_cleanup_wait_does_not_block_direct_analysis(tmp_path):
    from app.worker import Worker
    cfg,s=setup(tmp_path)
    s.put('runtime','proxy_cleanup',{'pending':True,'retry_at':time.time()+300})
    t=s.enqueue('analysis','x')
    await Worker(cfg,store=s,github_factory=FakeGitHub,model_factory=FakeModel).tick()
    assert s.get('tasks',t['id'])['status']=='waiting_confirmation'


def test_worker_cancels_legacy_subscription_jobs_without_executing(tmp_path):
    from app.worker import Worker
    cfg=Config(data_dir=str(tmp_path),encryption_key=Fernet.generate_key().decode(),admin_password='test-password-long')
    store=Store(cfg)
    legacy=store.enqueue('subscription')
    worker=Worker(cfg,store)
    assert worker.claim() is None
    assert store.get('tasks',legacy['id'])['status']=='cancelled'
