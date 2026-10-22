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
