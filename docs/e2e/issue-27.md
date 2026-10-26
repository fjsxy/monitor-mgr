# Issue #27 自动化测试计划：单 CR 操作恢复与旧 ServiceMonitor 任务兼容（2026-10-26 修订）

主要入口：公开 HTTP → 真实 Kitex RPC → SQLite → worker，受控 TLS Kubernetes API 记录真实 HTTP 写请求；必要时直接 RPC。沿用 UT-11 生命周期与 UT-12 删除与 UT-16 持久化行为组，新增子例不改变固定 18 项 UT 目录。真实集群与浏览器由集成验收执行。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| I27-U01 | KSM 发布冻结目标与逐资源进度可见且持久化 | 公开 API 创建 KSM；预览并下发；读取操作与版本；关闭并重启平台后再次读取 | 版本列出唯一 KubeStateMetrics 冻结目标和有效 YAML；操作资源保存状态、UID、可能已发送标记；重启后内容一致且不追加 ServiceMonitor | #27 |
| I27-U02 | Agent 发布与删除使用独立资源进度 | 公开 API 创建并下发 Agent；读取操作；预览删除并确认；读取删除操作 | PrometheusAgent 资源使用原 namespace/name/UID；删除核实原身份后完成；存储结果仍独立表达 | #27 |
| I27-U03 | 明确拒绝后的人工重试保留原冻结版本 | 目标首次明确拒绝 KSM 发布；修改草稿；对原操作重试；读取版本、操作和目标 | 新尝试仍使用原版本与原资源冻结配置；资源状态转成功；新草稿未混入 | #27 |
| I27-U04 | 首次创建响应丢失不认领同名对象 | 目标创建成功但断开响应；读取操作；刷新并重启平台；尝试重试、继续、取消 | 资源显示 IDENTITY_UNCERTAIN、possiblySent=true、confirmedUID 为空；同名同配置不补认；无额外写请求且拒绝变更 | #27 |
| I27-U05 | 已知 UID 更新响应丢失只读恢复 | 完成首次发布；再次发布时目标更新但丢响应；修改草稿；重启并刷新原操作 | 按原 UID 和原冻结配置核实成功；进度保留原 UID；不重放写请求、不采用新草稿 | #27 |
| I27-U06 | 同名替换和漂移不被恢复接管 | 已知 UID 更新结果不明后将目标换 UID，或保留 UID 改为非冻结配置；刷新并重启 | 保留待确认与资源具体原因；不认领替代 UID、不覆盖漂移、不解除保护 | #27 |
| I27-U07 | 未发送任务支持取消或明确继续 | 在持久派发前停住并重启；读取资源；分别执行取消、继续 | 未发资源保留 possiblySent=false；重启不写；取消零写；继续创建新尝试且只写原冻结目标 | #27 |
| I27-U08 | 每资源发送标记与结果先持久化 | 在 afterSendMarker 停止；读操作并重启；对已有成功资源读回 | 标记先于网络写提交；重启仅核验；成功 UID、结果与状态保留，不因恢复降级或重复写 | #27 |
| I27-U09 | 真实旧 schema 迁移保留历史任务语义 | 用公开 API 生成成功历史和未发旧任务；裁成真实旧 schema；启动升级并读取版本和操作；明确继续旧单 CR 未发任务；另加载真实旧双 CR schema/任务夹具 | 旧单 CR 版本仅投影原组件资源；旧双 CR 历史原样保留；凭据、会话、UID、冻结映射、尝试保持；升级零写，不追加配套 CR | #27 |
| I27-K01 | 实际 Pod 重建不增加写请求 | 集成环境 Helm 部署 testedCommit；单 CR 发布注入响应丢失；重建平台 Pod；查操作和目标写计数 | 逐资源结果与 UID 跨 Pod 保留；恢复只读；未确认身份保持保护；计数不增加 | #27 |
| I27-B01 | 页面展示具体资源进度及安全动作 | 浏览器打开成功、未发、结果待确认和删除操作；展开资源信息及执行尝试 | 显示 kind、namespace/name、状态、UID、发送边界、冻结 YAML及原因；动作与现有安全边界一致 | #27 |

以上为实施前计划，尚不表示实际通过；结果另记并绑定 testedCommit。

## 审查补充：拒绝旧冻结目标与当前实例身份错绑

