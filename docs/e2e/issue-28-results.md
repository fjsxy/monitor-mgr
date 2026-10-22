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
