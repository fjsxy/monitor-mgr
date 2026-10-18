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
	defer func() { tx.Rollback() }()
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
	if e = deletionPreviewIdentity(ctx, tx, i, ""); e != nil {
		return nil, e
	}
	if r.GetPreviewId() == "" {
		return nil, Error("INVALID_INPUT", "缺少删除预览，请重新确认")
	}
	tx.Rollback()
	snapshot, e := s.observeDeletionForUser(ctx, c, i)
	if e != nil {
		return nil, e
	}
	tx, e = s.DB.BeginTx(ctx, nil)
	if e != nil {
		return nil, e
	}
	p, i, e = s.instanceTx(ctx, tx, c, r.Id)
	if e != nil {
		return nil, e
	}
	cmd, hit, e = s.beginCommand(ctx, tx, p, c, "DELETE_INSTANCE", i.Id, r, &replay)
	if e != nil {
		return nil, e
	}
	if hit {
		current, readErr := scanOperation(tx.QueryRowContext(ctx, `SELECT `+operationColumns+` FROM operations WHERE id=?`, replay.OperationId))
		if readErr == nil {
			s.enrichLifecycle(ctx, tx, current)
		}
		return current, readErr
	}
	if e = deletionPreviewIdentity(ctx, tx, i, ""); e != nil {
		return nil, e
	}
	if r.VersionId != i.CurrentVersionId {
		return nil, Error("CONFLICT", "部署版本已变化，请重新确认")
	}
	if e = s.validateDeletionPreview(ctx, tx, i, r.GetPreviewId(), r.VersionId, "", snapshot); e != nil {
		return nil, e
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
	frozen, e := frozenVersionSpec(ctx, tx, v.VersionId, i.Id)
	if e != nil {
		return nil, e
	}
	if e = componentFor(i.Component).deletionGuard(cfg, frozen, snapshot.storage); e != nil {
		return nil, e
	}
	now := s.Now().UTC().Format(time.RFC3339Nano)
	attempt := ID()
	o := &rpc.Operation{OperationId: ID(), Action: "DELETE", State: "QUEUED", ClusterId: i.ClusterId, ActorId: p.Account.Id, OwnerId: p.Account.Id, InstanceId: &i.Id, VersionId: &v.VersionId, AttemptId: &attempt, Revision: reference(int64(1)), Message: "已受理原 UID Foreground 删除，等待核验", CreatedAt: now, UpdatedAt: now}
	result := "UNVERIFIED"
	if i.Component == KSMComponent {
		result = "NOT_APPLICABLE"
	}
	o.StorageResult_ = &result
	deploymentNamespace := cfg.identity().DeploymentNamespace
	if i.Component == KSMComponent {
		deploymentNamespace = objectString(frozen, "targetNamespace")
	}
	for _, q := range []struct {
		sql  string
		args []any
	}{
		{`INSERT INTO operations(id,action,state,cluster_id,actor_id,owner_id,name,message,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)`, []any{o.OperationId, o.Action, o.State, o.ClusterId, o.ActorId, o.OwnerId, i.Name, o.Message, now, now}},
		{`INSERT INTO execution_attempts(id,operation_id,state,possibly_sent,created_at,updated_at,actor_id) VALUES(?,?,'QUEUED',0,?,?,?)`, []any{attempt, o.OperationId, now, now, p.Account.Id}},
		{`INSERT INTO operation_lifecycle VALUES(?,?,1,2)`, []any{o.OperationId, attempt}},
		{`INSERT INTO deletion_operations(operation_id,instance_id,version_id,original_uid,cr_path,target_namespace,preview_id) VALUES(?,?,?,?,?,?,?)`, []any{o.OperationId, i.Id, v.VersionId, i.Uid, componentFor(i.Component).objectPath(i), deploymentNamespace, r.GetPreviewId()}},
		{`INSERT INTO instance_protections VALUES(?,?)`, []any{i.Id, o.OperationId}},
	} {
		if _, e = tx.ExecContext(ctx, q.sql, q.args...); e != nil {
			return nil, Error("UNAVAILABLE", "删除受理事务失败")
		}
	}
	if e = s.recordInstanceActivity(ctx, tx, i.Id); e != nil {
		return nil, e
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

// deletionGuard要求锁外实际存储核验；没有可信快照不能通过通用删除入口。
func (a componentAdapter) deletionGuard(cfg configuration, frozen map[string]any, snapshot *agentStorageSnapshot) error {
	if a.component == AgentComponent {
		if snapshot == nil {
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
		path := base
		if namespace != "" {
			path += "/namespaces/" + url.PathEscape(namespace)
		}
		path += "/" + g.resource
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
				actualNamespace := objectString(m, "namespace")
				if actualNamespace == "" {
					actualNamespace = namespace
				}
				if uid == "" || name == "" || actualNamespace == "" {
					return nil, Error("UNAVAILABLE", "辅助资源身份不完整")
				}
				out = append(out, seenResource{g.api, g.kind, actualNamespace, name, uid, ownerUID(m), base + "/namespaces/" + url.PathEscape(actualNamespace) + "/" + g.resource + "/" + url.PathEscape(name)})
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
	records, e := s.observeOwnerGraphRecords(ctx, i, t, namespace)
	return len(records), e
}

// observeOwnerGraphRecords 同时为清理核验和Pod来源返回完整读取中实际存在的原UID归属节点。
func (s *Store) observeOwnerGraphRecords(ctx context.Context, i *rpc.ObservationInstance, t *Target, namespace string) ([]seenResource, error) {
	// 关键：KSM是cluster-scoped，外部漂移可跨namespace；完整集群分页不可缩成最近部署位置。
	if componentFor(i.Component).scope == "Cluster" {
		namespace = ""
	}
	candidates, e := t.listOwnedCandidates(ctx, namespace)
	if e != nil {
		return nil, e
	}
	rows, e := s.DB.QueryContext(ctx, `SELECT uid FROM seen_resources WHERE instance_id=?`, i.Id)
	if e != nil {
		return nil, e
	}
	seen := map[string]bool{i.Uid: true}
	for rows.Next() {
		var id string
		if e = rows.Scan(&id); e != nil {
			rows.Close()
			return nil, e
		}
		seen[id] = true
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return nil, e
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
		return nil, e
	}
	defer tx.Rollback()
	if !observationScopeCurrent(ctx, tx, ctx) {
		return nil, Error("CONFLICT", "观察目标修订已变化")
	}
	live := []seenResource{}
	for _, r := range candidates {
		if !seen[r.uid] {
			continue
		}
		live = append(live, r)
		if _, e = tx.ExecContext(ctx, `INSERT INTO seen_resources VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(instance_id,uid) DO NOTHING`, i.Id, r.apiVersion, r.kind, r.namespace, r.name, r.uid, r.ownerUID, r.path); e != nil {
			return nil, e
		}
	}
	if e = tx.Commit(); e != nil {
		return nil, e
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
	snapshot, e := s.readDeletionSnapshot(life, i, t)
	var preview, previewOperation string
	if e == nil {
		e = s.DB.QueryRowContext(life, `SELECT d.preview_id,p.operation_id FROM deletion_operations d LEFT JOIN deletion_previews p ON p.id=d.preview_id WHERE d.operation_id=?`, id).Scan(&preview, &previewOperation)
	}
	if e == nil {
		e = s.validateDeletionPreview(life, s.DB, i, preview, o.GetVersionId(), previewOperation, snapshot)
	}
	// 关键：读取辅助图后重读CR，配置变化需新预览；status/RV自然推进只刷新条件RV。
	if e == nil {
		cr, e = t.readComponent(life, i)
	}
	if e == nil {
		e = checkTarget(i, cr)
	}
	if e == nil && snapshot.target != s.fingerprint(i, "delete-target/v1:"+componentFor(i.Component).normalizeSpec(objectMap(cr, "spec"))) {
		e = Error("CONFLICT", "删除目标已变化，请重新预览")
	}
	if e != nil {
		s.finishExecution(life, o, "FAILED", e, nil)
		return
	}
	rv = objectString(objectMap(cr, "metadata"), "resourceVersion")
	if rv == "" {
		s.finishExecution(life, o, "FAILED", Error("CONFLICT", "原RV不可读"), nil)
		return
	}
	release, e := s.markLifecycleDispatch(life, o, a.GetActorId(), cl.CredentialRevision, snapshot.storage)
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
	// 关键：DELETE404只证明CR缺失，预检后消失的原workload/Pod仍须完整核验，不能释放保护。
	if result.Err != nil && result.Rejected && result.HTTPStatus != 404 {
		s.finishExecution(life, o, "FAILED", result.Err, nil)
		return
	}
	s.finishDeletion(o, "DELETION_VERIFYING", "已发送原 UID Foreground 删除；等待 CR、workload 和 Pod 全部消失")
	s.verifyDeletion(life, o, i, t)
	s.verifyAgentStorageTasks(life, id)
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
		if _, e = tx.ExecContext(ctx, `UPDATE instances SET lifecycle='DELETED' WHERE id=?`, expected.GetInstanceId()); e != nil {
			return
		}
		if _, e = tx.ExecContext(ctx, `DELETE FROM instance_protections WHERE instance_id=? AND operation_id=?`, expected.GetInstanceId(), expected.OperationId); e != nil {
			return
		}
	}
	tx.Commit()
}
