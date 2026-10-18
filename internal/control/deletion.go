// 删除固定原CR身份与部署版本；Foreground请求和完整owner UID图核验共用持久生命周期。
package control

import (
	"context"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"net/http/httptrace"
	"net/url"
	"time"
)

// DeleteInstance 只受理已确认原UID的实例；真正删除不绑定HTTP请求或Cookie寿命。
func (s *Store) DeleteInstance(ctx context.Context, c *rpc.RequestContext, r *rpc.DeleteInstanceRequest) (*rpc.Operation, error) {
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return nil, e
	}
	defer tx.Rollback()
	p, i, e := s.instanceTx(ctx, tx, c, r.Id)
	if e != nil {
		return nil, e
	}
	var replay rpc.Operation
	cmd, hit, e := s.beginCommand(ctx, tx, p, c, "DELETE_INSTANCE", i.Id, r, &replay)
	if e != nil {
		return nil, e
	}
	if hit {
		o, e := scanOperation(tx.QueryRowContext(ctx, `SELECT `+operationColumns+` FROM operations WHERE id=?`, replay.OperationId))
		if e == nil {
			s.enrichLifecycle(ctx, tx, o)
		}
		return o, e
	}
	if i.Lifecycle == "DELETED" || i.Uid == "" || i.CurrentVersionId == "" {
		return nil, Error("CONFLICT", "没有确认的原 CR UID；未部署草稿使用删除草稿，身份不明不能删除")
	}
	if r.VersionId != i.CurrentVersionId {
		return nil, Error("CONFLICT", "部署版本已变化，请重新确认名称和命名空间")
	}
	var n int
	if tx.QueryRowContext(ctx, `SELECT count(*) FROM instance_protections WHERE instance_id=?`, i.Id).Scan(&n) != nil {
		return nil, Error("UNAVAILABLE", "实例保护不可读")
	}
	if n > 0 {
		return nil, Error("CONFLICT", "实例仍有未结束变更")
	}
	v, e := versionByID(ctx, tx, i.CurrentVersionId, i.Id)
	if e != nil {
		return nil, e
	}
	cfg, e := decodeVersionConfiguration(i.Component, v)
	if e != nil {
		return nil, e
	}
	// Agent有持久存储时必须由专属策略门禁证明安全，不能借通用删除绕过后续存储契约。
	if e = componentFor(i.Component).deletionGuard(cfg); e != nil {
		return nil, e
	}
	now := s.Now().UTC().Format(time.RFC3339Nano)
	attempt := ID()
	o := &rpc.Operation{OperationId: ID(), Action: "DELETE", State: "QUEUED", ClusterId: i.ClusterId, ActorId: p.Account.Id, OwnerId: p.Account.Id, InstanceId: &i.Id, VersionId: &v.VersionId, AttemptId: &attempt, Revision: reference(int64(1)), Message: "已受理原 UID Foreground 删除，等待核验", CreatedAt: now, UpdatedAt: now}
	for _, q := range []struct {
		sql  string
		args []any
	}{
		{`INSERT INTO operations(id,action,state,cluster_id,actor_id,owner_id,name,message,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)`, []any{o.OperationId, o.Action, o.State, o.ClusterId, o.ActorId, o.OwnerId, i.Name, o.Message, now, now}},
		{`INSERT INTO execution_attempts(id,operation_id,state,possibly_sent,created_at,updated_at,actor_id) VALUES(?,?,'QUEUED',0,?,?,?)`, []any{attempt, o.OperationId, now, now, p.Account.Id}},
		{`INSERT INTO operation_lifecycle VALUES(?,?,1,2)`, []any{o.OperationId, attempt}},
		{`INSERT INTO deletion_operations VALUES(?,?,?,?,?,?)`, []any{o.OperationId, i.Id, v.VersionId, i.Uid, componentFor(i.Component).objectPath(i), cfg.identity().DeploymentNamespace}},
		{`INSERT INTO instance_protections VALUES(?,?)`, []any{i.Id, o.OperationId}},
		{`UPDATE instances SET activity_at=? WHERE id=?`, []any{now, i.Id}},
	} {
		if _, e = tx.ExecContext(ctx, q.sql, q.args...); e != nil {
			return nil, Error("UNAVAILABLE", "删除受理事务失败")
		}
	}
	if e = s.finishCommand(ctx, tx, cmd, o); e != nil {
		return nil, e
	}
	if e = audit(ctx, tx, p.Account.Id, "DELETE_ACCEPTED", i.Id, s.Now()); e != nil {
		return nil, e
	}
	if tx.Commit() != nil {
		return nil, Error("UNAVAILABLE", "删除受理未提交")
	}
	s.wakeWorker()
	return o, nil
}

