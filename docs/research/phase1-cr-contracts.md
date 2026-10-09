# 一期 CR 配置与状态集成契约研究

研究日期：2026-10-10（Asia/Shanghai）。任务：[核验一期 CR 配置与状态集成契约](https://github.com/fjsxy/monitor-mgr/issues/2)，所属地图：[厘清可观测管控平台一期的产品与技术决策](https://github.com/fjsxy/monitor-mgr/issues/1)。

本研究回答平台可以依赖什么 CR 契约；只核查源码、CRD、测试脚本和对应版本的官方来源。没有运行集群、读取实际 kubeconfig/Secret、执行预检、验证测试通过或实施平台。下文“已验证”指来源核查，不表示环境已部署或功能已交付。

## 结论

1. `KubeStateMetrics` 使用 `monitoring.fjsxy.github.io/v1alpha1`，是 cluster-scoped；`PrometheusAgent` 使用 `monitoring.coreos.com/v1alpha1`，是 namespaced。两者必须分别处理资源身份、字段和状态，不能共用一套“namespace/name + Ready”模型。[KSM CRD][m-crd]、[Agent CRD][p-crd]
2. 已观察到的测试依赖是 Prometheus Operator `v0.83.0`、Prometheus `v2.55.1`。该 Operator 的 Agent status 可用，普通 StatefulSet 模式无需为 status 开启 feature gate。这是候选兼容性基线，不替用户确定平台一期最终测试矩阵。[测试版本][m-versions]、[Agent status 写入][p-agent-status]、[controller 前提][p-main]
3. 当前 generation 已被观察、调谐成功、workload 可用、数据面健康是不同事实。KSM 挂起或 namespace 零匹配可 `Ready=True` 且 `Available=False`；Agent `replicas=0` 时也可能 `Available=True`，但没有运行 Pod。[KSM 挂起][m-suspend]、[零匹配][m-zero]、[Agent 状态计算][p-reporter]
4. CR status 不记录平台发布版本、草稿、平台所有权、与发布版本的配置漂移或远程写入成功率。平台需要保存发布依据、回读 metadata/spec，并将自己的操作状态与原始 CR status 分开展示。此结论是由现有 status 字段和写入路径推得。[KSM status 类型][m-types-status]、[Agent status schema][p-crd-status]、[KSM status 写入][m-success]、[Agent status 写入][p-agent-status]

## 来源固定与本地变更边界

| 来源 | 本次固定状态 | 使用方式 |
| --- | --- | --- |
| `monitor-op` 本地目录 | `/Users/sunxuanyi/code/monitor-op`；HEAD `4d339211f1e0bc9e2a6d913c3bd21691bf17b190`；对应远端仓库 `fjsxy/monitor-in-hand` | 用 `git rev-parse HEAD`、`git status --short`、限定路径的 `git diff` 核查；GitHub commit API 确认该 commit 存在。下文 `m-*` 链接全部固定到此 commit。 |
| Prometheus Operator | tag `v0.83.0` 对应 commit `5cf2f5d37906d64b2259a262e319df4c74639e31` | GitHub commit API 解析 tag 后读取官方源码和生成 CRD；下文 `p-*` 链接全部固定到此 commit，不使用 `main` 文档代替。 |
| 本研究分支 | `research/phase1-cr-contracts`，从 `monitor-mgr` 的 `6a0e3f36090e8dd32a47c161e741679d4a8ffb92` 建立 | 仅保存本文件，不修改产品、集群或任务状态。 |

`monitor-op` 不是干净工作区。本次观察到以下未提交内容：

```text
 M .sandcastle/implement-prompt.md
 M AGENTS.md
 D CONTEXT.md
 M README.md
 M docs/agents/domain.md
 M skills-lock.json
?? GLOSSARY.md
?? examples/kubestatemetrics/
```

其中领域文档从 `CONTEXT.md` 调整为 `GLOSSARY.md`，README 添加手工 YAML 链接，`examples/kubestatemetrics/` 尚未跟踪；这些本地内容不能用 HEAD 的公开链接冒充。为避免混淆，CR 契约依据采用已提交源码：对 15 个引用的类型、CRD、planner/controller、Agent 示例、fixture、脚本与 Makefile，逐文件比较工作树字节与 `git show HEAD:<path>`，全部一致。领域所有权用已提交的 [CONTEXT.md][m-context] 交叉核对；未提交的手工 YAML 不作为本研究证据。

术语和范围遵循主工作区已确认的 `GLOSSARY.md`、`docs/adr/0001-observability-integration-boundary.md`、`docs/adr/0002-publication-and-resource-ownership.md`：复用两个 CR 与 Operator；本地测试工具预装依赖；一期只关联已有采集配置 CR；草稿显式下发；平台只管理自己创建的 CR，同名冲突拒绝接管，外部修改显示配置漂移后由用户主动下发。这些文件在读取时尚未进入本研究分支的基线，属于项目决策背景，不作为外部 CR 已实现能力的证据。

## 已验证事实：KubeStateMetrics

| 契约 | 准确字段与边界 | 来源 |
| --- | --- | --- |
| API 与实例身份 | group `monitoring.fjsxy.github.io`，version `v1alpha1`，plural `kubestatemetrics`，kind `KubeStateMetrics`，scope `Cluster`；CR 不设置 `metadata.namespace`。同一集群内由 `metadata.name` 定位实例，Operator 生成资源名称使用 `ksm-` 加名称派生的稳定 ID，所有权还检查 CR UID。 | [CRD][m-crd]、[名称生成][m-name]、[UID 所有权][m-owner] |
| 目标 namespace | `spec.targetNamespace` 必填，namespace 必须预先存在；Operator 不创建它。`status.appliedTargetNamespace` 一旦非空就禁止改为其他 namespace。这项不可变约束由 controller/planner 执行，不能把 API 接受更新等同于部署成功。 | [类型][m-types-spec]、[不可变检查][m-plan]、[首次写入前固定 namespace][m-checkpoint] |
| 默认拓扑 | `spec.deployment.mode=Deployment`，固定单副本，API 没有通用 `replicas` 配置。 | [部署类型][m-types-deploy]、[资源计划][m-plan-resources] |
| StatefulSet 分片 | `mode=StatefulSetSharded`，必须且只能携带 `statefulSetSharded` 分支，`shards>=2`；源码注明 experimental。 | [部署类型][m-types-deploy]、[union 校验][m-plan] |
| DaemonSet 分片 | `mode=DaemonSetSharded`，必须携带 `daemonSetSharded` 分支；按节点 Pod 采集、cluster collectors、unscheduled Pods 分工；collector 集合须包含 `pods` 和至少一个非 Pod collector；仅 1.28 profile 支持，源码注明 experimental。 | [部署类型][m-types-deploy]、[collector/版本限制][m-plan-qualification]、[资源构建][m-daemon] |
| 资源 requests/limits | 通用 `spec.podResources` 是 Kubernetes `ResourceRequirements`，省略时不注入 requests/limits。DaemonSet 分支的 `nodePodResources`、`clusterPodResources`、`unscheduledPodResources` 分别覆盖通用值；非空 `claims` 被 planner 拒绝。这里是资源声明，不是实际 CPU/内存利用率。 | [类型][m-types-deploy]、[通用字段][m-types-spec]、[claims 校验][m-plan]、[覆盖行为][m-daemon] |
| collectors | `spec.collectors.mode=Default/Include`；Default 要求 `names` 为空，Include 要求非空、无重复且来自共同 catalog；解析结果报告于 `status.effectiveCollectors`。 | [collector 解析][m-collectors]、[status 写入][m-success] |
| 指标过滤 | `spec.metricFilter.mode=All/Allow/Deny`；All 要求 `patterns` 为空；Allow/Deny 要求非空、各项为合法 Go regexp 且不能含逗号。pattern 不额外锚定，planner 保留其匹配语义后生成 allowlist/denylist 参数。 | [metric filter 解析][m-metric] |
| namespace 过滤 | `spec.namespaceFilter.mode=All/Include`；All 取集群 namespace 集合；Include 的 `names` 与 `namePatterns` 至少一项非空，literal 与 regexp 匹配结果合并去重，regexp 被锚定为完整 namespace 名匹配。`effectiveNamespaceCount` 是下发 literal 数，不是成功产出指标的 namespace 数。 | [namespace 解析][m-namespaces]、[类型][m-types-status] |
| 生命周期 | `enabled` 默认 true；false 删除直接受管资源但保留 CR。`suspended` 默认 false；true 停止 workload，保留 Service、ServiceAccount、RBAC，与删除 CR 或停用不同。 | [Spec][m-types-spec]、[停用状态][m-disable]、[挂起状态][m-suspend] |
| 版本/profile | Kubernetes 1.19.x → KSM 2.3.0；1.28.x → KSM 2.11.0；其他 minor 为 `UnsupportedKubernetesVersion`。DaemonSetSharded 在 1.19 为 `UnsupportedMode`。 | [profile][m-profile]、[模式资格][m-plan-qualification] |
| 拓扑切换 | 先删除旧 workload 并确认消失，再调整 Service/RBAC、创建新 workload；存在短暂采集空窗，状态有 `SwitchingDeploymentMode`。 | [controller 顺序][m-transition]、[切换期状态][m-transition-status] |

KSM status 包含：`observedGeneration`、`phase`、`conditions`、`managedResources`、`appliedTargetNamespace`、`kubeStateMetricsVersion`、`deploymentMode`、`effectiveCollectors`、`desiredPods`、`availablePods`、`effectiveNamespaceCount`。[status 类型][m-types-status]

`phase` 取 `Pending/Deleting/Disabling/Disabled/Reconciling/Ready/Degraded`，由 conditions 与删除状态推导。condition 使用 Kubernetes `metav1.Condition`，status 为 `True/False/Unknown`，另有 `reason/message/observedGeneration/lastTransitionTime`。[phase 推导][m-lifecycle]、[condition schema][m-condition]

`observedGeneration` 表示 condition 或 status 是依据哪一代 CR 设置的，**相等不代表成功**：配置不合法时 controller 同样写入当前 generation，同时报告 `Ready=False`、`Degraded=True`；已应用旧 workload 仍可能 `Available=True`。观测读取失败时，还可能保留旧 Pod 数并通过 `LastObservedWorkloadAvailable/ObservationFailed` 说明。[错误状态][m-problem]、[观测失败状态][m-observation]

## 已验证事实：PrometheusAgent 与采集配置 CR

| 契约 | 对应 v0.83.0 的事实 | 来源 |
| --- | --- | --- |
| API 与身份 | `monitoring.coreos.com/v1alpha1`、kind `PrometheusAgent`、plural `prometheusagents`、scope `Namespaced`；由 namespace/name 定位，删除重建应区分 UID。status 和 scale 均为 subresource。 | [Agent CRD][p-crd]、[subresources][p-crd-subresource] |
| 生命周期责任 | 原生 Agent 与采集配置由 Prometheus Operator 处理，`monitor-op` 不包装或拥有其生命周期；平台下发 CR，避免直接更新 Operator 生成的 workload。 | [已提交领域契约][m-context]、[Agent 调谐][p-agent-workload] |
| 资源配置 | `spec.resources` 声明 `prometheus` 容器的 requests/limits；不表达实时用量，也不等于 config-reloader/整个 Pod 的统一资源设置。 | [上游字段][p-resources] |
| Agent 拓扑 | 上游有 `replicas` 与 `shards`。`monitor-op` 现有预检仅支持 replicas 省略/1，0 表示全部 shards 缩零，shards 至少 1；replicas>1 是上游能力，但不通过该项目一期预检。 | [现有预检][m-agent-topology] |
| pause 与缩零 | `spec.paused` 停止普通调谐，不能据此认定已有 Pods 已停止；`replicas=0` 才是现有预检定义的缩零挂起。 | [Agent pause 路径][p-agent-pause]、[现有预检][m-agent-topology] |
| status 基础 | Agent 类型直接使用 `monitoringv1.PrometheusStatus`；字段为 `paused/replicas/updatedReplicas/availableReplicas/unavailableReplicas/conditions/shardStatuses/shards/selector`，没有 KSM 的顶层 `phase/observedGeneration`。generation 从各个 condition 读取。 | [Agent 类型][p-agent-types]、[status schema][p-crd-status] |
| status 写入前提 | Agent controller 直接处理并 `ApplyStatus`；启动时检查 Agent get/list/watch 与 status/update 权限，无 status feature gate 前置条件。CRD存在但 controller、权限或 watch scope 不满足时，不能保证得到有效 status。 | [status 路径][p-agent-status]、[启动前提][p-main]、[项目权限预检][m-agent-rbac] |
| feature gate | `PrometheusAgentDaemonSet` 默认 false，控制 Agent DaemonSet 模式；`StatusForConfigurationResources` 默认 false，声明用于配置资源 status，不控制 `PrometheusAgent.status`。不能由存在该 gate 推断当前版本四类采集配置都可提供完整关联/拒绝状态。 | [gate 定义][p-gates]、[默认值][p-config]、[Agent 状态路径][p-agent-status] |
| conditions | `Reconciled` 为 `True/False/Unknown`；`Available` 还允许 `Degraded` 表示部分可用。这是上游 condition 的 **status 值**，不能按 KSM 的 `Degraded` condition 类型解释。 | [Condition 定义][p-condition] |
| 零副本状态 | `StatusReporter` 将 ready Pods 数达到期望副本数视为满足，期望为 0 时也可 `Available=True`；展示“运行中”须同时考虑 replicas/availableReplicas 和用户期望。 | [状态计算][p-reporter] |

### 四类关联的字段映射

| 采集配置 CR | API version | Agent object selector | Agent namespace selector |
| --- | --- | --- | --- |
| `ServiceMonitor` | `monitoring.coreos.com/v1` | `spec.serviceMonitorSelector` | `spec.serviceMonitorNamespaceSelector` |
| `PodMonitor` | `monitoring.coreos.com/v1` | `spec.podMonitorSelector` | `spec.podMonitorNamespaceSelector` |
| `Probe` | `monitoring.coreos.com/v1` | `spec.probeSelector` | `spec.probeNamespaceSelector` |
| `ScrapeConfig` | `monitoring.coreos.com/v1alpha1` | `spec.scrapeConfigSelector` | `spec.scrapeConfigNamespaceSelector` |

API versions 来自 [现有环境检查][m-crd-verify]；字段与下列 selector 语义来自 [上游 Spec][p-selectors] 和 [实际选择实现][p-selection]：

- object selector 为 nil/省略时不选择该类；`{}` 选择可见 namespace 范围内全部该类对象；非空时按对象 labels 匹配。
- namespace selector 为 nil/省略时只看 Agent 所在 namespace；`{}` 匹配 Operator 可见范围内全部 namespace；非空时按 namespace labels 匹配。四类各自独立。
- `monitor-op` 现有预检进一步拒绝空 object selector，接受明确非空 selector，也接受四类全部 nil 的零配置。这是项目支持子集，不是上游禁止 `{}`。[预检实现][m-agent-selector]
- selector 选择采集配置 CR，不直接决定最终采集目标。被选中的配置还会接受配置校验，非法对象可能被拒绝并产生 `InvalidConfiguration` Event；Agent status 不提供完整的选中/拒绝清单。[上游选择与拒绝][p-selection]、[现有排障契约][m-agent-readme]
- Agent 的关联 API 是 LabelSelector，不是 CR name 列表。若页面按名称选择既有 CR，必须明确如何转换为已有 labels 的 selector，并回读 selector 实际覆盖的集合；不能在“只关联”范围内偷偷修改采集配置 labels。这是从字段与已确认范围推得的设计约束。[上游字段][p-selectors]

### remoteWrite 的映射边界

`spec.remoteWrite[]` 使用上游 `RemoteWriteSpec`，有 `url`、可选 `name/messageVersion/remoteTimeout/queueConfig/writeRelabelConfigs/tlsConfig` 等，并支持引用凭据的认证配置；并非只有一个 URL。多个认证方式存在互斥校验；Agent 自身凭据引用解析在 Agent namespace。[生成 CRD 的 remoteWrite schema][p-crd-remote]、[认证校验][p-remote-validation]、[Agent 资源加载][p-agent-selection]

本地公开 Agent 示例使用 Prometheus `v2.55.1`、显式 `messageVersion: V1.0`、VictoriaMetrics HTTP 地址和稳定集群标识 `mih_cluster_id`；这些是示例与现有测试边界，不是平台必须支持所有认证/TLS/queue 参数的结论。[示例][m-agent-example]、[示例说明][m-agent-readme]

现有预检检查 remoteWrite 所需 schema 和 queue 字段，但不能证明外部端点能接收数据。预检的最小 Agent 是零副本，并仅验证 `Reconciled` 与生成 StatefulSet 的 UID 所有权。[schema 检查][m-remote-schema]、[零副本探针][m-probe]、[成功条件][m-probe-status]

## 已验证事实：安装与测试前提

1. 现有 kind fixture 固定 Kubernetes `1.19.16` 和 `1.28.15` 的 multi-arch node image；Prometheus Operator bundle 下载后校验 SHA-256，Operator 和 config-reloader 使用固定 digest。[版本文件][m-versions]、[安装脚本][m-install]
2. `test-env-up` 创建独立 kind 集群，安装 Prometheus Operator 和测试 workloads；**不会安装当前 monitor-op 工作树**。后者由 `e2e-operator-install` 构建/加载镜像并通过 Helm 安装；chart 携带 KSM CRD。[环境创建][m-env-up]、[Makefile][m-make]、[chart CRD][m-chart-crd]
3. 外部环境检查五类 CRD 的 served version/schema、Operator watch scope/RBAC、Agent runtime ServiceAccount/RBAC；验证持久化 fixture Agent 的当前 generation `Reconciled=True/Available=True` 和实际 StatefulSet 镜像。这是测试代码所要求的结果，本研究没有执行它。[验证脚本][m-crd-verify]、[状态验收][m-status-verify]
4. Agent runtime 权限与 Prometheus Operator 读取配置/Secret 的权限分属不同主体。现有示例的 runtime ServiceAccount、ClusterRoleBinding 由管理员提供，不随 Agent 删除；平台注册成功不能推导这些依赖都已存在。[示例 RBAC][m-runtime-rbac]、[示例边界][m-agent-readme]
5. 现有部署预检会创建并清理零副本 Agent 探针，不是纯只读能力检查。平台是否调用它、还是实现只读注册检查与下发后的状态确认，需要明确；不能在无副作用的注册检查中默认复用整段脚本。[探针创建][m-probe]、[探针清理][m-probe-cleanup]

这些事实支持复用现有本地测试工具，但不能据此声称 monitor-mgr 已具有 kind 管理、Operator 安装、注册检查或状态同步能力，也不决定最终验收要跑一个还是两个 Kubernetes minor。

## 建议：字段与状态映射

以下是基于事实的建议，尚未构成平台实现规格。

| 页面/后端含义 | KSM 数据依据 | Agent 数据依据 | 不可推断的内容 |
| --- | --- | --- | --- |
| 发布身份 | 平台发布记录 + CR name/UID | 平台发布记录 + CR namespace/name/UID | CR name 或 label 本身不能证明平台所有权。 |
| 本次配置是否被处理 | CR generation 对照顶层/condition observedGeneration，读取 Ready/Progressing/Degraded reason | CR generation 对照 `Reconciled.observedGeneration`，读取 status/reason/message | 相等不等于调谐成功；CR 被外部更新后，应先确认 spec 仍对应发布版本。 |
| 组件运行与可用性 | `Available`、desiredPods/availablePods、enabled/suspended、有效 namespace 数 | `Available` 原始 status、availableReplicas/replicas/shardStatuses、spec.replicas/paused | KSM Ready 或 Agent Available 不能独自映射为“运行中”。 |
| 失败原因 | 原始 conditions 与 phase | 原始 Reconciled/Available conditions | 不把缺失状态转成失败或健康；先表示尚无观测。 |
| 状态新鲜度 | 成功回读时间、读取错误、generation 对照 | 成功回读时间、读取错误、各 condition generation | `lastTransitionTime` 不是平台最后成功观测时间；generation 没变化时仍可能已失联。 |
| 配置漂移 | 发布版本映射结果与当前 CR spec 比较 | 发布版本映射结果与当前 CR spec 比较 | status 不报告平台配置漂移，也不替代 spec 回读。 |
| 数据面健康 | 一期不从 CR status 推导 | 一期不从 CR status 推导 | 目标可抓取、remote_write 可发送、接收端可查询样本。 |

建议保留 CR status 原始快照用于排障，再生成页面摘要；将“草稿保存成功”“Kubernetes API 接受下发”“Operator 处理结果”“组件运行状态”分别记录。原因是它们由不同系统产生，失败和重试边界不同，现有 status schema 无法把它们合并为一项成功。[KSM status][m-types-status]、[Agent status][p-crd-status]

KSM 自身会对直接受管资源检查完整 ownerReference 与 CR UID，拒绝同名非受管资源；普通调谐会恢复受管 workload 的声明字段。**这保护的是 CR 到其子资源的归属，不是平台到 CR 的归属。** 平台下发时仍需自己的 CR 身份检查；人工修改 CR spec 会成为 Operator 当前声明，不能依靠 Operator 替平台检测或回滚配置漂移。[冲突检查][m-conflict]、[所有权比较][m-owner]、[资源更新][m-apply]

## 仍需用户决定

- 最终本地验收矩阵：复用 1.19/1.28 两条现有 fixture，还是首版选一个 minor；若开放 DaemonSetSharded，应怎样呈现 1.28-only 与 experimental 资格。
- 一期页面开放的准确字段子集：KSM 三种拓扑、各过滤项和生命周期操作；Agent shards、replicas、pause、WAL/存储、remoteWrite 认证/TLS/queue 等哪些进入首版。原生 API 有字段不等于必须开放全部字段。
- 已有采集配置 CR 的关联方式：输入 LabelSelector 或选择已有对象后从既有 labels 生成 selector；集合漂移、对象删除或标签变更如何反馈。
- CR 资源身份与发布边界：平台所有权标记、UID 保存、并发更新控制、重试、删除/重建和多组件部分下发失败规则。已确认“不接管已有同名 CR”“外部修改后不自动覆盖”，具体机制仍需决策。
- 状态展示与同步契约：缺失/过期/失联的判定、轮询或 watch、保存原始 status 与摘要的方式，以及页面怎样区分处理结果、运行情况、部分可用和旧 workload 仍可用。
- 注册和下发的能力检查：只读权限/API 检查覆盖哪些依赖；预检探针这种有副作用的验证是否纳入明确的测试或下发流程。平台账号角色与集群凭据权限不能互相替代，KSM 的 cluster scope 尤其需要后端按已注册集群执行授权。

## 固定原始来源

所有链接中的 commit 均为本次核查固定值。行号指 GitHub 原始文件行号，不是网页解析器行号。

[m-crd]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/config/crd/bases/monitoring.fjsxy.github.io_kubestatemetrics.yaml#L7-L29
[m-types-deploy]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/api/v1alpha1/kubestatemetrics_types.go#L97-L135
[m-types-spec]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/api/v1alpha1/kubestatemetrics_types.go#L137-L178
[m-types-status]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/api/v1alpha1/kubestatemetrics_types.go#L189-L218
[m-condition]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/config/crd/bases/monitoring.fjsxy.github.io_kubestatemetrics.yaml#L401-L459
[m-plan]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/kubestatemetrics/planner.go#L105-L172
[m-plan-qualification]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/kubestatemetrics/planner.go#L174-L204
[m-plan-resources]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/kubestatemetrics/planner.go#L207-L241
[m-namespaces]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/kubestatemetrics/planner.go#L245-L293
[m-metric]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/kubestatemetrics/planner.go#L304-L350
[m-collectors]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/kubestatemetrics/planner.go#L352-L377
[m-name]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/kubestatemetrics/planner.go#L397-L461
[m-profile]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/kubestatemetrics/planner.go#L402-L438
[m-daemon]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/kubestatemetrics/planner.go#L592-L667
[m-checkpoint]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/controller/kubestatemetrics_controller.go#L181-L185
[m-transition]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/controller/kubestatemetrics_controller.go#L147-L191
[m-transition-status]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/controller/kubestatemetrics_controller.go#L797-L847
[m-conflict]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/controller/kubestatemetrics_controller.go#L690-L711
[m-apply]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/controller/kubestatemetrics_controller.go#L713-L786
[m-owner]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/controller/kubestatemetrics_controller.go#L788-L795
[m-disable]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/controller/kubestatemetrics_controller.go#L931-L945
[m-suspend]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/controller/kubestatemetrics_controller.go#L947-L974
[m-problem]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/controller/kubestatemetrics_controller.go#L992-L1032
[m-observation]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/controller/kubestatemetrics_controller.go#L829-L847
[m-success]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/controller/kubestatemetrics_controller.go#L1070-L1105
[m-zero]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/controller/kubestatemetrics_controller.go#L1108-L1126
[m-lifecycle]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/internal/kubestatemetrics/lifecycle.go#L21-L44
[m-context]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/CONTEXT.md#L55-L89
[m-versions]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/test/fixtures/testenv/versions.env#L1-L15
[m-agent-example]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/examples/prometheus-agent/agent.yaml#L1-L43
[m-agent-readme]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/examples/prometheus-agent/README.md#L19-L72
[m-agent-topology]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/hack/preflight.sh#L210-L236
[m-agent-selector]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/hack/preflight.sh#L238-L257
[m-agent-rbac]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/hack/preflight.sh#L1172-L1180
[m-remote-schema]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/hack/preflight.sh#L997-L1009
[m-probe]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/hack/preflight.sh#L1284-L1295
[m-probe-status]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/hack/preflight.sh#L1393-L1404
[m-probe-cleanup]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/hack/preflight.sh#L1213-L1274
[m-install]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/hack/test-env.sh#L75-L108
[m-env-up]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/hack/test-env.sh#L177-L205
[m-crd-verify]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/hack/test-env.sh#L280-L338
[m-status-verify]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/hack/test-env.sh#L346-L360
[m-make]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/Makefile#L155-L172
[m-chart-crd]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/charts/monitor-in-hand/crds/monitoring.fjsxy.github.io_kubestatemetrics.yaml#L7-L29
[m-runtime-rbac]: https://github.com/fjsxy/monitor-in-hand/blob/4d339211f1e0bc9e2a6d913c3bd21691bf17b190/examples/prometheus-agent/runtime-rbac.yaml#L1-L38
[p-crd]: https://github.com/prometheus-operator/prometheus-operator/blob/5cf2f5d37906d64b2259a262e319df4c74639e31/example/prometheus-operator-crd/monitoring.coreos.com_prometheusagents.yaml#L1-L49
[p-crd-remote]: https://github.com/prometheus-operator/prometheus-operator/blob/5cf2f5d37906d64b2259a262e319df4c74639e31/example/prometheus-operator-crd/monitoring.coreos.com_prometheusagents.yaml#L5144-L6268
[p-crd-status]: https://github.com/prometheus-operator/prometheus-operator/blob/5cf2f5d37906d64b2259a262e319df4c74639e31/example/prometheus-operator-crd/monitoring.coreos.com_prometheusagents.yaml#L10745-L10878
[p-crd-subresource]: https://github.com/prometheus-operator/prometheus-operator/blob/5cf2f5d37906d64b2259a262e319df4c74639e31/example/prometheus-operator-crd/monitoring.coreos.com_prometheusagents.yaml#L10882-L10889
[p-agent-types]: https://github.com/prometheus-operator/prometheus-operator/blob/5cf2f5d37906d64b2259a262e319df4c74639e31/pkg/apis/monitoring/v1alpha1/prometheusagent_types.go#L47-L93
[p-agent-status]: https://github.com/prometheus-operator/prometheus-operator/blob/5cf2f5d37906d64b2259a262e319df4c74639e31/pkg/prometheus/agent/operator.go#L944-L982
[p-agent-pause]: https://github.com/prometheus-operator/prometheus-operator/blob/5cf2f5d37906d64b2259a262e319df4c74639e31/pkg/prometheus/agent/operator.go#L578-L587
[p-agent-workload]: https://github.com/prometheus-operator/prometheus-operator/blob/5cf2f5d37906d64b2259a262e319df4c74639e31/pkg/prometheus/agent/operator.go#L716-L798
[p-agent-selection]: https://github.com/prometheus-operator/prometheus-operator/blob/5cf2f5d37906d64b2259a262e319df4c74639e31/pkg/prometheus/agent/operator.go#L837-L867
[p-resources]: https://github.com/prometheus-operator/prometheus-operator/blob/5cf2f5d37906d64b2259a262e319df4c74639e31/pkg/apis/monitoring/v1/prometheus_types.go#L401-L444
[p-selectors]: https://github.com/prometheus-operator/prometheus-operator/blob/5cf2f5d37906d64b2259a262e319df4c74639e31/pkg/apis/monitoring/v1/prometheus_types.go#L128-L202
[p-selection]: https://github.com/prometheus-operator/prometheus-operator/blob/5cf2f5d37906d64b2259a262e319df4c74639e31/pkg/prometheus/resource_selector.go#L78-L145
[p-condition]: https://github.com/prometheus-operator/prometheus-operator/blob/5cf2f5d37906d64b2259a262e319df4c74639e31/pkg/apis/monitoring/v1/types.go#L228-L281
[p-reporter]: https://github.com/prometheus-operator/prometheus-operator/blob/5cf2f5d37906d64b2259a262e319df4c74639e31/pkg/prometheus/operator.go#L138-L249
[p-remote-validation]: https://github.com/prometheus-operator/prometheus-operator/blob/5cf2f5d37906d64b2259a262e319df4c74639e31/pkg/prometheus/operator.go#L85-L136
[p-gates]: https://github.com/prometheus-operator/prometheus-operator/blob/5cf2f5d37906d64b2259a262e319df4c74639e31/pkg/operator/feature_gates.go#L25-L37
[p-config]: https://github.com/prometheus-operator/prometheus-operator/blob/5cf2f5d37906d64b2259a262e319df4c74639e31/pkg/operator/config.go#L92-L110
[p-main]: https://github.com/prometheus-operator/prometheus-operator/blob/5cf2f5d37906d64b2259a262e319df4c74639e31/cmd/operator/main.go#L395-L455
