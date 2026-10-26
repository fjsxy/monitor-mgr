// 逐资源模型分开保存不可变版本目标、操作目标及每次尝试的执行结果。
package control

import (
	"context"
	"database/sql"
	"encoding/json"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

var resourceProgressSchema = []string{
	`CREATE TABLE version_resources(version_id TEXT NOT NULL REFERENCES publication_versions(id),resource_key TEXT NOT NULL,position INTEGER NOT NULL,collection_path TEXT NOT NULL,object_path TEXT NOT NULL,document BLOB NOT NULL,PRIMARY KEY(version_id,resource_key),UNIQUE(version_id,position))`,
	`CREATE TABLE operation_resources(operation_id TEXT NOT NULL REFERENCES operations(id),resource_key TEXT NOT NULL,position INTEGER NOT NULL,collection_path TEXT NOT NULL,object_path TEXT NOT NULL,document BLOB NOT NULL,original_uid TEXT NOT NULL,PRIMARY KEY(operation_id,resource_key),UNIQUE(operation_id,position))`,
	`CREATE TABLE resource_progress(attempt_id TEXT NOT NULL REFERENCES execution_attempts(id),resource_key TEXT NOT NULL,confirmed_uid TEXT NOT NULL,possibly_sent INTEGER NOT NULL,state TEXT NOT NULL,message TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(attempt_id,resource_key))`,
	`CREATE TABLE instance_resource_identities(instance_id TEXT NOT NULL REFERENCES instances(id),resource_key TEXT NOT NULL,uid TEXT NOT NULL,PRIMARY KEY(instance_id,resource_key))`,
}

// frozenResource 保留实际请求路径与完整有效文档；执行时不依赖新映射默认值。
type frozenResource struct {
	key, collection, path string
	position              int
	document              map[string]any
	originalUID           string
}

// primaryFrozenResource 从冻结文档投影唯一请求身份，保持创建与恢复使用同一规则。
func primaryFrozenResource(i *rpc.ObservationInstance, cfg configuration, spec map[string]any) frozenResource {
	doc := cfg.document()
	doc["spec"] = spec
	a := componentFor(i.Component)
	identity := resourceInstance(i, frozenResource{document: doc}, i.Uid)
	return frozenResource{key: "component", collection: a.collectionPath(instanceCRNamespace(identity)), path: a.objectPath(identity), document: doc, originalUID: i.Uid}
}

// public 仅返回已脱敏冻结文档，不泄漏内部请求路径及凭据。
func (r frozenResource) public() *rpc.FrozenResource {
	m := objectMap(r.document, "metadata")
	return &rpc.FrozenResource{Key: r.key, ApiVersion: objectString(r.document, "apiVersion"), Kind: objectString(r.document, "kind"), Namespace: objectString(m, "namespace"), Name: objectString(m, "name"), Yaml: yamlText(safeTargetDocument(r.document))}
}

// freezePublicationResources 仅为明确的新发布冻结平台实际写入的单 CR；旧冻结版本迁移不调用此入口。
func freezePublicationResources(ctx context.Context, tx *sql.Tx, version string, i *rpc.ObservationInstance, cfg configuration, spec map[string]any) error {
	resources := []frozenResource{primaryFrozenResource(i, cfg, spec)}
	return insertFrozenVersionResources(ctx, tx, version, resources)
}

// insertFrozenVersionResources 在同一事务保存完整路径及文档，不接受后续草稿覆盖。
func insertFrozenVersionResources(ctx context.Context, tx *sql.Tx, version string, resources []frozenResource) error {
	for position, r := range resources {
		if _, e := tx.ExecContext(ctx, `INSERT INTO version_resources VALUES(?,?,?,?,?,?)`, version, r.key, position, r.collection, r.path, []byte(canonical(r.document))); e != nil {
			return e
		}
	}
	return nil
}

// readFrozenResources 按原顺序读取冻结集合，旧双 CR 仍可准确展示。
func readFrozenResources(ctx context.Context, q clusterQuery, id string, operation bool) ([]frozenResource, error) {
	query := `SELECT resource_key,position,collection_path,object_path,document,'' FROM version_resources WHERE version_id=? ORDER BY position`
	if operation {
		query = `SELECT resource_key,position,collection_path,object_path,document,original_uid FROM operation_resources WHERE operation_id=? ORDER BY position`
	}
	rows, e := q.QueryContext(ctx, query, id)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []frozenResource{}
	for rows.Next() {
		var r frozenResource
		var raw []byte
		if e = rows.Scan(&r.key, &r.position, &r.collection, &r.path, &raw, &r.originalUID); e != nil {
			return nil, e
		}
		if json.Unmarshal(raw, &r.document) != nil || r.document == nil {
			return nil, Error("UNAVAILABLE", "冻结资源文档不可读")
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// validateFrozenInstanceIdentity 将原操作冻结组件身份与当前实例对照，不从最新草稿重算旧目标。
func validateFrozenInstanceIdentity(ctx context.Context, q clusterQuery, i *rpc.ObservationInstance, operation string) error {
	resources, e := readFrozenResources(ctx, q, operation, true)
	if e != nil {
		return e
	}
	for _, r := range resources {
		if r.key != "component" {
			continue
		}
		metadata := objectMap(r.document, "metadata")
		namespace := objectString(metadata, "namespace")
		if i.Component == KSMComponent {
			namespace = objectString(objectMap(r.document, "spec"), "targetNamespace")
		}
		// 关键：首次确定失败后草稿可合法更名；旧重试不能把旧目标UID绑定到新身份，也不能回写草稿。
		if objectString(metadata, "name") != i.Name || namespace != i.TargetNamespace {
			return Error("CONFLICT", "实例 name/namespace 已偏离原冻结目标，请预览并明确发布当前草稿")
		}
		return nil
	}
	return Error("UNAVAILABLE", "原冻结组件身份缺失")
}

// freezeOperationResources 在受理事务内复制目标并绑定原身份；后续草稿及实例字段不能改变目标。
func freezeOperationResources(ctx context.Context, tx *sql.Tx, o *rpc.Operation, i *rpc.ObservationInstance) error {
	resources, e := readFrozenResources(ctx, tx, o.GetVersionId(), false)
	if e != nil {
		return e
	}
	if len(resources) != 1 || resources[0].key != "component" || objectString(resources[0].document, "kind") != componentFor(i.Component).kind {
		return Error("CONFLICT", historicalPendingMessage)
	}
	for _, r := range resources {
		uid := ""
		e = tx.QueryRowContext(ctx, `SELECT uid FROM instance_resource_identities WHERE instance_id=? AND resource_key=?`, i.Id, r.key).Scan(&uid)
		if e != nil && e != sql.ErrNoRows {
			return e
		}
		if r.key == "component" {
			uid = i.Uid
		}
		if o.Action == "DELETE" && uid == "" {
			return Error("CONFLICT", "删除资源身份未确认")
		}
		if _, e = tx.ExecContext(ctx, `INSERT INTO operation_resources VALUES(?,?,?,?,?,?,?)`, o.OperationId, r.key, r.position, r.collection, r.path, []byte(canonical(r.document)), uid); e != nil {
			return e
		}
		if _, e = tx.ExecContext(ctx, `INSERT INTO resource_progress VALUES(?,?,?,0,'QUEUED','已受理，尚未发送',?)`, o.GetAttemptId(), r.key, uid, o.UpdatedAt); e != nil {
			return e
		}
	}
	return nil
}

// resourceProgressFor 公开每次尝试的独立资源事实及附加只读观察。
func resourceProgressFor(ctx context.Context, q clusterQuery, op, attempt string) ([]*rpc.ResourceProgress, error) {
	resources, e := readFrozenResources(ctx, q, op, true)
	if e != nil {
		return nil, e
	}
	out := []*rpc.ResourceProgress{}
	for _, r := range resources {
		p := &rpc.ResourceProgress{Target: r.public()}
		if e = q.QueryRowContext(ctx, `SELECT confirmed_uid,possibly_sent,state,message,updated_at,attempt_id FROM resource_progress WHERE attempt_id=? AND resource_key=?`, attempt, r.key).Scan(&p.ConfirmedUid, &p.PossiblySent, &p.State, &p.Message, &p.UpdatedAt, &p.AttemptId); e != nil {
			return nil, e
		}
		out = append(out, p)
	}
	projected := &rpc.Operation{OperationId: op, Resources: out}
	projectHistoricalResources(ctx, q, projected)
	return out, nil
}

// enrichResourceProgress 将资源事实加入公共操作，只在响应中标记历史待处理。
func enrichResourceProgress(ctx context.Context, q clusterQuery, o *rpc.Operation) {
	if o.GetAttemptId() != "" {
		o.Resources, _ = resourceProgressFor(ctx, q, o.OperationId, o.GetAttemptId())
		var disposition string
		if q.QueryRowContext(ctx, `SELECT disposition FROM historical_operation_releases WHERE operation_id=?`, o.OperationId).Scan(&disposition) == nil && disposition == "EXPLICIT_DELETION_ABSENT" {
			o.State = "HISTORICAL_RESOLVED"
			o.Message = "管理员已明确确认外部清理并解除原删除互斥；旧执行状态和未知发送事实保持原样"
			return
		}
		for _, p := range o.Resources {
			if p.GetHistoricalPending() {
				o.State = "HISTORICAL_PENDING"
				o.Message = historicalPendingMessage
				break
			}
		}
	}
}

// markResourceDispatch 与操作派发资格同事务保存单份资源的发送边界。
func markResourceDispatch(ctx context.Context, tx *sql.Tx, o *rpc.Operation, key, now string) error {
	result, e := tx.ExecContext(ctx, `UPDATE resource_progress SET state='DISPATCHED',possibly_sent=1,message='已取得该资源派发资格，结果尚未确认',updated_at=? WHERE attempt_id=? AND resource_key=? AND state NOT IN ('SUCCEEDED','CANCELLED')`, now, o.GetAttemptId(), key)
	if e != nil {
		return e
	}
	n, _ := result.RowsAffected()
	if n != 1 {
		return Error("ALREADY_FINISHED", "资源已结束或缺失")
	}
	return nil
}

// recordResourceResult 与操作结果同事务保存 UID；已成功事实不能被后续恢复降级。
func recordResourceResult(ctx context.Context, tx *sql.Tx, o *rpc.Operation, key, state, message, uid, now string) error {
	_, e := tx.ExecContext(ctx, `UPDATE resource_progress SET state=?,message=?,confirmed_uid=CASE WHEN ?<>'' THEN ? ELSE confirmed_uid END,updated_at=? WHERE attempt_id=? AND resource_key=? AND state<>'SUCCEEDED'`, state, message, uid, uid, now, o.GetAttemptId(), key)
	if e == nil && state == "SUCCEEDED" && o.Action == "PUBLISH" && uid != "" {
		_, e = tx.ExecContext(ctx, `INSERT INTO instance_resource_identities VALUES(?,?,?) ON CONFLICT(instance_id,resource_key) DO UPDATE SET uid=excluded.uid`, o.GetInstanceId(), key, uid)
	}
	return e
}

// migrateResourceProgress 只投影历史已冻结单CR，禁止调用新增配套资源构建器。
func migrateResourceProgress(ctx context.Context, tx *sql.Tx) error {
	rows, e := tx.QueryContext(ctx, `SELECT id,instance_id,config,effective_spec FROM publication_versions`)
	if e != nil {
		return e
	}
	type legacyVersion struct {
		id, instance string
		config, spec []byte
	}
	versions := []legacyVersion{}
	for rows.Next() {
		var v legacyVersion
		if e = rows.Scan(&v.id, &v.instance, &v.config, &v.spec); e != nil {
			rows.Close()
			return e
		}
		versions = append(versions, v)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	for _, v := range versions {
		// 关键：v12迁移只读取当时已有的身份字段，不调用依赖后续schema的运行时读模型。
		i := new(rpc.ObservationInstance)
		e := tx.QueryRowContext(ctx, `SELECT id,cluster_id,component,name,target_namespace,uid FROM instances WHERE id=?`, v.instance).Scan(&i.Id, &i.ClusterId, &i.Component, &i.Name, &i.TargetNamespace, &i.Uid)
		if e != nil {
			return e
		}
		cfg, e := decodeStoredConfiguration(i.Component, v.config)
		if e != nil {
			return e
		}
		var spec map[string]any
		if e = json.Unmarshal(v.spec, &spec); e != nil {
			return e
		}
		if e = insertFrozenVersionResources(ctx, tx, v.id, []frozenResource{primaryFrozenResource(i, cfg, spec)}); e != nil {
			return e
		}
	}
	if _, e = tx.ExecContext(ctx, `INSERT INTO instance_resource_identities SELECT id,'component',uid FROM instances WHERE uid<>''`); e != nil {
		return e
	}
	if _, e = tx.ExecContext(ctx, `INSERT INTO operation_resources SELECT p.operation_id,r.resource_key,r.position,r.collection_path,r.object_path,r.document,b.uid FROM publication_operations p JOIN version_resources r ON r.version_id=p.version_id JOIN publication_previews b ON b.id=p.preview_id`); e != nil {
		return e
	}
	if _, e = tx.ExecContext(ctx, `INSERT INTO operation_resources SELECT d.operation_id,r.resource_key,r.position,r.collection_path,d.cr_path,r.document,d.original_uid FROM deletion_operations d JOIN version_resources r ON r.version_id=d.version_id`); e != nil {
		return e
	}
	_, e = tx.ExecContext(ctx, `INSERT INTO resource_progress SELECT a.id,r.resource_key,CASE WHEN a.state='SUCCEEDED' AND o.action='PUBLISH' THEN i.uid ELSE r.original_uid END,a.possibly_sent,a.state,a.message,a.updated_at FROM execution_attempts a JOIN operation_resources r ON r.operation_id=a.operation_id JOIN operations o ON o.id=a.operation_id JOIN publication_operations p ON p.operation_id=o.id JOIN instances i ON i.id=p.instance_id WHERE o.action='PUBLISH' UNION ALL SELECT a.id,r.resource_key,r.original_uid,a.possibly_sent,a.state,a.message,a.updated_at FROM execution_attempts a JOIN operation_resources r ON r.operation_id=a.operation_id JOIN operations o ON o.id=a.operation_id WHERE o.action='DELETE'`)
	return e
}

// resourceInstance 用冻结目标构造读取身份，不能从最新草稿重算 namespace/name。
func resourceInstance(i *rpc.ObservationInstance, r frozenResource, uid string) *rpc.ObservationInstance {
	copy := *i
	m := objectMap(r.document, "metadata")
	copy.Name = objectString(m, "name")
	copy.Uid = uid
	copy.TargetNamespace = objectString(objectMap(r.document, "spec"), "targetNamespace")
	if i.Component == AgentComponent {
		copy.TargetNamespace = objectString(m, "namespace")
		copy.CrNamespace = reference(copy.TargetNamespace)
	}
	return &copy
}

// readFrozenResource 只读取原冻结目标，404 表达当前不存在而非重新认领。
func readFrozenResource(ctx context.Context, t *Target, r frozenResource) (map[string]any, error) {
	var cr map[string]any
	e := t.Request(ctx, "GET", r.path, nil, &cr)
	if apiCode(e) == "TARGET_NOT_FOUND" {
		return nil, nil
	}
	return cr, e
}

// recoverResourceProgress 将每份未结束资源按自己的发送边界恢复；已经完成的结果保持原样。
func (s *Store) recoverResourceProgress() error {
	ctx := context.Background()
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return e
	}
	defer tx.Rollback()
	for _, query := range []string{
		`UPDATE resource_progress SET state=CASE WHEN possibly_sent=0 THEN 'WAITING_CONFIRMATION' WHEN (SELECT action FROM operations o JOIN execution_attempts a ON a.operation_id=o.id WHERE a.id=attempt_id)='DELETE' THEN 'DELETION_VERIFYING' WHEN confirmed_uid='' THEN 'IDENTITY_UNCERTAIN' ELSE 'RESULT_UNCERTAIN' END,message='平台重启；保留本资源身份与发送边界，不自动重放' WHERE attempt_id IN (SELECT current_attempt_id FROM operation_lifecycle) AND state IN ('QUEUED','CHECKING','DISPATCHED') AND attempt_id NOT IN (SELECT a.id FROM execution_attempts a JOIN operation_resources r ON r.operation_id=a.operation_id WHERE r.resource_key<>'component')`,
		`UPDATE operations SET state='WAITING_CONFIRMATION',message='平台已重启，等待明确继续；已完成资源保留' WHERE state IN ('QUEUED','RUNNING','CHECKING') AND id NOT IN (SELECT operation_id FROM operation_resources WHERE resource_key<>'component')`,
		`UPDATE operations SET state=CASE WHEN EXISTS(SELECT 1 FROM resource_progress p JOIN operation_lifecycle l ON l.current_attempt_id=p.attempt_id WHERE l.operation_id=operations.id AND p.state='IDENTITY_UNCERTAIN') THEN 'IDENTITY_UNCERTAIN' ELSE 'RESULT_UNCERTAIN' END,message='平台已重启，只恢复原资源读取核验，不重放' WHERE action='PUBLISH' AND state='DISPATCHED' AND id NOT IN (SELECT operation_id FROM operation_resources WHERE resource_key<>'component')`,
		`UPDATE operations SET state='DELETION_VERIFYING',message='平台已重启，只核验原UID清理结果' WHERE action='DELETE' AND state='DISPATCHED' AND id NOT IN (SELECT operation_id FROM operation_resources WHERE resource_key<>'component')`,
		`UPDATE execution_attempts SET state=(SELECT state FROM operations WHERE id=operation_id),revision=revision+1 WHERE id IN (SELECT current_attempt_id FROM operation_lifecycle) AND state IN ('QUEUED','CHECKING','DISPATCHED') AND operation_id NOT IN (SELECT operation_id FROM operation_resources WHERE resource_key<>'component')`,
	} {
		if _, e = tx.ExecContext(ctx, query); e != nil {
			return e
		}
	}
	return tx.Commit()
}
