# Repository Steward · 仓库助理

通过网页管理 GitHub **公开仓库**的后台 Agent：仓库问答、Issue 整理、进展报告，以及经确认后创建 Issue / 评论。不会修改代码、创建分支或合并 PR。

## 当前实现

- React 中文网页：项目工作空间、自动初始化、仓库资料与 Issue/PR 快照、项目指令和历史、草稿编辑发布、配置管理。
- FastAPI + 独立 Worker；SQLite WAL 持久化任务和事件，适用于单服务器。浏览器关闭后继续工作。
- OpenAI-compatible `/chat/completions` 模型接口，支持工具调用；必须选择支持 tools 的模型。
- GitHub 只读工具、公开状态检查、受限分页、网络重试、限流等待；写操作不盲目重试。
- 密钥加密保存、会话认证与 CSRF、草稿版本锁定、凭证撤销检查。
- HTTP/SOCKS 代理；可选 Mihomo 客户端导入受支持的订阅，节点测试后切换。

项目以“导入仓库 → 初始化资料 → 在项目中持续下达指令”为主流程。模型调用和发布权限仍需在实际部署环境验证。

## 项目工作流程

1. 配置全局模型、可选 GitHub 令牌和网络连接。
2. 在工作台输入公开 GitHub 仓库 URL。首次导入自动创建项目和后台初始化任务；重复导入打开已有项目。
3. 初始化读取仓库元信息、根目录、README 和关键配置文件，以及 Issue/PR，并保存资料快照与 AI 概览。网页显示采集时间与覆盖限制。
4. 在项目中下达自然语言指令。每次任务固定目标仓库，并携带该项目的摘要、资料摘录和最近六条已完成/待确认指令的报告。AI 可按需读取最新 GitHub 内容。
5. 创建 Issue、评论仍先生成草稿，用户编辑并确认后才发布。刷新项目资料重新采集并生成概览，失败保留上次成功快照。

每次初始化最多保存根目录 1000 项、README 和 5 个关键文件、100 条 Issue 与 100 条 PR；不下载整仓库或执行仓库代码。摘要上下文进一步截取文件和 Issue/PR 内容，不能代替完整审计。已有分析任务按仓库自动归入待初始化项目，保留草稿与历史；迁移不会自动访问网络或调用模型，需手动初始化后继续发送新指令。

## 本地运行

需要 Python 3.14 与 Node.js 24+。部署密钥不包含在代码中。

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
python3 scripts/init_deployment.py --domain localhost
```

本地开发将 `.env` 的 `DATA_DIR` 改为 `./data`，`SECURE_COOKIE` 改为 `false`。生成的管理员密码位于 `.env`，不应提交或共享该文件。只有本机 HTTP 调试可关闭 secure cookie；远程部署保持 `true`。

在各自终端启动 API、Worker 和前端：

```sh
set -a
. ./.env
set +a
PYTHONPATH=backend .venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

```sh
set -a
. ./.env
set +a
PYTHONPATH=backend .venv/bin/python -m app.worker
```

```sh
cd frontend
npm ci
npm run dev
```

打开 Vite 输出的本机地址。前端将 `/api` 转发到 `127.0.0.1:8000`。

## 远程服务器部署

目标服务器位于中国大陆，公网 IP `101.132.153.33`。下面是可选的容器部署方案；现有服务器使用文末的原生部署。

1. 为网页准备域名，指向该服务器；准备 Docker Engine 和 Compose。先验证服务器能下载所需镜像和依赖，必要时在可信构建环境构建并传送镜像，不把运行时 GitHub 代理误当作镜像拉取代理。
2. 复制项目，生成 `.env` 与私有代理初始配置：

```sh
python3 scripts/init_deployment.py --domain your-domain.example
sudo chown -R 10001:10001 data
```

3. 设置防火墙只对网页开放 80/443，保留自己的管理通道。数据库、API 和代理不映射公网端口。Caddy 使用域名申请 HTTPS 证书；域名和网络条件需允许证书签发。
4. 首次启动（包括订阅客户端）：

```sh
docker compose --profile proxy up -d --build
docker compose ps
```

不需要订阅客户端时可以省略 `--profile proxy`。Mihomo 默认镜像为浮动标签；正式部署前在 `.env` 的 `MIHOMO_IMAGE` 固定为实测通过的版本或 digest。当前无法验证 Docker daemon，未宣称这些镜像已成功构建或完成真实容器联调。

Compose 默认使用 `172.30.84.0/24` 内部网段，Caddy 固定为 `172.30.84.2`，API 只信任此来源的转发 IP 以按真实客户端进行登录限流。若网段冲突，需同时修改网段、Caddy IP 和 `FORWARDED_ALLOW_IPS`，不能设为 `*`。

API、Worker 与 Mihomo 共享 API 的容器网络命名空间；代理仅监听该空间的 `127.0.0.1:7890`，控制接口仅监听 `127.0.0.1:9090`。代理配置目录挂载路径在 Worker 与 Mihomo 中必须一致。不要将代理或控制接口增加到公网 `ports`。

