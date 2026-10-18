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
struct Empty {}
service ControlService {
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
