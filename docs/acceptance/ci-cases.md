# 一期 CI 用例与选取标准

由 `make ci-cases` 从源码、固定注册表、workflow 与 Makefile 生成；`make ci-cases-check` 校验一致性。修改用例或入口后必须重新生成。

<!-- CI 入口指纹：6e599f744cea094be5e12fc4646c4157383753fa1a5ac4c1072839ee12d69a6e -->

## 执行范围与准入标准

- `fast`：每次 main PR、main push 和手动运行执行 `make check`、`make check-generated`，随后执行 CI_FAST 的固定 58 个 Q 键。
- `smoke`：沿用 workflow 的可信来源限制；当前仅同仓 `codex/phase1-implementation` PR、main push，以及 main 或该实施分支的手动运行可以读取专用只读 deploy key。其他 PR 只跑 fast。
- Go UT 全量纳入，不按 profile 抽样；race 同次执行。手写产品包 `internal/control` 与 `internal/platform` 的合计语句覆盖率至少 70%；排除生成的 `kitex_gen`、命令参数入口与验收工具。
- Python 验收工具自测全部纳入：它们保护源码身份、用例选择、原始证据、敏感数据、独占 lane、停止恢复及清理，不能用产品用例替代。
- CI_FAST 保留冻结目录中的快速契约边界：真实 HTTP→Kitex→SQLite 加受控 TLS 目标，适合每次变更重复执行；不声明真实 Kubernetes 或浏览器验收。
- CI_SMOKE 保留固定 17 个键：在 Linux amd64 的单 control 集群以真实平台 Pod 验证核心链路，包含 KSM、Agent、PVC 与浏览器；不依赖 data 集群。
- LOCAL_FULL 固定 207 个键覆盖双集群、破坏性故障与长链路恢复，需要独占环境，未全部放进常规 CI。supplemental 不计入固定键数；其 Go/工具回归仍随全量 UT 执行。
- 键集沿用 `phase1.json` 与 `registry.py` 的冻结选择；新增关键风险先补用例，再按可重复性、所需拓扑、耗时和独占资源评估 profile，不能因某例易失败而删减。
- passing 必须同时满足同一 candidate SHA、逐 selector 实际执行且通过、必选输入齐全、原证据可核验、敏感数据不公开、清理成功；失败、跳过、环境失败、未执行、缺失或 dry-run 均不能替代通过。

## 各入口具体执行内容

| CI 入口 | 执行内容 | 通过标准 |
| --- | --- | --- |
| `make acceptance-plan` | 固定验收目录、需求和稳定子例追踪 | 元数据与固定矩阵一致；不计产品通过 |
| `make ci-cases-check` | 本文与当前源码、profile、workflow、Makefile 对照 | 完全一致 |
| `make acceptance-host-check` | 下表全部 Python 工具自测，含真实 Ginkgo child 的调度正负控 | 所有 unittest 通过；合成业务仅记 Host 自测 |
| `make test-race` | 下表全部 Go Ginkgo It/By、race 和覆盖率 | 无失败、无 race；覆盖率至少 70% |
| `make vet` / `make web-build` | Go 静态检查、TypeScript 类型检查及 Vite 构建 | 命令全部成功；不计浏览器通过 |
| `make check-generated` | 固定 Thrift/Kitex 生成 | `kitex_gen` 与提交一致 |
| `run.py run --profile CI_FAST` | 下表 58 Q；由原生 Ginkgo 报告核验根与完成子例 | 逐键独立收据和原事件全通过 |
| `run.py run --profile CI_SMOKE` | 下表 17 K/B；Ginkgo 每个 It 调度一个原 Python/Playwright 场景 | 原业务断言、Ginkgo 状态、原收据、Pod/镜像身份、浏览器事件及清理全部通过 |
| validate / export / recover | 独立核验、白名单导出、按原 UID 顺序清理 | 核验和导出成功；always 清理有独立成功收据 |

## Go UT 与快速场景：115 个 It（含 1 个显式启用的诊断夹具）

原 `Test...` 字符串作为稳定 selector 保留；只有 suite 入口仍使用 `testing.T`。参数与连续步骤由 Ginkgo `By` 执行，只有完成全部步骤后才记录子例完成标识。共享状态的步骤保持顺序，按子例选择 Q 时运行其所属完整 It。

