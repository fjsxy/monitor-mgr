package control

import (
	"context"
	"encoding/json"
	"errors"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"net/url"
	"time"
)

func apiCode(e error) string {
	var a *rpc.APIError
	if errors.As(e, &a) {
		return a.Code
	}
	return "UNAVAILABLE"
}
func objectMap(o map[string]any, key string) map[string]any {
	m, _ := o[key].(map[string]any)
	return m
}
func objectString(o map[string]any, key string) string { v, _ := o[key].(string); return v }
func (t *Target) readKSM(ctx context.Context, name string) (map[string]any, error) {
	var out map[string]any
	e := t.Request(ctx, "GET", ksmPath+"/"+url.PathEscape(name), nil, &out)
	if apiCode(e) == "TARGET_NOT_FOUND" {
		return nil, nil
	}
	return out, e
}
func checkTarget(i *rpc.ObservationInstance, cr map[string]any) error {
	if i.Uid == "" {
		if cr != nil {
			return Error("CONFLICT", "同名 CR 已存在，不能自动接管")
		}
		return nil
	}
	if cr == nil {
		return Error("CONFLICT", "原 CR 已缺失，不自动重建")
	}
	if objectString(objectMap(cr, "metadata"), "uid") != i.Uid {
		return Error("CONFLICT", "CR UID 已变化，资源身份无法确认")
	}
	return nil
}
func (t *Target) publicationDependencies(ctx context.Context, cl *rpc.Cluster, cfg *KSMConfig, create bool) error {
	var ns map[string]any
	if e := t.Request(ctx, "GET", "/api/v1/namespaces/kube-system", nil, &ns); e != nil {
		return e
	}
	if objectString(objectMap(ns, "metadata"), "uid") != cl.Uid {
		return Error("CONFLICT", "集群 UID 已变化")
	}
	var version struct {
		GitVersion string `json:"gitVersion"`
	}
	if e := t.Request(ctx, "GET", "/version", nil, &version); e != nil {
		return e
	}
	if len(version.GitVersion) < 6 || version.GitVersion[:6] != "v1.28." {
		return Error("CONFLICT", "KSM 下发限定 Kubernetes 1.28.x")
	}
	var list any
	if e := t.Request(ctx, "GET", ksmPath+"?limit=1", nil, &list); e != nil {
		return Error("CONFLICT", "KubeStateMetrics CRD 或读取权限不可用")
	}
	if e := t.Request(ctx, "GET", "/api/v1/namespaces/"+url.PathEscape(cfg.TargetNamespace), nil, &ns); e != nil {
		return e
	}
	verb := "update"
	if create {
		verb = "create"
	}
	workload := "deployments"
	if cfg.DeploymentMode == "StatefulSetSharded" {
		workload = "statefulsets"
	}
	if cfg.DeploymentMode == "DaemonSetSharded" {
		workload = "daemonsets"
	}
	for _, a := range []map[string]string{{"group": "monitoring.fjsxy.github.io", "resource": "kubestatemetrics", "verb": verb}, {"group": "apps", "resource": workload, "verb": verb, "namespace": cfg.TargetNamespace}} {
		var review struct {
			Status struct {
				Allowed bool `json:"allowed"`
			} `json:"status"`
		}
		body := map[string]any{"apiVersion": "authorization.k8s.io/v1", "kind": "SelfSubjectAccessReview", "spec": map[string]any{"resourceAttributes": a}}
		if e := t.Request(ctx, "POST", "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews", body, &review); e != nil {
			return e
		}
		if !review.Status.Allowed {
			return Error("FORBIDDEN", "目标 namespace 或 KSM 操作权限不足")
		}
	}
	return nil
}
func (s *Store) PreviewPublication(ctx context.Context, c *rpc.RequestContext, r *rpc.PreviewRequest) (*rpc.PublicationPreview, error) {
	i, e := s.authorizedInstance(ctx, c, r.Id)
	if e != nil {
		return nil, e
	}
	if i.ReadOnly {
		return nil, Error("FORBIDDEN", "未纳管实例只读")
	}
	cfg, rev, e := readDraft(ctx, s.DB, r.Id)
	if e != nil {
		return nil, e
	}
	if rev != r.DraftRevision {
		return nil, Error("CONFLICT", "草稿修订已变化")
	}
	t, e := s.TargetForCluster(ctx, c, i.ClusterId)
	if e != nil {
		return nil, e
	}
	defer t.Close()
	cl, e := s.GetCluster(ctx, c, &rpc.ClusterID{Id: i.ClusterId})
	if e != nil {
		return nil, e
	}
	if e = t.publicationDependencies(ctx, cl, cfg, i.Uid == ""); e != nil {
		return nil, e
	}
	cr, e := t.readKSM(ctx, i.Name)
	if e != nil {
		return nil, e
	}
	if e = checkTarget(i, cr); e != nil {
		return nil, e
	}
	current := "null"
	uid := ""
	left := "# 首次创建：当前不存在 CR\n"
	if cr != nil {
		current = normalizedSpec(objectMap(cr, "spec"))
		uid = i.Uid
		left = yamlText(cr)
	}
	drift := false
	if i.CurrentVersionId != "" {
		var baseline []byte
		if s.DB.QueryRowContext(ctx, `SELECT effective_spec FROM publication_versions WHERE id=? AND instance_id=?`, i.CurrentVersionId, i.Id).Scan(&baseline) != nil {
			return nil, Error("UNAVAILABLE", "写入版本基线不可读")
		}
		var spec map[string]any
		json.Unmarshal(baseline, &spec)
		drift = normalizedSpec(spec) != current
	}
	preview := &rpc.PublicationPreview{PreviewId: ID(), DraftRevision: rev, LeftYAML: left, RightYAML: yamlText(ksmCR(cfg.Name, cfg.spec())), Drift: drift, Warnings: []string{}}
	if cfg.DeploymentMode != "Deployment" {
		preview.Warnings = append(preview.Warnings, "experimental 模式；切换会重建 workload 并出现采集空窗")
	}
	if cr != nil {
		oldMode := objectString(objectMap(objectMap(cr, "spec"), "deployment"), "mode")
		if oldMode != cfg.DeploymentMode {
			preview.Warnings = append(preview.Warnings, "模式切换由 Operator 清理旧 workload 后建立新 workload，不承诺无损")
		}
	}
	if cfg.NamespaceFilter.Mode == "Include" && len(cfg.NamespaceFilter.Names) == 0 {
		preview.Warnings = append(preview.Warnings, "正则可能零匹配，零匹配会停止全部 workload，不会自动改为 All")
	}
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return nil, e
	}
	defer tx.Rollback()
	if _, _, e = s.instanceTx(ctx, tx, c, r.Id); e != nil {
		return nil, e
	}
	_, latest, e := readDraft(ctx, tx, r.Id)
	if e != nil {
		return nil, e
	}
	if latest != rev {
		return nil, Error("CONFLICT", "预览期间草稿已变化")
	}
	if _, e = tx.ExecContext(ctx, `INSERT INTO publication_previews VALUES(?,?,?,?,?,?,?)`, preview.PreviewId, i.Id, rev, uid, []byte(current), []byte(normalizedSpec(cfg.spec())), drift); e != nil {
		return nil, Error("UNAVAILABLE", "预览无法保存")
	}
	if tx.Commit() != nil {
		return nil, Error("UNAVAILABLE", "预览提交失败")
	}
	return preview, nil
}

