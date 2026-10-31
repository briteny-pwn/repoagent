import httpx
import pytest

from app.integrations import GitHubClient, IntegrationError
from app.project_context import collect_project_snapshot


class FakeGitHub:
    def __init__(self, entries=None, error=None):
        self.entries = entries or []
        self.error = error
        self.reads = []

    async def repository(self, repo):
        return {'full_name': repo, 'private': False, 'html_url': f'https://github.com/{repo}', 'token': 'excluded'}

    async def directory(self, repo):
        return self.entries

    async def tool(self, repo, name, args):
        if name == 'read_file':
            self.reads.append(args['path'])
            if self.error:
                raise IntegrationError('failure', kind=self.error)
            return {'path': args['path'], 'text': 'x' * 13000, 'coverage': {'truncated': False}}
        return {'items': [{'number': 1, 'title': 't' * 600, 'body': 'b' * 5000},
                          {'number': 2, 'pull_request': {}, 'body': 'PR'}],
                'coverage': {'pages': 1, 'truncated': False}}


def entry(name):
    return {'name': name, 'path': name, 'type': 'file'}


@pytest.mark.asyncio
async def test_missing_readme_filters_issues_and_bounds_fields():
    result = await collect_project_snapshot(FakeGitHub(), 'a/b', lambda: None, lambda *args: None)
    assert result['readme'] is None
    assert any('README' in item for item in result['limitations'])
    assert len(result['issues']['items']) == 1
    assert len(result['pull_requests']['items']) == 2
    assert len(result['issues']['items'][0]['title']) == 500
    assert len(result['issues']['items'][0]['body']) == 4000
    assert 'token' not in result['metadata']
    assert result['captured_at']


@pytest.mark.asyncio
async def test_case_insensitive_readme_file_bounds_and_config_limit():
    gh = FakeGitHub([entry(name) for name in ['ReadMe.MD', 'package.json', 'pyproject.toml', 'requirements.txt', 'Cargo.toml', 'go.mod', 'AGENTS.md', 'CONTRIBUTING.md', '.env']])
    checks = []
    async def check():
        checks.append(True)
    result = await collect_project_snapshot(gh, 'a/b', check, lambda *args: None)
    assert len(result['readme']['text']) == 12000
    assert result['readme']['coverage']['truncated']
    assert len(result['files']) == 5
    assert '.env' not in gh.reads
    assert len(checks) == 2 * (4 + 6)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['unsupported', 'not_found'])
async def test_unavailable_file_records_limitation(kind):
    result = await collect_project_snapshot(FakeGitHub([entry('README.md')], kind), 'a/b', lambda: None, lambda *args: None)
    assert result['readme'] is None
    assert any('README.md' in item for item in result['limitations'])


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['network', 'auth', 'rate_limit', 'private', 'remote'])
async def test_file_remote_failure_propagates(kind):
    with pytest.raises(IntegrationError) as exc:
        await collect_project_snapshot(FakeGitHub([entry('README.md')], kind), 'a/b', lambda: None, lambda *args: None)
    assert exc.value.kind == kind


@pytest.mark.asyncio
async def test_directory_public_check_and_field_allowlist():
    paths = []
    def handler(request):
        paths.append(request.url.path)
        if request.url.path == '/repos/a/b':
            return httpx.Response(200, json={'private': False})
        return httpx.Response(200, json=[{**entry('README.md'), 'download_url': 'excluded'}] * 1001)
    async with GitHubClient(transport=httpx.MockTransport(handler)) as gh:
        entries = await gh.directory('a/b')
    assert paths == ['/repos/a/b', '/repos/a/b/contents']
    assert len(entries) == 1000
    assert 'download_url' not in entries[0]


@pytest.mark.asyncio
async def test_directory_rejects_private_repository():
    async with GitHubClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'private': True}))) as gh:
        with pytest.raises(IntegrationError) as exc:
            await gh.directory('a/b')
    assert exc.value.kind == 'private'


@pytest.mark.asyncio
async def test_list_items_are_bounded_even_if_provider_exceeds_limit():
    class Oversized(FakeGitHub):
        async def tool(self, repo, name, args):
            return {'items': [{'number': number} for number in range(110)], 'coverage': {}}
    result = await collect_project_snapshot(Oversized(), 'a/b', lambda: None, lambda *args: None)
    for name in ('issues', 'pull_requests'):
        assert len(result[name]['items']) == 100
        assert result[name]['coverage']['truncated']


@pytest.mark.asyncio
async def test_cancellation_after_io_stops_further_collection():
    import asyncio
    gh = FakeGitHub([entry('README.md')])
    count = 0
    async def check():
        nonlocal count
        count += 1
        if count == 2:
            raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await collect_project_snapshot(gh, 'a/b', check, lambda *args: None)
    assert not gh.reads


@pytest.mark.asyncio
async def test_missing_repository_during_file_read_is_not_optional():
    class Disappeared(FakeGitHub):
        async def repository(self, repo):
            if self.reads:
                raise IntegrationError('repository unavailable', kind='not_found')
            return await super().repository(repo)
    with pytest.raises(IntegrationError) as exc:
        await collect_project_snapshot(Disappeared([entry('README.md')], 'not_found'), 'a/b', lambda: None, lambda *args: None)
    assert exc.value.kind == 'not_found'


@pytest.mark.asyncio
async def test_empty_public_repository_directory_records_limitation():
    class Empty(FakeGitHub):
        validations = 0
        async def repository(self, repo):
            self.validations += 1
            return await super().repository(repo)
        async def directory(self, repo):
            raise IntegrationError('empty', kind='not_found')
    gh = Empty()
    result = await collect_project_snapshot(gh, 'a/b', lambda: None, lambda *args: None)
    assert gh.validations == 2
    assert result['directory'] == []
    assert any('根目录无法读取' in text for text in result['limitations'])


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['auth', 'network', 'rate_limit'])
async def test_directory_failures_propagate(kind):
    class Broken(FakeGitHub):
        async def directory(self, repo):
            raise IntegrationError('failed', kind=kind)
    with pytest.raises(IntegrationError) as exc:
        await collect_project_snapshot(Broken(), 'a/b', lambda: None, lambda *args: None)
    assert exc.value.kind == kind


@pytest.mark.asyncio
async def test_directory_not_found_revalidates_repository_access():
    class Gone(FakeGitHub):
        gone = False
        async def repository(self, repo):
            if self.gone:
                raise IntegrationError('gone', kind='not_found')
            return await super().repository(repo)
        async def directory(self, repo):
            self.gone = True
            raise IntegrationError('gone', kind='not_found')
    with pytest.raises(IntegrationError):
        await collect_project_snapshot(Gone(), 'a/b', lambda: None, lambda *args: None)


@pytest.mark.asyncio
async def test_disabled_issues_skips_endpoint_with_unavailable_coverage():
    class Disabled(FakeGitHub):
        async def repository(self, repo):
            return {**await super().repository(repo), 'has_issues': False}
        async def tool(self, repo, name, args):
            assert name != 'list_issues'
            return await super().tool(repo, name, args)
    result = await collect_project_snapshot(Disabled(), 'a/b', lambda: None, lambda *args: None)
    assert result['metadata']['has_issues'] is False
    assert result['issues']['items'] == []
    assert result['issues']['coverage']['available'] is False
    assert result['issues']['coverage']['reason'] == 'issues_disabled'
    assert any('未启用 Issue' in text for text in result['limitations'])
    assert result['pull_requests']['items']