| 稳定 selector | 源码 | 验证目的 | CI 选择标准 |
| --- | --- | --- | --- |
| TestUnknownFieldGuardContainersAndNil | internal/control/unknown_fields_test.go | 使用生成类型真实保留的未知 wire 字段，验证递归 guard 的容器边界及错误路径。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentOperatorCapabilityBoundaries | internal/platform/agent_dependency_test.go | CAP补充回归通过HTTP公开预览边界验证，缺启动能力、单-范围、override及旧RS均不能误放行。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentDependencyErrorClassification | internal/platform/agent_dependency_test.go | A13安全错误必须区分不存在、禁止访问与缺key，不把所有依赖问题揉成CONFLICT。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentIndependentSourceFreshness | internal/platform/agent_dependency_test.go | R07公开读模型：CR持续成功也不能掩盖PVC/关联读取失败或刷新其最后成功时间。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentDraftSourceIsolation | internal/platform/agent_dependency_test.go | 草稿匹配与实际CR运行来源属于不同读模型；冲突预览也不能写入运行缓存。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentRevisionNameBudgetHTTP | internal/platform/agent_name_test.go | REG21-AGENT-NAME-001：创建/保存/预览经真实HTTP→Kitex，固定Operator派生标签由同版Kubernetes校验。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentRevisionNameBudgetLegacyAndWorker | internal/platform/agent_name_test.go | 旧库只读与已排队worker是独立入口：保留原名称/版本，但任何新派发都不能绕过预算。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestF01Agent | internal/platform/agent_protocol_test.go | F01每个子例经过锁定FastCodec及实际generated client→Kitex，不用JSON模拟Thrift往返。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestF03Agent | internal/platform/agent_protocol_test.go | F03通过HTTP与直接RPC验证不同非法输入，拒绝发生在任何CR写入前。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentDurationRange | internal/platform/agent_protocol_test.go | REG18-VALID-006：CRD格式之外还需校验Prometheus duration可表示范围。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentFixedCapacityRevalidated | internal/platform/agent_safety_test.go | REG18-FIXED-008：首次受理后保存新容量，成功回读后必须在预览和确认再次拒绝。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentFixedCapacityPossiblySent | internal/platform/agent_safety_test.go | 可能已发但尚无UID时也锁定版本容量，不能等到首次成功回读后才建立约束。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentHeadersReadProjection | internal/platform/agent_safety_test.go | REG18-HEADERS-009：合法Agent remoteWrite.headers任意值必须投影脱敏，原值仍参与漂移指纹。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentSelectorExpressionProjection | internal/platform/agent_selector_projection_test.go | SELECTOR012沿HTTP→真实Kitex→SQLite→worker验证CR裁剪；typed空集合与外层selector语义分别断言。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentStoragePendingDeleteBlocksRestore | internal/platform/agent_storage_restore_test.go | REG20-STORAGE-005：只经公开入口产生Delete责任，原UID未独立核销前不允许重新挂载。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentStorageDeletePreviewRequiresAdoptedPolicy | internal/platform/agent_storage_test.go | P14.policy-pending：真实HTTP/Kitex读取实际ST采用状态，不能仅凭CR策略打开删除预览。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentStorageIncompleteListRemainsUnknown | internal/platform/agent_storage_test.go | 不完整的辅助列表必须保存读取错误，不能把缺items当成已经没有卷。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentStorageEmptyDirDoesNotRequirePVCClass | internal/platform/agent_storage_test.go | A06：首次emptyDir不依赖StorageClass，也不能把PVC模板或保留策略写入CR。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentStorageNewInstanceRejectsOrphanClaim | internal/platform/agent_storage_test.go | REG20-STORAGE-001.orphan：孤卷没有ownerRef也不能被新Agent确定名称自动挂载。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestR03AgentZeroUsesApplicableStatus | internal/platform/agent_storage_test.go | R03.agent-zero：适用当前generation的零副本status可合法省略计数字段，不能推断仍在运行。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentStorageRetainedIdentitySurvivesParents | internal/platform/agent_storage_test.go | 已确认原UID卷在父资源消失后仍单独读取；同名新UID不能取代历史身份。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentStorageDangerRequiresAdoptedPolicy | internal/platform/agent_storage_test.go | A02/P14：开放类型化多shard和挂起，但危险预览必须读取每个相关ST的实际策略。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentStorageResponsibilityOutlivesInstance | internal/platform/agent_storage_test.go | REG20-STORAGE-002：实例完成与逐卷任务独立；未知/Delete待核验保留凭据责任，合法Retain不阻塞集群变更。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentStoragePreviewImpact | internal/platform/agent_storage_test.go | REG20-STORAGE-003：每种影响事实独立注入，ST新UID负例保持安全guard仍可通过。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentStorageIndependentOwnerPolicy | internal/platform/agent_storage_test.go | REG20-STORAGE-004：ST ownerRef在保留ST的挂起中合法，删ST时却明确受whenDeleted=Delete控制。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentTypedDraftIdentityAndDefaults | internal/platform/agent_test.go | A03/F01：保存typed Agent只创建平台草稿，跨namespace同名保持独立。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentDefaultPublicationWithDependencies | internal/platform/agent_test.go | A01/A04/F01：默认Agent发布仍经过同一真实Kitex/版本/attempt/worker链，四selector省略不能丢remoteWrite。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestR03AgentPausedReadOnly | internal/platform/agent_test.go | R03.agent-paused：外部paused仅停止调谐，已有Pod不能被宣称停止。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAgentMalformedURLReadProjection | internal/platform/agent_url_projection_test.go | PROJECTION013经真实HTTP→生成Kitex→SQLite及TLS目标检验失败关闭；DB检查属于已批准的秘密不落盘边界。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestC01PersistentClusterRegistrationThroughRPC | internal/platform/clusters_test.go | 通过真实 RPC 注册集群，再核验数据库中的注册结果。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestC03UnverifiedIdentityCannotRegister | internal/platform/clusters_test.go | 逐项构造身份核验失败，确认集群不能被注册。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestC09RejectSelectedContextDependenciesBeforeNetworking | internal/platform/clusters_test.go | 逐项构造不支持的 kubeconfig 依赖，确认联网前拒绝。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestT07TargetOfflineDoesNotBreakPlatformReadiness | internal/platform/clusters_test.go | 使目标集群离线，确认平台自身仍可就绪。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestClusterCredentialLifecycleAndImmediateOwnership | internal/platform/clusters_test.go | 替换集群凭据与归属，核验权限立即生效。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestClusterSecretsRecoverOnlyWithOriginalKeyAndPreserveSession | internal/platform/clusters_test.go | 重启并替换加密密钥，核验凭据恢复限制及会话保留。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestClusterUnfinishedChangesBlockTransferAndUnregisterAcrossKinds | internal/platform/clusters_test.go | 构造未完成操作，阻止跨组件的归属转移和注销。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestClusterMetadataReadFailureCannotBecomeEmptyList | internal/platform/clusters_test.go | 注入元数据读取失败，确认不会将失败转换为空列表。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestClusterPendingCredentialSurvivesRestartWithoutAutomaticReplay | internal/platform/clusters_test.go | 重启待确认的凭据变更，核验状态保留且写请求不自动重放。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestCredentialContinueRejectsOtherOperationKindsWithoutChangingActor | internal/platform/clusters_test.go | 尝试继续其他类型操作，确认被拒绝且发起者不变。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestReferenceMetadataRequiresCompletePagesAndNeverReturnsSecretValues | internal/platform/clusters_test.go | 验证引用资源分页完整性，并确认 Secret 值不被返回。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestClusterCanRegisterWithRestrictedComponentCapabilities | internal/platform/clusters_test.go | 使用受限组件权限注册，核验平台准确记录能力边界。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestSharedTargetSensitiveReadProjection | internal/platform/component_seed_test.go | REG18-SHARED-003：从外部API返回的秘密也不能进入平台发现/YAML读模型。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestSharedSchema4PreviewSurvivesUpgrade | internal/platform/component_seed_test.go | REG18-SHARED-001：真实v4旧库预览必须升级成安全指纹，且原previewId仍能发布。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestSharedHMACDetectsHiddenConfigAndPreservesMetadata | internal/platform/component_seed_test.go | REG18-SHARED-003：隐藏秘密仍须参与配置变化判定；仅RV/status变化不能使预览过期。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestDeletionPreviewBindsTargetAndImpact | internal/platform/deletion_preview_test.go | REG17-DELETE-003：状态自然变化可继续，目标或原UID影响范围变化必须重新预览。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestDeletionPreviewSchema6UnsentCanContinue | internal/platform/deletion_preview_test.go | 旧v6未发删除升级后仍受保护，显式再确认只能沿原operation/UID/version建立新attempt。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestWorkbenchHTTPInt64RoundTrip | internal/platform/http_i64_test.go | REG19-HTTP-I64-001：HTTP不安全修订以字符串精确往返，真实RPC保留i64。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestK10DraftNameAndRevision | internal/platform/ksm_test.go | 创建并更新 KSM 草稿，核验名称与 revision 并发保护。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestF04EncryptedSchema3WrongKeyDoesNotMigrate | internal/platform/ksm_test.go | 用错误密钥打开旧加密数据库，确认拒绝迁移。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestP02PublicationFreezesSourceAndP03Idempotency | internal/platform/ksm_test.go | 发布草稿后继续编辑并重试，核验冻结版本与幂等。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestExternalKSMReadOnlyAndFailedList | internal/platform/ksm_test.go | 发现外部 KSM 并注入列表失败，核验只读与错误边界。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestP04DraftAndTargetPreviewRaces | internal/platform/ksm_test.go | 预览后分别修改草稿与目标，确认旧预览不能发布。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestP05DriftOverwritePreservesMetadataAndP06Equivalent | internal/platform/ksm_test.go | 覆盖配置漂移并比较等价配置，核验元数据保留。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestF05PublicationCrashBoundaries | internal/platform/ksm_test.go | 在事务提交前后模拟崩溃，核验发布任务恢复边界。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestF03KSMValidationThroughHTTPAndDirectRPC | internal/platform/ksm_test.go | 从 HTTP 和直接 RPC 分别提交非法 KSM 输入，核验一致拒绝。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestR03KSMEffectiveZeroValuesAndMissingStatus | internal/platform/ksm_test.go | 分别构造 KSM 零副本、停用和无匹配状态，核验运行摘要。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestF02KSMHTTPHasRealRPCAndCannotBypassUnavailableRPC | internal/platform/ksm_test.go | 关闭真实 RPC 传输，确认 HTTP 无法绕过 RPC 执行业务。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestPublicationPreflightDoesNotBlockCurrentAccountRevocation | internal/platform/ksm_test.go | 在发布预检期间撤销账号权限，核验授权不被阻塞。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestF04LifecycleSchema5Upgrade | internal/platform/lifecycle_migration_test.go | F04：精确v5旧库保留由HTTP/Kitex创建的密文、会话、固定版本、已成功历史和未发保护。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestP08CancellationCompetesWithPersistentDispatch | internal/platform/lifecycle_test.go | 控制取消与派发竞争，验证持久化边界只允许一个结果。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestP07RetryUsesFrozenVersionAndCurrentRevisions | internal/platform/lifecycle_test.go | P07：确定拒绝后，重试固定原版本且保留失败尝试，不能偷换为保存的新草稿。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestPublicationUnknownOutcomeNeverReplays | internal/platform/lifecycle_test.go | P10/P11：目标已执行但响应丢失，已知UID只读确认，首次创建绝不按同名补认。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestT08ShutdownRejectsNewAcceptanceBeforeDrain | internal/platform/lifecycle_test.go | T08：退出先撤销就绪和新受理资格，已发送任务仍保留真实结果核验依据。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestP14ForegroundDeletionKeepsUIDGraphProtection | internal/platform/lifecycle_test.go | 逐项构造删除残留与身份替换，核验前台删除的 UID 保护。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestI06CookieExpiryDoesNotCancelAcceptedOperation | internal/platform/lifecycle_test.go | I06：后台执行使用账号当前权限，原Cookie到期仅使新的用户请求失效。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestP13RestartUsesPersistentSentBoundary | internal/platform/lifecycle_test.go | P13：真实子进程在两个持久点退出，重启复用原卷/密钥/会话并禁止隐式重放。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestDeletionCrossNamespaceOwnerUIDRemainsProtected | internal/platform/lifecycle_test.go | REG17-DELETE-001：外部漂移到另一namespace后，父对象先消失的Pod仍属于原UID清理图。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestDeletionCompletionDoesNotChangeActivity | internal/platform/lifecycle_test.go | REG17-ACTIVITY-001：后台完成不是一次显式操作，不应重新置顶实例。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestShutdownClosesRPCAdmissionBeforeLauncherDrain | internal/platform/lifecycle_test.go | REG17-SHUTDOWN-001：即使launcher仍占锁，停机起点也必须拒绝真正的直接RPC变更。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestDeletion404AfterPreflightKeepsSeenPodProtected | internal/platform/lifecycle_test.go | REG17-DELETE-002：预检后外部删除原CR而DELETE返回404时，旧Pod仍须保留保护。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestI01InitializeOnlyEmptyDatabase | internal/platform/platform_test.go | 初始化空库，并拒绝再次初始化已有平台。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestI01TemporaryPasswordMustChangeAndRevokesSession | internal/platform/platform_test.go | 使用临时密码登录、修改密码并核验旧会话失效。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestI07ResetDisableRoleAndLastAdministrator | internal/platform/platform_test.go | 重置密码、禁用账号和变更角色，并保护最后一名管理员。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestI02AbsoluteExpiryAndPersistence | internal/platform/platform_test.go | 推进受控时钟并重启平台，验证会话绝对过期与持久化。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestI02OriginAndDirectRPCEnforceAuthorization | internal/platform/platform_test.go | 分别通过 HTTP Origin 与直接 RPC 验证授权边界。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestI07ConcurrentDemotionsRetainAdministrator | internal/platform/platform_test.go | 并发降级管理员，验证事务始终保留一名管理员。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestT07StartupFailureCleansListenersAndRejectsSecondWriter | internal/platform/platform_test.go | 注入启动失败并尝试第二个 writer，核验监听与锁的清理。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestUnknownAPIStaysJSONWhenServingFrontend | internal/platform/platform_test.go | 请求未知 API，验证静态页面服务不吞掉 JSON 错误。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestT07CanceledStartDoesNotCreateResources | internal/platform/platform_test.go | 取消启动上下文，确认平台不再创建资源。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestT07ProbeFailureOrInFlightCancellationReleasesResources | internal/platform/platform_test.go | 分别注入探针失败与启动中取消，检查资源释放。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestT07ActualRPCProbeRequiredBeforeReady | internal/platform/platform_test.go | 阻断真实 RPC 探针，确认平台不能提前就绪。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestI02HTTPSOriginSetsSecureCookie | internal/platform/platform_test.go | 使用 HTTPS Origin 登录，核验 Cookie 的 Secure 属性。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestAccountMutationsAreDurablyIdempotentAndStillAuthorize | internal/platform/platform_test.go | 重试账号写操作，验证持久化幂等与当前授权仍生效。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestSchemaOneMigrationPreservesSessionAndBacksUpBeforeDDL | internal/platform/platform_test.go | 迁移旧数据库，核验 DDL 前备份及原会话保留。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestDirectRPCMutationReplayDoesNotRepeatPasswordOrRoleChange | internal/platform/platform_test.go | 重放直接 RPC 写请求，确认密码与角色不会再次变更。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestT07DatabaseCorruptionNeverStartsReady | internal/platform/platform_test.go | 破坏数据库后启动平台，确认不会报告就绪。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestT07ReadyStopsWhenRealRPCTransportFails | internal/platform/platform_test.go | 中断真实 RPC 传输，确认就绪状态随之失败。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestPublicationYAMLFirstCreate | internal/platform/publication_yaml_test.go | 首次创建分别验证KSM和Agent，不把已存在CR的保留字段规则带入新对象。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestReviewCredentialReplayCurrent | internal/platform/review_contract_test.go | 注册/候选/人工继续都先持久受理；命中只能找回当前任务，不再触发核验。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestReviewCapabilitiesRoute | internal/platform/review_contract_test.go | 分别以管理员、所属用户及无权账号查询组件能力。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestReviewDeleteRoute | internal/platform/review_contract_test.go | 逐项验证删除接口的当前权限、输入与幂等边界。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestReviewContinueRoute | internal/platform/review_contract_test.go | 逐项验证继续操作接口的类型、状态与权限边界。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestReviewStorageScope | internal/platform/review_storage_test.go | 逐项验证存储删除影响范围与保留策略。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestR06WorkbenchSubmillisecondBudget | internal/platform/rpc_budget_test.go | R06的codec边界：不足1ms时在TTHeader编码前拒绝，不能发出0代表无限预算。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestF03KSMUnknownFieldsHTTPAndRPC | internal/platform/unknown_fields_test.go | F03 同时约束 HTTP 与真实 generated client→Kitex server，不把反序列化丢字段当作拒绝。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestWorkbenchBrowserFixture | internal/platform/workbench_browser_fixture_test.go | TestWorkbenchBrowserFixture 仅提供受控TLS目标上的真实Host应用诊断，不访问真实集群或计入Pod B。 | 显式 WORKBENCH_HOST_FIXTURE/STOP 才运行；普通 CI 跳过 |
| TestR06WorkbenchConcurrentBudgets | internal/platform/workbench_budget_test.go | R06：三个真实TLS边界同时阻塞，断言全局8/集群4及排队也计入8秒，不用私有limiter断言。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestR06WorkbenchServerBudgetMaximum | internal/platform/workbench_budget_test.go | R06：请求方缺失/异常/零或夸大metadata也不能把服务端总预算扩大到10秒以上。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestR05WorkbenchFailedInventoryPage | internal/platform/workbench_inventory_test.go | R05.page：第二页失败只标未知/历史，不能丢掉上次完整发现的对象或提交第一页为全集。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestR05WorkbenchIndependentItems | internal/platform/workbench_inventory_test.go | R05.item：同一后台批次中失败对象与成功对象各自记录，不整批覆盖。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestR01WorkbenchIntervals | internal/platform/workbench_poll_test.go | R01.intervals：服务端调度不依赖页面，默认10秒；公开启动配置精确限定1–20秒。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestR06WorkbenchCoalescesReads | internal/platform/workbench_poll_test.go | R06：并发手动读取同一真实目标只产生一个GET；每个HTTP调用仍独立鉴权。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestR06WorkbenchRemainingBudget | internal/platform/workbench_poll_test.go | R06：下游不得使用超过短调用方剩余预算的8秒新预算。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestWorkbenchQueryFiltersAndStatistics | internal/platform/workbench_query_test.go | U04的后端契约：完整授权集合先过滤/排序再分页，统计独立于过滤。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestWorkbenchQueryCurrentAuthorization | internal/platform/workbench_query_test.go | 查询工作台前核验当前账号与集群归属权限。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestR01WorkbenchSourceFreshness | internal/platform/workbench_sources_test.go | 推进观察时钟，验证工作台按来源计算新鲜度。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestR01WorkbenchEarlyError | internal/platform/workbench_sources_test.go | 在首次成功观察前注入错误，验证错误状态的展示。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestWorkbenchLateObservationAfterUnregister | internal/platform/workbench_sources_test.go | REG19-SOURCE-001：注销后的旧在途读取不能继续推进原来源。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestWorkbenchLateObservationAfterCredentialSwitch | internal/platform/workbench_sources_test.go | REG19-SOURCE-001：旧凭据读取失败不得覆盖新修订已经提交的成功观察。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestR03WorkbenchMissingStatus | internal/platform/workbench_sources_test.go | 构造缺失 status 的资源，核验运行摘要保持未知。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestR03WorkbenchIncompleteStatus | internal/platform/workbench_sources_test.go | 构造不完整 status，防止工作台误报运行正常。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestWorkbenchPodSourceIndependent | internal/platform/workbench_sources_test.go | R07.pod的来源隔离回归：CR成功不能续期读取失败的Pod来源。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestWorkbenchLateObservationIdentityRead | internal/platform/workbench_sources_test.go | REG19-SOURCE-001：后台集群身份预读失败也必须保留读取开始时的凭据范围。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestWorkbenchActivitySequencePersists | internal/platform/workbench_test.go | REG19-ACTIVITY-001：真实入口上的活动顺序独立于墙钟，并跨原目录重启保留。 | 全量 Go UT/race，不按业务 profile 删减 |
| TestVolumeProbeActualSQLite | scripts/acceptance/environmentfixture/main_test.go | Host 协议只验证真实文件和 SQLite，不代替集群 PVC 的运行证据。 | 全量 Go UT/race，不按业务 profile 删减 |

