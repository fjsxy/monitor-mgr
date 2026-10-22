package platform_test

import (
	ginkgo "github.com/onsi/ginkgo/v2"
)

// U04的后端契约：完整授权集合先过滤/排序再分页，统计独立于过滤。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestWorkbenchQueryFiltersAndStatistics(t ginkgo.GinkgoTInterface) {
	// 接下来U04的后端契约：完整授权集合先过滤/排序再分页，统计独立于过滤。
	ginkgo.By("U04的后端契约：完整授权集合先过滤/排序再分页，统计独立于过滤。")

	p, c, id, f, _ := workbenchSourceFixture(t)
	cl := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)["clusterId"].(string)
	for _, name := range []string{"alpha.one", "alpha-two"} {
		clusterCall(t, p, "POST", "/clusters/"+cl+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig(name)}, c, name, 201)
	}
	clusterCall(t, p, "POST", "/clusters/"+cl+"/instances", map[string]any{"component": "Prometheus Agent", "agent": agentConfig("agent-one", "monitoring")}, c, "agent", 201)
	f.mu.Lock()
	f.inventoryPages = map[string]map[string]any{"": {"items": []any{map[string]any{"apiVersion": "monitoring.fjsxy.github.io/v1alpha1", "kind": "KubeStateMetrics", "metadata": map[string]any{"name": "outside-alpha", "uid": "outside-uid"}, "spec": map[string]any{"targetNamespace": "monitoring"}}}}}
	f.mu.Unlock()
	clusterCall(t, p, "GET", "/clusters/"+cl+"/instances", nil, c, "", 200)
	result := clusterCall(t, p, "GET", "/clusters/"+cl+"/instances?name=ALPHA&type=exporter&component=kube-state-metrics&summary=Unknown&page=1&pageSize=1", nil, c, "", 200)
	instances := result["instances"].([]any)
	if len(instances) != 1 || instances[0].(map[string]any)["name"] != "alpha-two" {
		t.Fatal("后端没有完整AND过滤及显式活动排序", result)
	}
	stats := result["statistics"].(map[string]any)
	page := result["page"].(map[string]any)
	if stats["managed"] != float64(4) || stats["unmanaged"] != float64(1) || page["total"] != float64(3) || page["pages"] != float64(3) {
		t.Fatal("统计随筛选/分页丢失完整集合", result)
	}
	second := clusterCall(t, p, "GET", "/clusters/"+cl+"/instances?name=ALPHA&type=exporter&component=kube-state-metrics&summary=Unknown&page=2&pageSize=1", nil, c, "", 200)
	if second["instances"].([]any)[0].(map[string]any)["name"] != "alpha.one" {
		t.Fatal("分页第二页或排序错误", second)
	}
	for _, query := range []string{"name=%25", "name=_", "name=does-not-exist", "type=collector&component=kube-state-metrics"} {
		zero := clusterCall(t, p, "GET", "/clusters/"+cl+"/instances?"+query+"&page=1", nil, c, "", 200)
		if len(zero["instances"].([]any)) != 0 || zero["statistics"].(map[string]any)["managed"] != float64(4) {
			t.Fatal("名称不是字面子串或零结果污染统计", zero)
		}
	}
	all := clusterCall(t, p, "GET", "/clusters/"+cl+"/instances?page=1&pageSize=100", nil, c, "", 200)["instances"].([]any)
	if len(all) != 5 || all[4].(map[string]any)["readOnly"] != true {
		t.Fatal("未纳管未始终置底")
	}
	clusterCall(t, p, "GET", "/clusters/"+cl+"/instances?page=0&pageSize=100", nil, c, "", 422)
	clusterCall(t, p, "GET", "/clusters/"+cl+"/instances?page=1&pageSize=101", nil, c, "", 422)
}
