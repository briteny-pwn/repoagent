"""Deterministic, bounded public repository snapshots for project initialization."""

import inspect
from datetime import datetime, timezone

from .integrations import IntegrationError


_METADATA = ('full_name', 'name', 'description', 'html_url', 'default_branch', 'language',
             'topics', 'stargazers_count', 'forks_count', 'open_issues_count', 'archived',
             'created_at', 'updated_at', 'pushed_at', 'visibility', 'private', 'has_issues')
_CONFIGS = ('package.json', 'pyproject.toml', 'requirements.txt', 'cargo.toml', 'go.mod',
            'agents.md', 'contributing.md')


async def _callback(callback, *args):
    result = callback(*args)
    if inspect.isawaitable(result):
        return await result
    return result


async def collect_project_snapshot(github, repo: str, check, event) -> dict:
    """Collect fixed evidence; callbacks may be synchronous or asynchronous.

    check() runs around every IO, including failed IO. event(kind, message) receives
    concise progress messages. Auth/network/rate-limit errors remain retryable by
    the worker; only absent or unsupported file contents are optional.
    """
    async def io(method, *args):
        await _callback(check)
        try:
            return await method(*args)
        finally:
            await _callback(check)

    await _callback(event, 'progress', '正在读取仓库信息与根目录')
    raw = await io(github.repository, repo)
    metadata = {key: raw[key] for key in _METADATA if key in raw}
    limitations = ['资料为采集时点快照；仅包含根目录及选定文件，不代表完整代码审查。']
    try:
        directory = (await io(github.directory, repo))[:1000]
    except IntegrationError as exc:
        if exc.kind != 'not_found':
            raise
        await io(github.repository, repo)
        directory = []
        limitations.append('根目录无法读取（HTTP 404）；仓库仍公开可访问，可能尚无提交，未采集文件。')
    if len(directory) >= 1000:
        limitations.append('根目录最多保存 1000 项，目录内容可能不完整。')
    snapshot = {'metadata': metadata, 'directory': directory, 'readme': None, 'files': [],
                'captured_at': datetime.now(timezone.utc).isoformat(), 'limitations': limitations}
    files = {item['name'].lower(): item for item in directory
             if item.get('type') == 'file' and isinstance(item.get('name'), str)}
    readmes = sorted((name for name in files if name == 'readme' or name.startswith('readme.')),
                     key=lambda name: (name != 'readme.md', name))
    selected = [(files[readmes[0]], True)] if readmes else []
    if not readmes:
        limitations.append('根目录未发现 README 文件。')
    configs = [files[name] for name in _CONFIGS if name in files]
    selected += [(item, False) for item in configs[:5]]
    if len(configs) > 5:
        limitations.append('关键配置与说明文件最多采集 5 个，其余未读取。')
    for item, is_readme in selected:
        path = item['path']
        await _callback(event, 'progress', f'正在读取文件：{path}')
        try:
            data = await io(github.tool, repo, 'read_file', {'path': path})
        except IntegrationError as exc:
            if exc.kind not in ('not_found', 'unsupported'):
                raise
            if exc.kind == 'not_found':
                # read_file revalidates public visibility: do not swallow a repo
                # that disappeared or became inaccessible during collection.
                await io(github.repository, repo)
            limitations.append(f'文件 {path} 未采集：不存在或不支持文本读取。')
            continue
        text = data.get('text', '')
        coverage = {**data.get('coverage', {}), 'characters_returned': min(len(text), 12000),
                    'character_limit': 12000,
                    'truncated': bool(data.get('coverage', {}).get('truncated')) or len(text) > 12000}
        record = {key: data.get(key) for key in ('path', 'sha', 'html_url')}
        record.update(text=text[:12000], coverage=coverage)
        if coverage['truncated']:
            limitations.append(f'文件 {path} 内容已截断，最多保存 12000 字符。')
        if is_readme:
            snapshot['readme'] = record
        else:
            snapshot['files'].append(record)
    for name, tool in (('issues', 'list_issues'), ('pull_requests', 'list_pull_requests')):
        if name == 'issues' and metadata.get('has_issues') is False:
            snapshot[name] = {'items': [], 'coverage': {
                'available': False, 'reason': 'issues_disabled', 'pages': 0,
                'returned': 0, 'fetched': 0, 'item_limit': 100, 'truncated': False}}
            limitations.append('仓库未启用 Issue，未采集 Issue 列表。')
            continue
        await _callback(event, 'progress', '正在读取 Issue 列表' if name == 'issues' else '正在读取 PR 列表')
        data = await io(github.tool, repo, tool, {'state': 'all', 'sort': 'updated', 'direction': 'desc'})
        source = data.get('items', [])
        bounded = source[:100]
        eligible = [item for item in bounded if isinstance(item, dict)
                    and (name != 'issues' or 'pull_request' not in item)]
        items = []
        text_truncated = False
        for item in eligible:
            record = {key: item.get(key) for key in ('number', 'html_url', 'state', 'updated_at')}
            for key, limit in (('title', 500), ('body', 4000)):
                value = item.get(key) or ''
                text_truncated |= len(value) > limit
                record[key] = value[:limit]
            items.append(record)
        coverage = {**data.get('coverage', {}), 'returned': len(items), 'fetched': len(bounded),
                    'item_limit': 100, 'text_truncated': text_truncated,
                    'truncated': bool(data.get('coverage', {}).get('truncated')) or len(source) > 100}
        if name == 'issues':
            coverage['pull_requests_filtered'] = sum(isinstance(item, dict) and 'pull_request' in item for item in bounded)
        snapshot[name] = {'items': items, 'coverage': coverage}
        if coverage['truncated']:
            limitations.append(f'{name} 仅采集最近更新的最多 100 条列表记录，可能存在未采集内容。')
        if text_truncated:
            limitations.append(f'{name} 标题或正文已截断（上限分别为 500、4000 字符）。')
    return snapshot
