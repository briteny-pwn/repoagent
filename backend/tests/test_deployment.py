import importlib.util
import os
from pathlib import Path
import pytest
from starlette.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from test_api import make_client


ROOT=Path(__file__).resolve().parents[2]


def test_generated_configuration_has_private_permissions_and_no_overwrite(tmp_path):
    spec=importlib.util.spec_from_file_location('initialize',ROOT/'scripts'/'init_deployment.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    (tmp_path/'.env.example').write_text((ROOT/'.env.example').read_text())
    module.initialize(tmp_path,'steward.example.com')
    env=(tmp_path/'.env').read_text()
    assert 'ADMIN_PASSWORD=\n' not in env
    assert 'ENCRYPTION_KEY=\n' not in env
    assert os.stat(tmp_path/'.env').st_mode & 0o777 == 0o600
    assert 'MATCH,DIRECT' in (tmp_path/'data/proxy/config.yaml').read_text()
    with pytest.raises(FileExistsError):module.initialize(tmp_path,'other.example.com')
    assert (tmp_path/'.env').read_text()==env


def test_trusted_proxy_isolates_login_failure_budgets(tmp_path):
    _,app=make_client(tmp_path)
    wrapped=ProxyHeadersMiddleware(app,trusted_hosts=['172.30.84.2'])
    c=TestClient(wrapped,client=('172.30.84.2',12345))
    for _ in range(10):
        assert c.post('/api/auth/login',headers={'X-Forwarded-For':'203.0.113.1'},json={'password':'bad'}).status_code==401
    assert c.post('/api/auth/login',headers={'X-Forwarded-For':'203.0.113.1'},json={'password':'bad'}).status_code==429
    assert c.post('/api/auth/login',headers={'X-Forwarded-For':'203.0.113.2'},json={'password':'correct-horse-long-pass'}).status_code==200


def test_untrusted_clients_cannot_spoof_forwarded_ip(tmp_path):
    _,app=make_client(tmp_path)
    wrapped=ProxyHeadersMiddleware(app,trusted_hosts=['172.30.84.2'])
    c=TestClient(wrapped,client=('203.0.113.10',12345))
    for n in range(10):
        assert c.post('/api/auth/login',headers={'X-Forwarded-For':f'203.0.113.{n+20}'},json={'password':'bad'}).status_code==401
    assert c.post('/api/auth/login',headers={'X-Forwarded-For':'203.0.113.250'},json={'password':'bad'}).status_code==429
