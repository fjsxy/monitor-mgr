package platform_test

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"sync/atomic"

	ginkgo "github.com/onsi/ginkgo/v2"
)

func operatorDoc(args []string) map[string]any {
	return map[string]any{"items": []any{map[string]any{"metadata": map[string]any{"name": "prometheus-operator", "namespace": "operators", "uid": "operator-uid", "labels": map[string]string{"app.kubernetes.io/version": "0.83.0"}}, "spec": map[string]any{"template": map[string]any{"spec": map[string]any{"serviceAccountName": "prometheus-operator", "containers": []any{map[string]any{"name": "prometheus-operator", "image": "quay.io/prometheus-operator/prometheus-operator:v0.83.0", "args": args}}}}}}}}
}
func operatorPodDoc(name string, args []string) map[string]any {
	return map[string]any{"metadata": map[string]any{"name": name, "uid": "operator-pod-uid", "ownerReferences": []any{map[string]any{"kind": "ReplicaSet", "uid": "operator-rs-uid", "controller": true}}}, "spec": map[string]any{"serviceAccountName": "prometheus-operator", "containers": []any{map[string]any{"name": "prometheus-operator", "image": "quay.io/prometheus-operator/prometheus-operator:v0.83.0", "args": args}}}, "status": map[string]any{"phase": "Running", "containerStatuses": []any{map[string]any{"name": "prometheus-operator", "ready": true, "containerID": "containerd://operator-instance"}}}}
}

// CAP补充回归通过HTTP公开预览边界验证，缺启动能力、单-范围、override及旧RS均不能误放行。

// A13安全错误必须区分不存在、禁止访问与缺key，不把所有依赖问题揉成CONFLICT。

// R07公开读模型：CR持续成功也不能掩盖PVC/关联读取失败或刷新其最后成功时间。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestAgentIndependentSourceFreshness(t ginkgo.GinkgoTInterface) {
	// 接下来R07公开读模型：CR持续成功也不能掩盖PVC/关联读取失败或刷新其最后成功时间。
	ginkgo.By("R07公开读模型：CR持续成功也不能掩盖PVC/关联读取失败或刷新其最后成功时间。")

	f, target := agentTarget(t)
	p, c, owner := clusterPlatform(t)
	registered := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "sources", "ownerId": owner, "context": "explicit", "kubeconfig": target}, c, "register", 202)
	cluster := awaitClusterOperation(t, p, c, registered["operationId"].(string))["clusterId"].(string)
	var fault atomic.Int32
	f.mu.Lock()
	f.hook = func(w http.ResponseWriter, r *http.Request) bool {
		if strings.HasSuffix(r.URL.Path, "/servicemonitors") {
			if fault.Load() == 2 {
				w.WriteHeader(403)
				io.WriteString(w, `{}`)
			} else {
				io.WriteString(w, `{"items":[{"apiVersion":"monitoring.coreos.com/v1","kind":"ServiceMonitor","metadata":{"name":"matched","namespace":"monitoring","uid":"matched-uid","labels":{"team":"blue"}},"spec":{"selector":{},"endpoints":[{"port":"http"}]}}]}`)
			}
			return true
		}
		if strings.HasSuffix(r.URL.Path, "/persistentvolumeclaims") && fault.Load() == 1 {
			w.WriteHeader(403)
			io.WriteString(w, `{}`)
			return true
		}
		return false
	}
	f.mu.Unlock()
	cfg := agentConfig("source-reader", "monitoring")
	cfg["associations"].(map[string]any)["serviceMonitor"].(map[string]any)["object"] = map[string]any{"mode": "Labels", "selector": map[string]any{"matchLabels": map[string]string{"team": "blue"}}}
	i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": cfg}, c, "create", 201)
	id := i["id"].(string)
	preview := clusterCall(t, p, "POST", "/instances/"+id+"/association-previews", map[string]any{"draftRevision": 1}, c, "associations", 200)
	sources := func(value map[string]any) map[string]map[string]any {
		out := map[string]map[string]any{}
		for _, raw := range value["sources"].([]any) {
			v := raw.(map[string]any)
			out[v["source"].(string)] = v
		}
		return out
	}
	if len(sources(preview)["association:ServiceMonitor"]["matches"].([]any)) != 1 {
		t.Fatal("关联未独立读取匹配")
	}
	pv := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "preview", 200)
	op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": pv["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "publish", 202)
	if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("前置Agent发布失败")
	}
	clusterCall(t, p, "POST", "/instances/"+id+"/refresh", map[string]any{}, c, "refresh", 200)
	before := sources(clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200))
	for _, test := range []struct {
		fault  int32
		source string
	}{{2, "association:ServiceMonitor"}, {1, "PVC"}} {
		before = sources(clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200))
		fault.Store(test.fault)
		runtime := clusterCall(t, p, "POST", "/instances/"+id+"/refresh", map[string]any{}, c, "refresh-failure", 200)
		if runtime["summary"] != "Available" {
			t.Fatal("辅助失败污染主CR摘要")
		}
		current := sources(clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200))[test.source]
		old := before[test.source]
		if current["lastSuccessAt"] != old["lastSuccessAt"] || current["error"] == "" || current["historical"] != true || current["lastAttemptAt"] == old["lastAttemptAt"] {
			t.Fatal("辅助失败刷新成功时间或被当成空集")
		}
		if test.source == "association:ServiceMonitor" && len(current["matches"].([]any)) != 1 {
			t.Fatal("失败丢失历史匹配集合")
		}
	}
}

