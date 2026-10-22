// 附加资源复用相同的持久尝试、写前授权和结果不明保护；不包含任何 ServiceMonitor 产品规则。
package control

import (
	"context"
	"encoding/json"
	"net/http/httptrace"
	"strings"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

// frozenResourceMatches 核对冻结 spec 及平台明确管理的 labels，忽略 API 添加的无关元数据。
func frozenResourceMatches(i *rpc.ObservationInstance, r frozenResource, cr map[string]any) bool {
	if r.key == "component" {
		return componentFor(i.Component).normalizeSpec(objectMap(cr, "spec")) == componentFor(i.Component).normalizeSpec(objectMap(r.document, "spec"))
	}
	if canonical(objectMap(cr, "spec")) != canonical(objectMap(r.document, "spec")) {
		return false
	}
	for key, value := range objectMap(objectMap(r.document, "metadata"), "labels") {
		if objectMap(objectMap(cr, "metadata"), "labels")[key] != value {
			return false
		}
	}
	return true
}
func resourceWriteDocument(r frozenResource, current map[string]any) map[string]any {
	var doc map[string]any
	if current == nil {
		doc = cloneResourceDocument(r.document)
	} else {
		doc = cloneResourceDocument(current)
		doc["spec"] = cloneResourceDocument(objectMap(r.document, "spec"))
		meta := objectMap(doc, "metadata")
		labels := objectMap(meta, "labels")
		if labels == nil {
			labels = map[string]any{}
			meta["labels"] = labels
		}
		for key, value := range objectMap(objectMap(r.document, "metadata"), "labels") {
			labels[key] = value
		}
	}
	return doc
}

// executeAdditionalResource 执行已冻结的附加CR；构建者负责产品依赖和预览，执行器只接受原身份。
func (s *Store) executeAdditionalResource(ctx context.Context, id string, r frozenResource, deleting bool) {
	o, e := scanOperation(s.DB.QueryRowContext(ctx, `SELECT `+operationColumns+` FROM operations WHERE id=? AND state='CHECKING'`, id))
	if e != nil {
		return
	}
	s.enrichPublicationOperation(ctx, s.DB, o)
	s.enrichLifecycle(ctx, s.DB, o)
	selectExecutionResource(o, r.key)
	i, e := instanceByID(ctx, s.DB, o.GetInstanceId())
	if e != nil {
		s.finishExecution(ctx, o, "FAILED", e, nil)
		return
	}
	a, _, e := lifecycleAttempt(ctx, s.DB, id)
	if e != nil {
		return
	}
	t, cl, e := s.targetForActor(ctx, a.GetActorId(), o.ClusterId)
	if e != nil {
		s.finishExecution(ctx, o, "FAILED", e, nil)
		return
	}
	defer t.Close()
	if s.OperationBoundary != nil {
		if s.OperationBoundary("beforeResourceDispatch:"+r.key, id) != nil {
			return
		}
	}
	// 关键：每个资源独立核验原集群、namespace和写权限，不能继承前一份资源的检查结果。
	if e = checkResourceDependencies(ctx, t, cl, r, deleting); e != nil {
		s.finishExecution(ctx, o, "FAILED", e, nil)
		return
	}
	cr, e := readFrozenResource(ctx, t, r)
	if e != nil {
		s.finishExecution(ctx, o, "FAILED", e, nil)
		return
	}
	if r.originalUID == "" && cr != nil {
		s.finishExecution(ctx, o, "FAILED", Error("CONFLICT", "同名附加资源存在，拒绝自动接管"), nil)
		return
	}
	if r.originalUID != "" && (cr == nil || objectString(objectMap(cr, "metadata"), "uid") != r.originalUID) {
		s.finishExecution(ctx, o, "FAILED", Error("CONFLICT", "附加资源原 UID 缺失或已被替换"), nil)
		return
	}
	method, path, body := "POST", r.collection, resourceWriteDocument(r, cr)
	if r.originalUID != "" {
		method, path = "PUT", r.path
		if objectString(objectMap(cr, "metadata"), "resourceVersion") == "" {
			s.finishExecution(ctx, o, "FAILED", Error("CONFLICT", "资源 RV 缺失，拒绝无条件写"), nil)
			return
		}
	}
	if deleting {
		method, path = "DELETE", r.path
		body = map[string]any{"apiVersion": "v1", "kind": "DeleteOptions", "propagationPolicy": "Foreground", "preconditions": map[string]any{"uid": r.originalUID, "resourceVersion": objectString(objectMap(cr, "metadata"), "resourceVersion")}}
	}
	release, e := s.markLifecycleDispatch(ctx, o, a.GetActorId(), cl.CredentialRevision)
	if e != nil {
		if apiCode(e) != "ALREADY_FINISHED" {
			s.finishExecution(ctx, o, "FAILED", e, nil)
		}
		return
	}
	defer release()
	if s.OperationBoundary != nil {
		if s.OperationBoundary("afterResourceSendMarker:"+r.key, id) != nil {
			return
		}
	}
	trace := httptrace.WithClientTrace(ctx, &httptrace.ClientTrace{WroteRequest: func(httptrace.WroteRequestInfo) { release() }})
	result := t.Mutate(trace, method, path, body)
	release()
	if result.Err != nil {
		if deleting && (!result.Rejected || result.HTTPStatus == 404) {
			s.finishDeletion(o, "DELETION_VERIFYING", "附加资源删除结果待只读核验")
			return
		}
		state := "RESULT_UNCERTAIN"
		if result.Rejected {
			state = "FAILED"
		} else if r.originalUID == "" {
			state = "IDENTITY_UNCERTAIN"
		}
		s.finishExecution(ctx, o, state, result.Err, nil)
		return
	}
	if deleting {
		s.finishDeletion(o, "DELETION_VERIFYING", "等待核实原附加资源消失")
		s.verifyAdditionalDeletion(ctx, o, r, t)
		return
	}
	uid := objectString(objectMap(result.Object, "metadata"), "uid")
	if uid == "" || (r.originalUID != "" && uid != r.originalUID) || !frozenResourceMatches(i, r, result.Object) {
		state := "RESULT_UNCERTAIN"
		if r.originalUID == "" {
			state = "IDENTITY_UNCERTAIN"
		}
		s.finishExecution(ctx, o, state, Error("CONFLICT", "附加资源响应不足以确认原身份和冻结配置"), nil)
		return
	}
	s.finishExecution(ctx, o, "SUCCEEDED", nil, result.Object)
}
func checkResourceDependencies(ctx context.Context, t *Target, cl *rpc.Cluster, r frozenResource, deleting bool) error {
	var ns map[string]any
	if e := t.Request(ctx, "GET", "/api/v1/namespaces/kube-system", nil, &ns); e != nil {
		return e
	}
	if objectString(objectMap(ns, "metadata"), "uid") != cl.Uid {
		return Error("CONFLICT", "原集群 UID 已变化")
	}
	namespace := objectString(objectMap(r.document, "metadata"), "namespace")
	if namespace != "" {
		if e := t.Request(ctx, "GET", "/api/v1/namespaces/"+namespace, nil, &ns); e != nil {
			return e
		}
	}
	parts := strings.Split(r.collection, "/")
	verb := "create"
	if r.originalUID != "" {
		verb = "update"
	}
	if deleting {
		verb = "delete"
	}
	group := strings.Split(objectString(r.document, "apiVersion"), "/")[0]
	var review struct {
		Status struct {
			Allowed bool `json:"allowed"`
		} `json:"status"`
	}
	attrs := map[string]any{"group": group, "resource": parts[len(parts)-1], "verb": verb}
	if namespace != "" {
		attrs["namespace"] = namespace
	}
	e := t.Request(ctx, "POST", "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews", map[string]any{"apiVersion": "authorization.k8s.io/v1", "kind": "SelfSubjectAccessReview", "spec": map[string]any{"resourceAttributes": attrs}}, &review)
	if e != nil {
		return e
	}
	if !review.Status.Allowed {
		return Error("FORBIDDEN", "附加资源写权限不足")
	}
	return nil
}
func (s *Store) verifyAdditionalDeletion(ctx context.Context, o *rpc.Operation, r frozenResource, t *Target) {
	cr, e := readFrozenResource(ctx, t, r)
	if e != nil {
		s.noteLifecycle(ctx, o, e)
		return
	}
	if cr != nil {
		s.finishDeletion(o, "DELETION_BLOCKED", "原附加资源尚未核实消失；不删除替代 UID 或移除 finalizer")
		return
	}
	s.finishDeletion(o, "SUCCEEDED", "原附加资源已核验消失")
}

func cloneResourceDocument(value map[string]any) map[string]any {
	var out map[string]any
	json.Unmarshal([]byte(canonical(value)), &out)
	return out
}

// verifyCompletedResource 在人工重试时先只读核对前次成功步骤，不能盲目沿用已替换或漂移的资源。
func (s *Store) verifyCompletedResource(ctx context.Context, id, attempt string, r frozenResource, deleting bool) bool {
	o, e := scanOperation(s.DB.QueryRowContext(ctx, `SELECT `+operationColumns+` FROM operations WHERE id=?`, id))
	if e != nil {
		return false
	}
	s.enrichLifecycle(ctx, s.DB, o)
	s.enrichPublicationOperation(ctx, s.DB, o)
	selectExecutionResource(o, r.key)
	i, e := instanceByID(ctx, s.DB, o.GetInstanceId())
	if e != nil {
		return false
	}
	t, _, e := s.targetForRecovery(ctx, o.ClusterId)
	if e == nil {
		defer t.Close()
		var cr map[string]any
		cr, e = readFrozenResource(ctx, t, r)
		if e == nil {
			var uid string
			e = s.DB.QueryRowContext(ctx, `SELECT confirmed_uid FROM resource_progress WHERE attempt_id=? AND resource_key=?`, attempt, r.key).Scan(&uid)
			if e == nil && ((deleting && cr == nil) || (!deleting && cr != nil && uid != "" && objectString(objectMap(cr, "metadata"), "uid") == uid && frozenResourceMatches(i, r, cr))) {
				return true
			}
		}
	}
	// 关键：只改变新尝试的核验结果，旧尝试中已经确认的历史事实保持原样。
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return false
	}
	defer tx.Rollback()
	state := "RESULT_UNCERTAIN"
	if deleting {
		state = "DELETION_BLOCKED"
	}
	now := resourceNow(s)
	message := "前次已完成资源当前无法核实原 UID 与冻结配置；停止后续写入，只读核验"
	for _, query := range []struct {
		sql  string
		args []any
	}{
		{`UPDATE resource_progress SET state=?,message=?,updated_at=? WHERE attempt_id=? AND resource_key=?`, []any{state, message, now, attempt, r.key}},
		{`UPDATE operations SET state=?,message=?,updated_at=? WHERE id=? AND state='CHECKING'`, []any{state, message, now, id}},
		{`UPDATE execution_attempts SET state=?,message=?,revision=revision+1,updated_at=? WHERE id=?`, []any{state, message, now, attempt}},
		{`UPDATE operation_lifecycle SET revision=revision+1 WHERE operation_id=? AND current_attempt_id=?`, []any{id, attempt}},
	} {
		if _, e = tx.ExecContext(ctx, query.sql, query.args...); e != nil {
			return false
		}
	}
	tx.Commit()
	return false
}
