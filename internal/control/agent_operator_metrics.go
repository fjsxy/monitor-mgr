// 固定Operator运行能力证据取自同一原Pod；当前RBAC或CRD不能证明启动时建立了controller/informer。
package control

import (
	"context"
	"net/url"
	"regexp"
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

var scrapeEndpointMetric = regexp.MustCompile(`endpoint="/apis/monitoring\.coreos\.com/v1alpha1/(namespaces/[^/]+/)?scrapeconfigs"`)

func operatorContainer(pod map[string]any) map[string]any {
	for _, raw := range anySlice(objectMap(pod, "spec")["containers"]) {
		c, ok := raw.(map[string]any)
		if ok && strings.Contains(objectString(c, "image"), "prometheus-operator/prometheus-operator") {
			return c
		}
	}
	return nil
}
func operatorContainerID(pod map[string]any, name string) (string, bool) {
	for _, raw := range anySlice(objectMap(pod, "status")["containerStatuses"]) {
		c, ok := raw.(map[string]any)
		if ok && c["name"] == name {
			return objectString(c, "containerID"), c["ready"] == true
		}
	}
	return "", false
}
func operatorFlagsSafe(args map[string]string) bool {
	for _, flag := range []string{"as", "as-group", "apiserver", "cert-file", "key-file", "ca-file", "tls-insecure", "tls-server-name", "kubeconfig"} {
		if _, ok := args[flag]; ok {
			return false
		}
	}
	return true
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
	template := objectMap(objectMap(o.deployment, "spec"), "template")
	expected := operatorContainer(map[string]any{"spec": objectMap(template, "spec")})
	proven := 0
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
		if meta["deletionTimestamp"] != nil {
			continue
		}
		name, uid := objectString(meta, "name"), objectString(meta, "uid")
		container := operatorContainer(pod)
		podArgs, parseErr := argumentMap(container["args"])
		if parseErr != nil {
			return parseErr
		}
		// 关键：同Deployment旧RS并不能代表当前template；滚动中不同scope/身份一律阻止，不能任选绿Pod。
		if uid == "" || container == nil || expected == nil || objectString(objectMap(pod, "spec"), "serviceAccountName") != objectString(objectMap(template, "spec"), "serviceAccountName") || container["image"] != expected["image"] || canonical(podArgs) != canonical(o.args) || len(anySlice(container["command"])) > 0 || !operatorFlagsSafe(podArgs) {
			return Error("CONFLICT", "Operator实际Pod的访问身份/目标/参数与当前配置无法一致确认")
		}
		containerID, ready := operatorContainerID(pod, objectString(container, "name"))
		if containerID == "" || !ready || objectString(objectMap(pod, "status"), "phase") != "Running" {
			return Error("CONFLICT", "Operator实际Pod尚未就绪")
		}
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
			if strings.HasPrefix(metric, "prometheus_operator_kubernetes_client_http_request_duration_seconds_count{") && scrapeEndpointMetric.MatchString(metric) && value > 0 {
				hasScrape = true
			}
		}
		var current map[string]any
		if e = t.Request(ctx, "GET", "/api/v1/namespaces/"+url.PathEscape(o.namespace)+"/pods/"+url.PathEscape(name), nil, &current); e != nil {
			return e
		}
		currentID, currentReady := operatorContainerID(current, objectString(container, "name"))
		if objectString(objectMap(current, "metadata"), "uid") != uid || currentID != containerID || !currentReady || objectMap(current, "metadata")["deletionTimestamp"] != nil {
			return Error("CONFLICT", "Operator Pod或容器读取期间被替换")
		}
		if !hasBuild || !hasAgent || requireScrape && !hasScrape {
			return Error("CONFLICT", "原Operator进程未证明Agent controller及所需ScrapeConfig informer已就绪")
		}
		proven++
	}
	if proven == 0 {
		return Error("CONFLICT", "没有身份与运行能力均可确认的Operator原Pod")
	}
	return nil
}
