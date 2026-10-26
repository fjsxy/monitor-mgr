# Issue #23 review 修复补充测试计划

2026-10-26：基于 `6b8f166522f5e048df87c5e91e73f635e9e7d086` 修复 Standards review 及验收准备失败的安全定位。验证入口沿用 #23 已确认的公开 HTTP→Kitex→SQLite、公开验收 CLI、Ginkgo UT-18 工具边界与真实浏览器；不增加稳定顶层用例，UT18 / E2E10 编号保持不变。计划不是实际执行结果。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| I23-R01 / E2E-03 | 配套观察遵守 ADR0001 的页面边界 | 在既有浏览器采集意图流程查看 Operator 配套观察、故障及历史快照；检查页面可见文字 | 保留收敛状态、故障原因与新鲜度，不展示 generation 或 conditions 列表 | #23 / #28 |
| I23-R02 / UT-18 | source / fetch / verify 通过统一 Makefile 入口调用 | 使用显式 PYTHON、隔离 destination / bundle，分别执行三个参数化 target；缺少参数时调用 target；检查 CI 与 Environment 使用相同入口 | 三个入口可发现且参数原样传递，缺参拒绝；CI 与编排复用，保持匿名获取和逐字节摘要校验 | #23 / #28 |
| I23-R03 / UT-18 | 非零外部命令公开失败阶段和退出码 | 通过 Environment.command 执行受控失败 child，在固定 source / fetch / build label 下检查 public command-failure.json | 公开结果仅含固定阶段、固定错误码及整数 exit；原 stdout / stderr 仅进入 .private | #23 / #28 |
| I23-R04 / UT-18 | 未知或恶意 label 不被公开 | 用包含私有路径、token 标记及特殊字符的 label 执行失败 child；检查公开摘要及捕获文件权限 | 公开阶段降为固定 unknown，标记、路径、argv 与原始输出均不进入公共结果；原始捕获文件为私有 | #23 / #28 |
| I23-R05 / UT-18 | 超时仍能定位准备阶段 | 在固定发布源码获取 label 下执行超过极短预算的 child；读取公开安全摘要与外层 failure_summary | 标明固定阶段和超时类别，退出码不能从异常原文推测；未执行用例保持 not_run，不伪报业务失败或添加盲目重试 | #23 / #28 |
| I23-R06 / UT-18 | 非命令异常的准备失败仍保留可信源码位置 | 对受控准备失败调用与 E2E 顶层一致的 failure_summary；异常正文包含私有标记 | 仅报告冻结仓库 scripts 源码相对位置，正文/局部变量不公开；外层结果包含诊断且保留真实 errorType | #23 / #28 |
| I23-R07 / UT-10 / UT-12 | 冻结身份投影去重不改变行为 | 沿用公开 HTTP→Kitex→SQLite 的 KSM / Agent 发布、改名与原冻结目标恢复测试 | 新发布路径及旧冻结身份保持一致，namespace、name、UID 不从最新草稿重新计算 | #23 / #27 |
| I23-R08 | 中文职责注释和关键边界完整 | 对 Standards 点名的手写函数逐项核查中文职责注释；对授权、身份核验与恢复禁重放检查关键注释；执行 Go 格式、Python 语法、前端构建 | 注释说明当前职责及原因，关键边界使用“关键：”；低影响注释不新增镜像测试 | #23 / #25 / #26 / #27 / #28 / #29 / #30 |
| I23-R09.none / UT-08 / UT-10 | 新 CRD 不能替代实际 Operator 支持证据 | 完整新 schema 下，通过公开 HTTP→Kitex→SQLite 验证 无 controller | 拒绝；不借用 Prometheus Operator，平台 SM mutation 为零 | #23 / #28 |
| I23-R09.old / UT-08 / UT-10 | 新 CRD 不能替代实际 Operator 支持证据 | 完整新 schema 下，通过公开 HTTP→Kitex→SQLite 验证 旧运行镜像 | 拒绝；不借用 Prometheus Operator，平台 SM mutation 为零 | #23 / #28 |
| I23-R09.tag / UT-08 / UT-10 | 新 CRD 不能替代实际 Operator 支持证据 | 完整新 schema 下，通过公开 HTTP→Kitex→SQLite 验证 仅有 RC4 tag 且无 digest | 拒绝；不借用 Prometheus Operator，平台 SM mutation 为零 | #23 / #28 |
| I23-R09.digest / UT-08 / UT-10 | 新 CRD 不能替代实际 Operator 支持证据 | 完整新 schema 下，通过公开 HTTP→Kitex→SQLite 验证 实际 imageID 与批准 digest 不同 | 拒绝；不借用 Prometheus Operator，平台 SM mutation 为零 | #23 / #28 |
| I23-R09.ready / UT-08 / UT-10 | 新 CRD 不能替代实际 Operator 支持证据 | 完整新 schema 下，通过公开 HTTP→Kitex→SQLite 验证 原 Pod 未就绪 | 拒绝；不借用 Prometheus Operator，平台 SM mutation 为零 | #23 / #28 |
| I23-R09.read / UT-08 / UT-10 | 新 CRD 不能替代实际 Operator 支持证据 | 完整新 schema 下，通过公开 HTTP→Kitex→SQLite 验证 运行身份读取被拒绝 | 拒绝；不借用 Prometheus Operator，平台 SM mutation 为零 | #23 / #28 |
| I23-R09.preview / UT-08 / UT-10 | 新 CRD 不能替代实际 Operator 支持证据 | 完整新 schema 下，通过公开 HTTP→Kitex→SQLite 验证 预览成功后移除兼容 controller，再受理发布 | 拒绝且零业务写；不借用 Prometheus Operator，平台 SM mutation 为零 | #23 / #28 |
| I23-R09.dispatch / UT-08 / UT-10 | 新 CRD 不能替代实际 Operator 支持证据 | 完整新 schema 下，通过公开 HTTP→Kitex→SQLite 验证 受理成功后、后台派发屏障前移除兼容 controller | 异步任务失败且零业务写；不借用 Prometheus Operator，平台 SM mutation 为零 | #23 / #28 |
| I23-R09.compatible / UT-08 / UT-10 | 新 CRD 不能替代实际 Operator 支持证据 | 完整新 schema 下，通过公开 HTTP→Kitex→SQLite 验证 锁定 RC4 的实际 Deployment→ReplicaSet→Running/Ready Pod 与 imageID | 允许发布，CR 已写入/配套收敛/实际采集分别表达；不借用 Prometheus Operator，平台 SM mutation 为零 | #23 / #28 |
| I23-R09.disabled / UT-08 / UT-10 | 新 CRD 不能替代实际 Operator 支持证据 | 完整新 schema 下，通过公开 HTTP→Kitex→SQLite 验证 关闭模式且无运行 controller | 保持纯 exporter 发布，不新增运行能力依赖；不借用 Prometheus Operator，平台 SM mutation 为零 | #23 / #28 |
| I23-R10 / UT-11 / UT-12 | 旧多资源 DELETE 外部清理后可明确收尾 | 加载独立旧双资源删除夹具；管理员外部清理原父 CR、SM 与全部 workload；先确认 SM，再携带冻结快照、路径、原 UID 明确解除原删除操作 | 实例成为 DELETED、原互斥释放并独立留痕；旧任务状态、发送标记及冻结字节保持原样；平台 SM mutation 为零 | #23 / #27 / #29 |
| I23-R11.parent / UT-11 / UT-12 | 删除收尾不能跳过身份或清理核验 | 对 同名替代父 CR 请求旧删除显式收尾 | 拒绝、不释放互斥、不认领替代，不重写旧发送事实或写 SM | #23 / #27 / #29 |
| I23-R11.sm / UT-11 / UT-12 | 删除收尾不能跳过身份或清理核验 | 对 确认后重新出现同名 SM 请求旧删除显式收尾 | 拒绝、不释放互斥、不认领替代，不重写旧发送事实或写 SM | #23 / #27 / #29 |
| I23-R11.workload / UT-11 / UT-12 | 删除收尾不能跳过身份或清理核验 | 对 原 UID 图中残存 workload 请求旧删除显式收尾 | 拒绝、不释放互斥、不认领替代，不重写旧发送事实或写 SM | #23 / #27 / #29 |
| I23-R11.read / UT-11 / UT-12 | 删除收尾不能跳过身份或清理核验 | 对 资源图读取失败 请求旧删除显式收尾 | 拒绝、不释放互斥、不认领替代，不重写旧发送事实或写 SM | #23 / #27 / #29 |
| I23-R11.snapshot / UT-11 / UT-12 | 删除收尾不能跳过身份或清理核验 | 对 错误快照 请求旧删除显式收尾 | 拒绝、不释放互斥、不认领替代，不重写旧发送事实或写 SM | #23 / #27 / #29 |
| I23-R11.uid / UT-11 / UT-12 | 删除收尾不能跳过身份或清理核验 | 对 错误原 UID 请求旧删除显式收尾 | 拒绝、不释放互斥、不认领替代，不重写旧发送事实或写 SM | #23 / #27 / #29 |
| I23-R11.unresolved / UT-11 / UT-12 | 删除收尾不能跳过身份或清理核验 | 对 未确认旧 SM 请求旧删除显式收尾 | 拒绝、不释放互斥、不认领替代，不重写旧发送事实或写 SM | #23 / #27 / #29 |
| I23-R11.ordinary / UT-11 / UT-12 | 删除收尾不能跳过身份或清理核验 | 对 非管理员 请求旧删除显式收尾 | 拒绝、不释放互斥、不认领替代，不重写旧发送事实或写 SM | #23 / #27 / #29 |

