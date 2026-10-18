# #20 E2E 测试用例

实施前 E2E 计划；发布并回读后才能开始实现。

所属任务：[Agent 存储、分片与实例生命周期](https://github.com/fjsxy/monitor-mgr/issues/20)。本地副本：`docs/e2e/issue-20.md`。

以下为计划，不代表执行通过。完整层级、profile、前置、清理与证据要求继承固定 `docs/acceptance/phase1.json`；稳定子例及原定语义保持不变。每例独立建立前置，只操作本 runId 资源，故障 finally 恢复，凭据不得进入报告。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| A02.primary | Agent shards与挂起恢复 | 设置多个shards，下发挂起再恢复；回读每组StatefulSet及Pod数量。 本子例输入：设置多个shards，下发挂起再恢复；回读每组StatefulSet及Pod数量。。 | 每shard单副本；运行/挂起分别replicas=1/0，paused保持false；实际数量与运行摘要一致。 | #20 |
| A06.primary | emptyDir不创建PVC | 首次创建时选择emptyDir并下发、挂起和删除。 本子例输入：首次创建时选择emptyDir并下发、挂起和删除。。 | Pod使用emptyDir；不创建WAL PVC，不显示虚构的PVC保留/删除结果。 | #20 |
| A07.retain | whenScaled作用于挂起缩零 | 参数化Retain/Delete，先单独发布并核验实际策略，再挂起。 本子例输入：先确认实际 whenScaled=Retain，再挂起缩零。 | Retain 保留原 PVC | #20 |
| A07.delete | whenScaled作用于挂起缩零 | 参数化Retain/Delete，先单独发布并核验实际策略，再挂起。 本子例输入：先确认实际 whenScaled=Delete，再挂起缩零。 | Delete 最终删除相应 PVC，独立保存证据 | #20 |
| A08.retain | whenDeleted作用于删除 | 参数化Retain/Delete，先确认策略再删除Agent，分别读取CR、workload、Pod和PVC。 本子例输入：先确认实际 whenDeleted=Retain，再删除 Agent。 | CR/workload/Pod 全消失才完成；PVC 保留不阻止完成 | #20 |
| A08.delete | whenDeleted作用于删除 | 参数化Retain/Delete，先确认策略再删除Agent，分别读取CR、workload、Pod和PVC。 本子例输入：先确认实际 whenDeleted=Delete，再删除 Agent。 | 实例完成与 PVC 删除分别回读，不以 CR 消失代替卷核验 | #20 |
| A09.retain | 减少shards的实际PVC结果 | 运行多shard，确认whenDeleted策略后减少shards，观察被移除StatefulSet及卷。 本子例输入：多个 shard 已运行且 whenDeleted=Retain，然后减少 shards。 | 仅已移除 StatefulSet 的卷按 whenDeleted 处理，保留 shard 不受损；不能误用 whenScaled | #20 |
| A09.delete | 减少shards的实际PVC结果 | 运行多shard，确认whenDeleted策略后减少shards，观察被移除StatefulSet及卷。 本子例输入：多个 shard 已运行且 whenDeleted=Delete，然后减少 shards。 | 仅已移除 StatefulSet 的卷按 whenDeleted 处理，保留 shard 不受损；不能误用 whenScaled | #20 |
| A10.fixed-mode | 容量与模式变更必须新实例 | 首次创建前自定义容量；创建后尝试改容量/存储模式/name/namespace，再按新实例重建。 本子例输入：创建成功后切换 PVC/emptyDir。 | 后端拒绝，需要新实例 | #20 |
| A10.new-instance | 容量与模式变更必须新实例 | 首次创建前自定义容量；创建后尝试改容量/存储模式/name/namespace，再按新实例重建。 本子例输入：复制旧配置后提供新 name/身份并创建。 | 新 CR UID 和新 PVC，不复用 Retain 残留卷 | #20 |
| P14.policy-pending | 清理阻塞和策略未生效不能报成功 | 参数化未采用新PVC策略的危险操作、带finalizer的删除、CR已没但Pod残留及辅助读取失败。 本子例输入：策略尚未作用于相关 StatefulSet 就挂起/减少 shards/删除。 | 先阻止，单独发布策略并确认后才能继续 | #20 |
| R03.agent-zero | 缺status与合法零值分开 | 参数化无status、字段合法省略、有效挂起/停用/零匹配及已删除。 本子例输入：Agent replicas=0 且有效状态确认。 | 挂起与零副本分开解释，不因 Available=True 显示运行中 | #20 |
