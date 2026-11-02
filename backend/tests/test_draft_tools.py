import pytest
from app.draft_tools import prepare_draft
from app.integrations import IntegrationError


class GitHub:
    def __init__(self): self.calls=[];self.target={'number':4,'state':'open','html_url':'https://github.com/a/b/issues/4'}
    async def tool(self,repo,name,args):
        self.calls.append((repo,name,args))
        if name=='search_issues':return {'items':[],'coverage':{'total_count':0}}
        return self.target


@pytest.mark.asyncio
async def test_issue_preparation_searches_and_never_writes():
    g=GitHub();draft,info=await prepare_draft('prepare_issue',{'title':' UI ','body':'## Goal\nReadable','duplicate_query':'display'},'a/b',g,lambda:None)
    assert draft=={'kind':'issue','title':'UI','body':'## Goal\nReadable','issue_number':None}
    assert g.calls==[('a/b','search_issues',{'query':'is:issue display'})]
    assert '尚未发布' in info['report']


@pytest.mark.asyncio
@pytest.mark.parametrize('target',[
    {'number':4,'state':'closed'},
    {'number':4,'state':'open','pull_request':{}},
    {'number':5,'state':'open'},
])
async def test_comment_rejects_wrong_closed_and_pr_targets(target):
    g=GitHub();g.target=target
    with pytest.raises(IntegrationError):
        await prepare_draft('prepare_comment',{'issue_number':4,'body':'Follow up'},'a/b',g,lambda:None)


@pytest.mark.asyncio
async def test_comment_uses_fixed_repository_and_validates_target():
    g=GitHub();d,info=await prepare_draft('prepare_comment',{'issue_number':4,'body':'Follow up'},'a/b',g,lambda:None)
    assert d['kind']=='comment' and d['issue_number']==4
    assert g.calls==[('a/b','get_issue',{'number':4})]


@pytest.mark.asyncio
@pytest.mark.parametrize('name,args',[
    ('prepare_issue',{'title':'','body':'B','duplicate_query':'UI'}),
    ('prepare_issue',{'title':'A','body':'x'*12001,'duplicate_query':'UI'}),
    ('prepare_issue',{'title':'A','body':'B'}),
    ('prepare_issue',{'title':'A','body':'B','duplicate_query':'UI','repository':'evil/repo'}),
    ('prepare_comment',{'issue_number':True,'body':'B'}),
    ('prepare_comment',{'issue_number':4,'body':' '}),
])
async def test_invalid_arguments_do_not_make_requests(name,args):
    g=GitHub()
    with pytest.raises(IntegrationError):
        await prepare_draft(name,args,'a/b',g,lambda:None)
    assert g.calls==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('name,args',[
    ('prepare_issue',{'title':'Improve display','body':'## Goal\nReadable','duplicate_query':'display'}),
    ('prepare_comment',{'issue_number':4,'body':'Please clarify the expected UI.'}),
])
async def test_explicit_tool_creates_editable_draft_and_only_confirm_publishes(tmp_path,name,args):
    import json
    from test_api import make_client, login
    from test_worker import FakeGitHub
    from app.worker import Worker
    c,app=make_client(tmp_path);login(c);s=app.state.store
    cfg=s.settings();cfg['github'].update(repository='a/b',token='test-token')
    cfg['model'].update(base_url='https://example.com/v1',model='test',api_key='test-key');s.save_settings(cfg)
    class Remote(FakeGitHub):
        async def tool(self,repo,tool,arguments):
            if tool=='search_issues':return {'items':[],'coverage':{}}
            if tool=='get_issue':return {'number':4,'state':'open','html_url':'https://github.com/a/b/issues/4'}
            raise AssertionError('Unexpected tool')
    class Model:
        calls=0
        def __init__(self,*a):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def complete(self,messages,tools=None):
            self.calls+=1
            assert self.calls==1
            schema=next(t['function'] for t in tools if t['function']['name']==name)
            assert set(schema['parameters']['required'])==set(args)
            return {'role':'assistant','content':None,'tool_calls':[
                {'id':'prepare','type':'function','function':{'name':name,'arguments':json.dumps(args)}},
                {'id':'unused','type':'function','function':{'name':'read_file','arguments':'{"path":"must-not-read"}'}},
            ]}
    Remote.publications=[]
    w=Worker(app.state.config,s,github_factory=Remote,model_factory=Model)
    task=s.enqueue('analysis','Please prepare a draft')
    await w.tick()
    assert s.get('tasks',task['id'])['status']=='waiting_confirmation'
    draft=s.all('drafts')[0];assert draft['status']=='draft'
    assert Remote.publications==[]
    edited=c.patch('/api/drafts/'+draft['id'],json={'version':1,'body':'Edited body'});assert edited.status_code==200
    confirm=c.post('/api/drafts/'+draft['id']+'/confirm',json={'version':2});assert confirm.status_code==200
    assert c.post('/api/drafts/'+draft['id']+'/confirm',json={'version':2}).status_code==200
    await w.tick();await w.tick()
    assert len(Remote.publications)==1
    assert 'Edited body' in Remote.publications[0]
    assert s.get('drafts',draft['id'])['status']=='succeeded'


@pytest.mark.asyncio
async def test_invalid_tool_arguments_can_be_corrected_within_budget(tmp_path):
    import json
    from test_worker import setup, FakeGitHub
    from app.worker import Worker
    class Remote(FakeGitHub):
        async def tool(self,*args):return {'items':[]}
    class Model:
        calls=0
        def __init__(self,*a):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def complete(self,messages,tools=None):
            self.calls+=1
            args={'title':'Title','body':'Body','duplicate_query':'UI'}
            if self.calls==1:args['body']=''
            else:assert 'error' in json.loads(messages[-1]['content'])
            return {'role':'assistant','tool_calls':[{'id':str(self.calls),'type':'function','function':{'name':'prepare_issue','arguments':json.dumps(args)}}]}
    cfg,s=setup(tmp_path);t=s.enqueue('analysis','write issue')
    await Worker(cfg,s,github_factory=Remote,model_factory=Model).tick()
    assert s.get('tasks',t['id'])['status']=='waiting_confirmation'
    assert len(s.all('drafts'))==1


@pytest.mark.asyncio
@pytest.mark.parametrize('public', [True,False])
async def test_missing_comment_target_is_correctable_only_when_repo_still_public(public):
    class Missing(GitHub):
        async def tool(self,*a):raise IntegrationError('not found',kind='not_found')
        async def repository(self,repo):
            if not public:raise IntegrationError('not public',kind='private')
            return {'private':False}
    with pytest.raises(IntegrationError) as caught:
        await prepare_draft('prepare_comment',{'issue_number':404,'body':'Hello'},'a/b',Missing(),lambda:None)
    assert caught.value.kind==('draft_validation' if public else 'private')