## 验收工具 Host 自测：254 项

以下全部由 `unittest discover -s scripts/acceptance -p "*test.py"` 收集。统一纳入的理由是保护验收结果的可信度；保持 Python 实现。

| 用例 selector | 验证目的 |
| --- | --- |
| agent_crd_restore_test.AgentCRDRestoreBoundary.test_deleted_original_crd_is_restored_once_with_its_new_response_uid | 真实 C04 case 恢复 CRD 并闭合 cleanup，旧 UID 历史不能冒充新创建响应。 |
| agent_crd_restore_test.AgentCRDRestoreBoundary.test_lost_create_response_keeps_new_uid_unconfirmed_and_cleanup_failed | API 创建生效但响应丢失时保留原 gap，不能从同名读取补认新 UID。 |
| agent_crd_restore_test.AgentCRDRestoreBoundary.test_restore_intent_persistence_failure_prevents_create_and_cleanup_success | 故障恢复的创建 intent 未持久化，不能发送 create 或让 cleanup 放行。 |
| agent_crd_restore_test.AgentCRDRestoreBoundary.test_restore_rejects_read_failure_and_same_name_replacement | 目标读取失败不是缺失，同名替代对象不能承接原故障恢复。 |
| agent_crd_restore_test.AgentCRDRestoreBoundary.test_restore_requires_one_original_fault_for_case_target_identity_and_uid | 缺原故障、身份不符或原故障不唯一时，caller 必须在创建前拒绝。 |
| agent_projection_test.ProjectionArtifactSafety.test_database_probe_fingerprints_blob_bytes_and_storage_class | 013验收工具的公开证据边界；纯fixture检查不计为产品Q或真实K通过。 |
| agent_projection_test.ProjectionArtifactSafety.test_hit_is_failed_but_public_record_and_file_are_safe | 013验收工具的公开证据边界；纯fixture检查不计为产品Q或真实K通过。 |
| agent_projection_test.ProjectionArtifactSafety.test_operator_branch_requires_actual_status_and_failure_is_safe | 013验收工具的公开证据边界；纯fixture检查不计为产品Q或真实K通过。 |
| agent_projection_test.ProjectionArtifactSafety.test_reason_branch_requires_exact_hidden_runtime_in_both_read_models | 013验收工具的公开证据边界；纯fixture检查不计为产品Q或真实K通过。 |
| bootstrap_linux_test.BootstrapProtocolTest.test_apt_policy_keeps_suite_candidate_and_excludes_url_credentials | 来源验证 seam：实际磁盘字节、archive 越界与失败时禁止安装。 |
| bootstrap_linux_test.BootstrapProtocolTest.test_download_mismatch_rejects_actual_bytes | 来源验证 seam：实际磁盘字节、archive 越界与失败时禁止安装。 |
| bootstrap_linux_test.BootstrapProtocolTest.test_main_early_failure_writes_public_diagnostic_before_any_download | 来源验证 seam：实际磁盘字节、archive 越界与失败时禁止安装。 |
| bootstrap_linux_test.BootstrapProtocolTest.test_main_projects_fixed_failure_fields_without_message_body_path_or_dynamic_class | 来源验证 seam：实际磁盘字节、archive 越界与失败时禁止安装。 |
| bootstrap_linux_test.BootstrapProtocolTest.test_main_records_real_child_exit_and_keeps_original_output_private | 来源验证 seam：实际磁盘字节、archive 越界与失败时禁止安装。 |
| bootstrap_linux_test.BootstrapProtocolTest.test_main_success_and_final_report_failure_never_turn_into_false_success | 来源验证 seam：实际磁盘字节、archive 越界与失败时禁止安装。 |
| bootstrap_linux_test.BootstrapProtocolTest.test_python_location_rejects_default_cache_and_changed_loaded_library_bytes | 来源验证 seam：实际磁盘字节、archive 越界与失败时禁止安装。 |
| bootstrap_linux_test.BootstrapProtocolTest.test_setup_first_process_and_github_env_use_only_new_loader_path | 来源验证 seam：实际磁盘字节、archive 越界与失败时禁止安装。 |
| bootstrap_linux_test.BootstrapProtocolTest.test_unsafe_archive_cannot_escape_install_prefix | 来源验证 seam：实际磁盘字节、archive 越界与失败时禁止安装。 |
| business_test.BusinessFailureBoundary.test_agent_and_workbench_producers_use_same_report_kind_as_collection | 受控子进程只写Playwright原结构；不启动浏览器或建立B通过键。 |
| business_test.BusinessFailureBoundary.test_c5_browser_report_alias_keeps_one_semantic_kind_and_original_events | C5 I01的同path/hash不同kind结构；可移植受控报告不冒充原CI passing。 |
| business_test.BusinessFailureBoundary.test_collection_rejects_conflicting_or_unsafe_declarations | 原业务异常的私有留存与公开诊断边界；不建立产品通过键。 |
| business_test.BusinessFailureBoundary.test_declared_types_are_canonical_without_filename_or_directory_rules | C6 T01同原bytes类型别名；Host夹具不建立正式passing。 |
| business_test.BusinessFailureBoundary.test_original_exception_is_private_and_public_summary_has_no_message_or_private_path | 原业务异常的私有留存与公开诊断边界；不建立产品通过键。 |
| business_test.BusinessFailureBoundary.test_private_capture_oserror_keeps_primary_failure_code_type_and_frames | 原业务异常的私有留存与公开诊断边界；不建立产品通过键。 |
| business_test.BusinessFailureBoundary.test_rejected_collection_preserves_shutdown_cleanup_and_child_root_image | 受控Suite不执行API；证据拒绝不能跳过原finally。 |
| business_test.BusinessFailureBoundary.test_untyped_defaults_and_guards_precede_private_or_symlink_reads | 原业务异常的私有留存与公开诊断边界；不建立产品通过键。 |
| capture_test.CaptureProtocolTest.test_captured_failure_ids_and_frames_are_verified_against_frozen_source | 真实子进程验证公开 console 出口，不使用真实认证材料。 |
| capture_test.CaptureProtocolTest.test_exception_projection_uses_static_frames_and_exact_prepare_code_membership | 真实子进程验证公开 console 出口，不使用真实认证材料。 |
| capture_test.CaptureProtocolTest.test_go_assertion_location_uses_unique_frozen_basename_and_never_message_or_function_text | 真实子进程验证公开 console 出口，不使用真实认证材料。 |
| capture_test.CaptureProtocolTest.test_go_locations_reject_ambiguous_modified_untracked_symlink_invalid_line_and_dynamic_paths | 真实子进程验证公开 console 出口，不使用真实认证材料。 |
| capture_test.CaptureProtocolTest.test_go_locations_share_existing_frame_and_attempt_limits | 真实子进程验证公开 console 出口，不使用真实认证材料。 |
| capture_test.CaptureProtocolTest.test_make_projection_rejects_unbound_rules_lines_and_private_dynamic_output | 真实子进程验证公开 console 出口，不使用真实认证材料。 |
| capture_test.CaptureProtocolTest.test_make_recipe_and_timeout_projection_host_fixture_never_replays_remote_failure | 受控输出只验证安全诊断投影，不代表恢复原CI错误或实际timeout。 |
| capture_test.CaptureProtocolTest.test_nonzero_child_keeps_private_bytes_and_safe_console | 真实子进程验证公开 console 出口，不使用真实认证材料。 |
| capture_test.CaptureProtocolTest.test_optional_public_report_keeps_actual_exit_private_bytes_and_single_execution | 真实子进程验证公开 console 出口，不使用真实认证材料。 |
| capture_test.CaptureProtocolTest.test_priority_projection_keeps_late_make_stage_after_repeated_go_locations | 真实子进程验证公开 console 出口，不使用真实认证材料。 |
| capture_test.CaptureProtocolTest.test_priority_projection_shares_original_attempt_frame_identity_and_prefix_bounds | 真实子进程验证公开 console 出口，不使用真实认证材料。 |
| capture_test.CaptureProtocolTest.test_projection_rejects_modified_untracked_external_symlink_ambiguous_and_subtest_inputs | 真实子进程验证公开 console 出口，不使用真实认证材料。 |
| capture_test.CaptureProtocolTest.test_public_report_write_failure_never_overwrites_original_failed_exit | 真实子进程验证公开 console 出口，不使用真实认证材料。 |
| capture_test.CaptureProtocolTest.test_repeated_signals_do_not_interrupt_child_cleanup | 真实子进程验证公开 console 出口，不使用真实认证材料。 |
| capture_test.CaptureProtocolTest.test_timeout_only_projects_source_verified_running_roots_and_fixed_marker | 真实子进程验证公开 console 出口，不使用真实认证材料。 |
| capture_test.CaptureProtocolTest.test_timeout_parallel_leaf_only_host_projection_verifies_root_and_discards_nested_private_suffix | 模拟Go标准runningList的parallel叶子；仅诊断投影Host回归，不是原CI timeout复现。 |
| capture_test.CaptureProtocolTest.test_timeout_parallel_leaf_projection_rejects_unknown_root_malformed_lines_and_keeps_attempt_bound | 真实子进程验证公开 console 出口，不使用真实认证材料。 |
| ci_test.CIProtocolTest.test_bootstrap_reports_are_uploaded_always_before_request_without_raw | CI Host 协议验证；不消费真实 deploy key，不触发 GitHub 或 Kubernetes。 |
| ci_test.CIProtocolTest.test_fast_request_has_no_private_source_or_cluster | CI Host 协议验证；不消费真实 deploy key，不触发 GitHub 或 Kubernetes。 |
| ci_test.CIProtocolTest.test_private_fetch_failure_removes_synthetic_key_and_captures_git_output | CI Host 协议验证；不消费真实 deploy key，不触发 GitHub 或 Kubernetes。 |
| ci_test.CIProtocolTest.test_quality_and_generated_capture_same_command_once_and_always_upload_only_safe_report | CI Host 协议验证；不消费真实 deploy key，不触发 GitHub 或 Kubernetes。 |
| ci_test.CIProtocolTest.test_smoke_request_contains_one_control_no_data | CI Host 协议验证；不消费真实 deploy key，不触发 GitHub 或 Kubernetes。 |
| ci_test.CIProtocolTest.test_untrusted_repository_is_rejected_before_git_or_key_write | CI Host 协议验证；不消费真实 deploy key，不触发 GitHub 或 Kubernetes。 |
| derived_browser_test.DerivedBrowserProtocol.test_copy_closure_preserves_raw_parent_json_and_rejects_changed_file | 派生证据的 Host 字节协议；validator-test 绝不冒充真实父 FULL。 |
| derived_browser_test.DerivedBrowserProtocol.test_i64_requires_actual_substep_selector_without_skip_or_failed_retry | 派生证据的 Host 字节协议；validator-test 绝不冒充真实父 FULL。 |
| derived_browser_test.DerivedBrowserProtocol.test_old_source_is_rejected_by_real_parent_gate | 派生证据的 Host 字节协议；validator-test 绝不冒充真实父 FULL。 |
| derived_browser_test.DerivedBrowserProtocol.test_pattern_missing_attachment_or_wrong_dom_or_compilation_is_rejected | 派生证据的 Host 字节协议；validator-test 绝不冒充真实父 FULL。 |
| derived_browser_test.DerivedBrowserProtocol.test_pattern_reads_all_twenty_actual_inputs | 派生证据的 Host 字节协议；validator-test 绝不冒充真实父 FULL。 |
| derived_browser_test.DerivedBrowserProtocol.test_real_gate_refuses_validator_fixture_without_creating_derived_passing_record | 派生证据的 Host 字节协议；validator-test 绝不冒充真实父 FULL。 |
| execution_test.ExecutionBoundary.test_browser_client_yaml_conversion_and_formal_create_keep_distinct_identity_boundaries | 真实loopback/子进程保留Node的YAML字节；正式create仍先有intent才确认原UID。 |
| execution_test.ExecutionBoundary.test_browser_collection_without_actual_execution_is_rejected | --list的相同selector不是浏览器运行，缺results不能生成通过报告引用。 |
| execution_test.ExecutionBoundary.test_go_skip_beneath_passing_root_cannot_pass | 父测试pass但真实leaf skip仍拒绝，Output原文本不会被公开。 |
| execution_test.ExecutionBoundary.test_legacy_docker_archive_through_node_prepare_supplies_real_manifest_to_session | 真实tar原config→受控node原manifest→Session闭包；不运行Docker或Kubernetes。 |
| execution_test.ExecutionBoundary.test_original_docker_oci_index_prepared_proof_keeps_child_bytes_and_gate_digest_checks | 实际执行接缝的Host边界检查，不产生产品K/B通过。 |
| execution_test.ExecutionBoundary.test_original_file_default_base_preserves_nested_immutable_reference | 公开封存API默认按原件父目录解析引用，不修改原收据字节。 |
| execution_test.ExecutionBoundary.test_prepared_config_only_has_fixed_error_and_private_operator_is_not_public | 实际执行接缝的Host边界检查，不产生产品K/B通过。 |
| execution_test.ExecutionBoundary.test_product_confirmation_uses_actual_instance_version_without_invented_resource_version | 平台响应只有uid/version，账本不能伪造Kubernetes resourceVersion。 |
| execution_test.ExecutionBoundary.test_shutdown_drains_accepted_http_handler_and_ordinary_process_before_return | 已接受的handler及同步run须在锁内结束，不能shutdown后才继续写原响应。 |
| execution_test.ExecutionBoundary.test_undrained_handler_keeps_failed_lease_until_process_exit | 有界未排空不能解锁；Host夹具确认原handler结束后才手工撤回自己的保留fd。 |
| execution_test.PublicationCleanupBoundary.test_all_unsent_proofs_are_durable_before_first_shared_writer_stop | C7原sent-first顺序可重现；次序不得决定queued能否读取原证明。 |
| execution_test.PublicationCleanupBoundary.test_incomplete_unsent_evidence_cannot_become_confirmed_unsent | 实际执行接缝的Host边界检查，不产生产品K/B通过。 |
| execution_test.PublicationCleanupBoundary.test_other_case_publications_and_instances_are_never_queried_or_rewritten | 实际执行接缝的Host边界检查，不产生产品K/B通过。 |
| execution_test.PublicationCleanupBoundary.test_proof_persistence_failure_stops_later_mutation_and_gc | 实际执行接缝的Host边界检查，不产生产品K/B通过。 |
| execution_test.PublicationCleanupBoundary.test_remaining_uncertainty_preserves_original_barrier_uid_and_absence_guards | 实际执行接缝的Host边界检查，不产生产品K/B通过。 |
| exporter_test.ProtocolDiagnosticExporterTests.test_nested_missing_private_canary_path_and_credentials_remain_explicit_gaps | REG21真实出口测试；敏感输入仅使用本例合成canary和临时文件。 |
| exporter_test.ProtocolDiagnosticExporterTests.test_typed_eight_closure_keeps_original_bytes_and_incomplete_104 | REG21真实出口测试；敏感输入仅使用本例合成canary和临时文件。 |
| exporter_test.ExporterTests.test_canary | REG21真实出口测试；敏感输入仅使用本例合成canary和临时文件。 |
| exporter_test.ExporterTests.test_completed_diagnostic_validates_with_same_pre_read_private_guard | 完成态必须走完整首record校验，不能由failed索引早停掩盖先读private旁路。 |
| exporter_test.ExporterTests.test_console | REG21真实出口测试；敏感输入仅使用本例合成canary和临时文件。 |
| exporter_test.ExporterTests.test_image_tar | REG21真实出口测试；敏感输入仅使用本例合成canary和临时文件。 |
| exporter_test.ExporterTests.test_kubeconfig | REG21真实出口测试；敏感输入仅使用本例合成canary和临时文件。 |
| exporter_test.ExporterTests.test_private_source | REG21真实出口测试；敏感输入仅使用本例合成canary和临时文件。 |
| exporter_test.ExporterTests.test_raw_db | REG21真实出口测试；敏感输入仅使用本例合成canary和临时文件。 |
| exporter_test.ExporterTests.test_safe_bundle_revalidates_from_other_cwd | REG21真实出口测试；敏感输入仅使用本例合成canary和临时文件。 |
| fault_proxy_test.FaultControlTest.test_exact_write_rejection_excludes_dry_run | 仅证明故障fixture控制HTTP机制，不替代真实Kubernetes或mTLS验收。 |
| fault_proxy_test.FaultControlTest.test_original_create_response_requires_prior_exact_identity | 响应丢失时只接受先登记请求的原UID白名单事件，不凭事后同名GET认领。 |
| fault_proxy_test.FaultControlTest.test_read_fault_control_and_reset | 仅证明故障fixture控制HTTP机制，不替代真实Kubernetes或mTLS验收。 |
| focus_layer_test.FocusLayer.test_default_cli_and_focus_without_layer_keep_existing_contracts | 精确分层增量选择的Host协议；不运行产品Q、真实K/B或任何目标集群。 |
| focus_layer_test.FocusLayer.test_invalid_layers_and_empty_intersection_reject_before_environment_or_dispatch | 精确分层增量选择的Host协议；不运行产品Q、真实K/B或任何目标集群。 |
| focus_layer_test.FocusLayer.test_list_intersection_is_exact_twenty_and_does_not_select_overlapping_q | REG21-FOCUS-LAYER-001：仅17K3B；两013 K与SOURCE B的同ID Q不得被选中。 |
| focus_layer_test.FocusLayer.test_owner_and_layer_select_only_requested_business_without_mutating_profile | 组件 Make 入口使用所属 Issue 与层级交集，空交集不能退化成全量执行。 |
| focus_layer_test.FocusLayer.test_real_mismatched_host_rejects_before_environment_or_business_dispatch | REG21-FOCUS-LAYER-002：真实host不符仍拒绝，不能把合成声明扩散到正式入口。 |
| focus_layer_test.FocusLayer.test_run_dispatch_matches_list_keeps_104_seed_and_partial_strict_failure | 真实batch/index/关闭判定；Git、环境快照与child结果是隔离Host系统边界输入。 |
| gate_test.ValidatorTests.test_changed_artifact | REG21 validator 协议测试；仅合成 validator-test，不代表产品K通过。 |
| gate_test.ValidatorTests.test_cleanup_failure | REG21 validator 协议测试；仅合成 validator-test，不代表产品K通过。 |
| gate_test.ValidatorTests.test_contract_mismatch | REG21 validator 协议测试；仅合成 validator-test，不代表产品K通过。 |
| gate_test.ValidatorTests.test_duplicate_key | REG21 validator 协议测试；仅合成 validator-test，不代表产品K通过。 |
| gate_test.ValidatorTests.test_full_and_smoke_fixtures_retain_oci_and_browser_evidence | REG21 validator 协议测试；仅合成 validator-test，不代表产品K通过。 |
| gate_test.ValidatorTests.test_missing_artifact | REG21 validator 协议测试；仅合成 validator-test，不代表产品K通过。 |
| gate_test.ValidatorTests.test_missing_key | REG21 validator 协议测试；仅合成 validator-test，不代表产品K通过。 |
| gate_test.ValidatorTests.test_original_evidence_cannot_be_omitted | REG21 validator 协议测试；仅合成 validator-test，不代表产品K通过。 |
| gate_test.ValidatorTests.test_path_escape | REG21 validator 协议测试；仅合成 validator-test，不代表产品K通过。 |
| gate_test.ValidatorTests.test_skipped | REG21 validator 协议测试；仅合成 validator-test，不代表产品K通过。 |
| gate_test.ValidatorTests.test_unknown_key | REG21 validator 协议测试；仅合成 validator-test，不代表产品K通过。 |
| gate_test.ValidatorTests.test_valid_fixture_has_explicit_non_product_scope | REG21 validator 协议测试；仅合成 validator-test，不代表产品K通过。 |
| gate_test.ValidatorTests.test_wrong_image_source | REG21 validator 协议测试；仅合成 validator-test，不代表产品K通过。 |
| gate_test.ValidatorTests.test_wrong_profile | REG21 validator 协议测试；仅合成 validator-test，不代表产品K通过。 |
| gate_test.ValidatorTests.test_wrong_source | REG21 validator 协议测试；仅合成 validator-test，不代表产品K通过。 |
| gate_test.ProtocolRunnerTests.test_cli_records_actual_unittest_result | REG21 validator 协议测试；仅合成 validator-test，不代表产品K通过。 |
| ginkgo_dispatch_test.GinkgoDispatchTests.test_business_failure_is_a_ginkgo_failure_and_next_case_still_runs | 调度器 Host 自测：运行真实 Ginkgo child，但业务回调只操作临时文件。 |
| ginkgo_dispatch_test.GinkgoDispatchTests.test_duplicate_plan_fails_before_starting_child | 调度器 Host 自测：运行真实 Ginkgo child，但业务回调只操作临时文件。 |
| ginkgo_dispatch_test.GinkgoDispatchTests.test_fail_fast_keeps_remaining_case_skipped | 调度器 Host 自测：运行真实 Ginkgo child，但业务回调只操作临时文件。 |
| ginkgo_dispatch_test.GinkgoDispatchTests.test_real_specs_dispatch_each_key_once_and_keep_order | 调度器 Host 自测：运行真实 Ginkgo child，但业务回调只操作临时文件。 |
| ginkgo_events_test.GinkgoEventsTests.test_missing_or_duplicate_children_are_not_passing | 原生 Ginkgo 报告的正负控：通过声明不能替代实际执行与完整子例。 |
| ginkgo_events_test.GinkgoEventsTests.test_native_report_retains_root_and_completed_child_without_private_text | 原生 Ginkgo 报告的正负控：通过声明不能替代实际执行与完整子例。 |
| ginkgo_events_test.GinkgoEventsTests.test_old_stdout_empty_report_and_unknown_root_cannot_pass | 原生 Ginkgo 报告的正负控：通过声明不能替代实际执行与完整子例。 |
| ginkgo_events_test.GinkgoEventsTests.test_skip_failed_retry_dry_run_and_nonzero_exit_cannot_pass | 原生 Ginkgo 报告的正负控：通过声明不能替代实际执行与完整子例。 |
| image_provenance_test.ImageProofProtocolTest.test_docker29_manifest_id_is_not_confused_with_config_digest | 通过真实 tar/config 字节验证来源，inspect 声明不能代替 image config。 |
| image_provenance_test.ImageProofProtocolTest.test_node_graph_rejects_unknown_wrong_config_source_platform_bytes_and_ambiguous_index | 通过真实 tar/config 字节验证来源，inspect 声明不能代替 image config。 |
| image_provenance_test.ImageProofProtocolTest.test_old_actual_config_cannot_borrow_new_inspect_revision | 通过真实 tar/config 字节验证来源，inspect 声明不能代替 image config。 |
| image_provenance_test.ImageProofProtocolTest.test_original_node_index_and_child_bytes_bind_frozen_config | 通过真实 tar/config 字节验证来源，inspect 声明不能代替 image config。 |
| image_provenance_test.ImageProofProtocolTest.test_raw_config_bytes_determine_config_digest | 通过真实 tar/config 字节验证来源，inspect 声明不能代替 image config。 |
| lifecycle_runtime_test.P14RuntimeBoundary.test_actual_index_identity_reaches_deletion_only_after_full_runtime_chain_verification | 合法index/manifest都需真实CRI与原字节链；证明及typed原件均可回读。 |
| lifecycle_runtime_test.P14RuntimeBoundary.test_original_single_pod_requirement_is_still_enforced_before_runtime_reads | 原单Pod计数约束不能因补运行身份检查而被跳过。 |
| lifecycle_runtime_test.P14RuntimeBoundary.test_runtime_rejects_wrong_config_pod_container_node_platform_and_missing_proof | 当前Pod的CRI身份与原OCI链缺一不可，不能仅凭index或准备声明放行。 |
| lifecycle_runtime_test.P14RuntimeBoundary.test_runtime_without_original_typed_metadata_cannot_advance | 实际运行校验后仍须封存原typed OCI证明，不能只留下verified布尔值。 |
| platform_cases_test.PlatformCaseParameters.test_all_seven_account_cases_collect_without_launching_browser | 平台适配参数边界测试；不运行API或浏览器，不产生产品通过键。 |
| platform_cases_test.PlatformCaseParameters.test_disabled_owner_selects_all_three_existing_tests | 平台适配参数边界测试；不运行API或浏览器，不产生产品通过键。 |
| platform_cases_test.PlatformCaseParameters.test_initialization_case_keeps_database_uninitialized | 平台适配参数边界测试；不运行API或浏览器，不产生产品通过键。 |
| platform_cases_test.PlatformCaseParameters.test_private_wrapper_collects_the_original_three_tests | 平台适配参数边界测试；不运行API或浏览器，不产生产品通过键。 |
| platform_cases_test.PlatformCaseParameters.test_real_registry_routes_every_platform_case_in_its_declared_profiles | 使用真实注册表，避免手工entry掩盖继承owner把T01/T03分派到另一case。 |
| platform_cases_test.PlatformCaseParameters.test_unknown_cases_and_unavailable_profiles_fail_before_setup | 平台适配参数边界测试；不运行API或浏览器，不产生产品通过键。 |
| prepare_test.PrepareProtocolTest.test_data_node_existing_taint_is_conditionally_removed_before_readback | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_data_node_postconditions_reject_unready_unschedulable_or_blocking_taint | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_data_node_read_and_patch_errors_never_become_missing_taint_success | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_data_node_replacement_or_malformed_readback_is_rejected | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_data_node_without_taint_is_valid_without_mutation | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_existing_cluster_is_rejected_before_any_intent_or_write | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_fast_has_no_cluster_and_never_invokes_environment_commands | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_ledger_refusal_never_issues_prepare_writes | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_local_node_manifest_completion_never_overwrites_first_node_or_publishes_operator | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_partial_create_failure_carries_observed_identity_to_session | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_public_operator_bundle_cannot_enable_python_tags_or_mutate_global_loader | 局部构造器仅兼容YAML标准value标量，未知/Python标签仍拒绝。 |
| prepare_test.PrepareProtocolTest.test_public_operator_bundle_preserves_literal_equal_enum_and_scalar_types | 官方0.83 bundle合法裸'='必须保留；不能为绕过解析把bool/int全改string。 |
| prepare_test.PrepareProtocolTest.test_recovery_execution_failure_continues_other_confirmed_target | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_recovery_ledger_failure_is_fatal_before_later_mutation | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_recovery_read_403_is_failure_and_never_delete | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_recovery_result_ledger_failure_stops_after_recorded_delete | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_recovery_uid_replacement_is_not_deleted | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_recovery_uses_explicit_config_and_confirms_container_absence | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_smoke_recovery_refuses_unplanned_data_before_commands | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_volume_legacy_archive_accepts_import_manifest_only_after_actual_config_and_content_binding | legacy原config与导入manifest不同；通过实际容器和node原bytes证明同一个config。 |
| prepare_test.PrepareProtocolTest.test_volume_original_job_pod_capability_and_success_assertions_remain_required | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_volume_probe_preserves_original_proof_then_normally_deletes_original_job_and_pod | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_volume_probe_replacement_or_read_failure_is_not_deleted_or_marked_clean | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_volume_runtime_index_selects_one_actual_platform_child_without_rewriting_host_proof | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_volume_runtime_read_failure_or_timeout_never_deletes_or_checkpoints | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| prepare_test.PrepareProtocolTest.test_volume_runtime_reads_only_recorded_node_container_uid | node同名替代不能借旧身份；docker exec须绑定本轮已记录container UID。 |
| prepare_test.PrepareProtocolTest.test_volume_runtime_rejects_unbound_container_or_content_before_cleanup | 环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。 |
| protocol_cases_test.KubernetesNodeResponseTests.test_node_read_preserves_both_legal_container_bytes | 协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。 |
| protocol_cases_test.KubernetesNodeResponseTests.test_node_read_rejects_wrong_container_items_identity_and_arch | 协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。 |
| protocol_cases_test.KubernetesProtocolGateTests.test_deep_original_inputs_cannot_be_replaced_by_matching_outer_wrapper | 协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。 |
| protocol_cases_test.KubernetesProtocolGateTests.test_host_fixture_cannot_be_used_as_actual_k | 协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。 |
| protocol_cases_test.KubernetesProtocolGateTests.test_main_and_recovery_accept_v1_list_and_nodelist | 协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。 |
| protocol_cases_test.KubernetesProtocolGateTests.test_main_and_recovery_reject_wrong_node_response | 协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。 |
| protocol_cases_test.KubernetesProtocolGateTests.test_missing_or_wrong_protocol_inputs_fail_closed | 协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。 |
| protocol_cases_test.KubernetesProtocolGateTests.test_protocol_ref_base_stays_inside_canonical_run_root | 协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。 |
| protocol_cases_test.KubernetesProtocolGateTests.test_recovery_actual_target_restricted_sa_and_replacement_originals_are_required | 协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。 |
| protocol_cases_test.KubernetesProtocolGateTests.test_validator_fixtures_cover_all_eight_without_product_pass | 协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。 |
| protocol_cases_test.ProtocolCaseHostCLI.test_changed_private_handoff_is_rejected_before_child_setup | 协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。 |
| protocol_cases_test.ProtocolCaseHostCLI.test_inherited_lease_does_not_deadlock_or_unlock_parent | 协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。 |
| protocol_cases_test.ProtocolCaseHostCLI.test_int_retains_keyboard_interrupt | 协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。 |
| protocol_cases_test.ProtocolCaseHostCLI.test_kill_has_no_finally_claim_and_cleans_recorded_auxiliary | 协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。 |
| protocol_cases_test.ProtocolCaseHostCLI.test_protocol_child_seeds_exact_207_not_run_before_lock_or_api | 协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。 |
| protocol_cases_test.ProtocolCaseHostCLI.test_real_protocol_context_rejects_cross_target_before_any_child_or_api | 协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。 |
| protocol_cases_test.ProtocolCaseHostCLI.test_repeat_signals_do_not_reenter_cleanup | 协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。 |
| protocol_cases_test.ProtocolCaseHostCLI.test_restricted_policy_has_no_delete_and_read_deny_keeps_only_system_identity | 协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。 |
| protocol_cases_test.ProtocolCaseHostCLI.test_setup_failure_keeps_environment_failed_and_pending | 协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。 |
| protocol_cases_test.ProtocolCaseHostCLI.test_term_preserves_complete_index_and_natural_cleanup | 协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。 |
| protocol_test.ProtocolCLI.test_after_first_failure_keeps_remaining_not_run | 首个协议 fixture 完成后崩溃，不能借这一行声称完整 profile 通过。 |
| protocol_test.ProtocolCLI.test_before_first_failure_keeps_all_keys | 首例 setup 失败有当前环境错误和完整剩余键，不能丢弃尚未执行的例。 |
| protocol_test.ProtocolCLI.test_inherited_lane_lease_does_not_relock_or_unlock_batch_owner | 受控child共享同一open-file-description；退出后整批owner仍持独占锁。 |
| protocol_test.ProtocolCLI.test_lock_wait_stops_before_setup | 等锁时收到停止后，不能在锁释放时继续创建任何夹具。 |
| recovery_test.RecoveryProtocol.test_active_recovery_coordinator_is_never_a_stop_target | 恢复入口的 Host 协议；实际临时 Git 与受控可执行文件不产生产品验收键。 |
| recovery_test.RecoveryProtocol.test_actual_clean_source_uid_normal_gc_keeps_original_run_immutable | 恢复入口的 Host 协议；实际临时 Git 与受控可执行文件不产生产品验收键。 |
| recovery_test.RecoveryProtocol.test_actual_cluster_uid_replacement_is_rejected_before_delete | 恢复入口的 Host 协议；实际临时 Git 与受控可执行文件不产生产品验收键。 |
| recovery_test.RecoveryProtocol.test_auxiliary_group_and_term_ignoring_grandchild_are_stopped_by_birth_identity | 恢复入口的 Host 协议；实际临时 Git 与受控可执行文件不产生产品验收键。 |
| recovery_test.RecoveryProtocol.test_child_manifest_without_ledger_keeps_identity_gap | 恢复入口的 Host 协议；实际临时 Git 与受控可执行文件不产生产品验收键。 |
| recovery_test.RecoveryProtocol.test_child_request_original_hash_and_child_run_id_are_required | 恢复入口的 Host 协议；实际临时 Git 与受控可执行文件不产生产品验收键。 |
| recovery_test.RecoveryProtocol.test_environment_only_writes_workflow_exact_public_safe_receipt_on_success_and_failure | 恢复入口的 Host 协议；实际临时 Git 与受控可执行文件不产生产品验收键。 |
| recovery_test.RecoveryProtocol.test_fsync_failure_escapes_old_recover_broad_catch_before_delete | 恢复入口的 Host 协议；实际临时 Git 与受控可执行文件不产生产品验收键。 |
| recovery_test.RecoveryProtocol.test_inherited_same_fd_lease_is_kept_locked_after_recovery_returns | 恢复入口的 Host 协议；实际临时 Git 与受控可执行文件不产生产品验收键。 |
| recovery_test.RecoveryProtocol.test_lock_wait_signal_keeps_seed_and_never_enters_api | 恢复入口的 Host 协议；实际临时 Git 与受控可执行文件不产生产品验收键。 |
| recovery_test.RecoveryProtocol.test_original_cluster_uid_mismatch_never_deletes | 恢复入口的 Host 协议；实际临时 Git 与受控可执行文件不产生产品验收键。 |
| recovery_test.RecoveryProtocol.test_prepare_partial_snapshot_never_adopts_unknown_container_uid | 恢复入口的 Host 协议；实际临时 Git 与受控可执行文件不产生产品验收键。 |
| recovery_test.RecoveryProtocol.test_reused_birth_is_refused_without_signalling_live_fixture | 恢复入口的 Host 协议；实际临时 Git 与受控可执行文件不产生产品验收键。 |
| recovery_test.RecoveryProtocol.test_shared_verified_full_environment_keeps_creation_owner_for_new_execution | 恢复入口的 Host 协议；实际临时 Git 与受控可执行文件不产生产品验收键。 |
| recovery_test.RecoveryProtocol.test_wrong_actual_source_or_dirty_tree_rejects_before_any_api | 恢复入口的 Host 协议；实际临时 Git 与受控可执行文件不产生产品验收键。 |
| registry_test.RegistryTests.test_disabled_owner_requires_all_three_browser_entries | 固定验收键与真实执行选择器的公开查询测试。 |
| registry_test.RegistryTests.test_fixed_profiles_are_independently_expanded | 固定验收键与真实执行选择器的公开查询测试。 |
| registry_test.RegistryTests.test_focus_rejects_unknown_duplicate_and_wrong_profile | 固定验收键与真实执行选择器的公开查询测试。 |
| registry_test.RegistryTests.test_only_eight_reg21_k_use_kubernetes_protocol_boundary | 固定验收键与真实执行选择器的公开查询测试。 |
| run_test.RunCLI.test_original_exception_is_private_and_public_index_has_no_message_or_private_reference | 统一入口的 Host 协议检查；列举与失败留存不算功能子例执行。 |
| run_test.RunCLI.test_private_exception_write_failure_is_secondary_and_keeps_original_failure_and_full_keys | 统一入口的 Host 协议检查；列举与失败留存不算功能子例执行。 |
| run_test.RunCLI.test_setup_error_writes_full_expected_profile | 正式入口在读取坏环境前预写17个Smoke键，错误不能吞掉剩余键。 |
| runtime_image_test.RuntimeImageProtocol.test_official_index_and_tag_use_same_original_bytes_and_ignore_untrusted_repo_digest_claims | 真实JSON/OCI原字节结构的Host协议回归；不调用Docker/API，不建立正式passing。 |
| runtime_image_test.RuntimeImageProtocol.test_original_t01_legacy_operator_manifest_requires_actual_config_not_host_digest_alias | 真实JSON/OCI原字节结构的Host协议回归；不调用Docker/API，不建立正式passing。 |
| runtime_image_test.RuntimeImageProtocol.test_original_u01_accepts_locked_tag_only_with_original_owner_and_actual_official_runtime_chain | 真实JSON/OCI原字节结构的Host协议回归；不调用Docker/API，不建立正式passing。 |
| runtime_image_test.RuntimeImageProtocol.test_prepare_and_t01_share_actual_runtime_binding_without_rebinding_existing_node_uid | 真实JSON/OCI原字节结构的Host协议回归；不调用Docker/API，不建立正式passing。 |
| runtime_image_test.RuntimeImageProtocol.test_private_operator_host_oci_rejects_wrong_digest_bytes_and_config_binding | 真实JSON/OCI原字节结构的Host协议回归；不调用Docker/API，不建立正式passing。 |
| runtime_image_test.RuntimeImageProtocol.test_runtime_known_uuid_is_only_public_after_original_identity_confirmation | 真实JSON/OCI原字节结构的Host协议回归；不调用Docker/API，不建立正式passing。 |
| runtime_image_test.RuntimeImageProtocol.test_runtime_rejects_wrong_original_node_pod_container_config_source_arch_and_unlocked_reference | 真实JSON/OCI原字节结构的Host协议回归；不调用Docker/API，不建立正式passing。 |
| runtime_image_test.RuntimeImageProtocol.test_runtime_unknown_image_error_value_is_never_public | 真实JSON/OCI原字节结构的Host协议回归；不调用Docker/API，不建立正式passing。 |
| runtime_image_test.RuntimeImageProtocol.test_runtime_unvalidated_or_non_uuid_identity_is_hashed_without_private_token | 真实JSON/OCI原字节结构的Host协议回归；不调用Docker/API，不建立正式passing。 |
| runtime_image_test.RuntimeImageProtocol.test_t01_private_operator_host_oci_manifest_and_index_stay_private_and_bind_actual_runtime | 真实JSON/OCI原字节结构的Host协议回归；不调用Docker/API，不建立正式passing。 |
| runtime_image_test.RuntimeImageProtocol.test_u01_does_not_adopt_namespace_pod_or_replacement_workload_owner | 真实JSON/OCI原字节结构的Host协议回归；不调用Docker/API，不建立正式passing。 |
| status_fixture_test.StatusFixture.test_conflict_and_local_stale_each_exhaust_at_eight_without_refresh | status fixture的真实传输/账本Host接缝；只替换外部API，不计正式K通过。 |
| status_fixture_test.StatusFixture.test_forbidden_invalid_unknown_and_response_loss_never_retry_or_confirm | status fixture的真实传输/账本Host接缝；只替换外部API，不计正式K通过。 |
| status_fixture_test.StatusFixture.test_intent_persistence_failure_dispatches_no_write | status fixture的真实传输/账本Host接缝；只替换外部API，不计正式K通过。 |
| status_fixture_test.StatusFixture.test_invalid_raw_status_path_fails_before_any_dispatch | status fixture的真实传输/账本Host接缝；只替换外部API，不计正式K通过。 |
| status_fixture_test.StatusFixture.test_local_stale_revision_reloads_same_uid_before_put | caller读取后RV前进时，第一轮不写，只按本地明确stale code重新GET。 |
| status_fixture_test.StatusFixture.test_namespace_and_unconfirmed_identity_guards_still_precede_put | status fixture的真实传输/账本Host接缝；只替换外部API，不计正式K通过。 |
| status_fixture_test.StatusFixture.test_plain_replace_patch_and_other_subresource_keep_uid_rv_jsonpatch | status fixture的真实传输/账本Host接缝；只替换外部API，不计正式K通过。 |
| status_fixture_test.StatusFixture.test_replacement_before_guard_or_after_get_never_binds_new_uid | status fixture的真实传输/账本Host接缝；只替换外部API，不计正式K通过。 |
| status_fixture_test.StatusFixture.test_standard_conflict_reloads_current_rv_and_keeps_original_uid | status fixture的真实传输/账本Host接缝；只替换外部API，不计正式K通过。 |
| status_fixture_test.StatusFixture.test_status_replace_preserves_native_put_and_original_uid_rv | REG21-STATUS-FIXTURE-001：真实caller写两原UID，status PUT body保留本轮RV。 |
| suite_session_test.SuiteSessionProtocolTest.test_actual_http_value_is_observed_before_original_status_assertion | 旧业务 Suite 的 Host 协议测试；受控 API 响应不构成 K/B 验收。 |
| suite_session_test.SuiteSessionProtocolTest.test_all_inherited_constructors_accept_smoke_and_cleanup_stops_before_legacy_restore | 旧业务 Suite 的 Host 协议测试；受控 API 响应不构成 K/B 验收。 |
| suite_session_test.SuiteSessionProtocolTest.test_base_cleanup_returns_session_result_before_legacy_paths | 旧业务 Suite 的 Host 协议测试；受控 API 响应不构成 K/B 验收。 |
| suite_session_test.SuiteSessionProtocolTest.test_fixture_gate_records_actual_barrier_and_release_order_for_storage_alias | 旧业务 Suite 的 Host 协议测试；受控 API 响应不构成 K/B 验收。 |
| suite_session_test.SuiteSessionProtocolTest.test_inherited_http_helpers_preserve_actual_failed_response_and_hooks | 旧业务 Suite 的 Host 协议测试；受控 API 响应不构成 K/B 验收。 |
| suite_session_test.SuiteSessionProtocolTest.test_non_initialization_case_cannot_skip_initialization | 旧业务 Suite 的 Host 协议测试；受控 API 响应不构成 K/B 验收。 |
| suite_session_test.SuiteSessionProtocolTest.test_original_pod_uid_cannot_be_replaced_before_forward | 旧业务 Suite 的 Host 协议测试；受控 API 响应不构成 K/B 验收。 |
| suite_session_test.SuiteSessionProtocolTest.test_replacement_endpoint_refuses_forward_before_any_process | 旧业务 Suite 的 Host 协议测试；受控 API 响应不构成 K/B 验收。 |
| suite_session_test.SuiteSessionProtocolTest.test_service_forward_uses_explicit_context_and_rechecks_original_endpoint_before_proxy | 旧业务 Suite 的 Host 协议测试；受控 API 响应不构成 K/B 验收。 |
| suite_session_test.SuiteSessionProtocolTest.test_session_rejects_wrong_source_before_image_or_api_work | 旧业务 Suite 的 Host 协议测试；受控 API 响应不构成 K/B 验收。 |
| suite_session_test.SuiteSessionProtocolTest.test_smoke_accepts_only_actual_control_and_delegates_failed_command | 旧业务 Suite 的 Host 协议测试；受控 API 响应不构成 K/B 验收。 |
| suite_session_test.SuiteSessionProtocolTest.test_smoke_token_case_uses_real_control_target_without_data_alias | 旧业务 Suite 的 Host 协议测试；受控 API 响应不构成 K/B 验收。 |
| transport_test.ProcessBoundary.test_auxiliary_group_stops_ignoring_grandchild_after_parent_exits | 父进程收到TERM即退出时，也必须结束同一原进程组中忽略TERM的孙进程。 |
| transport_test.ProcessBoundary.test_parent_forwards_each_signal_once_and_waits_for_child_finally | 父持批次锁等待时转交TERM/INT，child自己的finally自然完成且不被重复触发。 |
| transport_test.ProcessBoundary.test_private_file_sink_keeps_actual_bytes_without_console_passthrough | Docker save 一类调用保留私有二进制文件，不能恢复任意stdout透传。 |
| transport_test.ProcessBoundary.test_raw_output_is_private_before_any_console | 真实 child 的原 stdout/stderr 仅给调用者与私有捕获，不自动进入公开 console。 |
| transport_test.ProcessBoundary.test_running_browser_process_observes_cooperative_stop | 主线程等待Node/Go时也检查停止；被中断child先退出再将原停止上抛。 |
| transport_test.IdentityLedger.test_apply_list_confirms_each_original_response | List部分成功仍有逐对象原UID，不能只记一个批次名字。 |
| transport_test.IdentityLedger.test_baseline_under_canonical_run_root_allows_system_tmp_alias | 系统入口的 /tmp 别名可用，run 内真正符号链接仍不能读取基线。 |
| transport_test.IdentityLedger.test_client_dry_run_keeps_yaml_bytes_and_never_creates_ledger_identity | U06本地YAML转换保留原参数/stdin，不进入实际create及原UID确认。 |
| transport_test.IdentityLedger.test_cr_cleanup_failure_preserves_namespace_and_rbac | 原Agent未结束时不能撤namespace/RBAC，否则GC失去原运行身份。 |
| transport_test.IdentityLedger.test_explicit_nonwaiting_delete_preserves_shutdown_observation_window | T08的--wait=false必须在条件DELETE受理后返回，不能等Pod退出再观察503。 |
| transport_test.IdentityLedger.test_intent_survives_reopen_without_invented_uid | 发送窗口崩溃后保留未知身份，重开不能凭名字造出创建确认。 |
| transport_test.IdentityLedger.test_mutation_uses_original_uid_and_current_revision_conditions | 受控外部 client 验证实际发送JSON Patch含UID/RV测试，而非先GET后按名覆盖。 |
| transport_test.IdentityLedger.test_namespaced_create_rejects_replaced_parent_uid_before_mutation | 本run namespace被替代后，不能按同名将新fixture写进外部namespace。 |
| transport_test.IdentityLedger.test_own_finalizer_added_to_absent_field_preserves_later_foreign_token | 原字段缺失也按token差异撤回，不能因缺失与空列表不同而清空外部token。 |
| transport_test.IdentityLedger.test_recovery_read_forbidden_is_not_absent | 真实外部client的403必须保留失败，不能按无资源报告清理成功。 |
| transport_test.IdentityLedger.test_recovery_rejects_same_name_replacement_without_delete | 恢复读到替代UID必须失败，真实child不能收到任何delete命令。 |
| transport_test.IdentityLedger.test_restore_only_our_finalizer_and_preserve_later_foreign_token | P14恢复只撤本fixture新增token，不清除后来出现的外来finalizer。 |
| transport_test.IdentityLedger.test_transport_persists_intent_before_actual_child_response | 外部 client 在返回创建响应前已能读到 intent，确认后才出现原 UID。 |
| transport_test.IdentityLedger.test_unconfirmed_create_blocks_dependent_namespace_cleanup | cluster-scoped创建gap未闭合时，不撤其可能依赖的原namespace/RBAC。 |
| workbench_name_test.WorkbenchAgentFixtureName.test_original_long_prefix_reaches_create_and_runtime_with_bounded_distinct_identity | REG21-U05-FIXTURE-001：真实prepare→创建/发布/runtime读取的名称接缝；不运行集群或浏览器。 |

