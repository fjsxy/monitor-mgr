// 一期 UT 以业务风险组织，每个 It 的必要阶段共同决定结果。
package platform_test

import (
	"os"

	"github.com/fjsxy/monitor-mgr/internal/platform"
	ginkgo "github.com/onsi/ginkgo/v2"
)

var _ = ginkgo.It("UT-01 认证与 Session", func() {
	t := ginkgo.GinkgoT()
	checkTestI01InitializeOnlyEmptyDatabase(t)
	checkTestI01TemporaryPasswordMustChangeAndRevokesSession(t)
	checkTestI02AbsoluteExpiryAndPersistence(t)
	checkTestI02HTTPSOriginSetsSecureCookie(t)
})

var _ = ginkgo.It("UT-02 账号与当前授权", func() {
	t := ginkgo.GinkgoT()
	checkTestI07ResetDisableRoleAndLastAdministrator(t)
	checkTestI07ConcurrentDemotionsRetainAdministrator(t)
	checkTestReviewCredentialReplayCurrent(t)
})

var _ = ginkgo.It("UT-03 kubeconfig 安全解析", func() {
	t := ginkgo.GinkgoT()
	checkTestC09RejectSelectedContextDependenciesBeforeNetworking(t)
})

var _ = ginkgo.It("UT-04 集群登记与管理状态", func() {
	t := ginkgo.GinkgoT()
	checkTestC01PersistentClusterRegistrationThroughRPC(t)
	checkTestC03UnverifiedIdentityCannotRegister(t)
	checkTestClusterCredentialLifecycleAndImmediateOwnership(t)
	checkTestClusterUnfinishedChangesBlockTransferAndUnregisterAcrossKinds(t)
	checkTestClusterCanRegisterWithRestrictedComponentCapabilities(t)
})

var _ = ginkgo.It("UT-07 实例身份与不可变字段", func() {
	t := ginkgo.GinkgoT()
	checkTestInstanceCreatorIdentity(t)
	checkTestAdminInstanceWriteProtection(t)
	checkTestCollectorAdminProtection(t)
	checkTestInstanceCreatorRoleSnapshot(t)
	checkTestK10DraftNameAndRevision(t)
	checkTestAgentTypedDraftIdentityAndDefaults(t)
	checkTestAgentRevisionNameBudgetLegacyAndWorker(t)
	checkTestAgentFixedCapacityRevalidated(t)
	checkTestExternalKSMReadOnlyAndFailedList(t)
})

var _ = ginkgo.It("UT-08 保存预览与版本冻结", func() {
	t := ginkgo.GinkgoT()
	checkTestP02PublicationFreezesSourceAndP03Idempotency(t)
	checkTestP04DraftAndTargetPreviewRaces(t)
	checkTestAgentDraftSourceIsolation(t)
})

var _ = ginkgo.It("UT-09 幂等与受理事务", func() {
	t := ginkgo.GinkgoT()
	if os.Getenv("MM_F05_STAGE") != "" {
		checkTestF05PublicationCrashBoundaries(t)
		return
	}
	checkTestAccountMutationsAreDurablyIdempotentAndStillAuthorize(t)
	checkTestF05PublicationCrashBoundaries(t)
})

var _ = ginkgo.It("UT-10 派发取消与任务授权", func() {
	t := ginkgo.GinkgoT()
	checkTestInstanceDispatchReauthorizesCreatorProtection(t)
	checkTestInstanceDispatchRejectsDisabledAccount(t)
	checkTestInstanceConflictRetryReauthorizes(t)
	checkTestInstanceDeletionDispatchReauthorizes(t)
	checkTestInstanceSentResultSurvivesRevocation(t)
	checkTestP08CancellationCompetesWithPersistentDispatch(t)
	checkTestI06CookieExpiryDoesNotCancelAcceptedOperation(t)
	checkTestPublicationPreflightDoesNotBlockCurrentAccountRevocation(t)
	checkTestT08ShutdownRejectsNewAcceptanceBeforeDrain(t)
})

