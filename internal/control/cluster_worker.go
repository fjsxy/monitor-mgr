package control

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"time"
)

func (s *Store) wakeWorker() {
	select {
	case s.workerWake <- struct{}{}:
	default:
	}
}
func (s *Store) StartWorkers() error {
	// Restart never silently resumes an accepted mutation. All candidates remain encrypted.
	if _, e := s.DB.Exec(`UPDATE operations SET state='WAITING_CONFIRMATION',message='平台已重启，等待管理员明确继续核验' WHERE state IN ('QUEUED','RUNNING')`); e != nil {
		return e
	}
	ctx, cancel := context.WithCancel(context.Background())
	s.workerCancel = cancel
	s.workerWake = make(chan struct{}, 1)
	s.workerDone = make(chan struct{})
	go func() {
		defer close(s.workerDone)
		tick := time.NewTicker(100 * time.Millisecond)
		defer tick.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-tick.C:
			case <-s.workerWake:
			}
			for ctx.Err() == nil {
				var id string
				if s.DB.QueryRowContext(ctx, `SELECT id FROM operations WHERE state='QUEUED' AND action IN ('REGISTER','CREDENTIAL','REACTIVATE') ORDER BY created_at,id LIMIT 1`).Scan(&id) != nil {
					break
				}
				s.verifyOperation(ctx, id)
			}
		}
	}()
	return nil
}
func (s *Store) stopWorkers() {
	if s.workerCancel != nil {
		s.workerCancel()
		<-s.workerDone
	}
}
func actorAdmin(ctx context.Context, tx *sql.Tx, id string) error {
	var role string
	var enabled bool
	var temporary bool
	if tx.QueryRowContext(ctx, `SELECT role,enabled,must_change FROM accounts WHERE id=?`, id).Scan(&role, &enabled, &temporary) != nil || !enabled || temporary || role != "ADMIN" {
		return Error("FORBIDDEN", "发起账号当前已无管理权限")
	}
	return nil
}
func (s *Store) verifyOperation(life context.Context, id string) {
	tx, e := s.DB.BeginTx(life, nil)
	if e != nil {
		return
	}
	o, e := scanOperation(tx.QueryRowContext(life, `SELECT `+operationColumns+` FROM operations WHERE id=? AND state='QUEUED'`, id))
	if e != nil {
		tx.Rollback()
		return
	}
	e = actorAdmin(life, tx, o.ActorId)
	if e != nil {
		tx.Rollback()
		s.finishVerification(life, o, nil, nil, e)
		return
	}
	var encrypted []byte
	if tx.QueryRowContext(life, `SELECT cipher FROM cluster_candidates WHERE operation_id=?`, id).Scan(&encrypted) != nil {
		tx.Rollback()
		s.finishVerification(life, o, nil, nil, Error("UNAVAILABLE", "候选凭据不可读"))
		return
	}
	if _, e = tx.ExecContext(life, `UPDATE operations SET state='RUNNING',message='正在核验 TLS、身份与集群 UID',updated_at=? WHERE id=?`, s.Now().UTC().Format(time.RFC3339Nano), id); e != nil {
		tx.Rollback()
		return
	}
	if tx.Commit() != nil {
		return
	}
	cred, e := s.openCredential(encrypted, id)
	var verified *verifiedTarget
	if e == nil {
		var target *Target
		target, e = newTarget(cred)
		if e == nil {
			bounded, cancel := context.WithTimeout(life, 8*time.Second)
			verified, e = target.verify(bounded)
			cancel()
			target.Close()
		}
	}
	s.finishVerification(life, o, cred, verified, e)
}
func (s *Store) finishVerification(life context.Context, o *rpc.Operation, cred *targetCredential, v *verifiedTarget, verifyErr error) {
	// Shutdown writes recovery state with an independent bounded context after transport cancellation.
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return
	}
	defer tx.Rollback()
	var state string
	if tx.QueryRowContext(ctx, `SELECT state FROM operations WHERE id=?`, o.OperationId).Scan(&state) != nil || state == "SUCCEEDED" || state == "FAILED" {
		return
	}
	now := s.Now().UTC().Format(time.RFC3339Nano)
	if life.Err() != nil {
		tx.ExecContext(ctx, `UPDATE operations SET state='WAITING_CONFIRMATION',message='平台退出中，等待管理员明确继续核验',updated_at=? WHERE id=?`, now, o.OperationId)
		tx.Commit()
		return
	}
	if e = actorAdmin(ctx, tx, o.ActorId); e != nil {
		verifyErr = e
	}
	var owner, name string
	if tx.QueryRowContext(ctx, `SELECT owner_id,name FROM operations WHERE id=?`, o.OperationId).Scan(&owner, &name) != nil {
		return
	}
	if verifyErr == nil && o.Action != "CREDENTIAL" {
		verifyErr = enabledOwner(ctx, tx, owner)
	}
	var current *rpc.Cluster
	if verifyErr == nil && o.Action != "REGISTER" {
		current, verifyErr = clusterByID(ctx, tx, o.ClusterId)
		if verifyErr == nil && current.Uid != v.uid {
			verifyErr = Error("CONFLICT", "候选属于不同集群 UID，请独立注册；原记录与历史保留")
		}
		if verifyErr == nil && ((o.Action == "REACTIVATE" && current.State != "ARCHIVED") || (o.Action == "CREDENTIAL" && current.State != "ACTIVE")) {
			verifyErr = Error("CONFLICT", "集群状态已变化")
		}
	}
	if verifyErr == nil && o.Action == "REGISTER" {
		var n int
		if tx.QueryRowContext(ctx, `SELECT count(*) FROM clusters WHERE uid=?`, v.uid).Scan(&n) != nil {
			return
		}
		if n > 0 {
			verifyErr = Error("CONFLICT", "同 UID 集群已登记，请更新凭据或重新启用原集群")
		}
	}
	revision := int64(1)
	if current != nil {
		revision = current.CredentialRevision + 1
	}
	if verifyErr == nil {
		cipher, e := s.sealCredential(cred, o.ClusterId)
		if e != nil {
			return
		}
		caps, _ := json.Marshal(v.capabilities)
		if o.Action == "REGISTER" {
			_, e = tx.ExecContext(ctx, `INSERT INTO clusters(id,name,owner_id,uid,identity,kubernetes_version,state,credential_revision,revision,capabilities,credential,created_at) VALUES(?,?,?,?,?,?,'ACTIVE',1,1,?,?,?)`, o.ClusterId, name, owner, v.uid, v.identity, v.version, caps, cipher, now)
		} else if o.Action == "CREDENTIAL" {
			_, e = tx.ExecContext(ctx, `UPDATE clusters SET identity=?,kubernetes_version=?,credential_revision=?,revision=revision+1,capabilities=?,credential=? WHERE id=?`, v.identity, v.version, revision, caps, cipher, o.ClusterId)
		} else {
			_, e = tx.ExecContext(ctx, `UPDATE clusters SET state='ACTIVE',owner_id=?,identity=?,kubernetes_version=?,credential_revision=?,revision=revision+1,capabilities=?,credential=? WHERE id=?`, owner, v.identity, v.version, revision, caps, cipher, o.ClusterId)
		}
		if e != nil {
			return
		}
	}
	message := "核验成功，凭据已生效；仅恢复读取，不自动下发"
	final := "SUCCEEDED"
	if verifyErr != nil {
		final = "FAILED"
		revision = 0
		message = "核验失败，原凭据与历史保持"
		var api *rpc.APIError
		if errors.As(verifyErr, &api) {
			message = api.Message
		}
	}
	if _, e = tx.ExecContext(ctx, `UPDATE operations SET state=?,message=?,updated_at=?,credential_revision=? WHERE id=?`, final, message, now, revision, o.OperationId); e != nil {
		return
	}
	if _, e = tx.ExecContext(ctx, `DELETE FROM cluster_candidates WHERE operation_id=?`, o.OperationId); e != nil {
		return
	}
	if e = audit(ctx, tx, o.ActorId, o.Action+"_"+final, o.ClusterId, s.Now()); e != nil {
		return
	}
	tx.Commit()
}
