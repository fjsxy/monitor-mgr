package platform_test

import (
	ginkgo "github.com/onsi/ginkgo/v2"
)

// R05.page：第二页失败只标未知/历史，不能丢掉上次完整发现的对象或提交第一页为全集。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestR05WorkbenchFailedInventoryPage(t ginkgo.GinkgoTInterface) {
	// 接下来R05.page：第二页失败只标未知/历史，不能丢掉上次完整发现的对象或提交第一页为全集。
	ginkgo.By("R05.page：第二页失败只标未知/历史，不能丢掉上次完整发现的对象或提交第一页为全集。")

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
