# Backend review — 2026-10-30

Scope: `backend/app/{main,store,security,agent,worker,integrations,subscriptions}.py` against `docs/api-contract.md`. Reviewed the working snapshot, without Git history. No application code changed by this review.

Validation: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests` returned **45 passed**. Additional isolated temporary-database reproductions below reveal cases absent from those tests. No external credentials or live GitHub writes were used.

## Findings

### P1 — Confirmation resurrects a revoked GitHub token

Location: `backend/app/main.py:353–361`.

After an analysis has produced a draft, deleting the GitHub credential clears the current token and increments its generation. Confirming that draft then falls back to the token in the old task snapshot at line 355, but assigns the **current** generation at line 360. The worker therefore accepts and uses the revoked token. This defeats the application-level credential revocation guarantee without requiring a race.

Reproduction: create an analysis snapshot containing a token and an unconfirmed draft; call `DELETE /api/settings/credentials/github`; confirm version 1. Observed `200`, `pending`, the old token in the encrypted draft credential, and a credential generation equal to the current generation.

Fix: use only an explicitly configured current credential at confirmation, or permit snapshot fallback only when its original generation still matches. Add a test covering revocation between analysis completion and confirmation.

### P1 — Cancellation or revocation during publication preflight does not prevent the subsequent write

Locations: `backend/app/worker.py:188–195`; `backend/app/integrations.py:187–200`.

The worker performs its final cancellation/revocation checks before calling `GitHubClient.publish`. That method then awaits a repository metadata GET (including potentially several retries) before issuing POST. A cancellation or revocation acknowledged while that GET is in flight does not prevent the POST. This is a later external write, not an already-issued write whose result needs reconciliation.

Reproduction: use the real GitHub client with `httpx.MockTransport`; in the repository GET handler, call the task cancellation endpoint, then return public metadata. Observed task status `cancelled`, **one POST after cancellation**, and draft status `succeeded`. Changing the handler to revoke the token exposes the same missing check.

Fix: perform the final guard immediately after public-repository validation and before the actual POST, via an executor guard callback or an explicit prepare/write split. Keep the public-repository check. Test cancellation and revocation during delayed/retried preflight, not only before worker claim.

### P1 — The 500-record display limit silently removes durable work from scheduling

Locations: `backend/app/store.py:81–85`; `backend/app/worker.py:53–67`; also `backend/app/main.py:283,307`.

`Store.all` always returns only the 500 newest records, and the worker uses it to discover queued tasks, pending publications, and expired leases. Once 500 newer records exist, an older unresolved job becomes permanently invisible even if all newer jobs are complete. The same limit hides older drafts from task details/cancellation and can incorrectly exclude unresolved drafts from completion checks. Records are not deleted, so processing newer jobs never brings the older ones back into the window.

Reproduction: insert one queued task followed by 500 completed tasks. Observed the old task still `queued` while `Worker.claim()` returns `None`.

Fix: use transactional queries for eligible work, filtered before applying a limit; query drafts by task ID for task operations. Keep pagination limits only on list views. Test queue recovery and cancellation with more than 500 retained records.

### P1 — Subscription activation mutates the sidecar before checking that the task/configuration is still valid

Location: `backend/app/worker.py:157–169`.

`activate_mihomo` replaces the live configuration and changes the running sidecar before the post-await cancellation/revocation check and before comparing the current subscription URL. If a user cancels, revokes the subscription, or saves another subscription while activation is in flight, the task exits or fails after the old nodes have already become active. The database can say cancelled/deleted or describe the newer subscription while the global proxy runs the stale credentials. No rollback is performed in these paths.

This follows directly from the ordering: sidecar mutation at line 161, cancellation/revocation check at line 162, URL conflict at lines 165–166. The rollback inside `activate_mihomo` only handles its own HTTP failures; it cannot undo a successful activation rejected afterward.

Fix: stage candidates separately and coordinate activation with configuration revisions. If cancellation/revocation/revision changes during activation, restore the previous sidecar state before returning. Ensure deleting an active subscription also deactivates its sidecar credentials. Add a delayed-activation test that changes the subscription and verifies both persisted and live state afterward.

### P2 — Failed first activation leaves the failed candidate running while reporting rollback

Location: `backend/app/subscriptions.py:215–233`.

If there is no previous configuration file, a candidate may load successfully but fail the subsequent GitHub connectivity probe. The failure handler deletes the candidate file and asks Mihomo to reload the now-missing path. It ignores the reload response status, then reports that the previous configuration was restored. The in-memory sidecar can therefore retain the failed candidate and its credentials. Rollback failures with an existing file are likewise suppressed and reported as successful restoration.

Fix: retain an explicit known-good initial configuration, validate the rollback HTTP response and actual state, and report rollback failure distinctly. Test a successful candidate reload followed by probe failure with no existing file, as well as a failed rollback reload.

### P2 — Public-address validation is disconnected from the actual network connection

Locations: `backend/app/integrations.py:48–63,243–249`; `backend/app/subscriptions.py:150–157`; `backend/app/security.py:43–48`.

These validators resolve DNS and inspect the answers, then hand the original hostname to a separately resolving HTTP/proxy client. The connection is not restricted to the addresses that passed validation. A hostname with changing DNS answers can pass the public-address check and subsequently connect to an internal address. For proxy-mediated requests, the proxy may resolve the hostname differently from the API process. Subscription node hostnames also receive only syntactic validation in `_server` (`subscriptions.py:34–48`), allowing internal-resolving names to reach the sidecar directly.

Fix: bind connections to validated IPs while retaining the original TLS hostname, or enforce the public-only boundary through a controlled egress layer that applies to direct requests and sidecar/proxy traffic. Validate node DNS at the enforcing boundary. Add transport-level tests that change resolution between validation and connection. This finding is based on code inspection; no live rebinding exploit was attempted.

## Positive observations and limits

Draft confirmation is transactionally versioned; uncertain publications are not automatically reposted; task snapshots and draft credentials are encrypted; remote error bodies are not echoed; GitHub tools recheck public metadata and filter search results to the selected repository. These controls do not cover the specific gaps above.

The existing suite is mocked and does not establish real deployment reachability, DNS enforcement, or sidecar rollback behavior. Public/private repository transitions between authenticated metadata and content requests remain a race to consider; this review does not claim a reproduced private-data disclosure.

## Follow-up review of fixes

Reviewed the subsequent working snapshot on 2026-10-30. Root owns the follow-up test run; the statements here describe code inspection, not an additional test result.

- Revoked-token fallback: addressed in `main.py` by requiring the original snapshot generation to equal the current generation before using its token. A newly supplied current token can still authorize an existing draft as intended.
- Queue truncation: addressed by removing `LIMIT 500` from internal `Store.all`. This restores visibility of old work. Unbounded scanning remains a scaling concern, but the specific silent-loss bug is fixed.
- Publication preflight: addressed in inspected code. The worker passes `before_write=guard`, and the adapter invokes it after repository validation and immediately before POST, checking cancellation and credential generation at the write boundary.
- Subscription commit: the worker passes a `before_commit` callback that checks cancellation, subscription URL, and subscription generation in the transaction that persists settings. The adapter now executes the callback inside its rollback-on-failure scope. This addresses the original ordering gap by inspection; live rollback behavior remains part of the adapter verification.
- Initial rollback and DNS enforcement findings are intentionally not closed in this follow-up while adapter work remains in progress.

The new proxy-cleanup queue-blocking regression and release/UI findings are recorded separately in `docs/release-review.md`.
