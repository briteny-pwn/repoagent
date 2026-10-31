import asyncio
import base64
import json
import time

import httpx
import pytest

from app.integrations import GitHubClient, IntegrationError, ModelClient, parse_repository, validate_public_url, pin_public_url
from app.subscriptions import parse_subscription, render_mihomo_config, render_direct_mihomo_config, pin_subscription_nodes, download_subscription, activate_mihomo, deactivate_mihomo


def test_repository_url_is_exact_github_repository():
    assert parse_repository('https://github.com/Owner/Repo.git/') == 'Owner/Repo'
    for url in ('http://github.com/a/b', 'https://evil.com/a/b', 'https://github.com/a/b/issues', 'https://github.com/a/b?x=1', 'https://me@github.com/a/b'):
        with pytest.raises(IntegrationError):
            parse_repository(url)


@pytest.mark.asyncio
async def test_public_url_rejects_private_ip_and_userinfo():
    for url in ('https://127.0.0.1/x', 'https://10.0.0.1/x', 'https://user@public.example/x', 'http://example.com/x'):
        with pytest.raises(IntegrationError):
            await validate_public_url(url)


@pytest.mark.asyncio
async def test_github_repository_rejects_private_and_get_retries():
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={'full_name':'a/b','private':False,'html_url':'https://github.com/a/b'})
    async with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        result = await client.repository('a/b')
    assert result['full_name'] == 'a/b'
    assert calls == 2


@pytest.mark.asyncio
async def test_github_rejects_private_repository():
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={'full_name':'a/b','private':True}))
    async with GitHubClient(transport=transport) as client:
        with pytest.raises(IntegrationError) as error:
            await client.repository('a/b')
    assert error.value.kind == 'private'


@pytest.mark.asyncio
async def test_github_rate_limit_has_retry_epoch():
    transport = httpx.MockTransport(lambda _: httpx.Response(429, headers={'Retry-After':'120'}))
    async with GitHubClient(transport=transport) as client:
        with pytest.raises(IntegrationError) as error:
            await client.repository('a/b')
    assert error.value.kind == 'rate_limit'
    assert error.value.retry_at >= time.time() + 100


@pytest.mark.asyncio
async def test_publish_timeout_ambiguous_and_not_retried():
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        if request.method == 'GET':
            return httpx.Response(200, json={'full_name':'a/b','private':False})
        raise httpx.ReadTimeout('timeout')
    async with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(IntegrationError) as error:
            await client.publish('a/b','issue','title','body')
    assert error.value.ambiguous
    assert calls == 2


@pytest.mark.asyncio
async def test_publish_before_write_guard_can_block_post():
    methods = []
    def handler(request):
        methods.append(request.method)
        return httpx.Response(200, json={'full_name':'a/b','private':False})
    async with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match='cancelled'):
            await client.publish('a/b','issue','title','body', before_write=lambda: (_ for _ in ()).throw(RuntimeError('cancelled')))
    assert methods == ['GET']


@pytest.mark.asyncio
@pytest.mark.parametrize('issue', [
    {'number':1,'state':'closed'},
    {'number':1,'state':'open','pull_request':{'url':'https://api.github.com/repos/a/b/pulls/1'}},
])
async def test_comment_publish_rejects_closed_issue_and_pull_request(issue):
    methods = []
    def handler(request):
        methods.append(request.method)
        if request.url.path == '/repos/a/b':
            return httpx.Response(200, json={'full_name':'a/b','private':False})
        return httpx.Response(200, json=issue)
    async with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(IntegrationError) as error:
            await client.publish('a/b','comment','','body',issue_number=1)
    assert error.value.kind == 'validation'
    assert methods == ['GET','GET']


@pytest.mark.asyncio
async def test_comment_guard_runs_after_target_preflight():
    paths = []
    def handler(request):
        paths.append(request.url.path)
        if request.url.path == '/repos/a/b':
            return httpx.Response(200, json={'full_name':'a/b','private':False})
        if request.url.path == '/repos/a/b/issues/1':
            return httpx.Response(200, json={'number':1,'state':'open'})
        return httpx.Response(201, json={'id':42})
    guarded = []
    def guard():
        guarded.append(list(paths))
    async with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        result = await client.publish('a/b','comment','','body',issue_number=1,before_write=guard)
    assert result['id'] == 42
    assert guarded == [['/repos/a/b','/repos/a/b/issues/1']]


