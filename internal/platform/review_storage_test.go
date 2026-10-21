// 独立PVC任务使用公开operation结果观察；故障只在外部TLS API设置，不旁路改库。
package platform_test

import (
	"encoding/json"
	"net/http"
	"reflect"
	"strings"
	"sync"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/teststeps"
	ginkgo "github.com/onsi/ginkgo/v2"
)

var _ = ginkgo.It("TestReviewStorageScope", func() {
	t := ginkgo.GinkgoT()
	// 接下来逐项验证存储删除影响范围与保留策略。
	ginkgo.By("逐项验证存储删除影响范围与保留策略")

	for _, variant := range []string{"identity-failure", "pvc-success"} {
		teststeps.Step(variant, func() {
			f, target := agentTarget(t)
			p, c, owner := clusterPlatform(t)
			registration := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "storage-scope", "ownerId": owner, "context": "explicit", "kubeconfig": target}, c, "register", 202)
			cluster := awaitClusterOperation(t, p, c, registration["operationId"].(string))["clusterId"].(string)
			name := "review-storage"
			claim := "prom-agent-" + name + "-db-prom-agent-" + name + "-0"
			config := agentConfig(name, "monitoring")
			config["storage"].(map[string]any)["whenDeleted"] = "Delete"
			instance := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": config}, c, "create", 201)
			id := instance["id"].(string)
			preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "", 200)
			publication := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "publish", 202)
			awaitLifecycleState(t, p, c, publication["operationId"].(string), "SUCCEEDED")
			var mu sync.Mutex
			removed, gone, armed := false, false, false
			gate, entered := make(chan struct{}), make(chan struct{}, 1)
			var released sync.Once
			defer released.Do(func() { close(gate) })
			f.mu.Lock()
			f.hook = func(w http.ResponseWriter, r *http.Request) bool {
				if r.Method == "DELETE" && strings.HasSuffix(r.URL.Path, "/prometheusagents/"+name) {
					var body map[string]any
					json.NewDecoder(r.Body).Decode(&body)
					if body["propagationPolicy"] != "Foreground" || body["preconditions"].(map[string]any)["uid"] != "uid-monitoring/"+name {
						t.Error("删除未保留原Agent UID")
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
				mu.Lock()
				oldGate := armed && r.Header.Get("Authorization") == "Bearer fixture-sensitive-token" && ((variant == "identity-failure" && r.URL.Path == "/api/v1/namespaces/kube-system") || (variant == "pvc-success" && strings.HasSuffix(r.URL.Path, "/persistentvolumeclaims/"+claim)))
				if oldGate {
					armed = false
				}
				isRemoved, isGone := removed, gone
				mu.Unlock()
				if oldGate {
					entered <- struct{}{}
					select {
					case <-gate:
					case <-r.Context().Done():
						return true
					}
					if variant == "identity-failure" {
						w.WriteHeader(403)
						json.NewEncoder(w).Encode(map[string]any{})
						return true
					}
				}
				pvc := map[string]any{"metadata": map[string]any{"namespace": "monitoring", "name": claim, "uid": "review-pvc-uid", "ownerReferences": []any{map[string]any{"kind": "StatefulSet", "uid": "review-st-uid", "controller": true}}}, "spec": map[string]any{"resources": map[string]any{"requests": map[string]any{"storage": "1Gi"}}, "accessModes": []any{"ReadWriteOnce"}}, "status": map[string]any{"phase": "Bound"}}
				items := []any{}
				switch r.URL.Path {
				case "/apis/apps/v1/namespaces/monitoring/statefulsets":
					if !isRemoved {
						items = append(items, map[string]any{"metadata": map[string]any{"namespace": "monitoring", "name": "prom-agent-" + name, "uid": "review-st-uid", "ownerReferences": []any{map[string]any{"kind": "PrometheusAgent", "uid": "uid-monitoring/" + name, "controller": true}}}, "spec": map[string]any{"replicas": 1, "persistentVolumeClaimRetentionPolicy": map[string]any{"whenScaled": "Retain", "whenDeleted": "Delete"}}})
					}
				case "/api/v1/namespaces/monitoring/pods":
					if !isRemoved {
						items = append(items, map[string]any{"metadata": map[string]any{"namespace": "monitoring", "name": "prom-agent-" + name + "-0", "uid": "review-pod-uid", "ownerReferences": []any{map[string]any{"kind": "StatefulSet", "uid": "review-st-uid", "controller": true}}}, "spec": map[string]any{"volumes": []any{map[string]any{"name": "wal", "persistentVolumeClaim": map[string]any{"claimName": claim}}}}})
					}
				case "/api/v1/namespaces/monitoring/persistentvolumeclaims":
					if !isGone {
						items = append(items, pvc)
					}
				case "/api/v1/namespaces/monitoring/persistentvolumeclaims/" + claim:
					if isGone {
						w.WriteHeader(404)
						json.NewEncoder(w).Encode(map[string]any{})
						return true
					}
					if isRemoved && !oldGate {
						w.WriteHeader(403)
						json.NewEncoder(w).Encode(map[string]any{})
						return true
					}
					json.NewEncoder(w).Encode(pvc)
					return true
				default:
					if isRemoved && (strings.HasSuffix(r.URL.Path, "/statefulsets/prom-agent-"+name) || strings.HasSuffix(r.URL.Path, "/pods/prom-agent-"+name+"-0")) {
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
			deletion := clusterCall(t, p, "POST", "/instances/"+id+"/deletions", deletionConfirmation(t, p, c, id), c, "delete", 202)
			oid := deletion["operationId"].(string)
			awaitLifecycleState(t, p, c, oid, "SUCCEEDED")
			baseline := clusterCall(t, p, "POST", "/operations/"+oid+"/refresh", nil, c, "", 200)
			if baseline["storageResult"] != "Unknown" {
				t.Fatal("缺少Pending原卷前置", baseline["storageResult"])
			}
			clusterCall(t, p, "POST", "/clusters/"+cluster+"/unregister", map[string]any{}, c, "blocked-archive", 409)
			clusterCall(t, p, "POST", "/clusters/"+cluster+"/transfer", map[string]any{"ownerId": owner}, c, "blocked-transfer", 409)
			mu.Lock()
			armed = true
			mu.Unlock()
			finished := make(chan struct{})
			go func() {
				defer ginkgo.GinkgoRecover()
				clusterCall(t, p, "POST", "/operations/"+oid+"/refresh", nil, c, "", 200)
				close(finished)
			}()
			select {
			case <-entered:
			case <-time.After(2 * time.Second):
				t.Fatal("原存储读取未到TLS屏障")
			}
			// 独立候选操作正常轮换，同operation只读single-flight不阻断它。
			rotation := clusterCall(t, p, "POST", "/clusters/"+cluster+"/credentials", map[string]any{"context": "explicit", "kubeconfig": strings.Replace(target, "fixture-sensitive-token", "review-new-token", 1)}, c, "rotate", 202)
			if awaitClusterOperation(t, p, c, rotation["operationId"].(string))["state"] != "SUCCEEDED" {
				t.Fatal("凭据轮换失败")
			}
			before := clusterCall(t, p, "GET", "/operations/"+oid, nil, c, "", 200)
			released.Do(func() { close(gate) })
			select {
			case <-finished:
			case <-time.After(2 * time.Second):
				t.Fatal("旧存储读取未结束")
			}
			after := clusterCall(t, p, "GET", "/operations/"+oid, nil, c, "", 200)
			if !reflect.DeepEqual(before["storageVolumes"], after["storageVolumes"]) || before["storageResult"] != after["storageResult"] {
				t.Fatal("旧目标结果推进当前task/原卷时间或结果")
			}
			mu.Lock()
			gone = true
			mu.Unlock()
			current := clusterCall(t, p, "POST", "/operations/"+oid+"/refresh", nil, c, "", 200)
			if current["storageResult"] != "Deleted" {
				t.Fatal("当前凭据未能确认原PVC消失", current["storageResult"])
			}
		})
	}
})