| I23-R12 / E2E-03 | RC4 停用终态不伪造 SM generation | 先关闭再明确启用并取得真实 SM，随后停用父 spec.enabled=false；读取父 Disabled 及其当前观察、原始 SM Disabled；独立列出原父 UID 资源图与全部 SM，核实旧子对象及 workload/Service/Pod 清空 | 不强求 RC4 未承诺更新的 SM observedGeneration；保留原值，清理事实必须完整且不得把终态误称实际采集成功 | #23 / #28 |
| I23-R13 / E2E-08 | 授权验收与未知发送任务互斥隔离 | 在同一稳定 E2E-08 中先完成写前撤权/已发只读恢复和归属还原，再运行故意制造 IDENTITY_UNCERTAIN 的恢复与历史迁移输入 | 转移只发生在无未完成管理操作时；未知发送任务与实例锁保持原样，不修改产品 HasUnfinishedChangesTx guard | #23 / #25 / #27 / #29 |
| I23-R14 / E2E-09 | 真实 finalizer 阻塞身份与读取诊断 | 对原 UID 施加 fixture hold finalizer 时同时核对 RV；删除前回读确认 hold；删除后仅移除本例原 UID 上的 hold；分别覆盖成功、NotFound 与读取失败的固定分类 | 必须在实际存在 hold 的原对象上证明删除被阻止，原图清空才完成；读取失败与 NotFound 不混淆，拒绝同名替代且清理失败保留原 UID 责任 | #23 / #27 / #29 |
| I23-R15 / UT-18 | 云端 UT 失败可安全定位原生 case | 通过 capture.project_output 的冻结源码边界输入失败 Ginkgo 原始输出与原生报告；含任意 Failure.Message/私有 marker 负例 | 只投影固定 UT-ID、真实状态、冻结源码位置和 Go 超时固定类别；正文/路径/凭据原文不公开，原始日志保持私有 | #23 |
| I23-R16 / UT-18 | 超过 1MiB 的真实 UT 日志不饿死末尾失败 | 输入大体积前缀和末尾固定 Ginkgo fail/Go test timeout、running tests 与 Make 错误；固定用例及路径须经当前候选源码核对 | 可定位末尾真实失败及超时；有界解析、截断明确 unknown，不凭 exit=2 断定超时 | #23 |

