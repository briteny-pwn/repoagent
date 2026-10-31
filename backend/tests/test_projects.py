import json
import pytest
from test_api import make_client, login
from app.store import Store


def configure(app):
    c=app.state.store.settings();c['model'].update(model='test',api_key='secret',base_url='https://model.example/v1');app.state.store.save_settings(c)


class PublicGitHub:
    def __init__(self,*args,**kwargs):pass
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def repository(self,repo):return {'full_name':repo,'private':False,'html_url':f'https://github.com/{repo}'}


def test_project_import_is_idempotent_and_initializes(tmp_path,monkeypatch):
    import app.main as main
    monkeypatch.setattr(main,'GitHubClient',PublicGitHub)
    c,app=make_client(tmp_path);login(c);configure(app)
    first=c.post('/api/projects',json={'repository_url':'https://github.com/Owner/Repo'})
    assert first.status_code==200
    p=first.json();assert p['status']=='pending' and p['initialization_task_id']
    again=c.post('/api/projects',json={'repository_url':'https://github.com/owner/repo'})
    assert again.json()['id']==p['id']
    assert len(app.state.store.all('tasks'))==1
    assert app.state.store.all('tasks')[0]['project_id']==p['id']
    assert app.state.store.all('tasks')[0]['kind']=='initialization'
    assert c.post('/api/projects/'+p['id']+'/tasks',json={'prompt':'analyze','request_id':'one'}).status_code==409


def test_legacy_analysis_migration_is_idempotent_and_preserves_drafts(tmp_path):
    c,app=make_client(tmp_path);s=app.state.store
    cfg=s.settings();cfg['github']['repository']='owner/repo';s.save_settings(cfg)
    task=s.enqueue('analysis','legacy')
    # Simulate old records lacking project fields.
    task.pop('project_id',None);s.put('tasks',task['id'],task)
    for p in s.all('projects'):s.delete('projects',p['id'])
    s.put('drafts','draft',{'id':'draft','task_id':task['id']})
    Store(app.state.config);Store(app.state.config)
    assert len(s.all('projects'))==1
    assert s.get('tasks',task['id'])['project_id']==s.all('projects')[0]['id']
    assert s.get('drafts','draft')['task_id']==task['id']
    assert len(s.all('tasks'))==1


def test_project_command_freezes_own_context_and_scopes_idempotency(tmp_path):
    c,app=make_client(tmp_path);login(c);configure(app);s=app.state.store
    with s.connect() as conn:
        a,_=s.create_project('a/repo',conn);b,_=s.create_project('b/repo',conn)
        for p in (a,b):
            p.update(status='ready',initialized_at='2026-10-31',context=s.seal({'snapshot':{'metadata':{},'files':[],'issues':{'items':[]},'pull_requests':{'items':[]},'captured_at':'2026-10-31'},'summary':{'report':p['repository'],'evidence':[],'limitations':[]}}))
            s.put('projects',p['id'],p,conn)
    one=c.post('/api/projects/'+a['id']+'/tasks',json={'prompt':'question','request_id':'same'}).json()
    two=c.post('/api/projects/'+b['id']+'/tasks',json={'prompt':'question','request_id':'same'}).json()
    assert one['id']!=two['id']
    ta=s.get('tasks',one['id']);assert ta['repository']=='a/repo'
    assert s.unseal(ta['snapshot'])['project_context']['summary']['report']=='a/repo'
    assert [t['id'] for t in c.get('/api/projects/'+a['id']+'/tasks').json()]==[one['id']]
    assert 'snapshot' not in c.get('/api/tasks/'+one['id']).json()


