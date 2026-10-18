// 生命周期命令只改变持久意图；重试保留固定版本，取消与派发由同一事务状态竞争。
package control

import (
	"context"
	"database/sql"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"sync"
	"time"
)

// lifecycleSchema 追加在组件共享迁移之后；已发布版本的语句保持不可变。
var lifecycleSchema = []string{
	`ALTER TABLE execution_attempts ADD COLUMN revision INTEGER NOT NULL DEFAULT 1`,
	`ALTER TABLE execution_attempts ADD COLUMN sequence INTEGER NOT NULL DEFAULT 1`,
	`ALTER TABLE execution_attempts ADD COLUMN actor_id TEXT NOT NULL DEFAULT ''`,
	`ALTER TABLE execution_attempts ADD COLUMN message TEXT NOT NULL DEFAULT ''`,
	`UPDATE execution_attempts SET actor_id=(SELECT actor_id FROM operations WHERE id=operation_id)`,
	`CREATE TABLE operation_lifecycle(operation_id TEXT PRIMARY KEY REFERENCES operations(id),current_attempt_id TEXT NOT NULL REFERENCES execution_attempts(id),revision INTEGER NOT NULL,next_sequence INTEGER NOT NULL)`,
	`INSERT INTO operation_lifecycle SELECT operation_id,id,1,2 FROM execution_attempts`,
	`CREATE TABLE deletion_operations(operation_id TEXT PRIMARY KEY REFERENCES operations(id),instance_id TEXT NOT NULL REFERENCES instances(id),version_id TEXT NOT NULL REFERENCES publication_versions(id),original_uid TEXT NOT NULL,cr_path TEXT NOT NULL,target_namespace TEXT NOT NULL)`,
	`CREATE TABLE seen_resources(instance_id TEXT NOT NULL REFERENCES instances(id),api_version TEXT NOT NULL,kind TEXT NOT NULL,namespace TEXT NOT NULL,name TEXT NOT NULL,uid TEXT NOT NULL,owner_uid TEXT NOT NULL,path TEXT NOT NULL,PRIMARY KEY(instance_id,uid))`,
}

// lifecycleAttempt 从显式当前指针读取尝试，不能依赖相同 clock 下的随机 ID 排序。
func lifecycleAttempt(ctx context.Context, q clusterQuery, id string) (*rpc.ExecutionAttempt, int64, error) {
	a := new(rpc.ExecutionAttempt)
	var revision int64
	e := q.QueryRowContext(ctx, `SELECT a.id,a.operation_id,a.state,a.possibly_sent,a.created_at,a.updated_at,a.revision,a.sequence,a.actor_id,a.message,l.revision FROM operation_lifecycle l JOIN execution_attempts a ON a.id=l.current_attempt_id WHERE l.operation_id=?`, id).Scan(&a.AttemptId, &a.OperationId, &a.State, &a.PossiblySent, &a.CreatedAt, &a.UpdatedAt, &a.Revision, &a.Sequence, &a.ActorId, &a.Message, &revision)
	if e != nil {
		return nil, 0, Error("CONFLICT", "操作没有可变更的生命周期尝试")
	}
	return a, revision, nil
}

// enrichLifecycle 将修订和当前尝试回填到公共操作，不修改原始发起者。
func (s *Store) enrichLifecycle(ctx context.Context, q clusterQuery, o *rpc.Operation) {
	a, rev, e := lifecycleAttempt(ctx, q, o.OperationId)
	if e != nil {
		return
	}
	o.Revision = reference(rev)
	o.AttemptId = &a.AttemptId
	var instance, version string
	if q.QueryRowContext(ctx, `SELECT instance_id,version_id FROM deletion_operations WHERE operation_id=?`, o.OperationId).Scan(&instance, &version) == nil {
		o.InstanceId = &instance
		o.VersionId = &version
		var component string
		if q.QueryRowContext(ctx, `SELECT component FROM instances WHERE id=?`, instance).Scan(&component) == nil {
			result := "UNVERIFIED"
			if component == KSMComponent {
				result = "NOT_APPLICABLE"
			}
			o.StorageResult_ = &result
		}
	}
}

