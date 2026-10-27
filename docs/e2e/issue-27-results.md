# Issue #27 本地实现与验证结果

计划：[2026-10-26 完整修订与成功旧步骤补充](https://github.com/fjsxy/monitor-mgr/issues/27#issuecomment-6414415755)，已与 `docs/e2e/issue-27.md` 逐字回读一致。

本分支已合并集成提交 `c320d632091d684fa5d0e5ae456505dc9e9c1476`，保留 #25 的真实 v11 创建身份迁移、后台写前授权和页面 `canManage` 约束。v12 保存与旧 PR31 相同的资源表，v13 仅兼容保留旧确认表，v14 新建独立 `historical_resource_observations`，不会用观察覆盖旧 UID、发送标记、state 或执行消息。旧 v13 表在任何升级 DDL 前检查结构。

## 已执行

- `testedCommit=0a5daa1c91a03a38be5fc522e0fe8692ea04d103`：`make test-issue27`，公开 HTTP → 真实 Kitex → SQLite → worker → 受控 TLS Kubernetes API；UT-07/10/11/12/13/16 六组全部通过，包含 race，耗时 215.196 秒。其余十组未选择执行，没有把定向过滤算成完整验收。
- `testedCommit=e5719c5`：追加旧 SM `SUCCEEDED` 夹具后，UT-11 全组通过，12.698 秒；v12/v13 × queued/sent/unknown/succeeded 八种输入均实际执行，每种两次 Runtime/Kitex/SQLite 重启，旧原路径 GET，SM 写请求为 0。成功的旧 SM 也保留原名称、UID、YAML 和查询结果。
- 合并后 `make generate`、`make web-build`、`make vet`、`make ci-cases-check`、`git diff --check` 通过；固定目录仍为 18 UT / 10 E2E。

| 用例编号 | 实际结果 | 已执行接缝与依据 |
| --- | --- | --- |
| I27-U01 | 通过 | UT-11：KSM 单资源版本、UID、发送边界与持久恢复 |
| I27-U02 | 通过 | UT-11/13：Agent 原 namespace/name/UID、UID/RV Foreground 删除及独立存储保护 |
| I27-U03 | 通过 | UT-11：拒绝后的新尝试仍使用原配置，草稿不混入 |
| I27-U04 | 通过 | UT-11：首次创建丢响应、重启、refresh/retry/continue/cancel 均不认领或重发 |
| I27-U05 | 通过 | UT-11：已知 UID 更新丢响应只读核实，不重放 |
| I27-U06 | 通过 | UT-11：同名替换 UID 与原 UID 漂移保留保护 |
| I27-U07 | 通过 | UT-10/11：未发取消及真实子进程退出后的明确继续 |
| I27-U08 | 通过 | UT-10/11：afterSendMarker 真实进程退出，逐资源发送标记先持久化 |
| I27-U09 | 通过 | UT-16：精确 v5 失败回滚、v10/v11 单 CR 升级；UT-11 独立 v12/v13 资源 schema 与旧任务 SQL 夹具 |
| I27-R01.ksm-name | 通过 | UT-11：旧重试 409、无新增 attempt/目标写，旧版本与新草稿保持 |
| I27-R01.ksm-namespace | 通过 | UT-11：旧 targetNamespace 不与新草稿错绑 |
| I27-R01.agent-name | 通过 | UT-11：Agent 更名后旧重试零写 |
| I27-R01.agent-namespace | 通过 | UT-11：Agent namespace 变化后旧重试零写 |
| I27-R02 | 通过 | UT-11：四种新身份均可重新预览并明确发布，真实请求与新 UID 一致 |
| I27-C01.queued | 通过 | v12/v13：旧未发 SM 状态原样保留，全部续写入口拒绝 |
| I27-C01.sent | 通过 | v12/v13：旧已知 UID 只读核验，原 possibly_sent/state/message 不改写 |
| I27-C01.unknown | 通过 | v12/v13：同名同 spec 对象不补认 UID，显式历史待处理 |
| I27-C01.succeeded | 通过 | v12/v13：成功旧 SM 仍按 instanceID 及原 operation/resource 身份可见，不视为已清理 |
| I27-C02 | 通过 | UT-11：新 KSM/Agent 均只有实际组件 CR，无第二份 SM 派发目标 |
| I27-C03 | 通过 | UT-12/13：原 UID/RV 删除、owner UID 图及存储保护；不同 UID 不接管 |
| I27-C04 | 通过（受控目标） | UT-11：注入 Operator 子资源故障状态，CR 写入成功与观察故障分别表达，写计数不增加 |
| I27-K01 | 未执行 | 交主任务串行执行真实 Helm Pod 重建与 kind 验收 |
| I27-B01 | 未执行 | 页面已实现并完成类型检查/构建；真实浏览器由集成验收执行 |

## 早期诊断结果的使用限制

早期 `make ut PYTHON=/tmp/monitor-mgr-spec23-20261026/python-env/bin/python` 已实际退出 0：18/18、race、产品语句覆盖率 76.89%。该命令运行期间工作树追加了修改，因此**不绑定最终提交，也不能作为最终集成通过证据**。其 `.scratch/ut-results.json` 的 candidate 是结束时读取的 HEAD，不能反推测试编译来源。最终完整 UT、真实集群、浏览器、远端 CI 和清理收据仍由主任务在冻结的集成提交上执行。

本地原始日志位于 `/tmp/monitor-mgr-spec23-20261026/issue27-*.log`；未上传原始日志、数据库或凭据。

## #28/#29 接续边界

本项只保证旧任务自动派发、重试、继续、取消、重启恢复均不能续写 SM；新模式只允许实际 KubeStateMetrics/PrometheusAgent CR。没有新增无法解除的永久实例保护。

公开盘点：`GET /api/v1/operations?instanceId=<id>`，不传 `idempotencyKey`，返回所有原 operationId/attemptId/versionId 与 resources；成功的旧 SM 也保留其中。事务内查询：`historicalServiceMonitorSteps(ctx, q, instanceID)`，返回所有旧 SM 步骤（含成功），原 operation/attempt/resource key、冻结 path/document、originalUID、confirmedUID、possiblySent、state、pending。调用者先核验实例授权。

#28 新发布预检与 #29 显式盘点/处理须覆盖这些旧对象与 Agent 的实际采集影响，包含不同旧名称，不能只检查新 Operator 同名对象或只过滤 pending。后续处理结果须独立记录，不能把旧失败/未知任务伪造成功；旧 v13 确认事实由 #26 的 v15 入口接续，不在本项补造。

Issue 未关闭，未新建独立 PR；独立 worktree 保留供 merger 使用。
