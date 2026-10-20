### U05 前置失败：名称派生边界修复前测试计划

本轮 C10 增量实际为 13 项通过、U05 准备阶段失败、U06 未执行；原 index、失败和清理结果均保留，REG101 尚未启动。原通过 192 项及本轮通过 13 项不从头重跑。

根因已用原 StatefulSet revision 和 Kubernetes v0.28.15 的真实 ValidateLabels 验证：44 字符 Agent name → 55 字符 StatefulSet name → 66 字符 controller-revision-hash，超过标签上限 63；仅缩父名称、保持相同 hash 和标签键即可通过。规格 #13 要求符合固定 Operator 的命名约束，因此修复前后端的派生 revision 预算，并让 U05 夹具使用有界且可追踪的测试名；不延长等待、不放宽 Ready/PVC 判定、不改固定 Operator。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 issue 编号 |
| --- | --- | --- | --- | --- |
| REG21-AGENT-NAME-001 | 前后端执行同一固定 Operator 名称边界 | 通过实际配置校验和 HTTP/Kitex 创建、保存/预览路径，覆盖单 shard 最长合法名及超一字符、原44字符失败名、多 shard 最长名称与 shard 位数变化；在真实 Chromium 检查名称输入的 maxlength、提示和非法值阻止。按固定 v0.28.15 ValidateLabels 核派生 revision 标签。 | 预留 StatefulSet revision 的连接符和最长10字符hash；单 shard 最长41字符，多 shard 依据最长派生 StatefulSet 名称进一步扣减。合法边界通过，超长在写入 CR 前明确拒绝；直接 API 不能绕过，前端限制及提示一致。 | #21 |
| REG21-U05-FIXTURE-001 | 修复测试编排长 runId 意外生成非法 Agent 名称 | 用本轮原长 prefix、其他 prefix/index 经过真实 U05 prepare 名称生成路径；检查实际传给创建/运行读取的名字、唯一性及原 UID 清理关联。 | 测试名在固定 Operator 派生约束内且不同身份不冲突；不改用户填写名称、不复用旧 CR/PVC，不用改短 runId 掩盖问题。 | #21 |
| U05.primary | 真实 Pod 上验证版本快照和大 i64，重新执行原失败场景 | 使用修复候选的原生业务入口，仅选择 U05；实际 Agent ST/Pod/PVC 就绪后执行原版本浏览器检查和 REG19-HTTP-I64-001 浏览器子步骤，并执行新增名称边界浏览器断言。 | 原版本/失败发布/配置恢复行为正确，大 i64 保留原值；三个浏览器检查实际通过、无 skip/retry，逐原 UID 清理成功。新增断言独立列明，不伪造成另一完整 profile。 | #21 |
| U06.primary | 完成此前未执行的 YAML 与运行导航验收 | 同一增量批次在 U05 通过后执行原 U06；检查真实 YAML 左右 diff 与确认发布后的运行信息导航。 | 原断言通过，配置/运行展示一致且清理成功。 | #21 |

后续仅执行上述相关新增回归、失败 U05、未执行 U06，以及此前已经批准但尚未启动的 REG101。独立 Q i64 已通过，不重复；REG18 pattern 的 B 关联保留本轮 U02 的真实20 DOM原件，i64 的 B 关联必须来自新 U05 的实际浏览器原件。中央207/104 seed与严格门禁不改，不生成跨源码伪全通过 index。

本次沿用户已确认范围完成增量验收和 PR；Ginkgo、UT覆盖率≥70%与Helm迁移不追溯加入本次交付，后续开发遵循新规约。新增测试计划与此评论相同内容将保存到 docs/e2e/issue-21-u05-name.md 并随修复提交。
