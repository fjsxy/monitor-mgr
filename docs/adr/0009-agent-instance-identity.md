# Agent 名称由用户填写并作为稳定 CR 身份

一期每个已注册集群允许多个独立 Agent 实例，每个实例分别保存配置草稿、发布版本和运行状态。实例访问以所在集群的权限为前提；2026-10-21 根据 [ADR0015](0015-exporter-servicemonitor-and-instance-creator.md) 补充实例变更限制，平台管理员创建的实例禁止普通用户修改。

根据 2026-10-14 页面评审，创建前填写名称，直接作为 PrometheusAgent CR 的 metadata.name；不支持中文，不再自动生成 CR name 或另设显示名称。名称按 Kubernetes DNS subdomain 规则校验，同 namespace 下的同名资源不可接管。下发开始后保护名称，首次成功创建 CR 后固定 namespace/name；迁移、改名或容量调整通过新实例、新 CR 和新 PVC 完成，保留旧实例历史。

此结论取代前一版“自动生成固定 CR name 与可修改显示名称分离”的约定，稳定资源身份和存储边界仍然有效。
