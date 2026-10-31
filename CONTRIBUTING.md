# 开发与提交规范

- `main` 保存集成基线；功能和修复分别使用 `feat/<topic>`、`fix/<topic>` 分支。
- 每次提交聚焦一个目的，采用 Conventional Commits，例如 `fix(frontend): prompt for missing analysis settings`。
- 从最新 main 创建分支，通过 PR 合入；禁止强推 main。PR 说明问题、最终行为、验证结果与部署影响。
- 合入前通过后端测试、前端测试和生产构建，并完成代码审查。CI 提供检查，分支保护是否启用需以 GitHub 仓库设置为准。
- 不提交 `.env`、密钥、代理订阅、数据库、依赖目录和构建输出。日志和测试使用脱敏数据。
- 发布记录需包含 commit SHA、验证结果和回滚方式。生产部署使用已验证提交；试运行分支须明确注明。

```sh
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q
npm --prefix frontend test
npm --prefix frontend run build
git diff --check
```
