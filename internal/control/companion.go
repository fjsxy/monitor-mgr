// exporter 配套采集配置沿用锁定 monitor-op 的 Service 契约，平台只管理 ServiceMonitor。
package control

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"fmt"
	"net/url"
	"strings"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

const companionKey = "service-monitor"

// ksmServiceInstanceID 对齐 monitor-op 2b2a8b7 planner.go 的 stableInstanceID。
// 关键：点号和长名称会加入原始名称摘要，不能直接使用 CR name 或只替换点号。
func ksmServiceInstanceID(name string) string {
	normalized := strings.ReplaceAll(name, ".", "-")
	if normalized == name && len(normalized) <= 43 {
		return normalized
	}
	digest := fmt.Sprintf("%x", sha256.Sum256([]byte(name)))[:8]
	prefix := strings.Trim(normalized, "-")
	if len(prefix) > 34 {
		prefix = strings.TrimRight(prefix[:34], "-")
	}
	return prefix + "-" + digest
}

// companionResource 覆盖 Deployment、StatefulSetSharded 与 DaemonSetSharded 的全部指标 Service。
func companionResource(i *rpc.ObservationInstance, spec map[string]any) (frozenResource, error) {
	if i.CreatorUserId == "" {
		return frozenResource{}, Error("CONFLICT", "实例创建用户未知，不能生成配套 ServiceMonitor")
	}
	ns := objectString(spec, "targetNamespace")
	if ns == "" {
		return frozenResource{}, Error("CONFLICT", "配套 ServiceMonitor 部署 namespace 缺失")
	}
	identity := ksmServiceInstanceID(i.Name)
	name := "monitor-mgr-" + identity
	collection := "/apis/monitoring.coreos.com/v1/namespaces/" + url.PathEscape(ns) + "/servicemonitors"
	doc := map[string]any{"apiVersion": "monitoring.coreos.com/v1", "kind": "ServiceMonitor", "metadata": map[string]any{"name": name, "namespace": ns, "labels": map[string]any{"monitor-mgr.io/user-id": i.CreatorUserId}}, "spec": map[string]any{
		"namespaceSelector": map[string]any{"matchNames": []any{ns}},
		// 不限定 role/endpoint-group：DaemonSetSharded 的 node/control Service 都是本实例的必要指标源。
		"selector":  map[string]any{"matchLabels": map[string]any{"app.kubernetes.io/name": "kube-state-metrics", "app.kubernetes.io/instance": identity, "app.kubernetes.io/managed-by": "monitor-in-hand"}},
		"endpoints": []any{map[string]any{"port": "http-metrics", "path": "/metrics", "interval": "30s", "scrapeTimeout": "10s"}},
	}}
	return frozenResource{key: companionKey, position: 1, collection: collection, path: collection + "/" + name, document: doc}, nil
}

// companionFingerprint 绑定预览时的独立 UID、完整 spec 与平台管理标签；忽略 RV/status 自然推进。
func (s *Store) companionFingerprint(i *rpc.ObservationInstance, r frozenResource, cr map[string]any) string {
	var target any
	if cr != nil {
		target = map[string]any{"uid": objectString(objectMap(cr, "metadata"), "uid"), "spec": objectMap(cr, "spec"), "user": objectMap(objectMap(cr, "metadata"), "labels")["monitor-mgr.io/user-id"]}
	}
	stable := *i
	stable.Uid = ""
	return s.fingerprint(&stable, "companion-preview/v1:"+canonical(map[string]any{"desired": r.document, "target": target}))
}

type companionSnapshot struct {
	resource    frozenResource
	current     map[string]any
	fingerprint string
	drift       bool
}