// deletionGuard 供存储契约增加类型化验证；当前不允许Agent持久存储被通用入口直接删除。
func (a componentAdapter) deletionGuard(cfg configuration) error {
	if a.component == AgentComponent {
		if _, has := cfg.spec()["storage"]; has {
			return Error("CONFLICT", "Agent 持久存储删除需要核验保留策略")
		}
	}
	return nil
}

type seenResource struct{ apiVersion, kind, namespace, name, uid, ownerUID, path string }

// ownerUID 只接受controller ownerReference，名称或普通label不能建立所有权。
func ownerUID(meta map[string]any) string {
	refs, _ := meta["ownerReferences"].([]any)
	for _, raw := range refs {
		ref, _ := raw.(map[string]any)
		if ref["controller"] == true {
			return objectString(ref, "uid")
		}
	}
	return ""
}

// listOwnedCandidates 分页读取全部工作负载和Pod，任何缺页/失败都不能当作资源已经消失。
func (t *Target) listOwnedCandidates(ctx context.Context, namespace string) ([]seenResource, error) {
	out := []seenResource{}
	for _, g := range []struct{ api, kind, resource string }{{"apps/v1", "Deployment", "deployments"}, {"apps/v1", "StatefulSet", "statefulsets"}, {"apps/v1", "DaemonSet", "daemonsets"}, {"apps/v1", "ReplicaSet", "replicasets"}, {"v1", "Pod", "pods"}} {
		base := "/apis/" + g.api
		if g.api == "v1" {
			base = "/api/v1"
		}
		path := base + "/namespaces/" + url.PathEscape(namespace) + "/" + g.resource
		next := ""
		tokens := map[string]bool{}
		for {
			var list struct {
				Items    []map[string]any `json:"items"`
				Metadata struct {
					Continue string `json:"continue"`
				} `json:"metadata"`
			}
			q := path + "?limit=200"
			if next != "" {
				q += "&continue=" + url.QueryEscape(next)
			}
			if e := t.Request(ctx, "GET", q, nil, &list); e != nil {
				return nil, e
			}
			if list.Items == nil {
				return nil, Error("UNAVAILABLE", "辅助资源列表不完整")
			}
			for _, obj := range list.Items {
				m := objectMap(obj, "metadata")
				uid, name := objectString(m, "uid"), objectString(m, "name")
				if uid == "" || name == "" {
					return nil, Error("UNAVAILABLE", "辅助资源身份不完整")
				}
				out = append(out, seenResource{g.api, g.kind, namespace, name, uid, ownerUID(m), path + "/" + url.PathEscape(name)})
			}
			next = list.Metadata.Continue
			if next == "" {
				break
			}
			if tokens[next] {
				return nil, Error("UNAVAILABLE", "辅助资源分页未前进")
			}
			tokens[next] = true
		}
	}
	return out, nil
}

