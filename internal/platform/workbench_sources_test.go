package platform_test

import (
	"context"
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/platform"
)

// workbenchSourceFixture 仅控制外部API与时钟，实例/版本/授权始终经真实HTTP和Kitex建立。
func workbenchSourceFixture(t *testing.T) (*platform.Runtime, *http.Cookie, string, *ksmFixture, *atomic.Int64) {
	t.Helper()
	f, target := ksmTarget(t)
	stamp := &atomic.Int64{}
	stamp.Store(time.Date(2026, 10, 19, 2, 0, 0, 0, time.UTC).UnixNano())
	cfg := platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", Now: func() time.Time { return time.Unix(0, stamp.Load()) }}
	p, e := platform.Start(context.Background(), cfg)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { p.Close(context.Background()) })
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	owner := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"].(string)
	op := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "source", "ownerId": owner, "context": "explicit", "kubeconfig": target}, c, "register", 202)
	cl := awaitClusterOperation(t, p, c, op["operationId"].(string))["clusterId"].(string)
	id := clusterCall(t, p, "POST", "/clusters/"+cl+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("source")}, c, "create", 201)["id"].(string)
	published := lifecyclePublish(t, p, c, id)
	awaitLifecycleState(t, p, c, published["operationId"].(string), "SUCCEEDED")
	f.mu.Lock()
	f.cr["metadata"].(map[string]any)["generation"] = float64(1)
	f.cr["status"] = map[string]any{"observedGeneration": 1, "phase": "Ready", "deploymentMode": "Deployment", "desiredPods": 1, "availablePods": 1, "conditions": []any{map[string]any{"type": "Ready", "status": "True", "observedGeneration": 1, "reason": "Ready"}}}
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, c, "", 200)
	return p, c, id, f, stamp
}
func workbenchSource(t *testing.T, value map[string]any, name string) map[string]any {
	t.Helper()
	sources, _ := value["sources"].([]any)
	for _, raw := range sources {
		v := raw.(map[string]any)
		if v["source"] == name {
			return v
		}
	}
	t.Fatalf("缺少独立来源 %s: %v", name, value)
	return nil
}

func TestR01WorkbenchSourceFreshness(t *testing.T) {
	p, c, id, _, clock := workbenchSourceFixture(t)
	start := clock.Load()
	for _, seconds := range []int64{29, 30, 31} {
		clock.Store(start + seconds*int64(time.Second))
		instance := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
		source := workbenchSource(t, instance, "CR")
		runtime := instance["runtime"].(map[string]any)
		if source["runtimeSnapshot"].(map[string]any)["summary"] != "Available" {
			t.Fatal("最后成功快照被当前过期覆盖")
		}
		if seconds <= 30 {
			if runtime["summary"] != "Available" || source["historical"] != false || source["stale"] != false {
				t.Fatal("30秒及以内不能过期", instance)
			}
		} else {
			if runtime["summary"] != "Unknown" || runtime["availablePods"] != nil || source["historical"] != true || source["stale"] != true {
				t.Fatal("仅超过30秒当前未知并保留历史", instance)
			}
		}
	}
}
func TestR01WorkbenchEarlyError(t *testing.T) {
	p, c, id, f, clock := workbenchSourceFixture(t)
	original := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
	before := workbenchSource(t, original, "CR")
	clock.Add(int64(time.Second))
	f.mu.Lock()
	f.crReadFailure = true
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, c, "", 200)
	instance := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
	source := workbenchSource(t, instance, "CR")
	runtime := instance["runtime"].(map[string]any)
	if runtime["summary"] != "Unknown" || runtime["availablePods"] != nil || source["historical"] != true || source["stale"] != false || source["error"] == "" || source["lastSuccessAt"] != before["lastSuccessAt"] || source["lastAttemptAt"] == source["lastSuccessAt"] || source["runtimeSnapshot"].(map[string]any)["summary"] != "Available" {
		t.Fatal("立即失败不能丢失历史或等30秒才未知", instance)
	}
}
