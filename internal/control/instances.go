package control

import (
	"context"
	"database/sql"
	"encoding/json"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"time"
)

const instanceColumns = `id,cluster_id,component,name,target_namespace,uid,lifecycle,current_version_id,activity_at`

func scanInstance(row interface{ Scan(...any) error }) (*rpc.ObservationInstance, error) {
	i := new(rpc.ObservationInstance)
	e := row.Scan(&i.Id, &i.ClusterId, &i.Component, &i.Name, &i.TargetNamespace, &i.Uid, &i.Lifecycle, &i.CurrentVersionId, &i.ActivityAt)
	i.Type = "exporter"
	i.Ownership = "managed"
	return i, e
}
func instanceByID(ctx context.Context, q clusterQuery, id string) (*rpc.ObservationInstance, error) {
	i, e := scanInstance(q.QueryRowContext(ctx, `SELECT `+instanceColumns+` FROM instances WHERE id=?`, id))
	if e == sql.ErrNoRows {
		var raw []byte
		if q.QueryRowContext(ctx, `SELECT document FROM discovered_instances WHERE id=?`, id).Scan(&raw) == nil {
			if json.Unmarshal(raw, i) == nil && i.ReadOnly {
				return i, nil
			}
		}
		return nil, Error("NOT_FOUND", "实例不存在")
	}
	if e != nil {
		return nil, Error("UNAVAILABLE", "实例不可读")
	}
	return i, nil
}
func (s *Store) authorizedInstance(ctx context.Context, c *rpc.RequestContext, id string) (*rpc.ObservationInstance, error) {
	p, e := s.Authorize(ctx, token(c))
	if e != nil {
		return nil, e
	}
	i, e := instanceByID(ctx, s.DB, id)
	if e != nil {
		return nil, e
	}
	cl, e := clusterByID(ctx, s.DB, i.ClusterId)
	if e != nil {
		return nil, e
	}
	if e = clusterPermission(p, cl); e != nil {
		return nil, e
	}
	return i, nil
}
func (s *Store) instanceTx(ctx context.Context, tx *sql.Tx, c *rpc.RequestContext, id string) (*Principal, *rpc.ObservationInstance, error) {
	p, e := s.AuthorizeTx(ctx, tx, token(c))
	if e != nil {
		return nil, nil, e
	}
	i, e := instanceByID(ctx, tx, id)
	if e != nil {
		return nil, nil, e
	}
	if i.ReadOnly {
		return nil, nil, Error("FORBIDDEN", "未纳管实例只读，不能编辑、下发或删除")
	}
	cl, e := clusterByID(ctx, tx, i.ClusterId)
	if e != nil {
		return nil, nil, e
	}
	if e = clusterPermission(p, cl); e != nil {
		return nil, nil, e
	}
	if cl.State != "ACTIVE" {
		return nil, nil, Error("CONFLICT", "集群已注销，历史只读")
	}
	return p, i, nil
}
func (s *Store) CreateInstance(ctx context.Context, c *rpc.RequestContext, r *rpc.CreateInstanceRequest) (*rpc.ObservationInstance, error) {
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return nil, Error("UNAVAILABLE", "暂不可用")
	}
	defer tx.Rollback()
	p, e := s.AuthorizeTx(ctx, tx, token(c))
	if e != nil {
		return nil, e
	}
	cl, e := clusterByID(ctx, tx, r.ClusterId)
	if e != nil {
		return nil, e
	}
	if e = clusterPermission(p, cl); e != nil {
		return nil, e
	}
	if cl.State != "ACTIVE" {
		return nil, Error("CONFLICT", "集群已注销")
	}
	var replay rpc.ObservationInstance
	cmd, hit, e := s.beginCommand(ctx, tx, p, c, "CREATE_INSTANCE", r.ClusterId, r, &replay)
	if e != nil {
		return nil, e
	}
	if hit {
		return &replay, nil
	}
	if r.Component != "kube-state-metrics" {
		return nil, fieldError("component", "不支持的组件")
	}
	cfg, e := decodeKSM(r.Ksm)
	if e != nil {
		return nil, e
	}
	var n int
	if tx.QueryRowContext(ctx, `SELECT count(*) FROM instances WHERE cluster_id=? AND component=? AND name=?`, cl.Id, r.Component, cfg.Name).Scan(&n) != nil {
		return nil, Error("UNAVAILABLE", "身份不可读")
	}
	if n > 0 {
		return nil, Error("CONFLICT", "同集群已存在该 CR 身份")
	}
	id, now := ID(), s.Now().UTC().Format(time.RFC3339Nano)
	raw, _ := json.Marshal(cfg)
	if _, e = tx.ExecContext(ctx, `INSERT INTO instances(id,cluster_id,component,name,target_namespace,activity_at) VALUES(?,?,?,?,?,?)`, id, cl.Id, r.Component, cfg.Name, cfg.TargetNamespace, now); e != nil {
		return nil, Error("CONFLICT", "实例身份冲突")
	}
	if _, e = tx.ExecContext(ctx, `INSERT INTO drafts VALUES(?,1,?)`, id, raw); e != nil {
		return nil, Error("UNAVAILABLE", "草稿保存失败")
	}
	i, e := instanceByID(ctx, tx, id)
	if e != nil {
		return nil, e
	}
	if e = s.finishCommand(ctx, tx, cmd, i); e != nil {
		return nil, e
	}
	if tx.Commit() != nil {
		return nil, Error("UNAVAILABLE", "创建失败")
	}
	return i, nil
}
func (s *Store) GetInstance(ctx context.Context, c *rpc.RequestContext, r *rpc.InstanceID) (*rpc.ObservationInstance, error) {
	i, e := s.authorizedInstance(ctx, c, r.Id)
	if e != nil {
		return nil, e
	}
	if !i.ReadOnly {
		i.Runtime, e = s.GetRuntime(ctx, c, r)
	}
	return i, e
}
func (s *Store) ListInstances(ctx context.Context, c *rpc.RequestContext, r *rpc.ClusterID) (*rpc.InstancesResult_, error) {
	if _, e := s.GetCluster(ctx, c, r); e != nil {
		return nil, e
	}
	rows, e := s.DB.QueryContext(ctx, `SELECT `+instanceColumns+` FROM instances WHERE cluster_id=? AND lifecycle!='DELETED' ORDER BY activity_at DESC,id`, r.Id)
	if e != nil {
		return nil, Error("UNAVAILABLE", "列表读取失败")
	}
	out := []*rpc.ObservationInstance{}
	for rows.Next() {
		i, e := scanInstance(rows)
		if e != nil {
			rows.Close()
			return nil, Error("UNAVAILABLE", "列表读取失败")
		}
		out = append(out, i)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return nil, e
	}
	for _, i := range out {
		runtime, e := s.GetRuntime(ctx, c, &rpc.InstanceID{Id: i.Id})
		if e != nil {
			return nil, e
		}
		i.Runtime = runtime
	}
	cl, e := s.GetCluster(ctx, c, r)
	if e != nil {
		return nil, e
	}
	if cl.State == "ACTIVE" {
		external, e := s.discoverKSM(ctx, c, r.Id, out)
		if e != nil {
			return nil, e
		}
		out = append(out, external...)
	}
	return &rpc.InstancesResult_{Instances: out}, nil
}