@pytest.mark.asyncio
async def test_model_completion_uses_openai_shape_and_hides_remote_body():
    captured = []
    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={'choices':[{'message':{'role':'assistant','content':'ok'}}]})
    async with ModelClient('https://model.example/v1','secret','m',transport=httpx.MockTransport(handler)) as client:
        assert await client.complete([{'role':'user','content':'hello'}]) == {'role':'assistant','content':'ok'}
    assert captured[0].url.path == '/v1/chat/completions'
    assert json.loads(captured[0].content)['model'] == 'm'
    assert json.loads(captured[0].content)['max_tokens'] == 4096


@pytest.mark.asyncio
async def test_read_file_returns_bounded_decoded_text_and_source_link():
    large='a' * (200 * 1024 + 12)
    def handler(request):
        if request.url.path=='/repos/a/b':
            return httpx.Response(200,json={'full_name':'a/b','private':False})
        return httpx.Response(200,json={'type':'file','path':'README.md','sha':'abc','size':len(large),
                                         'html_url':'https://github.com/a/b/blob/main/README.md',
                                         'encoding':'base64','content':base64.b64encode(large.encode()).decode()})
    async with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        result=await client.tool('a/b','read_file',{'path':'README.md'})
    assert result['text']=='a' * (200 * 1024)
    assert result['coverage']['truncated'] is True
    assert result['html_url']=='https://github.com/a/b/blob/main/README.md'
    assert 'content' not in result


@pytest.mark.asyncio
async def test_read_file_rejects_binary_content():
    def handler(request):
        if request.url.path=='/repos/a/b':
            return httpx.Response(200,json={'full_name':'a/b','private':False})
        return httpx.Response(200,json={'type':'file','encoding':'base64','content':base64.b64encode(b'\x00\xff').decode()})
    async with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(IntegrationError) as error:
            await client.tool('a/b','read_file',{'path':'binary.dat'})
    assert error.value.kind=='unsupported'


@pytest.mark.asyncio
async def test_read_file_truncation_keeps_valid_utf8():
    value='a' * (200 * 1024 - 1) + 'é'
    def handler(request):
        if request.url.path=='/repos/a/b':
            return httpx.Response(200,json={'full_name':'a/b','private':False})
        return httpx.Response(200,json={'type':'file','encoding':'base64','content':base64.b64encode(value.encode()).decode()})
    async with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        result=await client.tool('a/b','read_file',{'path':'file.txt'})
    assert result['text']=='a' * (200 * 1024 - 1)
    assert result['coverage']['truncated'] is True


def test_subscription_sanitizes_clash_and_renders_fixed_config():
    text = '''mixed-port: 9999
external-controller: 0.0.0.0:9090
rules: [SCRIPT,evil]
proxies:
  - {name: alpha, type: trojan, server: example.com, port: 443, password: pass, script: evil}
'''
    nodes = parse_subscription(text)
    assert nodes == [{'name':'alpha','type':'trojan','server':'example.com','port':443,'password':'pass'}]
    config = render_mihomo_config(nodes,'alpha','controller-secret')
    assert 'mixed-port: 7890' in config
    assert '127.0.0.1:9090' in config
    assert '9999' not in config and 'SCRIPT' not in config and 'script:' not in config


def test_subscription_parses_base64_uri_list():
    uri = 'trojan://pass@example.com:443#node1'
    encoded = base64.b64encode(uri.encode()).decode()
    assert parse_subscription(encoded)[0]['name'] == 'node1'


def test_repository_parser_rejects_encoded_path_and_port():
    for url in ('https://github.com:443/a/b', 'https://github.com/a/%2e%2e', 'https://github.com/a/b%2fc'):
        with pytest.raises(IntegrationError):
            parse_repository(url)


@pytest.mark.asyncio
async def test_public_url_rejects_nonpublic_dns_answer(monkeypatch):
    async def lookup(*args, **kwargs):
        return [(2, 1, 6, '', ('192.168.1.5', 443))]
    monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', lookup)
    with pytest.raises(IntegrationError):
        await validate_public_url('https://internal.example/path')


