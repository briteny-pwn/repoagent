"""Normalize subscription nodes and manage the optional Mihomo sidecar."""

import base64
import inspect
import ipaddress
import json
import os
import re
import ssl
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
import yaml

from .integrations import IntegrationError, validate_public_url, pin_public_url


_LIMIT = 2 * 1024 * 1024
_CIPHERS = {'aes-128-gcm', 'aes-192-gcm', 'aes-256-gcm', 'chacha20-ietf-poly1305', 'xchacha20-ietf-poly1305'}
_ALLOWED = {
    'socks5': {'username', 'password', 'udp', 'tls'},
    'trojan': {'password', 'sni', 'udp'},
    'ss': {'cipher', 'password', 'udp'},
    'vmess': {'uuid', 'alterId', 'cipher', 'tls', 'servername', 'network', 'ws-opts', 'udp'},
}


def _b64(text):
    try:
        return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4)).decode('utf-8')
    except (ValueError, UnicodeError):
        raise IntegrationError('Invalid subscription encoding.', kind='validation') from None


def _server(value):
    if not isinstance(value, str) or not value or len(value) > 253 or any(c in value for c in '/?#@ \\'):
        raise IntegrationError('Invalid node server.', kind='validation')
    host = value.strip('[]').lower().rstrip('.')
    if host == 'localhost' or host.endswith('.localhost'):
        raise IntegrationError('订阅包含回环或非公网地址，不能作为公网代理节点导入；请检查订阅服务返回的节点。', kind='validation')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', host):
            raise IntegrationError('Invalid node server.', kind='validation') from None
    else:
        if not address.is_global:
            raise IntegrationError('订阅包含回环或非公网地址，不能作为公网代理节点导入；请检查订阅服务返回的节点。', kind='validation')
    return value


def _normalize(raw):
    if not isinstance(raw, dict):
        raise IntegrationError('Invalid subscription node.', kind='validation')
    kind = raw.get('type')
    if kind not in _ALLOWED:
        raise IntegrationError('Unsupported subscription protocol.', kind='validation')
    name = raw.get('name')
    port = raw.get('port')
    if not isinstance(name, str) or not name.strip() or len(name) > 100 or any(ord(c) < 32 for c in name):
        raise IntegrationError('Invalid node name.', kind='validation')
    if isinstance(port, bool) or not str(port).isdigit() or not 1 <= int(port) <= 65535:
        raise IntegrationError('Invalid node port.', kind='validation')
    node = {'name': name.strip(), 'type': kind, 'server': _server(raw.get('server')), 'port': int(port)}
    for key in _ALLOWED[kind]:
        if key in raw:
            node[key] = raw[key]
    if kind == 'socks5':
        if 'tls' in node and node['tls'] is not False:
            raise IntegrationError('SOCKS5 over TLS 暂不支持，请使用普通 SOCKS5 节点。', kind='validation')
        if 'udp' in node and type(node['udp']) is not bool:
            raise IntegrationError('Invalid SOCKS5 UDP option.', kind='validation')
        if 'username' in node or 'password' in node:
            for field in ('username', 'password'):
                value = node.get(field)
                if not isinstance(value, str) or not 1 <= len(value.encode('utf-8')) <= 255:
                    raise IntegrationError('SOCKS5 用户名和密码须同时提供，长度为 1–255 字节。', kind='validation')
        return node
    required = {'trojan': 'password', 'ss': 'password', 'vmess': 'uuid'}[kind]
    if not isinstance(node.get(required), str) or not node[required]:
        raise IntegrationError('Subscription node lacks credentials.', kind='validation')
    if kind == 'ss' and node.get('cipher') not in _CIPHERS:
        raise IntegrationError('Unsupported Shadowsocks cipher.', kind='validation')
    if kind == 'vmess':
        node.setdefault('alterId', 0)
        node.setdefault('cipher', 'auto')
        if node.get('network', 'tcp') not in ('tcp', 'ws'):
            raise IntegrationError('Unsupported VMess network.', kind='validation')
        if 'ws-opts' in node:
            options = node['ws-opts']
            if not isinstance(options, dict) or set(options) - {'path'} or not isinstance(options.get('path'), str) or not options['path'].startswith('/') or len(options['path']) > 500:
                raise IntegrationError('Unsupported VMess WebSocket options.', kind='validation')
        if node.get('tls', False) not in (True, False):
            raise IntegrationError('Invalid VMess TLS setting.', kind='validation')
    return node