@pytest.mark.asyncio
async def test_initialization_persists_snapshot_report_then_commands_use_context(tmp_path,monkeypatch):
    from app.worker import Worker
    import app.project_context as pc
    c,app=make_client(tmp_path);configure(app);s=app.state.store
    p,_=s.create_project('a/repo');task=s.initialize_project(p['id'])
    snapshot={'metadata':{'html_url':'https://github.com/a/repo'},'readme':None,'files':[], 'directory':[{'path':'src','type':'dir','html_url':'https://github.com/a/repo/tree/main/src'}],
              'issues':{'items':[],'coverage':{}},'pull_requests':{'items':[],'coverage':{}},'captured_at':'2026-10-31','limitations':['limited']}
    async def collect(*args):return snapshot
    monkeypatch.setattr(pc,'collect_project_snapshot',collect)
    class Model:
        messages=[]
        def __init__(self,*a):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def complete(self,messages,tools=None):
            self.messages.append(messages)
            return {'role':'assistant','content':json.dumps({'report':'project summary','evidence':[{'title':'repo','url':'https://github.com/a/repo'}],'limitations':[],'drafts':[]})}
    w=Worker(app.state.config,s,github_factory=PublicGitHub,model_factory=Model)
    await w.tick()
    project=s.get('projects',p['id']);assert project['status']=='ready'
    assert s.unseal(project['context'])['snapshot']==snapshot
    assert s.get('tasks',task['id'])['status']=='completed'
    assert s.all('drafts')==[]
    command=s.enqueue('analysis','follow up',project_id=p['id'])
    await w.tick()
    assert s.get('tasks',command['id'])['status']=='completed'
    assert 'project summary' in json.dumps(Model.messages[-1],ensure_ascii=False)
    assert 'src' in json.dumps(Model.messages[0])


@pytest.mark.asyncio
async def test_initialization_failure_preserves_previous_context_and_retry(tmp_path,monkeypatch):
    from app.worker import Worker
    from app.integrations import IntegrationError
    import app.project_context as pc
    c,app=make_client(tmp_path);configure(app);s=app.state.store
    p,_=s.create_project('a/repo');p['context']=s.seal({'snapshot':{},'summary':{'report':'previous'}});s.put('projects',p['id'],p)
    task=s.initialize_project(p['id'])
    async def collect(*args):raise IntegrationError('network unavailable',kind='network')
    monkeypatch.setattr(pc,'collect_project_snapshot',collect)
    class Model:
        def __init__(self,*a):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
    await Worker(app.state.config,s,github_factory=PublicGitHub,model_factory=Model).tick()
    failed=s.get('projects',p['id']);assert failed['status']=='failed'
    assert s.unseal(failed['context'])['summary']['report']=='previous'
    retry=s.initialize_project(p['id']);assert retry['id']!=task['id']
    assert s.get('tasks',task['id'])['status']=='cancelled'


def test_initialization_cancel_updates_project_and_allows_new_task(tmp_path):
    c,app=make_client(tmp_path);login(c);configure(app);s=app.state.store
    p,_=s.create_project('a/repo');task=s.initialize_project(p['id'])
    assert c.post('/api/tasks/'+task['id']+'/cancel').status_code==200
    assert s.get('projects',p['id'])['status']=='failed'
    retry=c.post('/api/projects/'+p['id']+'/initialize').json()
    assert retry['id']!=task['id']
    assert s.get('projects',p['id'])['status']=='pending'


def test_context_history_is_scoped_and_bounded(tmp_path):
    _,app=make_client(tmp_path);s=app.state.store
    a,_=s.create_project('a/repo');b,_=s.create_project('b/repo')
    for p in (a,b):
        for n in range(8):
            t=s.enqueue('analysis',str(n),project_id=p['id'])
            t.update(status='completed',result={'report':p['repository']+str(n)})
            s.put('tasks',t['id'],t)
    t=s.enqueue('analysis','follow up',project_id=a['id'])
    context=s.unseal(t['snapshot'])['project_context']
    assert len(context['history'])==6
    assert all(item['report'].startswith('a/repo') for item in context['history'])

def test_stale_cancel_cannot_invalidate_completed_initialization(tmp_path):
    c,app=make_client(tmp_path);login(c);configure(app);s=app.state.store
    p,_=s.create_project('a/repo');task=s.initialize_project(p['id'])
    task.update(status='completed');s.put('tasks',task['id'],task)
    p=s.get('projects',p['id']);p.update(status='ready');s.put('projects',p['id'],p)
    assert c.post('/api/tasks/'+task['id']+'/cancel').status_code==200
    assert s.get('projects',p['id'])['status']=='ready'
    assert s.get('tasks',task['id'])['status']=='completed'


def test_project_exposes_waiting_state_and_retry_time(tmp_path):
    c,app=make_client(tmp_path);login(c);configure(app);s=app.state.store
    p,_=s.create_project('a/repo');t=s.initialize_project(p['id'])
    t.update(status='waiting_rate_limit',retry_at=2000000000,error='limited')
    s.put('tasks',t['id'],t)
    detail=c.get('/api/projects/'+p['id']).json()
    assert detail['status']=='waiting_rate_limit'
    assert detail['retry_at']==2000000000
