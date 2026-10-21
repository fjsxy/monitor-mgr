package platform_test

import (
	"context"
	"net/http"
	"reflect"
	"sync/atomic"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/platform"
	ginkgo "github.com/onsi/ginkgo/v2"
)

// workbenchSourceFixture 仅控制外部API与时钟，实例/版本/授权始终经真实HTTP和Kitex建立。
func workbenchSourceFixture(t ginkgo.GinkgoTInterface, configure ...func(*platform.Config)) (*platform.Runtime, *http.Cookie, string, *ksmFixture, *atomic.Int64) {
	t.Helper()
	f, target := ksmTarget(t)
	stamp := &atomic.Int64{}
	stamp.Store(time.Date(2026, 10, 19, 2, 0, 0, 0, time.UTC).UnixNano())
	cfg := platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", Now: func() time.Time { return time.Unix(0, stamp.Load()) }}
	for _, apply := range configure {
		apply(&cfg)
	}
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
func workbenchSource(t ginkgo.GinkgoTInterface, value map[string]any, name string) map[string]any {
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

var _ = ginkgo.It("TestR01WorkbenchSourceFreshness", func() {
	t := ginkgo.GinkgoT()
	// 接下来推进观察时钟，验证工作台按来源计算新鲜度。
	ginkgo.By("推进观察时钟，验证工作台按来源计算新鲜度")

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
})
var _ = ginkgo.It("TestR01WorkbenchEarlyError", func() {
	t := ginkgo.GinkgoT()
	// 接下来在首次成功观察前注入错误，验证错误状态的展示。
	ginkgo.By("在首次成功观察前注入错误，验证错误状态的展示")

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
})

// REG19-SOURCE-001：注销后的旧在途读取不能继续推进原来源。
var _ = ginkgo.It("TestWorkbenchLateObservationAfterUnregister", func() {
	t := ginkgo.GinkgoT()
	// 接下来REG19-SOURCE-001：注销后的旧在途读取不能继续推进原来源。
	ginkgo.By("REG19-SOURCE-001：注销后的旧在途读取不能继续推进原来源。")

	p, c, id, f, clock := workbenchSourceFixture(t)
	original := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
	before := workbenchSource(t, original, "CR")
	gate := make(chan struct{})
	entered := make(chan struct{})
	f.mu.Lock()
	f.observationGate = gate
	f.observationEntered = entered
	f.mu.Unlock()
	done := make(chan struct{})
	go func() {
		defer ginkgo.GinkgoRecover()
		defer close(done)
		req, _ := http.NewRequest("POST", p.URL()+"/api/v1/instances/"+id+"/refresh", nil)
		req.Header.Set("Origin", p.URL())
		req.AddCookie(c)
		if resp, e := http.DefaultClient.Do(req); e == nil {
			resp.Body.Close()
		}
	}()
	defer func() {
		select {
		case <-gate:
		default:
			close(gate)
		}
		select {
		case <-done:
		case <-time.After(time.Second):
		}
	}()
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("旧读取没有到达目标API")
	}
	clock.Add(int64(time.Second))
	clusterCall(t, p, "POST", "/clusters/"+original["clusterId"].(string)+"/unregister", map[string]any{}, c, "unregister", 200)
	close(gate)
	<-done
	current := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
	after := workbenchSource(t, current, "CR")
	if after["lastAttemptAt"] != before["lastAttemptAt"] || after["lastSuccessAt"] != before["lastSuccessAt"] || after["historical"] != true || current["runtime"].(map[string]any)["summary"] != "Unknown" {
		t.Fatal("注销后迟到读取改变来源", after)
	}
})

// REG19-SOURCE-001：旧凭据读取失败不得覆盖新修订已经提交的成功观察。
var _ = ginkgo.It("TestWorkbenchLateObservationAfterCredentialSwitch", func() {
	t := ginkgo.GinkgoT()
	// 接下来REG19-SOURCE-001：旧凭据读取失败不得覆盖新修订已经提交的成功观察。
	ginkgo.By("REG19-SOURCE-001：旧凭据读取失败不得覆盖新修订已经提交的成功观察。")

	p, c, id, f, clock := workbenchSourceFixture(t)
	instance := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
	gate := make(chan struct{})
	entered := make(chan struct{})
	f.mu.Lock()
	f.observationGate = gate
	f.observationEntered = entered
	f.mu.Unlock()
	done := make(chan struct{})
	go func() {
		defer ginkgo.GinkgoRecover()
		defer close(done)
		req, _ := http.NewRequest("POST", p.URL()+"/api/v1/instances/"+id+"/refresh", nil)
		req.Header.Set("Origin", p.URL())
		req.AddCookie(c)
		if resp, e := http.DefaultClient.Do(req); e == nil {
			resp.Body.Close()
		}
	}()
	defer func() {
		select {
		case <-gate:
		default:
			close(gate)
		}
		select {
		case <-done:
		case <-time.After(time.Second):
		}
	}()
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("旧读取没有到达目标API")
	}
	updated := clusterCall(t, p, "POST", "/clusters/"+instance["clusterId"].(string)+"/credentials", map[string]any{"context": "explicit", "kubeconfig": f.targetConfiguration}, c, "rotate", 202)
	if awaitClusterOperation(t, p, c, updated["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("凭据切换失败")
	}
	f.mu.Lock()
	f.observationGate = nil
	f.mu.Unlock()
	clock.Add(int64(time.Second))
	clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, c, "", 200)
	before := workbenchSource(t, clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200), "CR")
	clock.Add(int64(time.Second))
	f.mu.Lock()
	f.crReadFailure = true
	f.mu.Unlock()
	close(gate)
	<-done
	current := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
	after := workbenchSource(t, current, "CR")
	if after["lastAttemptAt"] != before["lastAttemptAt"] || after["lastSuccessAt"] != before["lastSuccessAt"] || after["error"] != "" || current["runtime"].(map[string]any)["summary"] != "Available" {
		t.Fatal("旧修订失败覆盖了新成功", after)
	}
})

