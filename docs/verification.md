# 本地验证记录

日期：2026-10-30。此记录描述本地验证结果，不代表目标服务器已上线。

| 检查 | 结果 |
| --- | --- |
| `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q` | 73 passed，4.76 秒；存在 1 条 Starlette/httpx 测试客户端弃用提示，无失败 |
| 前端 `npm test` | 8 passed |
| 前端 `npm run build` | TypeScript 检查与 Vite 生产构建成功 |
| `python -m compileall -q backend/app scripts` | 成功 |
| `APP_ENV_FILE=.env.example SITE_ADDRESS=localhost docker compose --env-file .env.example --profile proxy config --quiet` | Compose 配置校验成功 |
| 本地浏览器 | 登录、工作台、设置页可用；后台健康状态为运行中 |
| 独立代码审查 | 初审与发布审查所列阻断问题已修复并增加回归覆盖；见 backend-review.md、release-review.md |

覆盖范围包括：认证与 CSRF、秘密不回显、加密持久化、配置重启读取、非 GitHub 仓库拒绝、公开仓库检查、工具调用、报告和草稿、发布幂等、撤销凭证、取消竞态、限流持久化、500 条以上记录的调度、代理订阅解析、候选回滚、URL/IP 绑定、前端 API 和表单状态。

外部 GitHub、模型与代理控制接口均使用模拟响应，测试不会发布真实 Issue 或 Comment。用户提供的订阅没有被下载或保存到项目。实际订阅格式、节点连通性、模型兼容性和 GitHub 写入权限还需部署后验证。

本机 Docker CLI 存在，但 daemon 不可用，故只验证 Compose 配置，未完成镜像构建、Caddy 证书签发或 Mihomo 真容器联调。当时尚未连接目标服务器；2026-10-31 的实际部署结果见下文。

临时本地预览使用独立临时数据库与仅用于预览的管理员密码，未配置真实模型或 GitHub 凭证。正式部署须使用初始化脚本生成的新秘密并保留加密主密钥。

## 远端部署验证（2026-10-31）

- 已在目标服务器完成原生部署，访问地址 https://101.132.153.33 。
- 服务器执行后端测试：73 passed；前端测试：8 passed；TypeScript 与 Vite 生产构建成功。
- 已签发受信任 HTTPS IP 证书，配置每日两次续期检查；尚未验证实际到期续签。
- 从本机外网访问 HTTPS 健康接口成功，Worker 存活。
- 通过正式 HTTPS 完成登录、Secure/HttpOnly 会话检查、读取设置、提交网络诊断任务、后台执行完成及退出登录。真实 GitHub 连接测试成功。
- API 与 Worker 使用独立 systemd 服务并设置开机启动。
- 尚未配置真实模型密钥与 GitHub 发布令牌，未执行真实模型分析或 Issue/Comment 发布。
- 当前 GitHub 直连可用；未下载用户订阅，未安装 Mihomo，订阅激活尚不可用。

域名入口 `https://repoagent.ai4s.chat` 已配置，DNS 指向服务器；域名证书签发成功，到期日 2027-01-29，纳入现有续期 timer。服务器通过域名访问 HTTPS 健康接口成功。
