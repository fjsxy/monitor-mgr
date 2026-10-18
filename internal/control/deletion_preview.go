// 删除预览保留原UID、部署版本和已确认影响范围，目标完整配置仅以HMAC摘要持久化。
package control

import (
	"context"
	"database/sql"
	"encoding/json"
	"sort"
	"time"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

// deletionPreviewSchema 独立追加v7；旧未派发删除保留保护，须明确重新预览后继续。
var deletionPreviewSchema = []string{
	`CREATE TABLE deletion_previews(id TEXT PRIMARY KEY,instance_id TEXT NOT NULL REFERENCES instances(id),version_id TEXT NOT NULL REFERENCES publication_versions(id),original_uid TEXT NOT NULL,operation_id TEXT NOT NULL,target_fingerprint TEXT NOT NULL,impact_fingerprint TEXT NOT NULL,target_namespace TEXT NOT NULL,impact_namespaces BLOB NOT NULL,created_at TEXT NOT NULL)`,
	`ALTER TABLE deletion_operations ADD COLUMN preview_id TEXT NOT NULL DEFAULT ''`,
}

type deletionSnapshot struct {
	target, impact, namespace string
	namespaces                []string
	storage                   *agentStorageSnapshot
}

// readDeletionSnapshot 完整读取原资源图，以namespace范围表达影响，忽略Pod数量/UID的自然滚动。
func (s *Store) readDeletionSnapshot(ctx context.Context, i *rpc.ObservationInstance, t *Target) (*deletionSnapshot, error) {
	cr, e := t.readComponent(ctx, i)
	if e != nil {
		return nil, e
	}
	if e = checkTarget(i, cr); e != nil {
		return nil, e
	}
	ns := instanceCRNamespace(i)
	if componentFor(i.Component).scope == "Cluster" {
		ns = objectString(objectMap(cr, "spec"), "targetNamespace")
	}
	if ns == "" {
		return nil, Error("CONFLICT", "删除目标命名空间无法确认")
	}
	if _, e = s.observeOwnerGraph(ctx, i, t, ns); e != nil {
		return nil, e
	}
	rows, e := s.DB.QueryContext(ctx, `SELECT DISTINCT namespace FROM seen_resources WHERE instance_id=?`, i.Id)
	if e != nil {
		return nil, e
	}
	scope := map[string]bool{ns: true}
	for rows.Next() {
		var value string
		if e = rows.Scan(&value); e != nil {
			rows.Close()
			return nil, e
		}
		scope[value] = true
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return nil, e
	}
	namespaces := make([]string, 0, len(scope))
	for value := range scope {
		namespaces = append(namespaces, value)
	}
	sort.Strings(namespaces)
	var storage *agentStorageSnapshot
	impact := s.fingerprint(i, "delete-impact/v1:"+canonical(namespaces))
	if i.Component == AgentComponent {
		storage, e = s.readAgentStorageTransition(ctx, i, t, cr, objectMap(cr, "spec"), true)
		if e != nil {
			return nil, e
		}
		// 关键：实际策略和原PVC UID属于确认影响；自然Pod滚动不改变影响。
		impact = s.fingerprint(i, "delete-impact/agent-storage/v3:"+canonical(namespaces)+":"+s.agentStorageImpact(i, storage))
	}
	// 关键：目标摘要不含status/RV，影响摘要不含易变Pod UID；新增跨namespace原owner链仍使确认过期。
	return &deletionSnapshot{target: s.fingerprint(i, "delete-target/v1:"+componentFor(i.Component).normalizeSpec(objectMap(cr, "spec"))), impact: impact, namespace: ns, namespaces: namespaces, storage: storage}, nil
}

// observeDeletionForUser 仅用当前授权凭据读取，并在任何预览/受理前核对原集群UID。
func (s *Store) observeDeletionForUser(ctx context.Context, c *rpc.RequestContext, i *rpc.ObservationInstance) (*deletionSnapshot, error) {
	t, e := s.TargetForCluster(ctx, c, i.ClusterId)
	if e != nil {
		return nil, e
	}
	defer t.Close()
	cl, e := s.GetCluster(ctx, c, &rpc.ClusterID{Id: i.ClusterId})
	if e != nil {
		return nil, e
	}
	var root map[string]any
	if e = t.Request(ctx, "GET", "/api/v1/namespaces/kube-system", nil, &root); e != nil {
		return nil, e
	}
	if objectString(objectMap(root, "metadata"), "uid") != cl.Uid {
		return nil, Error("CONFLICT", "原集群UID已变化")
	}
	return s.readDeletionSnapshot(ctx, i, t)
}

// deletionPreviewIdentity 允许同一受保护操作重新确认，但不能换原UID、版本或借另一操作解除保护。
func deletionPreviewIdentity(ctx context.Context, q clusterQuery, i *rpc.ObservationInstance, operation string) error {
	if i.ReadOnly {
		return Error("FORBIDDEN", "未纳管实例只读")
	}
	if i.Lifecycle == "DELETED" || i.Uid == "" || i.CurrentVersionId == "" {
		return Error("CONFLICT", "没有确认的原CR UID或部署版本，不能删除")
	}
	var protected string
	e := q.QueryRowContext(ctx, `SELECT operation_id FROM instance_protections WHERE instance_id=?`, i.Id).Scan(&protected)
	if e != nil && e != sql.ErrNoRows {
		return e
	}
	if protected != "" && protected != operation {
		return Error("CONFLICT", "实例仍有另一未结束变更")
	}
	if operation != "" {
		var uid, version, state string
		e = q.QueryRowContext(ctx, `SELECT d.original_uid,d.version_id,o.state FROM deletion_operations d JOIN operations o ON o.id=d.operation_id WHERE d.operation_id=? AND d.instance_id=?`, operation, i.Id).Scan(&uid, &version, &state)
		if e != nil || uid != i.Uid || version != i.CurrentVersionId {
			return Error("CONFLICT", "删除原身份或版本已变化")
		}
		if state != "FAILED" && state != "WAITING_CONFIRMATION" {
			return Error("CONFLICT", "此操作只能只读核验，不能重新确认派发")
		}
	}
	return nil
}

// PreviewDeletion 输出可阅读的名称/namespace范围，完整目标值不回显、不存入历史。
func (s *Store) PreviewDeletion(ctx context.Context, c *rpc.RequestContext, r *rpc.DeletionPreviewRequest) (*rpc.DeletionPreview, error) {
	i, e := s.authorizedInstance(ctx, c, r.Id)
	if e != nil {
		return nil, e
	}
	if e = rejectUnknownFields(r, "request"); e != nil {
		return nil, e
	}
	if e = deletionPreviewIdentity(ctx, s.DB, i, r.GetOperationId()); e != nil {
		return nil, e
	}
	snapshot, e := s.observeDeletionForUser(ctx, c, i)
	if e != nil {
		return nil, e
	}
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return nil, e
	}
	defer tx.Rollback()
	_, latest, e := s.instanceTx(ctx, tx, c, i.Id)
	if e != nil {
		return nil, e
	}
	if latest.Uid != i.Uid || latest.CurrentVersionId != i.CurrentVersionId {
		return nil, Error("CONFLICT", "预览期间原身份或部署版本变化")
	}
	if e = deletionPreviewIdentity(ctx, tx, latest, r.GetOperationId()); e != nil {
		return nil, e
	}
	preview := &rpc.DeletionPreview{PreviewId: ID(), InstanceId: i.Id, VersionId: i.CurrentVersionId, Uid: i.Uid, Name: i.Name, Namespace: snapshot.namespace, ImpactNamespaces: snapshot.namespaces}
	if snapshot.storage != nil {
		preview.StorageMode = reference(snapshot.storage.mode)
		preview.StorageVolumes = snapshotVolumes(snapshot.storage, i.Uid)
		preview.Policies = snapshotPolicies(snapshot.storage)
	}
	ranges, _ := json.Marshal(snapshot.namespaces)
	if _, e = tx.ExecContext(ctx, `INSERT INTO deletion_previews VALUES(?,?,?,?,?,?,?,?,?,?)`, preview.PreviewId, i.Id, i.CurrentVersionId, i.Uid, r.GetOperationId(), snapshot.target, snapshot.impact, snapshot.namespace, ranges, s.Now().UTC().Format(time.RFC3339Nano)); e != nil {
		return nil, e
	}
	if e = tx.Commit(); e != nil {
		return nil, e
	}
	return preview, nil
}

// validateDeletionPreview 同时用于受理和实际派发；最新RV只作条件写，不代替用户确认过的意图。
func (s *Store) validateDeletionPreview(ctx context.Context, q clusterQuery, i *rpc.ObservationInstance, preview, version, operation string, snapshot *deletionSnapshot) error {
	if preview == "" {
		return Error("INVALID_INPUT", "缺少删除预览，请重新核对原身份和影响范围")
	}
	var boundInstance, boundVersion, uid, boundOperation, target, impact string
	e := q.QueryRowContext(ctx, `SELECT instance_id,version_id,original_uid,operation_id,target_fingerprint,impact_fingerprint FROM deletion_previews WHERE id=?`, preview).Scan(&boundInstance, &boundVersion, &uid, &boundOperation, &target, &impact)
	if e != nil || boundInstance != i.Id || boundVersion != version || uid != i.Uid || boundOperation != operation {
		return Error("CONFLICT", "删除预览不属于本次原身份与版本，请重新确认")
	}
	if snapshot == nil || target != snapshot.target || impact != snapshot.impact {
		return Error("CONFLICT", "目标配置或删除影响已变化，请重新预览确认")
	}
	return nil
}
