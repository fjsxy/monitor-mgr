# #29 历史 exporter 显式补建与旧发布边界：实施前测试计划

测试接缝沿用 #23 已批准的公开 HTTP → 真实 Kitex RPC → SQLite → worker，辅以直接 RPC、独占真实集群和现有唯一浏览器 test。历史夹具真实裁去 v11+ 表/字段，保留 v10 业务关系；升级前清除的 ServiceMonitor 必须是本 run 已确认原 UID 的自有资源，仅用于重现旧集群。计划不表示已执行。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| I29-U01 | 升级、补身份、确认与轮询零补建 | HTTP 创建并发布 exporter；停平台并裁为真实 v10 单资源库及旧集群；升级，读取实例/版本/任务，管理员明确确认，刷新状态 | 原 exporter UID、旧版本/任务不变；创建用户可靠补齐、角色未知；各阶段 SM mutation 增量均 0 | #29 |
| I29-U02 | 新明确发布补建与原创建用户 | 对 I29-U01 实例先由另一管理员预览，再明确发布新版本；读取冻结版本/逐资源进度和目标请求 | 新 versionID 冻结 component + service-monitor；user-id 为原 creator，角色未知仍可发布；原 KSM UID 不变且仅条件 PUT，SM 单独 POST | #29 |
| I29-U03 | 未知创建用户拒绝且无替代 | 真实 v10 夹具删除 CREATE_INSTANCE 证据；升级后管理员允许管理；分别 HTTP 与直接 Kitex RPC 预览/发布 | 身份仍未知；明确拒绝依赖身份的配套发布；不采用 owner/currentActor，KSM/SM mutation 增量均 0 | #29 |
| I29-U04.failed | 旧失败操作仅重试原资源 | 真实 v10 单资源 FAILED 操作升级；移除SM CRD并拒绝create权限；编辑新草稿，明确 retry | 原 versionID/operationID不变，attempt增加；仅恢复原 component，SM 0写，不采纳新草稿 | #29 |
| I29-U04.unsent | 旧未发送操作只读恢复及明确继续 | 真实 v10 单资源操作保留未发送边界；重启升级后移除SM CRD并拒绝create权限，轮询，再明确 continue-unsent | 轮询0写；明确继续仅写原 component；旧资源集合不追加SM | #29 |
| I29-U04.sent | 旧已发送操作只读确认 | 真实 v10 保留原 KSM UID、possiblySent 与待确认操作；升级后移除SM CRD并拒绝create权限，再refresh | 只读核验原UID与冻结配置，无 KSM/SM 重放，旧版本仍单资源 | #29 |
| I29-U05.uid | 补建原 exporter UID 替换保护 | 历史升级后替换 KSM UID，再新预览/发布 | 拒绝接管替代资源；SM0写，原平台UID不改 | #29 |
| I29-U05.drift | 补建原 exporter 漂移保护 | 历史升级后外改 KSM spec，先不确认漂移，再明确确认 | 未确认时拒绝且0写；明确发布沿原UID/RV写，冻结新双资源版本 | #29 |
| I29-U05.foreign | 同名外部SM保护 | 历史升级后预置同名外部SM，预览/发布 | 拒绝且0业务写，不凭name/spec/labels认领 | #29 |
| I29-U05.permission | 缺权限拒绝补建 | 历史升级后拒绝SM create SSAR，预览/发布 | 明确失败且KSM/SM0业务写，原exporter保留 | #29 |
| I29-U05.lost | 新补建响应丢失保护 | 历史升级后明确发布，SM POST实际生效但丢响应；refresh并重启 | KSM已成功保留；SM无已确认UID时保持身份不确定，不按同名配置认领，不重放 | #29 |
| I29-K01 | 真实Pod旧集群零写与显式补建 | 独占Helm/PVC通过HTTP发布；记录本run KSM/SM UID后仅按原SM UID删除夹具SM；停Pod裁真实v10并升级；轮询/管理员确认；有权且非原创建用户明确新发布 | 升级/补身份/确认/轮询的SM业务请求增量0；新发布单独SM POST、KSM仅原UID条件PUT；保存旧/新版本资源差异与请求计数 | #29 |
| I29-K02 | 真实补建部分失败和未知响应 | 独占真实目标代理拒绝SM POST后显式retry；另一历史实例SM POST丢响应后重建Pod/refresh | 前者只补SM不重复写成功KSM；后者无SM UID保持身份不确定且不重放；证据绑定testedCommit | #29 |
| I29-B01 | 浏览器呈现旧/新冻结集合与补建进度 | E2E-10唯一test中，在I26最后注销集群前打开历史已发布KSM旧版本和操作；明确预览/发布，再读新版本及操作 | 旧版本/操作只显示KSM；新预览/版本显示SM配置，操作展示独立资源UID与状态；原版本仍不变 | #29 |
| I29-R01.gone | 旧夹具 UID 仅在消失后退出清理登记 | 本 run 公开发布并登记旧 SM UID；实际历史转换按该 UID DELETE、wait 并再次读取 NotFound；检查登记 | 仅移除仍对应旧 UID 的条目；不移除其他 UID，不凭同名对象更新归属 | #29 |
| I29-R01.blocked | 未证明消失时保留原清理责任 | 独立输入令 wait 失败、wait 后仍返回原对象；执行实际转换 helper | 转换失败且旧登记保留；不能跳过原 CR 清理 | #29 |
| I29-R02.confirmed | 浏览器显式发布完整交接资源 UID | 原 E2E-10 两次明确发布在受理点保存私有 operationId/instanceId/versionId；Python finally 在转发/代理终止前经公开 HTTP 读取精确操作；公开 HTTP → RPC 验证原管理员 Session 在集群注销后仍回读原部分失败且重试成功的同一操作；使用其已确认 UID 登记 KSM/SM；执行实际 Scenario.cleanup | 新历史 SM 按其 confirmedUid DELETE，原 KSM UID 保留；已删除 large 资源按 NotFound 结束；逐资源完整清理而非只忘记旧条目 | #29 |
| I29-R02.unknown | 未确认创建身份不能凭同名认领 | 本 run 操作中的可能已发送资源没有 confirmedUid；同名同配置对象在场；交接并清理 | 可能已发送且无已确认 UID 时明确登记失败；持久未发送的空 UID 不登记也不误报未知；同名对象零 DELETE，不采用 name/spec/labels 补认 | #29 |
| I29-R02.foreign | 跨实例或版本操作不能登记 | 独立交接输入含错 instanceId、versionId、operationId 或不属于本 run 的实例；HTTP 回读操作 | 每项均拒绝登记；已有清理条目不被覆盖，错误对象零 DELETE | #29 |
| I29-R03.replaced | 清理拒绝同名替代 UID | 已按原明确发布登记 confirmedUid，目标同名对象换为其他 UID；执行实际 cleanup | original-cr-cleanup 失败门禁保持；替代对象零 DELETE；安全收据固定分类为 identity/uid-mismatch | #29 |
| I29-R03.read | 读取失败只公开清理阶段 | 真实 cleanup 接缝的目标 HTTP 返回读取失败，异常附私有 sentinel | 清理仍失败，固定 read/read-failed 分类；公共 JSON 不含异常正文、配置、URL 或 sentinel | #29 |
| I29-R03.write | finalizer 与删除失败可区分 | 独立输入在自有 fixture finalizer 条件 PATCH 或 UID 条件 DELETE 处拒绝；实际 cleanup | 清理仍失败，分别固定 finalizers/finalizer-patch-failed、delete/delete-failed；不移除外部 finalizer，不泄露原 stderr | #29 |

执行映射：I29-U01/U02/U03/U04 → UT-16；I29-U05 → UT-12；I29-K01 → E2E-01；I29-K02 → E2E-08；I29-B01 → E2E-10；I29-R01/R02/R03 → UT-18 工具可信边界及 E2E-10 清理交接。已有 #28 通用身份/恢复契约若已覆盖真实历史输入则复用，不为制造红灯改产品；结果独立记录。
