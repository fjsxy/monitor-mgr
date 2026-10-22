# #21 E2E 并行 CI 实施计划（修订1）

保持 18 UT / 10 E2E 及原业务断言。只改变 E2E 调度：三个独立 runner，每个 runner 独占 BuildKit、control/data 集群、私有目录和 public artifact；组内串行，组间并行。`make e2e` 默认仍运行全部10项；`--shard` 只允许固定组名。

基准：run 39620800818，candidate 13d62347b72410ccff598390df8504a1c7ef8b19；E2E job 21分21秒，UT 10分26秒。按实际用例耗时分配如下。

| 组 | 原用例 ID | 基准用例合计 |
| --- | --- | --- |
| topology | E2E-01、03、04、10 | 308.3秒 |
| storage | E2E-05、09 | 296.2秒 |
| recovery | E2E-02、06、07、08 | 271.3秒 |

关键：每组 Ginkgo 只注册本组 ID，不能用 skip/retry 将未执行项算通过。汇总必须检查三个组的 CI job 均成功、来源和 run 身份一致、组分配精确、原生 Ginkgo 成功摘要与实际场景结果一一对应、10项无漏跑/重复、清理成功。失败后不取消其他组，保留完整诊断。私有源码/镜像不通过 public artifact 或跨 job cache 共享。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| UT-18 | 并行结果不能制造错误绿灯 | 既有结果可信边界组加入完整三组正控，以及缺组/多组/重复ID/错组/错候选/错run/失败/skip/retry/未执行/清理失败反例；同一控制入口运行。 | 仅同来源、固定分组、全部实际成功且清理完成通过；保留18个顶层UT，产品覆盖率≥70%。 | #21 |
| E2E-01 | 平台部署、就绪与 Pod 重建 | 在 `topology` 独占双集群执行原步骤：Helm 部署平台到 control；经 Service HTTP 初始化并登录；保存一条管理记录；删除平台 Pod 并等待新 Pod Ready；用原 Cookie 读回记录；回读容器监听和 Service 端口。RPC/DB 故障由 UT-16/17 定向验证，避免再造一套集群故障前置。 | 新 Pod UID 不同，原 PVC/数据/有效 Session 保留；/readyz 真实检查 RPC 与数据库；RPC 仅 Pod loopback、不暴露 Service 端口；所有请求确实进入 Pod。 原生 Ginkgo 恰好执行一次，并进入全部10项汇总。 | #21 |
| E2E-02 | 双集群登记、凭据与归属 | 在 `recovery` 独占双集群执行原步骤：从平台 Pod 登记 control/data；有效证书与 token 各用一个目标；同 UID 换 context 再登记；输入一个 TLS 错误；用受限 namespace 凭据登记后尝试预览/发布；为正常集群发布一个默认 KSM 作资源保留锚点，再验证凭据替换、归属转移、注销再启用。 | UID/身份由真实 API 确认；重复登记和非法身份拒绝；受限能力可登记，但对应 namespace 下发被阻止且无 CR 写；失败凭据不替换旧凭据；转移立即改变权限；注销后原受管 CR UID/配置与历史保留、访问凭据清除；重新启用不自动发布。 原生 Ginkgo 恰好执行一次，并进入全部10项汇总。 | #21 |
| E2E-03 | KSM 从草稿到三种真实拓扑 | 在 `topology` 独占双集群执行原步骤：HTTP 保存后先确认无 CR 写入，再发布 control 上的 KSM，按 Deployment→StatefulSet(2 shards)→DaemonSet 执行替换；在同一流程检查资源覆盖、过滤、零匹配及运行/挂起/停用；data 再部署一个默认 KSM；预置一个外部只读 CR。 | 真实 CR、Deployment/StatefulSet/DaemonSet、三角色、Pod 数和参数匹配当前模式；两个集群均能运行；保存不部署；切换清理旧工作负载；外部 CR 不能编辑/发布/删除。 原生 Ginkgo 恰好执行一次，并进入全部10项汇总。 | #21 |
| E2E-04 | Agent 采集关联与发送配置 | 在 `topology` 独占双集群执行原步骤：HTTP 在 data 发布默认 Agent，核验默认 None/Own；再创建四类采集 CR 并一次发布各自的代表规则；增删一个匹配对象/namespace；同一 Agent 的多个 remoteWrite 分别引用 None、BasicAuth、Bearer 和 TLS；control 部署一个默认 Agent；删除一个被引用 key 作拒绝输入。 | 两个集群均有真实 StatefulSet；默认资源为 CPU 200m、memory request 512Mi/limit 1Gi，PVC 1Gi；四种资源都被实际识别，生成配置与 Secret 引用正确；缺依赖阻止写入；不据此宣称下游收到样本。 原生 Ginkgo 恰好执行一次，并进入全部10项汇总。 | #21 |
| E2E-05 | Agent 分片与 PVC 完整生命周期 | 在 `storage` 独占双集群执行原步骤：使用两个新 Agent：whenScaled=Retain/whenDeleted=Delete，以及反向配置；分别执行 shards 2→1→0→1，再删除；另发布一个 emptyDir 实例；尝试原实例扩容量或换 storage mode。 删除已保留的原 PVC，再以同名新 UID 重建并刷新原操作，核对历史仍绑定原 UID。 | 每一步回读实际 StatefulSet 策略与原 PVC UID；缩容/挂起/删除的保留和删除结果分别正确；emptyDir 无 PVC；固定容量/模式不原地变更；同名替代卷不接管。 原生 Ginkgo 恰好执行一次，并进入全部10项汇总。 | #21 |
| E2E-06 | 发布冻结、漂移与版本回看 | 在 `recovery` 独占双集群执行原步骤：先发布 v1，再编辑草稿 v2；重复提交同幂等键；外部修改 CR spec 并尝试普通发布，再明确覆盖；保留外部 annotation；单独更新 status/RV 作正控；从版本入口读取来源。 | 同键无第二次写；v1 来源不随草稿变化；漂移先冲突、确认后才覆盖；annotation/status 不被清除；纯 status/RV 不触发重复确认；版本入口返回原配置。 原生 Ginkgo 恰好执行一次，并进入全部10项汇总。 | #21 |
| E2E-07 | 真实目标故障与状态隔离 | 在 `recovery` 独占双集群执行原步骤：两个集群各运行一个实例；用独占代理阻断 data API，保留 control 可用；等待观测过期并回读两者；恢复代理；给一个工作负载制造一个未 Ready 副本。 | control 正常结果不被 data 错误覆盖；data 显示具体访问错误/历史/过期，不能当成 0 个对象或已删除；恢复后重新观察；部分 Ready 显示未就绪。 原生 Ginkgo 恰好执行一次，并进入全部10项汇总。 | #21 |
| E2E-08 | 真实写响应丢失与重启禁重放 | 在 `recovery` 独占双集群执行原步骤：代理让真实 API 完成一次已知 UID 更新后丢响应；在确认前重建平台 Pod并恢复读取；再对首次创建丢响应、未保存 UID 的情况执行一次同名资源检查；对一个确定失败操作显式 retry。 | 已有 UID 只核验原资源，原 API 写次数不增加；首次创建身份不明不按名字补认/重建/删除；retry 使用原固定版本和新 attempt；重启不自动重放。 原生 Ginkgo 恰好执行一次，并进入全部10项汇总。 | #21 |
| E2E-09 | 真实前台删除与阻塞保护 | 在 `storage` 独占双集群执行原步骤：为受管 CR/子资源添加一个测试 finalizer 后从 HTTP 删除；确认仍受阻；移除自己的 finalizer 并等原 UID 图消失；另将同名 CR 替换为新 UID 后尝试删除；检查无关 namespace/对象。 | 只有原 CR、workload、Pod 全消失才完成；阻塞期间不能解除保护/转移集群；新 UID 和无关对象未被平台删除；PVC 结果与 E2E-05 独立确认。 原生 Ginkgo 恰好执行一次，并进入全部10项汇总。 | #21 |
| E2E-10 | 唯一浏览器主流程 | 在 `topology` 独占双集群执行原步骤：Playwright 登录→用表单创建一个 KSM 草稿→编辑测试夹具预置的 revision=9007199254740993 草稿→查看只读 YAML/diff→明确发布→工作台查看状态/版本→明确删除；前后通过后端读回确认操作对象与版本；不增加产品测试接口。 | 关键控件可操作、确认后才发发布/删除；显示正确实例和状态，大 revision 经浏览器操作后不舍入；没有前端控制台致命错误；不逐字段/逐模式重复验证后端规则。 原生 Ginkgo 恰好执行一次，并进入全部10项汇总。 | #21 |

## 执行与判定

本地通过 UT/race/覆盖率、固定分组校验和 Ginkgo 实际注册/调度验证后，推送 PR #24。远端实际运行三个集群组与汇总；比较同一 workflow 的 E2E 关键路径（首组开始至汇总完成）、各组准备/用例/清理时间与完整 CI 等待时间。结果写入单独结果文件，不以时间估算或调度模拟代替真实集群结果。

计划发布并回读：https://github.com/fjsxy/monitor-mgr/issues/21#issuecomment-6331066744。
