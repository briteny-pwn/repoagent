# 开发与提交规范

- 大改动先创建 Issue，说明目标、范围和验收标准，再从最新 `main` 创建 `feat/<topic>` 或 `fix/<topic>` 分支，通过关联该 Issue 的 PR 交付。
- 小改动在最新 `main` 上修改，完成相应验证后直接提交并推送，无须 Issue 或 PR。
- 按影响与风险判断大小：架构调整、新子系统、数据迁移、安全或接口的大范围变更属于大改动；文档、文案、样式及局部低风险修复通常属于小改动。不能拆分大改动来绕过流程。
- 每次提交聚焦一个目的，采用 Conventional Commits，例如 `fix(frontend): prompt for missing analysis settings`。
- 禁止强推 main。大改动的 PR 说明问题、最终行为、验证结果与部署影响，并关联先前创建的 Issue；合并 PR 仍需用户授权。
- 提交前按改动范围完成必要验证；前端行为变更需测试及生产构建，后端行为变更需相关测试。纯文档修改检查内容和差异即可。大改动合入前完成代码审查及完整检查。CI 同时检查 main 推送和 PR，分支保护是否启用以 GitHub 仓库设置为准。
- 不提交 `.env`、密钥、代理订阅、数据库、依赖目录和构建输出。日志和测试使用脱敏数据。
- 发布记录需包含 commit SHA、验证结果和回滚方式。生产部署使用已验证提交；试运行分支须明确注明。

```sh
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q
npm --prefix frontend test
npm --prefix frontend run build
git diff --check
```