// readCompanionSnapshot 是新发布专用预检；旧冻结版本和历史恢复不经过此构建器。
func (s *Store) readCompanionSnapshot(ctx context.Context, i *rpc.ObservationInstance, t *Target, cl *rpc.Cluster, spec map[string]any) (*companionSnapshot, error) {
	if i.Component != KSMComponent {
		return nil, nil
	}
	r, e := companionResource(i, spec)
	if e != nil {
		return nil, e
	}
	e = s.DB.QueryRowContext(ctx, `SELECT uid FROM instance_resource_identities WHERE instance_id=? AND resource_key=?`, i.Id, r.key).Scan(&r.originalUID)
	if e != nil && e != sql.ErrNoRows {
		return nil, e
	}
	if e = checkResourceDependencies(ctx, t, cl, r, false); e != nil {
		return nil, e
	}
	cr, e := readFrozenResource(ctx, t, r)
	if e != nil {
		return nil, e
	}
	if r.originalUID == "" && cr != nil {
		return nil, Error("CONFLICT", "同名 ServiceMonitor 已存在，不能自动接管")
	}
	if r.originalUID != "" && (cr == nil || objectString(objectMap(cr, "metadata"), "uid") != r.originalUID) {
		return nil, Error("CONFLICT", "配套 ServiceMonitor 原 UID 缺失或已变化")
	}
	drift := false
	if r.originalUID != "" && i.CurrentVersionId != "" {
		previous, e := readFrozenResources(ctx, s.DB, i.CurrentVersionId, false)
		if e != nil {
			return nil, e
		}
		for _, old := range previous {
			if old.key == r.key {
				drift = !frozenResourceMatches(i, old, cr)
			}
		}
	}
	return &companionSnapshot{r, cr, s.companionFingerprint(i, r, cr), drift}, nil
}

// validateCompanionDispatch 拒绝预览/受理后发生的独立资源变化，重试继续使用原预览和原版本。
func (s *Store) validateCompanionDispatch(ctx context.Context, o *rpc.Operation, i *rpc.ObservationInstance, r frozenResource, cr map[string]any) error {
	if r.key != companionKey {
		return nil
	}
	var expected string
	if e := s.DB.QueryRowContext(ctx, `SELECT b.storage_fingerprint FROM publication_operations p JOIN publication_previews b ON b.id=p.preview_id WHERE p.operation_id=?`, o.OperationId).Scan(&expected); e != nil {
		return e
	}
	if expected != s.companionFingerprint(i, r, cr) {
		return Error("CONFLICT", "配套 ServiceMonitor 基线已变化，请重新预览")
	}
	return nil
}

// companionDeletionImpact 在删除前核对每份原 UID 和冻结配置；已由本删除步骤确认消失的资源沿用原身份。
func (s *Store) companionDeletionImpact(ctx context.Context, i *rpc.ObservationInstance, t *Target) (string, error) {
	resources, e := readFrozenResources(ctx, s.DB, i.CurrentVersionId, false)
	if e != nil {
		return "", e
	}
	impact := []any{}
	for _, r := range resources {
		if r.key == "component" {
			continue
		}
		if e = s.DB.QueryRowContext(ctx, `SELECT uid FROM instance_resource_identities WHERE instance_id=? AND resource_key=?`, i.Id, r.key).Scan(&r.originalUID); e != nil || r.originalUID == "" {
			return "", Error("CONFLICT", "配套资源删除身份未确认")
		}
		cr, e := readFrozenResource(ctx, t, r)
		if e != nil {
			return "", e
		}
		if cr == nil {
			var done int
			e = s.DB.QueryRowContext(ctx, `SELECT count(*) FROM deletion_operations p JOIN operations o ON o.id=p.operation_id JOIN operation_lifecycle l ON l.operation_id=o.id JOIN resource_progress r ON r.attempt_id=l.current_attempt_id WHERE p.instance_id=? AND o.action='DELETE' AND r.resource_key=? AND r.state='SUCCEEDED' AND r.confirmed_uid=?`, i.Id, r.key, r.originalUID).Scan(&done)
			if e != nil || done != 1 {
				return "", Error("CONFLICT", "原配套资源缺失，不能确认删除影响")
			}
		} else if objectString(objectMap(cr, "metadata"), "uid") != r.originalUID || !frozenResourceMatches(i, r, cr) {
			return "", Error("CONFLICT", "配套资源 UID 或配置漂移，请先核验或发布确认")
		}
		impact = append(impact, map[string]any{"uid": r.originalUID, "target": r.document})
	}
	return canonical(impact), nil
}