@pytest.mark.asyncio
async def test_public_url_allows_custom_https_port_for_public_ip():
    assert await validate_public_url('https://1.1.1.1:55356/sub') == 'https://1.1.1.1:55356/sub'
    for url in ('https://1.1.1.1:abc/sub','https://1.1.1.1:0/sub'):
        with pytest.raises(IntegrationError):
            await validate_public_url(url)


@pytest.mark.asyncio
async def test_public_url_pin_uses_validated_ip_and_original_tls_host(monkeypatch):
    async def lookup(*args, **kwargs):
        return [(2,1,6,'',('1.1.1.1',443))]
    monkeypatch.setattr(asyncio.get_running_loop(),'getaddrinfo',lookup)
    target, headers, extensions = await pin_public_url('https://model.example/v1/chat/completions')
    assert target == 'https://1.1.1.1/v1/chat/completions'
    assert headers['Host'] == 'model.example'
    assert extensions['sni_hostname'] == 'model.example'


@pytest.mark.asyncio
async def test_public_ipv6_pin_preserves_bracketed_host():
    target, headers, extensions = await pin_public_url('https://[2606:4700:4700::1111]:443/sub')
    assert target == 'https://[2606:4700:4700::1111]:443/sub'
    assert headers['Host'] == '[2606:4700:4700::1111]:443'


@pytest.mark.asyncio
async def test_github_redirect_rejected_without_forwarding_token():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={'location':'https://evil.example/steal'})
    async with GitHubClient('token', transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(IntegrationError):
            await client.repository('a/b')
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_github_list_tools_do_not_require_issue_number():
    paths = []
    def handler(request):
        paths.append(request.url.path)
        if request.url.path == '/repos/a/b':
            return httpx.Response(200, json={'full_name':'a/b','private':False})
        return httpx.Response(200, json=[])
    async with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        for name in ('list_issues','list_pull_requests','list_commits'):
            result = await client.tool('a/b', name, {})
            assert result['items'] == []
    assert '/repos/a/b/issues' in paths and '/repos/a/b/pulls' in paths and '/repos/a/b/commits' in paths


@pytest.mark.asyncio
async def test_secondary_rate_limit_without_headers_has_retry_epoch():
    transport = httpx.MockTransport(lambda _: httpx.Response(403,json={'message':'You have exceeded a secondary rate limit.'}))
    async with GitHubClient(transport=transport) as client:
        with pytest.raises(IntegrationError) as error:
            await client.repository('a/b')
    assert error.value.kind == 'rate_limit'
    assert error.value.retry_at >= time.time() + 59


@pytest.mark.asyncio
async def test_search_issues_cannot_return_other_repository_results():
    def handler(request):
        if request.url.path == '/repos/a/b':
            return httpx.Response(200,json={'full_name':'a/b','private':False})
        return httpx.Response(200,json={'total_count':2,'items':[
            {'id':1,'repository_url':'https://api.github.com/repos/a/b'},
            {'id':2,'repository_url':'https://api.github.com/repos/evil/repo'}]})
    async with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        result = await client.tool('a/b','search_issues',{'query':'bug OR repo:evil/repo'})
    assert [item['id'] for item in result['items']] == [1]


@pytest.mark.asyncio
async def test_model_preserves_tool_call_shape():
    msg = {'role':'assistant', 'content':None, 'tool_calls':[{'id':'x','type':'function','function':{'name':'get_repository','arguments':'{}'}}]}
    async with ModelClient('https://model.example/v1','secret','m',transport=httpx.MockTransport(lambda _: httpx.Response(200,json={'choices':[{'message':msg}]}))) as client:
        assert await client.complete([{'role':'user','content':'hello'}], tools=[{'type':'function','function':{'name':'get_repository'}}]) == msg


@pytest.mark.asyncio
async def test_model_rejects_userinfo_even_with_injected_transport():
    async with ModelClient('https://name:secret@model.example/v1','key','m',transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        with pytest.raises(IntegrationError) as error:
            await client.complete([{'role':'user','content':'hi'}])
    assert error.value.kind == 'validation'


@pytest.mark.asyncio
async def test_model_request_uses_pinned_ip_after_dns_check(monkeypatch):
    async def lookup(*args, **kwargs):
        return [(2,1,6,'',('1.1.1.1',443))]
    monkeypatch.setattr(asyncio.get_running_loop(),'getaddrinfo',lookup)
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'ok'}}]})
    async with ModelClient('https://model.example/v1','key','m',transport=httpx.MockTransport(handler)) as client:
        client._injected=False
        await client.complete([{'role':'user','content':'hi'}])
    assert seen[0].url.host=='1.1.1.1'
    assert seen[0].headers['host']=='model.example'
    assert seen[0].extensions['sni_hostname']=='model.example'


