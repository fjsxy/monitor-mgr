// 历史迁移仅盘点旧冻结资源，所有 Kubernetes 请求均为 GET。
package control

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/url"
	"time"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/validation"
)

var historicalMigrationSchema = []string{
	`CREATE TABLE historical_operation_releases(instance_id TEXT NOT NULL REFERENCES instances(id),operation_id TEXT NOT NULL REFERENCES operations(id),object_path TEXT NOT NULL,original_uid TEXT NOT NULL,document_fingerprint TEXT NOT NULL,disposition TEXT NOT NULL,released_by TEXT NOT NULL REFERENCES accounts(id),released_at TEXT NOT NULL,snapshot_id TEXT NOT NULL REFERENCES historical_migration_snapshots(id),PRIMARY KEY(instance_id,operation_id))`,
	`CREATE TABLE historical_servicemonitor_resolutions(instance_id TEXT NOT NULL REFERENCES instances(id),operation_id TEXT NOT NULL REFERENCES operations(id),resource_key TEXT NOT NULL,object_path TEXT NOT NULL,original_uid TEXT NOT NULL,document_fingerprint TEXT NOT NULL,resolved_by TEXT NOT NULL REFERENCES accounts(id),resolved_at TEXT NOT NULL,PRIMARY KEY(instance_id,operation_id,resource_key))`,
	`CREATE TABLE historical_migration_snapshots(id TEXT PRIMARY KEY,instance_id TEXT NOT NULL REFERENCES instances(id),observed_at TEXT NOT NULL,document BLOB NOT NULL)`,
}

// migrationPath 只接受冻结文档自身所声明的已知 API 路径，不请求数据库里的任意地址。
func migrationPath(path string, document map[string]any) bool {
	m := objectMap(document, "metadata")
	name := objectString(m, "name")
	ns := objectString(m, "namespace")
	if len(validation.IsDNS1123Subdomain(name)) != 0 {
		return false
	}
	switch objectString(document, "kind") {
	case "KubeStateMetrics":
		return objectString(document, "apiVersion") == "monitoring.fjsxy.github.io/v1alpha1" && ns == "" && path == ksmPath+"/"+url.PathEscape(name)
	case "ServiceMonitor":
		return len(validation.IsDNS1123Label(ns)) == 0 && objectString(document, "apiVersion") == "monitoring.coreos.com/v1" && path == "/apis/monitoring.coreos.com/v1/namespaces/"+url.PathEscape(ns)+"/servicemonitors/"+url.PathEscape(name)
	}
	return false
}

