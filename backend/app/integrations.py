"""Bounded, public-only remote integrations. No persistence or application imports."""

import asyncio
import base64
import binascii
import inspect
import ipaddress
import random
import re
import socket
import time
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlsplit, urlunsplit

import httpx


class IntegrationError(Exception):
    def __init__(self, message: str, *, kind: str = 'remote', retry_at: float | None = None, ambiguous: bool = False):
        super().__init__(message)
        self.kind = kind
        self.retry_at = retry_at
        self.ambiguous = ambiguous


_PART = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$')


def parse_repository(url: str) -> str:
    try:
        parsed = urlsplit(url.strip())
        if parsed.scheme != 'https' or parsed.netloc.lower() != 'github.com' or parsed.query or parsed.fragment:
            raise ValueError
        path = parsed.path.rstrip('/')
        if path.endswith('.git'):
            path = path[:-4]
        parts = path.strip('/').split('/')
        if len(parts) != 2 or not all(_PART.fullmatch(p) for p in parts):
            raise ValueError
        return '/'.join(parts)
    except (ValueError, AttributeError):
        raise IntegrationError('Enter a public github.com repository URL.', kind='validation') from None


def _repo(repo: str) -> str:
    if isinstance(repo, str) and len(repo.split('/')) == 2 and all(_PART.fullmatch(p) for p in repo.split('/')):
        return repo
    raise IntegrationError('Invalid repository identifier.', kind='validation')


async def _public_address(url: str):
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError
        port = parsed.port
        if port is not None and port < 1:
            raise ValueError
        host = parsed.hostname.rstrip('.').lower()
        if not host or host == 'localhost' or host.endswith('.localhost'):
            raise ValueError
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            infos = await asyncio.get_running_loop().getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
            addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
        if not addresses or not all(addr.is_global for addr in addresses):
            raise ValueError
        return parsed, addresses[0]
    except (ValueError, OSError, UnicodeError):
        raise IntegrationError('Address must resolve to a public HTTPS endpoint.', kind='validation') from None


async def validate_public_url(url: str) -> str:
    await _public_address(url)
    return url


async def pin_public_url(url: str):
    """Return an IP URL while retaining the verified hostname for Host and TLS."""
    parsed, address = await _public_address(url)
    ip = f'[{address}]' if address.version == 6 else str(address)
    port = parsed.port
    authority = ip + (f':{port}' if port is not None else '')
    original_host = f'[{parsed.hostname}]' if ':' in parsed.hostname else parsed.hostname
    host = original_host + (f':{port}' if port is not None else '')
    target = urlunsplit(('https', authority, parsed.path, parsed.query, ''))
    return target, {'Host': host}, {'sni_hostname': parsed.hostname}


def _rate_limit(response: httpx.Response) -> IntegrationError | None:
    if response.status_code not in (403, 429):
        return None
    headers = response.headers
    secondary = response.status_code == 403 and any(term in response.text[:500].lower() for term in ('secondary rate limit', 'abuse detection'))
    is_limit = response.status_code == 429 or headers.get('x-ratelimit-remaining') == '0' or 'retry-after' in headers or secondary
    if not is_limit:
        return None
    now = time.time()
    retry_at = now + 60
    retry = headers.get('retry-after')
    if retry:
        try:
            retry_at = now + max(0, float(retry))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry).timestamp()
            except (TypeError, ValueError, OverflowError):
                pass
    elif headers.get('x-ratelimit-reset'):
        try:
            retry_at = max(now, float(headers['x-ratelimit-reset']))
        except ValueError:
            pass
    return IntegrationError('GitHub rate limit reached.', kind='rate_limit', retry_at=retry_at)


