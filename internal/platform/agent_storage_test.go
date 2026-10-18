package platform_test

import (
	"encoding/json"
	"net/http"
	"strings"
	"sync"
	"testing"
)

// P14.policy-pending：真实HTTP/Kitex读取实际ST采用状态，不能仅凭CR策略打开删除预览。
func TestAgentStorageDeletePreviewRequiresAdoptedPolicy(t *testing.T) {
	p, c, cluster, _, f := agentSafetyFixture(t, nil)
	cfg := agentConfig("storage-policy", "monitoring")
	i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": cfg}, c, "create", 201)
	id := i["id"].(string)
	preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "", 200)
	op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "publish", 202)
	if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("前置Agent发布失败")
	}
	var mu sync.Mutex
	actual := "Delete"
	readFailed := false
	f.mu.Lock()
	f.hook = func(w http.ResponseWriter, r *http.Request) bool {
		if r.Method != "GET" {
			return false
		}
		if r.URL.Path == "/apis/apps/v1/namespaces/monitoring/statefulsets" {
			mu.Lock()
			defer mu.Unlock()
			if readFailed {
				w.WriteHeader(403)
				json.NewEncoder(w).Encode(map[string]any{})
				return true
			}
			st := map[string]any{"metadata": map[string]any{"namespace": "monitoring", "name": "prom-agent-storage-policy", "uid": "original-st-uid", "generation": 1, "ownerReferences": []any{map[string]any{"kind": "PrometheusAgent", "uid": "uid-monitoring/storage-policy", "controller": true}}}, "spec": map[string]any{"replicas": 1, "persistentVolumeClaimRetentionPolicy": map[string]any{"whenScaled": "Retain", "whenDeleted": actual}}, "status": map[string]any{"observedGeneration": 1}}
			json.NewEncoder(w).Encode(map[string]any{"items": []any{st}})
			return true
		}
		if strings.HasPrefix(r.URL.Path, "/apis/apps/v1/namespaces/monitoring/") || r.URL.Path == "/api/v1/namespaces/monitoring/pods" || r.URL.Path == "/api/v1/namespaces/monitoring/persistentvolumeclaims" {
			json.NewEncoder(w).Encode(map[string]any{"items": []any{}})
			return true
		}
		return false
	}
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/deletion-previews", map[string]any{}, c, "", 409)
	mu.Lock()
	actual = "Retain"
	readFailed = true
	mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/deletion-previews", map[string]any{}, c, "", 403)
	mu.Lock()
	readFailed = false
	mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/deletion-previews", map[string]any{}, c, "", 200)
	f.mu.Lock()
	exists := f.objects["monitoring/storage-policy"] != nil
	f.mu.Unlock()
	if !exists {
		t.Fatal("预览产生删除副作用")
	}
}

// 不完整的辅助列表必须保存读取错误，不能把缺items当成已经没有卷。
func TestAgentStorageIncompleteListRemainsUnknown(t *testing.T) {
	p, c, cluster, _, f := agentSafetyFixture(t, nil)
	cfg := agentConfig("storage-incomplete", "monitoring")
	i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": cfg}, c, "create", 201)
	id := i["id"].(string)
	preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "", 200)
	op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "publish", 202)
	if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("前置Agent发布失败")
	}
	f.mu.Lock()
	f.hook = func(w http.ResponseWriter, r *http.Request) bool {
		if r.Method == "GET" && r.URL.Path == "/apis/apps/v1/namespaces/monitoring/statefulsets" {
			json.NewEncoder(w).Encode(map[string]any{})
			return true
		}
		return false
	}
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/refresh", map[string]any{}, c, "", 200)
	current := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
	for _, raw := range current["sources"].([]any) {
		source := raw.(map[string]any)
		if source["source"] == "PVC" {
			if source["error"] == "" || source["historical"] != true {
				t.Fatal("不完整ST集合被当成PVC空集", source)
			}
			return
		}
	}
	t.Fatal("PVC来源未保留独立错误")
}

// A06：首次emptyDir不依赖StorageClass，也不能把PVC模板或保留策略写入CR。
func TestAgentStorageEmptyDirDoesNotRequirePVCClass(t *testing.T) {
	p, c, cluster, _, f := agentSafetyFixture(t, nil)
	f.mu.Lock()
	f.hook = func(w http.ResponseWriter, r *http.Request) bool {
		if r.URL.Path == "/apis/storage.k8s.io/v1/storageclasses" {
			w.WriteHeader(403)
			json.NewEncoder(w).Encode(map[string]any{})
			return true
		}
		return false
	}
	f.mu.Unlock()
	cfg := agentConfig("ephemeral-agent", "monitoring")
	cfg["storage"].(map[string]any)["mode"] = "emptyDir"
	delete(cfg["storage"].(map[string]any), "capacity")
	i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": cfg}, c, "create", 201)
	id := i["id"].(string)
	preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "", 200)
	op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "publish", 202)
	if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("emptyDir发布失败")
	}
	f.mu.Lock()
	spec := f.objects["monitoring/ephemeral-agent"]["spec"].(map[string]any)
	_, pvc := spec["storage"].(map[string]any)["volumeClaimTemplate"]
	_, ephemeral := spec["storage"].(map[string]any)["emptyDir"]
	_, policy := spec["persistentVolumeClaimRetentionPolicy"]
	f.mu.Unlock()
	if pvc || !ephemeral || policy {
		t.Fatal("emptyDir映射含PVC语义", spec)
	}
	changed := agentConfig("ephemeral-agent", "monitoring")
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "agent": changed}, c, "change-mode", 409)
}

// REG20-STORAGE-001.orphan：孤卷没有ownerRef也不能被新Agent确定名称自动挂载。
func TestAgentStorageNewInstanceRejectsOrphanClaim(t *testing.T) {
	p, c, cluster, _, f := agentSafetyFixture(t, nil)
	claim := "prom-agent-orphan-candidate-db-prom-agent-orphan-candidate-0"
	f.mu.Lock()
	f.hook = func(w http.ResponseWriter, r *http.Request) bool {
		if r.Method == "GET" && r.URL.Path == "/api/v1/namespaces/monitoring/persistentvolumeclaims/"+claim {
			json.NewEncoder(w).Encode(map[string]any{"metadata": map[string]any{"name": claim, "namespace": "monitoring", "uid": "old-orphan-pvc"}, "spec": map[string]any{"resources": map[string]any{"requests": map[string]any{"storage": "1Gi"}}}})
			return true
		}
		return false
	}
	f.mu.Unlock()
	cfg := agentConfig("orphan-candidate", "monitoring")
	i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": cfg}, c, "create", 201)
	id := i["id"].(string)
	clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "", 409)
	f.mu.Lock()
	_, created := f.objects["monitoring/orphan-candidate"]
	f.mu.Unlock()
	if created {
		t.Fatal("碰撞后仍创建候选Agent")
	}
}
