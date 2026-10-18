#!/usr/bin/env python3
"""固定profile与补充回归的唯一注册表；仅用标准库，查询不访问集群。"""
import argparse
import hashlib
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
MANIFEST_HASH = 'a3e71ae6cbe5828a04cd9fe0cc425274f05ad042ecab53a62d20db1def7d93f8'
PROFILES = {'LOCAL_FULL': 207, 'CI_FAST': 58, 'CI_SMOKE': 17}

GO_SELECTORS = {'K10.name': ['TestF03KSMValidationThroughHTTPAndDirectRPC'],
 'K10.collision': ['TestF03KSMValidationThroughHTTPAndDirectRPC'],
 'K10.fixed-target': ['TestP05DriftOverwritePreservesMetadataAndP06Equivalent'],
 'K10.unsupported': ['TestF03KSMValidationThroughHTTPAndDirectRPC'],
 'P04.draft-race': ['TestK10DraftNameAndRevision'],
 'P04.draft-after-preview': ['TestP04DraftAndTargetPreviewRaces'],
 'P04.target-after-preview': ['TestP04DraftAndTargetPreviewRaces'],
 'R03.ksm-suspended': ['TestR03KSMEffectiveZeroValuesAndMissingStatus/ksm-suspended'],
 'R03.ksm-disabled': ['TestR03KSMEffectiveZeroValuesAndMissingStatus/ksm-disabled'],
 'R03.ksm-no-match': ['TestR03KSMEffectiveZeroValuesAndMissingStatus/ksm-no-match'],
 'F02.primary': ['TestF02KSMHTTPHasRealRPCAndCannotBypassUnavailableRPC'],
 'F03.quantity': ['TestF03KSMValidationThroughHTTPAndDirectRPC'],
 'F03.regexp': ['TestF03KSMValidationThroughHTTPAndDirectRPC'],
 'F03.unknown-field': ['TestF03KSMUnknownFieldsHTTPAndRPC'],
 'F05.before-commit': ['TestF05PublicationCrashBoundaries/beforeAcceptanceCommit'],
 'F05.after-commit': ['TestF05PublicationCrashBoundaries/afterAcceptanceCommit'],
 'I06.primary': ['TestI06CookieExpiryDoesNotCancelAcceptedOperation'],
 'P08.cancel-wins': ['TestP08CancellationCompetesWithPersistentDispatch/cancel-wins'],
 'P08.dispatch-wins': ['TestP08CancellationCompetesWithPersistentDispatch/dispatch-wins'],
 'R03.deleted': ['TestP14ForegroundDeletionKeepsUIDGraphProtection/complete'],
 'F04.success': ['TestF04LifecycleSchema5Upgrade/success'],
 'F04.failure': ['TestF04LifecycleSchema5Upgrade/failure'],
 'R03.agent-paused': ['TestR03AgentPausedReadOnly'],
 'F01.absent': ['TestF01Agent/absent'],
 'F01.empty-object': ['TestF01Agent/empty-object'],
 'F01.empty-list': ['TestF01Agent/empty-list'],
 'F01.unknown-mode': ['TestF01Agent/unknown-mode'],
 'F03.selector': ['TestF03Agent'],
 'F03.url': ['TestF03Agent'],
 'F03.tls-pair': ['TestF03Agent'],
 'R01.intervals': ['TestR01WorkbenchIntervals',
                   'TestR01WorkbenchIntervals/1',
                   'TestR01WorkbenchIntervals/10',
                   'TestR01WorkbenchIntervals/20'],
 'R01.freshness': ['TestR01WorkbenchSourceFreshness'],
 'R01.early-error': ['TestR01WorkbenchEarlyError'],
 'R03.missing-status': ['TestR03WorkbenchMissingStatus'],
 'R03.incomplete-status': ['TestR03WorkbenchIncompleteStatus'],
 'R05.page': ['TestR05WorkbenchFailedInventoryPage'],
 'R05.item': ['TestR05WorkbenchIndependentItems'],
 'R06.primary': ['TestR06WorkbenchCoalescesReads',
                 'TestR06WorkbenchRemainingBudget',
                 'TestR06WorkbenchConcurrentBudgets',
                 'TestR06WorkbenchSubmillisecondBudget',
                 'TestR06WorkbenchServerBudgetMaximum',
                 'TestR06WorkbenchServerBudgetMaximum/missing',
                 'TestR06WorkbenchServerBudgetMaximum/100000',
                 'TestR06WorkbenchServerBudgetMaximum/0',
                 'TestR06WorkbenchServerBudgetMaximum/invalid'],
 'R03.agent-zero': ['TestR03AgentZeroUsesApplicableStatus/valid-zero',
                    'TestR03AgentZeroUsesApplicableStatus/missing-status',
                    'TestR03AgentZeroUsesApplicableStatus/old-generation'],
 'T07.db': ['TestT07StartupFailureCleansListenersAndRejectsSecondWriter',
            'TestT07DatabaseCorruptionNeverStartsReady'],
 'T07.rpc': ['TestT07ActualRPCProbeRequiredBeforeReady', 'TestT07ReadyStopsWhenRealRPCTransportFails'],
 'T07.listener': ['TestT07StartupFailureCleansListenersAndRejectsSecondWriter/http',
                  'TestT07StartupFailureCleansListenersAndRejectsSecondWriter/rpc'],
 'T07.target-offline': ['TestT07TargetOfflineDoesNotBreakPlatformReadiness'],
 'I02.default-expiry': ['TestI02AbsoluteExpiryAndPersistence/default-expiry'],
 'I02.configured-expiry': ['TestI02AbsoluteExpiryAndPersistence/configured-expiry'],
 'I02.cookie': ['TestI02OriginAndDirectRPCEnforceAuthorization', 'TestI02HTTPSOriginSetsSecureCookie'],
 'C03.missing': ['TestC03UnverifiedIdentityCannotRegister/missing'],
 'C03.forbidden': ['TestC03UnverifiedIdentityCannotRegister/forbidden'],
 'C03.anonymous': ['TestC03UnverifiedIdentityCannotRegister/anonymous'],
 'C03.error': ['TestC03UnverifiedIdentityCannotRegister/error'],
 'C09.http': ['TestC09RejectSelectedContextDependenciesBeforeNetworking/http'],
 'C09.skip-tls': ['TestC09RejectSelectedContextDependenciesBeforeNetworking/skip-tls'],
 'C09.external-ca': ['TestC09RejectSelectedContextDependenciesBeforeNetworking/external-ca'],
 'C09.external-cert': ['TestC09RejectSelectedContextDependenciesBeforeNetworking/external-cert'],
 'C09.token-file': ['TestC09RejectSelectedContextDependenciesBeforeNetworking/token-file'],
 'C09.exec': ['TestC09RejectSelectedContextDependenciesBeforeNetworking/exec'],
 'C09.auth-provider': ['TestC09RejectSelectedContextDependenciesBeforeNetworking/auth-provider'],
 'C09.proxy': ['TestC09RejectSelectedContextDependenciesBeforeNetworking/proxy']}

