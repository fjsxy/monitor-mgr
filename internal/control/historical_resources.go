// 旧多 CR 任务停止派发；保留原始执行事实，新的读取观察独立存储。
package control

import (
	"context"
	"strings"
	"time"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

var historicalResourceSchema = []string{
	`CREATE TABLE historical_resource_observations(operation_id TEXT NOT NULL REFERENCES operations(id),resource_key TEXT NOT NULL,observation TEXT NOT NULL,observed_at TEXT NOT NULL,PRIMARY KEY(operation_id,resource_key))`,
}

const historicalPendingMessage = "旧方案资源步骤待集群管理员显式处理；平台仅只读核验，不继续写入或转换为新模式"

// historicalResourceOperation 用原集合识别旧任务，不能依靠当前草稿是否开启采集来推断。
func historicalResourceOperation(ctx context.Context, q clusterQuery, id string) bool {
	var n int
	if q.QueryRowContext(ctx, `SELECT count(*) FROM operation_resources WHERE operation_id=? AND (resource_key<>'component' OR json_extract(document,'$.kind') NOT IN ('KubeStateMetrics','PrometheusAgent'))`, id).Scan(&n) != nil {
		return true
	}
	return n > 0
}

// executablePrimaryResource 将派发能力限定为真实组件 CR；即便旧记录的 key 被伪装也不能写 SM。
func executablePrimaryResource(ctx context.Context, q clusterQuery, id string, i *rpc.ObservationInstance) (frozenResource, error) {
	rs, e := readFrozenResources(ctx, q, id, true)
	if e != nil {
		return frozenResource{}, e
	}
	if len(rs) != 1 || rs[0].key != "component" {
		return frozenResource{}, Error("CONFLICT", historicalPendingMessage)
	}
	r := rs[0]
	a := componentFor(i.Component)
	frozen := resourceInstance(i, r, r.originalUID)
	if objectString(r.document, "apiVersion") != a.apiVersion || objectString(r.document, "kind") != a.kind || r.collection != a.collectionPath(instanceCRNamespace(frozen)) || r.path != a.objectPath(frozen) {
		return frozenResource{}, Error("CONFLICT", "冻结目标不是本实例允许写入的原组件 CR")
	}
	return r, nil
}

// projectHistoricalResources 只改变本次响应，数据库中的旧状态、UID、消息和发送标记保持原样。
func projectHistoricalResources(ctx context.Context, q clusterQuery, o *rpc.Operation) {
	pending := false
	for _, p := range o.Resources {
		if p.Target.Key == "component" && (p.Target.Kind == "KubeStateMetrics" || p.Target.Kind == "PrometheusAgent") {
			continue
		}
		p.HistoricalPending = reference(p.State != "SUCCEEDED" && p.State != "CANCELLED")
		pending = pending || p.GetHistoricalPending()
		var observation string
		if q.QueryRowContext(ctx, `SELECT observation FROM historical_resource_observations WHERE operation_id=? AND resource_key=?`, o.OperationId, p.Target.Key).Scan(&observation) == nil {
			p.Observation = &observation
		} else {
			p.Observation = reference("尚未读取旧冻结目标；" + historicalPendingMessage)
		}
	}
	if pending {
		o.State = "HISTORICAL_PENDING"
		o.Message = historicalPendingMessage
	}
}

// verifyHistoricalResources 只 GET 原冻结路径；已发送的真实执行结果与新的观察不能相互覆盖。
func (s *Store) verifyHistoricalResources(ctx context.Context, o *rpc.Operation) {
	t, _, e := s.targetForRecovery(ctx, o.ClusterId)
	if e != nil {
		return
	}
	defer t.Close()
	rs, e := readFrozenResources(ctx, s.DB, o.OperationId, true)
	if e != nil {
		return
	}
	for _, r := range rs {
		if r.key == "component" && (objectString(r.document, "kind") == "KubeStateMetrics" || objectString(r.document, "kind") == "PrometheusAgent") {
			continue
		}
		// 关键：历史读取同样受限于 Kubernetes 原资源路径，不把数据库中的任意地址当成请求目标。
		if !strings.HasPrefix(r.path, "/apis/") || strings.Contains(r.path, "?") || strings.Contains(r.path, "..") {
			continue
		}
		cr, readErr := readFrozenResource(ctx, t, r)
		observation := "读取失败；保留旧执行结果及人工处理要求"
		var uid string
		if s.DB.QueryRowContext(ctx, `SELECT confirmed_uid FROM resource_progress WHERE attempt_id=? AND resource_key=?`, o.GetAttemptId(), r.key).Scan(&uid) != nil {
			continue
		}
		if uid == "" {
			uid = r.originalUID
		}
		switch {
		case readErr != nil:
		case uid == "":
			observation = "身份无法确认：原 UID 未确认，同名同 spec/labels 对象也不认领；" + historicalPendingMessage
		case cr == nil:
			observation = "原冻结路径当前不存在对象；未发送步骤不能据此假记成功；" + historicalPendingMessage
		case objectString(objectMap(cr, "metadata"), "uid") != uid:
			observation = "同名对象 UID 已替换；禁止接管；" + historicalPendingMessage
		case canonical(objectMap(cr, "spec")) != canonical(objectMap(r.document, "spec")):
			observation = "原 UID 存在但配置偏离旧冻结文档；" + historicalPendingMessage
		default:
			observation = "原 UID 与旧冻结配置一致；此为当前只读观察，不代表旧执行成功；" + historicalPendingMessage
		}
		s.DB.ExecContext(ctx, `INSERT INTO historical_resource_observations VALUES(?,?,?,?) ON CONFLICT(operation_id,resource_key) DO UPDATE SET observation=excluded.observation,observed_at=excluded.observed_at`, o.OperationId, r.key, observation, s.Now().UTC().Format(time.RFC3339Nano))
	}
}

// validateLegacyManagementSchema 兼容旧 v13 结构时核对原表形状，不能仅信任版本号而清表重建。
func validateLegacyManagementSchema(ctx context.Context, db clusterQuery) error {
	var exists int
	if e := db.QueryRowContext(ctx, `SELECT count(*) FROM sqlite_master WHERE type='table' AND name='instance_management_confirmations'`).Scan(&exists); e != nil {
		return e
	}
	if exists == 0 {
		return nil
	}
	rows, e := db.QueryContext(ctx, `SELECT sequence,instance_id,actor_id,confirmed_at,allow_cluster_managers FROM instance_management_confirmations LIMIT 0`)
	if e != nil {
		return Error("UNAVAILABLE", "旧 v13 管理确认结构不兼容，保留原库")
	}
	return rows.Close()
}