沿用公开 HTTP → 真实 Kitex RPC → SQLite → worker/受控 TLS Kubernetes API 接缝；各行在 UT-11 内独立执行，不扩大固定 UT 目录。保留首次确定失败后可修改未部署实例身份的现有行为。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| I27-R01.ksm-name | KSM 更名后禁止旧版本错绑 UID | 首次 KSM POST 明确拒绝；公开保存新 name；恢复目标；原操作 retry；读取草稿、实例、版本及目标写计数 | retry 返回 409 CONFLICT、0新增目标写和0新增 attempt；实例 UID 为空、name 为新值；旧冻结文档与新草稿均不被改写 | #27 |
| I27-R01.ksm-namespace | KSM 部署 namespace 也是冻结身份 | 首次 KSM POST 明确拒绝；保存新 targetNamespace；恢复目标并 retry 原操作 | 写前返回409 CONFLICT；不创建旧 namespace 的 exporter、不绑定 UID；当前 targetNamespace 与旧冻结目标各自保留 | #27 |
| I27-R01.agent-name | Agent 更名后禁止旧版本错绑 UID | 首次 PrometheusAgent POST 明确拒绝；保存新 name；恢复目标并 retry 原版本 | 写前返回409 CONFLICT；原 namespaced CR 不被创建、实例不绑定旧 UID；最新草稿不被回写 | #27 |
| I27-R01.agent-namespace | Agent namespaced identity 不能被旧重试绕过 | 首次 PrometheusAgent POST 明确拒绝；保存新 namespace；恢复目标并 retry 原版本 | 写前返回409 CONFLICT；旧 namespace 无新增请求；实例 namespace 与 UID 正确保留，冻结文档不重算 | #27 |
| I27-R02 | 合法新身份可明确发布，派发使用一致身份 | 对以上四个变更后草稿分别重新预览并明确发布；读取新版本、逐资源进度、实例与真实目标请求路径 | 新发布成功，instance name/namespace/UID 与新冻结组件一致；KSM 使用新部署 namespace；旧操作仍失败、旧资源文档不变；每次写入前均核对当前实例与冻结组件身份 | #27 |

本补充为实施前计划，不表示实际通过；实际红绿与 testedCommit 另记。

## 职责切换后的兼容边界

下列用例沿用相同公开接缝；旧 schema/双 CR 任务从独立 SQL 夹具加载，模拟升级前已存在的持久事实，不能通过新版用户流程制造旧方案。新发布流程使用公开 HTTP。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| I27-C01.queued | 旧未发 SM 步骤禁止恢复续写 | 加载旧 schema 中 component 成功、SM QUEUED 的任务；启动、读取、refresh、retry、continue、cancel 并重启 | 旧版本/UID/逐资源事实保留；SM 显示历史待处理；各入口 0 SM 写入；不能假记成功或转新模式 | #27 |
| I27-C01.sent | 旧可能已发 SM 只读核验 | 加载 SM possibly_sent=true 且原 UID 已确认的旧任务，目标存在原 UID；启动、refresh 与重启 | 只 GET 原冻结路径；保留 UID/发送标记/原结果，并独立显示核验结果与人工处理原因；0 SM 写入 | #27 |
| I27-C01.unknown | 旧 SM 首次发送未知身份不认领 | 加载 SM possibly_sent=true、原 UID 为空的任务，目标含同名同 spec 对象；refresh、retry、continue、重启 | UID 仍为空，身份仍待确认，明确旧任务待处理；0 SM 写入，不自动接管 | #27 |
| I27-C02 | 新模式资源派发白名单 | 公开创建并发布 KSM/Agent，再重试明确失败及继续未发操作；读取版本/操作/attempt | 仅一个 component 资源，kind 分别为 KubeStateMetrics/PrometheusAgent；所有目标写入无 ServiceMonitor | #27 |
| I27-C03 | 删除冻结路径和替换 UID 保护 | 发布 KSM/Agent 后预览删除；目标同名换 UID 或出现 RV 冲突；重试/继续/重启 | 仅按原冻结路径和原 UID/RV 条件删除；不删替代对象；存储保护独立有效 | #27 |
| I27-C04 | Operator 子资源故障不会变成平台补写 | 新发布成功后目标报告子资源故障；刷新操作与运行观察 | CR 写入成功与观察故障分开表达；无第二份平台写入任务，无 SM 写入 | #27 |

本修订替代旧方案职责预期；所有真实 kind/浏览器验收由集成任务串行执行。本计划尚未表示通过，实际结果另存并绑定 testedCommit。