| I23-R17.publish / UT-11 | 已显式处理旧发布不永久阻塞集群变更 | 完成绑定原 operation 的独立历史 PUBLISH 处理后，通过公开 HTTP 转移到当前所属用户 | 旧未知执行状态保持原样，已处理历史项不再计入集群未结束互斥 | #23 / #27 / #29 |
| I23-R17.delete / UT-11 | 已显式收尾旧删除不永久阻塞集群变更 | 完成独立历史 DELETE 收尾后，通过公开 HTTP 转移到当前所属用户 | DELETED 与独立处理事实保留，旧 DELETE 原状态不伪改成功，集群转移成功 | #23 / #27 / #29 |
| I23-R17.other / UT-11 | 独立处理不能放行其他未知任务 | 旧任务显式处理后，在同集群新建另一个首次创建响应丢失任务，再请求转移/注销 | 新任务保持 IDENTITY_UNCERTAIN 和原锁；转移/注销均拒绝 | #23 / #27 / #29 |
| I23-R18 / E2E-04 | Agent 长退出宽限不造成夹具清理倒序 | 采集与产品删除断言全部完成后，先固定本 run 原 CR UID 下的 Pod UID 图；发送原 CR Foreground 删除后、等待前，仅对已确认 namespace UID/label 内原 Pod 按 UID grace=0 加速；核实原图消失 | 只加速本 run 夹具 Pod；不触碰其他 run/同名替代，不缩短业务删除断言，失败仍保留真实清理收据 | #23 / #30 |