// lifecycleCommand 在当前授权和幂等命中之后核对两个修订，旧页面不能操作较新的尝试。
func (s *Store) lifecycleCommand(ctx context.Context, c *rpc.RequestContext, r *rpc.LifecycleRequest, action string) (*rpc.Operation, error) {
	return s.lifecycleCommandObserved(ctx, c, r, action, nil)
}

// lifecycleCommandObserved 在锁外读取删除影响，回到事务再次核对当前授权、修订和幂等。
func (s *Store) lifecycleCommandObserved(ctx context.Context, c *rpc.RequestContext, r *rpc.LifecycleRequest, action string, snapshot *deletionSnapshot) (*rpc.Operation, error) {
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return nil, e
	}
	defer tx.Rollback()
	o, e := scanOperation(tx.QueryRowContext(ctx, `SELECT `+operationColumns+` FROM operations WHERE id=?`, r.OperationId))
	if e != nil {
		return nil, Error("NOT_FOUND", "操作不存在")
	}
	s.enrichPublicationOperation(ctx, tx, o)
	s.enrichLifecycle(ctx, tx, o)
	if o.Action != "PUBLISH" && o.Action != "DELETE" {
		return nil, Error("CONFLICT", "此入口仅支持实例发布和删除")
	}
	p, i, e := s.instanceTx(ctx, tx, c, o.GetInstanceId())
	if e != nil {
		return nil, e
	}
	var replay rpc.Operation
	cmd, hit, e := s.beginCommand(ctx, tx, p, c, action, o.OperationId, r, &replay)
	if e != nil {
		return nil, e
	}
	if hit {
		// 关键：幂等记录只找回操作身份；状态和当前尝试必须在本事务回读，不能重放受理快照。
		current, readErr := scanOperation(tx.QueryRowContext(ctx, `SELECT `+operationColumns+` FROM operations WHERE id=?`, replay.OperationId))
		if readErr == nil {
			s.enrichPublicationOperation(ctx, tx, current)
			s.enrichLifecycle(ctx, tx, current)
		}
		return current, readErr
	}
	a, rev, e := lifecycleAttempt(ctx, tx, o.OperationId)
	if e != nil {
		return nil, e
	}
	if rev != r.ExpectedOperationRevision || a.AttemptId != r.ExpectedAttemptId || a.GetRevision() != r.ExpectedAttemptRevision {
		return nil, Error("CONFLICT", "操作或尝试已变化，请刷新后重试")
	}
	now := s.Now().UTC().Format(time.RFC3339Nano)
	if action == "CANCEL_LIFECYCLE" {
		// 关键：派发标记先提交即可能已发送，之后任何取消都不能宣称集群写入被撤销。
		if a.PossiblySent || (o.State != "QUEUED" && o.State != "CHECKING" && o.State != "WAITING_CONFIRMATION") {
			return nil, Error("CONFLICT", "可能已发送或已结束，不能取消；请读取核验结果")
		}
		o.State = "CANCELLED"
		o.Message = "已取消，未发送集群写请求"
		if _, e = tx.ExecContext(ctx, `UPDATE execution_attempts SET state='CANCELLED',revision=revision+1,message=?,updated_at=? WHERE id=? AND possibly_sent=0`, o.Message, now, a.AttemptId); e != nil {
			return nil, e
		}
		if _, e = tx.ExecContext(ctx, `DELETE FROM instance_protections WHERE instance_id=? AND operation_id=?`, i.Id, o.OperationId); e != nil {
			return nil, e
		}
	} else {
		if action == "RETRY_LIFECYCLE" && o.State != "FAILED" {
			return nil, Error("CONFLICT", "只有确定失败才能普通重试；待确认结果只能读取核验")
		}
		if action == "CONTINUE_LIFECYCLE" && (o.State != "WAITING_CONFIRMATION" || a.PossiblySent) {
			return nil, Error("CONFLICT", "仅明确未发送的重启任务可继续")
		}
		if i.Lifecycle == "DELETED" {
			return nil, Error("CONFLICT", "实例已删除")
		}
		if o.Action == "DELETE" {
			if r.GetDeletePreviewId() == "" {
				return nil, Error("INVALID_INPUT", "继续或重试删除前必须重新预览确认")
			}
			if snapshot == nil {
				tx.Rollback()
				observation, readErr := s.observeDeletionForUser(ctx, c, i)
				if readErr != nil {
					return nil, readErr
				}
				return s.lifecycleCommandObserved(ctx, c, r, action, observation)
			}
			if e = s.validateDeletionPreview(ctx, tx, i, r.GetDeletePreviewId(), o.GetVersionId(), o.OperationId, snapshot); e != nil {
				return nil, e
			}
			if _, e = tx.ExecContext(ctx, `UPDATE deletion_operations SET preview_id=? WHERE operation_id=?`, r.GetDeletePreviewId(), o.OperationId); e != nil {
				return nil, e
			}
		}
		var protection string
		e = tx.QueryRowContext(ctx, `SELECT operation_id FROM instance_protections WHERE instance_id=?`, i.Id).Scan(&protection)
		if e != nil && e != sql.ErrNoRows {
			return nil, e
		}
		if e == nil && protection != o.OperationId {
			return nil, Error("CONFLICT", "实例有另一未结束变更")
		}
		var seq int64
		if e = tx.QueryRowContext(ctx, `SELECT next_sequence FROM operation_lifecycle WHERE operation_id=?`, o.OperationId).Scan(&seq); e != nil {
			return nil, e
		}
		attempt := ID()
		o.AttemptId = &attempt
		o.State = "QUEUED"
		o.Message = "已受理新尝试，仍使用原固定版本"
		if _, e = tx.ExecContext(ctx, `INSERT INTO execution_attempts(id,operation_id,state,possibly_sent,created_at,updated_at,revision,sequence,actor_id,message) VALUES(?,?,'QUEUED',0,?,?,1,?,?,?)`, attempt, o.OperationId, now, now, seq, p.Account.Id, o.Message); e != nil {
			return nil, e
		}
		if _, e = tx.ExecContext(ctx, `UPDATE operation_lifecycle SET current_attempt_id=?,next_sequence=next_sequence+1 WHERE operation_id=?`, attempt, o.OperationId); e != nil {
			return nil, e
		}
		if _, e = tx.ExecContext(ctx, `INSERT INTO instance_protections VALUES(?,?) ON CONFLICT(instance_id) DO UPDATE SET operation_id=excluded.operation_id`, i.Id, o.OperationId); e != nil {
			return nil, e
		}
	}
	o.Revision = reference(rev + 1)
	o.UpdatedAt = now
	if _, e = tx.ExecContext(ctx, `UPDATE operations SET state=?,message=?,updated_at=? WHERE id=?`, o.State, o.Message, now, o.OperationId); e != nil {
		return nil, e
	}
	if _, e = tx.ExecContext(ctx, `UPDATE operation_lifecycle SET revision=revision+1 WHERE operation_id=?`, o.OperationId); e != nil {
		return nil, e
	}
	if _, e = tx.ExecContext(ctx, `UPDATE instances SET activity_at=? WHERE id=?`, now, i.Id); e != nil {
		return nil, e
	}
	if e = s.finishCommand(ctx, tx, cmd, o); e != nil {
		return nil, e
	}
	if e = audit(ctx, tx, p.Account.Id, action, i.Id, s.Now()); e != nil {
		return nil, e
	}
	if e = tx.Commit(); e != nil {
		return nil, Error("UNAVAILABLE", "生命周期命令提交失败")
	}
	s.wakeWorker()
	return o, nil
}

