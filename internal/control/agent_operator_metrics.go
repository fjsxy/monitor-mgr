// 固定Operator运行能力证据取自同一原Pod；仅当前RBAC或CRD不能证明启动时建立了informer。
package control

import (
	"context"
	"net/url"
	"strconv"
	"strings"
)

func agentOwnedBy(obj map[string]any, kind, uid string) bool {
	for _, raw := range anySlice(objectMap(obj, "metadata")["ownerReferences"]) {
		ref, ok := raw.(map[string]any)
		if ok && ref["kind"] == kind && ref["uid"] == uid && ref["controller"] == true {
			return true
		}
	}
	return false
}
func (t *Target) verifyOperatorRuntime(ctx context.Context, o *operatorAccess, requireScrape bool) error {
	if o.uid == "" {
		return Error("CONFLICT", "Operator原资源身份无法确认")
	}
	replicas, e := t.listObjects(ctx, "/apis/apps/v1/namespaces/"+url.PathEscape(o.namespace)+"/replicasets")
	if e != nil {
		return Error("TARGET_ERROR", "无法核验Operator ReplicaSet身份")
	}
	owners := map[string]bool{}
	for _, rs := range replicas {
		if agentOwnedBy(rs, "Deployment", o.uid) {
			owners[objectString(objectMap(rs, "metadata"), "uid")] = true
		}
	}
	pods, e := t.listObjects(ctx, "/api/v1/namespaces/"+url.PathEscape(o.namespace)+"/pods")
	if e != nil {
		return Error("TARGET_ERROR", "无法核验Operator Pod身份")
	}
	for _, pod := range pods {
		owned := false
		for uid := range owners {
			if uid != "" && agentOwnedBy(pod, "ReplicaSet", uid) {
				owned = true
			}
		}
		if !owned {
			continue
		}
		meta := objectMap(pod, "metadata")
		name, uid := objectString(meta, "name"), objectString(meta, "uid")
		if uid == "" || objectString(objectMap(pod, "status"), "phase") != "Running" {
			continue
		}
		ready := false
		for _, raw := range anySlice(objectMap(pod, "status")["containerStatuses"]) {
			status, ok := raw.(map[string]any)
			if ok && status["name"] == "prometheus-operator" && status["ready"] == true {
				ready = true
			}
		}
		if !ready {
			continue
		}
		// 关键：通过API代理取同Pod的metrics，API连接的TLS/身份与权限沿用目标适配器。
		var metrics targetText
		if e = t.Request(ctx, "GET", "/api/v1/namespaces/"+url.PathEscape(o.namespace)+"/pods/"+url.PathEscape(name+":8080")+"/proxy/metrics", nil, &metrics); e != nil {
			return Error("TARGET_FORBIDDEN", "无法读取Operator运行能力指标，需要pods/proxy读取权限")
		}
		hasAgent, hasScrape, hasBuild := false, false, false
		for _, line := range strings.Split(string(metrics), "\n") {
			fields := strings.Fields(line)
			if len(fields) < 2 {
				continue
			}
			value, err := strconv.ParseFloat(fields[1], 64)
			if err != nil {
				continue
			}
			metric := fields[0]
			if strings.HasPrefix(metric, "prometheus_operator_build_info{") && strings.Contains(metric, `version="0.83.0"`) && strings.Contains(metric, `revision="5cf2f5d`) {
				hasBuild = true
			}
			if strings.HasPrefix(metric, "prometheus_operator_ready{") && strings.Contains(metric, `controller="prometheus-agent"`) && value == 1 {
				hasAgent = true
			}
			if strings.HasPrefix(metric, "prometheus_operator_kubernetes_client_http_request_duration_seconds_count{") && strings.Contains(metric, "scrapeconfigs\"") && value > 0 {
				hasScrape = true
			}
		}
		var current map[string]any
		if e = t.Request(ctx, "GET", "/api/v1/namespaces/"+url.PathEscape(o.namespace)+"/pods/"+url.PathEscape(name), nil, &current); e != nil {
			return e
		}
		if objectString(objectMap(current, "metadata"), "uid") != uid {
			return Error("CONFLICT", "Operator Pod读取期间被替换")
		}
		if hasBuild && hasAgent && (!requireScrape || hasScrape) {
			return nil
		}
	}
	return Error("CONFLICT", "运行Operator未证明Agent controller及ScrapeConfig informer已就绪")
}
