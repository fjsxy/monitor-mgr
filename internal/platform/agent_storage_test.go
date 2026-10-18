package platform_test

import (
	"encoding/json"
	"net/http"
	"strings"
	"sync"
	"testing"
	"time"
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

// R03.agent-zero：适用当前generation的零副本status可合法省略计数字段，不能推断仍在运行。
func TestR03AgentZeroUsesApplicableStatus(t *testing.T) {
	for _, variant := range []string{"valid-zero", "missing-status", "old-generation"} {
		t.Run(variant, func(t *testing.T) {
			p, c, cluster, _, f := agentSafetyFixture(t, nil)
			cr := map[string]any{"apiVersion": "monitoring.coreos.com/v1alpha1", "kind": "PrometheusAgent", "metadata": map[string]any{"name": "zero-agent", "namespace": "monitoring", "uid": "zero-agent-uid", "generation": 2}, "spec": map[string]any{"replicas": 0, "shards": 3, "paused": false, "mode": "StatefulSet"}}
			if variant != "missing-status" {
				generation := 2
				if variant == "old-generation" {
					generation = 1
				}
				cr["status"] = map[string]any{"conditions": []any{map[string]any{"type": "Available", "status": "True", "observedGeneration": generation}}}
			}
			f.mu.Lock()
			f.objects["monitoring/zero-agent"] = cr
			f.mu.Unlock()
			list := clusterCall(t, p, "GET", "/clusters/"+cluster+"/instances", nil, c, "", 200)["instances"].([]any)
			runtime := list[0].(map[string]any)["runtime"].(map[string]any)
			if variant == "valid-zero" {
				if runtime["summary"] != "Suspended" || runtime["desiredPods"] != float64(0) || runtime["availablePods"] != float64(0) || runtime["shards"] != float64(3) || runtime["paused"] != false {
					t.Fatal("合法零值被丢失或被误报运行", runtime)
				}
			} else if runtime["summary"] != "Unknown" {
				t.Fatal("无适用status猜测挂起", runtime)
			}
		})
	}
}

// 已确认原UID卷在父资源消失后仍单独读取；同名新UID不能取代历史身份。
func TestAgentStorageRetainedIdentitySurvivesParents(t *testing.T) {
	p, c, cluster, _, f := agentSafetyFixture(t, nil)
	name := "history-agent"
	cfg := agentConfig(name, "monitoring")
	i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": cfg}, c, "create", 201)
	id := i["id"].(string)
	preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "", 200)
	op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "publish", 202)
	if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("前置发布失败")
	}
	var mu sync.Mutex
	parents := true
	pvcUID := "original-pvc-uid"
	claim := "prom-agent-history-agent-db-prom-agent-history-agent-0"
	f.mu.Lock()
	f.hook = func(w http.ResponseWriter, r *http.Request) bool {
		if r.Method != "GET" {
			return false
		}
		mu.Lock()
		defer mu.Unlock()
		items := []any{}
		switch r.URL.Path {
		case "/apis/apps/v1/namespaces/monitoring/statefulsets":
			if parents {
				items = append(items, map[string]any{"metadata": map[string]any{"namespace": "monitoring", "name": "prom-agent-history-agent", "uid": "original-st-uid", "ownerReferences": []any{map[string]any{"kind": "PrometheusAgent", "uid": "uid-monitoring/history-agent", "controller": true}}}, "spec": map[string]any{"replicas": 1}})
			}
		case "/api/v1/namespaces/monitoring/pods":
			if parents {
				items = append(items, map[string]any{"metadata": map[string]any{"namespace": "monitoring", "name": "prom-agent-history-agent-0", "uid": "original-pod-uid", "ownerReferences": []any{map[string]any{"kind": "StatefulSet", "uid": "original-st-uid", "controller": true}}}, "spec": map[string]any{"volumes": []any{map[string]any{"name": "wal", "persistentVolumeClaim": map[string]any{"claimName": claim}}}, "containers": []any{map[string]any{"volumeMounts": []any{map[string]any{"name": "wal", "mountPath": "/prometheus"}}}}}, "status": map[string]any{"conditions": []any{map[string]any{"type": "Ready", "status": "True"}}}})
			}
		case "/api/v1/namespaces/monitoring/persistentvolumeclaims":
			items = append(items, map[string]any{"metadata": map[string]any{"namespace": "monitoring", "name": claim, "uid": pvcUID}, "spec": map[string]any{"resources": map[string]any{"requests": map[string]any{"storage": "1Gi"}}, "accessModes": []any{"ReadWriteOnce"}}, "status": map[string]any{"phase": "Bound"}})
		default:
			return false
		}
		json.NewEncoder(w).Encode(map[string]any{"items": items})
		return true
	}
	f.mu.Unlock()
	source := func() map[string]any {
		clusterCall(t, p, "POST", "/instances/"+id+"/refresh", map[string]any{}, c, "", 200)
		current := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
		for _, raw := range current["sources"].([]any) {
			v := raw.(map[string]any)
			if v["source"] == "PVC" {
				return v
			}
		}
		t.Fatal("无PVC来源")
		return nil
	}
	first := source()
	if len(storageVolumes(first)) != 1 {
		t.Fatal("未识别原卷", first)
	}
	mu.Lock()
	parents = false
	mu.Unlock()
	retained := source()
	if len(storageVolumes(retained)) != 1 || retained["volumes"].([]any)[0].(map[string]any)["uid"] != "original-pvc-uid" {
		t.Fatal("父消失后丢失原卷身份", retained)
	}
	if len(retained["volumes"].([]any)[0].(map[string]any)["mountedByPods"].([]any)) != 0 {
		t.Fatal("残留卷误报挂载", retained)
	}
	mu.Lock()
	pvcUID = "replacement-pvc-uid"
	mu.Unlock()
	replaced := source()
	if replaced["error"] == "" || replaced["historical"] != true {
		t.Fatal("同名新UID被认领", replaced)
	}
}