| I23-R14.gone / UT-18 | 最后一个 fixture hold 释放后404正确退休责任 | 真实受控 HTTP 夹具 PATCH 移除 hold 后立即删除原对象，运行实际 cleanup helper | 固定 finalizers/gone 收据成功、原 UID 清理登记退休、零额外 DELETE | #23 / #27 / #29 |
| I23-R14.conflict / UT-18 | RV 条件冲突可安全重读原身份 | 第一次 PATCH 返回409，随后回读仍为原 UID 并使用其当前 RV 重试 | 有界重试只针对明确条件冲突，每次 PATCH 保留原 UID/RV test，最终原图清空 | #23 / #27 / #29 |
| I23-R14.replaced / UT-18 | finalizer 处理后同名替代不接管 | PATCH 后读到同名新 UID | identity/uid-mismatch 失败、登记保留，不对替代发 DELETE | #23 / #27 / #29 |
| I23-R19 / E2E-10 | 真实旧库浏览器必须明确选择采集启用 | 打开真实 v10 large revision 实例，先断言配套开关未选中；用户明确勾选后才填写 interval/timeout 并保存、预览、发布 | 升级不自动 enable，缺省关闭事实保留；明确操作后才出现采集参数，精确 revision 和真实发布断言继续执行 | #23 / #28 / #29 |

| I23-R09.admission / UT-08 | 实际 Pod 的 SA admission 字段不能误判版本 | 锁定 RC4 的 Pod 含 Kubernetes 自动注入的 serviceaccount token volumeMount，Deployment template 没有该字段；经公开预览/发布 | 核对运行镜像、原UID链、参数与当前SA，允许正常 admission 差异；env/command 等可能改写控制目标的覆盖仍拒绝 | #23 / #28 |

| I23-R09.env / UT-08 | 已批准镜像不能以环境覆盖控制目标 | 在 Running/Ready 原 Pod 上声明 KUBERNETES_SERVICE_HOST 环境覆盖，再请求公开预览 | 拒绝且零业务写，不能用同一镜像 digest 为其他目标背书 | #23 / #28 |
| I23-R09.command / UT-08 | 已批准镜像不能替换入口进程 | 在原 Pod 覆盖 command，再请求公开预览 | 拒绝且零业务写，tag/digest 不能代替实际 controller 进程 | #23 / #28 |
| I23-R20 | 根据实测完整矩阵给Go UT有界预算余量 | 6f6 CI完整18项已绿，platform耗时合计约1083s、完整命令19分30；本轮新增R21/R22必选输入后将Go包预算20m调整25m，CI job仍30m；核Makefile/help与固定目录 | 不减少输入或断言；最终native18项/race/覆盖率决定成功；旧CI exit2原因仍未知，不以扩预算声称已证实超时 | #23 / #25 / #26 / #27 / #28 / #29 / #30 |

