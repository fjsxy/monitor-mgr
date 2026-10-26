# Issue #23：配套 ServiceMonitor 职责调整范围

2026-10-23 用户确认，配套 ServiceMonitor 改由 monitor-op 管理，monitor-mgr 负责配置与 Agent 关联。本文记录已确认范围及任务关系，不代表实现或测试完成。用户随后授权同步 #25–#30 并创建 Operator 实现 Issue，现已发布；本轮不领取、不编码，不修改 PR。

远端已同步 [Issue #23 正文及修订记录](https://github.com/fjsxy/monitor-mgr/issues/23#issuecomment-6362600022)，旧实现验收评论保留为历史证据。

后续授权的六项任务修订、两个 Operator 新任务及依赖关系，见 [跨仓库任务同步记录](https://github.com/fjsxy/monitor-mgr/issues/23#issuecomment-6363558261)。

## 已核实基线

| 仓库 | 当前检视基线 | 本次需要处理的现状 |
| --- | --- | --- |
| monitor-mgr | 本地 `main`：`0238eba7cf0a6482236accb2f0a13acb7087ad5c`；未合并 PR #31：`6bad701b14859423131758f52456ba0afe03fe5c` | 本地决策与 PR 均采用平台直写 ServiceMonitor 的旧方案；后续实施必须以选定且重新核实的代码基线进行调整，不能只在旧 main 上追加新逻辑 |
| monitor-op | 本地 `main`：`2b2a8b79a52070fb89a08f5afae2b7698901c999`；remote：`fjsxy/monitor-in-hand` | `KubeStateMetricsSpec` 尚无配套采集配置；资源规划、apply、watch、清理、status 和 Helm RBAC 均需支持 ServiceMonitor |

两仓库存在其他未提交改动，后续实施前重新盘点并保留。PR #31 的既有绿色验收只证明旧方案；其创建身份、历史权限保护、Agent 关联和安全恢复能力应评估复用，不能将整份实现或整个逐资源模型一并删除。

## 职责与共同契约

| 事项 | monitor-mgr | monitor-op |
| --- | --- | --- |
| 用户与权限 | 固定实例创建用户、创建时角色；执行平台授权 | 不引入账号、角色或平台授权模型 |
| 配套配置意图 | 草稿、预览、不可变版本；明确发布时写入 exporter CR | 校验并执行 CR 中的采集声明 |
| 配套对象 | 只读展示和观察，不直接写入 | 唯一创建、更新、漂移修复、缺失重建与清理者 |
| Service 契约 | 不复制命名、标签、端口或分片拓扑推导算法 | 在同一资源规划中确定 Service 与 ServiceMonitor |
| Agent 关联 | 用户快捷关联、自定义 selector、namespace 范围与匹配预览 | 不管理 Agent、remote_write 或外部采集配置 |

以下是实现前需共同冻结的最小 CR 契约，字段名暂定为 `spec.serviceMonitor`，本轮不生成 API 或 schema：

- `enabled`：缺省为 `false`；旧 CR 与旧冻结版本保持原行为。平台新 exporter 草稿默认开启，但只在明确发布时写入 `true`；历史实例须新预览、新发布后启用。
- `labels`：通用附加标签。平台根据可信创建关系设置 `monitor-mgr.io/user-id`，不接受客户端冒充；Operator 原样投影，但拒绝覆盖其保留的身份与管理标签，不解释 user ID 的业务含义。
- `interval`、`scrapeTimeout`：可选参数，校验合法 duration 及超时不超过间隔；省略时沿用旧方案的 `30s`、`10s`。不开放任意 Service selector、目标 namespace 或端口覆盖，避免破坏实例关联。
- ServiceMonitor 的 namespace 为 `spec.targetNamespace`，名称由 Operator 稳定派生；一个实例的配置覆盖全部必要 Service。KSM 指标端口为 `http-metrics`，路径为 `/metrics`；`telemetry` 不是此次默认采集目标。
- 当前 KSM CR 是 cluster-scoped，配套对象的 controller ownerReference 指向实际 CR UID；不能照搬 namespaced CR 的 namespace 假设。身份和所有权按 Kubernetes 规则验证，不凭名称或 user 标签认领。
- Operator 回报配套配置的收敛结果、故障原因及对象引用和 UID，并绑定已观察的 exporter generation。平台按引用回读、核对 owner UID，区分未知、读取失败、未收敛、已收敛；子资源被 Operator 合法重建时允许 UID 更新。
- 新平台预检识别 CRD schema 与 Operator 能力，拒绝把字段提交给会裁剪未知字段的旧版本；仅看到 ServiceMonitor CRD 存在不能证明 monitor-op 支持此契约。
- 平台只需要 exporter CR 写权限及状态/配套对象读取权限，不再要求 ServiceMonitor 的 create/update/patch/delete 权限。Operator 自身另需 ServiceMonitor 管理权限；不能用平台的 SelfSubjectAccessReview 代替 Operator 权限结论。

## monitor-op 改动范围

1. **声明契约**：扩展 `api/v1alpha1/kubestatemetrics_types.go` 的 spec 和配套观察信息，补齐默认值、校验和中文说明。同步 deepcopy、`config/crd/bases/` 与 `charts/monitor-in-hand/crds/`；保持现有 CR 兼容，不打包或接管 Prometheus Operator 的 CRD。
2. **规划与生命周期**：在 `internal/kubestatemetrics/planner.go` 及相关生命周期逻辑生成配套资源，复用现有名称规范化和实例标签；覆盖 Deployment、StatefulSetSharded、DaemonSetSharded 的必要 Service 集合和多实例隔离。挂起保留 ServiceMonitor；停用、删除清理它；关闭配套采集只清理本实例的配套对象；恢复按 CR 声明重建。
3. **调谐、身份与观察**：扩展 `internal/controller/kubestatemetrics_controller.go` 中 apply、owner 冲突检查、资源引用、故障状态、finalizer 和待删除核验。只修复本 CR UID 所拥有的资源，不覆盖同名外部对象；失败保留既有 exporter，由 Operator 持续重试。子对象控制面收敛不等于指标采集成功。
4. **可选依赖**：扩展 `cmd/main.go`、`internal/operator/manager.go` 相关 scheme、直读、cache 和 watch 配置；现有 cache 按 managed-by 过滤，精确身份冲突检查仍须能够读到无标签的同名外部对象。未安装 ServiceMonitor CRD 且未启用时，Operator 能启动并管理纯 exporter；启用但 API/权限缺失要报告依赖故障，恢复依赖后能收敛，不能因无条件注册 informer 阻塞全局启动。
5. **部署与文档**：通过现有生成入口维护 `config/rbac/role.yaml`、`charts/monitor-in-hand/generated/manager-role.yaml` 及 Helm 相关模板，授予 Operator 必要的 ServiceMonitor 权限；更新 README、示例、GLOSSARY 和既有验收矩阵中的所有权边界。Agent、管理员独立声明的 ServiceMonitor/PodMonitor/Probe/ScrapeConfig 及其 workload 继续不受本 Operator 管理。
6. **验收与命令**：扩展现有 Ginkgo planner/controller/manager 测试与真实 kind E2E；保持 Operator 自己的双版本支持矩阵，不将平台当前的 Kubernetes 1.28 限制推广到 Operator。按需固化 Makefile 入口，不批量改动无关测试或基线。

## monitor-mgr 改动范围

1. **草稿与类型化接口**：扩展 `internal/control/ksm_config.go`、`idl/control.thrift`、HTTP/Kitex 映射及 `web/src/`，表达采集开关和参数。归属标签由服务端固定创建用户派生，不开放任意覆盖；保存草稿无集群写入。
2. **发布与兼容预检**：调整 `internal/control/publications.go`、`publication_worker.go` 和组件适配，仅冻结、预览并写入包含采集意图的 exporter CR。移除对 SM 完整 YAML 的平台生成，以及 Service 名称、selector 和端口算法复制；替换 PR #31 `companion.go` 中相关逻辑。检查目标 Operator 契约支持及读取能力，不把平台用户的 SM 写权限作为新方案前置。
3. **操作与删除**：调整 `deletion.go`、`deletion_preview.go`、`lifecycle_recovery.go` 及 PR #31 的资源步骤派发，确保新模式没有 ServiceMonitor 写入。删除 exporter CR 后只读核验 Operator 清理；CR 写入成功、配套配置收敛与实际采集分开显示。保留原 exporter UID、发送边界、结果待确认及历史审计保护。
4. **观察与页面**：调整 `observations.go`、`observation_worker.go` 及 `web/src/instances.tsx`、`lifecycle.tsx` 等相关读模型，读取 Operator 回报和配套对象，展示配置、收敛状态及故障原因。原先“重试第二份 CR”交互改为观察 Operator 修复结果；用户修改期望配置仍走新预览和新发布。
5. **创建身份与 Agent**：复用、保留 PR #31 的固定创建身份、管理员实例保护、可靠历史补齐与独立权限确认；保留按 Agent 创建用户生成 `serviceMonitorSelector`、自定义规则、namespace 范围和匹配预览。标签来源变化不能改变归属语义；不按当前登录账号重置 Agent 关联。
6. **持久化与历史版本**：审查 `migrations.go`、`resource_progress.go`、`resource_worker.go` 等旧方案逻辑，区分历史已冻结多资源数据与新模式。不要为了简化新操作删除旧记录、篡改旧 YAML、重置 UID 或给在途操作追加资源；新模式停止 SM 写入，但通用恢复和审计能力按实际用途保留。
7. **验收与部署基线**：维护平台的 Ginkgo UT/E2E、真实 Kitex/SQLite 故障夹具、浏览器、`scripts/acceptance/`、验收目录及 Makefile；平台测试环境锁定支持新契约的 Operator 镜像与 Helm CRD/RBAC，不将旧 Operator 上的测试成功当作本方案证据。

## 生命周期、失败与迁移边界

| 场景 | 目标行为 |
| --- | --- |
| 草稿保存 | 只保存平台配置，不写 exporter CR 或 ServiceMonitor |
| 新版本明确发布 | 平台写 exporter CR；Operator 创建、更新配套配置；不假设跨资源原子性 |
| ServiceMonitor 创建或更新失败 | 保留运行中的 exporter，显示具体故障；Operator 持续重试，不由平台另发 SM 写入 |
| SM 被删除或修改 | Operator 核实自己拥有的身份后重建或修复；外部同名对象阻断，不接管 |
| 挂起、停用、恢复 | 挂起保留非 workload 资源；停用清理全部受管资源；恢复按声明重建 |
| 删除实例 | 平台以已确认 exporter UID 发起删除，Operator finalizer 清理；只读核实配套资源消失，不误删同名替代对象 |
| 平台撤权、超时或重启 | 阻止后续平台写入；已发布期望态由 Operator 持续执行；原 CR UID 不明时禁止认领和重放 |
| 历史 CR 无新字段 | 默认不生成 SM；新版本明确启用之前，升级和轮询不补建 |

旧方案迁移仅在实际存在旧数据、旧任务或旧 ServiceMonitor 时执行，不预设 PR 已部署。顺序是：

1. 盘点已部署版本、旧任务发送边界、exporter/ServiceMonitor 的实际 UID、所有权和 Agent 选择范围；保留数据库及对象快照。
2. 停止旧平台对 SM 的派发。已发送请求只读核验；未完成旧任务保留历史并显式处理，不自动续写、重放或转换为新模式。旧单 CR 任务仍按原冻结语义处理。
3. 旧平台创建的 SM 不由 Operator 自动接管。由集群管理员在核实 UID、配置和采集影响后显式清理旧对象，再进行新的平台发布；有采集空窗时事先纳入切换安排。核对所有被 Agent 选中的旧对象，避免新旧名称不同而留下重复采集。
4. 启用新 CR 契约，验证只有 Operator 写配套对象、Agent 实际采到预期指标，并核实旧资源和旧写入路径已退出。

本轮不执行迁移，不更改集群或数据库。

## 旧任务影响与交付顺序

| 原有子 Issue | 新方案下的处理方向 |
| --- | --- |
| #25 创建身份与保护、#26 历史身份 | 主要行为保留，结合新配置入口补齐授权回归 |
| #27 逐资源进度与恢复 | 保留通用恢复、历史数据与 UID 边界；不再作为平台新增第二份受管 CR 的理由 |
| #28 配套生命周期 | 核心生成与调谐转移到 monitor-op；平台范围改为意图发布及只读观察 |
| #29 历史补建 | 改为新 CR 的显式启用及旧方案资源迁移，不再由平台补写 SM |
| #30 Agent 关联 | 归属、selector 与实际采集目标保留，夹具切换为 Operator 生成的 SM |

先在两个仓库冻结 CR/status、默认值、名称与所有权、依赖和迁移契约，再由 Operator 功能项 #86 实现并验收、发布项 #87 交付可安装基线，随后平台 #28 接入；#29/#30 按各自依赖推进。PR #31 不应凭旧验收直接视为本修订的交付；本轮只更新 tracker 与文档，不修改该 PR 或产品代码。

### 已发布任务与原生直接依赖

| 仓库 / Issue | 交付范围 | 直接阻塞于 |
| --- | --- | --- |
| [monitor-mgr#25](https://github.com/fjsxy/monitor-mgr/issues/25) | 实例创建身份与管理员实例写保护 | 无 |
| [monitor-mgr#26](https://github.com/fjsxy/monitor-mgr/issues/26) | 历史实例创建用户补齐与管理权限确认 | monitor-mgr#25 |
| [monitor-mgr#27](https://github.com/fjsxy/monitor-mgr/issues/27) | 平台 CR 操作恢复与旧 ServiceMonitor 任务兼容 | 无 |
| [monitor-in-hand#86](https://github.com/fjsxy/monitor-in-hand/issues/86) | KSM 可选配套 ServiceMonitor 的声明、调谐与生命周期 | 无 |
| [monitor-in-hand#87](https://github.com/fjsxy/monitor-in-hand/issues/87) | 发布配套 ServiceMonitor 的 Helm 与镜像集成基线 | monitor-in-hand#86 |
| [monitor-mgr#28](https://github.com/fjsxy/monitor-mgr/issues/28) | exporter 采集意图发布与 Operator 配套配置观察 | monitor-mgr#25、monitor-mgr#27、monitor-in-hand#87 |
| [monitor-mgr#29](https://github.com/fjsxy/monitor-mgr/issues/29) | 历史 exporter 显式启用与旧 ServiceMonitor 迁移 | monitor-mgr#26、monitor-mgr#28 |
| [monitor-mgr#30](https://github.com/fjsxy/monitor-mgr/issues/30) | Agent 关联 Operator 配套 ServiceMonitor 与实际采集 | monitor-mgr#26、monitor-mgr#28 |

8 项任务均关联父规格 monitor-mgr#23，共 9 条原生阻塞边；唯一跨仓库直接边为 monitor-in-hand#87→monitor-mgr#28。正文与原生关系同步，#29/#30 通过 #28 传递依赖；不存在 Operator 等待平台接入的循环。#25–#30 保留现有 assignee，#86/#87 未领取，全部保持 OPEN 和 ready-for-agent。

## 后续实施必须覆盖的验收

- Operator：无 ServiceMonitor CRD 的关闭模式启动；开启模式的依赖缺失与恢复；参数与保留标签校验；三种部署模式、长名称、多实例、正确端口；当前 CR UID 的 ownerReference；冲突不接管；自动修复；挂起/停用/关闭采集/恢复/删除/finalizer；重启与依赖故障后继续收敛。
- 平台：草稿零写入；新版本仅写 exporter CR；有 SM 读权限、无 SM 写权限仍可完成新流程；平台阻止向不支持新契约的旧 Operator 发布采集配置；创建归属和权限不回退；状态、实际配置与故障只读回显；原 CR UID 未确认时禁认领；历史任务与迁移不盲写。
- Agent 与真实数据面：按稳定 user 标签及 namespace 动态关联 Operator 生成的 SM，证明实际样本和 remote_write 接收/查询；平台或 Operator Ready、selector 匹配及配置生成均不足以替代。
- 隔离与清理：不修改管理员独立声明的四类采集配置、外部 Agent 或其他实例；旧 SM 不残留重复采集；确认逐例及全局清理。
- Go UT/E2E 使用 Ginkgo，UT 覆盖率至少 70%。实施前分别遵守各仓库 Issue 领取及五列用例发布、回读要求；计划、实际结果和 testedCommit 分别记录。本轮只有规格核对和文档检查，没有执行产品测试。
