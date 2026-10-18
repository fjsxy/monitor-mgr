// 本文件提供集群生命周期、当前归属授权和只读元信息的管控入口。
package control

import (
	"context"
	"database/sql"
	"encoding/json"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"net/url"
	"sort"
	"strings"
	"time"
)

const clusterColumns = `id,name,owner_id,uid,identity,kubernetes_version,state,credential_revision,revision,capabilities,created_at`
const operationColumns = `id,action,state,cluster_id,actor_id,message,created_at,updated_at,credential_revision,owner_id,owner_before`

// scanCluster 将持久化的非敏感集群列转换为 RPC 对象。
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

// scanOperation 读取可展示的操作历史，不读取当前或候选凭据。
func scanOperation(row interface{ Scan(...any) error }) (*rpc.Operation, error) {
	o := new(rpc.Operation)
	e := row.Scan(&o.OperationId, &o.Action, &o.State, &o.ClusterId, &o.ActorId, &o.Message, &o.CreatedAt, &o.UpdatedAt, &o.CredentialRevision, &o.OwnerId, &o.PreviousOwnerId)
	return o, e
}

// clusterQuery 统一数据库与事务内的单行读取边界。
type clusterQuery interface {
	QueryRowContext(context.Context, string, ...any) *sql.Row
}

// clusterByID 读取同一 clusterId 的管理记录，并区分不存在与读取失败。
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

// clusterPermission 按当前管理员角色或唯一归属决定访问权。
// 关键：历史操作者不是授权依据，转移后旧所属用户不能凭历史继续访问。
func clusterPermission(p *Principal, c *rpc.Cluster) error {
	if p.Account.Role != "ADMIN" && c.OwnerId != p.Account.Id {
		return Error("FORBIDDEN", "无权访问此集群")
	}
	return nil
}

// enabledOwner 验证注册、转移或重新启用指定的所属用户仍存在且启用。
func enabledOwner(ctx context.Context, q clusterQuery, id string) error {
	var enabled bool
	e := q.QueryRowContext(ctx, `SELECT enabled FROM accounts WHERE id=?`, id).Scan(&enabled)
	if e != nil || !enabled {
		return Error("INVALID_INPUT", "所属用户必须存在且启用")
	}
	return nil
}

// ListClusters 仅列出当前账号有权访问的集群，管理员可查看全部记录。
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

// GetCluster 先校验真实会话与当前归属，再返回集群元信息。
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

// RegisterCluster 受理一个新集群的异步凭据核验，不把候选立即视为有效登记。
func (s *Store) RegisterCluster(ctx context.Context, c *rpc.RequestContext, r *rpc.RegisterClusterRequest) (*rpc.Operation, error) {
	return s.acceptCredential(ctx, c, "REGISTER", ID(), r.OwnerId, r.Name, r.Context, r.Kubeconfig, r)
}

// UpdateCredential 受理候选验证；成功前旧凭据继续作为有效访问材料。
func (s *Store) UpdateCredential(ctx context.Context, c *rpc.RequestContext, r *rpc.CredentialRequest) (*rpc.Operation, error) {
	return s.acceptCredential(ctx, c, "CREDENTIAL", r.Id, "", "", r.Context, r.Kubeconfig, r)
}

// ReactivateCluster 为已注销记录受理新凭据与启用归属，原 UID 必须在核验时一致。
func (s *Store) ReactivateCluster(ctx context.Context, c *rpc.RequestContext, r *rpc.CredentialRequest) (*rpc.Operation, error) {
	return s.acceptCredential(ctx, c, "REACTIVATE", r.Id, r.GetOwnerId(), "", r.Context, r.Kubeconfig, r)
}

// acceptCredential 将当前授权、幂等结果、操作与候选密文原子受理。
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
	// 关键：先在事务内授权再命中幂等；旧结果不能绕过账号角色或归属生命周期。
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
	o := &rpc.Operation{OperationId: ID(), Action: action, State: "QUEUED", ClusterId: id, ActorId: p.Account.Id, Message: "已受理，候选尚未核验生效", CreatedAt: now, UpdatedAt: now, OwnerId: owner}
	// 关键：操作记录只保存身份与修订，候选单独加密，避免历史成为明文凭据旁路。
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

// HasUnfinishedChangesTx 在当前事务内检查集群的全部未结束变更。
// 关键：包括未来操作及未知状态，不能只检查凭据类型而放行仍受保护的转移或注销。
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

// TransferCluster 在无未结束变更时调整唯一归属并保留前后归属历史。
func (s *Store) TransferCluster(ctx context.Context, c *rpc.RequestContext, r *rpc.TransferClusterRequest) (*rpc.Cluster, error) {
	return s.clusterMutation(ctx, c, "TRANSFER", r.Id, r.OwnerId, r)
}