// GetHistoricalMigration 保存有时间戳的独立只读观察快照，旧任务、冻结字节和 UID 不参与更新。
func (s *Store) GetHistoricalMigration(ctx context.Context, c *rpc.RequestContext, r *rpc.InstanceID) (*rpc.HistoricalMigration, error) {
	i, e := s.authorizedInstance(ctx, c, r.Id)
	if e != nil {
		return nil, e
	}
	if i.ReadOnly || i.Component != KSMComponent {
		return nil, Error("CONFLICT", "仅支持纳管 exporter 历史迁移")
	}
	t, e := s.TargetForCluster(ctx, c, i.ClusterId)
	if e != nil {
		return nil, e
	}
	defer t.Close()
	result := &rpc.HistoricalMigration{InstanceId: i.Id, SnapshotId: ID(), ObservedAt: s.Now().UTC().Format(time.RFC3339Nano), SchemaVersion: schemaVersion, CurrentVersionId: i.CurrentVersionId, Resources: []*rpc.HistoricalMigrationResource{}, Agents: []*rpc.HistoricalMigrationAgent{}, ImpactComplete: true}
	_ = s.DB.QueryRowContext(ctx, `SELECT operation_id FROM instance_protections WHERE instance_id=?`, i.Id).Scan(&result.ProtectionOperationId)
	rows, e := s.DB.QueryContext(ctx, `SELECT r.operation_id,r.resource_key,r.object_path,r.original_uid,p.confirmed_uid,p.state,p.possibly_sent,r.document FROM operation_resources r JOIN operation_lifecycle l ON l.operation_id=r.operation_id JOIN resource_progress p ON p.attempt_id=l.current_attempt_id AND p.resource_key=r.resource_key WHERE r.operation_id IN (SELECT operation_id FROM publication_operations WHERE instance_id=? UNION SELECT operation_id FROM deletion_operations WHERE instance_id=?) ORDER BY r.operation_id,r.position`, i.Id, i.Id)
	if e != nil {
		return nil, e
	}
	docs := map[*rpc.HistoricalMigrationResource]map[string]any{}
	for rows.Next() {
		item := new(rpc.HistoricalMigrationResource)
		var confirmed string
		var raw []byte
		if e = rows.Scan(&item.OperationId, &item.ResourceKey, &item.ObjectPath, &item.OriginalUid, &confirmed, &item.State, &item.PossiblySent, &raw); e != nil {
			rows.Close()
			return nil, e
		}
		if confirmed != "" {
			item.OriginalUid = confirmed
		}
		var doc map[string]any
		if json.Unmarshal(raw, &doc) != nil {
			rows.Close()
			return nil, Error("UNAVAILABLE", "旧冻结文档不可读")
		}
		item.DocumentFingerprint = historicalSMDocumentFingerprint(raw)
		item.FrozenYaml = yamlText(safeTargetDocument(doc))
		docs[item] = doc
		result.Resources = append(result.Resources, item)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return nil, e
	}
	selectedDocs := map[*rpc.HistoricalMigrationResource]map[string]any{}
	for _, item := range result.Resources {
		doc := docs[item]
		selectedDocs[item] = doc
		if !migrationPath(item.ObjectPath, doc) {
			item.Error = "原冻结路径不安全"
			continue
		}
		var current map[string]any
		e = t.Request(ctx, "GET", item.ObjectPath, nil, &current)
		switch {
		case apiCode(e) == "TARGET_NOT_FOUND":
			item.Missing = true
		case e != nil:
			item.Error = safeSourceError(e)
		default:
			item.CurrentUid = objectString(objectMap(current, "metadata"), "uid")
			item.ResourceVersion = objectString(objectMap(current, "metadata"), "resourceVersion")
			item.CurrentYaml = yamlText(safeTargetDocument(current))
			selectedDocs[item] = current
		}
	}
	for _, item := range result.Resources {
		_ = s.DB.QueryRowContext(ctx, `SELECT resolved_by,resolved_at FROM historical_servicemonitor_resolutions WHERE instance_id=? AND operation_id=? AND resource_key=? AND object_path=? AND original_uid=? AND document_fingerprint=?`, i.Id, item.OperationId, item.ResourceKey, item.ObjectPath, item.OriginalUid, item.DocumentFingerprint).Scan(&item.ResolvedBy, &item.ResolvedAt)
	}
	s.migrationAgentImpact(ctx, t, result, selectedDocs)
	raw, e := json.Marshal(result)
	if e != nil {
		return nil, e
	}
	_, e = s.DB.ExecContext(ctx, `INSERT INTO historical_migration_snapshots VALUES(?,?,?,?)`, result.SnapshotId, i.Id, result.ObservedAt, raw)
	if e != nil {
		return nil, Error("UNAVAILABLE", "迁移快照保存失败")
	}
	return result, nil
}

// migrationSelector 按原生 LabelSelector 解释对象标签；nil 表示不选择，空 selector 表示全部。
func migrationSelector(raw any, obj map[string]any) bool {
	if raw == nil {
		return false
	}
	data, e := json.Marshal(raw)
	if e != nil {
		return false
	}
	var selector metav1.LabelSelector
	if json.Unmarshal(data, &selector) != nil {
		return false
	}
	parsed, e := metav1.LabelSelectorAsSelector(&selector)
	return e == nil && parsed.Matches(selectorLabels(obj))
}

