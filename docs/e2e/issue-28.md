# Issue #28 自动化测试计划：exporter 采集意图与 Operator 只读观察（2026-10-26 修订）

主要测试接缝沿用已确认的公开 HTTP → 真实 Kitex RPC → SQLite → worker、直接 RPC、受控 TLS Kubernetes API、真实双集群及浏览器。本计划替代旧双 CR 职责预期，保留原稳定 ID；不表示已通过。固定 18 UT + 10 E2E 不重编号，真实 kind 由集成协调者统一执行。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| I28-U01 | 草稿默认采集、零写与父 CR 单资源冻结 | HTTP 新建 KSM、保存并读回草稿；检查 TLS 请求；明确预览发布；读版本与操作 | 新草稿默认 enabled=true、30s/10s；保存0业务写；只冻结/派发父 KSM，spec.serviceMonitor.labels 来自固定 creatorUserId，无独立 SM YAML | #28 |
| I28-U02.deployment | Deployment 不复制 Operator 规则 | 含点名称 Deployment 预览发布；从父 status 取配套引用并 GET | 单父资源；引用 UID 与 owner CR UID 一致才可展示 Ready；不生成 Service/SM 名称或 selector | #28 |
| I28-U02.stateful | 分片模式依赖同一契约 | StatefulSetSharded 预览、发布、观察 | 采集意图相同、只读 status 引用；不复制分片 Service 算法 | #28 |
| I28-U02.daemon | DaemonSet 模式依赖同一契约 | DaemonSetSharded 预览、发布、观察 | 采集意图相同；不复制角色 labels/端口算法 | #28 |
| I28-U02.long | 长名称不自行派生配套对象 | 长合法 CR 名发布、按 status 引用观察 | 父 name 原样；配套 name 只取 Operator 引用 | #28 |
| I28-U03.crd | 能力预检拒绝裁剪旧 schema | 分别缺失 spec.serviceMonitor、enabled default=false、字段类型、状态 enum、resource.uid；预览及写前检查 | 明确拒绝且0父 CR 新字段业务写；SM 存在不能替代 capability | #28 |
| I28-U03.namespace | 目标 namespace 必须存在 | 预览前删除部署 namespace | 预览失败且0业务写 | #28 |
| I28-U03.create | 真实受限平台凭据可以发布 | 允许父 CR create、CRD/SM get/list，拒绝全部 SM 写；明确发布 | 发布成功，SM create/update/patch/delete 请求数均0 | #28 |
| I28-U03.update | 真实受限平台凭据可以更新与关闭 | 发布后修改 interval/timeout、关闭采集并发布 | 仅父 CR 条件更新；SM 写请求0 | #28 |
| I28-U03.foreign | 引用或所有权错误不认领 | 父引用返回错 apiVersion/kind/ns/name/UID，或 SM controller owner UID 错 | 配套 Unknown/身份错误；不影响已成功父发布、不写 SM | #28 |
| I28-U03.race | 预览后能力变化 | 预览通过后将实际 schema 替换为旧版，再确认发布/释放 worker | 接收或发送前拒绝；0新字段父 CR写 | #28 |
| I28-U04 | Operator 故障独立于父下发成功 | 父发布成功后 status.serviceMonitor Degraded；刷新再改 Ready | 父版本和 UID 保留；故障由只读观察展示；修复不创建平台第二任务 | #28 |
| I28-U04.uid | 子资源合法重建 | 保留父 UID/version，status 引用变为新 SM UID 并提供正确 owner | 新 UID可只读观察为 Ready；父实例身份不丢失 | #28 |
| I28-U04.drift | 配套未知/未收敛/过期分别呈现 | 分别缺 status、旧 generation、Ready/Degraded 与读取失败，推进时间超过30s | 区分 Unknown、Reconciling、Degraded、Ready、读取失败与 historical/stale | #28 |
| I28-U05.first | 首次父 CR 响应丢失禁认领 | 父 POST 实际生效但断响应；refresh、重启、retry | 原父 UID 未确认、只读恢复、不认领、不重放；SM 写0 | #28 |
| I28-U05.known | 已知父 UID 更新丢响应恢复 | 完成父发布；父 PUT 生效但断响应；refresh/重启 | 按原 UID/冻结意图只读确认；无重放、无SM写 | #28 |
| I28-U06 | 单父请求发送前重新授权 | 预览受理后暂停 worker，降级/停用操作者，再释放 | 父请求发送前阻止；SM 写0；已发结果继续只读 | #28 |
| I28-U07 | 按父原 UID 删除、只读核对配套消失 | 发布后删除父；保留原 SM 再移除；另父或子同名换 UID | 仅父 UID/RV 删除；原 graph/SM 未消失保持等待；替代 UID 不删除；SM DELETE0 | #28 |
| I28-U08 | 旧冻结版本与旧 SM 保护 | 加载旧单 CR/双 CR SQL；读取、重启、retry；对成功旧SM及不同名称旧SM尝试新启用 | 冻结不重算、不补写；仍可能存在/结果不明旧SM阻止新启用，提供 #29 显式核验解除边界 | #28 |
| I28-R01 | 受控标签不可由用户覆盖 | HTTP/RPC 提交 labels 字段或 creator 未知的历史实例启用 | 任意 labels 拒绝；creatorUserId 为空拒绝启用，权限确认不伪造 creator | #28 |
| I28-R02 | 采集参数契约 | 分别保存0/负/非法duration、timeout>interval，以及合法复合duration | 非法字段级拒绝；合法配置往返保留并冻结 | #28 |
| I28-R03 | 发布与观察分离 | 父 CR 成功、SM 读取 Forbidden，随后恢复读取 | 发布仍成功；观察错误独立且不沿用Ready作为当前 | #28 |
| I28-R04 | 启用前盘点全部历史SM | 加载旧成功/unknown SM不同路径；新启用预览；管理员显式确认原对象消失后重试 | 默认阻断重复采集；只有 #29 持久的显式处理确认可解阻，不按新对象同名筛选 | #28 |
| I28-R05 | 关闭、挂起、停用语义独立 | 保存并发布关闭采集、Suspended、Disabled，读父 frozen spec | 关闭仅表达serviceMonitor.enabled=false；挂起保留声明；停用表达父enabled=false；平台SM写0 | #28 |
| I28-K01 | 锁定发布品与受限身份真实生命周期 | 校验RC4原tgz/CRD/RBAC/contract及镜像digest；四节点环境使用SM read/no-write SA发布两个实例；参数修改、子重建、关闭、父删除 | Operator SA创建修复配套；严格引用与owner正确；另一实例不变；平台SM四类写metadata审计计数0，父写与SM读正控存在 | #28 |
| I28-K02 | 真实故障与恢复边界 | 父丢响应、平台Pod重建；注入外部SM冲突/观察Forbidden再恢复；核零写审计 | 父未知UID不认领/重放；exporter保留；Operator恢复配套；平台仅只读观察 | #28 |
| I28-B01 | 页面编辑与只读配套状态 | 现有E2E-10唯一Playwright流程编辑开关/interval/timeout；预览/版本/操作与状态页查看 | 可见父CR采集意图、CR下发结果、配套状态/引用/错误/新鲜度；无第二份SM发布或修复按钮 | #28 |

执行映射：I28-U01/U02/U03/R01/R02/R05 → UT-08/17；I28-U04/R03 → UT-14；I28-U05/U08/R04 → UT-11/16；I28-U06 → UT-10；I28-U07 → UT-12；I28-K01 → E2E-03/09；I28-K02 → E2E-08；I28-B01 → E2E-10。真实原始证据交由 Ginkgo/Gomega 断言，Python 负责编排和采证。结果另存并绑定 testedCommit。
