package platform_test

import (
	"net/http"
	"sync"
	"sync/atomic"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/platform"
	"github.com/fjsxy/monitor-mgr/internal/teststeps"
	ginkgo "github.com/onsi/ginkgo/v2"
)

// deletionConfirmation 通过公开HTTP获取已确认范围，不能用当前version冒充删除预览。
func deletionConfirmation(t ginkgo.GinkgoTInterface, p *platform.Runtime, c *http.Cookie, id string) map[string]any {
	t.Helper()
	preview := clusterCall(t, p, "POST", "/instances/"+id+"/deletion-previews", map[string]any{}, c, "", 200)
	return map[string]any{"versionId": preview["versionId"], "previewId": preview["previewId"]}
}

// REG17-DELETE-003：状态自然变化可继续，目标或原UID影响范围变化必须重新预览。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestDeletionPreviewBindsTargetAndImpact(t ginkgo.GinkgoTInterface) {
	// 接下来REG17-DELETE-003：状态自然变化可继续，目标或原UID影响范围变化必须重新预览。
	ginkgo.By("REG17-DELETE-003：状态自然变化可继续，目标或原UID影响范围变化必须重新预览。")

	for _, variant := range []string{"status-only", "before-acceptance-target", "before-dispatch-target", "before-dispatch-impact", "same-range-rollout"} {
		teststeps.Step(variant, func() {
			var armed atomic.Bool
			entered, release := make(chan struct{}), make(chan struct{})
			var once, released sync.Once
			defer released.Do(func() { close(release) })
			p, c, id, f := lifecycleFixture(t, func(stage, id string) error {
				if armed.Load() && stage == "beforeDispatch" {
					once.Do(func() { close(entered) })
					<-release
				}
				return nil
			})
			pub := lifecyclePublish(t, p, c, id)
			awaitLifecycleState(t, p, c, pub["operationId"].(string), "SUCCEEDED")
			f.mu.Lock()
			f.owned = deletionGraph()
			f.mu.Unlock()
			body := deletionConfirmation(t, p, c, id)
			clusterCall(t, p, "POST", "/instances/"+id+"/deletions", map[string]any{"versionId": pub["versionId"]}, c, "legacy-delete", 422)
			if variant == "before-acceptance-target" {
				f.mu.Lock()
				f.cr["spec"].(map[string]any)["targetNamespace"] = "moved"
				f.mu.Unlock()
				clusterCall(t, p, "POST", "/instances/"+id+"/deletions", body, c, "delete", 409)
			} else {
				armed.Store(true)
				op := clusterCall(t, p, "POST", "/instances/"+id+"/deletions", body, c, "delete", 202)
				select {
				case <-entered:
				case <-time.After(3 * time.Second):
					t.Fatal("删除未进入持久派发屏障")
				}
				f.mu.Lock()
				switch variant {
				case "before-dispatch-target":
					f.cr["spec"].(map[string]any)["targetNamespace"] = "moved"
				case "before-dispatch-impact":
					f.owned["/api/v1/namespaces/moved/pods"] = []any{map[string]any{"metadata": map[string]any{"name": "moved-pod", "namespace": "moved", "uid": "moved-pod-uid", "ownerReferences": []any{map[string]any{"uid": "created-uid", "controller": true}}}}}
				case "same-range-rollout":
					f.owned["/api/v1/namespaces/monitoring/pods"] = []any{map[string]any{"metadata": map[string]any{"name": "rolling", "namespace": "monitoring", "uid": "rolling-new-uid", "ownerReferences": []any{map[string]any{"uid": "rs-uid", "controller": true}}}}}
				}
				f.cr["metadata"].(map[string]any)["resourceVersion"] = "rv-status-new"
				f.cr["status"] = map[string]any{"observedGeneration": 99}
				f.mu.Unlock()
				released.Do(func() { close(release) })
				state := "SUCCEEDED"
				if variant == "before-dispatch-target" || variant == "before-dispatch-impact" {
					state = "FAILED"
				}
				awaitLifecycleState(t, p, c, op["operationId"].(string), state)
			}
			f.mu.Lock()
			deletes := f.deleteRequests
			f.mu.Unlock()
			want := 1
			if variant == "before-acceptance-target" || variant == "before-dispatch-target" || variant == "before-dispatch-impact" {
				want = 0
			}
			if deletes != want {
				t.Fatalf("目标/影响预览边界失效：DELETE=%d，期望%d", deletes, want)
			}
		})
	}
}

// 旧v6未发删除升级后仍受保护，显式再确认只能沿原operation/UID/version建立新attempt。