// storageVolumes把合法空列表作为空集断言，避免缺失字段导致测试panic。
func storageVolumes(source map[string]any) []any { v, _ := source["volumes"].([]any); return v }

const dropAgentStorageSchema = `ALTER TABLE publication_previews DROP COLUMN storage_fingerprint;DROP TABLE IF EXISTS agent_storage_tasks;DROP TABLE IF EXISTS agent_volume_lineage;DROP TABLE IF EXISTS agent_volume_identities;`

// A02/P14：开放类型化多shard和挂起，但危险预览必须读取每个相关ST的实际策略。
func TestAgentStorageDangerRequiresAdoptedPolicy(t *testing.T) {
	for _, action := range []string{"suspend", "shard"} {
		t.Run(action, func(t *testing.T) {
			p, c, cluster, _, f := agentSafetyFixture(t, nil)
			name := "danger-" + action
			cfg := agentConfig(name, "monitoring")
			cfg["shards"] = 2
			i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": cfg}, c, "create", 201)
			id := i["id"].(string)
			preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "", 200)
			op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "publish", 202)
			if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
				t.Fatal("前置发布失败")
			}
			var mu sync.Mutex
			adopted := false
			f.mu.Lock()
			f.hook = func(w http.ResponseWriter, r *http.Request) bool {
				if r.Method != "GET" || r.URL.Path != "/apis/apps/v1/namespaces/monitoring/statefulsets" {
					return false
				}
				mu.Lock()
				defer mu.Unlock()
				policy := "Delete"
				if adopted {
					policy = "Retain"
				}
				items := []any{}
				for n := 0; n < 2; n++ {
					stName := "prom-agent-" + name
					if n > 0 {
						stName += "-shard-1"
					}
					items = append(items, map[string]any{"metadata": map[string]any{"namespace": "monitoring", "name": stName, "uid": stName + "-uid", "ownerReferences": []any{map[string]any{"kind": "PrometheusAgent", "uid": "uid-monitoring/" + name, "controller": true}}}, "spec": map[string]any{"replicas": 1, "persistentVolumeClaimRetentionPolicy": map[string]any{"whenScaled": policy, "whenDeleted": policy}}})
				}
				json.NewEncoder(w).Encode(map[string]any{"items": items})
				return true
			}
			f.mu.Unlock()
			if action == "suspend" {
				cfg["desiredState"] = "Suspended"
			} else {
				cfg["shards"] = 1
			}
			clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "agent": cfg}, c, "save", 200)
			clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 2}, c, "", 409)
			mu.Lock()
			adopted = true
			mu.Unlock()
			adoptedPreview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 2}, c, "", 200)
			policies, _ := adoptedPreview["policies"].([]any)
			if adoptedPreview["storageMode"] != "PVC" || len(policies) != 2 {
				t.Fatal("危险预览缺少实际ST策略与存储影响", adoptedPreview)
			}
			cfg["storage"].(map[string]any)["whenScaled"] = "Delete"
			cfg["storage"].(map[string]any)["whenDeleted"] = "Delete"
			clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 2, "agent": cfg}, c, "mixed", 200)
			clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 3}, c, "", 409)
		})
	}
}