// RetryLifecycle 明确创建同操作、同版本的新尝试，失败尝试保留在历史中。
func (s *Store) RetryLifecycle(ctx context.Context, c *rpc.RequestContext, r *rpc.LifecycleRequest) (*rpc.Operation, error) {
	return s.lifecycleCommand(ctx, c, r, "RETRY_LIFECYCLE")
}

// CancelLifecycle 原子争用尚未发出的派发资格。
func (s *Store) CancelLifecycle(ctx context.Context, c *rpc.RequestContext, r *rpc.LifecycleRequest) (*rpc.Operation, error) {
	return s.lifecycleCommand(ctx, c, r, "CANCEL_LIFECYCLE")
}

// ContinueLifecycle 只恢复明确未发出的任务，不能用作不确定写入的重放入口。
func (s *Store) ContinueLifecycle(ctx context.Context, c *rpc.RequestContext, r *rpc.LifecycleRequest) (*rpc.Operation, error) {
	return s.lifecycleCommand(ctx, c, r, "CONTINUE_LIFECYCLE")
}

// markLifecycleDispatch 与撤权、转移、凭据切换共用读写保护，持久资格提交就是可能已发送边界。
func (s *Store) markLifecycleDispatch(ctx context.Context, o *rpc.Operation, actor string, credentialRevision int64) (func(), error) {
	if s.stopping.Load() {
		return nil, Error("UNAVAILABLE", "平台退出中，未取得派发资格")
	}
	s.dispatch.RLock()
	release := sync.OnceFunc(s.dispatch.RUnlock)
	fail := func(e error) (func(), error) { release(); return nil, e }
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return fail(e)
	}
	defer tx.Rollback()
	cl, e := actorCluster(ctx, tx, actor, o.ClusterId)
	if e != nil {
		return fail(e)
	}
	if cl.CredentialRevision != credentialRevision {
		return fail(Error("CONFLICT", "预检期间凭据已变化，请重新核验"))
	}
	if s.stopping.Load() {
		return fail(Error("UNAVAILABLE", "平台退出中，停止派发"))
	}
	if o.Action == "PUBLISH" {
		instance, err := instanceByID(ctx, tx, o.GetInstanceId())
		if err != nil {
			return fail(err)
		}
		frozen, err := frozenVersionSpec(ctx, tx, o.GetVersionId(), instance.Id)
		if err != nil {
			return fail(err)
		}
		// 关键：真正取得派发资格时再次读取固定基线，旧预览和在途草稿不能绕过容量约束。
		if err = validateFixedSpec(ctx, tx, instance, frozen); err != nil {
			return fail(err)
		}
	}
	if e = ctx.Err(); e != nil {
		return fail(e)
	}
	var attempt string
	if tx.QueryRowContext(ctx, `SELECT current_attempt_id FROM operation_lifecycle WHERE operation_id=?`, o.OperationId).Scan(&attempt) != nil || attempt != o.GetAttemptId() {
		return fail(Error("ALREADY_FINISHED", "当前尝试已变化"))
	}
	now := s.Now().UTC().Format(time.RFC3339Nano)
	result, e := tx.ExecContext(ctx, `UPDATE operations SET state='DISPATCHED',message='已取得派发资格，结果尚未确认',updated_at=? WHERE id=? AND state='CHECKING'`, now, o.OperationId)
	if e != nil {
		return fail(e)
	}
	n, _ := result.RowsAffected()
	if n != 1 {
		return fail(Error("ALREADY_FINISHED", "取消或另一状态已胜出"))
	}
	if _, e = tx.ExecContext(ctx, `UPDATE execution_attempts SET state='DISPATCHED',possibly_sent=1,revision=revision+1,updated_at=? WHERE id=?`, now, attempt); e != nil {
		return fail(e)
	}
	if _, e = tx.ExecContext(ctx, `UPDATE operation_lifecycle SET revision=revision+1 WHERE operation_id=?`, o.OperationId); e != nil {
		return fail(e)
	}
	if e = tx.Commit(); e != nil {
		return fail(e)
	}
	return release, nil
}

// reference 为兼容扩展字段保留显式值与缺省的区别。
func reference[T any](value T) *T { return &value }
