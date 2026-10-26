// exporter 的运行能力来自已批准 RC4 发布镜像与实际进程身份，不复制 Operator 的 Service 算法。
package control

import (
	"context"
	"net/url"
	"strings"
)

// RC4 digest 来自 #28 锁定 release.json 的 OCI index 与双平台 manifest；tag 不构成能力证据。
var exporterOperatorDigests = map[string]bool{
	"sha256:1a9ec41de21c7b2b633ee7179f5f3f4c1a9e098f1dc4e74a9e720dbee73f19ff": true,
	"sha256:76fb66b77b9b9ed6094ef70fc84fb85a0f22d7b63416414bde09c6ca289f9065": true,
	"sha256:0eadb4af76f5a1c72eb3f5bb4b8372eab43dbabb0a4dfb194866d58cf854ebde": true,
}

// exporterManager 从公开发布仓库识别 controller，不能把 Prometheus Operator 当成 exporter 能力。
func exporterManager(pod map[string]any) map[string]any {
	for _, raw := range anySlice(objectMap(pod, "spec")["containers"]) {
		container, _ := raw.(map[string]any)
		image := strings.TrimPrefix(objectString(container, "image"), "docker.io/")
		if strings.HasPrefix(image, "fjsxy/monitor-in-hand:") || strings.HasPrefix(image, "fjsxy/monitor-in-hand@") {
			return container
		}
	}
	return nil
}

// exporterImageDigest 只接受公开发布仓库的不可变摘要，拒绝相同 tag 和外部同摘要文字。
func exporterImageDigest(image string) bool {
	image = strings.TrimPrefix(strings.TrimPrefix(image, "docker-pullable://"), "docker.io/")
	return strings.HasPrefix(image, "fjsxy/monitor-in-hand@") && exporterOperatorDigests[strings.TrimPrefix(image, "fjsxy/monitor-in-hand@")]
}

// exporterProcessIdentity 绑定实际运行容器与经发布验证的字节；Ready 只证明进程可用，不代表采集成功。
func exporterProcessIdentity(pod map[string]any, expected map[string]any) string {
	meta, spec, status := objectMap(pod, "metadata"), objectMap(pod, "spec"), objectMap(pod, "status")
	container := exporterManager(pod)
	if objectString(meta, "uid") == "" || meta["deletionTimestamp"] != nil || objectString(status, "phase") != "Running" || container == nil || !exporterImageDigest(objectString(container, "image")) || canonical(container) != canonical(expected) || len(anySlice(container["command"])) != 0 {
		return ""
	}
	args, e := argumentMap(container["args"])
	if e != nil || args["leader-elect"] != "true" {
		return ""
	}
	for flag := range args {
		if flag != "leader-elect" && flag != "health-probe-bind-address" && flag != "log-level" {
			return ""
		}
	}
	if objectString(spec, "serviceAccountName") == "" {
		return ""
	}
	for _, raw := range anySlice(status["containerStatuses"]) {
		actual, _ := raw.(map[string]any)
		if actual["name"] == container["name"] && actual["ready"] == true && exporterImageDigest(objectString(actual, "imageID")) {
			return objectString(actual, "containerID")
		}
	}
	return ""
}

// checkExporterOperatorRuntime 读取 Deployment→ReplicaSet→Pod 的原 UID 链，缺权限或滚动到旧进程均拒绝。
func (t *Target) checkExporterOperatorRuntime(ctx context.Context) error {
	deployments, e := t.listObjects(ctx, "/apis/apps/v1/deployments")
	if e != nil {
		return Error("CONFLICT", "无法读取 exporter Operator Deployment，不能确认配套采集运行能力")
	}
	proven := 0
	for _, deployment := range deployments {
		template := objectMap(objectMap(deployment, "spec"), "template")
		expected := exporterManager(template)
		if expected == nil {
			continue
		}
		meta := objectMap(deployment, "metadata")
		namespace, uid := objectString(meta, "namespace"), objectString(meta, "uid")
		if namespace == "" || uid == "" || meta["deletionTimestamp"] != nil || !exporterImageDigest(objectString(expected, "image")) {
			return Error("CONFLICT", "exporter Operator 尚未运行已批准的配套采集发布版本")
		}
		replicas, err := t.listObjects(ctx, "/apis/apps/v1/namespaces/"+url.PathEscape(namespace)+"/replicasets")
		if err != nil {
			return Error("CONFLICT", "无法核验 exporter Operator ReplicaSet 身份")
		}
		owners := map[string]bool{}
		for _, rs := range replicas {
			if agentOwnedBy(rs, "Deployment", uid) {
				owners[objectString(objectMap(rs, "metadata"), "uid")] = true
			}
		}
		pods, err := t.listObjects(ctx, "/api/v1/namespaces/"+url.PathEscape(namespace)+"/pods")
		if err != nil {
			return Error("CONFLICT", "无法读取 exporter Operator 实际 Pod")
		}
		count := 0
		for _, pod := range pods {
			owned := false
			for owner := range owners {
				owned = owned || owner != "" && agentOwnedBy(pod, "ReplicaSet", owner)
			}
			if !owned || objectMap(pod, "metadata")["deletionTimestamp"] != nil {
				continue
			}
			containerID := exporterProcessIdentity(pod, expected)
			if containerID == "" || objectMap(pod, "spec")["serviceAccountName"] != objectMap(template, "spec")["serviceAccountName"] {
				return Error("CONFLICT", "exporter Operator 原 Pod 未就绪或实际镜像/进程身份不匹配")
			}
			metadata := objectMap(pod, "metadata")
			var current map[string]any
			path := "/api/v1/namespaces/" + url.PathEscape(namespace) + "/pods/" + url.PathEscape(objectString(metadata, "name"))
			// 关键：列表与身份核验间替换/重启不能沿用旧 Pod 的能力；发布受理和实际派发仍各自重做此预检。
			if t.Request(ctx, "GET", path, nil, &current) != nil || objectMap(current, "metadata")["uid"] != metadata["uid"] || exporterProcessIdentity(current, expected) != containerID {
				return Error("CONFLICT", "exporter Operator Pod 或容器在核验期间变化")
			}
			count++
		}
		if count == 0 {
			return Error("CONFLICT", "没有可核实已批准配套采集能力的 exporter Operator 原 Pod")
		}
		proven += count
	}
	if proven == 0 {
		return Error("CONFLICT", "未发现可核实配套采集能力的运行中 exporter Operator")
	}
	return nil
}
