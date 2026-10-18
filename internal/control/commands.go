package control

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"time"
)

type command struct{ actor, action, target, key, digest string }

// beginCommand 按当前操作者、动作、目标和请求键查找已经受理的命令。
// 关键：调用者必须先在同一事务中完成当前授权；幂等命中不能绕过失权检查。
// 含密码或凭据的请求只保存独立主密钥计算的 HMAC，不保存原文或普通可枚举摘要。
func (s *Store) beginCommand(ctx context.Context, tx *sql.Tx, p *Principal, c *rpc.RequestContext, action, target string, request any, result any) (*command, bool, error) {
	key := c.GetIdempotencyKey()
	if len(key) == 0 || len(key) > 128 {
		return nil, false, Error("INVALID_INPUT", "业务变更需要 1–128 字节 Idempotency-Key")
	}
	for _, ch := range []byte(key) {
		if ch < 33 || ch > 126 {
			return nil, false, Error("INVALID_INPUT", "Idempotency-Key 必须为可打印 ASCII")
		}
	}
	normalized, err := json.Marshal(request)
	if err != nil {
		return nil, false, Error("INVALID_INPUT", "请求无法规范化")
	}
	mac := hmac.New(sha256.New, s.Key)
	mac.Write(normalized)
	cmd := &command{actor: p.Account.Id, action: action, target: target, key: key, digest: hex.EncodeToString(mac.Sum(nil))}
	var previous string
	var raw []byte
	err = tx.QueryRowContext(ctx, `SELECT request_digest,result_json FROM command_results WHERE actor_id=? AND action=? AND target_id=? AND command_key=?`, cmd.actor, cmd.action, cmd.target, cmd.key).Scan(&previous, &raw)
	if err == sql.ErrNoRows {
		return cmd, false, nil
	}
	if err != nil {
		return nil, false, Error("UNAVAILABLE", "幂等结果暂不可用")
	}
	if !hmac.Equal([]byte(previous), []byte(cmd.digest)) {
		return nil, false, Error("CONFLICT", "相同 Idempotency-Key 的请求内容不同")
	}
	if json.Unmarshal(raw, result) != nil {
		return nil, false, Error("UNAVAILABLE", "幂等结果无法读取")
	}
	return cmd, true, nil
}

// finishCommand 保存可安全回放的结果，由调用者与业务变更共同提交。
// 关键：保持同一事务，防止业务已执行但去重结果缺失而造成重复副作用。
func (s *Store) finishCommand(ctx context.Context, tx *sql.Tx, cmd *command, result any) error {
	raw, err := json.Marshal(result)
	if err != nil {
		return Error("UNAVAILABLE", "幂等结果无法保存")
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO command_results(actor_id,action,target_id,command_key,request_digest,result_json,created_at) VALUES(?,?,?,?,?,?,?)`, cmd.actor, cmd.action, cmd.target, cmd.key, cmd.digest, raw, s.Now().UTC().Format(time.RFC3339Nano))
	if err != nil {
		return Error("UNAVAILABLE", "幂等结果无法保存")
	}
	return nil
}
