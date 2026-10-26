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

执行结果另存 `docs/e2e/issue-23-review-fixes-results.md`；真实集群、浏览器、实际指标与最终覆盖率由集成任务执行并绑定最终 testedCommit。