| I23-R21.manager / UT-08 | 已批准镜像的入口不可被挂载替换 | 经公开 HTTP→Kitex→SQLite，在真实Pod额外挂载覆盖/manager | 拒绝预览且零业务写 | #23 / #28 |
| I23-R21.template / UT-08 | Deployment模板不能为同样覆盖背书 | 经公开 HTTP→Kitex→SQLite，模板与Pod都声明覆盖/manager的相同挂载 | 拒绝，不把模板当作任意执行覆盖的可信来源 | #23 / #28 |
| I23-R21.source-hostpath / UT-08 | SA注入来源必须是受控projected token | 经公开 HTTP→Kitex→SQLite，保留正常SA mountPath但将volume改为hostPath，执行相应预览/历史收尾 | 拒绝，不因路径或volume名称相同而信任 | #23 / #28 |
| I23-R21.source-configmap / UT-08 | SA注入来源必须是受控projected token | 经公开 HTTP→Kitex→SQLite，保留正常SA mountPath但将volume改为错误configMap，执行相应预览/历史收尾 | 拒绝，不因路径或volume名称相同而信任 | #23 / #28 |
| I23-R21.token-path / UT-08 | SA token投影路径和audience不可改变 | 经公开 HTTP→Kitex→SQLite，修改projected serviceAccountToken path，执行相应预览/历史收尾 | 拒绝，保留当前SA身份和默认API audience边界 | #23 / #28 |
| I23-R21.token-audience / UT-08 | SA token投影路径和audience不可改变 | 经公开 HTTP→Kitex→SQLite，修改projected serviceAccountToken audience，执行相应预览/历史收尾 | 拒绝，保留当前SA身份和默认API audience边界 | #23 / #28 |
| I23-R21.subpath / UT-08 | SA token投影路径和audience不可改变 | 经公开 HTTP→Kitex→SQLite，给标准SA挂载增加subPath，执行相应预览/历史收尾 | 拒绝，保留当前SA身份和默认API audience边界 | #23 / #28 |
| I23-R21.lifecycle / UT-08 | 容器执行相关配置不得额外覆盖 | 经公开 HTTP→Kitex→SQLite，加入lifecycle执行钩子，执行相应预览/历史收尾 | 拒绝；不扩大monitor-op API | #23 / #28 |
| I23-R21.sidecar / UT-08 | 容器执行相关配置不得额外覆盖 | 经公开 HTTP→Kitex→SQLite，加入额外容器，执行相应预览/历史收尾 | 拒绝；不扩大monitor-op API | #23 / #28 |
| I23-R21.writable-root / UT-08 | 容器执行相关配置不得额外覆盖 | 经公开 HTTP→Kitex→SQLite，使manager根文件系统可写，执行相应预览/历史收尾 | 拒绝；不扩大monitor-op API | #23 / #28 |
| I23-R21.normal / UT-08 | 正常真实SA admission继续允许 | 经公开 HTTP→Kitex→SQLite，Pod有标准serviceAccountToken+kube-root-ca.crt+namespace downwardAPI投影，模板无挂载 | 允许预览/发布，实际UID链、imageID与写前重检继续有效 | #23 / #28 |
| I23-R22.leftover-Service / UT-11 | 完整原UID图包含Service | 旧DELETE已按原冻结快照确认旧SM并使旧路径404，原parent UID仍拥有另一份Service；请求显式收尾 | 拒绝、保留实例与原operation锁及未知执行事实；不写SM，不按名称算法认领 | #23 / #27 / #29 |
| I23-R22.denied-Service / UT-11 | Service读取未知不能当清空 | 对完整原图的Service列表返回Forbidden，旧路径均404；请求显式收尾 | 拒绝、保留保护；namespace/cluster作用域正确 | #23 / #27 / #29 |
| I23-R22.leftover-ServiceAccount / UT-11 | 完整原UID图包含ServiceAccount | 旧DELETE已按原冻结快照确认旧SM并使旧路径404，原parent UID仍拥有另一份ServiceAccount；请求显式收尾 | 拒绝、保留实例与原operation锁及未知执行事实；不写SM，不按名称算法认领 | #23 / #27 / #29 |
| I23-R22.denied-ServiceAccount / UT-11 | ServiceAccount读取未知不能当清空 | 对完整原图的ServiceAccount列表返回Forbidden，旧路径均404；请求显式收尾 | 拒绝、保留保护；namespace/cluster作用域正确 | #23 / #27 / #29 |
| I23-R22.leftover-Role / UT-11 | 完整原UID图包含Role | 旧DELETE已按原冻结快照确认旧SM并使旧路径404，原parent UID仍拥有另一份Role；请求显式收尾 | 拒绝、保留实例与原operation锁及未知执行事实；不写SM，不按名称算法认领 | #23 / #27 / #29 |
| I23-R22.denied-Role / UT-11 | Role读取未知不能当清空 | 对完整原图的Role列表返回Forbidden，旧路径均404；请求显式收尾 | 拒绝、保留保护；namespace/cluster作用域正确 | #23 / #27 / #29 |
| I23-R22.leftover-RoleBinding / UT-11 | 完整原UID图包含RoleBinding | 旧DELETE已按原冻结快照确认旧SM并使旧路径404，原parent UID仍拥有另一份RoleBinding；请求显式收尾 | 拒绝、保留实例与原operation锁及未知执行事实；不写SM，不按名称算法认领 | #23 / #27 / #29 |
| I23-R22.denied-RoleBinding / UT-11 | RoleBinding读取未知不能当清空 | 对完整原图的RoleBinding列表返回Forbidden，旧路径均404；请求显式收尾 | 拒绝、保留保护；namespace/cluster作用域正确 | #23 / #27 / #29 |
| I23-R22.leftover-ClusterRole / UT-11 | 完整原UID图包含ClusterRole | 旧DELETE已按原冻结快照确认旧SM并使旧路径404，原parent UID仍拥有另一份ClusterRole；请求显式收尾 | 拒绝、保留实例与原operation锁及未知执行事实；不写SM，不按名称算法认领 | #23 / #27 / #29 |
| I23-R22.denied-ClusterRole / UT-11 | ClusterRole读取未知不能当清空 | 对完整原图的ClusterRole列表返回Forbidden，旧路径均404；请求显式收尾 | 拒绝、保留保护；namespace/cluster作用域正确 | #23 / #27 / #29 |
| I23-R22.leftover-ClusterRoleBinding / UT-11 | 完整原UID图包含ClusterRoleBinding | 旧DELETE已按原冻结快照确认旧SM并使旧路径404，原parent UID仍拥有另一份ClusterRoleBinding；请求显式收尾 | 拒绝、保留实例与原operation锁及未知执行事实；不写SM，不按名称算法认领 | #23 / #27 / #29 |
| I23-R22.denied-ClusterRoleBinding / UT-11 | ClusterRoleBinding读取未知不能当清空 | 对完整原图的ClusterRoleBinding列表返回Forbidden，旧路径均404；请求显式收尾 | 拒绝、保留保护；namespace/cluster作用域正确 | #23 / #27 / #29 |
| I23-R22.leftover-Endpoints / UT-11 | 完整原UID图包含Endpoints | 旧DELETE已按原冻结快照确认旧SM并使旧路径404，原parent UID仍拥有另一份Endpoints；请求显式收尾 | 拒绝、保留实例与原operation锁及未知执行事实；不写SM，不按名称算法认领 | #23 / #27 / #29 |
| I23-R22.denied-Endpoints / UT-11 | Endpoints读取未知不能当清空 | 对完整原图的Endpoints列表返回Forbidden，旧路径均404；请求显式收尾 | 拒绝、保留保护；namespace/cluster作用域正确 | #23 / #27 / #29 |
| I23-R22.leftover-EndpointSlice / UT-11 | 完整原UID图包含EndpointSlice | 旧DELETE已按原冻结快照确认旧SM并使旧路径404，原parent UID仍拥有另一份EndpointSlice；请求显式收尾 | 拒绝、保留实例与原operation锁及未知执行事实；不写SM，不按名称算法认领 | #23 / #27 / #29 |
| I23-R22.denied-EndpointSlice / UT-11 | EndpointSlice读取未知不能当清空 | 对完整原图的EndpointSlice列表返回Forbidden，旧路径均404；请求显式收尾 | 拒绝、保留保护；namespace/cluster作用域正确 | #23 / #27 / #29 |
| I23-R22.leftover-ServiceMonitor / UT-11 | 完整原UID图包含ServiceMonitor | 旧DELETE已按原冻结快照确认旧SM并使旧路径404，原parent UID仍拥有另一份ServiceMonitor；请求显式收尾 | 拒绝、保留实例与原operation锁及未知执行事实；不写SM，不按名称算法认领 | #23 / #27 / #29 |
| I23-R22.denied-ServiceMonitor / UT-11 | ServiceMonitor读取未知不能当清空 | 对完整原图的ServiceMonitor列表返回Forbidden，旧路径均404；请求显式收尾 | 拒绝、保留保护；namespace/cluster作用域正确 | #23 / #27 / #29 |
| I23-R22.shape / UT-11 | 原资源图读取完整性与身份 | 必要列表缺少items，经公开HTTP执行旧DELETE显式收尾 | 拒绝，保留原保护和独立历史事实 | #23 / #27 / #29 |
| I23-R22.page-denied / UT-11 | 原资源图读取完整性与身份 | 经公开 HTTP→Kitex→SQLite，完整分页第二页读取失败，执行相应预览/历史收尾 | 拒绝，保留原保护和独立历史事实 | #23 / #27 / #29 |
| I23-R22.page-repeat / UT-11 | 原资源图读取完整性与身份 | 经公开 HTTP→Kitex→SQLite，continue未前进，执行相应预览/历史收尾 | 拒绝，保留原保护和独立历史事实 | #23 / #27 / #29 |
| I23-R22.replacement / UT-11 | 原资源图读取完整性与身份 | 持久seen UID路径出现同名新UID，经公开HTTP执行旧DELETE显式收尾 | 拒绝，保留原保护和独立历史事实 | #23 / #27 / #29 |
| I23-R22.empty / UT-11 | 原资源图读取完整性与身份 | 所有必要列表均合法空items，旧SM处理绑定有效，经公开HTTP执行旧DELETE显式收尾 | 成功独立收尾，旧发送事实不伪改 | #23 / #27 / #29 |


