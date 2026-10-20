# #17 发布预览保留字段回归计划

本计划落实最终两轴 review 的 P2：已存在 CR 的 RightYAML 目前显示新建文档，实际条件更新却保留原非 spec 字段，导致确认 diff 与写请求不一致。修复共用发布 document 构造：更新从原 CR 的独立副本替换整个目标 spec，首次创建继续使用 cfg.document()；预览左右两侧均经 safeTargetDocument。原 UID/RV、授权、派发、冻结版本与存储门禁不变，原未知 spec 仍清除。

批准接缝为真实 HTTP → 生成 Kitex → SQLite/worker → 受控 TLS 目标 API。只在外部目标边界记录实际请求的独立 JSON 副本，不能以预览 helper 的返回值反算预期。既有真实集群、浏览器和旧 207/104 证据不重跑、不改写。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| REG17-YAML-PRESERVE.ksm-existing | 已存在 KSM 的完整预览与真实更新一致 | 在 TestP05DriftOverwritePreservesMetadataAndP06Equivalent 的 HTTP/Kitex 链先创建，再向原目标加入外部 labels/annotations、UID/RV、status/finalizers、未知 spec；读取预览后明确覆盖，分别核对原 CR 未被预览改动及 TLS 端收到的写请求 | 原 C13 新断言真实红；修复后 RightYAML 保留所有非 spec 字段，未知 spec 清除且目标支持 spec 生效；安全 RightYAML 与实际请求投影一致，原 UID/RV 条件保持；原 P06 status/RV 等价行为继续通过 | #17 |
| REG17-YAML-PRESERVE.agent-existing | Agent 同样保留字段且敏感信息不回显 | 在 TestAgentHeadersReadProjection 的真实链加入外部 metadata/status/finalizers 与任意 headers、认证 URL/legacy last-applied；读取预览、确认原对象未被投影改动，再明确覆盖并核 TLS 写请求；保留原缓存与 header 漂移拒绝断言 | 原 C13 保留字段断言真实红；修复后非 spec 普通字段保留，左右 YAML 中任意 header、认证 URL 与存量敏感值仍脱敏，预览不修改原目标或草稿，未知 spec 清除；实际写仍保留原非 spec 内容且无自动认领 | #17 |
| REG17-YAML-PRESERVE.first-create.ksm | KSM首次创建维持原 document 与原身份规则 | 独立 TestPublicationYAMLFirstCreate/ksm 通过 HTTP 保存草稿、预览并发布，比较首次无目标说明、RightYAML 与受控 TLS 实际创建请求 | 首次左侧仍明确无 CR，右侧与实际新建请求相同且无伪造 UID/RV/status/finalizers；预览不写目标，创建成功后按原响应核实身份 | #17 |
| REG17-YAML-PRESERVE.first-create.agent | Agent首次创建维持原 document 与原身份规则 | 独立 TestPublicationYAMLFirstCreate/agent 通过 HTTP 保存草稿、预览并发布，比较首次无目标说明、RightYAML 与受控 TLS 实际创建请求 | 首次左侧仍明确无 CR，右侧与实际新建请求相同且无伪造 UID/RV/status/finalizers；预览不写目标，创建成功后按原响应核实身份 | #17 |
| REG17-YAML-PRESERVE.safe-projection | 新共享构造不绕过敏感投影边界 | 执行上述 targeted Go 回归及现有认证 URL 解析失败、存量投影保护 case；仅新增相关 Make 入口固定重复命令；执行现有 web-build 验证 clusters.tsx 注释改动 | 相关测试与前端检查通过；集群页仅中文职责/关键凭据清除与轮询注释，运行代码不变；不执行 go test ./...、LOCAL_FULL、REG104 或新环境 | #17 |

本计划是最终 review 修复，#17 既有关闭与原验收结果不重写，不加入中央 seed。每个输入保留原实际命令、exit、红绿输出安全投影、tested source/dirty 状态；工作树测试不是 clean formal profile。用户已确认本轮 Ginkgo/UT>=70%/Helm 不追溯迁移，后续开发按新规约。C12/C13 原873文件归档、原算法和 source index 完全保留；新产品 head 的最终 CI 另行取证，不复用 C13 test-only difference 作为新 head 证明。
