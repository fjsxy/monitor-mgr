package control

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"golang.org/x/crypto/bcrypt"
	"time"
)

// Principal is read from the current account row, never from browser-provided roles.
// AuthVersion can be checked inside future mutation/dispatch transactions.
type Principal struct {
	Account     *rpc.Account
	AuthVersion int64
	ExpiresAt   string
}

func digest(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:])
}
func authorize(ctx context.Context, q interface {
	QueryRowContext(context.Context, string, ...any) *sql.Row
}, token string, now time.Time, allowTemporary bool) (*Principal, error) {
	if token == "" {
		return nil, Error("UNAUTHENTICATED", "请先登录")
	}
	a := new(rpc.Account)
	p := &Principal{Account: a}
	var enabled bool
	err := q.QueryRowContext(ctx, `SELECT a.id,a.username,a.role,a.enabled,a.must_change,a.created_at,a.auth_version,s.expires_at FROM sessions s JOIN accounts a ON a.id=s.account_id WHERE s.digest=? AND s.auth_version=a.auth_version`, digest(token)).Scan(&a.Id, &a.Username, &a.Role, &enabled, &a.MustChangePassword, &a.CreatedAt, &p.AuthVersion, &p.ExpiresAt)
	if err != nil || !enabled {
		return nil, Error("UNAUTHENTICATED", "会话无效或已到期")
	}
	a.Enabled = enabled
	expires, err := time.Parse(time.RFC3339Nano, p.ExpiresAt)
	if err != nil || !now.Before(expires) {
		return nil, Error("UNAUTHENTICATED", "会话无效或已到期")
	}
	if a.MustChangePassword && !allowTemporary {
		return nil, Error("PASSWORD_CHANGE_REQUIRED", "请先修改临时密码")
	}
	return p, nil
}
func (s *Store) Authorize(ctx context.Context, token string) (*Principal, error) {
	return authorize(ctx, s.DB, token, s.Now(), false)
}
func (s *Store) AuthorizeTx(ctx context.Context, tx *sql.Tx, token string) (*Principal, error) {
	return authorize(ctx, tx, token, s.Now(), false)
}
func token(c *rpc.RequestContext) string {
	if c == nil {
		return ""
	}
	return c.GetSessionToken()
}
func (s *Store) Login(ctx context.Context, _ *rpc.RequestContext, r *rpc.LoginRequest) (*rpc.LoginResult_, error) {
	a := new(rpc.Account)
	var hash []byte
	var version int64
	err := s.DB.QueryRowContext(ctx, `SELECT `+accountColumns+`,password_hash,auth_version FROM accounts WHERE username=?`, r.Username).Scan(&a.Id, &a.Username, &a.Role, &a.Enabled, &a.MustChangePassword, &a.CreatedAt, &hash, &version)
	// A fixed real bcrypt hash keeps unknown-account comparisons on the same expensive path.
	if err != nil {
		hash = []byte("$2a$10$L7sUQdVEfNpnBzKsaRo7oOmyJTLoxzdutn/dufIEbTCd.YQFAvmKO")
	}
	valid := bcrypt.CompareHashAndPassword(hash, []byte(r.Password)) == nil
	if err != nil || !valid || !a.Enabled {
		return nil, Error("UNAUTHENTICATED", "账号或密码无效")
	}
	t := ID()
	expires := s.Now().Add(s.SessionTTL).UTC().Format(time.RFC3339Nano)
	// Conditional insert closes the password/role/disable race between comparison and session creation.
	res, err := s.DB.ExecContext(ctx, `INSERT INTO sessions(digest,account_id,auth_version,expires_at) SELECT ?,id,auth_version,? FROM accounts WHERE id=? AND enabled=1 AND auth_version=?`, digest(t), expires, a.Id, version)
	if err != nil {
		return nil, Error("UNAVAILABLE", "登录暂不可用")
	}
	n, _ := res.RowsAffected()
	if n != 1 {
		return nil, Error("UNAUTHENTICATED", "账号或密码无效")
	}
	return &rpc.LoginResult_{Account: a, SessionToken: t, ExpiresAt: expires}, nil
}
func (s *Store) GetSession(ctx context.Context, c *rpc.RequestContext) (*rpc.SessionResult_, error) {
	p, err := authorize(ctx, s.DB, token(c), s.Now(), true)
	if err != nil {
		return nil, err
	}
	return &rpc.SessionResult_{Account: p.Account, ExpiresAt: p.ExpiresAt}, nil
}
func (s *Store) Logout(ctx context.Context, c *rpc.RequestContext) (*rpc.Empty, error) {
	if _, err := s.DB.ExecContext(ctx, `DELETE FROM sessions WHERE digest=?`, digest(token(c))); err != nil {
		return nil, Error("UNAVAILABLE", "退出暂不可用")
	}
	return &rpc.Empty{}, nil
}
func (s *Store) ChangePassword(ctx context.Context, c *rpc.RequestContext, r *rpc.ChangePasswordRequest) (*rpc.Empty, error) {
	if err := validPassword(r.NewPassword_); err != nil {
		return nil, err
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(r.NewPassword_), bcrypt.DefaultCost)
	if err != nil {
		return nil, Error("INVALID_INPUT", "密码无效")
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, Error("UNAVAILABLE", "暂不可用")
	}
	defer tx.Rollback()
	p, err := authorize(ctx, tx, token(c), s.Now(), true)
	if err != nil {
		return nil, err
	}
	var oldHash []byte
	if err = tx.QueryRowContext(ctx, `SELECT password_hash FROM accounts WHERE id=?`, p.Account.Id).Scan(&oldHash); err != nil {
		return nil, Error("UNAVAILABLE", "暂不可用")
	}
	if bcrypt.CompareHashAndPassword(oldHash, []byte(r.CurrentPassword)) != nil {
		return nil, Error("UNAUTHENTICATED", "原密码无效")
	}
	if _, err = tx.ExecContext(ctx, `UPDATE accounts SET password_hash=?,must_change=0,auth_version=auth_version+1 WHERE id=?`, hash, p.Account.Id); err != nil {
		return nil, Error("UNAVAILABLE", "改密失败")
	}
	if err = audit(ctx, tx, p.Account.Id, "CHANGE_PASSWORD", p.Account.Id, s.Now()); err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, Error("UNAVAILABLE", "改密失败")
	}
	return &rpc.Empty{}, nil
}
func requireAdmin(p *Principal) error {
	if p.Account.Role != "ADMIN" {
		return Error("FORBIDDEN", "需要平台管理员权限")
	}
	return nil
}
func (s *Store) ListAccounts(ctx context.Context, c *rpc.RequestContext) (*rpc.AccountsResult_, error) {
	p, err := s.Authorize(ctx, token(c))
	if err != nil {
		return nil, err
	}
	if err = requireAdmin(p); err != nil {
		return nil, err
	}
	rows, err := s.DB.QueryContext(ctx, `SELECT `+accountColumns+` FROM accounts ORDER BY created_at,id`)
	if err != nil {
		return nil, Error("UNAVAILABLE", "读取失败")
	}
	defer rows.Close()
	accounts := make([]*rpc.Account, 0)
	for rows.Next() {
		a, err := scanAccount(rows)
		if err != nil {
			return nil, Error("UNAVAILABLE", "读取失败")
		}
		accounts = append(accounts, a)
	}
	if rows.Err() != nil {
		return nil, Error("UNAVAILABLE", "读取失败")
	}
	return &rpc.AccountsResult_{Accounts: accounts}, nil
}
func (s *Store) CreateAccount(ctx context.Context, c *rpc.RequestContext, r *rpc.CreateAccountRequest) (*rpc.Account, error) {
	if err := validCredentials(r.Username, r.Password); err != nil {
		return nil, err
	}
	if r.Role != "ADMIN" && r.Role != "USER" {
		return nil, Error("INVALID_INPUT", "角色无效")
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(r.Password), bcrypt.DefaultCost)
	if err != nil {
		return nil, Error("INVALID_INPUT", "密码无效")
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, Error("UNAVAILABLE", "暂不可用")
	}
	defer tx.Rollback()
	p, err := authorize(ctx, tx, token(c), s.Now(), false)
	if err != nil {
		return nil, err
	}
	if err = requireAdmin(p); err != nil {
		return nil, err
	}
	var replay rpc.Account
	cmd, hit, err := s.beginCommand(ctx, tx, p, c, "CREATE_ACCOUNT", "accounts", r, &replay)
	if err != nil {
		return nil, err
	}
	if hit {
		return &replay, nil
	}

	var n int
	if err = tx.QueryRowContext(ctx, `SELECT count(*) FROM accounts WHERE username=?`, r.Username).Scan(&n); err != nil {
		return nil, Error("UNAVAILABLE", "暂不可用")
	}
	if n != 0 {
		return nil, Error("CONFLICT", "账号已存在")
	}
	a := &rpc.Account{Id: ID(), Username: r.Username, Role: r.Role, Enabled: true, MustChangePassword: true, CreatedAt: s.Now().UTC().Format(time.RFC3339Nano)}
	if _, err = tx.ExecContext(ctx, `INSERT INTO accounts(id,username,password_hash,role,enabled,must_change,created_at) VALUES(?,?,?,?,1,1,?)`, a.Id, a.Username, hash, a.Role, a.CreatedAt); err != nil {
		return nil, Error("UNAVAILABLE", "创建失败")
	}
	if err = audit(ctx, tx, p.Account.Id, "CREATE_ACCOUNT", a.Id, s.Now()); err != nil {
		return nil, err
	}
	if err = s.finishCommand(ctx, tx, cmd, a); err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, Error("UNAVAILABLE", "创建失败")
	}
	return a, nil
}
func audit(ctx context.Context, tx *sql.Tx, actor, action, target string, now time.Time) error {
	_, err := tx.ExecContext(ctx, `INSERT INTO audit VALUES(?,?,?,?,?)`, ID(), actor, action, target, now.UTC().Format(time.RFC3339Nano))
	if err != nil {
		return Error("UNAVAILABLE", "审计保存失败")
	}
	return nil
}