| I23-R22.api-absent / UT-11 | 可选SM API确实不存在与未知区分 | 原冻结路径404、SM独立处理绑定有效、全图其他必要列表合法空；SM列表API返回404 | 可独立收尾；403、缺items、分页失败均不作为不存在 | #23 / #27 / #29 |
| I23-R23.namespace-permission / E2E-02 | 受限namespace写权限负例到达目标边界 | limited SA保留原CR权限和仅Operator Deployment/ReplicaSet/Pod只读能力；禁止目标namespace workload写，公开preview后在已授权namespace发布 | 原namespace明确权限拒绝且无CR；已授权namespace可发布；不新增CR/SM写权限 | #23 / #28 |
| I23-R24.unknown-window / E2E-08 | 首次创建丢响应只核当前子例单次发送 | 授权子例已创建auth-known CR后，记录代理事件基线，再触发unknown首次POST丢响应及只读恢复 | 当前窗口仅1次POST；原UID未知不认领，同名替代不认领，恢复零重放；保留未知任务锁 | #23 / #25 |

| I23-R25.collector-name / UT-18、E2E-08 | 派生namespace满足DNS63且保持唯一身份 | 输入CI实际run-id和最大39字符run-id，历史collector从长exporter namespace派生；对相近长前缀生成并核验名称与清理引用 | 名称≤63、DNS合法且稳定、不同输入不碰撞；完整run-id label和实际namespace UID继续绑定清理，所有引用使用同一派生名 | #23 / #29 |

