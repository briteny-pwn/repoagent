# MVP API and adapter contract

All API paths below start `/api`. JSON request/response. Auth session cookies via credentials include; mutations send `X-CSRF-Token` except login. No secrets returned. UTC ISO strings. IDs strings.

## Frontend API
- POST `/auth/login` {password} -> {authenticated:true,csrf_token}
- GET `/auth/session` -> {authenticated:true,csrf_token}; 401 otherwise
- POST `/auth/logout` -> {ok:true}
- GET `/health` -> {status:"ok",worker_alive:bool} (public)
- GET `/settings` -> {model:{base_url,model,api_key_configured,max_steps},github:{token_configured,repository_url,repository},network:{mode,proxy_url,proxy_configured},subscription:{configured,nodes:[{name,type}],selected_node,updated_at,status},runtime:{worker_alive}}
- PUT `/settings/model` {base_url,model,api_key?,max_steps?} -> settings. Empty secret retains existing.
- POST `/settings/model/test` {base_url?,model?,api_key?} -> {ok,message}; tests form values or saved.
- PUT `/settings/github` {token?,repository_url?} -> settings. Saving URL syntax checks; validation endpoint performs public check.
- POST `/repositories/validate` {url?} -> {full_name,html_url,description,private:false,default_branch}
- PUT `/settings/network` {mode:"direct"|"proxy"|"auto",proxy_url?} -> settings
- POST `/settings/network/test` -> {id,status:"queued"}; uses durable diagnostic job, view GET /tasks/id
- PUT `/settings/subscription` {url} -> settings (does not fetch)
- POST `/settings/subscription/refresh` -> settings; downloads and parses nodes inline (40s total deadline), no task and no proxy activation. Concurrent refresh/stale config: 409; timeout: 504; failures preserve prior nodes.
- PUT `/settings/subscription/node` {name} -> {id,status:"queued"}; test and activate
- DELETE `/settings/credentials/{kind}` where kind = model|github|proxy|subscription -> settings
- GET `/tasks` -> [{id,kind:"analysis"|"diagnostic"|"node",prompt,status,repository,created_at,updated_at,error}]
- POST `/tasks` {prompt,request_id} -> {id,status}; snapshot settings and target repo, reject unconfigured
- GET `/tasks/{id}` -> task fields plus {result:{report:string,evidence:[{title,url}],limitations:[string]}|null,events:[{id,kind,message,created_at}],drafts:[Draft]}
- GET `/tasks/{id}/events?after=0` -> SSE event progress data {id,kind,message,created_at}. UI can poll detail every 3s as fallback.
- POST `/tasks/{id}/cancel` -> {ok:true}
- POST `/tasks/{id}/resume` -> {ok:true} only waiting_network tasks
- Draft: {id,task_id,kind:"issue"|"comment",repository,issue_number:null|number,title,body,version,status,external_url,error}
- PATCH `/drafts/{id}` {version,title?,body} -> Draft
- POST `/drafts/{id}/confirm` {version} -> Draft; repeats same version return existing; pending async publish
- POST `/drafts/{id}/discard` {version} -> Draft

Task status queued/running/waiting_confirmation/waiting_network/waiting_rate_limit/completed/failed/cancelled. Draft status draft/pending/executing/succeeded/failed/unknown/discarded. Render text/markdown safely (no raw HTML). Link only https GitHub evidence. Unknown publication must not expose retry button.

## Adapter Python API (owned by adapter agent)
`backend/app/integrations.py` standalone (no store dependencies), async methods:
- `IntegrationError(message: str, *, kind: str="remote", retry_at: float|None=None, ambiguous: bool=False)` exposes safe message, kind, retry_at, ambiguous. Never include credentials/URLs with query in errors.
- `parse_repository(url: str) -> str`: canonical owner/repo; github.com https only, no query/fragment/credentials/extra path. Strip trailing slash or .git.
- `async validate_public_url(url: str) -> str`: HTTPS public-address resolution, no userinfo, no fragment; rejects any nonpublic IP incl DNS. Called at each external custom URL use. Subscription query allowed. Model URL query disallowed by caller.
- `GitHubClient(token: str="", proxy_url: str|None=None, transport=None)` async context manager. `async repository(repo)->dict` validates public every time. `async tool(repo,name,arguments)->dict|list` names get_repository/list_issues/get_issue/list_comments/list_pull_requests/list_commits/read_file/search_issues. Bounded pagination with explicit coverage; repo arg cannot be overridden. `async publish(repo,kind,title,body,issue_number=None)->dict` validates public, supports only issue/comment, does NOT retry post and wraps uncertain timeout/network/5xx as ambiguous. `async find_operation(repo,marker,issue_number=None)->dict|None` searches matching marker for reconciliation. `async diagnose(repo: str|None=None)->dict` read-only check.
- `ModelClient(base_url,api_key,model,transport=None)` async context manager. `async complete(messages:list,tools:list|None=None)->dict` returns OpenAI-compatible assistant message dict (`content`, optional `tool_calls`). `async test()->dict`. Validate public address and HTTPS on real traffic, injected transport tests can skip DNS. No redirects, no remote response body in errors. 60s timeout. Send minimal test.
- GitHub rate limits raise kind rate_limit with epoch retry_at (Retry-After then reset, secondary default >=60). GET limited transient retries; never blindly retry writes; no Authorization forwarded to other origins.
`backend/app/subscriptions.py` standalone:
- `parse_subscription(text: str)->list[dict]`: sanitize supported Clash YAML or base64 URI lists (at least ss/trojan/vmess if feasible; unsupported explicit). Never accept script/rules/controller/listener fields. Return nodes with name/type/server/port and protocol creds necessary for runtime.
- `async download_subscription(url,proxy_url=None)->list[dict]`: HTTPS/public validation, size 2MB, no redirects, timeout; errors redacted.
- `render_mihomo_config(nodes,selected,controller_secret)->str`: safe generated YAML, fixed mixed-port7890, controller9090, selector PROXY, rules MATCH,PROXY; no arbitrary config passthrough.
- `async activate_mihomo(nodes,selected,controller_url,controller_secret,config_path,proxy_url)->dict`: deploy-side configured internal controller only; atomic candidate config, load + select + GitHub read-only test, rollback on failure. If no sidecar configured raise clear error. Discuss signature changes with root.

Tests import `from app...` using PYTHONPATH=backend. Shared .venv is being installed by root. Do not modify other owners' files.

Legacy subscription refresh task records are retained internally but excluded from task APIs and cancelled by the worker if unfinished. Refreshing nodes does not change the active proxy; node activation remains a separate action.
