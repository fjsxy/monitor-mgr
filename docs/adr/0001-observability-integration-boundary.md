# 一期复用已有 CR 与 Operator，明确平台的集成边界

一期采用现有 monitor-op 的 KubeStateMetrics 和上游原生 PrometheusAgent 作为配置下发契约，由各自的 Operator 执行组件调谐。本地测试工具准备 kind 集群和两个 Operator，平台承担 kubeconfig 注册、能力检查、观测配置下发与状态回读，以复用既有部署逻辑并避免重复承担 Operator 的职责。

一期原定只关联已有采集配置 CR；2026-10-23 根据修订后的 [ADR0015](0015-exporter-servicemonitor-and-instance-creator.md)，由平台将配套采集意图写入 exporter CR，monitor-op 创建并持续调谐配套 ServiceMonitor；平台负责配置、只读回读及 Agent 关联。此决定替代 2026-10-21 由平台直接管理配套 ServiceMonitor 的方案，其他采集配置 CR 的独立创建、编辑和删除仍留待后续规划。

运行状态取自上述两个 CR 的 status；页面分别呈现配置下发结果、组件运行摘要、可用数量、观测新鲜度与故障原因，不展示 Operator 状态报告、调谐 conditions 列表、generation 或处理过程。采集成功率和 remote_write 成功率属于另行规划的数据面健康，不能由 CR 就绪状态推断；最终展示边界见 [确定状态同步、过期标记与故障展示契约](https://github.com/fjsxy/monitor-mgr/issues/9#issuecomment-6149704214)。