// REG20-STORAGE-002：实例完成与逐卷任务独立；未知/Delete待核验保留凭据责任，合法Retain不阻塞集群变更。
func TestAgentStorageResponsibilityOutlivesInstance(t *testing.T) {
	for _, action := range []string{"transfer", "unregister"} {
		for _, policy := range []string{"Delete", "Retain"} {
			t.Run(action+"-"+policy, func(t *testing.T) {
				p, c, cluster, _, f := agentSafetyFixture(t, nil)
				name := "storage-duty"
				cfg := agentConfig(name, "monitoring")
				cfg["storage"].(map[string]any)["whenDeleted"] = policy
				i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": cfg}, c, "create", 201)
				id := i["id"].(string)
				preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "", 200)
				op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "publish", 202)
				if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
					t.Fatal("前置发布失败")
				}
				var mu sync.Mutex
				removed, denied, gone := false, false, false
				claim := "prom-agent-storage-duty-db-prom-agent-storage-duty-0"
				f.mu.Lock()
				f.hook = func(w http.ResponseWriter, r *http.Request) bool {
					if r.Method == "DELETE" && strings.HasSuffix(r.URL.Path, "/prometheusagents/"+name) {
						var body map[string]any
						json.NewDecoder(r.Body).Decode(&body)
						if body["propagationPolicy"] != "Foreground" || body["preconditions"].(map[string]any)["uid"] != "uid-monitoring/"+name {
							t.Error("没有原UID foreground条件")
						}
						f.mu.Lock()
						delete(f.objects, "monitoring/"+name)
						f.mu.Unlock()
						mu.Lock()
						removed = true
						denied = policy == "Delete"
						mu.Unlock()
						json.NewEncoder(w).Encode(map[string]any{})
						return true
					}
					if r.Method != "GET" {
						return false
					}
					mu.Lock()
					defer mu.Unlock()
					pvc := map[string]any{"metadata": map[string]any{"namespace": "monitoring", "name": claim, "uid": "duty-pvc-uid"}, "spec": map[string]any{"resources": map[string]any{"requests": map[string]any{"storage": "1Gi"}}, "accessModes": []any{"ReadWriteOnce"}}, "status": map[string]any{"phase": "Bound"}}
					if policy == "Delete" {
						pvc["metadata"].(map[string]any)["ownerReferences"] = []any{map[string]any{"kind": "StatefulSet", "uid": "duty-st-uid", "controller": true}}
					}
					items := []any{}
					switch r.URL.Path {
					case "/apis/apps/v1/namespaces/monitoring/statefulsets":
						if !removed {
							items = append(items, map[string]any{"metadata": map[string]any{"namespace": "monitoring", "name": "prom-agent-" + name, "uid": "duty-st-uid", "ownerReferences": []any{map[string]any{"kind": "PrometheusAgent", "uid": "uid-monitoring/" + name, "controller": true}}}, "spec": map[string]any{"replicas": 1, "persistentVolumeClaimRetentionPolicy": map[string]any{"whenScaled": "Retain", "whenDeleted": policy}}})
						}
					case "/api/v1/namespaces/monitoring/pods":
						if !removed {
							items = append(items, map[string]any{"metadata": map[string]any{"namespace": "monitoring", "name": "prom-agent-" + name + "-0", "uid": "duty-pod-uid", "ownerReferences": []any{map[string]any{"kind": "StatefulSet", "uid": "duty-st-uid", "controller": true}}}, "spec": map[string]any{"volumes": []any{map[string]any{"name": "wal", "persistentVolumeClaim": map[string]any{"claimName": claim}}}}})
						}
					case "/api/v1/namespaces/monitoring/persistentvolumeclaims":
						if !gone {
							items = append(items, pvc)
						}
					case "/api/v1/namespaces/monitoring/persistentvolumeclaims/" + claim:
						if denied {
							w.WriteHeader(403)
							json.NewEncoder(w).Encode(map[string]any{})
							return true
						}
						if gone {
							w.WriteHeader(404)
							json.NewEncoder(w).Encode(map[string]any{})
							return true
						}
						json.NewEncoder(w).Encode(pvc)
						return true
					default:
						if removed && (strings.HasSuffix(r.URL.Path, "/statefulsets/prom-agent-"+name) || strings.HasSuffix(r.URL.Path, "/pods/prom-agent-"+name+"-0")) {
							w.WriteHeader(404)
							json.NewEncoder(w).Encode(map[string]any{})
							return true
						}
						return false
					}
					json.NewEncoder(w).Encode(map[string]any{"items": items})
					return true
				}
				f.mu.Unlock()
				current := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
				dp := clusterCall(t, p, "POST", "/instances/"+id+"/deletion-previews", map[string]any{}, c, "", 200)
				deletion := clusterCall(t, p, "POST", "/instances/"+id+"/deletions", map[string]any{"versionId": current["currentVersionId"], "previewId": dp["previewId"]}, c, "delete", 202)
				oid := deletion["operationId"].(string)
				if awaitClusterOperation(t, p, c, oid)["state"] != "SUCCEEDED" {
					t.Fatal("实例删除未独立完成")
				}
				current = clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
				if current["lifecycle"] != "DELETED" {
					t.Fatal("卷任务延迟实例完成", current)
				}
				body := map[string]any{}
				if action == "transfer" {
					u := clusterCall(t, p, "POST", "/accounts", map[string]any{"username": "new-owner", "password": "new-owner-password-123", "role": "USER"}, c, "new-owner", 201)
					body["ownerId"] = u["id"]
				}
				if policy == "Delete" {
					current = clusterCall(t, p, "POST", "/operations/"+oid+"/refresh", nil, c, "", 200)
					if current["storageResult"] != "Unknown" {
						t.Fatal("PVC读取失败未独立表达", current)
					}
					clusterCall(t, p, "POST", "/clusters/"+cluster+"/"+action, body, c, "blocked-unknown", 409)
					mu.Lock()
					denied = false
					mu.Unlock()
					current = clusterCall(t, p, "POST", "/operations/"+oid+"/refresh", nil, c, "", 200)
					if current["storageResult"] != "Deleting" {
						t.Fatal("原卷残留未保持独立待确认", current)
					}
					clusterCall(t, p, "POST", "/clusters/"+cluster+"/"+action, body, c, "blocked-pending", 409)
					mu.Lock()
					gone = true
					mu.Unlock()
					current = clusterCall(t, p, "POST", "/operations/"+oid+"/refresh", nil, c, "", 200)
					if current["storageResult"] != "Deleted" {
						t.Fatal("原卷消失未核实", current)
					}
				} else {
					current = clusterCall(t, p, "POST", "/operations/"+oid+"/refresh", nil, c, "", 200)
					if current["storageResult"] != "Retained" {
						t.Fatal("合法Retain未单独完成", current)
					}
				}
				clusterCall(t, p, "POST", "/clusters/"+cluster+"/"+action, body, c, "allowed", 200)
			})
		}
	}
}