| I23-R22.seen-child / UT-11 | 中间原Service已消失仍识别残存后代 | 持久seen原Service UID，完整列表只有该UID拥有的EndpointSlice；请求历史DELETE收尾 | 拒绝，原UID闭包跨已消失中间节点保持保护 | #23 / #27 / #29 |
| I23-R22.optional-page / UT-11 | 可选API后续页404不是完整清空 | SM首页合法items且有continue，第二页404；请求历史DELETE收尾 | 拒绝，不能把后续缺页视为API确实不存在 | #23 / #27 / #29 |
| I23-R22.active-rebuild / UT-14 | 原父活跃时Operator重建同名子对象仍可观察 | 原父UID不变，已观察SM从UID-a重建为UID-b，再公开refresh和删除预览 | 识别原父拥有的新子UID；与历史父已消失时的完整清空条件分别核验 | #23 / #27 / #28 |

| I23-R26.browser-diagnostic / UT-18、E2E-10 | 浏览器失败不再只见泛化外层assert | 构造嵌套test.step失败报告并含私有fixture/响应/token标记、未知标题/路径/状态；投影固定step标题、经源码核实的位置与白名单期望/实际枚举；真实浏览器失败时保存同一摘要 | 安全摘要可定位固定源码/阶段；未知值降为unknown，所有任意原文、绝对路径、凭据不公开；真实断言与预算保持原样 | #23 / #26 / #28 / #29 / #30 |

