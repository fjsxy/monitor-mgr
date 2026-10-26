# CI 测试清单与选择标准

本文件由 `make ci-cases` 生成；`make ci-cases-check` 核对 18 个 Ginkgo UT 与 10 条 Ginkgo E2E 实现。每行一个 It，同行中的必要输入/阶段用 By 定位，不能跳过后算通过。

| CI job | 执行入口与内容 | 通过标准 |
| --- | --- | --- |
| UT | `make ut`：18 个 Ginkgo UT，一次 race/覆盖率；生成一致性、vet、前端类型检查/构建 | 全部18项通过，无漏跑/skip/retry/race，手写产品语句覆盖率≥70% |
| E2E | 三个 matrix runner 并行；各自 `make ci-builder` 和 `make e2e E2E_ARGS="--shard ..."`；独立 control(1cp+2worker)/data(1可调度cp)，Helm 部署固定依赖和平台；组内串行 | 三组 job 均成功；`make e2e-summary` 校验同候选/本次run、恰好10项各执行一次，无skip/retry，场景/原生Ginkgo/清理均成功；部分通过不是完整E2E通过 |

两类 job 在可信同仓 PR 和 main 使用同一清单；E2E 的 Operator 发布 CLI 源码和原资产按固定 SHA/摘要匿名 HTTPS 获取，不依赖源码或 release 下载 secret。构建/生成检查不另算测试类型。依赖安装统一使用 `npm ci --no-audit`，保留 lockfile/integrity 校验，测试准备不隐式等待外部审计服务；npm audit 不在本清单28项用例之内。

选取标准：UT 覆盖授权、凭据、UID/PVC、事务、恢复和协议的独立规则；E2E 只保留 Kubernetes/RBAC/Operator/真实卷和浏览器接线才能证明的行为。等价输入合并复用前置，后端接口优先；浏览器仅 E2E-10。

明确不自动化：B-31 细布局/像素/横向溢出，B-32 轮询时焦点/滚动/多选展示，B-33 下拉联动/排序的浏览器组合；共3/33已识别风险组（9.1%）。后端筛选排序和大 i64 精度仍保留。该比例不是语句覆盖率。

## E2E 并行分配

按 run 39620800818 的逐例实测分组，三组用例本身约308/296/271秒；每组重新准备环境，增加 runner 总用量以减少等待。组名进入 runId、集群名、BuildKit 名和 artifact；源码/镜像不跨 job 上传或缓存。某组失败不取消其他组，最后汇总不能跳过失败组。

| matrix.shard | 固定用例 |
| --- | --- |
| topology | E2E-01、E2E-03、E2E-04、E2E-10 |
| storage | E2E-05、E2E-09 |
| recovery | E2E-02、E2E-06、E2E-07、E2E-08 |

`make e2e` 默认仍执行全部10项；单组使用 `make e2e E2E_ARGS="--shard topology ..."`。Service 连接前有界等待原Pod的唯一Ready endpoint，替代UID和多个Ready仍立即拒绝。失败时只公开冻结脚本文件/行号及精确命中固定产品Error的源码位置，原始错误保持私有。单组报告标记 `e2e-shard`，只有三个组通过汇总才形成 `e2e-complete`。分组和完整结果分别保存，缺组/错组/错SHA/错run/失败/skip/retry/清理失败均不能通过。

## UT：18 个