// migrationAgentImpact 读取所有实际 Agent 及 namespace labels，完整分页失败不能当作无影响。
func (s *Store) migrationAgentImpact(ctx context.Context, t *Target, result *rpc.HistoricalMigration, docs map[*rpc.HistoricalMigrationResource]map[string]any) {
	hasSM := false
	for _, doc := range docs {
		hasSM = hasSM || objectString(doc, "kind") == "ServiceMonitor"
	}
	if !hasSM {
		return
	}
	agents, e := t.listObjects(ctx, "/apis/monitoring.coreos.com/v1alpha1/prometheusagents")
	if e != nil {
		result.ImpactComplete = false
		result.Error = "Agent 选择范围未确认：" + safeSourceError(e)
		return
	}
	namespaces, e := t.listObjects(ctx, "/api/v1/namespaces")
	if e != nil {
		result.ImpactComplete = false
		result.Error = "namespace 选择范围未确认：" + safeSourceError(e)
		return
	}
	nsByName := map[string]map[string]any{}
	for _, ns := range namespaces {
		nsByName[objectString(objectMap(ns, "metadata"), "name")] = ns
	}
	for _, agent := range agents {
		meta := objectMap(agent, "metadata")
		spec := objectMap(agent, "spec")
		entry := &rpc.HistoricalMigrationAgent{Name: objectString(meta, "name"), Namespace: objectString(meta, "namespace"), Uid: objectString(meta, "uid"), SelectedPaths: []string{}, SelectionYaml: yamlText(map[string]any{"serviceMonitorSelector": spec["serviceMonitorSelector"], "serviceMonitorNamespaceSelector": spec["serviceMonitorNamespaceSelector"]})}
		if entry.Name == "" || entry.Namespace == "" || entry.Uid == "" {
			result.ImpactComplete = false
			result.Error = "Agent 身份不完整"
		}
		for item, doc := range docs {
			if objectString(doc, "kind") != "ServiceMonitor" {
				continue
			}
			ns := objectString(objectMap(doc, "metadata"), "namespace")
			namespaceMatches := ns == entry.Namespace
			if raw := spec["serviceMonitorNamespaceSelector"]; raw != nil {
				nsDoc, known := nsByName[ns]
				if !known {
					result.ImpactComplete = false
					result.Error = "旧 SM namespace labels 无法确认"
				}
				namespaceMatches = known && migrationSelector(raw, nsDoc)
			}
			// 关键：已不存在的旧对象不能算当前匹配，读失败也不能伪装成零影响。
			if item.Error != "" {
				result.ImpactComplete = false
				result.Error = "旧SM读取失败，Agent影响范围未确认"
				continue
			}
			if item.Missing {
				continue
			}
			if namespaceMatches && migrationSelector(spec["serviceMonitorSelector"], doc) {
				entry.SelectedPaths = append(entry.SelectedPaths, item.ObjectPath)
			}
		}
		result.Agents = append(result.Agents, entry)
	}
}

// ResolveHistoricalServiceMonitor 仅在原路径确认404后保存独立处理事实，不修改原任务。
func (s *Store) ResolveHistoricalServiceMonitor(ctx context.Context, c *rpc.RequestContext, r *rpc.HistoricalMigrationConfirmationRequest) (*rpc.HistoricalMigrationConfirmation, error) {
	return s.confirmHistoricalMigration(ctx, c, r, false)
}

// ReleaseHistoricalOperation 只解除经独立父身份核验的精确旧操作互斥，旧执行结果原样保留。
func (s *Store) ReleaseHistoricalOperation(ctx context.Context, c *rpc.RequestContext, r *rpc.HistoricalMigrationConfirmationRequest) (*rpc.HistoricalMigrationConfirmation, error) {
	return s.confirmHistoricalMigration(ctx, c, r, true)
}

