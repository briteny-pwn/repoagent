# 项目中心仓库管理

目标：导入公开仓库自动初始化；项目保存资料与报告，后续指令使用独立上下文。既有发布确认/取消/幂等保持。

## 接口约定
Project = {id, repository, name, status: pending|initializing|ready|failed, initialization_task_id: string|null, initialized_at: string|null, created_at, updated_at, last_error: string|null}。
ProjectDetail = Project & {summary: Report|null, snapshot: Snapshot|null}。
Report = {report,evidence:[{title,url}],limitations: string[]}。
Snapshot = {metadata:object, readme:object|null, files:object[], issues:{items:object[],coverage:object}, pull_requests:{items:object[],coverage:object}, directory:object[], captured_at:string, limitations:string[]}。

- GET /api/projects -> Project[]
- POST /api/projects {repository_url} -> ProjectDetail；验证公开仓库与模型配置，自动初始化；重复导入返回已有项目。
- GET /api/projects/{id} -> ProjectDetail
- POST /api/projects/{id}/initialize -> {id,status}；幂等返回未结束初始化任务，失败可重新初始化。
- GET /api/projects/{id}/tasks -> Task[]（initialization、analysis）
- POST /api/projects/{id}/tasks {prompt,request_id} -> {id,status}；未 ready 返回409。
- Task 新增可空 project_id，kind 新增 initialization。原 /api/tasks/{id} 详情和草稿操作不变。
- 原 POST /api/tasks 保留兼容旧客户端，但按其全局目标仓库归属项目；新界面只使用项目接口。

## 持久化/执行
Store records 中新增 projects，context 为加密快照+初始化报告。已有 analysis 按 repository 迁入 pending 项目，不发起网络或模型操作。重复 migration 无副作用。
初始化固定采集 metadata、根目录、README及最多5个关键配置、最多100 Issue/PR（两页，排除 Issue 列表中的 PR），保存截断和时间说明；模型归纳产生报告，不生成初始化发布草稿。失败保留旧 context。
项目指令固定 repository 与 context（初始化报告、快照摘要及最近6次同项目指令结果）到任务加密 snapshot。使用真实证据链接并声明资料时效；跨项目禁止混用。Worker 完成初始化时事务保存项目与任务；失败/等待/取消可从项目页看到并恢复。

## 工作划分与验证
1. 本地负责 store/main/worker/agent：事务建项目和任务、幂等迁移、初始化状态、上下文隔离、API及集成测试。
2. 独立实现 project_context.py 与 GitHub directory 方法：bounded 采集、缺失文件容错、资料截断测试。
3. 独立实现前端 ProjectWorkspace 与 App 接入：导入项目、状态、快照/Issue/PR、项目内指令与已有任务详情/草稿。
4. 完整测试、浏览器 mock 与真实本地 API 流程、独立跨模块审查、文档、关联 Issue 的 PR。

部署前数据库备份，不自动初始化迁移出的项目或调用用户模型。试运行分支版本明确记录；服务器私有代理不入库。
