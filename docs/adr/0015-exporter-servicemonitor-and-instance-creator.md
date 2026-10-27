# monitor-op 管理 exporter 配套 ServiceMonitor，平台配置采集意图与 Agent 关联

2026-10-23 用户确认：exporter 的工作负载、Service 及配套 ServiceMonitor 统一由 monitor-op 创建并持续调谐；monitor-mgr 负责采集配置意图、实例创建归属、权限及 Agent 关联。此决定替代 2026-10-21 由平台直接创建、更新和删除配套 ServiceMonitor 的方案：配套配置与 exporter 生命周期绑定，其 Service selector、端口和拓扑应由同一个 Operator 保持一致，避免平台复制 Operator 内部规则并协调第二份 CR 的写入。

保存草稿不写集群；平台在明确配置下发时，将采集开关、参数及受控标签写入 exporter CR，不直接写配套 ServiceMonitor，也不修改 Operator 的 Service。新发布版本冻结 exporter CR 内的采集意图，不将 Operator 生成的 ServiceMonitor 当作平台另行下发的资源；页面只读展示配套配置、观察结果与故障原因。配置下发结果、Operator 收敛及实际采集结果分别表达。

ServiceMonitor 支持为可选能力：exporter CR 未声明或未启用时默认关闭，避免 Operator 升级后为已有实例自动补建。平台新建 exporter 的草稿默认开启配套采集，明确发布时才生效；历史实例只在新的明确发布中选择启用。开启时检查 ServiceMonitor CRD、Operator 的管理能力及权限，缺失时明确报告原因；关闭时纯 exporter 部署不依赖该 CRD，未安装 CRD 不能阻断 Operator 启动和其他实例调谐。monitor-op 不安装 Prometheus Operator，也不创建或选择 Agent。

一个 exporter 对应一个配套 ServiceMonitor，由 monitor-op 派生稳定名称，选择该实例全部必要的指标 Service 与正确命名端口，覆盖现有合法部署模式及长名称。当前 KubeStateMetrics CR 是 cluster-scoped，ServiceMonitor 位于 `spec.targetNamespace`，controller ownerReference 指向该 CR 的实际 UID；不能把 CR 的空 namespace 当作部署 namespace。泛化到 namespaced exporter CR 时遵守同 namespace ownerReference 约束，不扩大本次产品类型。

ServiceMonitor 的 `metadata.labels["monitor-mgr.io/user-id"]` 由平台从实例创建用户的稳定 user ID 生成，经 exporter CR 的受控配置传给 monitor-op，再由 monitor-op 投影到配套对象。Operator 仅处理通用配置和标签，不解释平台账号、角色或授权；平台标签不能覆盖 Operator 保留的身份标签。创建关系固定，不随发布操作人、集群所属用户或集群归属转移改变；管理员创建的实例记录管理员自身的 user ID。

Agent 提供按用户关联 exporter 的快捷配置，默认使用 Agent 实例创建用户的 user ID，投影为原生 `serviceMonitorSelector`，并展示目标用户。保留现有标签配置与匹配预览；跨 namespace 关联同时配置相应 namespace 范围。管理员编辑已有 Agent 时不因当前登录用户变化而改变快捷配置的关联目标。

用户进一步确认：平台管理员创建的实例禁止普通用户修改。实例访问仍以既有集群权限为前提，实例变更还需检查这一限制；保护覆盖 exporter 与 collector，以及草稿编辑、配置下发、期望运行状态调整、删除和相关重试、取消操作。页面、后端请求入口与后台任务派发前均须遵守，不以 ServiceMonitor labels 代替平台授权判断。已经发送的请求继续只读核验真实结果，不因后续失权回滚或重放。此规则修订 ADR0007 与 ADR0009 中单纯继承集群权限的实例变更边界。

实例创建时记录并固定创建用户及其当时角色，按创建时的管理员身份保护实例。管理员后来降为普通用户，其先前以管理员身份创建的实例仍受保护；普通用户后来升为管理员，不改变旧实例的创建身份。当前操作者是否有权变更仍按其当前有效身份判断。

历史实例无法确定创建时角色时，默认禁止普通用户修改，由管理员明确确认后才开放相应管理权限。保留角色未知的历史事实，权限确认独立记录，不用账号当前角色倒推或补造历史创建角色。

已有实例从可靠的创建记录补齐实例创建用户，无法核实的记录标记为未知，不以集群所属用户或首次发布用户代替。历史创建身份和管理员实例保护规则保持有效。

配套 ServiceMonitor 创建、更新或清理失败时保留已运行的 exporter，由 monitor-op 根据已发布的期望态持续重试并报告故障，不要求平台重发第二份 CR。挂起保留配套配置，停用删除全部受管资源，重新启用按声明重建；关闭配套采集仅清理本实例拥有的 ServiceMonitor。实例删除由平台按已确认 UID 删除 exporter CR，由 Operator finalizer 与所有权机制清理配套资源，平台只读核实清理结果。同名外部对象不得接管；Operator 对其拥有的配套对象负责漂移修复和缺失重建，平台不以子资源正常重建后的 UID 变化判定 exporter 身份丢失。

平台首次创建 exporter CR 响应丢失、UID 未确认时，仍遵守 ADR0011 的禁认领、只读核验与恢复禁重放规则。平台失权阻止后续平台写入，不撤销已发布的期望态，也不把平台账号校验交给 Operator。旧冻结版本、任务及执行记录保持原样；涉及旧方案 ServiceMonitor 写入的未完成任务不得在升级后自动续写或转换为新模式。若已存在平台创建的 ServiceMonitor，先停止旧写入、核实实际 UID 及采集影响，再由集群管理员显式处理旧对象，最后通过新发布启用 Operator 管理；不自动接管，也不允许两个管理者并行维护或留下重复采集。

对应规格：[exporter 配套 ServiceMonitor、实例创建归属与管理员实例保护规格 #23](https://github.com/fjsxy/monitor-mgr/issues/23)。两仓库改动范围见 [职责调整范围](../planning/issue-23-operator-servicemonitor-scope.md)。2026-10-23 回读确认 [PR #31](https://github.com/fjsxy/monitor-mgr/pull/31) 仍未合并，其实现和既有验收对应旧方案，不能证明本次职责调整已实现或验收通过；本轮只更新决策、规格和范围。

2026-10-26 实施更新：旧 PR #31 已按用户要求关闭且未合并；从 main `0238eba7cf0a6482236accb2f0a13acb7087ad5c` 的新集成分支实施 #25–#30。Operator #86/#87 已完成，可安装候选为 `0.2.0-rc.4`；详细锁定信息以平台 #28 的当前正文为准。