type publicationBaseline struct {
	uid, target, desired string
	revision             int64
	drift                bool
}

func readPreview(ctx context.Context, q clusterQuery, id, instance string) (*publicationBaseline, error) {
	b := new(publicationBaseline)
	var target, desired []byte
	if q.QueryRowContext(ctx, `SELECT uid,target_spec,desired_spec,draft_revision,drift FROM publication_previews WHERE id=? AND instance_id=?`, id, instance).Scan(&b.uid, &target, &desired, &b.revision, &b.drift) != nil {
		return nil, Error("CONFLICT", "预览不存在，请重新预览")
	}
	b.target = string(target)
	b.desired = string(desired)
	return b, nil
}
func validatePublication(i *rpc.ObservationInstance, cfg *KSMConfig, rev int64, b *publicationBaseline, r *rpc.PublishRequest, cr map[string]any) error {
	if rev != r.DraftRevision || b.revision != rev || b.desired != normalizedSpec(cfg.spec()) {
		return Error("CONFLICT", "预览或草稿修订已过期，请重新预览")
	}
	if e := checkTarget(i, cr); e != nil {
		return e
	}
	target := "null"
	if cr != nil {
		target = normalizedSpec(objectMap(cr, "spec"))
	}
	if b.target != target || b.uid != i.Uid {
		return Error("CONFLICT", "目标配置基线已变化，请重新预览")
	}
	if b.drift && !r.OverwriteDrift {
		return Error("CONFLICT", "存在配置漂移，请检查完整 diff 并明确覆盖")
	}
	return nil
}
func (s *Store) enrichPublicationOperation(ctx context.Context, q clusterQuery, o *rpc.Operation) {
	var instance, version, attempt string
	if q.QueryRowContext(ctx, `SELECT p.instance_id,p.version_id,(SELECT id FROM execution_attempts WHERE operation_id=p.operation_id ORDER BY created_at DESC,id DESC LIMIT 1) FROM publication_operations p WHERE p.operation_id=?`, o.OperationId).Scan(&instance, &version, &attempt) == nil {
		o.InstanceId = &instance
		o.VersionId = &version
		o.AttemptId = &attempt
	}
}
func (s *Store) PublishConfiguration(ctx context.Context, c *rpc.RequestContext, r *rpc.PublishRequest) (*rpc.Operation, error) {
	// 当前授权先于幂等命中；命中不依赖目标 API 或已变化的草稿。
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return nil, e
	}
	p, i, e := s.instanceTx(ctx, tx, c, r.Id)
	if e != nil {
		tx.Rollback()
		return nil, e
	}
	var replay rpc.Operation
	_, hit, e := s.beginCommand(ctx, tx, p, c, "PUBLISH", i.Id, r, &replay)
	if e != nil {
		tx.Rollback()
		return nil, e
	}
	if hit {
		o, e := scanOperation(tx.QueryRowContext(ctx, `SELECT `+operationColumns+` FROM operations WHERE id=?`, replay.OperationId))
		if e == nil {
			s.enrichPublicationOperation(ctx, tx, o)
		}
		tx.Rollback()
		return o, e
	}
	tx.Rollback()
	t, e := s.TargetForCluster(ctx, c, i.ClusterId)
	if e != nil {
		return nil, e
	}
	defer t.Close()
	cr, e := t.readKSM(ctx, i.Name)
	if e != nil {
		return nil, e
	}
	tx, e = s.DB.BeginTx(ctx, nil)
	if e != nil {
		return nil, e
	}
	defer tx.Rollback()
	p, i, e = s.instanceTx(ctx, tx, c, r.Id)
	if e != nil {
		return nil, e
	}
	cmd, hit, e := s.beginCommand(ctx, tx, p, c, "PUBLISH", i.Id, r, &replay)
	if e != nil {
		return nil, e
	}
	if hit {
		o, e := scanOperation(tx.QueryRowContext(ctx, `SELECT `+operationColumns+` FROM operations WHERE id=?`, replay.OperationId))
		if e == nil {
			s.enrichPublicationOperation(ctx, tx, o)
		}
		return o, e
	}
	if i.Lifecycle == "DELETED" {
		return nil, Error("CONFLICT", "实例已删除")
	}
	var n int
	if tx.QueryRowContext(ctx, `SELECT count(*) FROM instance_protections WHERE instance_id=?`, i.Id).Scan(&n) != nil {
		return nil, e
	}
	if n > 0 {
		return nil, Error("CONFLICT", "实例仍有未结束变更")
	}
	cfg, rev, e := readDraft(ctx, tx, i.Id)
	if e != nil {
		return nil, e
	}
	baseline, e := readPreview(ctx, tx, r.PreviewId, i.Id)
	if e != nil {
		return nil, e
	}
	if e = validatePublication(i, cfg, rev, baseline, r, cr); e != nil {
		return nil, e
	}
	now := s.Now().UTC().Format(time.RFC3339Nano)
	version, attempt := ID(), ID()
	o := &rpc.Operation{OperationId: ID(), Action: "PUBLISH", State: "QUEUED", ClusterId: i.ClusterId, ActorId: p.Account.Id, OwnerId: p.Account.Id, CreatedAt: now, UpdatedAt: now, Message: "已受理，尚未确认 CR 写入", InstanceId: &i.Id, VersionId: &version, AttemptId: &attempt}
	config, _ := json.Marshal(cfg)
	spec, _ := json.Marshal(cfg.spec())
	for _, q := range []struct {
		sql  string
		args []any
	}{
		{`INSERT INTO publication_versions VALUES(?,?,?,?,?,?)`, []any{version, i.Id, rev, config, spec, now}},
		{`INSERT INTO operations(id,action,state,cluster_id,actor_id,owner_id,name,message,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)`, []any{o.OperationId, o.Action, o.State, o.ClusterId, o.ActorId, o.OwnerId, i.Name, o.Message, now, now}},
		{`INSERT INTO publication_operations VALUES(?,?,?,?,?)`, []any{o.OperationId, i.Id, version, r.PreviewId, r.OverwriteDrift}},
		{`INSERT INTO execution_attempts VALUES(?,?,?,0,?,?)`, []any{attempt, o.OperationId, "QUEUED", now, now}},
		{`INSERT INTO instance_protections VALUES(?,?)`, []any{i.Id, o.OperationId}},
		{`UPDATE instances SET activity_at=? WHERE id=?`, []any{now, i.Id}},
	} {
		if _, e = tx.ExecContext(ctx, q.sql, q.args...); e != nil {
			return nil, Error("UNAVAILABLE", "受理事务失败，未受理")
		}
	}
	if e = s.finishCommand(ctx, tx, cmd, o); e != nil {
		return nil, e
	}
	if e = audit(ctx, tx, p.Account.Id, "PUBLISH_ACCEPTED", i.Id, s.Now()); e != nil {
		return nil, e
	}
	if tx.Commit() != nil {
		return nil, Error("UNAVAILABLE", "受理提交失败")
	}
	s.wakeWorker()
	return o, nil
}
func versionByID(ctx context.Context, q clusterQuery, id, instance string) (*rpc.PublicationVersion, error) {
	v := new(rpc.PublicationVersion)
	var raw, spec []byte
	if q.QueryRowContext(ctx, `SELECT id,instance_id,source_revision,config,effective_spec,created_at FROM publication_versions WHERE id=? AND instance_id=?`, id, instance).Scan(&v.VersionId, &v.InstanceId, &v.SourceDraftRevision, &raw, &spec, &v.CreatedAt) != nil {
		return nil, Error("NOT_FOUND", "发布版本不存在")
	}
	var cfg KSMConfig
	if json.Unmarshal(raw, &cfg) != nil {
		return nil, Error("UNAVAILABLE", "来源配置不可读")
	}
	v.Ksm = cfg.rpc()
	v.Yaml = yamlText(ksmCR(cfg.Name, cfg.spec()))
	return v, nil
}
func (s *Store) ListVersions(ctx context.Context, c *rpc.RequestContext, r *rpc.InstanceID) (*rpc.VersionsResult_, error) {
	if _, e := s.authorizedInstance(ctx, c, r.Id); e != nil {
		return nil, e
	}
	rows, e := s.DB.QueryContext(ctx, `SELECT id FROM publication_versions WHERE instance_id=? ORDER BY created_at,id`, r.Id)
	if e != nil {
		return nil, e
	}
	ids := []string{}
	for rows.Next() {
		var id string
		if e = rows.Scan(&id); e != nil {
			rows.Close()
			return nil, e
		}
		ids = append(ids, id)
	}
	rows.Close()
	out := []*rpc.PublicationVersion{}
	for _, id := range ids {
		v, e := versionByID(ctx, s.DB, id, r.Id)
		if e != nil {
			return nil, e
		}
		out = append(out, v)
	}
	return &rpc.VersionsResult_{Versions: out}, nil
}
func (s *Store) GetVersion(ctx context.Context, c *rpc.RequestContext, r *rpc.VersionRequest) (*rpc.PublicationVersion, error) {
	if _, e := s.authorizedInstance(ctx, c, r.Id); e != nil {
		return nil, e
	}
	return versionByID(ctx, s.DB, r.VersionId, r.Id)
}
func (s *Store) LookupOperations(ctx context.Context, c *rpc.RequestContext, r *rpc.OperationLookup) (*rpc.OperationsResult_, error) {
	p, e := s.Authorize(ctx, token(c))
	if e != nil {
		return nil, e
	}
	if _, e = s.authorizedInstance(ctx, c, r.InstanceId); e != nil {
		return nil, e
	}
	rows, e := s.DB.QueryContext(ctx, `SELECT result_json FROM command_results WHERE actor_id=? AND action='PUBLISH' AND target_id=? AND command_key=?`, p.Account.Id, r.InstanceId, r.IdempotencyKey)
	if e != nil {
		return nil, e
	}
	ids := []string{}
	for rows.Next() {
		var raw []byte
		rows.Scan(&raw)
		var o rpc.Operation
		if json.Unmarshal(raw, &o) == nil {
			ids = append(ids, o.OperationId)
		}
	}
	rows.Close()
	out := []*rpc.Operation{}
	for _, id := range ids {
		o, e := s.GetOperation(ctx, c, &rpc.OperationID{OperationId: id})
		if e != nil {
			return nil, e
		}
		out = append(out, o)
	}
	return &rpc.OperationsResult_{Operations: out}, nil
}
func (s *Store) GetAttempts(ctx context.Context, c *rpc.RequestContext, r *rpc.OperationID) (*rpc.AttemptsResult_, error) {
	if _, e := s.GetOperation(ctx, c, r); e != nil {
		return nil, e
	}
	rows, e := s.DB.QueryContext(ctx, `SELECT id,operation_id,state,possibly_sent,created_at,updated_at FROM execution_attempts WHERE operation_id=? ORDER BY created_at,id`, r.OperationId)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []*rpc.ExecutionAttempt{}
	for rows.Next() {
		a := new(rpc.ExecutionAttempt)
		if e = rows.Scan(&a.AttemptId, &a.OperationId, &a.State, &a.PossiblySent, &a.CreatedAt, &a.UpdatedAt); e != nil {
			return nil, e
		}
		out = append(out, a)
	}
	return &rpc.AttemptsResult_{Attempts: out}, rows.Err()
}
