namespace go control

exception APIError {
 1: required string code
 2: required string message
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
 7: required string createdAt, 8: required string updatedAt, 9: required i64 credentialRevision, 10: required string ownerId, 11: required string previousOwnerId
}
struct OperationID { 1: required string operationId }
struct OperationsResult { 1: required list<Operation> operations }
struct NamespacesResult { 1: required list<string> namespaces }
struct ReferenceMetadata { 1: required string kind, 2: required string name, 3: required string namespace, 4: required list<string> keys }
struct ReferencesRequest { 1: required string id, 2: required string namespace, 3: required string kind }
struct ReferencesResult { 1: required list<ReferenceMetadata> references }
service ControlService {
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
