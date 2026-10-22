// 逐资源模型分开保存不可变版本目标、操作目标及每次尝试的执行结果。
package control

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/url"
	"time"

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

func primaryFrozenResource(i *rpc.ObservationInstance, cfg configuration, spec map[string]any) frozenResource {
	doc := cfg.document()
	doc["spec"] = spec
	a := componentFor(i.Component)
	return frozenResource{key: "component", collection: a.collectionPath(instanceCRNamespace(i)), path: a.objectPath(i), document: doc, originalUID: i.Uid}
}
func (r frozenResource) public() *rpc.FrozenResource {
	m := objectMap(r.document, "metadata")
	return &rpc.FrozenResource{Key: r.key, ApiVersion: objectString(r.document, "apiVersion"), Kind: objectString(r.document, "kind"), Namespace: objectString(m, "namespace"), Name: objectString(m, "name"), Yaml: yamlText(safeTargetDocument(r.document))}
}

// freezePublicationResources 是资源集合的构建接缝；这里只加入当前组件，旧版本绝不追加资源。
func freezePublicationResources(ctx context.Context, tx *sql.Tx, version string, i *rpc.ObservationInstance, cfg configuration, spec map[string]any) error {
	return insertFrozenVersionResources(ctx, tx, version, []frozenResource{primaryFrozenResource(i, cfg, spec)})
}
func insertFrozenVersionResources(ctx context.Context, tx *sql.Tx, version string, resources []frozenResource) error {
	for position, r := range resources {
		if _, e := tx.ExecContext(ctx, `INSERT INTO version_resources VALUES(?,?,?,?,?,?)`, version, r.key, position, r.collection, r.path, []byte(canonical(r.document))); e != nil {
			return e
		}
	}
	return nil
}
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

// freezeOperationResources 在受理事务内复制目标并绑定原身份；后续草稿及实例字段不能改变目标。
func freezeOperationResources(ctx context.Context, tx *sql.Tx, o *rpc.Operation, i *rpc.ObservationInstance) error {
	resources, e := readFrozenResources(ctx, tx, o.GetVersionId(), false)
	if e != nil {
		return e
	}
	if len(resources) == 0 {
		return Error("UNAVAILABLE", "冻结资源集合缺失")
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
	return out, nil
}
func enrichResourceProgress(ctx context.Context, q clusterQuery, o *rpc.Operation) {
	if o.GetAttemptId() != "" {
		o.Resources, _ = resourceProgressFor(ctx, q, o.OperationId, o.GetAttemptId())
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
		i, e := instanceByID(ctx, tx, v.instance)
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
	if i.Component == AgentComponent {
		copy.TargetNamespace = objectString(m, "namespace")
	}
	return &copy
}
func readFrozenResource(ctx context.Context, t *Target, r frozenResource) (map[string]any, error) {
	var cr map[string]any
	e := t.Request(ctx, "GET", r.path, nil, &cr)
	if apiCode(e) == "TARGET_NOT_FOUND" {
		return nil, nil
	}
	return cr, e
}

// resourcePath 供同类新增目标沿用安全路径编码；路径在冻结时固定。
func resourcePath(collection, name string) string { return collection + "/" + url.PathEscape(name) }
func resourceNow(s *Store) string                 { return s.Now().UTC().Format(time.RFC3339Nano) }

// recoverResourceProgress 将每份未结束资源按自己的发送边界恢复；已经完成的结果保持原样。
func (s *Store) recoverResourceProgress() error {
	ctx := context.Background()
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return e
	}
	defer tx.Rollback()
	for _, query := range []string{
		`UPDATE resource_progress SET state=CASE WHEN possibly_sent=0 THEN 'WAITING_CONFIRMATION' WHEN (SELECT action FROM operations o JOIN execution_attempts a ON a.operation_id=o.id WHERE a.id=attempt_id)='DELETE' THEN 'DELETION_VERIFYING' WHEN confirmed_uid='' THEN 'IDENTITY_UNCERTAIN' ELSE 'RESULT_UNCERTAIN' END,message='平台重启；保留本资源身份与发送边界，不自动重放' WHERE attempt_id IN (SELECT current_attempt_id FROM operation_lifecycle) AND state IN ('QUEUED','CHECKING','DISPATCHED')`,
		`UPDATE operations SET state='WAITING_CONFIRMATION',message='平台已重启，等待明确继续；已完成资源保留' WHERE state IN ('QUEUED','RUNNING','CHECKING')`,
		`UPDATE operations SET state=CASE WHEN EXISTS(SELECT 1 FROM resource_progress p JOIN operation_lifecycle l ON l.current_attempt_id=p.attempt_id WHERE l.operation_id=operations.id AND p.state='IDENTITY_UNCERTAIN') THEN 'IDENTITY_UNCERTAIN' ELSE 'RESULT_UNCERTAIN' END,message='平台已重启，只恢复原资源读取核验，不重放' WHERE action='PUBLISH' AND state='DISPATCHED'`,
		`UPDATE operations SET state='DELETION_VERIFYING',message='平台已重启，只核验原UID清理结果' WHERE action='DELETE' AND state='DISPATCHED'`,
		`UPDATE execution_attempts SET state=(SELECT state FROM operations WHERE id=operation_id),revision=revision+1 WHERE id IN (SELECT current_attempt_id FROM operation_lifecycle) AND state IN ('QUEUED','CHECKING','DISPATCHED')`,
	} {
		if _, e = tx.ExecContext(ctx, query); e != nil {
			return e
		}
	}
	return tx.Commit()
}

// selectExecutionResource 为单步派发选择明确目标；公共操作仍读取完整集合。
func selectExecutionResource(o *rpc.Operation, key string) {
	for _, p := range o.Resources {
		if p.Target.Key == key {
			o.Resources = []*rpc.ResourceProgress{p}
			return
		}
	}
}
func executionResourceKey(o *rpc.Operation) string {
	if len(o.Resources) == 1 {
		return o.Resources[0].Target.Key
	}
	return "component"
}
func unresolvedResourceSent(ctx context.Context, q clusterQuery, o *rpc.Operation) bool {
	var count int
	if q.QueryRowContext(ctx, `SELECT count(*) FROM resource_progress WHERE attempt_id=? AND possibly_sent=1 AND state NOT IN ('SUCCEEDED','FAILED','CANCELLED')`, o.GetAttemptId()).Scan(&count) != nil {
		return true
	}
	return count > 0
}
