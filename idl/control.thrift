namespace go control

exception APIError {
 1: required string code
 2: required string message
 3: optional map<string,string> fieldErrors
}
struct RequestContext {
 1: required string requestId
 2: optional string sessionToken
 3: optional string idempotencyKey
}
struct Account {
 1: required string id
 2: required string username
 3: required string role
 4: required bool enabled
 5: required bool mustChangePassword
 6: required string createdAt
}
struct InitializeRequest {
 1: required string username
 2: required string password
}
struct ProbeResult { 1: required bool initialized }
struct LoginRequest { 1: required string username, 2: required string password }
struct LoginResult { 1: required Account account, 2: required string sessionToken, 3: required string expiresAt }
struct SessionResult { 1: required Account account, 2: required string expiresAt }
struct ChangePasswordRequest { 1: required string currentPassword, 2: required string newPassword }
struct CreateAccountRequest { 1: required string username, 2: required string password, 3: required string role }
struct AccountsResult { 1: required list<Account> accounts }
struct UpdateAccountRequest { 1: required string id, 2: optional string role, 3: optional bool enabled }
struct ResetPasswordRequest { 1: required string id, 2: required string password }
struct KubeconfigRequest { 1: required string kubeconfig }
struct ContextsResult { 1: required list<string> contexts }
struct Empty {}
struct ClusterCapability { 1: required string component, 2: required bool allowed, 3: required string reason }
struct Cluster {
 1: required string id, 2: required string name, 3: required string ownerId,
 4: required string uid, 5: required string identity, 6: required string kubernetesVersion,
 7: required string state, 8: required i64 credentialRevision, 9: required i64 revision,
 10: required list<ClusterCapability> capabilities, 11: required string createdAt
}
struct ClustersResult { 1: required list<Cluster> clusters }
struct RegisterClusterRequest { 1: required string name, 2: required string ownerId, 3: required string context, 4: required string kubeconfig }
struct ClusterID { 1: required string id }
struct CredentialRequest { 1: required string id, 2: required string context, 3: required string kubeconfig, 4: optional string ownerId }
struct TransferClusterRequest { 1: required string id, 2: required string ownerId }
struct Operation {
 1: required string operationId, 2: required string action, 3: required string state,
 4: required string clusterId, 5: required string actorId, 6: required string message,
 7: required string createdAt, 8: required string updatedAt, 9: required i64 credentialRevision, 10: required string ownerId, 11: required string previousOwnerId, 12: optional string instanceId, 13: optional string versionId, 14: optional string attemptId, 15: optional i64 revision, 16: optional string storageResult, 17: optional list<VolumeObservation> storageVolumes
}
struct OperationID { 1: required string operationId }
struct OperationsResult { 1: required list<Operation> operations }
struct NamespacesResult { 1: required list<string> namespaces }
struct ReferenceMetadata { 1: required string kind, 2: required string name, 3: required string namespace, 4: required list<string> keys }
struct ReferencesRequest { 1: required string id, 2: required string namespace, 3: required string kind }
struct ReferencesResult { 1: required list<ReferenceMetadata> references }
struct ContainerResources { 1: optional string requestCPU, 2: optional string requestMemory, 3: optional string limitCPU, 4: optional string limitMemory }
struct CollectorSelection { 1: required string mode, 2: optional list<string> names }
struct MetricSelection { 1: required string mode, 2: optional list<string> patterns }
struct NamespaceSelection { 1: required string mode, 2: optional list<string> names, 3: optional list<string> namePatterns }
struct RoleResources { 1: required string mode, 2: optional ContainerResources resources }
struct KSMConfiguration {
 1: required string name, 2: required string targetNamespace, 3: required string deploymentMode,
 4: required string desiredState, 5: required ContainerResources resources,
 6: required CollectorSelection collectors, 7: required MetricSelection metricFilter, 8: required NamespaceSelection namespaceFilter,
 9: required bool advancedResources, 10: optional i32 shards,
 11: optional RoleResources nodeResources, 12: optional RoleResources clusterResources, 13: optional RoleResources unscheduledResources
}
// Agent配置仅表达平台支持字段；所有认证仅引用同namespace的既有Secret。
struct LabelExpression { 1: required string key, 2: required string operator, 3: optional list<string> values }
struct LabelSelector { 1: optional map<string,string> matchLabels, 2: optional list<LabelExpression> matchExpressions }
struct SelectorRule { 1: required string mode, 2: optional LabelSelector selector }
struct AssociationRule { 1: required SelectorRule object, 2: required SelectorRule namespace }
struct AgentAssociations { 1: required AssociationRule serviceMonitor, 2: required AssociationRule podMonitor, 3: required AssociationRule probe, 4: required AssociationRule scrapeConfig }
struct SecretKeyReference { 1: required string name, 2: required string key }
struct RemoteWriteAuth { 1: required string mode, 2: optional SecretKeyReference username, 3: optional SecretKeyReference password, 4: optional SecretKeyReference credentials }
struct AgentTLS { 1: optional SecretKeyReference ca, 2: optional SecretKeyReference cert, 3: optional SecretKeyReference key, 4: optional string serverName }
struct RemoteWriteEndpoint { 1: required string url, 2: optional string name, 3: optional string remoteTimeout, 4: required RemoteWriteAuth auth, 5: optional AgentTLS tls }
struct AgentStorage { 1: required string mode, 2: optional string capacity, 3: required string whenScaled, 4: required string whenDeleted }
struct AgentConfiguration { 1: required string name, 2: required string namespace, 3: required string desiredState, 4: required i32 shards, 5: required ContainerResources resources, 6: required AgentStorage storage, 7: required string serviceAccountName, 8: required AgentAssociations associations, 9: required list<RemoteWriteEndpoint> remoteWrite }
struct MatchedResource { 1: required string kind, 2: required string namespace, 3: required string name, 4: required string uid }
struct AgentStatefulPolicy { 1: required string name, 2: required string uid, 3: required string whenScaled, 4: required string whenDeleted }
struct VolumeObservation { 1: required string namespace, 2: required string name, 3: required string uid, 4: required string phase, 5: required string requestedStorage, 6: required list<string> accessModes, 7: required string storageClass, 8: required list<string> mountedByPods, 9: optional string expectedPolicy, 10: optional string retentionResult, 11: optional string lastAttemptAt, 12: optional string lastSuccessAt, 13: optional string error, 14: optional string statefulUID, 15: optional string statefulName, 16: optional i32 shard, 17: optional string agentUID, 18: optional string impactAction }
struct SourceObservation { 1: required string source, 2: required string lastAttemptAt, 3: required string lastSuccessAt, 4: required string error, 5: required bool historical, 6: optional list<MatchedResource> matches, 7: optional list<VolumeObservation> volumes, 8: optional InstanceRuntime runtimeSnapshot, 9: optional string uid, 10: optional bool stale, 11: optional list<AgentStatefulPolicy> policies, 12: optional string storageMode }
struct AssociationPreviewRequest { 1: required string id, 2: required i64 draftRevision }
struct AssociationPreview { 1: required i64 draftRevision, 2: required list<SourceObservation> sources }
struct InstanceID { 1: required string id }
struct CreateInstanceRequest { 1: required string clusterId, 2: required string component, 3: optional KSMConfiguration ksm, 4: optional AgentConfiguration agent }
struct SaveDraftRequest { 1: required string id, 2: required i64 expectedDraftRevision, 3: optional KSMConfiguration ksm, 4: optional AgentConfiguration agent }
struct ConfigurationDraft { 1: required i64 revision, 2: optional KSMConfiguration ksm, 3: required string yaml, 4: optional AgentConfiguration agent }
struct InstanceRuntime { 1: required string summary, 2: required string desiredState, 3: required string deploymentMode, 4: optional i32 availablePods, 5: optional i32 desiredPods, 6: required string lastSuccessAt, 7: required string error, 8: required string observedAt, 9: optional i32 shards, 10: optional bool paused }
struct ObservationInstance {
 1: required string id, 2: required string clusterId, 3: required string name, 4: required string targetNamespace,
 5: required string component, 6: required string type, 7: required bool readOnly, 8: required string uid,
 9: required string lifecycle, 10: required string currentVersionId, 11: required string activityAt, 12: optional InstanceRuntime runtime,
 13: required string ownership, 14: optional string yaml, 15: optional string crNamespace, 16: optional string scope, 17: optional list<SourceObservation> sources,
 // 创建事实只在服务端创建事务写入；canManage 是当前操作者的动态权限，不改变平台纳管归属。
 18: required string creatorUserId, 19: required string creatorRole, 20: required bool creatorIdentityKnown,
 21: required bool canManage, 22: required string readOnlyReason
}
struct InstancesResult { 1: required list<ObservationInstance> instances }
// 工作台查询以完整授权集合统计并在服务端过滤/分页；活动序号保持数据库内部i64。
struct QueryInstancesRequest { 1: required string clusterId, 2: optional string name, 3: optional string type, 4: optional string component, 5: optional string summary, 6: optional i32 page, 7: optional i32 pageSize }
struct InstanceStatistics { 1: required i32 managed, 2: required i32 unmanaged, 3: required i32 available, 4: required i32 blocked }
struct InstancePagination { 1: required i32 number, 2: required i32 size, 3: required i32 total, 4: required i32 pages }
struct QueryInstancesResult { 1: required list<ObservationInstance> instances, 2: required InstanceStatistics statistics, 3: required InstancePagination page }
struct PreviewRequest { 1: required string id, 2: required i64 draftRevision }
struct PublicationPreview { 1: required string previewId, 2: required i64 draftRevision, 3: required string leftYAML, 4: required string rightYAML, 5: required bool drift, 6: required list<string> warnings, 7: optional list<VolumeObservation> storageVolumes, 8: optional list<AgentStatefulPolicy> policies, 9: optional string storageMode }
struct PublishRequest { 1: required string id, 2: required string previewId, 3: required i64 draftRevision, 4: required bool overwriteDrift }
struct PublicationVersion { 1: required string versionId, 2: required string instanceId, 3: required i64 sourceDraftRevision, 4: optional KSMConfiguration ksm, 5: required string yaml, 6: required string createdAt, 7: optional AgentConfiguration agent }
struct VersionsResult { 1: required list<PublicationVersion> versions }
struct VersionRequest { 1: required string id, 2: required string versionId }
struct OperationLookup { 1: required string instanceId, 2: required string idempotencyKey }
struct ExecutionAttempt { 1: required string attemptId, 2: required string operationId, 3: required string state, 4: required bool possiblySent, 5: required string createdAt, 6: required string updatedAt, 7: optional i64 revision, 8: optional i64 sequence, 9: optional string actorId, 10: optional string message }
// 生命周期变更绑定当前操作与尝试修订，避免过期页面取消或重试较新的尝试。
struct LifecycleRequest { 1: required string operationId, 2: required i64 expectedOperationRevision, 3: required string expectedAttemptId, 4: required i64 expectedAttemptRevision, 5: optional string deletePreviewId }
struct DeleteInstanceRequest { 1: required string id, 2: required string versionId, 3: optional string previewId }
// 删除预览绑定原身份和影响语义；RV/status与同范围Pod滚动不改变用户确认范围。
struct DeletionPreviewRequest { 1: required string id, 2: optional string operationId }
struct DeletionPreview { 1: required string previewId, 2: required string instanceId, 3: required string versionId, 4: required string uid, 5: required string name, 6: required string namespace, 7: required list<string> impactNamespaces, 8: optional list<VolumeObservation> storageVolumes, 9: optional list<AgentStatefulPolicy> policies, 10: optional string storageMode }
struct AttemptsResult { 1: required list<ExecutionAttempt> attempts }
service ControlService {
 AssociationPreview PreviewAssociations(1: RequestContext ctx, 2: AssociationPreviewRequest request) throws (1: APIError error)
 DeletionPreview PreviewDeletion(1: RequestContext ctx, 2: DeletionPreviewRequest request) throws (1: APIError error)
 Operation RetryLifecycle(1: RequestContext ctx, 2: LifecycleRequest request) throws (1: APIError error)
 Operation CancelLifecycle(1: RequestContext ctx, 2: LifecycleRequest request) throws (1: APIError error)
 Operation ContinueLifecycle(1: RequestContext ctx, 2: LifecycleRequest request) throws (1: APIError error)
 Operation RefreshOperation(1: RequestContext ctx, 2: OperationID request) throws (1: APIError error)
 Operation DeleteInstance(1: RequestContext ctx, 2: DeleteInstanceRequest request) throws (1: APIError error)
 ObservationInstance CreateInstance(1: RequestContext ctx, 2: CreateInstanceRequest request) throws (1: APIError error)
 QueryInstancesResult QueryInstances(1: RequestContext ctx, 2: QueryInstancesRequest request) throws (1: APIError error)
 InstancesResult ListInstances(1: RequestContext ctx, 2: ClusterID request) throws (1: APIError error)
 ObservationInstance GetInstance(1: RequestContext ctx, 2: InstanceID request) throws (1: APIError error)
 ConfigurationDraft GetDraft(1: RequestContext ctx, 2: InstanceID request) throws (1: APIError error)
 ConfigurationDraft SaveDraft(1: RequestContext ctx, 2: SaveDraftRequest request) throws (1: APIError error)
 ConfigurationDraft CopyTemplate(1: RequestContext ctx, 2: InstanceID request) throws (1: APIError error)
 Empty RecordEditingActivity(1: RequestContext ctx, 2: InstanceID request) throws (1: APIError error)
 PublicationPreview PreviewPublication(1: RequestContext ctx, 2: PreviewRequest request) throws (1: APIError error)
 Operation PublishConfiguration(1: RequestContext ctx, 2: PublishRequest request) throws (1: APIError error)
 VersionsResult ListVersions(1: RequestContext ctx, 2: InstanceID request) throws (1: APIError error)
 PublicationVersion GetVersion(1: RequestContext ctx, 2: VersionRequest request) throws (1: APIError error)
 InstanceRuntime GetRuntime(1: RequestContext ctx, 2: InstanceID request) throws (1: APIError error)
 InstanceRuntime RefreshInstance(1: RequestContext ctx, 2: InstanceID request) throws (1: APIError error)
 Empty DeleteDraft(1: RequestContext ctx, 2: InstanceID request) throws (1: APIError error)
 OperationsResult LookupOperations(1: RequestContext ctx, 2: OperationLookup request) throws (1: APIError error)
 AttemptsResult GetAttempts(1: RequestContext ctx, 2: OperationID request) throws (1: APIError error)

 ContextsResult ListKubeconfigContexts(1: RequestContext ctx, 2: KubeconfigRequest request) throws (1: APIError error)
 ClustersResult ListClusters(1: RequestContext ctx) throws (1: APIError error)
 Cluster GetCluster(1: RequestContext ctx, 2: ClusterID request) throws (1: APIError error)
 Operation RegisterCluster(1: RequestContext ctx, 2: RegisterClusterRequest request) throws (1: APIError error)
 Operation UpdateCredential(1: RequestContext ctx, 2: CredentialRequest request) throws (1: APIError error)
 Operation ReactivateCluster(1: RequestContext ctx, 2: CredentialRequest request) throws (1: APIError error)
 Cluster TransferCluster(1: RequestContext ctx, 2: TransferClusterRequest request) throws (1: APIError error)
 Cluster UnregisterCluster(1: RequestContext ctx, 2: ClusterID request) throws (1: APIError error)
 Operation GetOperation(1: RequestContext ctx, 2: OperationID request) throws (1: APIError error)
 Operation ContinueOperation(1: RequestContext ctx, 2: OperationID request) throws (1: APIError error)
 OperationsResult ListClusterOperations(1: RequestContext ctx, 2: ClusterID request) throws (1: APIError error)
 NamespacesResult ListNamespaces(1: RequestContext ctx, 2: ClusterID request) throws (1: APIError error)
 ReferencesResult ListReferences(1: RequestContext ctx, 2: ReferencesRequest request) throws (1: APIError error)

 ProbeResult Probe(1: RequestContext ctx) throws (1: APIError error)
 Account Initialize(1: RequestContext ctx, 2: InitializeRequest request) throws (1: APIError error)
 LoginResult Login(1: RequestContext ctx, 2: LoginRequest request) throws (1: APIError error)
 SessionResult GetSession(1: RequestContext ctx) throws (1: APIError error)
 Empty Logout(1: RequestContext ctx) throws (1: APIError error)
 Empty ChangePassword(1: RequestContext ctx, 2: ChangePasswordRequest request) throws (1: APIError error)
 AccountsResult ListAccounts(1: RequestContext ctx) throws (1: APIError error)
 Account CreateAccount(1: RequestContext ctx, 2: CreateAccountRequest request) throws (1: APIError error)
 Account UpdateAccount(1: RequestContext ctx, 2: UpdateAccountRequest request) throws (1: APIError error)
 Empty ResetPassword(1: RequestContext ctx, 2: ResetPasswordRequest request) throws (1: APIError error)
}
