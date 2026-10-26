# #26 实施结果与集成交接

源码提交：`bdd5a25f240a67aa5f419ecfe1c8e8fe6b59aa76`；分支 `codex/spec23-v2-issue26`。本项已 merge 集成基线 `6dc4cd24101a090419c47ae10946e6158ea9ed22`，v11–v14 原迁移和 #25 派发授权均保留，新增 v15。

计划已在编码前完整发布并逐字回读；修订 2 仅把原有必选输入展开为独立行，未新增行为：
- [首次计划](https://github.com/fjsxy/monitor-mgr/issues/26#issuecomment-6414354883)
- [逐输入展开的当前计划](https://github.com/fjsxy/monitor-mgr/issues/26#issuecomment-6414702889)

## 已实现

- 历史创建用户只依据唯一 CREATE_INSTANCE 结果的稳定实例 ID、不可变 cluster/component、存在的实际 actor 补齐；合法发布前 name/namespace 变化不破坏关系。缺失、矛盾和无效对应保持未知，不推断历史角色。
- 类型化 `ConfirmInstanceManagement` RPC 与 `POST /api/v1/instances/:id/management-confirmations` 仅允许当前有效管理员确认历史未知角色的纳管实例。确认记录追加 actor/time/result，幂等重复不追加，旧键不能绕过降级后的当前授权。
- 确认与实际派发共用锁，撤销提交后未发送普通用户任务拒绝写入。已知 ADMIN 保护优先，外部/已注销实例不可确认。
- KSM/Agent 抽屉展示未知事实及最新确认；仅管理员看到明确确认选择。允许后的普通 owner 可真实保存非身份配置；已有 UID 的身份字段仍固定。
- v10/v11 真正裁除后续字段/表；v11 字段与旧 v13 确认/资源表来自独立旧 DDL 夹具。v15 复用并核验旧确认结构，保留原确认、UID、冻结版本、任务、密文和审计；冲突 DDL 失败保持原库及本次一致快照，移除冲突后可恢复。

## 本地已执行

| 用例编号 | 实际结果 | 入口/证据 |
| --- | --- | --- |
| I26-01.ksm / I26-01.agent | 通过 | UT-16，分别对应可靠 KSM/Agent 独立 v10 输入 |
| I26-01.rename-ksm / I26-01.rename-agent | 通过 | UT-16，合法首次发布前改名和 namespace 变化 |
| I26-01.v11-unknown / I26-01.v11-admin / I26-01.v11-user | 通过 | UT-16，独立旧 v11 DDL 字段，未知补齐与已知事实保留 |
| I26-01.v13 | 通过 | UT-16，独立旧 v12/v13 DDL，原确认/冻结资源/UID 保留；只读轮询无 exporter/SM 写入 |
| I26-02 | 通过 | UT-16，创建记录缺失不推测 |
| I26-03.actor / target / cluster / component / id / missing-actor / malformed | 各输入通过 | UT-16，每个输入有独立 By 与 ReportEntry |
| I26-04 | 通过 | UT-07，确认结果、普通 owner 保存、列表/详情/分页、重启保留，历史事实不改 |
| I26-05 | 通过 | UT-07 拒绝/撤销；UT-10 在 beforeDispatch 撤销，实际目标零写入 |
| I26-06 | 通过 | UT-07，HTTP/RPC 越权拒绝、缺决策/未知字段/错误类型拒绝、已知 ADMIN/USER 不可确认、外部和注销实例拒绝 |
| I26-07 | 通过 | UT-07，相同键幂等、变内容 CONFLICT、当前管理员降级后拒绝旧键 |
| I26-08.v10 / I26-08.v11 | 各输入通过 | UT-16，本次原库/快照保留，移除 DDL 冲突后恢复公开 UID/版本/任务 |

TDD 记录：首次 UT-16 因 creatorUserId 未补齐而失败，接入迁移后通过；首次 UT-07 确认入口返回 404 而失败，接入 RPC/HTTP/持久确认后通过。日志保留在本机 `/tmp/monitor-mgr-spec23-20261026/issue26-*.log`。

最后一次 `make ut-historical-identity`：UT-07、UT-10、UT-16 均 passed，各一次，无测试级 retry；其余 13 个 platform It 因局部 focus 未执行。原生报告：`internal/platform/.scratch/issue-26.json`。这不是完整 18 UT 结果。

`make web-build`、`make vet`、`make check-generated`、`make ci-cases-check`、`git diff --check` 通过；固定清单为 18 UT / 10 E2E。`go test ./tests/e2e` 仅做编译，缺少真实调度 socket 时不执行真实 E2E。Python 升级/场景编排语法检查通过。

## 真实验收代码与尚未执行

I26-K01 编排已加入 E2E-01：独占 Helm/PVC 真实降到 v10，原 KSM UID/RV 下去掉旧版不存在的字段，升级后补齐/确认/轮询零自动写入，允许普通 owner 后明确发布 disabled 采集配置，撤销后实际保存被拒。原始白名单观测存 `self.evidence['issue26']`；Go `tests/e2e/issue26_evidence_test.go` 的 `assertIssue26Evidence` 逐项断言，缺证据失败。

共享证据 registry 由 #28 交付，集成时需注册：`registerIssueEvidence("issue26", assertIssue26Evidence)` 和 `requireIssueEvidence("E2E-01", "issue26")`。本项未修改共享 event/suite，避免并行冲突。

I26-B01 夹具和断言已加入原 E2E-10：已发布历史 KSM、未发布且缺 creator 的 Agent；管理员确认允许/拒绝，普通用户真实保存 CPU 并核对精确 revision，已有 UID 身份固定，注销后隐藏确认且保留只读记录。

以上真实 kind、Chromium 尚未执行，完整 UT/race/覆盖率和远端 CI 尚未执行，由父规格集成后统一验收。云 CI 的既有环境限制不能写为本项通过。未操作 kind，未关闭 Issue，未创建独立 PR。

## #28 / #29 / #30 接口

`ObservationInstance.creatorUserId == ""` 表示创建用户未知；`creatorRole == ""` 表示创建时角色未知；`creatorIdentityKnown` 只有两项均已知才为 true。`managementConfirmation` 只改变当前管理权限，不补造身份。后续采集发布/快捷关联须以 creatorUserId 是否存在为判断，不以 canManage、确认允许、owner 或当前 actor 代替。I26-09 的依赖 user 标签发布/快捷关联及自定义 selector 联合验证留待 #28/#30 集成执行。

v15 身份迁移和确认不改旧版本或历史资源执行事实；#29 可直接消费稳定创建 user ID 与独立确认结果，并复用 #27 原历史资源接口。