func (s *Store) confirmHistoricalMigration(ctx context.Context, c *rpc.RequestContext, r *rpc.HistoricalMigrationConfirmationRequest, parent bool) (*rpc.HistoricalMigrationConfirmation, error) {
	i, e := s.authorizedInstanceForWrite(ctx, c, r.Id)
	if e != nil {
		return nil, e
	}
	p, e := s.Authorize(ctx, token(c))
	if e != nil {
		return nil, e
	}
	if e = requireAdmin(p); e != nil {
		return nil, e
	}
	if i.ReadOnly || i.Component != KSMComponent || i.Lifecycle == "DELETED" {
		return nil, Error("CONFLICT", "仅有效纳管 exporter 可确认迁移")
	}
	t, e := s.TargetForCluster(ctx, c, i.ClusterId)
	if e != nil {
		return nil, e
	}
	defer t.Close()
	s.dispatch.Lock()
	defer s.dispatch.Unlock()
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return nil, e
	}
	defer tx.Rollback()
	p, i, e = s.instanceTx(ctx, tx, c, r.Id)
	if e != nil {
		return nil, e
	}
	if e = requireAdmin(p); e != nil {
		return nil, e
	}
	var replay rpc.HistoricalMigrationConfirmation
	action := "RESOLVE_HISTORICAL_SM"
	if parent {
		action = "RELEASE_HISTORICAL_OPERATION"
	}
	cmd, hit, e := s.beginCommand(ctx, tx, p, c, action, i.Id, r, &replay)
	if e != nil {
		return nil, e
	}
	if hit {
		return &replay, nil
	}
	item, doc, e := migrationConfirmationTarget(ctx, tx, i.Id, r)
	if e != nil {
		return nil, e
	}
	if parent {
		return s.releaseHistoricalParent(ctx, tx, t, p, i, r, item, doc, cmd)
	}
	if objectString(doc, "kind") != "ServiceMonitor" {
		return nil, Error("CONFLICT", "此确认仅支持原旧 ServiceMonitor")
	}
	// 关键：原 UID 未知仍为空；同名同spec对象存在时拒绝，不能将它补认为历史原对象。
	var current map[string]any
	if e = t.Request(ctx, "GET", item.ObjectPath, nil, &current); apiCode(e) != "TARGET_NOT_FOUND" {
		return nil, Error("CONFLICT", "原 ServiceMonitor 路径仍存在对象或读取未知；请先由集群管理员显式处理")
	}
	now := s.Now().UTC().Format(time.RFC3339Nano)
	_, e = tx.ExecContext(ctx, `INSERT INTO historical_servicemonitor_resolutions VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(instance_id,operation_id,resource_key) DO NOTHING`, i.Id, item.OperationId, item.ResourceKey, item.ObjectPath, item.OriginalUid, item.DocumentFingerprint, p.Account.Id, now)
	if e != nil {
		return nil, e
	}
	result := &rpc.HistoricalMigrationConfirmation{InstanceId: i.Id, OperationId: item.OperationId, ResourceKey: item.ResourceKey, OriginalUid: item.OriginalUid, SnapshotId: r.SnapshotId}
	if e = tx.QueryRowContext(ctx, `SELECT resolved_by,resolved_at FROM historical_servicemonitor_resolutions WHERE instance_id=? AND operation_id=? AND resource_key=? AND object_path=? AND original_uid=? AND document_fingerprint=?`, i.Id, item.OperationId, item.ResourceKey, item.ObjectPath, item.OriginalUid, item.DocumentFingerprint).Scan(&result.ResolvedBy, &result.ResolvedAt); e != nil {
		return nil, Error("CONFLICT", "旧处理记录与原冻结身份不同")
	}
	if e = audit(ctx, tx, p.Account.Id, "RESOLVE_HISTORICAL_SM", item.OperationId, s.Now()); e != nil {
		return nil, e
	}
	if e = s.finishCommand(ctx, tx, cmd, result); e != nil {
		return nil, e
	}
	if e = tx.Commit(); e != nil {
		return nil, e
	}
	return result, nil
}