class GitHubClient:
    def __init__(self, token: str = '', proxy_url: str | None = None, transport=None):
        self._token = token
        self._client = httpx.AsyncClient(base_url='https://api.github.com', transport=transport, proxy=proxy_url, trust_env=False,
                                         timeout=httpx.Timeout(30, connect=5), follow_redirects=False,
                                         headers={'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28',
                                                  **({'Authorization': f'Bearer {token}'} if token else {})})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self._client.aclose()

    async def _request(self, method: str, path: str, *, params=None, json=None):
        attempts = 3 if method == 'GET' else 1
        for attempt in range(attempts):
            try:
                response = await self._client.request(method, path, params=params, json=json)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.ProxyError) as exc:
                if method == 'GET' and attempt + 1 < attempts:
                    await asyncio.sleep(min(2 ** attempt + random.random(), 3))
                    continue
                raise IntegrationError('GitHub connection failed.', kind='network', ambiguous=method != 'GET') from None
            rate = _rate_limit(response)
            if rate:
                hint = '请等待配额恢复。' if self._token else '当前未配置 GitHub 访问令牌，匿名配额由出口 IP 共享；可在设置中配置令牌后重新初始化。'
                raise IntegrationError('GitHub API 请求已达限额。'+hint, kind='rate_limit', retry_at=rate.retry_at)
            if method == 'GET' and response.status_code in (500, 502, 503, 504) and attempt + 1 < attempts:
                await asyncio.sleep(min(2 ** attempt + random.random(), 3))
                continue
            if response.status_code >= 300:
                kind = 'not_found' if response.status_code == 404 else 'auth' if response.status_code in (401, 403) else 'remote'
                status = response.status_code
                reasons = {401: '认证失败，请检查 GitHub 访问令牌',
                           403: '请求被拒绝，请检查访问权限或 GitHub 访问限制',
                           404: '资源不存在或不可访问，请检查仓库、文件路径或分支',
                           422: '请求参数无效，请检查工具参数'}
                reason = reasons.get(status, '发生重定向，请检查仓库地址' if 300 <= status < 400 else
                                     'GitHub 服务暂时异常，请稍后重试' if status >= 500 else 'GitHub 拒绝了请求')
                raise IntegrationError(f'{reason}（HTTP {status}）', kind=kind, ambiguous=method != 'GET' and status >= 500)
            try:
                return response.json()
            except ValueError:
                raise IntegrationError('GitHub returned invalid data.', ambiguous=method != 'GET') from None
        raise IntegrationError('GitHub connection failed.', kind='network')

    async def repository(self, repo: str) -> dict:
        data = await self._request('GET', f'/repos/{_repo(repo)}')
        if not isinstance(data, dict) or data.get('private') is not False:
            raise IntegrationError('Repository is not public.', kind='private')
        return data

    async def directory(self, repo: str) -> list[dict]:
        """Return at most 1000 root entries, after rechecking public access."""
        repo = _repo(repo)
        await self.repository(repo)
        data = await self._request('GET', f'/repos/{repo}/contents')
        if not isinstance(data, list):
            raise IntegrationError('GitHub returned invalid directory data.')
        fields = ('name', 'path', 'type', 'html_url', 'size', 'sha')
        return [{key: item.get(key) for key in fields} for item in data[:1000]
                if isinstance(item, dict)]

    async def _list(self, path, params):
        items = []
        pages = 0
        truncated = False
        for page in (1, 2):
            data = await self._request('GET', path, params={**params, 'per_page': 50, 'page': page})
            if not isinstance(data, list):
                raise IntegrationError('GitHub returned invalid list data.')
            items.extend(data)
            pages += 1
            if len(data) < 50:
                break
        else:
            truncated = True
        return {'items': items, 'coverage': {'pages': pages, 'per_page': 50, 'truncated': truncated}}

    async def tool(self, repo: str, name: str, arguments: dict) -> dict | list:
        repo = _repo(repo)
        await self.repository(repo)
        args = arguments if isinstance(arguments, dict) else {}
        base = f'/repos/{repo}'
        if name == 'get_repository':
            return await self.repository(repo)
        if name == 'get_issue':
            return await self._request('GET', f'{base}/issues/{_number(args.get("number"))}')
        if name == 'read_file':
            path = args.get('path', '')
            if not isinstance(path, str) or not path or path.startswith('/') or '..' in path.split('/'):
                raise IntegrationError('Invalid file path.', kind='validation')
            data = await self._request('GET', f'{base}/contents/{quote(path, safe="/")}', params={'ref': args['ref']} if args.get('ref') else None)
            if not isinstance(data, dict) or data.get('type') != 'file' or data.get('encoding') != 'base64' or not isinstance(data.get('content'), str):
                raise IntegrationError('Unsupported repository file.', kind='unsupported')
            encoded = ''.join(data['content'].split())
            limit = 200 * 1024
            cutoff = ((limit + 2) // 3) * 4
            try:
                decoded = base64.b64decode(encoded[:cutoff], validate=True)
                truncated = len(encoded) > cutoff or len(decoded) > limit
                snippet = decoded[:limit]
                try:
                    text = snippet.decode('utf-8')
                except UnicodeDecodeError as exc:
                    if not truncated or exc.reason != 'unexpected end of data' or exc.start < limit - 3:
                        raise
                    text = snippet[:exc.start].decode('utf-8')
            except (binascii.Error, UnicodeError):
                raise IntegrationError('Binary or invalid repository file.', kind='unsupported') from None
            if '\x00' in text:
                raise IntegrationError('Binary repository file.', kind='unsupported')
            return {'path':data.get('path', path), 'sha':data.get('sha'), 'html_url':data.get('html_url'),
                    'text':text, 'coverage':{'bytes_returned':len(decoded[:limit]), 'size':data.get('size'),
                                           'truncated':truncated}}
        if name == 'search_issues':
            query = str(args.get('query', ''))[:200]
            if not query:
                raise IntegrationError('Search query is required.', kind='validation')
            # GitHub requires an explicit record type for this authenticated search.
            query = re.sub(r'(?<!\S)is:pr(?=\s|$)', 'is:pull-request', query, flags=re.I)
            if not re.search(r'(?<!\S)is:(?:issue|pull-request)(?=\s|$)', query, re.I):
                query = 'is:issue ' + query
            data = await self._request('GET', '/search/issues', params={'q': f'repo:{repo} {query}', 'per_page': 50})
            if not isinstance(data, dict):
                raise IntegrationError('GitHub returned invalid search data.')
            items = [item for item in data.get('items', []) if isinstance(item, dict) and item.get('repository_url', '').lower() == f'https://api.github.com/repos/{repo}'.lower()]
            return {'items': items, 'coverage': {'total_count': data.get('total_count', 0), 'per_page': 50, 'truncated': data.get('total_count', 0) > 50, 'returned_in_repository': len(items)}}
        paths = {'list_issues': f'{base}/issues', 'list_pull_requests': f'{base}/pulls', 'list_commits': f'{base}/commits'}
        if name == 'list_comments':
            paths[name] = f'{base}/issues/{_number(args.get("number"))}/comments'
        if name not in paths:
            raise IntegrationError('Unknown GitHub tool.', kind='validation')
        params = {key: args[key] for key in ('state', 'sort', 'direction', 'since', 'sha') if key in args and isinstance(args[key], str)}
        return await self._list(paths[name], params)

    async def publish(self, repo: str, kind: str, title: str, body: str, issue_number: int | None = None, *, before_write=None) -> dict:
        repo = _repo(repo)
        await self.repository(repo)
        if kind == 'issue':
            if not title or not body:
                raise IntegrationError('Issue title and body are required.', kind='validation')
            path, payload = f'/repos/{repo}/issues', {'title': title, 'body': body}
        elif kind == 'comment':
            if not body:
                raise IntegrationError('Comment body is required.', kind='validation')
            number = _number(issue_number)
            target = await self._request('GET', f'/repos/{repo}/issues/{number}')
            if not isinstance(target, dict) or target.get('pull_request') or target.get('state') != 'open':
                raise IntegrationError('Comment target must be an open issue.', kind='validation')
            path, payload = f'/repos/{repo}/issues/{number}/comments', {'body': body}
        else:
            raise IntegrationError('Unknown publication kind.', kind='validation')
        if before_write is not None:
            result = before_write()
            if inspect.isawaitable(result):
                await result
        return await self._request('POST', path, json=payload)

    async def find_operation(self, repo: str, marker: str, issue_number: int | None = None) -> dict | None:
        repo = _repo(repo)
        await self.repository(repo)
        if not marker or len(marker) > 200:
            raise IntegrationError('Invalid operation marker.', kind='validation')
        path = f'/repos/{repo}/issues/{_number(issue_number)}/comments' if issue_number is not None else f'/repos/{repo}/issues'
        for item in (await self._list(path, {}))['items']:
            if marker in str(item.get('body', '')):
                return item
        return None

    async def diagnose(self, repo: str | None = None) -> dict:
        data = await self.repository(repo) if repo else await self._request('GET', '/rate_limit')
        return {'ok': True, 'repository': data.get('full_name') if repo else None, 'public': True if repo else None}


def _number(value):
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    raise IntegrationError('Valid issue number required.', kind='validation')


class ModelClient:
    def __init__(self, base_url, api_key, model, transport=None):
        self.base_url = str(base_url).rstrip('/')
        self.model = model
        self._injected = transport is not None
        self._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(60, connect=5, read=300), follow_redirects=False, trust_env=False,
                                         headers={'Authorization': f'Bearer {api_key}', 'Content-Type':'application/json'})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self._client.aclose()

    async def complete(self, messages: list, tools: list | None = None) -> dict:
        url = self.base_url + '/chat/completions'
        base = urlsplit(self.base_url)
        if base.scheme != 'https' or not base.hostname or base.username or base.password or base.query or base.fragment:
            raise IntegrationError('Invalid model base URL.', kind='validation')
        headers, extensions = None, None
        if not self._injected:
            url, headers, extensions = await pin_public_url(url)
        payload = {'model': self.model, 'messages': messages, 'max_tokens': 4096}
        if tools:
            payload['tools'] = tools
        try:
            response = await self._client.post(url, json=payload, headers=headers, extensions=extensions)
        except httpx.ReadTimeout:
            raise IntegrationError('模型响应超时：等待生成结果超过300秒，可稍后继续任务或选择响应更快的模型。', kind='network') from None
        except httpx.ConnectTimeout:
            raise IntegrationError('连接模型服务超时，请检查模型接口地址与服务器网络。', kind='network') from None
        except (httpx.TimeoutException, httpx.NetworkError, httpx.ProxyError):
            raise IntegrationError('模型请求传输失败，请检查服务器网络后继续任务。', kind='network') from None
        if response.status_code >= 300:
            raise IntegrationError('Model request failed.', kind='auth' if response.status_code in (401, 403) else 'remote')
        try:
            message = response.json()['choices'][0]['message']
            return {key: message[key] for key in ('role', 'content', 'tool_calls') if key in message}
        except (ValueError, KeyError, IndexError, TypeError):
            raise IntegrationError('Model returned invalid data.') from None

    async def test(self) -> dict:
        message = await self.complete([{'role':'user','content':'Reply OK.'}])
        return {'ok': True, 'message': 'Model connection succeeded.' if message else 'Model returned no message.'}
