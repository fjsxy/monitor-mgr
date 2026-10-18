// Agent运行摘要基于当前CR的适用status；写入、运行和数据发送成功分别表达。
package control

import rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"

func interpretAgent(cr map[string]any, now string) *rpc.InstanceRuntime {
	out := &rpc.InstanceRuntime{Summary: "Unknown", ObservedAt: now, LastSuccessAt: now, DesiredState: "Running"}
	spec, status, meta := objectMap(cr, "spec"), objectMap(cr, "status"), objectMap(cr, "metadata")
	replicas := int32(1)
	if x, ok := number(spec, "replicas"); ok {
		replicas = x
	}
	shards := int32(1)
	if x, ok := number(spec, "shards"); ok && x > 1 {
		shards = x
	}
	if replicas == 0 {
		out.DesiredState = "Suspended"
	}
	paused := spec["paused"] == true
	out.Paused = &paused
	mode := objectString(spec, "mode")
	if mode == "" {
		mode = "StatefulSet"
	}
	if len(status) == 0 {
		out.Error = "缺少运行状态，当前未知"
		return out
	}
	gen, gok := meta["generation"].(float64)
	availableCondition := ""
	reason := ""
	for _, raw := range anySlice(status["conditions"]) {
		c, ok := raw.(map[string]any)
		if ok && c["type"] == "Available" && gok && c["observedGeneration"] == gen {
			availableCondition = objectString(c, "status")
			reason = objectString(c, "reason")
		}
	}
	if availableCondition == "" {
		out.Error = "缺少适用于当前配置的运行状态"
		return out
	}
	actual, aok := number(status, "replicas")
	available, vok := number(status, "availableReplicas")
	// 关键：仅在当前generation的Available条件已确认且期望零副本时，将合法omitempty计数解释为0。
	if replicas == 0 {
		if _, present := status["replicas"]; !present {
			actual = 0
			aok = true
		}
		if _, present := status["availableReplicas"]; !present {
			available = 0
			vok = true
		}
	}
	if !aok || !vok || available > actual || replicas > 0 && shards > 2147483647/replicas {
		out.Error = "运行数量不完整或超出范围"
		return out
	}
	desired := replicas * shards
	out.AvailablePods = &available
	out.DesiredPods = &desired
	if paused {
		out.Error = "Operator调谐已暂停，不能据此认定Pod已停止"
		return out
	}
	if mode != "StatefulSet" {
		out.Error = "观测到未支持的Agent部署模式"
		out.DeploymentMode = mode
		return out
	}
	out.DeploymentMode = mode
	out.Shards = &shards
	if desired == 0 {
		if actual == 0 && availableCondition == "True" {
			out.Summary = "Suspended"
		} else {
			out.Summary = "NotReady"
			out.Error = "挂起意图尚未观察到全部Pod停止"
		}
		return out
	}
	out.Summary = "NotReady"
	out.Error = reason
	if availableCondition == "True" && available >= desired {
		out.Summary = "Available"
		out.Error = ""
	}
	return out
}
