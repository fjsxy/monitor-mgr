# #28 本地自动化结果

完整门禁 testedCommit：`ddeeacf9a02900a5d1bd0bee92eb9eec5bc6fdbc`，已合入集成分支 `e3f9e9efbe8caa1a8349e890a6648ba5fee68f15`。本记录不表示真实集群、浏览器或远程CI验收通过。

执行 `make ut PYTHON=/tmp/monitor-mgr-spec23-tools.3nB8bT/venv/bin/python` 退出0：18/18 Ginkgo、race、产品语句覆盖率 **77.70%**（门槛70%）、生成一致性、vet、前端类型检查/构建均通过。平台包465.513s；`.scratch/ut-results.json` candidate已核对此SHA。原日志在本次实施交接目录的 `issue28-ut.log`。

| 用例 | 实际结果 | 证据与范围 |
| --- | --- | --- |
| I28-U01 | 通过 | 公开HTTP→真实Kitex→SQLite/worker：草稿零业务写，两资源预览、版本和独立UID |
| I28-U02.deployment/stateful/daemon/long | 通过 | 公开预览核对锁定Operator已知规范化字面值、三模式共同标签及指标端口；实际Service由K01另验 |
| I28-U03.crd/namespace/create/update/foreign/race | 通过 | 各必要输入独立执行，预检拒绝且无新增组件写，不接管外部SM |
| I28-U04及uid/drift | 通过 | 第二资源拒绝后保留exporter；编辑草稿不改变原版本；retry仅补SM；前序UID/漂移异常停止补写 |
| I28-U05.first/known | 通过 | 外部TLS目标模拟实际写入后丢响应，真实平台Runtime重开；未知SM UID不补认、已知UID只读恢复，写计数不增 |
| I28-U06 | 通过 | exporter成功后、SM派发前真实账号降级，SM零写，成功组件结果保留 |
| I28-U07 | 通过 | SM替代UID拒绝、漂移需明确覆盖、两份CR条件删除后整体完成 |
| I28-U08 | 通过 | 真实旧schema迁移仍仅component，冻结来源/UID/发送边界保持；不暗加SM |
| I28-K01/K02、I28-B01 | 未执行 | 已在现有E2E-03/08/09/10及唯一Playwright test接线；由根在最终同源独占环境实际运行 |

首条RED为预览缺少SM（`issue28-red1.log`），UT-08 GREEN见`issue28-green1b.log`。新增第二资源暴露旧数量/只读恢复/预检屏障假设，修正后UT-08/10/11/12/16通过（`issue28-green3.log`，26.280s；扩展后`issue28-focused-final.log`，27.873s）。合并#26后旧迁移夹具DTO不匹配为RED，正确裁旧单资源后UT-07/10/16通过（`issue28-merge-green.log`，25.599s）。上述局部门禁不能替代完整18项结果。

完整门禁之后仅补充真实验收Python接线与文档：修正原创建响应identity字段取证、按独立原UID统计请求、为受限namespace夹具明确授予该namespace的SM权限。Python `py_compile`、固定目录检查通过；没有据此宣称真实API已通过。

另在远程旧候选`e3f9e9e`的E2E-09公共收据中观察到fixture finalizer patch失败，尚无私有stderr/HTTP code，**不能确认根因为RV推进**。夹具仅对明确`/metadata/resourceVersion` JSONpatch test失败或标准“object has been modified”Conflict，在重新读取确认同原UID+新RV后最多尝试三次；其他失败原样停止，并保存安全分类收据。该改动仍待真实重跑，不记为已修复通过。

## 真实 CI 验收缺口补充

补充计划 I28-R01–R04 已完整发布并逐字回读：https://github.com/fjsxy/monitor-mgr/issues/28#issuecomment-6337768012 。

原实际诊断候选 `ade5ce9d79d317e1f41b179308947fbde14f2b7d` 的独占 storage 重跑：E2E-05通过、E2E-09失败、cleanup=true。私有最后 Operation 的安全投影明确为 `WAITING_CONFIRMATION`，SM `SUCCEEDED/possiblySent=true`，component `QUEUED/possiblySent=false`。这确认原验收把 SM 异步删除阻塞误当成 KSM finalizer 阻塞；不能靠延长 timeout 或在 refresh 中补写组件解决。

- I28-R01 RED：旧代理通过真实 loopback 控制 HTTP 返回409拒绝精确SSAR条件，见 `runtime-red-proxy.log`；新增严格KSM SSAR白名单后 UT-18 GREEN（`runtime-green-proxy.log`，6.481s），并验证SM/畸形/dryRun不消费规则、旧无正文规则兼容。上游故意不可达，仅证明HTTP选择/暂停，不宣称Kubernetes/mTLS验收通过。
- I28-R02/R03：公开 HTTP→Kitex→SQLite→受控TLS目标覆盖已发送组件finalizer恢复，以及SM异步删除→只读确认→未发组件WAITING→重新预览/明确继续同版本新attempt→组件finalizer→只读成功。每份原UID/RV DELETE严格一次；UT-12通过，见 `runtime-delete-probe.log`（11.870s）。
- I28-R04：修正后的E2E-08先真实202/operation、再确认KSM SSAR屏障与possiblySent=false后撤权。E2E-09先证明原组件已发、实际deletionTimestamp与本例hold在场才移除hold；只读确认整体原图清空，原SM/KSM实际DELETE各一次，另一实例UID/spec保持。白名单收据为 `instance-dispatch-unsent.json` 与 `companion-deletion-recovery.json`；修后真实E2E仍待根最终同源执行。

完整 `testedCommit=c0a556a215b2dfe796a4a3be73a9026e11c6008f` 执行 `make ut PYTHON=/tmp/monitor-mgr-spec23-tools.3nB8bT/venv/bin/python` 退出0：18/18、race、产品合并语句覆盖率 **78.10%**、生成一致性、vet、前端构建及目录核对通过，平台包525.378s（`runtime-ut.log`）。已合最新集成 `7f8d853`。后续审查修复 `c82b75a` 的定向UT-08/10/11/12/16/18再次通过（52.412s）；两份收据分别保留，不把旧全量当成修复后全量。
