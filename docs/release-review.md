# Release review — 2026-10-30

Scope: current frontend `App.tsx`/API client, Compose, both Dockerfiles, Caddy configuration, and integration of backend review fixes. Static review only in this pass; root owns test/build execution. Adapter rollback and DNS changes are in progress and excluded from conclusions here.

## P1 — Reverse-proxy deployment collapses every user's login rate limit into one shared bucket

Locations: `Dockerfile:10` (`--no-proxy-headers`), `deploy/Caddyfile:14` (reverse proxy), `backend/app/main.py:104–112` (limiter keyed by `request.client.host`).

In the shipped deployment, every browser connects to Caddy and Caddy connects to the API. With proxy headers disabled, the API sees Caddy's address for every login. Ten incorrect attempts from any remote client exhaust the same five-minute budget used by the administrator. An unauthenticated caller can repeatedly prevent all new logins; successful attempts also consume that shared budget.

Fix: obtain the original client address through a trusted reverse-proxy boundary, accepting forwarded information only from the known proxy. Do not simply trust arbitrary forwarded headers on an externally reachable API. Add a deployment-aware limiter test with two client addresses and a spoofed-header case.

## P1 — Proxy cleanup failure blocks unrelated durable work indefinitely

Locations: `backend/app/worker.py:84–91,109–129`; `backend/app/main.py:231–233`; `.env.example` controller defaults and `compose.yaml` optional `proxy` profile.

When `proxy_cleanup.pending` is true, `tick` always returns before normal scheduling, including during the 30-second retry backoff. Any persistent cleanup error consequently halts every queued analysis, diagnostic, and publication. This is reachable without ever activating a proxy: the default environment specifies a controller address, the Mihomo service is optional, and deleting a saved subscription schedules cleanup unconditionally. With the proxy profile off, controller cleanup fails forever while direct tasks that do not use it are also blocked.

Fix: skip remote cleanup when no sidecar credentials were ever activated, and scope any cleanup safety barrier to work that could use the stale sidecar. Unrelated direct jobs should remain schedulable. Test deleting a saved, never-activated subscription with the proxy profile absent, followed by a normal direct analysis.

## P2 — Completed subscription work never refreshes the available nodes in the UI

Locations: `frontend/src/App.tsx:62,69,95–101`.

Enqueuing a subscription refresh calls `loadSettings` immediately and then opens the task. The background poll refreshes tasks/details only. After that task completes, navigating back to Settings reuses the old settings object; it does not reload settings on entry. The first subscription import therefore continues to show zero nodes, and users cannot select the imported nodes without reloading the entire page or performing an unrelated settings mutation. Selected-node state and worker status can likewise remain stale.

Fix: refresh settings when a settings-changing job reaches a terminal state or on entering Settings. Preserve any unsaved form fields when background status changes. Test refresh → task completion → return to Settings without a full-page reload.

## P2 — Testing a model connection discards all unsaved model configuration

Locations: `frontend/src/App.tsx:61–62,66`.

The same `action` helper is used for saving and testing. A successful test calls `loadSettings`, which replaces the settings object; the effect at line 61 then replaces form state with persisted values and clears the API key. On initial setup, a user enters endpoint/model/key, clicks “测试连接,” sees success, and immediately loses the configuration before saving. With an existing configuration, a tested replacement is silently reset to the old endpoint/model. Repository validation has a similar form-reset side effect.

Fix: make read-only tests/validation preserve form state, and only reconcile persisted form fields after an actual successful save. Add a test that fills an unsaved configuration, tests it, then saves the same entered values.

## Deployment observations

The API has no host-published port, web traffic goes through the Caddy origin, and worker/Mihomo share the API network namespace so the configured loopback proxy/controller addresses are coherent. The non-root API/worker and Mihomo UID match. Production data-directory ownership still depends on the deployment bootstrap. Markdown uses a renderer without raw HTML, hides remote images, and restricts clickable links to HTTPS GitHub URLs. No critical direct HTML injection was found in the inspected frontend.

## Final targeted follow-up

All four findings above are **addressed by code inspection** in the final reviewed snapshot. Their original descriptions are retained as review history, not current release blockers. Root is running the final test suite; this pass does not claim additional test or deployed-runtime results.

- **Shared login bucket:** Compose assigns Caddy `172.30.84.2` on its declared subnet and sets the API's `FORWARDED_ALLOW_IPS` to that same single address. Uvicorn now enables proxy headers. The API remains unexposed on host ports. Successful logins clear the accumulated source failure counter. These changes establish the intended trusted-proxy/client-address boundary instead of trusting arbitrary sources.
- **Proxy cleanup queue blockage:** `Worker.tick` continues into ordinary scheduling after a cleanup attempt or during retry backoff. Subscription activation alone checks the pending cleanup flag and enters recoverable `waiting_network`; unrelated durable work can proceed. Cleanup attempts still incur bounded network-call latency when due, but no longer create the reported permanent global scheduling barrier.
- **Stale subscription UI:** entering Settings refreshes persisted settings. Both task-list and detail polling observe settings-job status changes and request a refresh when relevant jobs finish. Per-tab dirty state prevents the refresh from replacing unsaved form values.
- **Connection-test form reset:** the model test explicitly uses `refresh: false`; other background refreshes reconcile only clean form tabs. Saves explicitly clear the corresponding dirty flag before loading persisted values. The reported test-then-save loss is addressed.

No additional critical issue was found within this targeted follow-up. This is not a claim of live deployment acceptance or a replacement for final test/build results.
