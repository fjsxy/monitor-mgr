package control

import (
	"context"
	"database/sql"
	"encoding/json"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"net/url"
	"strings"
	"time"
)

const clusterColumns = `id,name,owner_id,uid,identity,kubernetes_version,state,credential_revision,revision,capabilities,created_at`
const operationColumns = `id,action,state,cluster_id,actor_id,message,created_at,updated_at,credential_revision`

func scanCluster(row interface{ Scan(...any) error }) (*rpc.Cluster, error) {
	c := new(rpc.Cluster)
	var caps []byte
	e := row.Scan(&c.Id, &c.Name, &c.OwnerId, &c.Uid, &c.Identity, &c.KubernetesVersion, &c.State, &c.CredentialRevision, &c.Revision, &caps, &c.CreatedAt)
	if e != nil {
		return nil, e
	}
	if json.Unmarshal(caps, &c.Capabilities) != nil {
		return nil, Error("UNAVAILABLE", "集群能力记录不可读")
	}
	return c, nil
}
func scanOperation(row interface{ Scan(...any) error }) (*rpc.Operation, error) {
	o := new(rpc.Operation)
	e := row.Scan(&o.OperationId, &o.Action, &o.State, &o.ClusterId, &o.ActorId, &o.Message, &o.CreatedAt, &o.UpdatedAt, &o.CredentialRevision)
	return o, e
}

type clusterQuery interface {
	QueryRowContext(context.Context, string, ...any) *sql.Row
}