// REG20-STORAGE-003：每种影响事实独立注入，ST新UID负例保持安全guard仍可通过。
func TestAgentStoragePreviewImpact(t *testing.T) {
	variants := []string{"st-uid-accept", "st-uid-dispatch", "pvc-uid-accept", "pvc-uid-dispatch", "policy-accept", "policy-dispatch", "status-rv", "same-st-pod-roll"}
	for _, variant := range variants {
		t.Run(variant, func(t *testing.T) {
			var mu sync.Mutex
			armed := false
			entered, release := make(chan struct{}), make(chan struct{})
			var once, released sync.Once
			p, c, cluster, _, f := agentSafetyFixture(t, func(stage, id string) error {
				mu.Lock()
				block := armed && stage == "beforeDispatch"
				mu.Unlock()
				if block {
					once.Do(func() { close(entered) })
					<-release
				}
				return nil
			})
			defer released.Do(func() { close(release) })
			name := "impact-agent"
			cfg := agentConfig(name, "monitoring")
			i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": cfg}, c, "create", 201)
			id := i["id"].(string)
			preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "", 200)
			op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "publish", 202)
			if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
				t.Fatal("前置发布失败")
			}
			stUID, pvcUID, podUID, policy := "impact-st-uid", "impact-pvc-uid", "impact-pod-uid", "Retain"
			claim := "prom-agent-impact-agent-db-prom-agent-impact-agent-0"
			f.mu.Lock()
			f.hook = func(w http.ResponseWriter, r *http.Request) bool {
				if r.Method != "GET" {
					return false
				}
				mu.Lock()
				defer mu.Unlock()
				items := []any{}
				switch r.URL.Path {
				case "/apis/apps/v1/namespaces/monitoring/statefulsets":
					items = append(items, map[string]any{"metadata": map[string]any{"namespace": "monitoring", "name": "prom-agent-" + name, "uid": stUID, "resourceVersion": "natural-rv", "ownerReferences": []any{map[string]any{"kind": "PrometheusAgent", "uid": "uid-monitoring/" + name, "controller": true}}}, "spec": map[string]any{"replicas": 1, "persistentVolumeClaimRetentionPolicy": map[string]any{"whenScaled": policy, "whenDeleted": "Retain"}}})
				case "/api/v1/namespaces/monitoring/pods":
					items = append(items, map[string]any{"metadata": map[string]any{"namespace": "monitoring", "name": "prom-agent-" + name + "-0", "uid": podUID, "ownerReferences": []any{map[string]any{"kind": "StatefulSet", "uid": stUID, "controller": true}}}, "spec": map[string]any{"volumes": []any{map[string]any{"name": "wal", "persistentVolumeClaim": map[string]any{"claimName": claim}}}}})
				case "/api/v1/namespaces/monitoring/persistentvolumeclaims":
					items = append(items, map[string]any{"metadata": map[string]any{"namespace": "monitoring", "name": claim, "uid": pvcUID}, "spec": map[string]any{"resources": map[string]any{"requests": map[string]any{"storage": "1Gi"}}}, "status": map[string]any{"phase": "Bound"}})
				default:
					return false
				}
				json.NewEncoder(w).Encode(map[string]any{"items": items})
				return true
			}
			f.mu.Unlock()
			cfg["desiredState"] = "Suspended"
			clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "agent": cfg}, c, "save", 200)
			preview = clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 2}, c, "", 200)
			change := func() {
				mu.Lock()
				switch {
				case strings.HasPrefix(variant, "st-uid"):
					stUID = "replacement-st-uid"
					podUID = "replacement-st-pod-uid"
				case strings.HasPrefix(variant, "pvc-uid"):
					pvcUID = "replacement-pvc-uid"
				case strings.HasPrefix(variant, "policy"):
					policy = "Delete"
				case variant == "same-st-pod-roll":
					podUID = "rolled-pod-uid"
				}
				mu.Unlock()
				if variant == "status-rv" {
					f.mu.Lock()
					f.objects["monitoring/"+name]["metadata"].(map[string]any)["resourceVersion"] = "2"
					f.objects["monitoring/"+name]["status"].(map[string]any)["replicas"] = 1
					f.mu.Unlock()
				}
			}
			body := map[string]any{"previewId": preview["previewId"], "draftRevision": 2, "overwriteDrift": false}
			negative := strings.Contains(variant, "-accept") || strings.Contains(variant, "-dispatch")
			if strings.HasSuffix(variant, "-dispatch") {
				mu.Lock()
				armed = true
				mu.Unlock()
				op = clusterCall(t, p, "POST", "/instances/"+id+"/publications", body, c, "danger", 202)
				select {
				case <-entered:
				case <-time.After(3 * time.Second):
					t.Fatal("未到派发前产品屏障")
				}
				change()
				released.Do(func() { close(release) })
				if current := awaitClusterOperation(t, p, c, op["operationId"].(string)); current["state"] != "FAILED" {
					t.Fatal("旧存储影响预览仍派发", current)
				}
			} else {
				change()
				status := 202
				if negative {
					status = 409
				}
				op = clusterCall(t, p, "POST", "/instances/"+id+"/publications", body, c, "danger", status)
				if !negative && awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
					t.Fatal("自然推进误使预览失效")
				}
			}
			f.mu.Lock()
			replicas := f.objects["monitoring/"+name]["spec"].(map[string]any)["replicas"]
			f.mu.Unlock()
			if negative && replicas != float64(1) {
				t.Fatal("拒绝前出现目标mutation")
			}
		})
	}
}