def test_subscription_rejects_unsupported_or_unsafe_nodes():
    with pytest.raises(IntegrationError):
        parse_subscription('proxies:\n  - {name: bad, type: unsupported-protocol, server: example.com, port: 1080}')
    with pytest.raises(IntegrationError):
        parse_subscription('proxies:\n  - {name: bad, type: trojan, server: 127.0.0.1, port: 443, password: x}')
    with pytest.raises(IntegrationError):
        parse_subscription('proxies:\n  - {name: bad, type: vmess, server: example.com, port: 443, uuid: x, ws-opts: {headers: {Host: localhost}}}')


def test_subscription_supports_ss_and_vmess_uris():
    ss = 'ss://' + base64.urlsafe_b64encode(b'aes-128-gcm:secret@example.com:8388').decode().rstrip('=') + '#ssnode'
    vmess = 'vmess://' + base64.b64encode(json.dumps({'v':'2','ps':'vm','add':'example.com','port':'443','id':'00000000-0000-0000-0000-000000000001','aid':'0','net':'ws','path':'/ws','tls':'tls'}).encode()).decode()
    nodes = parse_subscription(base64.b64encode((ss+'\n'+vmess).encode()).decode())
    assert [node['type'] for node in nodes] == ['ss','vmess']
    assert nodes[0]['cipher'] == 'aes-128-gcm'
    assert nodes[1]['network'] == 'ws'


def test_malformed_subscription_uri_has_safe_error():
    with pytest.raises(IntegrationError) as error:
        parse_subscription('trojan://pass@example.com:notaport#name')
    assert error.value.kind == 'validation'


@pytest.mark.asyncio
async def test_subscription_download_rejects_redirect():
    transport = httpx.MockTransport(lambda _: httpx.Response(302, headers={'location':'https://example.com/next'}))
    with pytest.raises(IntegrationError):
        await download_subscription('https://example.com/sub', transport=transport)


@pytest.mark.asyncio
async def test_subscription_download_uses_pinned_ip(monkeypatch):
    async def lookup(*args, **kwargs):
        return [(2,1,6,'',('1.1.1.1',443))]
    monkeypatch.setattr(asyncio.get_running_loop(),'getaddrinfo',lookup)
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(200,text='trojan://pass@node.example:443#one')
    nodes=await download_subscription('https://feed.example:55356/sub?token=hidden',transport=httpx.MockTransport(handler),pin_injected=True)
    assert nodes[0]['name']=='one'
    assert seen[0].url.host=='1.1.1.1'
    assert seen[0].headers['host']=='feed.example:55356'
    assert seen[0].extensions['sni_hostname']=='feed.example'


@pytest.mark.asyncio
async def test_subscription_node_dns_is_pinned_before_sidecar(monkeypatch):
    async def lookup(*args, **kwargs):
        return [(2,1,6,'',('1.1.1.1',443))]
    monkeypatch.setattr(asyncio.get_running_loop(),'getaddrinfo',lookup)
    nodes=[{'name':'a','type':'trojan','server':'node.example','port':443,'password':'x'}]
    pinned=await pin_subscription_nodes(nodes)
    assert pinned[0]['server']=='1.1.1.1'
    assert pinned[0]['sni']=='node.example'
    assert nodes[0]['server']=='node.example'


@pytest.mark.asyncio
async def test_activation_requires_internal_controller(tmp_path):
    nodes = [{'name':'a','type':'trojan','server':'example.com','port':443,'password':'x'}]
    with pytest.raises(IntegrationError):
        await activate_mihomo(nodes,'a',None,'secret',tmp_path/'config.yaml',None)
    assert not (tmp_path/'config.yaml').exists()