func (s *Store) UpdateAccount(ctx context.Context, c *rpc.RequestContext, r *rpc.UpdateAccountRequest) (*rpc.Account, error) {
	if r.Role == nil && r.Enabled == nil {
		return nil, Error("INVALID_INPUT", "至少选择一个账号变更")
	}
	if r.Role != nil && *r.Role != "ADMIN" && *r.Role != "USER" {
		return nil, Error("INVALID_INPUT", "角色无效")
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, Error("UNAVAILABLE", "暂不可用")
	}
	defer tx.Rollback()
	p, err := authorize(ctx, tx, token(c), s.Now(), false)
	if err != nil {
		return nil, err
	}
	if err = requireAdmin(p); err != nil {
		return nil, err
	}
	var replay rpc.Account
	cmd, hit, err := s.beginCommand(ctx, tx, p, c, "UPDATE_ACCOUNT", r.Id, r, &replay)
	if err != nil {
		return nil, err
	}
	if hit {
		return &replay, nil
	}

	a, err := scanAccount(tx.QueryRowContext(ctx, `SELECT `+accountColumns+` FROM accounts WHERE id=?`, r.Id))
	if err == sql.ErrNoRows {
		return nil, Error("NOT_FOUND", "账号不存在")
	}
	if err != nil {
		return nil, Error("UNAVAILABLE", "读取失败")
	}
	role, enabled := a.Role, a.Enabled
	if r.Role != nil {
		role = *r.Role
	}
	if r.Enabled != nil {
		enabled = *r.Enabled
	}
	if a.Enabled && a.Role == "ADMIN" && (!enabled || role != "ADMIN") {
		var n int
		if err = tx.QueryRowContext(ctx, `SELECT count(*) FROM accounts WHERE enabled=1 AND role='ADMIN'`).Scan(&n); err != nil {
			return nil, Error("UNAVAILABLE", "读取失败")
		}
		if n <= 1 {
			return nil, Error("LAST_ADMIN", "至少保留一名启用的平台管理员")
		}
	}
	if role != a.Role || enabled != a.Enabled {
		if _, err = tx.ExecContext(ctx, `UPDATE accounts SET role=?,enabled=?,auth_version=auth_version+1 WHERE id=?`, role, enabled, a.Id); err != nil {
			return nil, Error("UNAVAILABLE", "更新失败")
		}
		if err = audit(ctx, tx, p.Account.Id, "UPDATE_ACCOUNT", a.Id, s.Now()); err != nil {
			return nil, err
		}
	}
	a.Role = role
	a.Enabled = enabled
	if err = s.finishCommand(ctx, tx, cmd, a); err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, Error("UNAVAILABLE", "更新失败")
	}
	return a, nil
}
func (s *Store) ResetPassword(ctx context.Context, c *rpc.RequestContext, r *rpc.ResetPasswordRequest) (*rpc.Empty, error) {
	if err := validPassword(r.Password); err != nil {
		return nil, err
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(r.Password), bcrypt.DefaultCost)
	if err != nil {
		return nil, Error("INVALID_INPUT", "密码无效")
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, Error("UNAVAILABLE", "暂不可用")
	}
	defer tx.Rollback()
	p, err := authorize(ctx, tx, token(c), s.Now(), false)
	if err != nil {
		return nil, err
	}
	if err = requireAdmin(p); err != nil {
		return nil, err
	}
	var replay rpc.Empty
	cmd, hit, err := s.beginCommand(ctx, tx, p, c, "RESET_PASSWORD", r.Id, r, &replay)
	if err != nil {
		return nil, err
	}
	if hit {
		return &replay, nil
	}

	res, err := tx.ExecContext(ctx, `UPDATE accounts SET password_hash=?,must_change=1,auth_version=auth_version+1 WHERE id=?`, hash, r.Id)
	if err != nil {
		return nil, Error("UNAVAILABLE", "重置失败")
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		return nil, Error("NOT_FOUND", "账号不存在")
	}
	if err = audit(ctx, tx, p.Account.Id, "RESET_PASSWORD", r.Id, s.Now()); err != nil {
		return nil, err
	}
	if err = s.finishCommand(ctx, tx, cmd, &rpc.Empty{}); err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, Error("UNAVAILABLE", "重置失败")
	}
	return &rpc.Empty{}, nil
}
