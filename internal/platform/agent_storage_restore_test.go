package platform_test

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/teststeps"
	ginkgo "github.com/onsi/ginkgo/v2"
)

// REG20-STORAGE-005：只经公开入口产生Delete责任，原UID未独立核销前不允许重新挂载。
var _ = ginkgo.It("TestAgentStoragePendingDeleteBlocksRestore", func() {
	t := ginkgo.GinkgoT()
	// 接下来REG20-STORAGE-005：只经公开入口产生Delete责任，原UID未独立核销前不允许重新挂载。
	ginkgo.By("REG20-STORAGE-005：只经公开入口产生Delete责任，原UID未独立核销前不允许重新挂载。")

	for _, action := range []string{"suspend", "shard"} {
		teststeps.Step(action, func() {
			for _, policy := range []string{"Delete", "Retain"} {
				teststeps.Step(policy, func() {
					p, c, cluster, _, f := agentSafetyFixture(t, nil)
					name := "restore-" + action
					cfg := agentConfig(name, "monitoring")
					count, affected := 1, 0
					if action == "shard" {
						count, affected = 2, 1
						cfg["shards"] = 2
						cfg["storage"].(map[string]any)["whenDeleted"] = policy
					} else {
						cfg["storage"].(map[string]any)["whenScaled"] = policy
					}
					i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": cfg}, c, "create", 201)
					id := i["id"].(string)
					publish := func(revision int, key string) map[string]any {
						pr := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": revision}, c, "", 200)
						o := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": pr["previewId"], "draftRevision": revision, "overwriteDrift": false}, c, key, 202)
						o = awaitClusterOperation(t, p, c, o["operationId"].(string))
						if o["state"] != "SUCCEEDED" {
							t.Fatal("前置/恢复发布失败", o)
						}
						return o
					}
					publish(1, "first")
					var mu sync.Mutex
					gone, replacement, writes := false, false, 0
					stName := func(shard int) string {
						v := "prom-agent-" + name
						if shard > 0 {
							v += fmt.Sprintf("-shard-%d", shard)
						}
						return v
					}
					claimName := func(shard int) string { return "prom-agent-" + name + "-db-" + stName(shard) + "-0" }
					originalUID := fmt.Sprintf("original-pvc-%d", affected)
					f.mu.Lock()
					f.hook = func(w http.ResponseWriter, r *http.Request) bool {
						if r.Method == "PUT" && strings.HasSuffix(r.URL.Path, "/prometheusagents/"+name) {
							mu.Lock()
							writes++
							mu.Unlock()
							return false
						}
						if r.Method != "GET" {
							return false
						}
						f.mu.Lock()
						spec := f.objects["monitoring/"+name]["spec"].(map[string]any)
						zero := spec["replicas"] == float64(0)
						currentShards := int(spec["shards"].(float64))
						f.mu.Unlock()
						mu.Lock()
						defer mu.Unlock()
						sts, pods, pvcs := []any{}, []any{}, []any{}
						for shard := 0; shard < count; shard++ {
							stUID := fmt.Sprintf("original-st-%d", shard)
							pvcUID := fmt.Sprintf("original-pvc-%d", shard)
							if replacement && shard == affected {
								pvcUID = "replacement-pvc"
								if action == "shard" {
									stUID = "replacement-st"
								}
							}
							if shard < currentShards {
								scaled, deleted := "Retain", "Retain"
								if action == "suspend" {
									scaled = policy
								} else {
									deleted = policy
								}
								sts = append(sts, map[string]any{"metadata": map[string]any{"namespace": "monitoring", "name": stName(shard), "uid": stUID, "ownerReferences": []any{map[string]any{"kind": "PrometheusAgent", "uid": "uid-monitoring/" + name, "controller": true}}}, "spec": map[string]any{"persistentVolumeClaimRetentionPolicy": map[string]any{"whenScaled": scaled, "whenDeleted": deleted}}})
								if !zero {
									pods = append(pods, map[string]any{"metadata": map[string]any{"namespace": "monitoring", "name": stName(shard) + "-0", "uid": stUID + "-pod", "ownerReferences": []any{map[string]any{"kind": "StatefulSet", "uid": stUID, "controller": true}}}, "spec": map[string]any{"volumes": []any{map[string]any{"name": "wal", "persistentVolumeClaim": map[string]any{"claimName": claimName(shard)}}}}})
								}
							}
							if !gone || shard != affected || replacement {
								pvcs = append(pvcs, map[string]any{"metadata": map[string]any{"namespace": "monitoring", "name": claimName(shard), "uid": pvcUID}, "spec": map[string]any{"resources": map[string]any{"requests": map[string]any{"storage": "1Gi"}}}, "status": map[string]any{"phase": "Bound"}})
							}
						}
						var items []any
						switch r.URL.Path {
						case "/apis/apps/v1/namespaces/monitoring/statefulsets":
							items = sts
						case "/api/v1/namespaces/monitoring/pods":
							items = pods
						case "/api/v1/namespaces/monitoring/persistentvolumeclaims":
							items = pvcs
						default:
							for _, raw := range pvcs {
								v := raw.(map[string]any)
								if r.URL.Path == "/api/v1/namespaces/monitoring/persistentvolumeclaims/"+v["metadata"].(map[string]any)["name"].(string) {
									json.NewEncoder(w).Encode(v)
									return true
								}
							}
							return false
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
					clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "agent": cfg}, c, "reduce", 200)
					reduced := publish(2, "reduce")
					oid := reduced["operationId"].(string)
					// 同操作flight可能已在后台读取；有界等待独立任务终态，不能将首个Pending当失败。
					storageResult := func(want string) map[string]any {
						deadline := time.Now().Add(5 * time.Second)
						var result map[string]any
						for time.Now().Before(deadline) {
							result = clusterCall(t, p, "POST", "/operations/"+oid+"/refresh", nil, c, "", 200)
							if result["storageResult"] == want {
								return result
							}
							time.Sleep(10 * time.Millisecond)
						}
						t.Fatal("独立原卷任务未达到预期结果", want, result)
						return nil
					}
					want := "Deleting"
					if policy == "Retain" {
						want = "Retained"
					}
					storageResult(want)
					if action == "suspend" {
						cfg["desiredState"] = "Running"
					} else {
						cfg["shards"] = 2
					}
					clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 2, "agent": cfg}, c, "restore", 200)
					if policy == "Delete" {
						clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 3}, c, "", 409)
						mu.Lock()
						if writes != 1 {
							t.Error("拒绝恢复仍发生新mutation", writes)
						}
						gone = true
						mu.Unlock()
						storageResult("Deleted")
					}
					publish(3, "restore")
					mu.Lock()
					replacement = policy == "Delete"
					mu.Unlock()
					clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, c, "", 200)
					view := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
					foundNew, foundOld := false, false
					for _, raw := range view["sources"].([]any) {
						source := raw.(map[string]any)
						if source["source"] != "PVC" {
							continue
						}
						if source["error"] != "" {
							t.Fatal("恢复卷身份无法确认", source)
						}
						for _, raw := range storageVolumes(source) {
							v := raw.(map[string]any)
							foundNew = foundNew || v["uid"] == "replacement-pvc"
							foundOld = foundOld || v["uid"] == originalUID
						}
					}
					if !foundOld || policy == "Delete" && !foundNew || policy == "Retain" && foundNew {
						t.Fatal("原责任与恢复卷身份混淆", view)
					}
					result := clusterCall(t, p, "GET", "/operations/"+oid, nil, c, "", 200)
					want = "Deleted"
					if policy == "Retain" {
						want = "Retained"
					}
					if result["storageResult"] != want {
						t.Fatal("新卷反向污染旧责任终态", result)
					}
				})
			}
		})
	}
})
