# #20 E2E 测试用例

实施前 E2E 计划；发布并回读后才能开始实现。

所属任务：[Agent 存储、分片与实例生命周期](https://github.com/fjsxy/monitor-mgr/issues/20)。本地副本：`docs/e2e/issue-20.md`。

以下为计划，不代表执行通过。完整层级、profile、前置、清理与证据要求继承固定 `docs/acceptance/phase1.json`；稳定子例及原定语义保持不变。每例独立建立前置，只操作本 runId 资源，故障 finally 恢复，凭据不得进入报告。


共同执行前置与断言：

- Q 沿用已批准的真实 HTTP→生成 Thrift/Kitex→SQLite、直接 RPC、受控 TLS/clock/transport 接缝；Q 红绿不代替真实卷验收。K 用固定 Kubernetes1.28.15 / Prometheus Operator0.83.0 / Prometheus2.55.1，平台运行于独占真实Pod/PVC，通过显式context、runId、固定source/image执行。真实lane须root另行授予，未授予不排队。
- 每项策略参数子例各自新建前置，先单独发布策略，再真实读取原CR UID控制的全部相关StatefulSet与两项policy及PVC ownerRefs，确认实际采用后重新预览/明确危险操作；whenScaled只用于缩副本/挂起，减少shards使用whenDeleted。Retain/Delete分别取证，不以字段存在、草稿或CR写入代替实际采用。
- A02使用多shards并逐组核ST/Pod/resources；A06完整读取WAL PVC集合为空，使用安全marker区分同Pod容器重启与Pod替换/挂起缩零后的emptyDir生命周期，不把WAL寿命等同PVC策略。A07/A08/A09分别保存逐PVC UID/namespace/claim/shard、ST/Pod owner UID及原卷结果，保留shard不得被损坏。
- A10经HTTP和真实直接RPC核创建后name/namespace、PVC容量、PVC/emptyDir模式固定；复制产生空名称新模板，填写新名后证明新ID/CR UID/PVC UID及实际挂载链，旧历史与Retain卷仍独立。已确认版本与可能发送版本的固定字段都受保护。
- P14.policy-pending使用仅影响本run相关ST更新的故障fixture保留旧实际策略，分别尝试挂起、减shards和删除；当前策略不一致、读取失败、异步旧ownerRef均不得发送危险mutation。恢复fixture、单独发布策略并核验后重新明确操作。
- Retain卷不阻止实例完成；CR/workload/Pod清空与PVC保留/删除中/已删/未知独立表达，未决PVC任务在实例operation完成后持续只读恢复。平台不删/认领PVC、不直删workload或移除finalizer；测试工具最后按runId/原UID清卷，单独记录。
- 每例保存source/tree/runner、实际imageID/架构、requestId/operation/version/attempt、CR/ST/Pod/PVC UID/RV、实际policy与ownerRef、决定性断言、独立存储结果与finally；任何分页/读取不完整不可当空集。只有实际运行且断言通过才写passed，失败/环境失败/未执行分别保留。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| A02.primary | Agent shards与挂起恢复 | 设置多个shards，下发挂起再恢复；回读每组StatefulSet及Pod数量。 本子例输入：设置多个shards，下发挂起再恢复；回读每组StatefulSet及Pod数量。。 | 每shard单副本；运行/挂起分别replicas=1/0，paused保持false；实际数量与运行摘要一致。 | #20 |
| A06.primary | emptyDir不创建PVC | 首次创建时选择emptyDir并下发、挂起和删除。 本子例输入：首次创建时选择emptyDir并下发、挂起和删除。。 | Pod使用emptyDir；不创建WAL PVC，不显示虚构的PVC保留/删除结果。 | #20 |
| A07.retain | whenScaled作用于挂起缩零 | 参数化Retain/Delete，先单独发布并核验实际策略，再挂起。 本子例输入：先确认实际 whenScaled=Retain，再挂起缩零。 | Retain 保留原 PVC | #20 |
| A07.delete | whenScaled作用于挂起缩零 | 参数化Retain/Delete，先单独发布并核验实际策略，再挂起。 本子例输入：先确认实际 whenScaled=Delete，再挂起缩零。 | Delete 最终删除相应 PVC，独立保存证据 | #20 |
| A08.retain | whenDeleted作用于删除 | 参数化Retain/Delete，先确认策略再删除Agent，分别读取CR、workload、Pod和PVC。 本子例输入：先确认实际 whenDeleted=Retain，再删除 Agent。 | CR/workload/Pod 全消失才完成；PVC 保留不阻止完成 | #20 |
| A08.delete | whenDeleted作用于删除 | 参数化Retain/Delete，先确认策略再删除Agent，分别读取CR、workload、Pod和PVC。 本子例输入：先确认实际 whenDeleted=Delete，再删除 Agent。 | 实例完成与 PVC 删除分别回读，不以 CR 消失代替卷核验 | #20 |
| A09.retain | 减少shards的实际PVC结果 | 运行多shard，确认whenDeleted策略后减少shards，观察被移除StatefulSet及卷。 本子例输入：多个 shard 已运行且 whenDeleted=Retain，然后减少 shards。 | 仅已移除 StatefulSet 的卷按 whenDeleted 处理，保留 shard 不受损；不能误用 whenScaled | #20 |
| A09.delete | 减少shards的实际PVC结果 | 运行多shard，确认whenDeleted策略后减少shards，观察被移除StatefulSet及卷。 本子例输入：多个 shard 已运行且 whenDeleted=Delete，然后减少 shards。 | 仅已移除 StatefulSet 的卷按 whenDeleted 处理，保留 shard 不受损；不能误用 whenScaled | #20 |
| A10.fixed-mode | 容量与模式变更必须新实例 | 首次创建前自定义容量；创建后尝试改容量/存储模式/name/namespace，再按新实例重建。 本子例输入：创建成功后切换 PVC/emptyDir。 | 后端拒绝，需要新实例 | #20 |
| A10.new-instance | 容量与模式变更必须新实例 | 首次创建前自定义容量；创建后尝试改容量/存储模式/name/namespace，再按新实例重建。 本子例输入：复制旧配置后提供新 name/身份并创建。 | 新 CR UID 和新 PVC，不复用 Retain 残留卷 | #20 |
| P14.policy-pending | 清理阻塞和策略未生效不能报成功 | 参数化未采用新PVC策略的危险操作、带finalizer的删除、CR已没但Pod残留及辅助读取失败。 本子例输入：策略尚未作用于相关 StatefulSet 就挂起/减少 shards/删除。 | 先阻止，单独发布策略并确认后才能继续 | #20 |
| R03.agent-zero | 缺status与合法零值分开 | 参数化无status、字段合法省略、有效挂起/停用/零匹配及已删除。 本子例输入：Agent replicas=0 且有效状态确认。 | 挂起与零副本分开解释，不因 Available=True 显示运行中 | #20 |

## 补充存储安全回归

以下7个独立实际K输入细化原A10与Retain恢复边界，不增加12个owned计数。按原UID与当次完整读取判定；不声称Kubernetes跨对象原子快照。各输入先经过相同公开应用链做Q红绿，再在独占真实目标取证。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| REG20-STORAGE-001.orphan | 新实例不能挂载同确定WAL名称的孤立Retain卷 | 独占真实fixture，按候选新名和shard计算WAL claim名；预建无ownerRef的PVC并记录原UID/容量。经HTTP和真实生成RPC分别预览/发布候选；回读目标mutation与PVC UID/ownerRefs。finally由测试工具清理。 | 碰撞明确受阻，无候选CR/workload或挂载、无PVC修改/删除/认领；不能以同容量或无ownerRef放行。 | #20 |
| REG20-STORAGE-001.forbidden | 不能把PVC读取Forbidden当不存在 | 独立真实fixture保留可读CR/ST，撤销仅PVC读取权限；经平台预览/发布候选并保存403证据；恢复本例权限，finally清理。 | 错误明确受阻，无候选创建副作用或PVC认领，恢复后必须重新预览。 | #20 |
| REG20-STORAGE-001.timeout | 不能把PVC读取超时当不存在 | 独立受控代理连接真实目标，先证明基线可读，仅阻断候选PVC读取直到原预算超时；经HTTP/Kitex尝试预览/发布，恢复代理并finally清理。 | 按原预算受阻，无候选创建或PVC改动；超时不转404/空集合，不内部重试写。 | #20 |
| REG20-STORAGE-001.dispatch-race | 干净预览不能绕过派发前新增碰撞 | 独立真实fixture先干净预览，利用既有应用派发屏障在最后依赖重验前由测试工具插入候选确定名称PVC；继续原operation，核对真实读取和mutation证据，finally清理。 | 最后读取发现碰撞即明确未发失败/受阻，原卷不变；不声称检查之后的K8s跨对象原子性。 | #20 |
| REG20-STORAGE-001.new-name | 新的干净名称获得新的CR和卷 | 独立fixture保存孤立旧Retain PVC UID；复制配置填写另一个无碰撞新名，明确预览发布，回读原CR→ST→Pod→PVC UID链和实际挂载，finally清理。 | 新CR与新PVC UID不同于旧卷，原孤卷UID及内容不变；平台未清理旧卷。 | #20 |
| REG20-STORAGE-001.owned-retain-suspend | 旧实例挂起后恢复自己已确认的Retain卷 | 独立真实运行实例并持久记录原CR/ST/Pod/PVC UID；确认实际whenScaled=Retain后挂起、等待原Pod消失，再明确恢复并回读原PVC UID及挂载，finally清理。 | 可信原实例/原PVC UID的挂起恢复允许；不被新实例碰撞规则误拒绝，不自动迁移/删除旧数据。 | #20 |
| REG20-STORAGE-001.owned-retain-shard | 旧实例恢复被移除shard的原Retain卷 | 独立真实多shard实例记录原CR/ST/Pod/PVC UID；确认实际whenDeleted=Retain后减少shard、等待移除ST/Pod消失，再明确恢复该shard并核对原PVC UID与挂载，finally清理。 | 可信原实例/原PVC UID的shard恢复允许，确实复用其历史身份；不认领同名不同UID卷，不迁移/删除旧数据。 | #20 |

## 独立PVC任务的集群管理责任

以下2项Q补充由父规格§3/§8导出，不增加12个owned计数。实例与operation可在原CR/workload/Pod清空后完成；只有仍需原凭据核验的独立存储任务保留集群管理责任，不能重新套回实例protection。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| REG20-STORAGE-002.transfer | 未决存储核验期间不丢集群归属责任 | 经真实HTTP→生成Kitex→SQLite和受控TLS API完成原UID Agent删除，使实例/operation已完成，而whenDeleted=Delete原PVC读取Unknown/待核；通过transfer尝试转移。恢复当前凭据/读取并核实原PVC结果，再重新转移。独立Retain正控保留合法原PVC且实际核实Retain后转移。 | pending/Unknown独立卷任务时transfer409，实例与operation仍完成且不重建实例protection；只读核实完成后允许转移，凭据更新不受此责任阻塞。已确认Retain合法残卷是终态，不阻塞转移。 | #20 |
| REG20-STORAGE-002.unregister | 未决存储核验期间不提前清除原凭据 | 独立应用fixture完成原UID Agent删除，使CR/workload/Pod消失、实例/operation完成但Delete/PVC结果未知；调用unregister，再恢复读取并核实原PVC消失后重试。另以实际核实Retain原PVC仍存在作为独立正控。全程公开HTTP/真实Kitex，不直接改业务表制造结果。 | pending/Unknown任务时unregister409、保留可用凭据供只读核验；实例完成不被回滚/延迟。任务核实终态后注销允许；已确认Retain残卷不阻塞注销，平台不删除该卷。 | #20 |

## Agent发布存储影响预览

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| REG20-STORAGE-003.st-uid-accept | 提交确认前独立更改实际ST UID必须使旧危险预览失效 | Q：公开HTTP→真实Kitex→SQLite/TLS fixture运行原UID PVC Agent，先保存挂起/减shard草稿并预览；提交确认前仅注入实际ST UID变化，再继续原请求。使用原Agent UID控制的新ST，PVC保持同原UID且在新ST原Pod链中，实际策略已采用；证明安全guard可通过，仅确认影响身份变化。保存请求/目标mutation及操作结果。 | 旧预览拒绝受理/派发，目标无新mutation，必须重新预览；分别证明该输入而非一次注入多种变化。ST-UID变体应记录实时安全重检可通过而旧HMAC拒绝的证据。 | #20 |
| REG20-STORAGE-003.st-uid-dispatch | 操作受理后、最后存储重验前独立更改实际ST UID必须使旧危险预览失效 | Q：公开HTTP→真实Kitex→SQLite/TLS fixture运行原UID PVC Agent，先保存挂起/减shard草稿并预览；操作受理后、最后存储重验前仅注入实际ST UID变化，再继续原请求。使用原Agent UID控制的新ST，PVC保持同原UID且在新ST原Pod链中，实际策略已采用；证明安全guard可通过，仅确认影响身份变化。保存请求/目标mutation及操作结果。 | 旧预览拒绝受理/派发，目标无新mutation，必须重新预览；分别证明该输入而非一次注入多种变化。ST-UID变体应记录实时安全重检可通过而旧HMAC拒绝的证据。 | #20 |
| REG20-STORAGE-003.pvc-uid-accept | 提交确认前独立更改原PVC UID必须使旧危险预览失效 | Q：公开HTTP→真实Kitex→SQLite/TLS fixture运行原UID PVC Agent，先保存挂起/减shard草稿并预览；提交确认前仅注入原PVC UID变化，再继续原请求。以同名新PVC UID替换；不认领新UID，不把旧卷消失当新卷授权。保存请求/目标mutation及操作结果。 | 旧预览拒绝受理/派发，目标无新mutation，必须重新预览；分别证明该输入而非一次注入多种变化。ST-UID变体应记录实时安全重检可通过而旧HMAC拒绝的证据。 | #20 |
| REG20-STORAGE-003.pvc-uid-dispatch | 操作受理后、最后存储重验前独立更改原PVC UID必须使旧危险预览失效 | Q：公开HTTP→真实Kitex→SQLite/TLS fixture运行原UID PVC Agent，先保存挂起/减shard草稿并预览；操作受理后、最后存储重验前仅注入原PVC UID变化，再继续原请求。以同名新PVC UID替换；不认领新UID，不把旧卷消失当新卷授权。保存请求/目标mutation及操作结果。 | 旧预览拒绝受理/派发，目标无新mutation，必须重新预览；分别证明该输入而非一次注入多种变化。ST-UID变体应记录实时安全重检可通过而旧HMAC拒绝的证据。 | #20 |
| REG20-STORAGE-003.policy-accept | 提交确认前独立更改实际所用存储策略必须使旧危险预览失效 | Q：公开HTTP→真实Kitex→SQLite/TLS fixture运行原UID PVC Agent，先保存挂起/减shard草稿并预览；提交确认前仅注入实际所用存储策略变化，再继续原请求。仅改变本次所用whenScaled/whenDeleted实际ST值，保留CR UID及其余影响事实。保存请求/目标mutation及操作结果。 | 旧预览拒绝受理/派发，目标无新mutation，必须重新预览；分别证明该输入而非一次注入多种变化。ST-UID变体应记录实时安全重检可通过而旧HMAC拒绝的证据。 | #20 |
| REG20-STORAGE-003.policy-dispatch | 操作受理后、最后存储重验前独立更改实际所用存储策略必须使旧危险预览失效 | Q：公开HTTP→真实Kitex→SQLite/TLS fixture运行原UID PVC Agent，先保存挂起/减shard草稿并预览；操作受理后、最后存储重验前仅注入实际所用存储策略变化，再继续原请求。仅改变本次所用whenScaled/whenDeleted实际ST值，保留CR UID及其余影响事实。保存请求/目标mutation及操作结果。 | 旧预览拒绝受理/派发，目标无新mutation，必须重新预览；分别证明该输入而非一次注入多种变化。ST-UID变体应记录实时安全重检可通过而旧HMAC拒绝的证据。 | #20 |
| REG20-STORAGE-003.status-rv | status/RV自然推进不改变确认影响 | Q：运行原Agent/原ST/原PVC UID与已采用策略，保存危险预览，仅推进CR/ST status和resourceVersion，再确认发布并回读。 | 原安全guard仍通过、旧预览可以使用；不把status/RV放入稳定存储影响摘要。 | #20 |
| REG20-STORAGE-003.same-st-pod-roll | 同ST下Pod自然滚动不改变确认影响 | Q：运行原Agent/原ST/原PVC UID与已采用策略，保存危险预览，仅让同ST UID下原Pod被新Pod UID替换且继续引用原PVC，再确认发布并回读。 | UID父链仍可信，原预览可以使用；保留新旧Pod历史，但不把Pod UID作为确认影响冲突。 | #20 |

## 两项策略的独立owner语义

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| REG20-STORAGE-004.scaled-retain-deleted-delete | 挂起不能把合法保留ST的ownerRef误判为危险 | Q：真实HTTP→生成Kitex→SQLite/TLS运行原Agent，whenScaled=Retain、whenDeleted=Delete且实际ST已采用；原WAL PVC有原ST ownerRef、无Pod ownerRef。保存并明确下发挂起，回读CR replicas=0/paused=false、ST仍为原UID、原Pod消失及独立原PVC结果。 | 挂起允许，原PVC当前同UID保留并核实Retained，但不承诺未来删除ST仍保留；ST ownerRef因本次保留ST而合法，不能误拒绝或错误阻塞transfer/unregister。未发WAL风险与删除策略分别说明。 | #20 |
| REG20-STORAGE-004.delete-with-st-owner | 同一策略组合删除时仍使用whenDeleted而非whenScaled | Q：独立公开API/TLS fixture运行whenScaled=Retain、whenDeleted=Delete且已采用的原Agent/ST/Pod/PVC，保留真实原ST ownerRef。预览并按原UID/RV Foreground删除Agent，先核实实例清空完成，再独立核实原PVC存在/删除。 | 删除允许且原PVC结果按whenDeleted=Delete为Deleting→Deleted；不能因whenScaled=Retain保留该卷或把ST引用当Retain已确认，也不能直接删除PVC。 | #20 |

## Delete未决原卷的恢复准入

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| REG20-STORAGE-005.pending-suspend | 挂起后的原卷仍承担Delete责任时不提前恢复挂载 | Q：真实HTTP→生成Kitex→SQLite/TLS运行原UID PVC Agent，实际whenScaled=Delete已采用。公开发布挂起，实例变更完成而独立原PVC任务为Deleting，原PVC尚无deletionTimestamp且UID历史可信；保存运行草稿并预览恢复。随后让原PVC返回404，先通过原任务refresh实际核实并持久Deleted，再预览/下发恢复，模拟原Agent owner链产生同名新PVC UID并读取来源。独立whenScaled=Retain正控挂起后原卷Retained，恢复复用原UID。 | 未终结Delete任务的同Agent/原PVC UID不得因历史可信被视为Retain，恢复预览409且无新mutation；仅一次GET404不能跳过旧任务。原任务先独立Deleted后恢复允许，新卷UID与旧卷责任分别保存；Retain正控通过，不阻塞其他卷或已经终态的责任。accept与dispatch复用同一guard，不另造状态旁路。 | #20 |
| REG20-STORAGE-005.pending-shard | 减分片后原卷Delete未决时不提前加回原shard | Q：独立公开HTTP/Kitex/TLS fixture运行2shard原UID PVC Agent，实际whenDeleted=Delete已采用，明确减至1shard；旧shard ST/Pod消失但原PVC仍同UID存在且任务Deleting。保存加回2shard草稿并预览。之后原PVC404先由原任务refresh核实持久Deleted，再明确加回并回读新owner链同名新PVC UID及旧任务结果。独立whenDeleted=Retain正控减分片后Retained，再加回复用原PVC。 | 原shard所用PVC的未结束Delete责任使加回预览409、原CR/卷不被新写；完成原UID404只读核验并落盘Deleted后允许加回，新旧PVC身份独立。Retain正控恢复允许；不能因其他卷未决一律阻塞。共用准入/派发重验，不通过直接业务表写或虚构不可达竞态测试。 | #20 |