@pytest.mark.asyncio
async def test_activation_restores_previous_config_when_probe_fails(tmp_path):
    path = tmp_path/'config.yaml'
    path.write_text(render_direct_mihomo_config('secret'))
    nodes = [{'name':'a','type':'trojan','server':'example.com','port':443,'password':'x'}]
    controller_calls = []
    def controller(request):
        controller_calls.append(request.url.path)
        if request.method == 'GET':
            return httpx.Response(200,json={'mixed-port':7890} if request.url.path=='/configs' else {'proxies':{}})
        return httpx.Response(204)
    def probe(request):
        return httpx.Response(503)
    with pytest.raises(IntegrationError):
        await activate_mihomo(nodes,'a','http://mihomo:9090','secret',path,'http://mihomo:7890',
                              transport=httpx.MockTransport(controller), probe_transport=httpx.MockTransport(probe))
    assert path.read_text() == render_direct_mihomo_config('secret')
    assert controller_calls.count('/configs') == 3


@pytest.mark.asyncio
async def test_activation_selects_node_after_successful_probe(tmp_path):
    path = tmp_path/'config.yaml'
    path.write_text(render_direct_mihomo_config('secret'))
    nodes = [{'name':'a','type':'trojan','server':'example.com','port':443,'password':'x'}]
    def controller(request):
        return httpx.Response(204)
    def probe(request):
        assert request.url.host == 'api.github.com'
        return httpx.Response(200, json={'resources':{}})
    result = await activate_mihomo(nodes,'a','http://mihomo:9090','secret',path,'http://mihomo:7890',
                                   transport=httpx.MockTransport(controller), probe_transport=httpx.MockTransport(probe))
    assert result['selected_node'] == 'a'
    assert path.exists()


@pytest.mark.asyncio
async def test_activation_requires_known_good_baseline_before_mutation(tmp_path):
    path = tmp_path/'missing.yaml'
    nodes = [{'name':'a','type':'trojan','server':'example.com','port':443,'password':'x'}]
    calls=[]
    def controller(request):
        calls.append(request)
        return httpx.Response(204)
    with pytest.raises(IntegrationError, match='baseline'):
        await activate_mihomo(nodes,'a','http://mihomo:9090','secret',path,'http://mihomo:7890',transport=httpx.MockTransport(controller))
    assert not calls and not path.exists()


@pytest.mark.asyncio
async def test_activation_rejects_existing_unmanaged_config(tmp_path):
    path=tmp_path/'config.yaml'
    path.write_text(render_direct_mihomo_config('secret')+'socks-port: 1080\n')
    nodes=[{'name':'a','type':'trojan','server':'example.com','port':443,'password':'x'}]
    with pytest.raises(IntegrationError,match='baseline'):
        await activate_mihomo(nodes,'a','http://mihomo:9090','secret',path,'http://mihomo:7890',transport=httpx.MockTransport(lambda _:httpx.Response(204)))


@pytest.mark.asyncio
async def test_activation_reports_rollback_failure_distinctly(tmp_path):
    path=tmp_path/'config.yaml'
    path.write_text(render_direct_mihomo_config('secret'))
    nodes=[{'name':'a','type':'trojan','server':'example.com','port':443,'password':'x'}]
    reloads=0
    def controller(request):
        nonlocal reloads
        if request.method=='PUT' and request.url.path=='/configs':
            reloads+=1
            return httpx.Response(500 if reloads==2 else 204)
        return httpx.Response(204)
    with pytest.raises(IntegrationError, match='rollback failed') as error:
        await activate_mihomo(nodes,'a','http://mihomo:9090','secret',path,'http://mihomo:7890',
                              transport=httpx.MockTransport(controller),probe_transport=httpx.MockTransport(lambda _:httpx.Response(503)))
    assert error.value.kind=='rollback'


@pytest.mark.asyncio
async def test_activation_before_commit_failure_rolls_back_and_propagates(tmp_path):
    path=tmp_path/'config.yaml'
    path.write_text(render_direct_mihomo_config('secret'))
    nodes=[{'name':'a','type':'trojan','server':'example.com','port':443,'password':'x'}]
    calls=[]
    def controller(request):
        calls.append((request.method,request.url.path))
        return httpx.Response(200,json={'mixed-port':7890} if request.url.path=='/configs' else {'proxies':{}}) if request.method=='GET' else httpx.Response(204)
    def guard():
        raise RuntimeError('stale revision')
    with pytest.raises(RuntimeError,match='stale revision'):
        await activate_mihomo(nodes,'a','http://mihomo:9090','secret',path,'http://mihomo:7890',
                              transport=httpx.MockTransport(controller),probe_transport=httpx.MockTransport(lambda _:httpx.Response(200)),before_commit=guard)
    assert path.read_text()==render_direct_mihomo_config('secret')
    assert calls.count(('PUT','/configs'))==2