| ID / 验证目的 | 必要动作 | 决定性结果 / 进入 CI 的理由 |
| --- | --- | --- |
| UT-01 认证与 Session | 空库初始化、重复初始化、错误密码、临时密码改密；用受控时钟验证默认 8h 和配置 2h 到期；检查 Cookie。 | 首次初始化成功，重复初始化拒绝；改密后旧 Session 失效；到期返回 401；Cookie 的 HttpOnly、SameSite 与 HTTPS Secure 符合约定。 |
| UT-02 账号与当前授权 | 管理员创建/禁用/重置用户；分别以管理员、所属用户、其他用户、禁用用户操作；并发降级最后两名管理员；同幂等键在撤权后重放。 | 越权返回 403、失效身份返回 401；旧会话失效；始终至少一名启用管理员；幂等命中不能绕过当前授权。 |
| UT-03 kubeconfig 安全解析 | 选中 context 分别包含 http、skip-tls、外部 CA/证书路径、tokenFile、exec、auth-provider、proxy；再输入完整内嵌证书或 token 的合法配置。 | 合法配置可解析；8 类不支持输入均在联网前拒绝；不执行插件、不读外部凭据、不降级 TLS。它们属于安全边界，保留为同一 UT 的输入表。 |
| UT-04 集群登记与管理状态 | 用受控 Kubernetes adapter 返回已核验身份/UID，执行登记、重复登记、候选凭据失败/成功、归属转移、注销、同 UID 重新启用和不同 UID 拒绝；插入未完成操作再转移。 | 凭据验证成功才切换，失败仍使用旧凭据；同 UID 不重复登记；不同 UID 不继承历史；未完成操作阻止转移/注销；注销不发目标删除请求。 |
| UT-05 KSM 配置规则与映射 | 对 Deployment、StatefulSet、DaemonSet 构造配置；三角色使用不同资源值；过滤保留 Default+All、Include+Allow、Include+Deny 三组；输入非法 quantity、request>limit、非法正则与未知字段。 | 生成目标 mode、shards、角色资源和过滤字段正确；高级选项关闭时仅保留草稿值；非法输入产生可定位字段错误。纯映射不启动平台或 Kubernetes。 |
| UT-06 Agent selector 与发送配置 | 对公共规则验证 object=None/All/Labels × namespace=Own/All/Labels 的 9 个样本，并用受控只读 adapter 检查实际匹配集合；检查 Exists/DoesNotExist 与 nil/空数组；映射 None、BasicAuth、Bearer、多 remoteWrite 和 TLS Secret 引用。 | 省略、{}、标签条件互不混淆；合法空值精确保留；错误表达式、URL 或不成对 cert/key 被拒绝；发送配置只引用同 namespace 的 Secret。 |
| UT-07 实例身份与不可变字段 | 同名 KSM 放到不同集群；同名 Agent 放到不同 namespace；第一次成功发布后尝试更改 name、namespace、storage mode、capacity；覆盖派生名称长度上界与一个旧库非法名称。 通过公开 HTTP 和直接 RPC 验证 I25-01–08/12：exporter/collector 固定创建用户和角色、伪造字段拒绝、管理员实例各写入口受保护、角色升降与归属转移不改写身份。 I30-U01/U02/U03经HTTP→真实Kitex→SQLite/worker独立验证固定creator、None/Own、原生All/Labels/四类表达式、跨namespace/Operator范围、独立权限与未知creator自定义规则。 | 实例 ID 独立；固定身份和存储模式/容量变更返回冲突；Worker 不发布旧库中的非法派生名称；外部资源不能变成平台所有。 当前管理员可管理；普通用户可读管理员实例但不能写；canManage/readOnlyReason 与纳管 readOnly 分开，USER 创建实例沿用集群权限。 管理员编辑/归属和角色变化不改已存关联；unknown不补造；零匹配、读失败与Forbidden分开。 |
| UT-08 保存、预览与版本冻结 | 保存草稿并读回；生成预览后分别修改草稿和目标配置；发布 v1 后编辑成 v2，再读 v1 来源；关闭高级选项后查看有效 CR。 I28-U01/U02/U03/R01/R02/R05：新草稿默认采集且零写、父 CR 单资源冻结与creator标签；缺失/错误 served schema、enabled默认值、status enum/UID和非法duration、任意labels逐项输入。 I30-U01/U02/U03经HTTP→真实Kitex→SQLite/worker独立验证固定creator、None/Own、原生All/Labels/四类表达式、跨namespace/Operator范围、独立权限与未知creator自定义规则。 | 仅保存/预览不产生 Kubernetes mutation；过期预览拒绝发布；发布版本和来源快照不被新草稿改写；未启用字段不进入有效 CR。 只冻结父spec.serviceMonitor，不生成独立SM；旧schema在写前拒绝，固定creator投影且参数契约严格。 管理员编辑/归属和角色变化不改已存关联；unknown不补造；零匹配、读失败与Forbidden分开。 |
| UT-09 幂等与受理事务 | 账号写入和发布各选一条代表命令；同键同内容重试、同键异内容重试；在受理提交前/后触发一次失败并重开数据库。 | 同键不产生第二次副作用，异内容返回 409；提交前全部回滚，提交后可查询原操作和版本；幂等记录与操作原子提交。 |
| UT-10 派发、取消与任务授权 | 用派发屏障分别让取消获胜、派发获胜；队列内撤销角色或转移归属；另让 Cookie 到期而账号仍有权限。 通过 I25-09/disabled/DELETE/RV 在受理后降级、停用、删除派发前及 409 后重试前撤权；I25-10 在已发送后降级并丢响应。 I28-U06：采集意图仍走同一父CR发送前当前授权边界。 | 取消与派发只有一个结果；当前失权时不发集群写；仅 Cookie 到期不取消已受理任务；HTTP 断开不删除持久化任务。 每次实际写前复核实例权限；失权后零新增写入；已发结果仅只读确认，不重放或回滚。 父已发结果只读恢复，失权不产生新父写或任何SM写。 |
| UT-11 重试与重启恢复 | 针对未提交、已受理未发、可能已发三个持久状态重开平台；对确定失败做显式 retry；创建新草稿后继续原操作。 I28-U05/U08/R04：父丢响应恢复；加载旧SM成功/unknown及不同冻结名称，尝试新启用。 | 未提交没有半条操作；未发任务待人工继续；可能已发只读核验，不自动补写；retry 保留原固定版本/actor，形成新 attempt。 原UID未知不认领/重放；全部仍可能存在的旧SM阻断新采集，旧事实不被新观察覆盖。 |
| UT-12 预览与删除保护 | 修改 preview 绑定的 UID、配置或 namespace 影响；在 Worker 读取后、条件写入前用屏障只改 status/RV，并分别改变 spec、UID、当前权限或凭据 revision；持续制造 RV 冲突；构造同名新 UID、跨 namespace owner、CR 已消失但旧 Pod 尚在。 I28-U07：父删除后保留配套原UID，再移除配套并refresh。 | 纯 status/RV 冲突可重新读取后在原 attempt 内完成（最多 3 次条件写）；spec/UID/权限/凭据变化或持续冲突停止；不确定响应绝不重放；真正影响变化需重新预览；仍有原工作负载时不报删除完成。 平台仅删除父原UID/RV；配套与原资源图未消失不能完成，SM DELETE请求始终为0。 |
| UT-13 PVC 危险动作决策 | 比较期望与实际 StatefulSet retention policy；输入策略未生效、读取失败、孤儿 PVC、原父资源已消失、合法零副本；分别检查两种混合 Retain/Delete 配置。 | 危险动作必须以实际已生效策略为准；身份/影响不明则阻止；不认领同名替代 PVC；保留责任不随实例记录结束丢失；两个策略字段不能互换。 |
| UT-14 状态与工作台查询 | 用同一批对象覆盖缺 status、旧 generation、部分 Ready、合法 0/0；独立推进 CR/Pod/PVC/关联来源时钟至 30 秒前后；某页读取失败；测试名称/类型/组件 AND 筛选和活动排序。 I28-U04/U03.foreign/R03：按完整Operator status引用核子对象、parent/controller UID；子UID重建、旧generation、Degraded、Forbidden及错误引用。 I30-U01/U02/U03经HTTP→真实Kitex→SQLite/worker独立验证固定creator、None/Own、原生All/Labels/四类表达式、跨namespace/Operator范围、独立权限与未知creator自定义规则。 | 未知、挂起、未就绪、过期分别可判；403/超时不是空集；一来源成功不刷新其他来源；筛选、统计、最近操作排序符合后端契约，轮询不篡改活动顺序。 配套与CR发布独立；合法子重建不改父身份，错误/未知/未收敛/历史/过期分开；不按labels认领。 管理员编辑/归属和角色变化不改已存关联；unknown不补造；零匹配、读失败与Forbidden分开。 |
| UT-15 请求预算与并发 | 对预算解析输入缺失、0、非法值、超大值；只有一条真实本地 RPC 计时验证服务端上限；用屏障并发刷新同一对象以及不同集群。 | 异常 metadata 不放大服务端预算；相同读取合并；全局/单集群并发分别不超过 8/4；一个集群慢不阻塞其他集群的全部请求。不再为每种 metadata 等满 10 秒。 |
| UT-16 数据库、主密钥与敏感读模型 | 用最旧支持的 v1 库和当前库分别升级/重开，另保留一个带冻结发布历史的 v5 样本（其迁移分支不能由空旧库代表）；注入备份失败、DDL 失败、缺失/错误 master.key；在凭据和错误中放敏感哨兵。 I25-11 从真实 v10 schema 迁移带草稿、版本、任务与 UID 的数据库。 | 已有库和原会话可恢复；失败不替换空库、不自动生成替代密钥；数据库损坏时不启动为 Ready；备份权限正确；凭据不以明文出现在库、响应或日志，敏感配置变化仍可被指纹识别。 未知角色保持未知，对普通用户只读，管理员可管理，不按当前账号角色补造历史。 |
| UT-17 HTTP/Kitex 协议契约 | 仅选一条受保护 RPC 验证未认证拒绝；阻断真实 Kitex 连接再请求 HTTP；测试未知字段、optional/空对象/空集合、非法枚举；让 revision 从 9007199254740992 增至 9007199254740993。 I28-U01/R01/R02 的采集字段通过真实HTTP/Kitex往返，并拒绝客户端labels。 I30-U01/U02/U03经HTTP→真实Kitex→SQLite/worker独立验证固定creator、None/Own、原生All/Labels/四类表达式、跨namespace/Operator范围、独立权限与未知creator自定义规则。 | HTTP 不能旁路 RPC 或弱化校验，真实 RPC 不可用时 /readyz 失败；未知字段不被幂等吞掉；大 revision 在 HTTP 为精确字符串、RPC 保持 i64；错误结构稳定。不再把全部业务再走一遍直接 RPC。 可选字段保持旧冻结缺省语义，新建缺省只在Create生效。 管理员编辑/归属和角色变化不改已存关联；unknown不补造；零匹配、读失败与Forbidden分开。 |
| UT-18 测试工具的最小可信边界 | 只验证三组结果：缺失/失败/跳过/重复事件不能通过；错误 candidate SHA 不能通过；清理不能操作非本 run 的资源或替代 UID。 | 报告不把缺例改绿；源码身份一致；清理范围明确且失败使 CI 失败。只保留这三组必要断言，不再把旧 254 项工具自测整套塞进本用例。 |