REG_GO_SELECTORS = {'REG18-SHARED-001': ['TestSharedSchema4PreviewSurvivesUpgrade'],
 'REG18-SHARED-002': ['TestSharedSchema4PreviewSurvivesUpgrade',
                      'TestAgentTypedDraftIdentityAndDefaults'],
 'REG18-SHARED-003': ['TestSharedTargetSensitiveReadProjection',
                      'TestSharedHMACDetectsHiddenConfigAndPreservesMetadata'],
 'REG18-CAP-004': ['TestAgentOperatorCapabilityBoundaries/missing-agent-controller'],
 'REG18-CAP-005': ['TestAgentOperatorCapabilityBoundaries/missing-scrape-informer'],
 'REG18-CAP-004.single-dash-namespace': ['TestAgentOperatorCapabilityBoundaries/single-dash-namespace'],
 'REG18-CAP-004.single-dash-selector': ['TestAgentOperatorCapabilityBoundaries/single-dash-selector'],
 'REG18-CAP-004.override': ['TestAgentOperatorCapabilityBoundaries/identity-override'],
 'REG18-CAP-004.old-rs': ['TestAgentOperatorCapabilityBoundaries/old-rs-mismatch',
                          'TestAgentOperatorCapabilityBoundaries/mixed-pod-scope'],
 'REG18-CAP-004.none-short-circuit': ['TestAgentOperatorCapabilityBoundaries/none-without-scrape-api'],
 'REG18-VALID-006': ['TestAgentDurationRange'],
 'REG18-SOURCE-007': ['TestAgentDraftSourceIsolation'],
 'REG18-FIXED-008': ['TestAgentFixedCapacityRevalidated', 'TestAgentFixedCapacityPossiblySent'],
 'REG18-HEADERS-009': ['TestAgentHeadersReadProjection'],
 'REG18-CAP-010.positional': ['TestAgentOperatorCapabilityBoundaries/positional'],
 'REG18-CAP-010.terminator': ['TestAgentOperatorCapabilityBoundaries/terminator'],
 'REG18-CAP-010.duplicate-scope': ['TestAgentOperatorCapabilityBoundaries/duplicate-scope'],
 'REG18-CAP-010.boolean-arity': ['TestAgentOperatorCapabilityBoundaries/boolean-arity'],
 'REG18-CAP-010.known-scope-space': ['TestAgentOperatorCapabilityBoundaries/known-scope-space'],
 'REG18-CAP-010.env-expansion': ['TestAgentOperatorCapabilityBoundaries/env-expansion'],
 'REG18-SELECTOR-012.exists-nil': ['TestAgentSelectorExpressionProjection/exists-nil'],
 'REG18-SELECTOR-012.exists-empty': ['TestAgentSelectorExpressionProjection/exists-empty'],
 'REG18-SELECTOR-012.not-exists-nil': ['TestAgentSelectorExpressionProjection/not-exists-nil'],
 'REG18-SELECTOR-012.not-exists-empty': ['TestAgentSelectorExpressionProjection/not-exists-empty'],
 'REG18-SELECTOR-012.in': ['TestAgentSelectorExpressionProjection/in'],
 'REG18-SELECTOR-012.not-in': ['TestAgentSelectorExpressionProjection/not-in'],
 'REG18-PROJECTION-013.url-parse-error': ['TestAgentMalformedURLReadProjection/url-parse-error'],
 'REG18-PROJECTION-013.proxy-parse-error': ['TestAgentMalformedURLReadProjection/proxy-parse-error'],
 'REG19-ACTIVITY-001': ['TestWorkbenchActivitySequencePersists'],
 'REG19-HTTP-I64-001': ['TestWorkbenchHTTPInt64RoundTrip'],
 'REG19-SOURCE-001': ['TestWorkbenchLateObservationAfterUnregister',
                      'TestWorkbenchLateObservationAfterCredentialSwitch',
                      'TestWorkbenchLateObservationIdentityRead'],
 'REG20-STORAGE-002.transfer': ['TestAgentStorageResponsibilityOutlivesInstance/transfer-Delete',
                                'TestAgentStorageResponsibilityOutlivesInstance/transfer-Retain'],
 'REG20-STORAGE-002.unregister': ['TestAgentStorageResponsibilityOutlivesInstance/unregister-Delete',
                                  'TestAgentStorageResponsibilityOutlivesInstance/unregister-Retain'],
 'REG20-STORAGE-003.st-uid-accept': ['TestAgentStoragePreviewImpact/st-uid-accept'],
 'REG20-STORAGE-003.st-uid-dispatch': ['TestAgentStoragePreviewImpact/st-uid-dispatch'],
 'REG20-STORAGE-003.pvc-uid-accept': ['TestAgentStoragePreviewImpact/pvc-uid-accept'],
 'REG20-STORAGE-003.pvc-uid-dispatch': ['TestAgentStoragePreviewImpact/pvc-uid-dispatch'],
 'REG20-STORAGE-003.policy-accept': ['TestAgentStoragePreviewImpact/policy-accept'],
 'REG20-STORAGE-003.policy-dispatch': ['TestAgentStoragePreviewImpact/policy-dispatch'],
 'REG20-STORAGE-003.status-rv': ['TestAgentStoragePreviewImpact/status-rv'],
 'REG20-STORAGE-003.same-st-pod-roll': ['TestAgentStoragePreviewImpact/same-st-pod-roll'],
 'REG20-STORAGE-004.scaled-retain-deleted-delete': ['TestAgentStorageIndependentOwnerPolicy/scaled-retain-deleted-delete'],
 'REG20-STORAGE-004.delete-with-st-owner': ['TestAgentStorageIndependentOwnerPolicy/delete-with-st-owner'],
 'REG20-STORAGE-005.pending-suspend': ['TestAgentStoragePendingDeleteBlocksRestore/suspend/Delete',
                                       'TestAgentStoragePendingDeleteBlocksRestore/suspend/Retain'],
 'REG20-STORAGE-005.pending-shard': ['TestAgentStoragePendingDeleteBlocksRestore/shard/Delete',
                                     'TestAgentStoragePendingDeleteBlocksRestore/shard/Retain'],
 'REG19-STORAGE-SCOPE-001': ['TestReviewStorageScope/identity-failure'],
 'REG19-STORAGE-SCOPE-002': ['TestReviewStorageScope/pvc-success'],
 'REG19-HTTP-DELETE-001': ['TestReviewDeleteRoute/draft',
                           'TestReviewDeleteRoute/confirmed',
                           'TestReviewDeleteRoute/missing-preview',
                           'TestReviewDeleteRoute/stale-impact',
                           'TestReviewDeleteRoute/replaced-uid',
                           'TestReviewDeleteRoute/other-owner',
                           'TestReviewDeleteRoute/idempotent'],
 'REG19-HTTP-CONTINUE-PUBLISH-001': ['TestReviewContinueRoute/PUBLISH/unsent',
                                     'TestReviewContinueRoute/PUBLISH/wrong-revision',
                                     'TestReviewContinueRoute/PUBLISH/other-owner',
                                     'TestReviewContinueRoute/PUBLISH/possibly-sent'],
 'REG19-HTTP-CONTINUE-DELETE-001': ['TestReviewContinueRoute/DELETE/unsent',
                                    'TestReviewContinueRoute/DELETE/wrong-revision',
                                    'TestReviewContinueRoute/DELETE/other-owner',
                                    'TestReviewContinueRoute/DELETE/possibly-sent',
                                    'TestReviewContinueRoute/DELETE/stale-preview'],
 'REG19-HTTP-CONTINUE-CREDENTIAL-001': ['TestReviewCredentialReplayCurrent/CONTINUE/SUCCEEDED/current-state',
                                        'TestReviewCredentialReplayCurrent/CONTINUE/SUCCEEDED/unauthenticated',
                                        'TestReviewCredentialReplayCurrent/CONTINUE/SUCCEEDED/revoked-role',
                                        'TestReviewCredentialReplayCurrent/CONTINUE/SUCCEEDED/disabled',
                                        'TestReviewCredentialReplayCurrent/CONTINUE/SUCCEEDED/no-second-verification',
                                        'TestReviewCredentialReplayCurrent/CONTINUE/FAILED/current-state',
                                        'TestReviewCredentialReplayCurrent/CONTINUE/FAILED/unauthenticated',
                                        'TestReviewCredentialReplayCurrent/CONTINUE/FAILED/revoked-role',
                                        'TestReviewCredentialReplayCurrent/CONTINUE/FAILED/disabled',
                                        'TestReviewCredentialReplayCurrent/CONTINUE/FAILED/no-second-verification',
                                        'TestReviewCredentialReplayCurrent/CONTINUE/WAITING_CONFIRMATION/current-state',
                                        'TestReviewCredentialReplayCurrent/CONTINUE/WAITING_CONFIRMATION/unauthenticated',
                                        'TestReviewCredentialReplayCurrent/CONTINUE/WAITING_CONFIRMATION/revoked-role',
                                        'TestReviewCredentialReplayCurrent/CONTINUE/WAITING_CONFIRMATION/disabled',
                                        'TestReviewCredentialReplayCurrent/CONTINUE/WAITING_CONFIRMATION/no-second-verification'],
 'REG19-HTTP-CAPABILITIES-001': ['TestReviewCapabilitiesRoute/admin',
                                 'TestReviewCapabilitiesRoute/owner',
                                 'TestReviewCapabilitiesRoute/other-owner',
                                 'TestReviewCapabilitiesRoute/disabled',
                                 'TestReviewCapabilitiesRoute/unauthenticated',
                                 'TestReviewCapabilitiesRoute/missing'],
 'REG19-CREDENTIAL-REPLAY-001': ['TestReviewCredentialReplayCurrent/REGISTER/SUCCEEDED/current-state',
                                 'TestReviewCredentialReplayCurrent/REGISTER/SUCCEEDED/unauthenticated',
                                 'TestReviewCredentialReplayCurrent/REGISTER/SUCCEEDED/revoked-role',
                                 'TestReviewCredentialReplayCurrent/REGISTER/SUCCEEDED/disabled',
                                 'TestReviewCredentialReplayCurrent/REGISTER/SUCCEEDED/no-second-verification',
                                 'TestReviewCredentialReplayCurrent/REGISTER/SUCCEEDED/changed-content',
                                 'TestReviewCredentialReplayCurrent/REGISTER/FAILED/current-state',
                                 'TestReviewCredentialReplayCurrent/REGISTER/FAILED/unauthenticated',
                                 'TestReviewCredentialReplayCurrent/REGISTER/FAILED/revoked-role',
                                 'TestReviewCredentialReplayCurrent/REGISTER/FAILED/disabled',
                                 'TestReviewCredentialReplayCurrent/REGISTER/FAILED/no-second-verification',
                                 'TestReviewCredentialReplayCurrent/REGISTER/FAILED/changed-content',
                                 'TestReviewCredentialReplayCurrent/REGISTER/WAITING_CONFIRMATION/current-state',
                                 'TestReviewCredentialReplayCurrent/REGISTER/WAITING_CONFIRMATION/unauthenticated',
                                 'TestReviewCredentialReplayCurrent/REGISTER/WAITING_CONFIRMATION/revoked-role',
                                 'TestReviewCredentialReplayCurrent/REGISTER/WAITING_CONFIRMATION/disabled',
                                 'TestReviewCredentialReplayCurrent/REGISTER/WAITING_CONFIRMATION/no-second-verification',
                                 'TestReviewCredentialReplayCurrent/REGISTER/WAITING_CONFIRMATION/changed-content',
                                 'TestReviewCredentialReplayCurrent/CREDENTIAL/SUCCEEDED/current-state',
                                 'TestReviewCredentialReplayCurrent/CREDENTIAL/SUCCEEDED/unauthenticated',
                                 'TestReviewCredentialReplayCurrent/CREDENTIAL/SUCCEEDED/revoked-role',
                                 'TestReviewCredentialReplayCurrent/CREDENTIAL/SUCCEEDED/disabled',
                                 'TestReviewCredentialReplayCurrent/CREDENTIAL/SUCCEEDED/no-second-verification',
                                 'TestReviewCredentialReplayCurrent/CREDENTIAL/SUCCEEDED/changed-content',
                                 'TestReviewCredentialReplayCurrent/CREDENTIAL/FAILED/current-state',
                                 'TestReviewCredentialReplayCurrent/CREDENTIAL/FAILED/unauthenticated',
                                 'TestReviewCredentialReplayCurrent/CREDENTIAL/FAILED/revoked-role',
                                 'TestReviewCredentialReplayCurrent/CREDENTIAL/FAILED/disabled',
                                 'TestReviewCredentialReplayCurrent/CREDENTIAL/FAILED/no-second-verification',
                                 'TestReviewCredentialReplayCurrent/CREDENTIAL/FAILED/changed-content',
                                 'TestReviewCredentialReplayCurrent/CREDENTIAL/WAITING_CONFIRMATION/current-state',
                                 'TestReviewCredentialReplayCurrent/CREDENTIAL/WAITING_CONFIRMATION/unauthenticated',
                                 'TestReviewCredentialReplayCurrent/CREDENTIAL/WAITING_CONFIRMATION/revoked-role',
                                 'TestReviewCredentialReplayCurrent/CREDENTIAL/WAITING_CONFIRMATION/disabled',
                                 'TestReviewCredentialReplayCurrent/CREDENTIAL/WAITING_CONFIRMATION/no-second-verification',
                                 'TestReviewCredentialReplayCurrent/CREDENTIAL/WAITING_CONFIRMATION/changed-content',
                                 'TestReviewCredentialReplayCurrent/REACTIVATE/SUCCEEDED/current-state',
                                 'TestReviewCredentialReplayCurrent/REACTIVATE/SUCCEEDED/unauthenticated',
                                 'TestReviewCredentialReplayCurrent/REACTIVATE/SUCCEEDED/revoked-role',
                                 'TestReviewCredentialReplayCurrent/REACTIVATE/SUCCEEDED/disabled',
                                 'TestReviewCredentialReplayCurrent/REACTIVATE/SUCCEEDED/no-second-verification',
                                 'TestReviewCredentialReplayCurrent/REACTIVATE/SUCCEEDED/changed-content',
                                 'TestReviewCredentialReplayCurrent/REACTIVATE/FAILED/current-state',
                                 'TestReviewCredentialReplayCurrent/REACTIVATE/FAILED/unauthenticated',
                                 'TestReviewCredentialReplayCurrent/REACTIVATE/FAILED/revoked-role',
                                 'TestReviewCredentialReplayCurrent/REACTIVATE/FAILED/disabled',
                                 'TestReviewCredentialReplayCurrent/REACTIVATE/FAILED/no-second-verification',
                                 'TestReviewCredentialReplayCurrent/REACTIVATE/FAILED/changed-content',
                                 'TestReviewCredentialReplayCurrent/REACTIVATE/WAITING_CONFIRMATION/current-state',
                                 'TestReviewCredentialReplayCurrent/REACTIVATE/WAITING_CONFIRMATION/unauthenticated',
                                 'TestReviewCredentialReplayCurrent/REACTIVATE/WAITING_CONFIRMATION/revoked-role',
                                 'TestReviewCredentialReplayCurrent/REACTIVATE/WAITING_CONFIRMATION/disabled',
                                 'TestReviewCredentialReplayCurrent/REACTIVATE/WAITING_CONFIRMATION/no-second-verification',
                                 'TestReviewCredentialReplayCurrent/REACTIVATE/WAITING_CONFIRMATION/changed-content'],
 'REG19-CONTINUE-REPLAY-001': ['TestReviewCredentialReplayCurrent/CONTINUE/SUCCEEDED/current-state',
                               'TestReviewCredentialReplayCurrent/CONTINUE/SUCCEEDED/unauthenticated',
                               'TestReviewCredentialReplayCurrent/CONTINUE/SUCCEEDED/revoked-role',
                               'TestReviewCredentialReplayCurrent/CONTINUE/SUCCEEDED/disabled',
                               'TestReviewCredentialReplayCurrent/CONTINUE/SUCCEEDED/no-second-verification',
                               'TestReviewCredentialReplayCurrent/CONTINUE/FAILED/current-state',
                               'TestReviewCredentialReplayCurrent/CONTINUE/FAILED/unauthenticated',
                               'TestReviewCredentialReplayCurrent/CONTINUE/FAILED/revoked-role',
                               'TestReviewCredentialReplayCurrent/CONTINUE/FAILED/disabled',
                               'TestReviewCredentialReplayCurrent/CONTINUE/FAILED/no-second-verification',
                               'TestReviewCredentialReplayCurrent/CONTINUE/WAITING_CONFIRMATION/current-state',
                               'TestReviewCredentialReplayCurrent/CONTINUE/WAITING_CONFIRMATION/unauthenticated',
                               'TestReviewCredentialReplayCurrent/CONTINUE/WAITING_CONFIRMATION/revoked-role',
                               'TestReviewCredentialReplayCurrent/CONTINUE/WAITING_CONFIRMATION/disabled',
                               'TestReviewCredentialReplayCurrent/CONTINUE/WAITING_CONFIRMATION/no-second-verification'],
 'REG17-DELETE-001': ['TestDeletionCrossNamespaceOwnerUIDRemainsProtected'],
 'REG17-ACTIVITY-001': ['TestDeletionCompletionDoesNotChangeActivity'],
 'REG17-SHUTDOWN-001': ['TestShutdownClosesRPCAdmissionBeforeLauncherDrain'],
 'REG17-DELETE-002': ['TestDeletion404AfterPreflightKeepsSeenPodProtected'],
 'REG17-VERSION-001': ['TestF04LifecycleSchema5Upgrade/success'],
 'REG17-IDEMPOTENCY-001': ['TestP07RetryUsesFrozenVersionAndCurrentRevisions',
                           'TestP08CancellationCompetesWithPersistentDispatch/cancel-wins',
                           'TestP13RestartUsesPersistentSentBoundary/beforeDispatch'],
 'REG17-DELETE-003': ['TestDeletionPreviewBindsTargetAndImpact',
                      'TestDeletionPreviewSchema6UnsentCanContinue']}

