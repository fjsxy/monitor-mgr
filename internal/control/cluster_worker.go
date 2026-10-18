// 本文件在应用生命周期内执行持久化的集群凭据核验，不绑定原 HTTP 请求或 Cookie 寿命。
package control

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"time"
)

// wakeWorker 合并唤醒信号，通知后台检查已持久化的排队操作。
func (s *Store) wakeWorker() {
	select {
	case s.workerWake <- struct{}{}:
	default:
	}
}

// StartWorkers 建立后台核验循环，并先将重启遗留操作转为等待明确继续。
func (s *Store) StartWorkers() error {
	// 关键：重启不能重放已受理变更；未发等待人工继续，可能已发保持不确定保护。
	if _, e := s.DB.Exec(`UPDATE operations SET state='WAITING_CONFIRMATION',message='平台已重启，等待管理员明确继续核验' WHERE state IN ('QUEUED','RUNNING','CHECKING')`); e != nil {
		return e
	}
	if _, e := s.DB.Exec(`UPDATE operations SET state=CASE WHEN EXISTS(SELECT 1 FROM publication_operations p JOIN instances i ON i.id=p.instance_id WHERE p.operation_id=operations.id AND i.uid='') THEN 'IDENTITY_UNCERTAIN' ELSE 'RESULT_UNCERTAIN' END,message='平台已重启，只恢复读取核验，不自动重放' WHERE action='PUBLISH' AND state='DISPATCHED'`); e != nil {
		return e
	}
	if _, e := s.DB.Exec(`UPDATE operations SET state='DELETION_VERIFYING',message='平台已重启，只恢复原UID清理核验，不重发删除' WHERE action='DELETE' AND state='DISPATCHED'`); e != nil {
		return e
	}
	if _, e := s.DB.Exec(`UPDATE execution_attempts SET state=(SELECT state FROM operations WHERE id=operation_id),revision=revision+1 WHERE id IN (SELECT current_attempt_id FROM operation_lifecycle) AND state IN ('QUEUED','CHECKING','DISPATCHED')`); e != nil {
		return e
	}
	ctx, cancel := context.WithCancel(context.Background())
	s.workerCancel = cancel
	s.workerWake = make(chan struct{}, 1)
	s.workerDone = make(chan struct{})
	go func() {
		defer close(s.workerDone)
		defer s.publicationWG.Wait()
		tick := time.NewTicker(100 * time.Millisecond)
		defer tick.Stop()
		recoveryTick := time.NewTicker(time.Second)
		defer recoveryTick.Stop()
		for {
			recovering := false
			select {
			case <-ctx.Done():
				return
			case <-recoveryTick.C:
				recovering = true
			case <-tick.C:
			case <-s.workerWake:
			}
			// 关键：退出与新增goroutine共用锁，Wait开始后不再增加任务，数据库必须最后关闭。
			s.workerLaunch.Lock()
			if s.OperationBoundary != nil {
				s.OperationBoundary("beforeLaunch", "")
			}
			if s.stopping.Load() {
				s.workerLaunch.Unlock()
				continue
			}
			if recovering {
				s.launchRecovery(ctx)
			} else {
				s.launchPublications(ctx)
				var id string
				if s.DB.QueryRowContext(ctx, `SELECT id FROM operations WHERE state='QUEUED' AND action IN ('REGISTER','CREDENTIAL','REACTIVATE') ORDER BY created_at,id LIMIT 1`).Scan(&id) == nil {
					s.publicationWG.Add(1)
					go func() { defer s.publicationWG.Done(); s.verifyOperation(ctx, id) }()
				}
			}
			s.workerLaunch.Unlock()
		}
	}()
	return nil
}

// StopWorkers 使用默认10秒排空预算；超时只取消本地等待，持久结果仍按可能发送边界保存。
func (s *Store) StopWorkers() {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	s.StopWorkersContext(ctx)
}

// StopWorkersContext 先停止受理/派发，再排空已运行任务，超时后保存不确定状态并等待落盘。
func (s *Store) StopWorkersContext(ctx context.Context) {
	// 关键：受理资格先原子关闭，不能等launcher/数据库锁，否则直接RPC可在退出起点后插入新任务。
	s.stopping.Store(true)
	s.stopMu.Lock()
	defer s.stopMu.Unlock()
	if s.workerCancel == nil {
		return
	}
	s.workerLaunch.Lock()
	s.workerLaunch.Unlock()
	drained := make(chan struct{})
	go func() { s.publicationWG.Wait(); close(drained) }()
	select {
	case <-drained:
	case <-ctx.Done():
	}
	s.workerCancel()
	<-s.workerDone
}

// actorAdmin 按账号当前启用、改密及角色状态核验发起者，不依赖原 Session。
func actorAdmin(ctx context.Context, tx *sql.Tx, id string) error {
	var role string
	var enabled bool
	var temporary bool
	if tx.QueryRowContext(ctx, `SELECT role,enabled,must_change FROM accounts WHERE id=?`, id).Scan(&role, &enabled, &temporary) != nil || !enabled || temporary || role != "ADMIN" {
		return Error("FORBIDDEN", "发起账号当前已无管理权限")
	}
	return nil
}

// verifyOperation 领取已受理候选，在当前授权后执行有预算的 TLS/身份/UID 核验。
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
	// 关键：派发前重新读取发起账号权限，受理时的授权不能支持失权后的新访问。
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
			target.limits = s.limits
			target.clusterID = o.ClusterId
			bounded, cancel := context.WithTimeout(life, 8*time.Second)
			verified, e = target.verify(bounded)
			cancel()
			target.Close()
		}
	}
	s.finishVerification(life, o, cred, verified, e)
}

// finishVerification 在事务内保存验证结果，仅成功且身份一致时切换有效凭据。
func (s *Store) finishVerification(life context.Context, o *rpc.Operation, cred *targetCredential, v *verifiedTarget, verifyErr error) {
	s.dispatch.Lock()
	defer s.dispatch.Unlock()
	// 关键：网络取消后仍用独立限时事务保存等待状态，退出不能把未核实结果误报失败或完成。
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
	if life.Err() != nil || s.stopping.Load() {
		tx.ExecContext(ctx, `UPDATE operations SET state='WAITING_CONFIRMATION',message='平台退出中，等待管理员明确继续核验',updated_at=? WHERE id=?`, now, o.OperationId)
		tx.Commit()
		return
	}
	// 关键：核验期间权限可能变化，切换事务必须再次检查发起账号和指定归属。
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
		// 关键：候选 UID 必须匹配原记录，避免把旧配置和历史继承给重建后的另一集群。
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
	// 关键：只有核验成功才原子切换当前密文；失败保留原凭据，恢复读取也不重放其他写入。
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
