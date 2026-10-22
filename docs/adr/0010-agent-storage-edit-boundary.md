# 首次创建后固定 Agent 存储模式与 PVC 容量

一期 Agent 支持 PVC 和 emptyDir，首次成功创建 Agent CR 后固定存储模式与 PVC 容量，保留策略仍可修改并明确发布。

当前 Operator 更新 PVC 模板容量会触发 StatefulSet 删除重建，保留下来的旧 PVC 可能仍保持原容量，因此容量调整通过新实例和新 PVC 完成，在线扩容留待后续规划，以避免平台配置与实际卷行为不一致。