5. 登录网页，在设置中配置：
   - 模型：HTTPS Base URL、API Key、模型名称，点击测试。
   - GitHub：需要发布时填写具备对应 Issue 写权限的令牌；目标仓库在工作台按项目导入。设置中的仓库地址保留用于旧接口和连接诊断。
   - 订阅：粘贴私密订阅链接并保存，点击“刷新订阅”在设置页更新节点列表，不创建任务、不切换代理。需要切换时单独点击节点的“测试并启用”；不直接把订阅 URL 填成代理地址。
   - 网络：检查当前代理与 GitHub 连接状态，然后在工作台导入公开仓库。

订阅支持 Clash YAML 的普通 SOCKS5（可选用户名/密码、UDP）、SS、Trojan、VMess 节点，以及纯文本/base64 URI 列表中的 SS、Trojan、VMess（TCP/WebSocket）。SOCKS5 over TLS 与 SOCKS5 URI 暂不支持。不支持的协议会明确报错；不保证用户提供的订阅属于支持格式。HTTPS 证书错误不会被忽略。订阅失败保留旧配置，首次下载需要服务器直连或一个已有可用代理。

## 数据与恢复

数据库在 `data/steward.db`；配置秘密和任务配置快照使用 `ENCRYPTION_KEY` 加密。**必须保留原加密主密钥**，重新生成密钥无法解密原数据。Mihomo 的运行配置包含节点秘密，目录权限必须保持私有。

```sh
python3 scripts/backup.py data/steward.db /secure-backup/steward-backup.db
```

将 `.env`（尤其 `ENCRYPTION_KEY`）另行安全备份。恢复时停止所有服务，用备份替换数据库并清理旧 WAL/SHM 文件，恢复匹配的主密钥，重建代理配置后再启动。恢复操作前保留当前数据副本，不在运行中的数据库上直接覆盖文件。

任务有一小时执行截止时间；支持取消、网络修复后恢复和限流延期。发布结果不确定时显示待核实，Worker 最多进行三次只读检查，查不到并不证明发布失败，不提供自动重发按钮。可在 GitHub 手动核对。

## 验证

```sh
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q
cd frontend
npm test
npm run build
```

测试中的 GitHub/模型使用 HTTP 模拟，不会调用真实发布接口。实际部署还应验证服务器网络、多次分页读取、所选模型的工具调用、代理中断恢复，以及专用测试仓库的真实发布。

## 初版限制

- 单管理员、单服务器，不支持私有仓库、多用户或 GitHub Enterprise。
- 模型仅支持 OpenAI-compatible Chat Completions；其他协议需新增适配器。
- GitHub 列表最多两页、每页 50 条；报告说明覆盖范围，大仓库需要缩小任务范围。
- GitHub 网络配置支持直连或指定代理，自动模式目前只选择已配置代理或直连，不自动探测多个出口。模型服务目前直连，独立模型代理还未实现。
- 诊断目前提供整体 API/仓库结果，尚未逐阶段统计 DNS/TLS 延迟；ETag 缓存、定时巡检和 Webhook 属于后续工作。
- 模型和订阅请求已绑定经过公共地址检查的 IP 并保留原始 TLS 主机名，节点激活也解析并固定公共 IP。自行填写的代理出口属于受信任部署组件，仍应使用部署侧出站策略并限制管理员访问。

设计见 [design.md](design.md)，接口见 [docs/api-contract.md](docs/api-contract.md)，实施进度见 [实施计划](docs/superpowers/plans/2026-10-30-repository-agent.md)。


部署配置参考：[Docker Compose 服务定义](https://docs.docker.com/reference/compose-file/services/) 与 [Caddy 反向代理](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)。


## 当前服务器原生部署（2026-10-31）

服务器已有 Nginx 且可用内存有限，本次采用独立 Python 虚拟环境 + systemd + 现有 Nginx 部署，未安装 Docker，也未替换系统 Node。原有 `ai4s.chat` 和 `planner.ai4s.chat` 的配置保持独立。

- 项目目录：`/opt/repository-steward`
- 专用账户：`steward`
- API：`127.0.0.1:8017`，服务 `repository-steward-api`
- 后台 Agent：服务 `repository-steward-worker`
- 网页：`https://repoagent.ai4s.chat`（原 IP HTTPS 入口保留）
- 私有部署配置：`/opt/repository-steward/.env`
- 系统服务模板：`deploy/systemd/`
- Nginx 模板：`deploy/nginx-native.conf`（IP）与 `deploy/nginx-domain.conf`（域名）
- IP 证书：Let’s Encrypt shortlived，`repository-steward-cert-renew.timer` 每日两次检查续期并重新加载 Nginx。

服务器 Python 3.10.12 已通过全部后端测试；前端使用项目独立 Node 24 构建。服务器已单独配置私有代理客户端，项目可使用设置页中的代理模式；代理节点和订阅秘密仅保存在服务器。模型与 GitHub 发布令牌由用户在 HTTPS 设置页填写。

常用运维命令：

```sh
systemctl status repository-steward-api repository-steward-worker
journalctl -u repository-steward-api -u repository-steward-worker --since today
systemctl list-timers repository-steward-cert-renew.timer
```

`deploy/server-bootstrap.sh` 仅用于首次初始化，检测到已有项目站点会拒绝覆盖。更新时备份数据库和 `.env`，更新代码、安装依赖、重新构建前端，再重启本项目的两个服务；不要重新生成加密密钥。