// observeOwnerGraph 保存见过的所有者UID链；父对象消失后仍能识别残留ReplicaSet/Pod。
func (s *Store) observeOwnerGraph(ctx context.Context, i *rpc.ObservationInstance, t *Target, namespace string) (int, error) {
	candidates, e := t.listOwnedCandidates(ctx, namespace)
	if e != nil {
		return 0, e
	}
	rows, e := s.DB.QueryContext(ctx, `SELECT uid FROM seen_resources WHERE instance_id=?`, i.Id)
	if e != nil {
		return 0, e
	}
	seen := map[string]bool{i.Uid: true}
	for rows.Next() {
		var id string
		if e = rows.Scan(&id); e != nil {
			rows.Close()
			return 0, e
		}
		seen[id] = true
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return 0, e
	}
	// 关键：递归闭包从原CR UID和持久seen UID出发，不能按同名新资源重新起根。
	changed := true
	for changed {
		changed = false
		for _, r := range candidates {
			if !seen[r.uid] && seen[r.ownerUID] {
				seen[r.uid] = true
				changed = true
			}
		}
	}
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return 0, e
	}
	defer tx.Rollback()
	live := 0
	for _, r := range candidates {
		if !seen[r.uid] {
			continue
		}
		live++
		if _, e = tx.ExecContext(ctx, `INSERT INTO seen_resources VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(instance_id,uid) DO NOTHING`, i.Id, r.apiVersion, r.kind, r.namespace, r.name, r.uid, r.ownerUID, r.path); e != nil {
			return 0, e
		}
	}
	if e = tx.Commit(); e != nil {
		return 0, e
	}
	return live, nil
}

// executeDeletion 在只读预检后争用派发资格，原UID与新读RV共同约束唯一Foreground请求。
func (s *Store) executeDeletion(life context.Context, id string) {
	if s.OperationBoundary != nil {
		if s.OperationBoundary("beforeDispatch", id) != nil {
			return
		}
	}
	o, e := scanOperation(s.DB.QueryRowContext(life, `SELECT `+operationColumns+` FROM operations WHERE id=? AND state='CHECKING'`, id))
	if e != nil {
		return
	}
	s.enrichLifecycle(life, s.DB, o)
	i, e := instanceByID(life, s.DB, o.GetInstanceId())
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
	var uid, path, ns string
	if s.DB.QueryRowContext(life, `SELECT original_uid,cr_path,target_namespace FROM deletion_operations WHERE operation_id=?`, id).Scan(&uid, &path, &ns) != nil {
		return
	}
	var originalCluster map[string]any
	e = t.Request(life, "GET", "/api/v1/namespaces/kube-system", nil, &originalCluster)
	if e == nil && objectString(objectMap(originalCluster, "metadata"), "uid") != cl.Uid {
		e = Error("CONFLICT", "原集群 UID 已变化")
	}
	if e != nil {
		s.finishExecution(life, o, "FAILED", e, nil)
		return
	}
	cr, e := t.readComponent(life, i)
	if e == nil {
		e = checkTarget(i, cr)
	}
	if e != nil {
		s.finishExecution(life, o, "FAILED", e, nil)
		return
	}
	rv := objectString(objectMap(cr, "metadata"), "resourceVersion")
	if rv == "" || i.Uid != uid {
		s.finishExecution(life, o, "FAILED", Error("CONFLICT", "原 UID/RV 不足，拒绝删除"), nil)
		return
	}
	if _, e = s.observeOwnerGraph(life, i, t, ns); e != nil {
		s.finishExecution(life, o, "FAILED", e, nil)
		return
	}
	release, e := s.markLifecycleDispatch(life, o, a.GetActorId(), cl.CredentialRevision)
	if e != nil {
		if apiCode(e) != "ALREADY_FINISHED" {
			s.finishExecution(life, o, "FAILED", e, nil)
		}
		return
	}
	defer release()
	if s.OperationBoundary != nil {
		if s.OperationBoundary("afterSendMarker", id) != nil {
			return
		}
	}
	// 关键：仅删除原CR；绝不直接删除workload、Pod、PVC或finalizer。
	body := map[string]any{"apiVersion": "v1", "kind": "DeleteOptions", "propagationPolicy": "Foreground", "preconditions": map[string]any{"uid": uid, "resourceVersion": rv}}
	sendContext := httptrace.WithClientTrace(life, &httptrace.ClientTrace{WroteRequest: func(httptrace.WroteRequestInfo) { release() }})
	result := t.Mutate(sendContext, "DELETE", path, body)
	release()
	if result.Err != nil && result.Rejected {
		s.finishExecution(life, o, "FAILED", result.Err, nil)
		return
	}
	s.finishDeletion(o, "DELETION_VERIFYING", "已发送原 UID Foreground 删除；等待 CR、workload 和 Pod 全部消失")
	s.verifyDeletion(life, o, i, t)
}

