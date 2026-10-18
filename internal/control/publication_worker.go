// 发布执行使用应用生命周期；持久化派发资格后仅发送一次写请求，运行就绪另行观察。
package control

import (
	"context"
	"database/sql"
	"errors"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"log/slog"
	"net/http/httptrace"
	"time"
)

func (s *Store) launchPublications(ctx context.Context) {
	for ctx.Err() == nil && !s.stopping.Load() {
		var id string
		if s.DB.QueryRowContext(ctx, `SELECT id FROM operations WHERE action IN ('PUBLISH','DELETE') AND state='QUEUED' ORDER BY created_at,id LIMIT 1`).Scan(&id) != nil {
			return
		}
		res, e := s.DB.ExecContext(ctx, `UPDATE operations SET state='CHECKING',message='正在核验当前权限与目标基线' WHERE id=? AND state='QUEUED'`, id)
		if e != nil {
			return
		}
		n, _ := res.RowsAffected()
		if n == 0 {
			continue
		}
		s.publicationWG.Add(1)
		go func() {
			defer s.publicationWG.Done()
			var action string
			s.DB.QueryRowContext(ctx, `SELECT action FROM operations WHERE id=?`, id).Scan(&action)
			if action == "DELETE" {
				s.executeDeletion(ctx, id)
			} else {
				s.executePublication(ctx, id)
			}
		}()
	}
}
func actorCluster(ctx context.Context, tx *sql.Tx, actor, cluster string) (*rpc.Cluster, error) {
	var a rpc.Account
	if tx.QueryRowContext(ctx, `SELECT id,role,enabled,must_change FROM accounts WHERE id=?`, actor).Scan(&a.Id, &a.Role, &a.Enabled, &a.MustChangePassword) != nil || !a.Enabled || a.MustChangePassword {
		return nil, Error("FORBIDDEN", "发起账号当前无操作权限")
	}
	cl, e := clusterByID(ctx, tx, cluster)
	if e != nil {
		return nil, e
	}
	if e = clusterPermission(&Principal{Account: &a}, cl); e != nil {
		return nil, e
	}
	if cl.State != "ACTIVE" {
		return nil, Error("CONFLICT", "集群已注销")
	}
	return cl, nil
}
func (s *Store) targetForActor(ctx context.Context, actor, cluster string) (*Target, *rpc.Cluster, error) {
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return nil, nil, e
	}
	defer tx.Rollback()
	cl, e := actorCluster(ctx, tx, actor, cluster)
	if e != nil {
		return nil, nil, e
	}
	var cipher []byte
	if tx.QueryRowContext(ctx, `SELECT credential FROM clusters WHERE id=? AND state='ACTIVE'`, cluster).Scan(&cipher) != nil {
		return nil, nil, Error("UNAVAILABLE", "当前凭据不可读")
	}
	cred, e := s.openCredential(cipher, cluster)
	if e != nil {
		return nil, nil, e
	}
	t, e := newTarget(cred)
	if e != nil {
		return nil, nil, e
	}
	t.limits = s.limits
	t.clusterID = cluster
	return t, cl, nil
}
func (s *Store) executePublication(life context.Context, id string) {
	// 只读预检不占派发保护，避免目标读取阻塞账号撤权；最终派发事务会重新授权。
	if s.OperationBoundary != nil {
		if s.OperationBoundary("beforeDispatch", id) != nil {
			return
		}
	}
	o, e := scanOperation(s.DB.QueryRowContext(life, `SELECT `+operationColumns+` FROM operations WHERE id=? AND state='CHECKING'`, id))
	if e != nil {
		return
	}
	s.enrichPublicationOperation(life, s.DB, o)
	i, e := instanceByID(life, s.DB, o.GetInstanceId())
	if e != nil {
		s.finishExecution(life, o, "FAILED", e, nil)
		return
	}
	v, e := versionByID(life, s.DB, o.GetVersionId(), i.Id)
	if e != nil {
		s.finishExecution(life, o, "FAILED", e, nil)
		return
	}
	cfg, e := decodeVersionConfiguration(i.Component, v)
	if e != nil {
		s.finishExecution(life, o, "FAILED", e, nil)
		return
	}
	frozen, e := frozenVersionSpec(life, s.DB, o.GetVersionId(), i.Id)
	if e != nil {
		s.finishExecution(life, o, "FAILED", e, nil)
		return
	}
	var previewID string
	var overwrite bool
	if s.DB.QueryRowContext(life, `SELECT preview_id,overwrite_drift FROM publication_operations WHERE operation_id=?`, id).Scan(&previewID, &overwrite) != nil {
		return
	}
	b, e := readPreview(life, s.DB, previewID, i.Id)
	if e != nil {
		s.finishExecution(life, o, "FAILED", e, nil)
		return
	}
	a, _, e := lifecycleAttempt(life, s.DB, id)
	if e != nil {
		return
	}
	t, cl, e := s.targetForActor(life, a.GetActorId(), i.ClusterId)
	if e != nil {
		s.finishExecution(life, o, "FAILED", e, nil)
		return
	}
	defer t.Close()
	if e = t.publicationDependencies(life, cl, cfg, i.Uid == "", frozen); e != nil {
		s.finishExecution(life, o, "FAILED", e, nil)
		return
	}
	cr, e := t.readComponent(life, i)
	if e != nil {
		s.finishExecution(life, o, "FAILED", e, nil)
		return
	}
	r := &rpc.PublishRequest{Id: i.Id, PreviewId: previewID, DraftRevision: v.SourceDraftRevision, OverwriteDrift: overwrite}
	if e = s.validatePublicationSpec(i, frozen, v.SourceDraftRevision, b, r, cr); e != nil {
		s.finishExecution(life, o, "FAILED", e, nil)
		return
	}
	// 关键：更新携带原UID与最新RV，防止覆盖并发写或同名替换；请求体只替换支持的spec，完整保留目标当前非平台 metadata/status/finalizers。
	body := cfg.document()
	body["spec"] = frozen
	method, path := "POST", componentFor(i.Component).collectionPath(instanceCRNamespace(i))
	if i.Uid != "" {
		body = cr
		body["spec"] = frozen
		method = "PUT"
		path = componentFor(i.Component).objectPath(i)
		if objectString(objectMap(body, "metadata"), "resourceVersion") == "" {
			s.finishExecution(life, o, "FAILED", Error("CONFLICT", "目标 RV 缺失，拒绝无条件更新"), nil)
			return
		}
	}
	releaseDispatch, e := s.markLifecycleDispatch(life, o, a.GetActorId(), cl.CredentialRevision)
	if e != nil {
		if apiCode(e) != "ALREADY_FINISHED" {
			s.finishExecution(life, o, "FAILED", e, nil)
		}
		return
	}
	defer releaseDispatch()
	if s.OperationBoundary != nil {
		if s.OperationBoundary("afterSendMarker", id) != nil {
			return
		}
	}
	slog.Info("目标API写请求", "operationId", id, "instanceId", i.Id, "clusterId", i.ClusterId, "method", method, "path", path)
	// 请求已写入transport后允许撤权；响应仍独立核实，不把原账号失权当成回滚。
	dispatchContext := httptrace.WithClientTrace(life, &httptrace.ClientTrace{WroteRequest: func(httptrace.WroteRequestInfo) { releaseDispatch() }})
	outcome := t.Mutate(dispatchContext, method, path, body)
	releaseDispatch()
	written, e := outcome.Object, outcome.Err
	if e != nil {
		state := "RESULT_UNCERTAIN"
		if outcome.Rejected {
			state = "FAILED"
		}
		if state == "RESULT_UNCERTAIN" && i.Uid == "" {
			state = "IDENTITY_UNCERTAIN"
		}
		s.finishExecution(life, o, state, e, nil)
		return
	}
	uid := objectString(objectMap(written, "metadata"), "uid")
	if uid == "" || (i.Uid != "" && uid != i.Uid) || componentFor(i.Component).normalizeSpec(objectMap(written, "spec")) != componentFor(i.Component).normalizeSpec(frozen) {
		state := "RESULT_UNCERTAIN"
		if i.Uid == "" {
			state = "IDENTITY_UNCERTAIN"
		}
		s.finishExecution(life, o, state, Error("CONFLICT", "写入响应不足以确认原身份与配置"), nil)
		return
	}
	s.finishExecution(life, o, "SUCCEEDED", nil, written)
	// 写入与运行分别表达；立即读取不将受理或意图当成已就绪。
	s.refreshObserved(life, i.Id, t)
}