def _uri(line):
    if line.startswith('trojan://'):
        parsed = urlsplit(line)
        return {'name': unquote(parsed.fragment) or 'trojan', 'type':'trojan', 'server':parsed.hostname,
                'port':parsed.port, 'password':unquote(parsed.username or '')}
    if line.startswith('ss://'):
        parsed = urlsplit(line)
        if '@' in line[5:].split('#', 1)[0]:
            user = unquote(parsed.username or '')
            credentials = _b64(user) if ':' not in user else user
            server, port = parsed.hostname, parsed.port
        else:
            decoded = _b64(line[5:].split('#', 1)[0])
            credentials, hostport = decoded.rsplit('@', 1)
            server, port = hostport.rsplit(':', 1)
        cipher, password = credentials.split(':', 1)
        return {'name':unquote(parsed.fragment) or 'ss', 'type':'ss', 'server':server,
                'port':port, 'cipher':cipher, 'password':password}
    if line.startswith('vmess://'):
        try:
            data = json.loads(_b64(line[8:]))
            node = {'name':data.get('ps') or 'vmess', 'type':'vmess', 'server':data['add'],
                    'port':data['port'], 'uuid':data['id'], 'alterId':int(data.get('aid', 0)),
                    'cipher':data.get('scy') or 'auto'}
            if data.get('tls') == 'tls':
                node['tls'] = True
            if data.get('sni'):
                node['servername'] = data['sni']
            if data.get('net') == 'ws':
                node['network'] = 'ws'
                node['ws-opts'] = {'path':data.get('path') or '/'}
            return node
        except (KeyError, ValueError, TypeError):
            raise IntegrationError('Invalid VMess node.', kind='validation') from None
    raise IntegrationError('Unsupported subscription protocol.', kind='validation')


def parse_subscription(text: str) -> list[dict]:
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > _LIMIT:
        raise IntegrationError('Subscription is empty or too large.', kind='validation')
    source = text.strip()
    if not (re.search(r'(?m)^proxies:\s*$', source) or source.startswith(('trojan://', 'ss://', 'vmess://'))):
        source = _b64(source)
    if source.lstrip().startswith('proxies:') or '\nproxies:' in source:
        try:
            data = yaml.safe_load(source)
        except yaml.YAMLError:
            raise IntegrationError('Invalid subscription YAML.', kind='validation') from None
        if not isinstance(data, dict) or not isinstance(data.get('proxies'), list):
            raise IntegrationError('Subscription has no proxies.', kind='validation')
        raw = data['proxies']
    else:
        try:
            raw = [_uri(line.strip()) for line in source.splitlines() if line.strip()]
        except (ValueError, TypeError, IndexError):
            raise IntegrationError('Invalid subscription URI.', kind='validation') from None
    if not raw or len(raw) > 200:
        raise IntegrationError('Subscription node count is invalid.', kind='validation')
    nodes = [_normalize(item) for item in raw]
    if len({node['name'] for node in nodes}) != len(nodes):
        raise IntegrationError('Duplicate subscription node name.', kind='validation')
    return nodes