func readDraft(ctx context.Context, q clusterQuery, id string) (*KSMConfig, int64, error) {
	var raw []byte
	var rev int64
	if q.QueryRowContext(ctx, `SELECT config,revision FROM drafts WHERE instance_id=?`, id).Scan(&raw, &rev) != nil {
		return nil, 0, Error("NOT_FOUND", "草稿不存在，外部实例只读")
	}
	var cfg KSMConfig
	if json.Unmarshal(raw, &cfg) != nil {
		return nil, 0, Error("UNAVAILABLE", "草稿无法读取")
	}
	return &cfg, rev, nil
}
func draftDTO(cfg *KSMConfig, rev int64) *rpc.ConfigurationDraft {
	return &rpc.ConfigurationDraft{Revision: rev, Ksm: cfg.rpc(), Yaml: yamlText(ksmCR(cfg.Name, cfg.spec()))}
}
func (s *Store) GetDraft(ctx context.Context, c *rpc.RequestContext, r *rpc.InstanceID) (*rpc.ConfigurationDraft, error) {
	if i, e := s.authorizedInstance(ctx, c, r.Id); e != nil {
		return nil, e
	} else if i.ReadOnly {
		return nil, Error("FORBIDDEN", "外部实例无平台草稿，请查看只读 YAML")
	}
	cfg, rev, e := readDraft(ctx, s.DB, r.Id)
	if e != nil {
		return nil, e
	}
	return draftDTO(cfg, rev), nil
}
func (s *Store) SaveDraft(ctx context.Context, c *rpc.RequestContext, r *rpc.SaveDraftRequest) (*rpc.ConfigurationDraft, error) {
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return nil, Error("UNAVAILABLE", "暂不可用")
	}
	defer tx.Rollback()
	p, i, e := s.instanceTx(ctx, tx, c, r.Id)
	if e != nil {
		return nil, e
	}
	var replay rpc.ConfigurationDraft
	cmd, hit, e := s.beginCommand(ctx, tx, p, c, "SAVE_DRAFT", r.Id, r, &replay)
	if e != nil {
		return nil, e
	}
	if hit {
		return &replay, nil
	}
	cfg, e := decodeKSM(r.Ksm)
	if e != nil {
		return nil, e
	}
	_, rev, e := readDraft(ctx, tx, r.Id)
	if e != nil {
		return nil, e
	}
	if rev != r.ExpectedDraftRevision {
		return nil, Error("CONFLICT", "草稿修订已变化，请保留本地编辑并重新读取")
	}
	if rev >= 9007199254740991 {
		return nil, Error("CONFLICT", "草稿修订已达上限")
	}
	var protected int
	if tx.QueryRowContext(ctx, `SELECT count(*) FROM instance_protections WHERE instance_id=?`, r.Id).Scan(&protected) != nil {
		return nil, Error("UNAVAILABLE", "保护无法读取")
	}
	if (i.Uid != "" || protected > 0) && (cfg.Name != i.Name || cfg.TargetNamespace != i.TargetNamespace) {
		return nil, Error("CONFLICT", "下发中或创建后 name/targetNamespace 固定，请新建实例")
	}
	raw, _ := json.Marshal(cfg)
	if _, e = tx.ExecContext(ctx, `UPDATE drafts SET config=?,revision=revision+1 WHERE instance_id=? AND revision=?`, raw, r.Id, rev); e != nil {
		return nil, Error("UNAVAILABLE", "保存失败")
	}
	if i.Uid == "" && protected == 0 {
		if _, e = tx.ExecContext(ctx, `UPDATE instances SET name=?,target_namespace=? WHERE id=?`, cfg.Name, cfg.TargetNamespace, r.Id); e != nil {
			return nil, Error("CONFLICT", "CR 身份重复")
		}
	}
	if _, e = tx.ExecContext(ctx, `UPDATE instances SET activity_at=? WHERE id=?`, s.Now().UTC().Format(time.RFC3339Nano), r.Id); e != nil {
		return nil, e
	}
	out := draftDTO(cfg, rev+1)
	if e = s.finishCommand(ctx, tx, cmd, out); e != nil {
		return nil, e
	}
	if tx.Commit() != nil {
		return nil, Error("UNAVAILABLE", "保存提交失败")
	}
	return out, nil
}
func (s *Store) CopyTemplate(ctx context.Context, c *rpc.RequestContext, r *rpc.InstanceID) (*rpc.ConfigurationDraft, error) {
	d, e := s.GetDraft(ctx, c, r)
	if e != nil {
		return nil, e
	}
	d.Ksm.Name = ""
	d.Revision = 0
	d.Yaml = ""
	return d, nil
}
func (s *Store) RecordEditingActivity(ctx context.Context, c *rpc.RequestContext, r *rpc.InstanceID) (*rpc.Empty, error) {
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return nil, Error("UNAVAILABLE", "暂不可用")
	}
	defer tx.Rollback()
	if _, _, e = s.instanceTx(ctx, tx, c, r.Id); e != nil {
		return nil, e
	}
	_, e = tx.ExecContext(ctx, `UPDATE instances SET activity_at=? WHERE id=?`, s.Now().UTC().Format(time.RFC3339Nano), r.Id)
	if e != nil {
		return nil, e
	}
	return &rpc.Empty{}, tx.Commit()
}
func (s *Store) DeleteDraft(ctx context.Context, c *rpc.RequestContext, r *rpc.InstanceID) (*rpc.Empty, error) {
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return nil, Error("UNAVAILABLE", "暂不可用")
	}
	defer tx.Rollback()
	p, i, e := s.instanceTx(ctx, tx, c, r.Id)
	if e != nil {
		return nil, e
	}
	if i.Uid != "" {
		return nil, Error("CONFLICT", "已部署实例删除须使用生命周期操作")
	}
	var n int
	tx.QueryRowContext(ctx, `SELECT count(*) FROM instance_protections WHERE instance_id=?`, r.Id).Scan(&n)
	if n > 0 {
		return nil, Error("CONFLICT", "实例仍有未结束操作")
	}
	var replay rpc.Empty
	cmd, hit, e := s.beginCommand(ctx, tx, p, c, "DELETE_DRAFT", r.Id, r, &replay)
	if e != nil {
		return nil, e
	}
	if hit {
		return &replay, nil
	}
	if _, e = tx.ExecContext(ctx, `UPDATE instances SET lifecycle='DELETED',activity_at=? WHERE id=?`, s.Now().UTC().Format(time.RFC3339Nano), r.Id); e != nil {
		return nil, e
	}
	if e = s.finishCommand(ctx, tx, cmd, &rpc.Empty{}); e != nil {
		return nil, e
	}
	return &rpc.Empty{}, tx.Commit()
}