RUNNERS = {'K10.name': 'ksm',
 'K10.collision': 'ksm',
 'K10.fixed-target': 'ksm',
 'K10.unsupported': 'ksm',
 'P04.draft-race': 'ksm',
 'P04.draft-after-preview': 'ksm',
 'P04.target-after-preview': 'ksm',
 'R03.ksm-suspended': 'ksm',
 'R03.ksm-disabled': 'ksm',
 'R03.ksm-no-match': 'ksm',
 'F02.primary': 'ksm',
 'F03.quantity': 'ksm',
 'F03.regexp': 'ksm',
 'F03.unknown-field': 'ksm',
 'F05.before-commit': 'ksm',
 'F05.after-commit': 'ksm',
 'I03.primary': 'ksm',
 'C04.ksm-crd': 'ksm',
 'C05.primary': 'ksm',
 'K02.primary': 'ksm',
 'K03.primary': 'ksm',
 'K04.primary': 'ksm',
 'K05.primary': 'ksm',
 'K06.node': 'ksm',
 'K06.cluster': 'ksm',
 'K06.unscheduled': 'ksm',
 'K07.default-all': 'ksm',
 'K07.default-allow': 'ksm',
 'K07.default-deny': 'ksm',
 'K07.include-all': 'ksm',
 'K07.include-allow': 'ksm',
 'K07.include-deny': 'ksm',
 'K08.all': 'ksm',
 'K08.union': 'ksm',
 'K08.dynamic-pattern': 'ksm',
 'K08.zero': 'ksm',
 'K09.primary': 'ksm',
 'P01.primary': 'ksm',
 'P02.primary': 'ksm',
 'P03.same': 'ksm',
 'P03.different-content': 'ksm',
 'P03.another-active': 'ksm',
 'P05.supported-drift': 'ksm',
 'P05.extra-spec': 'ksm',
 'P06.status-only': 'ksm',
 'P06.rv-only': 'ksm',
 'P06.default-equivalent': 'ksm',
 'P06.spec-change': 'ksm',
 'P09.response-drop': 'ksm',
 'P09.deadline': 'ksm',
 'U03.primary': 'ksm',
 'I06.primary': 'lifecycle',
 'P08.cancel-wins': 'lifecycle',
 'P08.dispatch-wins': 'lifecycle',
 'R03.deleted': 'lifecycle',
 'F04.success': 'lifecycle',
 'F04.failure': 'lifecycle',
 'T05.primary': 'lifecycle',
 'T08.primary': 'lifecycle',
 'I04.primary': 'lifecycle',
 'I05.primary': 'lifecycle',
 'C06.same-uid': 'lifecycle',
 'C07.primary': 'lifecycle',
 'K01.primary': 'lifecycle',
 'P07.primary': 'lifecycle',
 'P10.primary': 'lifecycle',
 'P11.primary': 'lifecycle',
 'P12.new-uid': 'lifecycle',
 'P12.missing': 'lifecycle',
 'P13.unsent': 'lifecycle',
 'P13.possibly-sent': 'lifecycle',
 'P14.finalizer': 'lifecycle',
 'P14.pod-remains': 'lifecycle',
 'P14.read-failure': 'lifecycle',
 'P14.delete-response-drop': 'lifecycle',
 'R03.agent-paused': 'agent',
 'F01.absent': 'agent',
 'F01.empty-object': 'agent',
 'F01.empty-list': 'agent',
 'F01.unknown-mode': 'agent',
 'F03.selector': 'agent',
 'F03.url': 'agent',
 'F03.tls-pair': 'agent',
 'REG18-SHARED-001': 'agent',
 'REG18-SHARED-002': 'agent',
 'REG18-SHARED-003': 'agent',
 'REG18-CAP-004': 'agent',
 'REG18-CAP-005': 'agent',
 'REG18-CAP-004.single-dash-namespace': 'agent',
 'REG18-CAP-004.single-dash-selector': 'agent',
 'REG18-CAP-004.override': 'agent',
 'REG18-CAP-004.old-rs': 'agent',
 'REG18-CAP-004.none-short-circuit': 'agent',
 'REG18-VALID-006': 'agent',
 'REG18-SOURCE-007': 'agent',
 'REG18-FIXED-008': 'agent',
 'REG18-HEADERS-009': 'agent',
 'REG18-CAP-010.positional': 'agent',
 'REG18-CAP-010.terminator': 'agent',
 'REG18-CAP-010.duplicate-scope': 'agent',
 'REG18-CAP-010.boolean-arity': 'agent',
 'REG18-CAP-010.known-scope-space': 'agent',
 'REG18-CAP-010.env-expansion': 'agent',
 'REG18-SELECTOR-012.exists-nil': 'agent',
 'REG18-SELECTOR-012.exists-empty': 'agent',
 'REG18-SELECTOR-012.not-exists-nil': 'agent',
 'REG18-SELECTOR-012.not-exists-empty': 'agent',
 'REG18-SELECTOR-012.in': 'agent',
 'REG18-SELECTOR-012.not-in': 'agent',
 'REG18-PROJECTION-013.url-parse-error': 'agent',
 'REG18-PROJECTION-013.proxy-parse-error': 'agent',
 'U02.primary': 'agent',
 'U06.primary': 'agent',
 'R01.intervals': 'workbench',
 'R01.freshness': 'workbench',
 'R01.early-error': 'workbench',
 'R03.missing-status': 'workbench',
 'R03.incomplete-status': 'workbench',
 'R05.page': 'workbench',
 'R05.item': 'workbench',
 'R06.primary': 'workbench',
 'REG19-ACTIVITY-001': 'workbench',
 'REG19-HTTP-I64-001': 'workbench',
 'REG19-SOURCE-001': 'workbench',
 'R02.primary': 'workbench',
 'R04.primary': 'workbench',
 'R07.pod': 'workbench',
 'R08.primary': 'workbench',
 'U01.primary': 'workbench',
 'U04.primary': 'workbench',
 'U05.primary': 'workbench',
 'R03.agent-zero': 'agent_storage',
 'REG20-STORAGE-002.transfer': 'agent_storage',
 'REG20-STORAGE-002.unregister': 'agent_storage',
 'REG20-STORAGE-003.st-uid-accept': 'agent_storage',
 'REG20-STORAGE-003.st-uid-dispatch': 'agent_storage',
 'REG20-STORAGE-003.pvc-uid-accept': 'agent_storage',
 'REG20-STORAGE-003.pvc-uid-dispatch': 'agent_storage',
 'REG20-STORAGE-003.policy-accept': 'agent_storage',
 'REG20-STORAGE-003.policy-dispatch': 'agent_storage',
 'REG20-STORAGE-003.status-rv': 'agent_storage',
 'REG20-STORAGE-003.same-st-pod-roll': 'agent_storage',
 'REG20-STORAGE-004.scaled-retain-deleted-delete': 'agent_storage',
 'REG20-STORAGE-004.delete-with-st-owner': 'agent_storage',
 'REG20-STORAGE-005.pending-suspend': 'agent_storage',
 'REG20-STORAGE-005.pending-shard': 'agent_storage',
 'REG20-STORAGE-001.orphan': 'agent_storage',
 'REG20-STORAGE-001.forbidden': 'agent_storage',
 'REG20-STORAGE-001.timeout': 'agent_storage',
 'REG20-STORAGE-001.dispatch-race': 'agent_storage',
 'REG20-STORAGE-001.new-name': 'agent_storage',
 'REG20-STORAGE-001.owned-retain-suspend': 'agent_storage',
 'REG20-STORAGE-001.owned-retain-shard': 'agent_storage',
 'A02.primary': 'agent_storage',
 'A06.primary': 'agent_storage',
 'A07.retain': 'agent_storage',
 'A07.delete': 'agent_storage',
 'A08.retain': 'agent_storage',
 'A08.delete': 'agent_storage',
 'A09.retain': 'agent_storage',
 'A09.delete': 'agent_storage',
 'A10.fixed-mode': 'agent_storage',
 'A10.new-instance': 'agent_storage',
 'P14.policy-pending': 'agent_storage',
 'REG19-STORAGE-SCOPE-001': 'review',
 'REG19-STORAGE-SCOPE-002': 'review',
 'REG19-HTTP-DELETE-001': 'review',
 'REG19-HTTP-CONTINUE-PUBLISH-001': 'review',
 'REG19-HTTP-CONTINUE-DELETE-001': 'review',
 'REG19-HTTP-CONTINUE-CREDENTIAL-001': 'review',
 'REG19-HTTP-CAPABILITIES-001': 'review',
 'REG19-CREDENTIAL-REPLAY-001': 'review',
 'REG19-CONTINUE-REPLAY-001': 'review',
 'REG19-AGENT-DELETE-OP-001': 'review',
 'REG19-AGENT-HISTORY-OP-001': 'review'}