| I23-R27.rv-conflict / UT-18、E2E-08/09 | 条件写使用可判别的RV冲突 | 对真实K8s1.28仅server dry-run，以metadata.uid/resourceVersion MergePatch核错误RV、正确UID；工具HTTP复现首次409后再读RV并成功 | 真实API错误可区分Conflict；最多3次且每次新GET核原UID；不减弱条件、不重放业务发送 | #23 / #25 / #27 / #29 |
| I23-R27.uid-mismatch / UT-18 | UID替代与RV并发分开处理 | 真实server dry-run核错误UID且正确RV；工具HTTP在重读时返回同名新UID | 不修改任何验收对象；UID不一致立即停止，不因Invalid或同名继续写 | #23 / #27 / #29 |
| I23-R27.unchanged / UT-18 | 正常无变化条件写保持合法 | 真实server dry-run用正确UID/RV写相同字段；工具HTTP清除本例hold并保留其它finalizer、之后GET404 | dry-run成功且不改变资源；fixture仅编辑自己字段、原UID最后hold释放后404可退休 | #23 / #27 / #29 |
| I23-R27.unknown / UT-18 | 未知Invalid/Forbidden不能当RV冲突 | 工具HTTP分别返回泛化422、403、网络错误、连续409；诊断含任意私有标记 | 只有明确409有界重读，其他错误立即失败；公开固定阶段/分类/尝试次数，无原文/路径/凭据 | #23 / #27 / #29 |
| I23-R27.legacy / UT-18、E2E-08 | 历史fixture移除字段同用原UID/RV条件契约 | 旧CR spec.serviceMonitor存在时仅MergePatch该字段为null；首次RV冲突后核原UID/新RV，其他spec保持 | 不再使用单次JSONPatch test错误猜测；字段移除确认且旧fixture身份/其它spec不被覆盖 | #23 / #29 |

| I23-R28.frames / UT-18、E2E-10 | 浏览器公共helper失败定位实际调用点 | 嵌套错误stack含同候选白名单web测试源码多个有效帧、外部私有路径及任意消息；生成安全摘要 | 保留有界可信调用帧数组；不输出任意路径/原文；固定step定位不变 | #23 / #25 / #26 / #29 / #30 |
| I23-R28.http / UT-18、E2E-10 | API断言仅公开有限HTTP枚举 | Expected/Received行分别含200/201/202/400/401/403/404/409/422/500/503，以及其它数字/私有标记 | 白名单状态可区分；未知值仍unknown；不导出APIpath、正文或凭据 | #23 / #25 / #26 / #29 / #30 |
| I23-R29.browser-order / E2E-10 | 既存历史未知任务不得污染归属转移子场景 | 先以本地真实Playwright原报告确认失败API/状态/代码；若为原迁移保护造成transfer409，先完成原I29管理员显式旧SM处理/历史release再执行I25转移和创建身份保护 | 产品未完成任务guard保持；旧未知发送不伪改；浏览器两组原断言完整执行；诊断前不猜测改断言 | #23 / #25 / #29 |

| I23-R30.current-mtime / UT-18 | 合成报告明确绑定当前捕获时间 | 在固定临时Git源码与报告夹具中显式设置capture开始时间及报告mtime为相同边界或其后；含尾部失败 | 当前原生失败可投影；不依赖真实微秒写入时序或宿主文件时间量化 | #23 / #25 / #26 / #27 / #28 / #29 / #30 |
| I23-R30.old-mtime / UT-18 | 旧报告拒绝仍保持 | 显式将同一路径native报告mtime置于capture开始时间之前，保留其它内容完全相同 | 原生旧报告不进入nativeFailures；生产来源判定不放宽，时间量化情形可重复定位 | #23 / #25 / #26 / #27 / #28 / #29 / #30 |

| I23-R27.rules / UT-18、E2E-03/04 | 实际RBAC故障注入也使用可判别条件写 | 原ClusterRole UID/RV下仅替换既定rules，首次Conflict后新读原UID/RV；恢复完整原规则 | UID替代/未知不写；仅明确Conflict有界重读；不扩大平台SA权限，SM写仍为空 | #23 / #28 / #30 |

执行结果另存 `docs/e2e/issue-23-review-fixes-results.md`；真实集群、浏览器、实际指标与最终覆盖率由集成任务执行并绑定最终 testedCommit。