@pytest.mark.asyncio
async def test_deactivation_clears_active_node_from_config(tmp_path):
    path=tmp_path/'config.yaml'
    nodes=[{'name':'a','type':'trojan','server':'example.com','port':443,'password':'x'}]
    path.write_text(render_mihomo_config(nodes,'a','secret'))
    def controller(request):
        return httpx.Response(204)
    result=await deactivate_mihomo('http://mihomo:9090','secret',path,transport=httpx.MockTransport(controller))
    assert result['selected_node'] is None
    assert 'password: x' not in path.read_text()
    assert 'MATCH,DIRECT' in path.read_text()


@pytest.mark.asyncio
async def test_rollback_detects_stale_running_proxy_state(tmp_path):
    path=tmp_path/'config.yaml'
    path.write_text(render_direct_mihomo_config('secret'))
    nodes=[{'name':'a','type':'trojan','server':'example.com','port':443,'password':'x'}]
    def controller(request):
        if request.method=='GET':
            return httpx.Response(200,json={'mixed-port':7890} if request.url.path=='/configs' else {'proxies':{'PROXY':{},'a':{}}})
        return httpx.Response(204)
    with pytest.raises(IntegrationError,match='rollback failed') as error:
        await activate_mihomo(nodes,'a','http://mihomo:9090','secret',path,'http://mihomo:7890',
                              transport=httpx.MockTransport(controller),probe_transport=httpx.MockTransport(lambda _:httpx.Response(503)))
    assert error.value.kind=='rollback'


def test_socks5_yaml_without_auth_round_trips_to_mihomo():
    import yaml
    nodes=parse_subscription('proxies:\n  - name: socks\n    type: socks5\n    server: proxy.example.com\n    port: 1080\n    udp: true\n')
    config=yaml.safe_load(render_mihomo_config(nodes,'socks','controller-test'))
    assert config['proxies'][0]==nodes[0]
    assert nodes[0]['type']=='socks5'
    assert 'password' not in nodes[0]


def test_socks5_optional_auth_preserved_and_unknown_fields_removed():
    import yaml
    node={'name':'socks','type':'socks5','server':'proxy.example.com','port':1080,'username':'user','password':'pass','dialer-proxy':'unsafe','udp':False}
    parsed=parse_subscription(yaml.safe_dump({'proxies':[node]}))[0]
    assert parsed['username']=='user' and parsed['password']=='pass'
    assert 'dialer-proxy' not in parsed


@pytest.mark.parametrize('extra', [
    {'username':'user'}, {'password':'pass'}, {'username':12,'password':'pass'},
    {'username':'user','password':''}, {'username':'x'*256,'password':'pass'},
    {'udp':'yes'}, {'tls':True}, {'tls':'false'},
])
def test_socks5_rejects_invalid_or_unsupported_options(extra):
    import yaml
    node={'name':'socks','type':'socks5','server':'proxy.example.com','port':1080,**extra}
    with pytest.raises(IntegrationError) as error:
        parse_subscription(yaml.safe_dump({'proxies':[node]}))
    assert error.value.kind=='validation'


@pytest.mark.asyncio
async def test_socks5_node_server_is_pinned_to_public_ip(monkeypatch):
    async def lookup(*args, **kwargs): return [(2,1,6,'',('1.1.1.1',1080))]
    monkeypatch.setattr(asyncio.get_running_loop(),'getaddrinfo',lookup)
    node={'name':'socks','type':'socks5','server':'proxy.example.com','port':1080}
    assert (await pin_subscription_nodes([node]))[0]['server']=='1.1.1.1'


@pytest.mark.parametrize('server', ['127.0.0.1', '::1', 'localhost', '10.0.0.1'])
def test_socks5_rejects_non_public_node_with_clear_error(server):
    import yaml
    with pytest.raises(IntegrationError, match='公网代理节点'):
        parse_subscription(yaml.safe_dump({'proxies':[{'name':'socks','type':'socks5','server':server,'port':1080}]}))