## CI_FAST：58 个固定键

以下逐键登记，重复引用同一根场景仍须分别满足对应必选输入；根场景通过不能替代缺失子例。

| 稳定键 / 层 | 实际 selector / 场景入口 | 验证目的 | 选取标准 |
| --- | --- | --- | --- |
| T07.db / Q | TestT07StartupFailureCleansListenersAndRejectsSecondWriter, TestT07DatabaseCorruptionNeverStartsReady；run | 就绪必须覆盖DB与真实RPC；预期：不 ready，清理半启动资源 | 无需真实集群即可重复验证的启动和就绪边界 |
| T07.rpc / Q | TestT07ActualRPCProbeRequiredBeforeReady, TestT07ReadyStopsWhenRealRPCTransportFails；run | 就绪必须覆盖DB与真实RPC；预期：不 ready，静态页面可访问不能代替业务探测 | 无需真实集群即可重复验证的启动和就绪边界 |
| T07.listener / Q | TestT07StartupFailureCleansListenersAndRejectsSecondWriter/http, TestT07StartupFailureCleansListenersAndRejectsSecondWriter/rpc；run | 就绪必须覆盖DB与真实RPC；预期：不 ready，另一半 listener 被清理 | 无需真实集群即可重复验证的启动和就绪边界 |
| T07.target-offline / Q | TestT07TargetOfflineDoesNotBreakPlatformReadiness；clusters | 就绪必须覆盖DB与真实RPC；预期：平台仍 ready，目标状态单独失败 | 无需真实集群即可重复验证的启动和就绪边界 |
| I02.default-expiry / Q | TestI02AbsoluteExpiryAndPersistence/default-expiry；run | Session绝对到期与Cookie安全；预期：到期后 401，轮询不能延长绝对寿命 | 无需真实集群即可重复验证的会话与授权边界 |
| I02.configured-expiry / Q | TestI02AbsoluteExpiryAndPersistence/configured-expiry；run | Session绝对到期与Cookie安全；预期：按配置绝对到期，重启保留仍有效会话 | 无需真实集群即可重复验证的会话与授权边界 |
| I02.cookie / Q | TestI02OriginAndDirectRPCEnforceAuthorization, TestI02HTTPSOriginSetsSecureCookie；run | Session绝对到期与Cookie安全；预期：HttpOnly；SameSite/Path 与入口相容，HTTPS 时 Secure；Origin/CSRF 检查有效，token 仅专用响应头交付 | 无需真实集群即可重复验证的会话与授权边界 |
| I06.primary / Q | TestI06CookieExpiryDoesNotCancelAcceptedOperation；lifecycle | Cookie到期不取消已受理任务；预期：旧用户请求401；任务仍按当前账号权限完成或如实核验，不因Cookie到期自动取消。 | 无需真实集群即可重复验证的会话与授权边界 |
| C03.missing / Q | TestC03UnverifiedIdentityCannotRegister/missing；clusters | 注册拒绝未核验身份；预期：拒绝注册，不伪称已运行 Kubernetes 1.19 | 无需真实集群即可重复验证的集群身份和凭据拒绝边界 |
| C03.forbidden / Q | TestC03UnverifiedIdentityCannotRegister/forbidden；clusters | 注册拒绝未核验身份；预期：拒绝注册，明确权限原因 | 无需真实集群即可重复验证的集群身份和凭据拒绝边界 |
| C03.anonymous / Q | TestC03UnverifiedIdentityCannotRegister/anonymous；clusters | 注册拒绝未核验身份；预期：拒绝注册，不以 SSAR 成功代替 | 无需真实集群即可重复验证的集群身份和凭据拒绝边界 |
| C03.error / Q | TestC03UnverifiedIdentityCannotRegister/error；clusters | 注册拒绝未核验身份；预期：拒绝注册，不保存为已生效 | 无需真实集群即可重复验证的集群身份和凭据拒绝边界 |
| C09.http / Q | TestC09RejectSelectedContextDependenciesBeforeNetworking/http；clusters | 拒绝不支持的kubeconfig依赖；预期：拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | 无需真实集群即可重复验证的集群身份和凭据拒绝边界 |
| C09.skip-tls / Q | TestC09RejectSelectedContextDependenciesBeforeNetworking/skip-tls；clusters | 拒绝不支持的kubeconfig依赖；预期：拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | 无需真实集群即可重复验证的集群身份和凭据拒绝边界 |
| C09.external-ca / Q | TestC09RejectSelectedContextDependenciesBeforeNetworking/external-ca；clusters | 拒绝不支持的kubeconfig依赖；预期：拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | 无需真实集群即可重复验证的集群身份和凭据拒绝边界 |
| C09.external-cert / Q | TestC09RejectSelectedContextDependenciesBeforeNetworking/external-cert；clusters | 拒绝不支持的kubeconfig依赖；预期：拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | 无需真实集群即可重复验证的集群身份和凭据拒绝边界 |
| C09.token-file / Q | TestC09RejectSelectedContextDependenciesBeforeNetworking/token-file；clusters | 拒绝不支持的kubeconfig依赖；预期：拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | 无需真实集群即可重复验证的集群身份和凭据拒绝边界 |
| C09.exec / Q | TestC09RejectSelectedContextDependenciesBeforeNetworking/exec；clusters | 拒绝不支持的kubeconfig依赖；预期：拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | 无需真实集群即可重复验证的集群身份和凭据拒绝边界 |
| C09.auth-provider / Q | TestC09RejectSelectedContextDependenciesBeforeNetworking/auth-provider；clusters | 拒绝不支持的kubeconfig依赖；预期：拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | 无需真实集群即可重复验证的集群身份和凭据拒绝边界 |
| C09.proxy / Q | TestC09RejectSelectedContextDependenciesBeforeNetworking/proxy；clusters | 拒绝不支持的kubeconfig依赖；预期：拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | 无需真实集群即可重复验证的集群身份和凭据拒绝边界 |
| K10.name / Q | TestF03KSMValidationThroughHTTPAndDirectRPC；ksm | 名称与不可变字段在后端校验；预期：字段级拒绝，不自动生成名称 | 无需真实集群即可重复验证的配置身份与输入边界 |
| K10.collision / Q | TestF03KSMValidationThroughHTTPAndDirectRPC；ksm | 名称与不可变字段在后端校验；预期：冲突，不接管已有 CR | 无需真实集群即可重复验证的配置身份与输入边界 |
| K10.fixed-target / Q | TestP05DriftOverwritePreservesMetadataAndP06Equivalent；ksm | 名称与不可变字段在后端校验；预期：拒绝修改固定身份；不把仅保存草稿当作已部署 | 无需真实集群即可重复验证的配置身份与输入边界 |
| K10.unsupported / Q | TestF03KSMValidationThroughHTTPAndDirectRPC；ksm | 名称与不可变字段在后端校验；预期：拒绝透传，不写集群 | 无需真实集群即可重复验证的配置身份与输入边界 |
| P04.draft-race / Q | TestK10DraftNameAndRevision；ksm | 过期修订或预览不能提交别版；预期：只有一个成功，另一冲突并保留本地编辑 | 无需真实集群即可重复验证的发布事务和竞争边界 |
| P04.draft-after-preview / Q | TestP04DraftAndTargetPreviewRaces；ksm | 过期修订或预览不能提交别版；预期：要求新预览，不能静默换版 | 无需真实集群即可重复验证的发布事务和竞争边界 |
| P04.target-after-preview / Q | TestP04DraftAndTargetPreviewRaces；ksm | 过期修订或预览不能提交别版；预期：配置基线冲突，需重新确认 | 无需真实集群即可重复验证的发布事务和竞争边界 |
| P08.cancel-wins / Q | TestP08CancellationCompetesWithPersistentDispatch/cancel-wins；lifecycle | 取消与派发竞争只有一个结果；预期：已取消且没有集群写请求 | 无需真实集群即可重复验证的发布事务和竞争边界 |
| P08.dispatch-wins / Q | TestP08CancellationCompetesWithPersistentDispatch/dispatch-wins；lifecycle | 取消与派发竞争只有一个结果；预期：取消不报成功，按真实结果核验 | 无需真实集群即可重复验证的发布事务和竞争边界 |
| R01.intervals / Q | TestR01WorkbenchIntervals, TestR01WorkbenchIntervals/1, TestR01WorkbenchIntervals/10, TestR01WorkbenchIntervals/20；workbench | 轮询与过期边界可判定；预期：边界合法，越界拒绝，默认仍 10 秒 | 无需真实集群即可重复验证的状态投影和请求预算边界 |
| R01.freshness / Q | TestR01WorkbenchSourceFreshness；workbench | 轮询与过期边界可判定；预期：只有超过 30 秒才过期，不自动放宽 | 无需真实集群即可重复验证的状态投影和请求预算边界 |
| R01.early-error / Q | TestR01WorkbenchEarlyError；workbench | 轮询与过期边界可判定；预期：立即当前未知并显示历史，不等待阈值 | 无需真实集群即可重复验证的状态投影和请求预算边界 |
| R03.missing-status / Q | TestR03WorkbenchMissingStatus；workbench | 缺status与合法零值分开；预期：运行摘要未知，不显示 0/0 可用 | 无需真实集群即可重复验证的状态投影和请求预算边界 |
| R03.incomplete-status / Q | TestR03WorkbenchIncompleteStatus；workbench | 缺status与合法零值分开；预期：未知，不能仅凭 Ready/Available 当作运行 | 无需真实集群即可重复验证的状态投影和请求预算边界 |
| R03.ksm-suspended / Q | TestR03KSMEffectiveZeroValuesAndMissingStatus/ksm-suspended；ksm | 缺status与合法零值分开；预期：解释为挂起，不能把意图即刻当完成 | 无需真实集群即可重复验证的状态投影和请求预算边界 |
| R03.ksm-disabled / Q | TestR03KSMEffectiveZeroValuesAndMissingStatus/ksm-disabled；ksm | 缺status与合法零值分开；预期：解释为停用，保留 CR 与历史 | 无需真实集群即可重复验证的状态投影和请求预算边界 |
| R03.ksm-no-match / Q | TestR03KSMEffectiveZeroValuesAndMissingStatus/ksm-no-match；ksm | 缺status与合法零值分开；预期：解释为无匹配且 0/0 | 无需真实集群即可重复验证的状态投影和请求预算边界 |
| R03.agent-zero / Q | TestR03AgentZeroUsesApplicableStatus/valid-zero, TestR03AgentZeroUsesApplicableStatus/missing-status, TestR03AgentZeroUsesApplicableStatus/old-generation；agent_storage | 缺status与合法零值分开；预期：挂起与零副本分开解释，不因 Available=True 显示运行中 | 无需真实集群即可重复验证的状态投影和请求预算边界 |
| R03.agent-paused / Q | TestR03AgentPausedReadOnly；agent | 缺status与合法零值分开；预期：不能声称 Pod 已停止 | 无需真实集群即可重复验证的状态投影和请求预算边界 |
| R03.deleted / Q | TestP14ForegroundDeletionKeepsUIDGraphProtection/complete；lifecycle | 缺status与合法零值分开；预期：生命周期已删除，不能冒充挂起或可用 | 无需真实集群即可重复验证的状态投影和请求预算边界 |
| R05.page / Q | TestR05WorkbenchFailedInventoryPage；workbench | 批次部分失败不能伪造不存在；预期：未完整核验项未知，不按缺失/零匹配处理 | 无需真实集群即可重复验证的状态投影和请求预算边界 |
| R05.item / Q | TestR05WorkbenchIndependentItems；workbench | 批次部分失败不能伪造不存在；预期：逐实例/来源记录独立时间和错误 | 无需真实集群即可重复验证的状态投影和请求预算边界 |
| R06.primary / Q | TestR06WorkbenchCoalescesReads, TestR06WorkbenchRemainingBudget, TestR06WorkbenchConcurrentBudgets, TestR06WorkbenchSubmillisecondBudget, TestR06WorkbenchServerBudgetMaximum, TestR06WorkbenchServerBudgetMaximum/missing, TestR06WorkbenchServerBudgetMaximum/100000, TestR06WorkbenchServerBudgetMaximum/0, TestR06WorkbenchServerBudgetMaximum/invalid；workbench | 请求预算及并发不被内部放大；预期：HTTP/RPC/K8s遵守15/10/8秒及剩余预算；全局≤8、单集群≤4；同目标合并，不自动重放写请求。 | 无需真实集群即可重复验证的状态投影和请求预算边界 |
| F01.absent / Q | TestF01Agent/absent；agent | Thrift往返保留显式配置语义；预期：缺失不意外变为空对象 | 无需真实集群即可重复验证的错误处理及恢复边界 |
| F01.empty-object / Q | TestF01Agent/empty-object；agent | Thrift往返保留显式配置语义；预期：{} 与省略保持不同语义 | 无需真实集群即可重复验证的错误处理及恢复边界 |
| F01.empty-list / Q | TestF01Agent/empty-list；agent | Thrift往返保留显式配置语义；预期：清空语义保持，非法组合拒绝 | 无需真实集群即可重复验证的错误处理及恢复边界 |
| F01.unknown-mode / Q | TestF01Agent/unknown-mode；agent | Thrift往返保留显式配置语义；预期：拒绝，不默认退化为全选或不选 | 无需真实集群即可重复验证的错误处理及恢复边界 |
| F02.primary / Q | TestF02KSMHTTPHasRealRPCAndCannotBypassUnavailableRPC；ksm | 真实RPC链路不能被HTTP旁路；预期：正常HTTP业务抵达server并执行业务授权；RPC不可用时无直接DB/K8s成功旁路；伪造角色/身份无效。 | 无需真实集群即可重复验证的错误处理及恢复边界 |
| F03.quantity / Q | TestF03KSMValidationThroughHTTPAndDirectRPC；ksm | 后端字段校验及稳定错误；预期：稳定字段级错误与 HTTP 类别，CR 无写入，敏感值不泄漏 | 无需真实集群即可重复验证的错误处理及恢复边界 |
| F03.regexp / Q | TestF03KSMValidationThroughHTTPAndDirectRPC；ksm | 后端字段校验及稳定错误；预期：稳定字段级错误与 HTTP 类别，CR 无写入，敏感值不泄漏 | 无需真实集群即可重复验证的错误处理及恢复边界 |
| F03.selector / Q | TestF03Agent；agent | 后端字段校验及稳定错误；预期：稳定字段级错误与 HTTP 类别，CR 无写入，敏感值不泄漏 | 无需真实集群即可重复验证的错误处理及恢复边界 |
| F03.url / Q | TestF03Agent；agent | 后端字段校验及稳定错误；预期：稳定字段级错误与 HTTP 类别，CR 无写入，敏感值不泄漏 | 无需真实集群即可重复验证的错误处理及恢复边界 |
| F03.tls-pair / Q | TestF03Agent；agent | 后端字段校验及稳定错误；预期：稳定字段级错误与 HTTP 类别，CR 无写入，敏感值不泄漏 | 无需真实集群即可重复验证的错误处理及恢复边界 |
| F03.unknown-field / Q | TestF03KSMUnknownFieldsHTTPAndRPC；ksm | 后端字段校验及稳定错误；预期：稳定字段级错误与 HTTP 类别，CR 无写入，敏感值不泄漏 | 无需真实集群即可重复验证的错误处理及恢复边界 |
| F04.success / Q | TestF04LifecycleSchema5Upgrade/success；lifecycle | 迁移失败保留原数据库；预期：关系、历史和可解密性保留 | 无需真实集群即可重复验证的错误处理及恢复边界 |
| F04.failure / Q | TestF04LifecycleSchema5Upgrade/failure；lifecycle | 迁移失败保留原数据库；预期：保留原库与恢复快照，不替换为空库 | 无需真实集群即可重复验证的错误处理及恢复边界 |
| F05.before-commit / Q | TestF05PublicationCrashBoundaries/beforeAcceptanceCommit；ksm | 幂等与任务受理事务原子；预期：要么完整未提交，不留下半个受理状态 | 无需真实集群即可重复验证的错误处理及恢复边界 |
| F05.after-commit / Q | TestF05PublicationCrashBoundaries/afterAcceptanceCommit；ksm | 幂等与任务受理事务原子；预期：同键重启查询原完整结果，不产生第二操作；仍执行当前授权 | 无需真实集群即可重复验证的错误处理及恢复边界 |

