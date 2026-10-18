# #16 E2E 测试用例

本轮新增格式约束下的继续实施计划；在继续新增实现前发布并回读。

所属任务：[KSM 配置、部署与实例管理](https://github.com/fjsxy/monitor-mgr/issues/16)。本地副本：`docs/e2e/issue-16.md`。

以下为计划，不代表执行通过。完整层级、profile、前置、清理与证据要求继承固定 `docs/acceptance/phase1.json`；稳定子例及原定语义保持不变。每例独立建立前置，只操作本 runId 资源，故障 finally 恢复，凭据不得进入报告。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| I03.primary | HTTP与直接RPC都隔离账号 | 两用户分别归属两个集群；逐一尝试对方的列表、详情、保存、下发和删除。 本子例输入：两用户分别归属两个集群；逐一尝试对方的列表、详情、保存、下发和删除。。 | 列表只含授权对象；越权命令明确拒绝、无副作用；伪造账号/角色或换传输入口不能绕过。 | #16 |
| C04.ksm-crd | 缺CRD可登记但不能下发 | 在独占数据集群fixture中移除相应CRD，使用有效身份注册/重检后发起下发。 本子例输入：独占 fixture 缺 KubeStateMetrics CRD。 | 允许登记，KSM 下发受阻并说明原因 | #16 |
| C05.primary | 按实际namespace核验权限 | 使用有身份/UID读取权限、但无某目标namespace写权限的测试凭据登记并发布。 本子例输入：使用有身份/UID读取权限、但无某目标namespace写权限的测试凭据登记并发布。。 | 登记允许且展示受限；目标发布被拒绝、无越权资源；其他有权namespace行为不被混淆。 | #16 |
| K02.primary | Deployment与资源预设实际映射 | 保存并下发默认KSM Deployment；回读CR、容器resources和Ready Pod。 本子例输入：保存并下发默认KSM Deployment；回读CR、容器resources和Ready Pod。。 | 单副本Deployment；省略分片分支；声明对应100m/250Mi/500Mi预设；真实Pod就绪，预设不被称为已验证容量。 | #16 |
| K03.primary | StatefulSet分片映射 | 选择StatefulSetSharded并设置2个shards后下发。 本子例输入：选择StatefulSetSharded并设置2个shards后下发。。 | 仅相应分片分支；StatefulSet副本等于2且实际Pod收敛；小于2的输入被拒绝。 | #16 |
| K04.primary | DaemonSet三角色拓扑 | 选择DaemonSetSharded及pods+非Pod collector后下发，读取工作负载。 本子例输入：选择DaemonSetSharded及pods+非Pod collector后下发，读取工作负载。。 | 出现node DaemonSet及cluster/unscheduled两个Deployment；无手工数量字段；非法collector组合不能下发。 | #16 |
| K05.primary | 模式切换遵循替换边界 | 依次切换Deployment、StatefulSetSharded、DaemonSetSharded并记录workload UID事件。 本子例输入：依次切换Deployment、StatefulSetSharded、DaemonSetSharded并记录workload UID事件。。 | 旧模式工作负载先消失，再形成新模式；不残留互相冲突的旧负载，不承诺无损滚动。 | #16 |
| K06.node | 角色资源继承与整块覆盖 | 分别启用继承、独立整块覆盖，关闭再开启高级选项并下发。 本子例输入：node 角色依次继承、整块独立覆盖、关闭再开启高级配置。 | 继承不生成覆盖块；独立时整块替换；关闭不下发且保留草稿值，再开启恢复 | #16 |
| K06.cluster | 角色资源继承与整块覆盖 | 分别启用继承、独立整块覆盖，关闭再开启高级选项并下发。 本子例输入：cluster 角色依次继承、整块独立覆盖、关闭再开启高级配置。 | 继承不生成覆盖块；独立时整块替换；关闭不下发且保留草稿值，再开启恢复 | #16 |
| K06.unscheduled | 角色资源继承与整块覆盖 | 分别启用继承、独立整块覆盖，关闭再开启高级选项并下发。 本子例输入：unscheduled 角色依次继承、整块独立覆盖、关闭再开启高级配置。 | 继承不生成覆盖块；独立时整块替换；关闭不下发且保留草稿值，再开启恢复 | #16 |
| K07.default-all | collector与指标过滤映射 | 参数化Default/明确collector和指标All/Allow/Deny等已支持模式，检查CR及容器参数。 本子例输入：collectors=Default，metricFilter=All。 | 仅映射该模式启用的 names/patterns；Default/All 保持省略，Include 非空；Allow/Deny 保留正则含义 | #16 |
| K07.default-allow | collector与指标过滤映射 | 参数化Default/明确collector和指标All/Allow/Deny等已支持模式，检查CR及容器参数。 本子例输入：collectors=Default，metricFilter=Allow。 | 仅映射该模式启用的 names/patterns；Default/All 保持省略，Include 非空；Allow/Deny 保留正则含义 | #16 |
| K07.default-deny | collector与指标过滤映射 | 参数化Default/明确collector和指标All/Allow/Deny等已支持模式，检查CR及容器参数。 本子例输入：collectors=Default，metricFilter=Deny。 | 仅映射该模式启用的 names/patterns；Default/All 保持省略，Include 非空；Allow/Deny 保留正则含义 | #16 |
| K07.include-all | collector与指标过滤映射 | 参数化Default/明确collector和指标All/Allow/Deny等已支持模式，检查CR及容器参数。 本子例输入：collectors=Include，metricFilter=All。 | 仅映射该模式启用的 names/patterns；Default/All 保持省略，Include 非空；Allow/Deny 保留正则含义 | #16 |
| K07.include-allow | collector与指标过滤映射 | 参数化Default/明确collector和指标All/Allow/Deny等已支持模式，检查CR及容器参数。 本子例输入：collectors=Include，metricFilter=Allow。 | 仅映射该模式启用的 names/patterns；Default/All 保持省略，Include 非空；Allow/Deny 保留正则含义 | #16 |
| K07.include-deny | collector与指标过滤映射 | 参数化Default/明确collector和指标All/Allow/Deny等已支持模式，检查CR及容器参数。 本子例输入：collectors=Include，metricFilter=Deny。 | 仅映射该模式启用的 names/patterns；Default/All 保持省略，Include 非空；Allow/Deny 保留正则含义 | #16 |
| K08.all | namespace选择及合法零匹配 | 分别验证已有literal names与namePatterns的并集；零匹配子例清空names，仅使用当前无匹配的namePatterns。 本子例输入：namespaceFilter=All。 | names/namePatterns 省略，不伪造明确全集 | #16 |
| K08.union | namespace选择及合法零匹配 | 分别验证已有literal names与namePatterns的并集；零匹配子例清空names，仅使用当前无匹配的namePatterns。 本子例输入：已有 literal names 与匹配完整 namespace 名称的 namePatterns 同时配置。 | 按并集去重，不自动添加模式外 namespace | #16 |
| K08.dynamic-pattern | namespace选择及合法零匹配 | 分别验证已有literal names与namePatterns的并集；零匹配子例清空names，仅使用当前无匹配的namePatterns。 本子例输入：names=[]，增删被 namePatterns 匹配的 namespace。 | 匹配集合动态改变，CR/运行状态按真实读取解释 | #16 |
| K08.zero | namespace选择及合法零匹配 | 分别验证已有literal names与namePatterns的并集；零匹配子例清空names，仅使用当前无匹配的namePatterns。 本子例输入：names=[]，仅提供当前无匹配的 namePatterns。 | 有效状态最终确认无匹配、0/0；不自动放宽为 All | #16 |
| K09.primary | 运行挂起停用独立于保存 | 依次保存但不发、明确下发挂起/恢复/停用；检查CR与负载。 本子例输入：依次保存但不发、明确下发挂起/恢复/停用；检查CR与负载。。 | 仅保存不改集群；发布后按各意图收敛；状态、数量和发布结果分别表达。 | #16 |
| K10.name | 名称与不可变字段在后端校验 | 直接HTTP/RPC传入中文/非法名称、重复身份，首次成功创建后改name或targetNamespace，或传原始YAML/未知字段。 本子例输入：分别经 HTTP/直接 RPC 提交空名称、中文或非法 DNS subdomain。 | 字段级拒绝，不自动生成名称 | #16 |
| K10.collision | 名称与不可变字段在后端校验 | 直接HTTP/RPC传入中文/非法名称、重复身份，首次成功创建后改name或targetNamespace，或传原始YAML/未知字段。 本子例输入：分别经 HTTP/直接 RPC 提交重复 CR 身份。 | 冲突，不接管已有 CR | #16 |
| K10.fixed-target | 名称与不可变字段在后端校验 | 直接HTTP/RPC传入中文/非法名称、重复身份，首次成功创建后改name或targetNamespace，或传原始YAML/未知字段。 本子例输入：首次成功创建 CR 后尝试更改 name 或 spec.targetNamespace。 | 拒绝修改固定身份；不把仅保存草稿当作已部署 | #16 |
| K10.unsupported | 名称与不可变字段在后端校验 | 直接HTTP/RPC传入中文/非法名称、重复身份，首次成功创建后改name或targetNamespace，或传原始YAML/未知字段。 本子例输入：分别提交原始 YAML 或未知配置字段。 | 拒绝透传，不写集群 | #16 |
| P01.primary | 保存不产生集群写 | 记录平台集群mutation请求及CR spec，修改并保存草稿后回读；将独立status更新与保存动作分别取证。 本子例输入：记录平台集群mutation请求及CR spec，修改并保存草稿后回读；将独立status更新与保存动作分别取证。。 | 仅DB草稿修订推进，平台集群写请求为0、CR spec未因保存变化；独立status/RV可变化，页面不显示已下发。 | #16 |
| P02.primary | 不可变发布与来源快照 | 受理草稿vN发布后继续保存新草稿；读取固定版本、任务及最终CR。 本子例输入：受理草稿vN发布后继续保存新草稿；读取固定版本、任务及最终CR。。 | 受理版本与sourceDraftRevision冻结；新草稿不污染该次发布；确认写入前不提升最近写入版本。 | #16 |
| P03.same | 重复点击只受理一次 | 并发发送同键同内容，再以同键不同内容和另一个变更请求提交。 本子例输入：同账号同动作同目标，同键同内容并发。 | 返回同一 operationId 与当前结果，实际副作用不重复 | #16 |
| P03.different-content | 重复点击只受理一次 | 并发发送同键同内容，再以同键不同内容和另一个变更请求提交。 本子例输入：同作用域同键但内容不同。 | 409 幂等冲突 | #16 |
| P03.another-active | 重复点击只受理一次 | 并发发送同键同内容，再以同键不同内容和另一个变更请求提交。 本子例输入：实例已有未结束操作时使用不同键发起变更。 | 拒绝第二变更，仍可保存新草稿 | #16 |
| P04.draft-race | 过期修订或预览不能提交别版 | 并发保存同修订；预览后修改草稿或目标配置再确认。 本子例输入：两个客户端以同一 expectedDraftRevision 保存。 | 只有一个成功，另一冲突并保留本地编辑 | #16 |
| P04.draft-after-preview | 过期修订或预览不能提交别版 | 并发保存同修订；预览后修改草稿或目标配置再确认。 本子例输入：预览后另存新修订，再确认旧预览。 | 要求新预览，不能静默换版 | #16 |
| P04.target-after-preview | 过期修订或预览不能提交别版 | 并发保存同修订；预览后修改草稿或目标配置再确认。 本子例输入：预览后实际目标配置改变。 | 配置基线冲突，需重新确认 | #16 |
| P05.supported-drift | 漂移覆盖与非平台元数据保护 | 外部改配置并添加额外spec及非平台labels/annotations；先普通发布，再明确覆盖。 本子例输入：外部修改已支持 spec，同时保留非平台元数据。 | 阻止普通发布，明确覆盖后恢复受支持 spec 并保留非平台 labels/annotations | #16 |
| P05.extra-spec | 漂移覆盖与非平台元数据保护 | 外部改配置并添加额外spec及非平台labels/annotations；先普通发布，再明确覆盖。 本子例输入：外部添加平台不支持 spec 字段。 | diff 左侧显示将删除字段；明确覆盖不得删除 status/finalizers | #16 |
| P06.status-only | status变化不重复确认配置 | 预览后仅改变status/RV或等价默认表现，再提交同配置；另改变实际spec。 本子例输入：预览后只变 status。 | 重读新 RV 后可继续，无需重复确认配置 | #16 |
| P06.rv-only | status变化不重复确认配置 | 预览后仅改变status/RV或等价默认表现，再提交同配置；另改变实际spec。 本子例输入：配置等价，仅 RV 推进。 | 仍使用原 UID 与当前 RV 条件 | #16 |
| P06.default-equivalent | status变化不重复确认配置 | 预览后仅改变status/RV或等价默认表现，再提交同配置；另改变实际spec。 本子例输入：仅固定 CRD 语义下的等价默认值变化。 | 不误报漂移；selector nil/{} 不可当作等价 | #16 |
| P06.spec-change | status变化不重复确认配置 | 预览后仅改变status/RV或等价默认表现，再提交同配置；另改变实际spec。 本子例输入：预览后实际 spec 变化。 | 必须新预览，不能静默覆盖 | #16 |
| P09.response-drop | 受理响应丢失仍可找回任务 | 真实受理后丢弃HTTP响应/令请求超时，按请求键查询并重复提交同键。 本子例输入：事务已受理，HTTP 响应被丢弃。 | 按请求键找回同一任务，不创建第二任务 | #16 |
| P09.deadline | 受理响应丢失仍可找回任务 | 真实受理后丢弃HTTP响应/令请求超时，按请求键查询并重复提交同键。 本子例输入：事务已受理，受理 HTTP/RPC deadline 到期。 | 不取消已受理任务，同键仍返回原操作 | #16 |
| R03.ksm-suspended | 缺status与合法零值分开 | 参数化无status、字段合法省略、有效挂起/停用/零匹配及已删除。 本子例输入：有效状态确认 KSM 挂起且合法零值省略。 | 解释为挂起，不能把意图即刻当完成 | #16 |
| R03.ksm-disabled | 缺status与合法零值分开 | 参数化无status、字段合法省略、有效挂起/停用/零匹配及已删除。 本子例输入：有效状态确认 KSM 停用。 | 解释为停用，保留 CR 与历史 | #16 |
| R03.ksm-no-match | 缺status与合法零值分开 | 参数化无status、字段合法省略、有效挂起/停用/零匹配及已删除。 本子例输入：有效状态确认 namespace 零匹配。 | 解释为无匹配且 0/0 | #16 |
| U03.primary | 多选及轮询保持编辑位置 | 在KSM抽屉连续选常用collectors/集群namespace，滚动并等待后台刷新。 本子例输入：在KSM抽屉连续选常用collectors/集群namespace，滚动并等待后台刷新。。 | 常用项在前；多选来自所选集群；内容、焦点和滚动不跳回顶部，读取失败保留选择并提示。 | #16 |
| F02.primary | 真实RPC链路不能被HTTP旁路 | 启动真实Kitex client/server与SQLite测试库；记录同requestId的调用；令RPC不可用。 本子例输入：启动真实Kitex client/server与SQLite测试库；记录同requestId的调用；令RPC不可用。。 | 正常HTTP业务抵达server并执行业务授权；RPC不可用时无直接DB/K8s成功旁路；伪造角色/身份无效。 | #16 |
| F03.quantity | 后端字段校验及稳定错误 | 直接发送非法quantity/正则/selector/URL/字段组合和未知字段，绕过页面禁用。 本子例输入：分别经 HTTP/直接 RPC 提交 非法 quantity、负数或 request>limit。 | 稳定字段级错误与 HTTP 类别，CR 无写入，敏感值不泄漏 | #16 |
| F03.regexp | 后端字段校验及稳定错误 | 直接发送非法quantity/正则/selector/URL/字段组合和未知字段，绕过页面禁用。 本子例输入：分别经 HTTP/直接 RPC 提交 Go regexp 不接受的正则/含逗号指标项。 | 稳定字段级错误与 HTTP 类别，CR 无写入，敏感值不泄漏 | #16 |
| F03.unknown-field | 后端字段校验及稳定错误 | 直接发送非法quantity/正则/selector/URL/字段组合和未知字段，绕过页面禁用。 本子例输入：分别经 HTTP/直接 RPC 提交 不支持字段、资源键或高级透传。 | 稳定字段级错误与 HTTP 类别，CR 无写入，敏感值不泄漏 | #16 |
| F05.before-commit | 幂等与任务受理事务原子 | 在版本、操作、尝试、去重/保护事务的提交边界注入崩溃，并重复相同请求键。 本子例输入：事务提交前崩溃。 | 要么完整未提交，不留下半个受理状态 | #16 |
| F05.after-commit | 幂等与任务受理事务原子 | 在版本、操作、尝试、去重/保护事务的提交边界注入崩溃，并重复相同请求键。 本子例输入：提交后、返回前崩溃。 | 同键重启查询原完整结果，不产生第二操作；仍执行当前授权 | #16 |