// migrationConfirmationTarget 将显式确认绑定保存的完整盘点和当前原冻结记录，而不是客户端任意路径。
func migrationConfirmationTarget(ctx context.Context, tx *sql.Tx, instance string, r *rpc.HistoricalMigrationConfirmationRequest) (*rpc.HistoricalMigrationResource, map[string]any, error) {
	if !r.ConfirmExternalHandling {
		return nil, nil, Error("INVALID_INPUT", "必须明确确认已核验采集影响并在平台外处理原对象")
	}
	var snapshot []byte
	if e := tx.QueryRowContext(ctx, `SELECT document FROM historical_migration_snapshots WHERE id=? AND instance_id=?`, r.SnapshotId, instance).Scan(&snapshot); e != nil {
		return nil, nil, Error("CONFLICT", "原迁移盘点快照不存在")
	}
	var saved rpc.HistoricalMigration
	if json.Unmarshal(snapshot, &saved) != nil || !saved.ImpactComplete {
		return nil, nil, Error("CONFLICT", "Agent 选择影响尚未完整核验，不能确认处理")
	}
	var item *rpc.HistoricalMigrationResource
	for _, candidate := range saved.Resources {
		if candidate.OperationId == r.OperationId && candidate.ResourceKey == r.ResourceKey && candidate.ObjectPath == r.ObjectPath && candidate.OriginalUid == r.OriginalUid && candidate.DocumentFingerprint == r.DocumentFingerprint {
			item = candidate
			break
		}
	}
	if item == nil || item.Error != "" {
		return nil, nil, Error("CONFLICT", "确认与原完整盘点身份不一致")
	}
	var raw []byte
	var original, confirmed string
	e := tx.QueryRowContext(ctx, `SELECT r.document,r.original_uid,p.confirmed_uid FROM operation_resources r JOIN operation_lifecycle l ON l.operation_id=r.operation_id JOIN resource_progress p ON p.attempt_id=l.current_attempt_id AND p.resource_key=r.resource_key WHERE r.operation_id=? AND r.resource_key=? AND r.object_path=? AND r.operation_id IN (SELECT operation_id FROM publication_operations WHERE instance_id=? UNION SELECT operation_id FROM deletion_operations WHERE instance_id=?)`, r.OperationId, r.ResourceKey, r.ObjectPath, instance, instance).Scan(&raw, &original, &confirmed)
	if confirmed != "" {
		original = confirmed
	}
	var doc map[string]any
	if e != nil || json.Unmarshal(raw, &doc) != nil || original != r.OriginalUid || historicalSMDocumentFingerprint(raw) != r.DocumentFingerprint || !migrationPath(r.ObjectPath, doc) {
		return nil, nil, Error("CONFLICT", "原冻结记录已变化，须重新盘点")
	}
	return item, doc, nil
}

