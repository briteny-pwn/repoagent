import pytest
import httpx
from app.integrations import GitHubClient, IntegrationError

@pytest.mark.asyncio
@pytest.mark.parametrize('status,word', [(401,'令牌'),(403,'权限'),(404,'不存在'),(422,'参数'),(302,'重定向'),(500,'服务')])
async def test_github_http_error_preserves_status_without_remote_secrets(status,word):
    transport=httpx.MockTransport(lambda _:httpx.Response(status,json={'message':'private-sensitive-value'}))
    async with GitHubClient(transport=transport) as client:
        with pytest.raises(IntegrationError) as error:
            await client._request('POST','/repos/a/b/issues',json={})
    assert str(status) in str(error.value)
    assert word in str(error.value)
    assert 'private-sensitive-value' not in str(error.value)
    assert error.value.ambiguous == (status>=500)