// 草稿匹配与实际CR运行来源属于不同读模型；冲突预览也不能写入运行缓存。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestAgentDraftSourceIsolation(t ginkgo.GinkgoTInterface) {
	// 接下来草稿匹配与实际CR运行来源属于不同读模型；冲突预览也不能写入运行缓存。
	ginkgo.By("草稿匹配与实际CR运行来源属于不同读模型；冲突预览也不能写入运行缓存。")

	f, target := agentTarget(t)
	p, c, owner := clusterPlatform(t)
	op := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "isolation", "ownerId": owner, "context": "explicit", "kubeconfig": target}, c, "register", 202)
	cluster := awaitClusterOperation(t, p, c, op["operationId"].(string))["clusterId"].(string)
	var revise atomic.Bool
	var duringPreview func()
	f.mu.Lock()
	f.hook = func(w http.ResponseWriter, r *http.Request) bool {
		if strings.HasSuffix(r.URL.Path, "/servicemonitors") {
			if revise.CompareAndSwap(true, false) {
				duringPreview()
			}
			io.WriteString(w, `{"items":[{"metadata":{"name":"blue","namespace":"monitoring","uid":"blue-uid","labels":{"team":"blue"}}},{"metadata":{"name":"green","namespace":"monitoring","uid":"green-uid","labels":{"team":"green"}}}]}`)
			return true
		}
		return false
	}
	f.mu.Unlock()
	cfg := agentConfig("isolation", "monitoring")
	rule := cfg["associations"].(map[string]any)["serviceMonitor"].(map[string]any)
	rule["object"] = map[string]any{"mode": "Labels", "selector": map[string]any{"matchLabels": map[string]string{"team": "blue"}}}
	i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": cfg}, c, "create", 201)
	id := i["id"].(string)
	pv := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "preview", 200)
	pub := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": pv["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "publish", 202)
	if awaitClusterOperation(t, p, c, pub["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("前置发布失败")
	}
	clusterCall(t, p, "POST", "/instances/"+id+"/refresh", map[string]any{}, c, "refresh", 200)
	source := func(v map[string]any) map[string]any {
		for _, raw := range v["sources"].([]any) {
			s := raw.(map[string]any)
			if s["source"] == "association:ServiceMonitor" {
				return s
			}
		}
		t.Fatal("缺运行来源")
		return nil
	}
	before := source(clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200))
	rule["object"].(map[string]any)["selector"].(map[string]any)["matchLabels"] = map[string]string{"team": "green"}
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "agent": cfg}, c, "save-green", 200)
	preview := clusterCall(t, p, "POST", "/instances/"+id+"/association-previews", map[string]any{"draftRevision": 2}, c, "preview-green", 200)
	if source(preview)["matches"].([]any)[0].(map[string]any)["uid"] != "green-uid" {
		t.Fatal("草稿预览未采用B规则")
	}
	assertUnchanged := func() {
		after := source(clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200))
		left, _ := json.Marshal(before)
		right, _ := json.Marshal(after)
		if string(left) != string(right) {
			t.Fatal("草稿预览覆盖已部署运行来源或时间")
		}
	}
	assertUnchanged()
	duringPreview = func() {
		clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 2, "agent": cfg}, c, "save-during-preview", 200)
	}
	revise.Store(true)
	clusterCall(t, p, "POST", "/instances/"+id+"/association-previews", map[string]any{"draftRevision": 2}, c, "preview-conflict", 409)
	assertUnchanged()
}