var _ = ginkgo.It("UT-11 重试与重启恢复", func() {
	t := ginkgo.GinkgoT()
	if os.Getenv("MM_P13_STAGE") != "" {
		checkTestP13RestartUsesPersistentSentBoundary(t)
		return
	}
	checkLegacySMNeverDispatches(t)
	checkSingleCRResourceProgress(t)
	checkAgentSingleCRResourceProgress(t)
	checkResourceRetryIdentity(t)
	checkResourceProgressSurvivesRestart(t)
	checkTestP07RetryUsesFrozenVersionAndCurrentRevisions(t)
	checkTestPublicationUnknownOutcomeNeverReplays(t)
	checkTestP13RestartUsesPersistentSentBoundary(t)
})

var _ = ginkgo.It("UT-12 预览与删除保护", func() {
	t := ginkgo.GinkgoT()
	checkTestDeletionPreviewBindsTargetAndImpact(t)
	checkTestP05DriftOverwritePreservesMetadataAndP06Equivalent(t)
	checkPublicationStatusConflict(t)
	checkTestP14ForegroundDeletionKeepsUIDGraphProtection(t)
	checkTestDeletionCrossNamespaceOwnerUIDRemainsProtected(t)
	checkTestDeletion404AfterPreflightKeepsSeenPodProtected(t)
})

var _ = ginkgo.It("UT-13 PVC 危险动作决策", func() {
	t := ginkgo.GinkgoT()
	checkTestAgentStorageDeletePreviewRequiresAdoptedPolicy(t)
	checkTestAgentStorageIncompleteListRemainsUnknown(t)
	checkTestAgentStorageEmptyDirDoesNotRequirePVCClass(t)
	checkTestAgentStorageNewInstanceRejectsOrphanClaim(t)
	checkTestAgentStorageRetainedIdentitySurvivesParents(t)
	checkTestAgentStorageDangerRequiresAdoptedPolicy(t)
	checkTestAgentStorageResponsibilityOutlivesInstance(t)
	checkTestAgentStoragePreviewImpact(t)
	checkTestAgentStorageIndependentOwnerPolicy(t)
	checkTestAgentStoragePendingDeleteBlocksRestore(t)
})

var _ = ginkgo.It("UT-14 状态与工作台查询", func() {
	t := ginkgo.GinkgoT()
	checkTestR03KSMEffectiveZeroValuesAndMissingStatus(t)
	checkTestR03AgentPausedReadOnly(t)
	checkTestR03AgentZeroUsesApplicableStatus(t)
	checkTestAgentIndependentSourceFreshness(t)
	checkTestR05WorkbenchFailedInventoryPage(t)
	checkTestWorkbenchQueryFiltersAndStatistics(t)
	checkTestWorkbenchActivitySequencePersists(t)
	checkTestR03WorkbenchIncompleteStatus(t)
})

var _ = ginkgo.It("UT-15 请求预算与并发", func() {
	t := ginkgo.GinkgoT()
	// 接下来只对一个协议边界做真实计时，其余异常 metadata 直接验证解析。
	platform.BudgetBoundaryForTest(t)
	checkTestR06WorkbenchCoalescesReads(t)
	checkTestR06WorkbenchConcurrentBudgets(t)
	checkTestR06WorkbenchServerBudgetMaximum(t)
})

var _ = ginkgo.It("UT-16 数据库主密钥与敏感投影", func() {
	t := ginkgo.GinkgoT()
	checkTestUnknownCreatorMigration(t)
	checkTestSchemaOneMigrationPreservesSessionAndBacksUpBeforeDDL(t)
	checkLegacyPublicationMigration(t)
	checkTestT07DatabaseCorruptionNeverStartsReady(t)
	checkTestClusterSecretsRecoverOnlyWithOriginalKeyAndPreserveSession(t)
	checkTestF04EncryptedSchema3WrongKeyDoesNotMigrate(t)
	checkTestSharedHMACDetectsHiddenConfigAndPreservesMetadata(t)
	checkTestAgentMalformedURLReadProjection(t)
})

var _ = ginkgo.It("UT-17 HTTP 与 Kitex 协议契约", func() {
	t := ginkgo.GinkgoT()
	checkTestI02OriginAndDirectRPCEnforceAuthorization(t)
	checkTestF03KSMUnknownFieldsHTTPAndRPC(t)
	checkTestF02KSMHTTPHasRealRPCAndCannotBypassUnavailableRPC(t)
	checkTestWorkbenchHTTPInt64RoundTrip(t)
	checkTestF01Agent(t)
})
