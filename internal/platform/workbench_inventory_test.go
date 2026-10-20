package platform_test

import (
	"github.com/fjsxy/monitor-mgr/internal/platform"
	"testing"
	"time"
)

// R05.page：第二页失败只标未知/历史，不能丢掉上次完整发现的对象或提交第一页为全集。
func TestR05WorkbenchFailedInventoryPage(t *testing.T) {
	p, c, id, f := ksmPublishedFixture(t)
	cl := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)["clusterId"].(string)
	outside := func(name, uid string) map[string]any {
		return map[string]any{"apiVersion": "monitoring.fjsxy.github.io/v1alpha1", "kind": "KubeStateMetrics", "metadata": map[string]any{"name": name, "uid": uid, "generation": 1}, "spec": map[string]any{"targetNamespace": "monitoring", "deployment": map[string]any{"mode": "Deployment"}}, "status": map[string]any{"observedGeneration": 1, "phase": "Ready", "deploymentMode": "Deployment", "desiredPods": 1, "availablePods": 1, "conditions": []any{map[string]any{"type": "Ready", "status": "True", "observedGeneration": 1, "reason": "Ready"}}}}
	}
	f.mu.Lock()
	f.inventoryPages = map[string]map[string]any{"": {"items": []any{outside("outside-one", "outside-one-uid")}, "metadata": map[string]any{"continue": "second"}}, "second": {"items": []any{outside("outside-two", "outside-two-uid")}, "metadata": map[string]any{}}}
	f.mu.Unlock()
	before := clusterCall(t, p, "GET", "/clusters/"+cl+"/instances", nil, c, "", 200)["instances"].([]any)
	if len(before) != 3 {
		t.Fatal("前置完整分页失败")
	}
	f.mu.Lock()
	f.inventoryFailure = "second"
	f.mu.Unlock()
	after := clusterCall(t, p, "GET", "/clusters/"+cl+"/instances", nil, c, "", 200)["instances"].([]any)
	if len(after) != 3 {
		t.Fatal("部分列表被当成对象删除")
	}
	for _, raw := range after {
		instance := raw.(map[string]any)
		if instance["readOnly"] != true {
			continue
		}
		source := workbenchSource(t, instance, "CR")
		if instance["runtime"].(map[string]any)["summary"] != "Unknown" || source["error"] == "" || source["historical"] != true || source["runtimeSnapshot"].(map[string]any)["summary"] != "Available" {
			t.Fatal("失败分页没有独立历史/未知", instance)
		}
		if instance["readOnly"] != true {
			t.Fatal("外部对象被认领")
		}
	}
}

// R05.item：同一后台批次中失败对象与成功对象各自记录，不整批覆盖。
func TestR05WorkbenchIndependentItems(t *testing.T) {
	ticks := make(chan time.Time, 1)
	p, c, id, f, clock := workbenchSourceFixture(t, func(cfg *platform.Config) { cfg.ObservationTicks = ticks })
	other, target := ksmTarget(t)
	other.mu.Lock()
	other.clusterUID = "second-cluster-uid"
	other.mu.Unlock()
	owner := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"].(string)
	op := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "second", "ownerId": owner, "context": "explicit", "kubeconfig": target}, c, "register-second", 202)
	cl := awaitClusterOperation(t, p, c, op["operationId"].(string))["clusterId"].(string)
	second := clusterCall(t, p, "POST", "/clusters/"+cl+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("second-source")}, c, "create-second", 201)["id"].(string)
	pub := lifecyclePublish(t, p, c, second)
	awaitLifecycleState(t, p, c, pub["operationId"].(string), "SUCCEEDED")
	original := workbenchSource(t, clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200), "CR")
	f.mu.Lock()
	f.crReadFailure = true
	f.mu.Unlock()
	clock.Add(int64(10 * time.Second))
	ticks <- time.Unix(0, clock.Load())
	expected := time.Unix(0, clock.Load()).UTC().Format(time.RFC3339Nano)
	deadline := time.Now().Add(3 * time.Second)
	for {
		first := workbenchSource(t, clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200), "CR")
		success := workbenchSource(t, clusterCall(t, p, "GET", "/instances/"+second, nil, c, "", 200), "CR")
		if first["error"] != "" && success["lastSuccessAt"] == expected {
			if first["lastSuccessAt"] != original["lastSuccessAt"] || success["error"] != "" || first["lastAttemptAt"] != expected {
				t.Fatal("独立来源时间/错误混淆", first, success)
			}
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("同批次独立来源未更新", first, success)
		}
		time.Sleep(5 * time.Millisecond)
	}
}