async def download_subscription(url: str, proxy_url: str | None = None, transport=None, *, pin_injected: bool = False) -> list[dict]:
    headers, extensions = None, None
    if transport is None or pin_injected:
        url, headers, extensions = await pin_public_url(url)
    elif urlsplit(url).scheme != 'https' or urlsplit(url).username or urlsplit(url).fragment:
        raise IntegrationError('Subscription URL must use public HTTPS.', kind='validation')
    try:
        async with httpx.AsyncClient(proxy=proxy_url, transport=transport, timeout=httpx.Timeout(30, connect=5), follow_redirects=False, trust_env=False) as client:
            async with client.stream('GET', url, headers=headers, extensions=extensions) as response:
                if response.status_code != 200:
                    raise IntegrationError(f'订阅下载失败：HTTP {response.status_code}；请检查订阅地址、有效期或服务商访问限制。')
                chunks = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > _LIMIT:
                        raise IntegrationError('Subscription is too large.', kind='validation')
                    chunks.append(chunk)
        return parse_subscription(b''.join(chunks).decode('utf-8'))
    except httpx.ConnectTimeout:
        raise IntegrationError('订阅下载连接超时：服务器尚未取得订阅内容，请检查订阅服务地址或出站网络。', kind='network') from None
    except httpx.ReadTimeout:
        raise IntegrationError('订阅内容读取超时：请稍后重试或检查订阅服务状态。', kind='network') from None
    except (httpx.ConnectError, ssl.SSLError) as exc:
        cause = exc
        tls_failure = False
        while cause is not None:
            if isinstance(cause, ssl.SSLError):
                tls_failure = True
                break
            cause = cause.__cause__
        message = ('订阅 HTTPS 握手或证书验证失败：请使用证书有效的订阅地址。' if tls_failure
                   else '无法连接订阅服务：请检查地址、端口、HTTPS 服务及服务器出站网络。')
        raise IntegrationError(message, kind='network') from None
    except UnicodeError:
        raise IntegrationError('订阅内容不是有效的 UTF-8 文本，无法解析。', kind='validation') from None
    except httpx.HTTPError:
        raise IntegrationError('订阅传输失败：尚未完成下载，请检查网络后重试。', kind='network') from None


def render_mihomo_config(nodes, selected, controller_secret) -> str:
    clean = [_normalize(node) for node in nodes]
    if selected not in {node['name'] for node in clean}:
        raise IntegrationError('Selected node is unavailable.', kind='validation')
    if not controller_secret or not isinstance(controller_secret, str):
        raise IntegrationError('Controller secret is required.', kind='validation')
    config = {'mixed-port':7890, 'allow-lan':False, 'bind-address':'127.0.0.1',
              'external-controller':'127.0.0.1:9090', 'secret':controller_secret,
              'proxies':clean, 'proxy-groups':[{'name':'PROXY','type':'select','proxies':[selected] + [n['name'] for n in clean if n['name'] != selected]}],
              'rules':['MATCH,PROXY']}
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)


async def pin_subscription_nodes(nodes: list[dict]) -> list[dict]:
    """Resolve every node to a public IP and keep TLS server names intact."""
    pinned = []
    for original in nodes:
        node = _normalize(original).copy()
        hostname = node['server']
        address = f'[{hostname}]' if ':' in hostname and not hostname.startswith('[') else hostname
        target, _, _ = await pin_public_url(f'https://{address}:{node["port"]}/')
        node['server'] = urlsplit(target).hostname
        if node['server'] != hostname:
            if node['type'] == 'trojan':
                node.setdefault('sni', hostname)
            elif node['type'] == 'vmess' and node.get('tls'):
                node.setdefault('servername', hostname)
        pinned.append(node)
    return pinned


def render_direct_mihomo_config(controller_secret: str) -> str:
    if not controller_secret:
        raise IntegrationError('Controller secret is required.', kind='validation')
    return yaml.safe_dump({'mixed-port':7890, 'allow-lan':False, 'bind-address':'127.0.0.1',
                           'external-controller':'127.0.0.1:9090', 'secret':controller_secret,
                           'mode':'rule', 'rules':['MATCH,DIRECT']}, sort_keys=False)


def _previous_config(target: Path, controller_secret: str) -> bytes:
    try:
        previous = target.read_bytes()
        data = yaml.safe_load(previous)
    except (OSError, yaml.YAMLError):
        raise IntegrationError('Known-good Mihomo baseline is required.', kind='configuration') from None
    valid = isinstance(data, dict) and data == yaml.safe_load(render_direct_mihomo_config(controller_secret))
    if isinstance(data, dict) and not valid:
        try:
            groups = data['proxy-groups']
            selected = groups[0]['proxies'][0]
            valid = data == yaml.safe_load(render_mihomo_config(data['proxies'], selected, controller_secret))
        except (KeyError, IndexError, TypeError, IntegrationError):
            valid = False
    if not valid:
        raise IntegrationError('Known-good Mihomo baseline is required.', kind='configuration')
    return previous


def _atomic_write(target: Path, data: bytes):
    fd, candidate = tempfile.mkstemp(dir=target.parent, prefix='.mihomo-', suffix='.yaml')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(candidate, target)
    finally:
        Path(candidate).unlink(missing_ok=True)


