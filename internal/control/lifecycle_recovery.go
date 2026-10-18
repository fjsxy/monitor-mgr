// 结果恢复只读取原集群和原资源身份；当前凭据更新不会触发任何写请求重放。
package control

import (
	"context"
	"encoding/json"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"sync"
	"time"
)

// recoveryFlights 防止自动刷新与页面刷新并发核验同一操作；不作为持久安全边界。
var recoveryFlights sync.Map

// targetForRecovery 使用当前已核验凭据，独立于原用户 Cookie 或当前账号启用状态。
func (s *Store) targetForRecovery(ctx context.Context, cluster string) (*Target, *rpc.Cluster, error) {
	cl, e := clusterByID(ctx, s.DB, cluster)
	if e != nil {
		return nil, nil, e
	}
	if cl.State != "ACTIVE" {
		return nil, nil, Error("CONFLICT", "集群已注销，停止同步")
	}
	var cipher []byte
	if s.DB.QueryRowContext(ctx, `SELECT credential FROM clusters WHERE id=? AND state='ACTIVE'`, cluster).Scan(&cipher) != nil {
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
	t.credentialRevision = cl.CredentialRevision
	t.clusterUID = cl.Uid
	var ns map[string]any
	if e = t.Request(ctx, "GET", "/api/v1/namespaces/kube-system", nil, &ns); e != nil {
		t.Close()
		return nil, nil, e
	}
	// 关键：只读恢复也要核对原集群 UID，不能把同地址替换后的另一集群当成原资源。
	if objectString(objectMap(ns, "metadata"), "uid") != cl.Uid {
		t.Close()
		return nil, nil, Error("CONFLICT", "集群 UID 已变化，禁止恢复绑定")
	}
	return t, cl, nil
}

// launchRecovery 定期读取待确认结果，不重新进入派发队列。
func (s *Store) launchRecovery(ctx context.Context) {
	rows, e := s.DB.QueryContext(ctx, `SELECT id FROM operations WHERE action IN ('PUBLISH','DELETE') AND state IN ('RESULT_UNCERTAIN','DELETION_VERIFYING','DELETION_BLOCKED')`)
	if e != nil {
		return
	}
	ids := []string{}
	for rows.Next() {
		var id string
		if rows.Scan(&id) == nil {
			ids = append(ids, id)
		}
	}
	rows.Close()
	for _, id := range ids {
		if _, busy := recoveryFlights.LoadOrStore(s.observationEpoch+id, true); busy {
			continue
		}
		s.publicationWG.Add(1)
		go func(id string) {
			defer s.publicationWG.Done()
			defer recoveryFlights.Delete(s.observationEpoch + id)
			s.verifyLifecycle(ctx, id)
		}(id)
	}
}

// RefreshOperation 验证当前查看授权后主动只读核验，身份不明不会通过此入口补认。
func (s *Store) RefreshOperation(ctx context.Context, c *rpc.RequestContext, r *rpc.OperationID) (*rpc.Operation, error) {
	o, e := s.GetOperation(ctx, c, r)
	if e != nil {
		return nil, e
	}
	if o.Action != "PUBLISH" && o.Action != "DELETE" {
		return nil, Error("CONFLICT", "此入口只读核验实例变更")
	}
	if _, busy := recoveryFlights.LoadOrStore(s.observationEpoch+o.OperationId, true); !busy {
		defer recoveryFlights.Delete(s.observationEpoch + o.OperationId)
		s.verifyLifecycle(ctx, o.OperationId)
	}
	return s.GetOperation(ctx, c, r)
}

// verifyLifecycle 只接受已发待确认状态，永不调用 Target.Mutate。
func (s *Store) verifyLifecycle(ctx context.Context, id string) {
	o, e := scanOperation(s.DB.QueryRowContext(ctx, `SELECT `+operationColumns+` FROM operations WHERE id=?`, id))
	if e != nil {
		return
	}
	s.enrichPublicationOperation(ctx, s.DB, o)
	s.enrichLifecycle(ctx, s.DB, o)
	if o.State != "RESULT_UNCERTAIN" && o.State != "DELETION_VERIFYING" && o.State != "DELETION_BLOCKED" {
		return
	}
	i, e := instanceByID(ctx, s.DB, o.GetInstanceId())
	if e != nil {
		return
	}
	t, _, e := s.targetForRecovery(ctx, o.ClusterId)
	if e != nil {
		s.noteLifecycle(ctx, o, e)
		return
	}
	defer t.Close()
	if o.Action == "DELETE" {
		s.verifyDeletion(ctx, o, i, t)
		return
	}
	// 关键：首次创建未保存 UID 时，同名、spec、labels 完全一致也不足以确认资源身份。
	if i.Uid == "" {
		return
	}
	cr, e := t.readComponent(ctx, i)
	if e != nil {
		s.noteLifecycle(ctx, o, e)
		return
	}
	if e = checkTarget(i, cr); e != nil {
		s.noteLifecycle(ctx, o, e)
		return
	}
	var spec []byte
	if s.DB.QueryRowContext(ctx, `SELECT effective_spec FROM publication_versions WHERE id=? AND instance_id=?`, o.GetVersionId(), i.Id).Scan(&spec) != nil {
		return
	}
	var desired map[string]any
	if json.Unmarshal(spec, &desired) != nil {
		return
	}
	if componentFor(i.Component).normalizeSpec(objectMap(cr, "spec")) != componentFor(i.Component).normalizeSpec(desired) {
		s.noteLifecycle(ctx, o, Error("CONFLICT", "原 UID 存在，配置尚不能确认；仅继续读取，不重放"))
		return
	}
	s.finishPublication(o, "SUCCEEDED", nil, cr)
	s.refreshObserved(ctx, i.Id, t)
}

// noteLifecycle 保存安全的读取原因，未核实结果不会解除保护。
func (s *Store) noteLifecycle(ctx context.Context, o *rpc.Operation, cause error) {
	message := "读取核验失败，保留未结束保护"
	if a, ok := cause.(*rpc.APIError); ok {
		message = a.Message
	}
	s.DB.ExecContext(ctx, `UPDATE operations SET message=?,updated_at=? WHERE id=? AND state IN ('RESULT_UNCERTAIN','DELETION_VERIFYING','DELETION_BLOCKED')`, message, s.Now().UTC().Format(time.RFC3339Nano), o.OperationId)
}