var _ = ginkgo.It("TestR03WorkbenchMissingStatus", func() {
	t := ginkgo.GinkgoT()
	// 接下来构造缺失 status 的资源，核验运行摘要保持未知。
	ginkgo.By("构造缺失 status 的资源，核验运行摘要保持未知")

	p, c, id, f, _ := workbenchSourceFixture(t)
	f.mu.Lock()
	delete(f.cr, "status")
	f.mu.Unlock()
	runtime := clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, c, "", 200)
	if runtime["summary"] != "Unknown" || runtime["availablePods"] != nil || runtime["desiredPods"] != nil {
		t.Fatal("无status伪造0/0", runtime)
	}
})
var _ = ginkgo.It("TestR03WorkbenchIncompleteStatus", func() {
	t := ginkgo.GinkgoT()
	// 接下来构造不完整 status，防止工作台误报运行正常。
	ginkgo.By("构造不完整 status，防止工作台误报运行正常")

	p, c, id, f, _ := workbenchSourceFixture(t)
	for _, status := range []map[string]any{
		{"phase": "Ready", "conditions": []any{map[string]any{"type": "Ready", "status": "True"}}},
		{"phase": "Ready", "observedGeneration": 1, "desiredPods": 1},
		{"phase": "Ready", "observedGeneration": 1, "desiredPods": 1, "conditions": []any{map[string]any{"type": "Ready", "status": "True", "observedGeneration": 0}}},
		{"phase": "Ready", "observedGeneration": 0, "desiredPods": 1, "conditions": []any{map[string]any{"type": "Ready", "status": "True", "observedGeneration": 0}}},
	} {
		f.mu.Lock()
		f.cr["status"] = status
		f.mu.Unlock()
		runtime := clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, c, "", 200)
		if runtime["summary"] != "Unknown" || runtime["availablePods"] != nil || runtime["desiredPods"] != nil {
			t.Fatal("不适用状态伪造运行", runtime)
		}
	}
})

