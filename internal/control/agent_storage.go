// Agent存储安全读取真实StatefulSet与卷引用；策略采用不能由草稿或CR写入成功推断。
package control

import (
	"context"
	"net/url"
	"sort"
	"strconv"

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

// readAgentStatefulPolicies只读取原Agent UID控制的全部ST实际策略，独立于CR与草稿。
func (t *Target) readAgentStatefulPolicies(ctx context.Context, i *rpc.ObservationInstance) ([]agentStatefulPolicy, error) {
	sts, e := t.listObjects(ctx, "/apis/apps/v1/namespaces/"+url.PathEscape(instanceCRNamespace(i))+"/statefulsets")
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

		policies = append(policies, agentStatefulPolicy{Name: name, UID: uid, WhenScaled: scaled, WhenDeleted: deleted})
	}
	sort.Slice(policies, func(a, b int) bool { return policies[a].UID < policies[b].UID })
	return policies, nil
}

// agentStatefulName沿固定Operator的每shard命名规则计算，不能借名字把旧卷认作新卷。
func agentStatefulName(name string, shard int32) string {
	base := "prom-agent-" + name
	if shard > 0 {
		return base + "-shard-" + strconv.FormatInt(int64(shard), 10)
	}
	return base
}
func agentClaimName(name string, shard int32) string {
	return "prom-agent-" + name + "-db-" + agentStatefulName(name, shard) + "-0"
}

// checkNewAgentClaims只查询本次实际会使用的确定WAL名称；已有孤卷或读取错误都拒绝首次创建。
func (t *Target) checkNewAgentClaims(ctx context.Context, cfg *AgentConfig) error {
	if cfg.Storage.Mode != "PVC" || cfg.DesiredState == "Suspended" {
		return nil
	}
	for shard := int32(0); shard < cfg.Shards; shard++ {
		if e := ctx.Err(); e != nil {
			return e
		}
		var pvc map[string]any
		e := t.Request(ctx, "GET", "/api/v1/namespaces/"+url.PathEscape(cfg.Namespace)+"/persistentvolumeclaims/"+url.PathEscape(agentClaimName(cfg.Name, shard)), nil, &pvc)
		if apiCode(e) == "TARGET_NOT_FOUND" {
			continue
		}
		if e != nil {
			return e
		}
		// 关键：Kubernetes会复用同claimName的Retain卷；相同容量、无ownerRef、新CR UID都不是新卷证明。
		return Error("CONFLICT", "候选Agent的确定WAL PVC已存在；请使用新的实例名称，不认领或清理旧卷")
	}
	return nil
}
