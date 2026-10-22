# Issue #27 本地执行结果

本地产品测试的 `testedCommit`：`de9d6448e352030614ddc62a13d2276964ba1cc0`。已包含 #25 的实例身份与写前授权。后续合入集成提交 `ca81a051bbc0932f9fdca64f95dafbb59ca0d94f` 只补验收脚本和证据；产品 Go、IDL、生成代码、前端源码与 testedCommit 相同。

## 本地完整门禁

`make ut PYTHON=/tmp/monitor-mgr-spec23-tools.3nB8bT/venv/bin/python` 通过：18/18 Ginkgo UT、race、产品合并语句覆盖率 **74.96%**（门槛 70%）、IDL 生成一致性、Go vet、前端类型检查与构建、18 UT / 10 E2E 固定目录核对。平台包完整执行耗时 367.663s。`.scratch/ut-results.json` 的 candidate 精确等于上述 testedCommit。

合入最后一份脚本后，`make ut-resource-progress` 再次通过（UT-11/12/16，15.878s），`python3 -m py_compile scripts/e2e_scenarios.py`、目录核对、`git diff --check` 通过。

## 红绿过程

- I27-U01 首轮红灯：发布已成功，但公开操作与版本没有 resources；持久化冻结集合及进度后转绿。
- I27-U07/U08 首轮红灯：平台重启只恢复操作状态，资源仍为 QUEUED；改为资源、操作和尝试同事务恢复后转绿。
- 完整回归曾暴露旧 Agent 名称夹具只改旧来源却残留新冻结目标。将夹具真正裁成 v10（移除新增 creator 列和逐资源表）后重启迁移，原 UID 删除用例转绿；没有放宽产品冻结校验。

## 逐例结果

| 用例编号 | 实际入口与源码 | 实际结果 |
| --- | --- | --- |
| I27-U01 | UT-11，resource_progress_test.go；公开 HTTP/真实 Kitex/SQLite | 通过；版本与操作有同一冻结资源，成功 UID、发送标记及结果持久保留 |
| I27-U02 | UT-14，agent_storage_test.go | 通过；Agent 删除保留 PrometheusAgent 资源结果，既有独立存储核验继续执行 |
| I27-U03 | UT-11，lifecycle_test.go 的原版本人工重试 | 通过；新尝试保留原版本，失败尝试和资源结果均保留 |
| I27-U04 | UT-11，首次创建丢响应与跨进程重启 | 通过；confirmedUid 为空，IDENTITY_UNCERTAIN 保留，禁止同名补认及重放 |
| I27-U05 | UT-11，known-uid 重启子例 | 通过；按原 UID 与冻结配置只读恢复成功，真实 TLS 目标写次数不增加 |
| I27-U06：UID 替换 | UT-11，uid-replaced 重启子例 | 通过；替代 UID 不被认领或覆盖，原 confirmedUid 保留 |
| I27-U06：配置漂移 | UT-11，drift 重启子例 | 通过；同 UID 非冻结配置仍保留待确认，不覆盖漂移 |
| I27-U07：取消 | UT-10，持久派发竞争的 cancel-wins | 通过；未发送资源取消，目标零写 |
| I27-U07：继续 | UT-11，beforeDispatch 跨进程崩溃 | 通过；等待明确继续，新的尝试仍使用原资源与原冻结版本 |
| I27-U08 | UT-11，afterSendMarker 跨进程崩溃 | 通过；单份资源发送标记先持久化，重启不重放，首次创建 UID 不补认 |
| I27-U09：v5 | UT-16，lifecycle_migration_test.go | 通过；真实旧 schema、旧冻结 300m 映射、会话/凭据/UID/尝试保留，升级不写目标 |
| I27-U09：v10 | UT-07 与 UT-16，agent_name_test.go / instance_identity_test.go | 通过；真实 v10 升级仅投影原组件资源，不追加 ServiceMonitor，保留确认身份与旧名称清理能力 |
| I27-K01 | E2E-08，scripts/e2e_scenarios.py:recovery | 未执行；已交付真实代理丢响应/Pod 重建/逐资源读回/写计数/公开收据步骤，等待集成环境执行 |
| I27-B01 | E2E-10，web/tests/phase1.spec.ts | 未执行；已交付资源进度、UID、冻结集合及截图断言，等待集成浏览器执行 |

真实第二 CR 的部分成功、第二资源响应丢失及跨步骤重启，由 #28 实际加入 ServiceMonitor 后验证；本结果不以合成资源或静态列表代替该证明。

## 审查修复：旧冻结身份与可变草稿隔离

本次定向 `testedCommit`：`c82b75a1e4d3f266577c4b927b7ce5bc6d9f5135`，包含最新集成基线 `7f8d853`。补充五列计划完整发布并回读：https://github.com/fjsxy/monitor-mgr/issues/27#issuecomment-6338083215 。

正式 RED：`runtime-red-identity.log` 中公开 HTTP 的旧版本 retry 返回 202，预期 409；与独立审查探针的旧 CR UID 错绑事实一致。新增共同核验后，单一 KSM name 变体 GREEN（`runtime-green-identity-name.log`，7.807s）；扩展四个身份变体与各自明确新发布正控后 GREEN（`runtime-green-identity-all.log`，8.891s）。Agent 新 namespace 的初次正控曾因测试目标漏返回 namespace metadata.name 失败；补齐真实 API 响应字段后转绿，没有放宽产品 namespace 校验。

| 用例编号 | 实际结果 | 证据与范围 |
| --- | --- | --- |
| I27-R01.ksm-name | 通过 | 首次 POST 明确拒绝后合法更名；旧 retry 返回409 CONFLICT，0新增目标请求/attempt，实例不绑定 UID；最新草稿与旧版本保持 |
| I27-R01.ksm-namespace | 通过 | KSM spec.targetNamespace 参与冻结身份比较；部署 namespace 变更后旧 retry 零新增写 |
| I27-R01.agent-name | 通过 | Agent 更名后旧 retry 写前拒绝，原 namespaced CR 不创建 |
| I27-R01.agent-namespace | 通过 | Agent metadata.namespace 参与比较；旧 namespace 零新增请求，新实例身份不被旧 UID 覆盖 |
| I27-R02 | 通过 | 四个变体均重新预览并明确发布当前草稿成功；新 UID 与新目标一致，KSM SM 位于新部署 namespace；旧冻结文档不变 |

提交后 UT-08/10/11/12/16/18 定向组合通过（`runtime-final-focused.log`，52.412s）；生成一致性、Go vet、Python语法、固定18 UT / 10 E2E目录及diff-check通过。受理事务与每份资源/条件写的最终派发事务共用冻结组件身份核验；不修改 SaveDraft 的既有更名语义，不引入产品测试钩子、UI或IDL变化。

此前 `c0a556a215b2dfe796a4a3be73a9026e11c6008f` 已完整 `make ut` 通过18/18、race、产品合并语句覆盖率78.10%，日志 `runtime-ut.log`，收据来源已核对精确SHA。该完整收据不替代本次产品修复后的最终全量门禁；最终集成 head 的完整UT和真实E2E由根协调执行。
