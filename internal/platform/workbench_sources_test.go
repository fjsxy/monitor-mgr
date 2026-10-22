package platform_test

import (
	"context"
	"net/http"
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

// REG19-SOURCE-001：注销后的旧在途读取不能继续推进原来源。

// REG19-SOURCE-001：旧凭据读取失败不得覆盖新修订已经提交的成功观察。

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestR03WorkbenchIncompleteStatus(t ginkgo.GinkgoTInterface) {
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
}

// R07.pod的来源隔离回归：CR成功不能续期读取失败的Pod来源。

// REG19-SOURCE-001：后台集群身份预读失败也必须保留读取开始时的凭据范围。
