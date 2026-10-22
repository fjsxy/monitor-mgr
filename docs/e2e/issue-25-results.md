# #25 局部执行结果

产品 testedCommit：`0778acf7c84533a1d1b2ecba8a112cfe57b22a71`。测试计划位于 `docs/e2e/issue-25.md`，最新完整计划评论已回读一致：<https://github.com/fjsxy/monitor-mgr/issues/25#issuecomment-6336357215>。

| 检查 | 实际结果 | 证据与范围 |
| --- | --- | --- |
| 身份保存红绿 | 先失败，后通过 | 新建 KSM/Agent 未返回创建身份时为 RED；服务端固定认证 user ID 与创建角色后通过；HTTP 冒充字段拒绝 |
| 前台写保护红绿 | 先失败，后通过 | 普通集群所属用户原可 PUT 管理员实例草稿（200）；修复后 HTTP 与真实直接 RPC 各写入口均 FORBIDDEN；列表/详情返回当前管理能力与原因；草稿及状态刷新可读 |
| 派发前失权红绿 | 先失败，后通过 | 原 worker 在管理员降为普通用户但保留集群归属后仍 SUCCEEDED；修复后发布及删除均 FAILED，实际目标写入为0 |
| 角色与归属变化 | 通过 | 创建时 ADMIN/USER 快照不随升降权改变；普通用户仍能按集群授权管理另一普通用户创建的实例；无集群权限访问仍拒绝 |
| 已发送后失权 | 通过（受控 TLS 目标） | 已知 UID 更新已发送后降级并丢响应，随后只读恢复 SUCCEEDED；目标写总数固定，不回滚、不重放 |
| 历史迁移 | 通过 | 从真实 v10 字段形状迁移，创建角色未知默认对普通用户只读；保留 UID、草稿、版本、任务和当前管理员管理能力 |
| 完整 `make ut` | 通过，18/18 | 使用隔离 Python 环境执行；包含 `-race`、生成一致性、`go vet`、前端类型检查/构建及真实 HTTP→Kitex→SQLite 行为 |
| 产品语句覆盖率 | 77.15%，通过 | `internal/control` 与 `internal/platform`，门槛70%；生成代码不计入产品覆盖率 |
| Playwright 场景发现 | 通过，1个 E2E-10 | 新 I25-B01 接入现有独占 `PHASE1_E2E_FIXTURE`；仅场景发现，未执行浏览器 |
| 真实 Pod 步骤静态检查 | 通过 | `scripts/e2e_scenarios.py` 可编译，固定目录仍为18 UT/10 E2E；不是实际集群通过证据 |

本地命令：

```sh
make ut PYTHON=/tmp/monitor-mgr-spec23-tools.3nB8bT/venv/bin/python
make ut-instance-identity
npm --prefix web run test:e2e -- --list
```

`UT-07` 执行创建事实、HTTP/RPC 保护及角色快照；`UT-10` 执行发布/删除写前失权和已发结果核验；`UT-16` 执行未知角色迁移。具体行为断言在 `internal/platform/instance_identity_test.go`，均接入既有 Ginkgo `It/By`，没有增加或伪造固定用例计数。

以下检查尚未执行，由父规格同源最终验收负责：

- I25-K01.unsent / I25-K01.sent：`E2E-08` 的 `instance_identity_authorization` 通过公开 HTTP、真实 Helm Pod 和真实 Kubernetes API 执行；未发送场景暂停的是 SelfSubjectAccessReview 只读预检，并同时核对操作 CHECKING、attempt.possiblySent=false、账号降级后仍为集群所属用户、组件写请求增量为0及真实 CR NotFound。已发送场景在真实更新完成后丢响应并阻读，再停用原发起账号，恢复读取后核验原 UID 和实际配置，禁止额外组件写入。
- I25-B01：`E2E-10` 分别检查管理员/普通用户创建的 KSM 与 Agent，在同一集群展示创建身份、只读原因、禁写与可编辑状态，以及允许的只读状态刷新。
- 远程 CI。

局部自动化通过不代表 #25 或父规格的真实集群、浏览器和远程 CI 已全部验收。