func clusterByID(ctx context.Context, q clusterQuery, id string) (*rpc.Cluster, error) {
	c, e := scanCluster(q.QueryRowContext(ctx, `SELECT `+clusterColumns+` FROM clusters WHERE id=?`, id))
	if e == sql.ErrNoRows {
		return nil, Error("NOT_FOUND", "集群不存在")
	}
	if e != nil {
		return nil, Error("UNAVAILABLE", "集群记录不可用")
	}
	return c, nil
}
func clusterPermission(p *Principal, c *rpc.Cluster) error {
	if p.Account.Role != "ADMIN" && c.OwnerId != p.Account.Id {
		return Error("FORBIDDEN", "无权访问此集群")
	}
	return nil
}
func enabledOwner(ctx context.Context, q clusterQuery, id string) error {
	var enabled bool
	e := q.QueryRowContext(ctx, `SELECT enabled FROM accounts WHERE id=?`, id).Scan(&enabled)
	if e != nil || !enabled {
		return Error("INVALID_INPUT", "所属用户必须存在且启用")
	}
	return nil
}
func (s *Store) ListClusters(ctx context.Context, c *rpc.RequestContext) (*rpc.ClustersResult_, error) {
	p, e := s.Authorize(ctx, token(c))
	if e != nil {
		return nil, e
	}
	q := `SELECT ` + clusterColumns + ` FROM clusters`
	args := []any{}
	if p.Account.Role != "ADMIN" {
		q += ` WHERE owner_id=?`
		args = append(args, p.Account.Id)
	}
	rows, e := s.DB.QueryContext(ctx, q+` ORDER BY created_at,id`, args...)
	if e != nil {
		return nil, Error("UNAVAILABLE", "集群列表不可读")
	}
	defer rows.Close()
	out := []*rpc.Cluster{}
	for rows.Next() {
		cl, e := scanCluster(rows)
		if e != nil {
			return nil, Error("UNAVAILABLE", "集群列表不可读")
		}
		out = append(out, cl)
	}
	if rows.Err() != nil {
		return nil, Error("UNAVAILABLE", "集群列表不可读")
	}
	return &rpc.ClustersResult_{Clusters: out}, nil
}
func (s *Store) GetCluster(ctx context.Context, c *rpc.RequestContext, r *rpc.ClusterID) (*rpc.Cluster, error) {
	p, e := s.Authorize(ctx, token(c))
	if e != nil {
		return nil, e
	}
	cl, e := clusterByID(ctx, s.DB, r.Id)
	if e != nil {
		return nil, e
	}
	if e = clusterPermission(p, cl); e != nil {
		return nil, e
	}
	return cl, nil
}
func (s *Store) RegisterCluster(ctx context.Context, c *rpc.RequestContext, r *rpc.RegisterClusterRequest) (*rpc.Operation, error) {
	return s.acceptCredential(ctx, c, "REGISTER", ID(), r.OwnerId, r.Name, r.Context, r.Kubeconfig, r)
}
func (s *Store) UpdateCredential(ctx context.Context, c *rpc.RequestContext, r *rpc.CredentialRequest) (*rpc.Operation, error) {
	return s.acceptCredential(ctx, c, "CREDENTIAL", r.Id, "", "", r.Context, r.Kubeconfig, r)
}
func (s *Store) ReactivateCluster(ctx context.Context, c *rpc.RequestContext, r *rpc.CredentialRequest) (*rpc.Operation, error) {
	return s.acceptCredential(ctx, c, "REACTIVATE", r.Id, r.GetOwnerId(), "", r.Context, r.Kubeconfig, r)
}
func (s *Store) acceptCredential(ctx context.Context, c *rpc.RequestContext, action, id, owner, name, contextName, raw string, request any) (*rpc.Operation, error) {
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return nil, Error("UNAVAILABLE", "暂不可用")
	}
	defer tx.Rollback()
	p, e := s.AuthorizeTx(ctx, tx, token(c))
	if e != nil {
		return nil, e
	}
	if e = requireAdmin(p); e != nil {
		return nil, e
	}
	scope := id
	if action == "REGISTER" {
		scope = "clusters"
	}
	var replay rpc.Operation
	cmd, hit, e := s.beginCommand(ctx, tx, p, c, action, scope, request, &replay)
	if e != nil {
		return nil, e
	}
	if hit {
		return &replay, nil
	}
	if action == "REGISTER" {
		if strings.TrimSpace(name) == "" || len(name) > 100 {
			return nil, Error("INVALID_INPUT", "请填写 1–100 字节集群名称")
		}
	} else {
		cl, e := clusterByID(ctx, tx, id)
		if e != nil {
			return nil, e
		}
		if action == "REACTIVATE" {
			if cl.State != "ARCHIVED" {
				return nil, Error("CONFLICT", "仅已注销集群可重新启用")
			}
		} else {
			if cl.State != "ACTIVE" {
				return nil, Error("CONFLICT", "已注销集群须重新启用")
			}
			owner = cl.OwnerId
		}
		name = cl.Name
		var n int
		if tx.QueryRowContext(ctx, `SELECT count(*) FROM operations WHERE cluster_id=? AND action IN ('REGISTER','CREDENTIAL','REACTIVATE') AND state NOT IN ('SUCCEEDED','FAILED','CANCELLED')`, id).Scan(&n) != nil {
			return nil, Error("UNAVAILABLE", "操作保护不可用")
		}
		if n > 0 {
			return nil, Error("CONFLICT", "集群仍有未结束的凭据验证")
		}
	}
	if action != "CREDENTIAL" {
		if e = enabledOwner(ctx, tx, owner); e != nil {
			return nil, e
		}
	}
	cred, e := parseCredential(raw, contextName)
	if e != nil {
		return nil, e
	}
	now := s.Now().UTC().Format(time.RFC3339Nano)
	o := &rpc.Operation{OperationId: ID(), Action: action, State: "QUEUED", ClusterId: id, ActorId: p.Account.Id, Message: "已受理，候选尚未核验生效", CreatedAt: now, UpdatedAt: now}
	cipher, e := s.sealCredential(cred, o.OperationId)
	if e != nil {
		return nil, Error("UNAVAILABLE", "候选凭据无法加密")
	}
	if _, e = tx.ExecContext(ctx, `INSERT INTO operations(id,action,state,cluster_id,actor_id,owner_id,name,message,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)`, o.OperationId, action, o.State, id, p.Account.Id, owner, name, o.Message, now, now); e != nil {
		return nil, Error("UNAVAILABLE", "操作无法保存")
	}
	if _, e = tx.ExecContext(ctx, `INSERT INTO cluster_candidates VALUES(?,?)`, o.OperationId, cipher); e != nil {
		return nil, Error("UNAVAILABLE", "候选无法保存")
	}
	if e = s.finishCommand(ctx, tx, cmd, o); e != nil {
		return nil, e
	}
	if e = audit(ctx, tx, p.Account.Id, action+"_ACCEPTED", id, s.Now()); e != nil {
		return nil, e
	}
	if e = tx.Commit(); e != nil {
		return nil, Error("UNAVAILABLE", "受理失败")
	}
	s.wakeWorker()
	return o, nil
}