// verifyDeletion 只读证明原资源图已清空；未知、finalizer和同名新UID都保留保护。
func (s *Store) verifyDeletion(ctx context.Context, o *rpc.Operation, i *rpc.ObservationInstance, t *Target) {
	var uid, ns string
	if s.DB.QueryRowContext(ctx, `SELECT original_uid,target_namespace FROM deletion_operations WHERE operation_id=?`, o.OperationId).Scan(&uid, &ns) != nil {
		return
	}
	cr, e := t.readComponent(ctx, i)
	if e != nil {
		s.noteLifecycle(ctx, o, e)
		return
	}
	if cr != nil && objectString(objectMap(cr, "metadata"), "uid") != uid {
		s.finishDeletion(o, "DELETION_BLOCKED", "同名新 UID 已出现；原记录保留，禁止删除或接管新 CR")
		return
	}
	live, e := s.observeOwnerGraph(ctx, i, t, ns)
	if e != nil {
		s.noteLifecycle(ctx, o, e)
		return
	}
	if cr != nil || live > 0 {
		s.finishDeletion(o, "DELETION_BLOCKED", "原 CR、workload 或 Pod 仍存在；等待清理，不移除 finalizer")
		return
	}
	s.finishDeletion(o, "SUCCEEDED", "原 CR、对应 workload 与 Pod 已全部核验消失")
}

// finishDeletion 保留历史身份和发布版本，只有完整清理核验成功才解除删除保护。
func (s *Store) finishDeletion(expected *rpc.Operation, state, message string) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return
	}
	defer tx.Rollback()
	a, _, e := lifecycleAttempt(ctx, tx, expected.OperationId)
	if e != nil || a.AttemptId != expected.GetAttemptId() {
		return
	}
	var current string
	if tx.QueryRowContext(ctx, `SELECT state FROM operations WHERE id=?`, expected.OperationId).Scan(&current) != nil || current == "SUCCEEDED" || current == "FAILED" || current == "CANCELLED" {
		return
	}
	now := s.Now().UTC().Format(time.RFC3339Nano)
	if _, e = tx.ExecContext(ctx, `UPDATE operations SET state=?,message=?,updated_at=? WHERE id=?`, state, message, now, expected.OperationId); e != nil {
		return
	}
	if _, e = tx.ExecContext(ctx, `UPDATE execution_attempts SET state=?,message=?,updated_at=?,revision=revision+1 WHERE id=?`, state, message, now, a.AttemptId); e != nil {
		return
	}
	if _, e = tx.ExecContext(ctx, `UPDATE operation_lifecycle SET revision=revision+1 WHERE operation_id=?`, expected.OperationId); e != nil {
		return
	}
	if state == "SUCCEEDED" {
		if _, e = tx.ExecContext(ctx, `UPDATE instances SET lifecycle='DELETED',activity_at=? WHERE id=?`, now, expected.GetInstanceId()); e != nil {
			return
		}
		if _, e = tx.ExecContext(ctx, `DELETE FROM instance_protections WHERE instance_id=? AND operation_id=?`, expected.GetInstanceId(), expected.OperationId); e != nil {
			return
		}
	}
	tx.Commit()
}