// finishExecution 以实际 attempt 身份保存结果，退出时不能把未完成网络读取误报为失败。
func (s *Store) finishExecution(life context.Context, o *rpc.Operation, state string, cause error, cr map[string]any) {
	if (life.Err() != nil || s.stopping.Load()) && state != "SUCCEEDED" {
		a, _, e := lifecycleAttempt(context.Background(), s.DB, o.OperationId)
		if e == nil && a.AttemptId == o.GetAttemptId() {
			if a.PossiblySent {
				state = "RESULT_UNCERTAIN"
				if o.Action == "DELETE" {
					state = "DELETION_VERIFYING"
				}
				if o.Action == "PUBLISH" {
					var uid string
					s.DB.QueryRow(`SELECT uid FROM instances WHERE id=?`, o.GetInstanceId()).Scan(&uid)
					if uid == "" {
						state = "IDENTITY_UNCERTAIN"
					}
				}
			} else {
				state = "WAITING_CONFIRMATION"
			}
		}
	}
	s.finishPublication(o, state, cause, cr)
}

// finishPublication 只结束调用者持有的当前尝试，旧 worker 不能覆盖新尝试或取消结果。
func (s *Store) finishPublication(expected *rpc.Operation, state string, cause error, cr map[string]any) {
	id := expected.OperationId
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return
	}
	defer tx.Rollback()
	o, e := scanOperation(tx.QueryRowContext(ctx, `SELECT `+operationColumns+` FROM operations WHERE id=?`, id))
	if e != nil {
		return
	}
	s.enrichPublicationOperation(ctx, tx, o)
	s.enrichLifecycle(ctx, tx, o)
	if o.GetAttemptId() != expected.GetAttemptId() {
		return
	}
	if o.State == "SUCCEEDED" || o.State == "FAILED" || o.State == "CANCELLED" {
		return
	}
	now := s.Now().UTC().Format(time.RFC3339Nano)
	message := "CR 已写入；组件运行情况另行读取"
	if cause != nil {
		message = "执行失败"
		var a *rpc.APIError
		if errors.As(cause, &a) {
			message = a.Message
		}
	}
	if state == "RESULT_UNCERTAIN" {
		message = "结果待确认，只恢复核验，不重放写请求"
	}
	if state == "IDENTITY_UNCERTAIN" {
		message = "首次创建可能已发送，原 UID 未确认；禁止根据同名自动认领"
	}
	if _, e = tx.ExecContext(ctx, `UPDATE operations SET state=?,message=?,updated_at=? WHERE id=?`, state, message, now, id); e != nil {
		return
	}
	if _, e = tx.ExecContext(ctx, `UPDATE execution_attempts SET state=?,updated_at=?,revision=revision+1,message=? WHERE id=?`, state, now, message, o.GetAttemptId()); e != nil {
		return
	}
	if _, e = tx.ExecContext(ctx, `UPDATE operation_lifecycle SET revision=revision+1 WHERE operation_id=?`, id); e != nil {
		return
	}
	if state == "WAITING_CONFIRMATION" {
		tx.ExecContext(ctx, `UPDATE operations SET message=? WHERE id=?`, "平台退出或重启；明确未发送，等待继续或取消", id)
	}
	if state == "SUCCEEDED" {
		uid := objectString(objectMap(cr, "metadata"), "uid")
		if _, e = tx.ExecContext(ctx, `UPDATE instances SET uid=?,lifecycle='DEPLOYED',current_version_id=? WHERE id=?`, uid, o.GetVersionId(), o.GetInstanceId()); e != nil {
			return
		}
	}
	if state == "SUCCEEDED" || state == "FAILED" || state == "CANCELLED" {
		if _, e = tx.ExecContext(ctx, `DELETE FROM instance_protections WHERE instance_id=? AND operation_id=?`, o.GetInstanceId(), id); e != nil {
			return
		}
	}
	tx.Commit()
}