async def _reload_controller(controller_url, controller_secret, target, transport):
    async with httpx.AsyncClient(transport=transport, timeout=10, follow_redirects=False, trust_env=False,
                                 headers={'Authorization':f'Bearer {controller_secret}'}) as client:
        response = await client.put(controller_url.rstrip('/') + '/configs', params={'force':'true'}, json={'path':str(target)})
        response.raise_for_status()


async def _restore(previous, target, controller_url, controller_secret, transport):
    try:
        _atomic_write(target, previous)
        await _reload_controller(controller_url, controller_secret, target, transport)
        async with httpx.AsyncClient(transport=transport, timeout=10, follow_redirects=False, trust_env=False,
                                     headers={'Authorization':f'Bearer {controller_secret}'}) as client:
            response = await client.get(controller_url.rstrip('/') + '/configs')
            response.raise_for_status()
            state = response.json()
            expected = yaml.safe_load(previous)
            if not isinstance(state, dict) or state.get('mixed-port') != expected['mixed-port']:
                raise ValueError('Unexpected controller state')
            response = await client.get(controller_url.rstrip('/') + '/proxies')
            response.raise_for_status()
            running = response.json().get('proxies')
            if not isinstance(running, dict):
                raise ValueError('Proxy state unavailable')
            expected_names = {node['name'] for node in expected.get('proxies', [])}
            if expected.get('rules') == ['MATCH,PROXY']:
                expected_names.add('PROXY')
            if not expected_names.issubset(running) or (expected.get('rules') == ['MATCH,DIRECT'] and 'PROXY' in running):
                raise ValueError('Proxy state differs from restored config')
    except (OSError, httpx.HTTPError, ValueError, yaml.YAMLError):
        raise IntegrationError('Node activation rollback failed; inspect Mihomo state.', kind='rollback') from None


async def _deploy(config, controller_url, controller_secret, config_path, proxy_url, transport, probe_transport, before_commit, probe):
    if not controller_url:
        raise IntegrationError('服务器尚未配置 Mihomo 代理组件，无法启用或清理代理。', kind='configuration')
    if probe and not proxy_url:
        raise IntegrationError('Mihomo proxy is not configured.', kind='configuration')
    parsed = urlsplit(controller_url)
    if parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1', 'mihomo') or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise IntegrationError('Invalid internal controller address.', kind='validation')
    target = Path(config_path)
    previous = _previous_config(target, controller_secret)
    _atomic_write(target, config.encode('utf-8'))
    try:
        headers = {'Authorization':f'Bearer {controller_secret}'}
        try:
            async with httpx.AsyncClient(transport=transport, timeout=10, follow_redirects=False, trust_env=False, headers=headers) as client:
                response = await client.put(controller_url.rstrip('/') + '/configs', params={'force':'true'}, json={'path':str(target)})
                response.raise_for_status()
                if probe:
                    response = await client.put(controller_url.rstrip('/') + '/proxies/PROXY', json={'name':probe})
                    response.raise_for_status()
            if probe:
                async with httpx.AsyncClient(proxy=proxy_url if probe_transport is None else None,
                                             transport=probe_transport, timeout=10, follow_redirects=False, trust_env=False) as client:
                    response = await client.get('https://api.github.com/rate_limit')
                    response.raise_for_status()
            if before_commit is not None:
                outcome = before_commit()
                if inspect.isawaitable(outcome):
                    await outcome
        except BaseException as original:
            await _restore(previous, target, controller_url, controller_secret, transport)
            if isinstance(original, httpx.HTTPError):
                raise IntegrationError('Node activation failed; previous configuration restored.', kind='network') from None
            raise
    except BaseException:
        raise


async def activate_mihomo(nodes, selected, controller_url, controller_secret, config_path, proxy_url, transport=None, probe_transport=None, before_commit=None) -> dict:
    if transport is None:
        nodes = await pin_subscription_nodes(nodes)
    config = render_mihomo_config(nodes, selected, controller_secret)
    await _deploy(config, controller_url, controller_secret, config_path, proxy_url, transport, probe_transport, before_commit, selected)
    return {'ok':True, 'selected_node':selected}


async def deactivate_mihomo(controller_url, controller_secret, config_path, transport=None, before_commit=None) -> dict:
    config = render_direct_mihomo_config(controller_secret)
    await _deploy(config, controller_url, controller_secret, config_path, None, transport, None, before_commit, None)
    return {'ok':True, 'selected_node':None}