// HasUnfinishedChangesTx protects ownership and unregister against every operation kind,
// including future publish/delete states and uncertain results. Unknown states fail closed.
func (s *Store) HasUnfinishedChangesTx(ctx context.Context, tx *sql.Tx, id string) error {
	var n int
	if tx.QueryRowContext(ctx, `SELECT count(*) FROM operations WHERE cluster_id=? AND state NOT IN ('SUCCEEDED','FAILED','CANCELLED')`, id).Scan(&n) != nil {
		return Error("UNAVAILABLE", "未结束变更无法核验")
	}
	if n > 0 {
		return Error("CONFLICT", "集群仍有未结束变更，不能转移或注销")
	}
	return nil
}
func (s *Store) TransferCluster(ctx context.Context, c *rpc.RequestContext, r *rpc.TransferClusterRequest) (*rpc.Cluster, error) {
	return s.clusterMutation(ctx, c, "TRANSFER", r.Id, r.OwnerId, r)
}
func (s *Store) UnregisterCluster(ctx context.Context, c *rpc.RequestContext, r *rpc.ClusterID) (*rpc.Cluster, error) {
	return s.clusterMutation(ctx, c, "UNREGISTER", r.Id, "", r)
}
func (s *Store) clusterMutation(ctx context.Context, c *rpc.RequestContext, action, id, owner string, request any) (*rpc.Cluster, error) {
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return nil, Error("UNAVAILABLE", "暂不可用")
	}
	defer tx.Rollback()
	p, e := s.AuthorizeTx(ctx, tx, token(c))
	if e != nil {
		return nil, e
	}
	if e = requireAdmin(p); e != nil {
		return nil, e
	}
	cl, e := clusterByID(ctx, tx, id)
	if e != nil {
		return nil, e
	}
	var replay rpc.Cluster
	cmd, hit, e := s.beginCommand(ctx, tx, p, c, action, id, request, &replay)
	if e != nil {
		return nil, e
	}
	if hit {
		return &replay, nil
	}
	if cl.State != "ACTIVE" {
		return nil, Error("CONFLICT", "集群已注销")
	}
	if e = s.HasUnfinishedChangesTx(ctx, tx, id); e != nil {
		return nil, e
	}
	if action == "TRANSFER" {
		if e = enabledOwner(ctx, tx, owner); e != nil {
			return nil, e
		}
		if _, e = tx.ExecContext(ctx, `UPDATE clusters SET owner_id=?,revision=revision+1 WHERE id=?`, owner, id); e != nil {
			return nil, Error("UNAVAILABLE", "转移失败")
		}
	} else {
		if _, e = tx.ExecContext(ctx, `UPDATE clusters SET state='ARCHIVED',credential=NULL,revision=revision+1 WHERE id=?`, id); e != nil {
			return nil, Error("UNAVAILABLE", "注销失败")
		}
		if _, e = tx.ExecContext(ctx, `DELETE FROM cluster_candidates WHERE operation_id IN(SELECT id FROM operations WHERE cluster_id=?)`, id); e != nil {
			return nil, Error("UNAVAILABLE", "候选清理失败")
		}
	}
	cl, e = clusterByID(ctx, tx, id)
	if e != nil {
		return nil, e
	}
	if e = audit(ctx, tx, p.Account.Id, action, id, s.Now()); e != nil {
		return nil, e
	}
	now := s.Now().UTC().Format(time.RFC3339Nano)
	if _, e = tx.ExecContext(ctx, `INSERT INTO operations(id,action,state,cluster_id,actor_id,owner_id,name,message,created_at,updated_at,credential_revision) VALUES(?,?,'SUCCEEDED',?,?,?,?,?,?,?,?)`, ID(), action, id, p.Account.Id, cl.OwnerId, cl.Name, map[bool]string{true: "集群已注销；目标 CR 和工作负载可能继续运行", false: "归属已转移"}[action == "UNREGISTER"], now, now, cl.CredentialRevision); e != nil {
		return nil, Error("UNAVAILABLE", "历史保存失败")
	}
	if e = s.finishCommand(ctx, tx, cmd, cl); e != nil {
		return nil, e
	}
	if tx.Commit() != nil {
		return nil, Error("UNAVAILABLE", "操作提交失败")
	}
	return cl, nil
}
func (s *Store) GetOperation(ctx context.Context, c *rpc.RequestContext, r *rpc.OperationID) (*rpc.Operation, error) {
	p, e := s.Authorize(ctx, token(c))
	if e != nil {
		return nil, e
	}
	o, e := scanOperation(s.DB.QueryRowContext(ctx, `SELECT `+operationColumns+` FROM operations WHERE id=?`, r.OperationId))
	if e == sql.ErrNoRows {
		return nil, Error("NOT_FOUND", "操作不存在")
	}
	if e != nil {
		return nil, Error("UNAVAILABLE", "操作读取失败")
	}
	if p.Account.Role != "ADMIN" {
		cl, e := clusterByID(ctx, s.DB, o.ClusterId)
		if e != nil {
			return nil, Error("FORBIDDEN", "无权访问操作")
		}
		if e = clusterPermission(p, cl); e != nil {
			return nil, e
		}
	}
	return o, nil
}
func (s *Store) ListClusterOperations(ctx context.Context, c *rpc.RequestContext, r *rpc.ClusterID) (*rpc.OperationsResult_, error) {
	if _, e := s.GetCluster(ctx, c, r); e != nil {
		return nil, e
	}
	rows, e := s.DB.QueryContext(ctx, `SELECT `+operationColumns+` FROM operations WHERE cluster_id=? ORDER BY created_at,id`, r.Id)
	if e != nil {
		return nil, Error("UNAVAILABLE", "历史不可读")
	}
	defer rows.Close()
	out := []*rpc.Operation{}
	for rows.Next() {
		o, e := scanOperation(rows)
		if e != nil {
			return nil, Error("UNAVAILABLE", "历史不可读")
		}
		out = append(out, o)
	}
	if rows.Err() != nil {
		return nil, Error("UNAVAILABLE", "历史不可读")
	}
	return &rpc.OperationsResult_{Operations: out}, nil
}
func (s *Store) ContinueOperation(ctx context.Context, c *rpc.RequestContext, r *rpc.OperationID) (*rpc.Operation, error) {
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return nil, Error("UNAVAILABLE", "暂不可用")
	}
	defer tx.Rollback()
	p, e := s.AuthorizeTx(ctx, tx, token(c))
	if e != nil {
		return nil, e
	}
	if e = requireAdmin(p); e != nil {
		return nil, e
	}
	o, e := scanOperation(tx.QueryRowContext(ctx, `SELECT `+operationColumns+` FROM operations WHERE id=?`, r.OperationId))
	if e != nil {
		return nil, Error("NOT_FOUND", "操作不存在")
	}
	var replay rpc.Operation
	cmd, hit, e := s.beginCommand(ctx, tx, p, c, "CONTINUE_OPERATION", o.OperationId, r, &replay)
	if e != nil {
		return nil, e
	}
	if hit {
		return &replay, nil
	}
	if o.State != "WAITING_CONFIRMATION" {
		return nil, Error("CONFLICT", "此操作不等待人工继续")
	}
	o.State = "QUEUED"
	o.ActorId = p.Account.Id
	o.Message = "已明确继续只读核验"
	o.UpdatedAt = s.Now().UTC().Format(time.RFC3339Nano)
	if _, e = tx.ExecContext(ctx, `UPDATE operations SET state=?,actor_id=?,message=?,updated_at=? WHERE id=?`, o.State, o.ActorId, o.Message, o.UpdatedAt, o.OperationId); e != nil {
		return nil, Error("UNAVAILABLE", "继续失败")
	}
	if e = s.finishCommand(ctx, tx, cmd, o); e != nil {
		return nil, e
	}
	if tx.Commit() != nil {
		return nil, Error("UNAVAILABLE", "继续提交失败")
	}
	s.wakeWorker()
	return o, nil
}
func (s *Store) TargetForCluster(ctx context.Context, c *rpc.RequestContext, id string) (*Target, error) {
	cl, e := s.GetCluster(ctx, c, &rpc.ClusterID{Id: id})
	if e != nil {
		return nil, e
	}
	if cl.State != "ACTIVE" {
		return nil, Error("CONFLICT", "集群已注销，访问凭据已清除")
	}
	var raw []byte
	if e = s.DB.QueryRowContext(ctx, `SELECT credential FROM clusters WHERE id=? AND state='ACTIVE'`, id).Scan(&raw); e != nil {
		return nil, Error("UNAVAILABLE", "凭据不可读")
	}
	cred, e := s.openCredential(raw, id)
	if e != nil {
		return nil, e
	}
	return newTarget(cred)
}
func (s *Store) ListNamespaces(ctx context.Context, c *rpc.RequestContext, r *rpc.ClusterID) (*rpc.NamespacesResult_, error) {
	t, e := s.TargetForCluster(ctx, c, r.Id)
	if e != nil {
		return nil, e
	}
	defer t.Close()
	bounded, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()
	names, e := t.Namespaces(bounded)
	if e != nil {
		return nil, e
	}
	return &rpc.NamespacesResult_{Namespaces: names}, nil
}
func (s *Store) ListReferences(ctx context.Context, c *rpc.RequestContext, r *rpc.ReferencesRequest) (*rpc.ReferencesResult_, error) {
	t, e := s.TargetForCluster(ctx, c, r.Id)
	if e != nil {
		return nil, e
	}
	defer t.Close()
	resource := map[string]string{"Secret": "secrets", "ServiceAccount": "serviceaccounts"}[r.Kind]
	if resource == "" || r.Namespace == "" || strings.ContainsAny(r.Namespace, "/?.# ") {
		return nil, Error("INVALID_INPUT", "引用类型或 namespace 无效")
	}
	bounded, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()
	var list struct {
		Items []struct {
			Metadata struct {
				Name      string `json:"name"`
				Namespace string `json:"namespace"`
			} `json:"metadata"`
			Data map[string]json.RawMessage `json:"data"`
		} `json:"items"`
	}
	if e = t.Request(bounded, "GET", "/api/v1/namespaces/"+url.PathEscape(r.Namespace)+"/"+resource, nil, &list); e != nil {
		return nil, e
	}
	out := []*rpc.ReferenceMetadata{}
	for _, i := range list.Items {
		keys := []string{}
		for k := range i.Data {
			keys = append(keys, k)
		}
		out = append(out, &rpc.ReferenceMetadata{Kind: r.Kind, Name: i.Metadata.Name, Namespace: i.Metadata.Namespace, Keys: keys})
	}
	return &rpc.ReferencesResult_{References: out}, nil
}