## CI_SMOKE：17 个固定键

以下逐键登记，重复引用同一根场景仍须分别满足对应必选输入；根场景通过不能替代缺失子例。

| 稳定键 / 层 | 实际 selector / 场景入口 | 验证目的 | 选取标准 |
| --- | --- | --- | --- |
| T01.primary / K | T01.primary；agent | 固定运行来源；预期：均符合锁定版本/摘要；main为1控制面2worker；FULL另有1控制面数据集群；缺锁或不Ready即环境失败。 | 单 control 集群覆盖部署来源与实际 Pod 就绪 |
| T02.primary / K | T02.primary；run | 平台实际运行在管控集群；预期：单Go进程真实经过Kitex；HTTP可达；RPC只监听Pod loopback且无Service端口；无宿主机业务旁路。 | 单 control 集群覆盖部署来源与实际 Pod 就绪 |
| T05.primary / K | T05.primary；lifecycle | 平台Pod重建保留管理状态；预期：数据、原密钥及有效Session恢复；SQLite可读写；任务按恢复规则处理，不因Pod消失被当作删除。 | 单 control 集群覆盖部署来源与实际 Pod 就绪 |
| I01.primary / B | I01.primary；run | 初始化与临时密码流程；预期：已有账号时不能再次初始化；密码以哈希保存；临时密码用户首次登录必须改密，旧Session失效，改密后正常访问自身资源。 | 单 control 集群覆盖账号和真实入口 |
| C01.client-cert / K | C01.client-cert；clusters | 有效注册不创建观测负载；预期：身份与 UID 核验通过后保存，不创建观测负载 | 单 control 集群覆盖注册与凭据链路 |
| C01.token / K | C01.token；clusters | 有效注册不创建观测负载；预期：核验有效非匿名身份，不将平台账号当 Kubernetes 身份 | 单 control 集群覆盖注册与凭据链路 |
| K01.primary / K | K01.primary；lifecycle | KSM多实例与外部只读；预期：按各自name/UID隔离；另一实例不变；外部实例不产生平台发布历史且不能编辑、下发、删除或接管。 | 单 control 集群覆盖KSM 真实工作负载 |
| K02.primary / K | K02.primary；ksm | Deployment与资源预设实际映射；预期：单副本Deployment；省略分片分支；声明对应100m/250Mi/500Mi预设；真实Pod就绪，预设不被称为已验证容量。 | 单 control 集群覆盖KSM 真实工作负载 |
| A01.primary / K | A01.primary；agent | Agent实际StatefulSet与预设；预期：每shard正常1副本、paused=false；200m/512Mi/1Gi声明正确；真实工作负载就绪。 | 单 control 集群覆盖Agent 真实工作负载与存储 |
| A04.primary / K | A04.primary；agent | 默认PVC为每卷1Gi；预期：PVC每卷请求1Gi、RWO、实际Bound并被目标Pod挂载；不沿用旧fixture的64Mi。 | 单 control 集群覆盖Agent 真实工作负载与存储 |
| P01.primary / K | P01.primary；ksm | 保存不产生集群写；预期：仅DB草稿修订推进，平台集群写请求为0、CR spec未因保存变化；独立status/RV可变化，页面不显示已下发。 | 单 control 集群覆盖发布或删除完成 |
| P02.primary / K | P02.primary；ksm | 不可变发布与来源快照；预期：受理版本与sourceDraftRevision冻结；新草稿不污染该次发布；确认写入前不提升最近写入版本。 | 单 control 集群覆盖发布或删除完成 |
| P03.same / K | P03.same；ksm | 重复点击只受理一次；预期：返回同一 operationId 与当前结果，实际副作用不重复 | 单 control 集群覆盖发布或删除完成 |
| P03.different-content / K | P03.different-content；ksm | 重复点击只受理一次；预期：409 幂等冲突 | 单 control 集群覆盖发布或删除完成 |
| P03.another-active / K | P03.another-active；ksm | 重复点击只受理一次；预期：拒绝第二变更，仍可保存新草稿 | 单 control 集群覆盖发布或删除完成 |
| U01.primary / B | U01.primary；workbench | C方案实际页面与信息层级；预期：品牌为可观测管控平台；名称下有namespace，类型/组件及状态分列；无横向溢出、重复名称或Operator状态报告。 | 单 control 集群覆盖关键浏览器操作 |
| U06.primary / B | U06.primary；agent | YAML预览及确认后导航；预期：YAML可解析且无孤立对象项横杠；确认立即进入运行信息，已接受/CR写入/运行分开，实际CR与预览语义一致。 | 单 control 集群覆盖关键浏览器操作 |

## 执行边界与维护

普通 Go UT 会跳过 `tests/e2e` 的环境入口与 `TestWorkbenchBrowserFixture` 的显式 Host 夹具；它们没有被算作常规 UT 或真实集群通过。实际 K/B 执行只从 `make acceptance-run` 进入，Ginkgo 调度器保留父 Session 的原 lane、UID 账本和清理责任；Playwright 的 JSON 报告仍作为独立浏览器证据。

本文件登记应执行的用例，不表示本次已经执行。实际结果、测试源码与环境失败分别见 `docs/e2e/issue-21-ginkgo-results.md`；远程 CI 以绑定候选 SHA 的 Actions run 为准。
