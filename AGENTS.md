# Repository workflow

- Follow CONTRIBUTING.md. For major changes, create an Issue first, then develop on a scoped feature/fix branch and open a PR linked to that Issue.
- For small, localized changes, validate and commit directly to the latest main, then push main; no Issue or PR is required.
- Judge scope by impact and risk, not line count: architecture changes, new subsystems, migrations, or broad security/API changes are major; documentation, copy, styling, and localized low-risk fixes are small. Do not split a major change to bypass the Issue/PR workflow.
- Do not force push main or merge a PR without user authorization.
- Use Conventional Commits; separate unrelated changes.
- Run relevant tests and the frontend production build for UI changes. Report actual validation and limitations.
- Never commit credentials, subscription URLs, databases, dependency folders, or generated build artifacts.
- Record deployed commit IDs; clearly identify deployments from unmerged branches.
