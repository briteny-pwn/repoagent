# Repository Steward Implementation Plan

> **For agentic workers:** Use executing-plans and test-driven-development. Independent frontend and external adapter tasks may use dispatching-parallel-agents; code review uses requesting-code-review.

**Goal:** Deliver a locally tested, self-hostable Chinese-language GitHub public-repository management assistant, with a real model tool loop and explicit publication confirmation.

**Architecture:** React/TypeScript frontend communicates with FastAPI using same-origin `/api`. A separate worker claims durable SQLite jobs (single-server MVP), stores events, and calls GitHub and OpenAI-compatible model APIs. Credentials use Fernet encryption with a deployment-owned key. SQLite WAL is the single-server relational store and queue; no Redis requirement for MVP.

**Tech Stack:** Python 3.12+, FastAPI, HTTPX SOCKS, SQLite, cryptography, PyYAML; React, TypeScript, Vite; Docker Compose, Caddy, optional Mihomo sidecar.

## Global Constraints
- GitHub public repositories only; no code modifications, git pushes, merge, settings writes, or shell tools available to the model.
- Only create Issue and Issue Comment after confirmation of an immutable draft version.
- Web settings manage model base URL/key/name, GitHub token, repository URL, network and subscription; never return stored secrets.
- Deployment target: mainland China server 101.132.153.33. Do not access or deploy to it without connection information. No credentials committed, including the user-supplied subscription.
- Remote-boundary tests use HTTP mocks. Actual server, actual model credentials, and actual subscription are not yet available as verified test environments.
- Do not initialize or alter Git history simply to satisfy workflow: this directory is not a Git checkout.

## Interface contract
See `docs/api-contract.md`; frontend and adapter tasks own different directories. All errors use `{detail: string}`. Session cookie is HttpOnly; authenticated mutations require `X-CSRF-Token` obtained from login/session. Secret inputs: omitted/empty means retain, explicit deletion uses credentials route. Settings show configured flags only.

## Task 1: authenticated configuration and persistence (root)
Files: `backend/app/config.py`, `store.py`, `security.py`, `main.py`, `schemas.py`, `backend/tests/test_api.py`.
- [ ] Write tests for anonymous access, login/CSRF, secret non-disclosure, repository URL rejection, version conflicts, persistence.
- [ ] Run `.venv/bin/python -m pytest backend/tests/test_api.py -q` and record red before implementation.
- [ ] Implement an application factory `create_app(config=None)` and transactional SQLite Store with settings, sessions, jobs, events, drafts and audit tables.
- [ ] Run the above tests to green, inspect persisted ciphertext and restart behavior.

## Task 2: bounded external adapters (delegated)
Files: `backend/app/integrations.py`, `backend/app/subscriptions.py`, `backend/tests/test_integrations.py`.
- [ ] Write tests for private/non-GitHub rejection, transient retry vs ambiguous writes, rate limit timing, hostile URLs, unsafe subscription fields, model tool parsing.
- [ ] Run tests red, then implement the contract in `docs/api-contract.md`.
- [ ] Test direct/proxy selection, subscription normalization and supported formats without fetching user subscription.
- [ ] Run adapter tests green and document exact supported protocols/limitations.

## Task 3: durable worker and confirmed publication (root)
Files: `backend/app/worker.py`, `backend/app/agent.py`, `backend/tests/test_worker.py`.
- [ ] Test a full mock GitHub/model tool conversation to report + draft, cancellation, retry schedule, restart recovery, idempotent confirmation and uncertain write handling before implementation.
- [ ] Implement `python -m app.worker`, serial durable claims with leases, capped tools, frozen task snapshots, readable events and protected write executor.
- [ ] Persist rate-limit waiting and ambiguous publication instead of retrying writes; reconcile using unique operation marker and remote reads.
- [ ] Run worker tests and application integration tests green.

## Task 4: web application (delegated)
Files: `frontend/src/*`, `frontend/package.json`, `frontend/vite.config.ts`, `frontend/index.html`.
- [ ] Add API client tests for authentication handling and mutation CSRF.
- [ ] Build login, workspace/task conversation/history, structured progress/report/evidence, draft edit-confirm-discard, settings tabs (model/GitHub/network/subscription).
- [ ] Use locally bundled styles/icons; no external CDN. Chinese labels, responsive layout, honest empty/loading/error states, no fake data.
- [ ] Run `npm test` and `npm run build`; check browser interaction with the backend.

## Task 5: deployment, integration and review (root + reviewer)
Files: `compose.yaml`, `Dockerfile`, `frontend/Dockerfile`, `deploy/Caddyfile`, `.env.example`, `README.md`, `scripts/*`.
- [ ] Provide single-server Compose, persistent data, optional internal proxy sidecar, admin secret generation, HTTPS/domain setup, backup/restore and smoke checks.
- [ ] Full verification: backend pytest, frontend tests/build, Python compile, Compose validation when Docker available, local HTTP/UI smoke.
- [ ] Independent review of auth/SSRF/secret leakage/public-only/write idempotency boundaries; fix material findings and rerun focused tests.
- [ ] Update design implementation status with tested features and remaining deployment prerequisites. Never claim actual server deployment or live publication without evidence.

## Progress
- Planning complete. No source existed at start; no Git checkout exists, so work proceeds in-place.


## Execution ledger — 2026-10-30
- Task 1 implemented: authentication/CSRF, encrypted persistent configuration, repository URL validation, versioned draft confirmation and credential revocation. Regression tests cover snapshot revocation and request deduplication.
- Task 2 implemented: bounded GitHub/model adapters and sanitized subscription sidecar lifecycle; independently reviewed. Supported protocols and limits are in docs/integrations-report.md.
- Task 3 implemented: serial durable worker, read-only tool loop, checkpointing, cancellation, rate-limit schedule, confirmed publication and ambiguous-result reconciliation. Cancellation guard executes immediately before POST.
- Task 4 implemented: real API-connected Chinese UI; local browser login/workspace/settings smoke; frontend tests and production build passed. Review fixes preserve unsaved settings and refresh subscription results.
- Task 5 local deliverables implemented: Compose/Caddy, secret initializer, backup script, deployment docs. Targeted review findings fixed; final evidence in docs/verification.md. Real Docker build and remote deployment remain unverified due unavailable daemon and missing SSH connection information.
- No Git repository exists; no commits, branches, PRs or remote changes were made.