// R07.pod的来源隔离回归：CR成功不能续期读取失败的Pod来源。
var _ = ginkgo.It("TestWorkbenchPodSourceIndependent", func() {
	t := ginkgo.GinkgoT()
	// 接下来R07.pod的来源隔离回归：CR成功不能续期读取失败的Pod来源。
	ginkgo.By("R07.pod的来源隔离回归：CR成功不能续期读取失败的Pod来源。")

	p, c, id, f, clock := workbenchSourceFixture(t)
	original := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
	pod := workbenchSource(t, original, "Pod")
	clock.Add(int64(time.Second))
	f.mu.Lock()
	f.readFailure = true
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, c, "", 200)
	current := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
	cr := workbenchSource(t, current, "CR")
	failed := workbenchSource(t, current, "Pod")
	if cr["lastSuccessAt"] == pod["lastSuccessAt"] || cr["error"] != "" || failed["lastSuccessAt"] != pod["lastSuccessAt"] || failed["error"] == "" || failed["historical"] != true || current["runtime"].(map[string]any)["summary"] != "Available" {
		t.Fatal("CR成功污染Pod独立失败来源", current)
	}
})

// REG19-SOURCE-001：后台集群身份预读失败也必须保留读取开始时的凭据范围。
var _ = ginkgo.It("TestWorkbenchLateObservationIdentityRead", func() {
	t := ginkgo.GinkgoT()
	// 接下来REG19-SOURCE-001：后台集群身份预读失败也必须保留读取开始时的凭据范围。
	ginkgo.By("REG19-SOURCE-001：后台集群身份预读失败也必须保留读取开始时的凭据范围。")

	ticks := make(chan time.Time, 1)
	p, c, id, f, clock := workbenchSourceFixture(t, func(cfg *platform.Config) { cfg.ObservationTicks = ticks })
	instance := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
	f.mu.Lock()
	f.inventoryPages = map[string]map[string]any{"": {"items": []any{map[string]any{"apiVersion": "monitoring.fjsxy.github.io/v1alpha1", "kind": "KubeStateMetrics", "metadata": map[string]any{"name": "outside", "uid": "outside-uid", "generation": 1}, "spec": map[string]any{"targetNamespace": "monitoring"}}}, "metadata": map[string]any{}}}
	f.mu.Unlock()
	var externalID string
	for _, raw := range clusterCall(t, p, "GET", "/clusters/"+instance["clusterId"].(string)+"/instances", nil, c, "", 200)["instances"].([]any) {
		if v := raw.(map[string]any); v["readOnly"] == true {
			externalID = v["id"].(string)
		}
	}
	if externalID == "" {
		t.Fatal("缺少外部发现前置")
	}
	gate, entered := make(chan struct{}), make(chan struct{})
	f.mu.Lock()
	f.identityGate = gate
	f.identityEntered = entered
	f.mu.Unlock()
	defer func() {
		select {
		case <-gate:
		default:
			close(gate)
		}
	}()
	clock.Add(int64(10 * time.Second))
	ticks <- time.Unix(0, clock.Load())
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("后台没有读取原集群身份")
	}
	f.mu.Lock()
	f.identityGate = nil
	f.mu.Unlock()
	updated := clusterCall(t, p, "POST", "/clusters/"+instance["clusterId"].(string)+"/credentials", map[string]any{"context": "explicit", "kubeconfig": f.targetConfiguration}, c, "rotate-identity", 202)
	if awaitClusterOperation(t, p, c, updated["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("凭据切换失败")
	}
	clock.Add(int64(time.Second))
	clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, c, "", 200)
	before := workbenchSource(t, clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200), "CR")
	clusterCall(t, p, "GET", "/clusters/"+instance["clusterId"].(string)+"/instances", nil, c, "", 200)
	externalBefore := workbenchSource(t, clusterCall(t, p, "GET", "/instances/"+externalID, nil, c, "", 200), "CR")
	clock.Add(int64(time.Second))
	f.mu.Lock()
	f.identityFailure = true
	f.mu.Unlock()
	close(gate)
	time.Sleep(75 * time.Millisecond)
	after := workbenchSource(t, clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200), "CR")
	if !reflect.DeepEqual(after, before) {
		t.Fatal("旧身份预读错误覆盖新凭据成功", after)
	}
	externalAfter := workbenchSource(t, clusterCall(t, p, "GET", "/instances/"+externalID, nil, c, "", 200), "CR")
	if !reflect.DeepEqual(externalAfter, externalBefore) {
		t.Fatal("旧身份预读错误覆盖新凭据外部发现", externalAfter)
	}
})
