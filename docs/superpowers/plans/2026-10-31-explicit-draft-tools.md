# Explicit draft tools implementation plan

**Goal:** Give the project agent explicit Issue/comment preparation tools independent of final report JSON.

**Architecture:** Add validated local draft tools to the existing model tool loop. prepare_issue performs scoped Issue duplicate search; prepare_comment reads and validates an open Issue. The first successful preparation ends the loop with a validated draft and deterministic report. Existing Worker atomically persists result/draft, and existing UI confirmation remains the only path to GitHub writes. Preserve legacy final-JSON drafts for old clients/checkpoints.

## Tasks
- [x] Add tests and backend/app/draft_tools.py: bounded required arguments, duplicate query, Issue target validation, safe reports/coverage, no external writes.
- [x] Register schemas in agent.py. Route preparation locally; return validation errors as tool messages so the model can correct them. Successful preparation returns immediately; additional calls in that model batch are not executed. Existing step/time budgets and cancellation apply.
- [x] Verify Worker persisted drafts, no publication before confirm, edit/confirm/discard and publication regression tests. Add concise documentation/examples.
- [ ] Run independent review, relevant tests/build if UI changes arise, open linked PR, back up and deploy verified branch. Do not publish a real Issue/comment as a test.

Validation includes invalid/missing/excessive fields, search failure, closed/PR/wrong-number comment targets, model argument correction, checkpoint recovery and normal draft confirmation. Publication credentials and server-private proxy configuration stay outside source.
