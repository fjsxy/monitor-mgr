// Agent存储安全读取真实StatefulSet与卷引用；策略采用不能由草稿或CR写入成功推断。
package control

import (
	"context"
	"net/url"
	"sort"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

type agentStatefulPolicy struct{ Name, UID, WhenScaled, WhenDeleted string }

// agentRetention按固定Kubernetes语义补缺省Retain，未知枚举拒绝而不猜测。
func agentRetention(spec map[string]any) (string, string, error) {
	p := objectMap(spec, "persistentVolumeClaimRetentionPolicy")
	scaled, deleted := objectString(p, "whenScaled"), objectString(p, "whenDeleted")
	if scaled == "" {
		scaled = "Retain"
	}
	if deleted == "" {
		deleted = "Retain"
	}
	if (scaled != "Retain" && scaled != "Delete") || (deleted != "Retain" && deleted != "Delete") {
		return "", "", Error("CONFLICT", "实际PVC保留策略无法确认")
	}
	return scaled, deleted, nil
}

// readAgentDeletionPolicies只读取原Agent UID控制的全部ST；任何读取失败保留错误，不转空集。
func (t *Target) readAgentDeletionPolicies(ctx context.Context, i *rpc.ObservationInstance, cr map[string]any) ([]agentStatefulPolicy, error) {
	sts, e := t.listObjects(ctx, "/apis/apps/v1/namespaces/"+url.PathEscape(instanceCRNamespace(i))+"/statefulsets")
	if e != nil {
		return nil, e
	}
	_, expected, e := agentRetention(objectMap(cr, "spec"))
	if e != nil {
		return nil, e
	}
	policies := []agentStatefulPolicy{}
	for _, st := range sts {
		if !agentOwnedBy(st, "PrometheusAgent", i.Uid) {
			continue
		}
		meta := objectMap(st, "metadata")
		uid, name := objectString(meta, "uid"), objectString(meta, "name")
		if uid == "" || name == "" {
			return nil, Error("CONFLICT", "实际StatefulSet身份不完整")
		}
		scaled, deleted, e := agentRetention(objectMap(st, "spec"))
		if e != nil {
			return nil, e
		}
		// 关键：删除使用ST实际whenDeleted；CR字段已变但ST未采用时必须先独立发布并等待确认。
		if deleted != expected {
			return nil, Error("CONFLICT", "StatefulSet尚未采用whenDeleted策略；请先单独发布策略并确认")
		}
		policies = append(policies, agentStatefulPolicy{Name: name, UID: uid, WhenScaled: scaled, WhenDeleted: deleted})
	}
	sort.Slice(policies, func(a, b int) bool { return policies[a].UID < policies[b].UID })
	return policies, nil
}