// UnregisterCluster 结束平台管理并清除访问凭据，本身不删除目标资源。
func (s *Store) UnregisterCluster(ctx context.Context, c *rpc.RequestContext, r *rpc.ClusterID) (*rpc.Cluster, error) {
	return s.clusterMutation(ctx, c, "UNREGISTER", r.Id, "", r)
}

// clusterMutation 在同一事务内执行转移或注销及对应审计、幂等结果。
func (s *Store) clusterMutation(ctx context.Context, c *rpc.RequestContext, action, id, owner string, request any) (*rpc.Cluster, error) {
	s.dispatch.Lock()
	defer s.dispatch.Unlock()
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
	// 关键：转移与注销保护所有未结束类型，结果不明时不能丢失管理责任。
	if e = s.HasUnfinishedChangesTx(ctx, tx, id); e != nil {
		return nil, e
	}
	previousOwner := cl.OwnerId
	if action == "TRANSFER" {
		if e = enabledOwner(ctx, tx, owner); e != nil {
			return nil, e
		}
		if _, e = tx.ExecContext(ctx, `UPDATE clusters SET owner_id=?,revision=revision+1 WHERE id=?`, owner, id); e != nil {
			return nil, Error("UNAVAILABLE", "转移失败")
		}
	} else {
		// 关键：注销同时清除当前与全部候选凭据；保留历史不能保留可继续访问的材料。
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
	if _, e = tx.ExecContext(ctx, `INSERT INTO operations(id,action,state,cluster_id,actor_id,owner_id,owner_before,name,message,created_at,updated_at,credential_revision) VALUES(?,?,'SUCCEEDED',?,?,?,?,?,?,?,?,?)`, ID(), action, id, p.Account.Id, cl.OwnerId, previousOwner, cl.Name, map[bool]string{true: "集群已注销；目标 CR 和工作负载可能继续运行", false: "归属已转移"}[action == "UNREGISTER"], now, now, cl.CredentialRevision); e != nil {
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

// GetOperation 按当前集群归属授权查询结果，未登记候选仅管理员可查。
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
	s.enrichPublicationOperation(ctx, s.DB, o)
	return o, nil
}

// ListClusterOperations 在当前授权后列出集群的脱敏管理历史。
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

// ContinueOperation 仅允许管理员明确继续集群注册或凭据的只读核验。
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
	// 关键：白名单先于幂等回读，禁止借此入口重排发布、删除或未知操作。
	if o.Action != "REGISTER" && o.Action != "CREDENTIAL" && o.Action != "REACTIVATE" {
		return nil, Error("CONFLICT", "此入口仅可继续集群注册或凭据只读核验")
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
	o.Message = "已明确继续凭据只读核验"
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

// TargetForCluster 通过当前授权读取有效凭据，再创建受统一预算限制的目标客户端。
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
	target, e := newTarget(cred)
	if e != nil {
		return nil, e
	}
	target.limits = s.limits
	target.clusterID = id
	return target, nil
}

// ListNamespaces 返回有权访问集群的完整 namespace 列表，失败不冒充空集。
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

// ListReferences 返回 Secret/ServiceAccount 引用元信息，不返回 Secret value。
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
	out := []*rpc.ReferenceMetadata{}
	// 关键：只有所有分页成功才返回完整引用集，避免部分结果使依赖校验误判。
	continuation := ""
	seen := map[string]bool{}
	for {
		var list struct {
			Metadata struct {
				Continue string `json:"continue"`
			} `json:"metadata"`
			Items []struct {
				Metadata struct {
					Name      string `json:"name"`
					Namespace string `json:"namespace"`
				} `json:"metadata"`
				Data map[string]json.RawMessage `json:"data"`
			} `json:"items"`
		}
		if e = t.Request(bounded, "GET", "/api/v1/namespaces/"+url.PathEscape(r.Namespace)+"/"+resource+"?limit=500&continue="+url.QueryEscape(continuation), nil, &list); e != nil {
			return nil, e
		}
		for _, i := range list.Items {
			keys := []string{}
			for k := range i.Data {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			out = append(out, &rpc.ReferenceMetadata{Kind: r.Kind, Name: i.Metadata.Name, Namespace: i.Metadata.Namespace, Keys: keys})
		}
		if list.Metadata.Continue == "" {
			break
		}
		if seen[list.Metadata.Continue] {
			return nil, Error("TARGET_ERROR", "引用元信息分页未前进，结果不完整")
		}
		seen[list.Metadata.Continue] = true
		continuation = list.Metadata.Continue
	}
	return &rpc.ReferencesResult_{References: out}, nil
}
