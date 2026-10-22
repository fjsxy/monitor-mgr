# #25 实例创建身份与管理员实例写保护测试计划

测试计划评论：https://github.com/fjsxy/monitor-mgr/issues/25#issuecomment-6335980289（首版）；以下修订明确现有 RPC 名称，运行意图通过 SaveDraft 后明确发布变更。

本计划的主要入口为公开 HTTP → 真实 Kitex RPC → SQLite → worker；直接 RPC 用于验证不能绕过授权，受控 TLS Kubernetes 目标用于观察实际写请求。计划不是执行结果，实际证据另存并绑定 testedCommit。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| I25-01 | 新建 exporter 固定认证创建身份 | 管理员与普通集群所属用户分别通过 HTTP 新建 KSM；读取列表、详情和直接 RPC | creatorUserId 为实际认证 user ID；creatorRole 为创建时 ADMIN/USER；身份已知且界面可展示 | #25 |
| I25-02 | collector 与冒充字段 | 分别新建 Agent；HTTP 和直接 RPC 携带伪造创建者/角色字段 | Agent 保存真实身份；非法字段被拒绝或不可能成为可写契约；不能覆盖关系 | #25 |
| I25-03 | 管理员实例草稿只读 | 普通用户读取管理员实例草稿，依次保存草稿、删除草稿、记录编辑活动 | 读取成功；每个变更独立返回 FORBIDDEN；草稿修订不变 | #25 |
| I25-04 | 管理员实例发布和生命周期只读 | 普通用户依次预览发布、下发、调整运行状态、预览删除、删除实例 | 所有变更入口返回 FORBIDDEN，目标无新写入 | #25 |
| I25-05 | 管理员实例操作控制只读 | 为管理员实例准备可控制操作；普通用户依次重试、继续、取消 | 各入口返回 FORBIDDEN；操作和执行尝试不发生变更 | #25 |
| I25-06 | 直接 RPC 保护 | 普通用户直接调用 SaveDraft（含 desiredState）、DeleteDraft、RecordEditingActivity、PreviewPublication、PublishConfiguration、PreviewDeletion、DeleteInstance、RetryLifecycle、ContinueLifecycle、CancelLifecycle | 各变更均由服务端拒绝，不能绕过 HTTP | #25 |
| I25-07 | 创建角色快照固定 | 管理员创建后降为普通用户；普通用户创建后升为管理员；重新登录读取并尝试变更 | 原 ADMIN 实例仍受保护；原 USER 身份不变；当前有效管理员仍可管理 | #25 |
| I25-08 | 不新增普通用户间所有权限制 | 用户 A 创建实例，管理员将集群归属转给 B；B 修改实例 | B 按既有集群授权可修改 USER 创建实例；创建用户仍为 A | #25 |
| I25-09 | 派发前重新授权 | 在 beforeDispatch 暂停已受理操作，降级发起管理员或停用账号，再释放 worker | 实际目标写请求为零，操作报告当前失权；实例身份不变 | #25 |
| I25-10 | 已发送后失权仅核验 | 在请求已发送后撤权，返回或注入丢响应，再刷新操作 | 已发结果仍可只读确认；不回滚、不重放，不产生额外目标写入 | #25 |
| I25-11 | 历史未知角色默认保护 | 从真实 v10 schema 迁移包含实例/草稿/版本/任务的数据库；普通用户读取并尝试修改 | 数据关系保留；creatorRole/creatorUserId 未知；普通用户只读；不根据当前角色补造历史 | #25 |
| I25-12 | 访问与外部实例边界 | 无集群权限用户读取受保护实例；管理员读取未纳管实例并尝试变更；读取已注销集群实例 | 集群访问拒绝仍生效；外部实例保持只读；注销集群历史只读 | #25 |
| I25-B01 | 浏览器明确只读区别 | 同一集群分别打开 ADMIN 与 USER 创建的 exporter/collector，普通用户检查创建身份、只读原因、编辑/发布/运行/删除/操作按钮 | 受保护实例可读且说明原因，所有写控件不可操作；普通实例保留编辑能力 | #25 |
| I25-K01.unsent | 真实 Pod 受理后、派发前撤权 | 在 E2E-08 的独占 Helm 平台与 mTLS 真实 API 代理中先完成预览；暂停 worker 的 SelfSubjectAccessReview 只读预检；读取已受理操作 CHECKING 和 possiblySent=false，再降级仍保留集群归属的管理员并释放；读取操作、代理请求及真实 CR | 操作 FAILED 且说明管理员实例只读；实际组件写请求增量为0；真实 CR 返回 NotFound | #25 |
| I25-K01.sent | 真实 Pod 已发请求失权后只读核验 | 使用同一独占平台，由管理员创建并发布已有 UID 的实例；更新时代理在真实 API 完成后丢响应且阻断后续读取；确认 RESULT_UNCERTAIN 后停用发起账号，恢复读取并刷新操作 | 操作只读恢复为 SUCCEEDED；原 UID 与实际新配置匹配；PUT 总数固定，不回滚、不重放 | #25 |

浏览器与真实集群由父规格的最终集成验收执行；本 Issue 局部测试通过不能替代这两层验收。