// releaseHistoricalParent 只使用原持久 UID 和冻结 spec；SM 的404绝不能证明父CR身份。
func (s *Store) releaseHistoricalParent(ctx context.Context, tx *sql.Tx, t *Target, p *Principal, i *rpc.ObservationInstance, r *rpc.HistoricalMigrationConfirmationRequest, item *rpc.HistoricalMigrationResource, doc map[string]any, cmd *command) (*rpc.HistoricalMigrationConfirmation, error) {
	if item.ResourceKey != "component" || objectString(doc, "kind") != "KubeStateMetrics" || !historicalResourceOperation(ctx, tx, item.OperationId) {
		return nil, Error("CONFLICT", "只有旧多资源任务的原父 CR 可确认解阻")
	}
	var protected, action, version string
	if tx.QueryRowContext(ctx, `SELECT operation_id FROM instance_protections WHERE instance_id=?`, i.Id).Scan(&protected) != nil || protected != item.OperationId {
		return nil, Error("CONFLICT", "实例保护已变化，不能解除其他操作")
	}
	if tx.QueryRowContext(ctx, `SELECT o.action,p.version_id FROM operations o JOIN publication_operations p ON p.operation_id=o.id WHERE o.id=? AND p.instance_id=?`, item.OperationId, i.Id).Scan(&action, &version) != nil || action != "PUBLISH" {
		return nil, Error("CONFLICT", "旧删除或非发布操作不能从此入口解除")
	}
	if i.CurrentVersionId != "" && i.CurrentVersionId != version {
		return nil, Error("CONFLICT", "已有较新发布基线，不允许回退旧任务身份")
	}
	var current map[string]any
	e := t.Request(ctx, "GET", item.ObjectPath, nil, &current)
	disposition := "VERIFIED_ORIGINAL"
	if item.OriginalUid == "" {
		// 关键：原父 UID 未知且对象仍在时，同名同配置也拒绝；只有单独明确处理且404可转入全新创建。
		if apiCode(e) != "TARGET_NOT_FOUND" || i.Uid != "" {
			return nil, Error("CONFLICT", "原父 CR UID 未确认且原路径仍有对象或结果未知，不能认领")
		}
		disposition = "EXPLICIT_ABSENT"
	} else {
		if e != nil || objectString(objectMap(current, "metadata"), "uid") != item.OriginalUid {
			return nil, Error("CONFLICT", "原父 CR UID 不符或读取未知")
		}
		if i.Uid != "" && i.Uid != item.OriginalUid {
			return nil, Error("CONFLICT", "实例已有其他已知 UID，不能回退")
		}
		if componentFor(i.Component).normalizeSpec(objectMap(current, "spec")) != componentFor(i.Component).normalizeSpec(objectMap(doc, "spec")) {
			return nil, Error("CONFLICT", "原父 CR 与冻结基线漂移，须先核实后再处理")
		}
	}
	if e = validateHistoricalCollection(ctx, tx, i, map[string]any{"serviceMonitor": map[string]any{"enabled": true}}, t); e != nil {
		return nil, e
	}
	now := s.Now().UTC().Format(time.RFC3339Nano)
	if _, e = tx.ExecContext(ctx, `INSERT INTO historical_operation_releases VALUES(?,?,?,?,?,?,?,?,?)`, i.Id, item.OperationId, item.ObjectPath, item.OriginalUid, item.DocumentFingerprint, disposition, p.Account.Id, now, r.SnapshotId); e != nil {
		return nil, e
	}
	if item.OriginalUid != "" && i.Uid == "" {
		// 关键：空实例 UID 仅从已持久确认的父步骤恢复，CAS 不覆盖后来已知身份，且不提升 current_version_id。
		result, e := tx.ExecContext(ctx, `UPDATE instances SET uid=? WHERE id=? AND uid='' AND current_version_id=?`, item.OriginalUid, i.Id, i.CurrentVersionId)
		if e != nil {
			return nil, e
		}
		n, _ := result.RowsAffected()
		if n != 1 {
			return nil, Error("CONFLICT", "原父身份恢复条件已变化")
		}
		if e = audit(ctx, tx, p.Account.Id, "RESTORE_CONFIRMED_COMPONENT_UID", item.OperationId, s.Now()); e != nil {
			return nil, e
		}
	}
	deleted, e := tx.ExecContext(ctx, `DELETE FROM instance_protections WHERE instance_id=? AND operation_id=?`, i.Id, item.OperationId)
	if e != nil {
		return nil, e
	}
	n, _ := deleted.RowsAffected()
	if n != 1 {
		return nil, Error("CONFLICT", "原互斥保护已变化")
	}
	result := &rpc.HistoricalMigrationConfirmation{InstanceId: i.Id, OperationId: item.OperationId, ResourceKey: item.ResourceKey, OriginalUid: item.OriginalUid, ResolvedBy: p.Account.Id, ResolvedAt: now, SnapshotId: r.SnapshotId, ParentReleased: true}
	if e = audit(ctx, tx, p.Account.Id, "RELEASE_HISTORICAL_OPERATION", item.OperationId, s.Now()); e != nil {
		return nil, e
	}
	if e = s.finishCommand(ctx, tx, cmd, result); e != nil {
		return nil, e
	}
	if e = tx.Commit(); e != nil {
		return nil, e
	}
	return result, nil
}