// REG20-STORAGE-004：ST ownerRef在保留ST的挂起中合法，删ST时却明确受whenDeleted=Delete控制。
func TestAgentStorageIndependentOwnerPolicy(t *testing.T) {
	for _, variant := range []string{"scaled-retain-deleted-delete", "delete-with-st-owner"} {
		t.Run(variant, func(t *testing.T) {
			p, c, cluster, _, f := agentSafetyFixture(t, nil)
			name := "owner-policy"
			cfg := agentConfig(name, "monitoring")
			cfg["storage"].(map[string]any)["whenDeleted"] = "Delete"
			i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": cfg}, c, "create", 201)
			id := i["id"].(string)
			preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "", 200)
			op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "first", 202)
			if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
				t.Fatal("前置发布失败")
			}
			var mu sync.Mutex
			removed, gone := false, false
			claim := "prom-agent-owner-policy-db-prom-agent-owner-policy-0"
			f.mu.Lock()
			f.hook = func(w http.ResponseWriter, r *http.Request) bool {
				if r.Method == "DELETE" && strings.HasSuffix(r.URL.Path, "/prometheusagents/"+name) {
					var b map[string]any
					json.NewDecoder(r.Body).Decode(&b)
					if b["propagationPolicy"] != "Foreground" || b["preconditions"].(map[string]any)["uid"] != "uid-monitoring/"+name {
						t.Error("未使用原UID Foreground")
					}
					f.mu.Lock()
					delete(f.objects, "monitoring/"+name)
					f.mu.Unlock()
					mu.Lock()
					removed = true
					mu.Unlock()
					json.NewEncoder(w).Encode(map[string]any{})
					return true
				}
				if r.Method != "GET" {
					return false
				}
				f.mu.Lock()
				cr := f.objects["monitoring/"+name]
				zero := cr != nil && cr["spec"].(map[string]any)["replicas"] == float64(0)
				f.mu.Unlock()
				mu.Lock()
				defer mu.Unlock()
				replicas := 1
				if zero {
					replicas = 0
				}
				st := map[string]any{"metadata": map[string]any{"namespace": "monitoring", "name": "prom-agent-" + name, "uid": "owner-policy-st", "ownerReferences": []any{map[string]any{"kind": "PrometheusAgent", "uid": "uid-monitoring/" + name, "controller": true}}}, "spec": map[string]any{"replicas": replicas, "persistentVolumeClaimRetentionPolicy": map[string]any{"whenScaled": "Retain", "whenDeleted": "Delete"}}}
				pvc := map[string]any{"metadata": map[string]any{"namespace": "monitoring", "name": claim, "uid": "owner-policy-pvc", "ownerReferences": []any{map[string]any{"kind": "StatefulSet", "uid": "owner-policy-st", "controller": true}}}, "spec": map[string]any{"resources": map[string]any{"requests": map[string]any{"storage": "1Gi"}}}, "status": map[string]any{"phase": "Bound"}}
				items := []any{}
				switch r.URL.Path {
				case "/apis/apps/v1/namespaces/monitoring/statefulsets":
					if !removed {
						items = append(items, st)
					}
				case "/api/v1/namespaces/monitoring/pods":
					if !removed && !zero {
						items = append(items, map[string]any{"metadata": map[string]any{"namespace": "monitoring", "name": "prom-agent-" + name + "-0", "uid": "owner-policy-pod", "ownerReferences": []any{map[string]any{"kind": "StatefulSet", "uid": "owner-policy-st", "controller": true}}}, "spec": map[string]any{"volumes": []any{map[string]any{"name": "wal", "persistentVolumeClaim": map[string]any{"claimName": claim}}}}})
					}
				case "/api/v1/namespaces/monitoring/persistentvolumeclaims":
					if !gone {
						items = append(items, pvc)
					}
				case "/apis/apps/v1/namespaces/monitoring/statefulsets/prom-agent-" + name:
					if removed {
						w.WriteHeader(404)
						json.NewEncoder(w).Encode(map[string]any{})
						return true
					}
					json.NewEncoder(w).Encode(st)
					return true
				case "/api/v1/namespaces/monitoring/persistentvolumeclaims/" + claim:
					if gone {
						w.WriteHeader(404)
						json.NewEncoder(w).Encode(map[string]any{})
						return true
					}
					json.NewEncoder(w).Encode(pvc)
					return true
				default:
					if removed && strings.HasSuffix(r.URL.Path, "/pods/prom-agent-"+name+"-0") {
						w.WriteHeader(404)
						json.NewEncoder(w).Encode(map[string]any{})
						return true
					}
					return false
				}
				json.NewEncoder(w).Encode(map[string]any{"items": items})
				return true
			}
			f.mu.Unlock()
			if variant == "scaled-retain-deleted-delete" {
				cfg["desiredState"] = "Suspended"
				clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "agent": cfg}, c, "save", 200)
				preview = clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 2}, c, "", 200)
				op = clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 2, "overwriteDrift": false}, c, "suspend", 202)
			} else {
				current := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
				dp := clusterCall(t, p, "POST", "/instances/"+id+"/deletion-previews", map[string]any{}, c, "", 200)
				op = clusterCall(t, p, "POST", "/instances/"+id+"/deletions", map[string]any{"versionId": current["currentVersionId"], "previewId": dp["previewId"]}, c, "delete", 202)
			}
			if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
				t.Fatal("实例变更未完成")
			}
			result := clusterCall(t, p, "POST", "/operations/"+op["operationId"].(string)+"/refresh", nil, c, "", 200)
			if variant == "scaled-retain-deleted-delete" {
				if result["storageResult"] != "Retained" {
					t.Fatal("保留ST的合法ownerRef阻塞当前保留结果", result)
				}
				f.mu.Lock()
				spec := f.objects["monitoring/"+name]["spec"].(map[string]any)
				if spec["replicas"] != float64(0) || spec["paused"] != false {
					t.Error("挂起意图错误", spec)
				}
				f.mu.Unlock()
				clusterCall(t, p, "POST", "/clusters/"+cluster+"/unregister", map[string]any{}, c, "unregister", 200)
			} else {
				if result["storageResult"] != "Deleting" {
					t.Fatal("误用whenScaled保留删除卷", result)
				}
				mu.Lock()
				gone = true
				mu.Unlock()
				result = clusterCall(t, p, "POST", "/operations/"+op["operationId"].(string)+"/refresh", nil, c, "", 200)
				if result["storageResult"] != "Deleted" {
					t.Fatal("原删除卷未独立核实", result)
				}
			}
		})
	}
}