def fingerprint(value):
    """固定输入/预期单独取指纹，不把runner或通过状态混入契约。"""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def contract(entry):
    return {k: entry[k] for k in ['caseId', 'variantId', 'layer', 'purpose', 'steps', 'expected', 'profiles']}


def _all(supplemental):
    raw = (ROOT / 'docs/acceptance/phase1.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_HASH:
        raise ValueError('固定manifest字节已变化')
    manifest = json.loads(raw)
    plans = json.loads((ROOT / 'docs/e2e/issue-21-plan-sources.json').read_text())
    entries = []
    if supplemental:
        rows = {}
        for line in (ROOT / 'docs/e2e/issue-21.md').read_text().splitlines():
            cells = [c.strip() for c in line.strip('|').split('|')]
            if len(cells) == 5 and cells[0].startswith('REG'):
                if cells[0] in rows:
                    raise ValueError('补充计划有重复ID')
                rows[cells[0]] = cells
        for item in plans['supplemental']['keys']:
            row = rows[item['variantId']]
            entries.append(dict(caseId=item['variantId'], variantId=item['variantId'],
                layer=item['layer'], profiles=['LOCAL_FULL'], owner=item['owner'],
                purpose=row[1], steps=row[2], expected=row[3]))
        if len(entries) != 104 or len({e['variantId'] for e in entries}) != 100:
            raise ValueError('补充计划必须精确100 ID/104层键')
    else:
        owners = {}
        for number in range(14, 21):
            path = ROOT / f'docs/e2e/issue-{number}.md'
            for line in path.read_text().splitlines():
                cells = [c.strip() for c in line.strip('|').split('|')]
                if len(cells) == 5 and re.fullmatch(r'[A-Z]\d+\.[^ ]+', cells[0]):
                    owners[cells[0]] = number
        for case in manifest['cases']:
            for variant in case['variants']:
                entries.append(dict(caseId=case['id'], variantId=variant['id'], layer=case['layer'],
                    profiles=case['profiles'], owner=owners.get(variant['id'], 21),
                    purpose=case['purpose'], steps=variant['steps'], expected=variant['expected']))
        for profile, count in PROFILES.items():
            actual = [e['variantId'] for e in entries if profile in e['profiles']]
            if len(actual) != count or actual != plans['fixed']['profiles'][profile]:
                raise ValueError('固定profile精确键集不符')
    keys = [(e['variantId'], e['layer']) for e in entries]
    if len(keys) != len(set(keys)):
        raise ValueError('注册表重复variant/layer')
    for e in entries:
        variant = e['variantId']
        selectors = (REG_GO_SELECTORS if supplemental else GO_SELECTORS).get(variant)
        e['runner'] = RUNNERS.get(variant, 'lifecycle' if variant.startswith('REG17-') else
            'agent' if variant.startswith('REG18-') else
            {14: 'run', 15: 'clusters', 16: 'ksm', 17: 'lifecycle', 18: 'agent',
             19: 'workbench', 20: 'agent_storage'}.get(e['owner'], 'run'))
        if e['layer'] != 'Q':
            selectors = ['U02.primary' if variant == 'REG18-PATTERN-011' else variant]
            if variant == 'I07.disabled-owner':
                selectors = ['I07.disabled-owner.' + mode for mode in ['register', 'transfer', 'reactivate']]
        elif selectors is None:
            if variant.startswith('REG21-VALIDATOR-011.'):
                selectors = ['gate_test.ValidatorTests.test_' + variant.split('.')[-1].replace('-', '_')]
                e['runner'] = 'gate'
            elif variant.startswith('REG21-EXPORT-012.'):
                selectors = ['exporter_test.ExporterTests.test_' + variant.split('.')[-1].replace('-', '_')]
                e['runner'] = 'exporter'
            elif variant.startswith('REG21-'):
                selectors = [{
                    'REG21-SIGNAL-001.lock-wait': 'protocol_test.ProtocolCLI.test_lock_wait_stops_before_setup',
                    'REG21-RESULT-007.before-first': 'protocol_test.ProtocolCLI.test_before_first_failure_keeps_all_keys',
                    'REG21-RESULT-007.after-first': 'protocol_test.ProtocolCLI.test_after_first_failure_keeps_remaining_not_run',
                }[variant]]
                e['runner'] = 'run'
            else:
                raise ValueError('Q缺实际selector: ' + variant)
        e['testSelectors'] = list(selectors)
        e['executionBoundary'] = 'validator-test' if variant.startswith('REG21-') and e['layer'] == 'Q' else ('host-Q' if e['layer'] == 'Q' else 'platform-pod')
        e['contractFingerprint'] = fingerprint(contract(e))
    return entries


def entries(profile, supplemental=False, focus=None):
    """先核精确计划；focus仅缩小诊断查询，不能改变完整门禁必选键。"""
    if profile not in PROFILES or supplemental and profile != 'LOCAL_FULL':
        raise ValueError('未知或不支持的profile')
    selected = [{**e, 'profile': profile} for e in _all(supplemental) if profile in e['profiles']]
    if focus is not None:
        valid = {e['variantId'] for e in selected}
        if not focus or len(focus) != len(set(focus)) or not set(focus) <= valid:
            raise ValueError('未知/重复/空或profile不符focus')
        selected = [e for e in selected if e['variantId'] in focus]
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, choices=PROFILES)
    parser.add_argument('--supplemental', action='store_true')
    parser.add_argument('--focus', nargs='+')
    args = parser.parse_args()
    try:
        print(json.dumps(entries(args.profile, args.supplemental, args.focus), ensure_ascii=False, indent=2))
    except ValueError:
        parser.error('注册表或focus无效')


if __name__ == '__main__':
    main()