## E2E：10 个

| ID / 验证目的 | 必要动作 | 决定性结果 / 进入 CI 的理由 |
| --- | --- | --- |
| E2E-01 平台部署、就绪与 Pod 重建 | Helm 部署平台到 control；经 Service HTTP 初始化并登录；保存一条管理记录；删除平台 Pod 并等待新 Pod Ready；用原 Cookie 读回记录；回读容器监听和 Service 端口。RPC/DB 故障由 UT-16/17 定向验证，避免再造一套集群故障前置。 | 新 Pod UID 不同，原 PVC/数据/有效 Session 保留；/readyz 真实检查 RPC 与数据库；RPC 仅 Pod loopback、不暴露 Service 端口；所有请求确实进入 Pod。 |
| E2E-02 双集群登记、凭据与归属 | 从平台 Pod 登记 control/data；有效证书与 token 各用一个目标；同 UID 换 context 再登记；输入一个 TLS 错误；用受限 namespace 凭据登记后尝试预览/发布；为正常集群发布一个默认 KSM 作资源保留锚点，再验证凭据替换、归属转移、注销再启用。 | UID/身份由真实 API 确认；重复登记和非法身份拒绝；受限能力可登记，但对应 namespace 下发被阻止且无 CR 写；失败凭据不替换旧凭据；转移立即改变权限；注销后原受管 CR UID/配置与历史保留、访问凭据清除；重新启用不自动发布。 |
| E2E-03 KSM 从草稿到三种真实拓扑 | HTTP 保存后先确认无 CR 写入，再发布 control 上的 KSM，按 Deployment→StatefulSet(2 shards)→DaemonSet 执行替换；在同一流程检查资源覆盖、过滤、零匹配及运行/挂起/停用；data 再部署一个默认 KSM；预置一个外部只读 CR。 I28-K01/K02：锁定原RC4发布品；真实SM read/no-write SA发布两个exporter，修改参数、子UID重建、外部冲突及读取Forbidden后修复，挂起/关闭/停用/恢复/父删除；返回API Metadata原始审计给Ginkgo。 | 真实 CR、Deployment/StatefulSet/DaemonSet、三角色、Pod 数和参数匹配当前模式；两个集群均能运行；保存不部署；切换清理旧工作负载；外部 CR 不能编辑/发布/删除。 配套按实际status/owner和Service/Endpoints核验；Operator独立SA创建修复，另一实例不变；平台父写/SM读正控存在，所有SM写尝试0；Go断言原始证据及testedCommit。 |
| E2E-04 Agent 采集关联与发送配置 | HTTP 在 data 发布默认 Agent，核验默认 None/Own；再创建四类采集 CR 并一次发布各自的代表规则；增删一个匹配对象/namespace；同一 Agent 的多个 remoteWrite 分别引用 None、BasicAuth、Bearer 和 TLS；control 部署一个默认 Agent；删除一个被引用 key 作拒绝输入。 I30-K01/K02/K03使用真实受限平台SA发布三exporter/两Agent，配套SM取RC4 statusref；Helm独占空scrape receiver接收remote_write；对全部实际Endpoints×被报告Pod作正样本、timestamp、双向负查询和精确全集比较，动态新增不重发Agent。 | 两个集群均有真实 StatefulSet；默认资源为 CPU 200m、memory request 512Mi/limit 1Gi，PVC 1Gi；四种资源都被实际识别，生成配置与 Secret 引用正确；缺依赖阻止写入；不据此宣称下游收到样本。 Ginkgo验证父/SM owner UID、实际进程配置、锁定镜像来源和真实样本；无额外job/target，动态AgentUID/版本/spec保持；平台SM所有写尝试0，有父写/SM读/Operator创建正控，空或缺proof不通过。 |
| E2E-05 Agent 分片与 PVC 完整生命周期 | 使用两个新 Agent：whenScaled=Retain/whenDeleted=Delete，以及反向配置；分别执行 shards 2→1→0→1，再删除；另发布一个 emptyDir 实例；尝试原实例扩容量或换 storage mode。 删除已保留的原 PVC，再以同名新 UID 重建并刷新原操作，核对历史仍绑定原 UID。 | 每一步回读实际 StatefulSet 策略与原 PVC UID；缩容/挂起/删除的保留和删除结果分别正确；emptyDir 无 PVC；固定容量/模式不原地变更；同名替代卷不接管。 |
| E2E-06 发布冻结、漂移与版本回看 | 先发布 v1，再编辑草稿 v2；重复提交同幂等键；外部修改 CR spec 并尝试普通发布，再明确覆盖；保留外部 annotation；单独更新 status/RV 作正控；从版本入口读取来源。 | 同键无第二次写；v1 来源不随草稿变化；漂移先冲突、确认后才覆盖；annotation/status 不被清除；纯 status/RV 不触发重复确认；版本入口返回原配置。 |
| E2E-07 真实目标故障与状态隔离 | 两个集群各运行一个实例；用独占代理阻断 data API，保留 control 可用；等待观测过期并回读两者；恢复代理；给一个工作负载制造一个未 Ready 副本。 | control 正常结果不被 data 错误覆盖；data 显示具体访问错误/历史/过期，不能当成 0 个对象或已删除；恢复后重新观察；部分 Ready 显示未就绪。 |
| E2E-08 真实写响应丢失与重启禁重放 | 代理让真实 API 完成一次已知 UID 更新后丢响应；在确认前重建平台 Pod并恢复读取；再对首次创建丢响应、未保存 UID 的情况执行一次同名资源检查；对一个确定失败操作显式 retry。 I28-U05：有配套采集意图的父CR同样遵守未知UID和恢复禁重放。 | 已有 UID 只核验原资源，原 API 写次数不增加；首次创建身份不明不按名字补认/重建/删除；retry 使用原固定版本和新 attempt；重启不自动重放。 只冻结/恢复父资源，不引入独立SM派发。 |
| E2E-09 真实前台删除与阻塞保护 | 为受管 CR/子资源添加一个测试 finalizer 后从 HTTP 删除；确认仍受阻；移除自己的 finalizer 并等原 UID 图消失；另将同名 CR 替换为新 UID 后尝试删除；检查无关 namespace/对象。 I28-U07：删除同时只读核实Operator配套清理。 | 只有原 CR、workload、Pod 全消失才完成；阻塞期间不能解除保护/转移集群；新 UID 和无关对象未被平台删除；PVC 结果与 E2E-05 独立确认。 不按标签或同名替代UID误删配套。 |
| E2E-10 唯一浏览器主流程 | Playwright 登录→用表单创建一个 KSM 草稿→编辑测试夹具预置的 revision=9007199254740993 草稿→查看只读 YAML/diff→明确发布→工作台查看状态/版本→明确删除；前后通过后端读回确认操作对象与版本；不增加产品测试接口。 I28-B01：表单保存采集开关、interval/timeout，查看只读配套状态与不可变父CR来源。 I30-B01/B02在I26注销前完成快捷/自定义/namespace/管理员编辑/历史unknown与保护交互。 | 关键控件可操作、确认后才发发布/删除；显示正确实例和状态，大 revision 经浏览器操作后不舍入；没有前端控制台致命错误；不逐字段/逐模式重复验证后端规则。 配套收敛/故障与发布分别显示，无SM独立发布或修复按钮。 默认None/Own保持；明确点击固定creator；unknown禁快捷仍可按权限编辑custom；管理员编辑不重绑目标。 |

## 来源与报告

`cases.json` 为当前固定目录，`test-plan.md` 保存旧 ID 的合并/保留/放弃映射。`phase1.json` 和旧组件 runner 只供历史追溯/复用断言，不再作为 CI profile 或额外 case；旧 254 项工具自测不再执行，UT-18 仅三组必要可信边界。

`make ut` 生成 `.scratch/ut-results.json` 和 `.scratch/coverage.out`；`make e2e` 输出 `.scratch/e2e/<runId>/public/results.json`（含阶段/逐例耗时）、`cleanup.json` 和浏览器截图。CI 三组 artifact 为 `e2e-<runId>-<attempt>-<shard>`，完整汇总为 `e2e-complete-<runId>-<attempt>`。原始日志/报告、kubeconfig、数据库、证书、私有源码仅在同 run 的 `.private/`。CI 只上传 public 和 UT 安全摘要。

入口摘要：`Makefile` SHA256=9bb92765f73250f3b1c8544e88689eed4cb3481563993957cd81f82ea9f34155；workflow SHA256=9b39da6683bc36ab3aa760ff54239a3847a80ae348a2584baa045bddc273678c。
