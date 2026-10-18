// 独立来源读模型保存最后成功快照和每次尝试；失败、epoch或过期只改变当前可信度。
package control

import (
	"context"
	"database/sql"
	"encoding/json"
	"time"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

func safeSourceError(e error) string {
	if a, ok := e.(*rpc.APIError); ok {
		return a.Code + "：" + a.Message
	}
	return "来源读取失败"
}

// saveSourceObservation 的失败分支保留相同原UID的成功文档，绝不由CR成功替其他来源续期。
func (s *Store) saveSourceObservation(ctx context.Context, i *rpc.ObservationInstance, source string, current *rpc.SourceObservation, cause error) *rpc.SourceObservation {
	// 本地落盘不依赖已结束的HTTP等待；仍有独立短预算且不再访问目标API。
	durable, cancel := context.WithTimeout(context.WithoutCancel(ctx), 2*time.Second)
	defer cancel()
	tx, e := s.DB.BeginTx(durable, nil)
	if e != nil {
		return &rpc.SourceObservation{Source: source, Error: "来源保存失败", Historical: true}
	}
	defer tx.Rollback()
	if !observationScopeCurrent(durable, tx, ctx) {
		return &rpc.SourceObservation{Source: source, Error: "观察目标修订已变化", Historical: true}
	}
	now := s.Now().UTC().Format(time.RFC3339Nano)
	out := current
	if out == nil {
		out = &rpc.SourceObservation{Source: source}
	}
	if cause != nil {
		out = &rpc.SourceObservation{Source: source}
		var raw []byte
		var uid string
		if tx.QueryRowContext(durable, `SELECT document,uid FROM instance_source_observations WHERE instance_id=? AND source=?`, i.Id, source).Scan(&raw, &uid) == nil && uid == i.Uid {
			json.Unmarshal(raw, out)
		}
		// 兼容既有安全cache：首次升级读取失败仍可展示原成功内容，但不能恢复当前可信状态。
		if source == "CR" && out.RuntimeSnapshot == nil {
			if tx.QueryRowContext(durable, `SELECT runtime FROM instance_observations WHERE instance_id=?`, i.Id).Scan(&raw) == nil {
				var old savedObservation
				if json.Unmarshal(raw, &old) == nil && old.Runtime != nil && old.Runtime.LastSuccessAt != "" {
					out.RuntimeSnapshot = old.Runtime
					out.LastSuccessAt = old.Runtime.LastSuccessAt
				}
			}
		}
		out.Error = safeSourceError(cause)
		out.Historical = true
	} else {
		out.LastSuccessAt = now
		if out.RuntimeSnapshot != nil {
			out.RuntimeSnapshot.LastSuccessAt = now
			out.RuntimeSnapshot.ObservedAt = now
		}
		out.Error = ""
		out.Historical = false
	}
	out.Source = source
	out.Uid = reference(i.Uid)
	out.LastAttemptAt = now
	out.Stale = reference(false)
	raw, _ := json.Marshal(out)
	if _, e := tx.ExecContext(durable, `INSERT INTO instance_source_observations VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(instance_id,source) DO UPDATE SET uid=excluded.uid,document=excluded.document,last_attempt_at=excluded.last_attempt_at,last_success_at=excluded.last_success_at,error=excluded.error,epoch=excluded.epoch`, i.Id, source, i.Uid, raw, out.LastAttemptAt, out.LastSuccessAt, out.Error, s.observationEpoch); e != nil {
		out.Error = "来源保存失败"
		out.Historical = true
	}
	if tx.Commit() != nil {
		out.Error = "来源保存失败"
		out.Historical = true
	}
	return out
}

// instanceSources 在每次授权查询时重新计算过期，重启epoch不会把旧成功冒充当前。
func (s *Store) instanceSources(ctx context.Context, i *rpc.ObservationInstance) []*rpc.SourceObservation {
	out := []*rpc.SourceObservation{}
	var clusterState string
	active := s.DB.QueryRowContext(ctx, `SELECT state FROM clusters WHERE id=?`, i.ClusterId).Scan(&clusterState) == nil && clusterState == "ACTIVE"
	rows, e := s.DB.QueryContext(ctx, `SELECT source,document,epoch FROM instance_source_observations WHERE instance_id=? AND uid=? ORDER BY source`, i.Id, i.Uid)
	if e != nil {
		return out
	}
	defer rows.Close()
	for rows.Next() {
		var name, epoch string
		var raw []byte
		if rows.Scan(&name, &raw, &epoch) != nil {
			continue
		}
		v := &rpc.SourceObservation{Source: name, Uid: reference(i.Uid)}
		if json.Unmarshal(raw, v) != nil {
			v = &rpc.SourceObservation{Source: name, Uid: reference(i.Uid), Error: "历史来源记录不可读", Historical: true}
		}
		stamp, err := time.Parse(time.RFC3339Nano, v.LastSuccessAt)
		stale := err == nil && s.Now().Sub(stamp) > 30*time.Second
		v.Stale = reference(stale)
		// 关键：读取失败立即未知，30秒边界不能掩盖当前错误；epoch不匹配也不能恢复旧Available。
		v.Historical = v.Historical || !active || epoch != s.observationEpoch || stale || v.Error != ""
		out = append(out, v)
	}
	return out
}

func runtimeFromSource(sources []*rpc.SourceObservation, fallback *rpc.InstanceRuntime) *rpc.InstanceRuntime {
	for _, source := range sources {
		if source.Source != "CR" {
			continue
		}
		if !source.Historical && source.RuntimeSnapshot != nil {
			return source.RuntimeSnapshot
		}
		message := source.Error
		if message == "" {
			message = "历史观测，当前尚未重新读取或已过期"
		}
		return &rpc.InstanceRuntime{Summary: "Unknown", Error: message, ObservedAt: source.LastAttemptAt, LastSuccessAt: source.LastSuccessAt}
	}
	return fallback
}

// observationScope 把网络读取绑定到启动时的原资源与当前凭据修订，防迟到旧结果覆盖新来源。
type observationScope struct {
	instance, uid, cluster, clusterUID, name, namespace string
	credentialRevision                                  int64
}
type observationScopeKey struct{}

func observationScopeCurrent(ctx context.Context, tx *sql.Tx, original context.Context) bool {
	scope, ok := original.Value(observationScopeKey{}).(observationScope)
	if !ok {
		return true
	}
	var n int
	// 关键：读取完成与凭据切换/注销竞争时，只有同一原UID、当前ACTIVE凭据仍相符的结果可以提交。
	return tx.QueryRowContext(ctx, `SELECT count(*) FROM instances i JOIN clusters c ON c.id=i.cluster_id WHERE i.id=? AND i.uid=? AND i.name=? AND i.target_namespace=? AND i.lifecycle<>'DELETED' AND c.id=? AND c.uid=? AND c.state='ACTIVE' AND c.credential_revision=?`, scope.instance, scope.uid, scope.name, scope.namespace, scope.cluster, scope.clusterUID, scope.credentialRevision).Scan(&n) == nil && n == 1
}
