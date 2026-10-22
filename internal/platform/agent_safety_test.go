package platform_test

import (
	"context"
	"net/http"
	"sync"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/platform"
	ginkgo "github.com/onsi/ginkgo/v2"
)

// 安全回归使用同一真实HTTP/Kitex/SQLite边界，派发屏障不冒充网络超时。
func agentSafetyFixture(t ginkgo.GinkgoTInterface, boundary func(string, string) error) (*platform.Runtime, *http.Cookie, string, string, *agentFixture) {
	t.Helper()
	f, target := agentTarget(t)
	dir := t.TempDir()
	p, e := platform.Start(context.Background(), platform.Config{DataDir: dir, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", OperationBoundary: boundary})
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { p.Close(context.Background()) })
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	owner := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"].(string)
	op := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "agent-safety", "ownerId": owner, "context": "explicit", "kubeconfig": target}, c, "register", 202)
	cluster := awaitClusterOperation(t, p, c, op["operationId"].(string))["clusterId"].(string)
	return p, c, cluster, dir, f
}

// REG18-FIXED-008：首次受理后保存新容量，成功回读后必须在预览和确认再次拒绝。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestAgentFixedCapacityRevalidated(t ginkgo.GinkgoTInterface) {
	// 接下来REG18-FIXED-008：首次受理后保存新容量，成功回读后必须在预览和确认再次拒绝。
	ginkgo.By("REG18-FIXED-008：首次受理后保存新容量，成功回读后必须在预览和确认再次拒绝。")

	entered, release := make(chan struct{}), make(chan struct{})
	var once, released sync.Once
	p, c, cluster, _, f := agentSafetyFixture(t, func(stage, id string) error {
		if stage == "beforeDispatch" {
			once.Do(func() { close(entered) })
			<-release
		}
		return nil
	})
	defer released.Do(func() { close(release) })
	cfg := agentConfig("fixed-race", "monitoring")
	i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": cfg}, c, "create", 201)
	id := i["id"].(string)
	preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "preview-one", 200)
	op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "publish-one", 202)
	select {
	case <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("未进入首次派发前屏障")
	}
	cfg["storage"].(map[string]any)["capacity"] = "2Gi"
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "agent": cfg}, c, "save-in-flight", 200)
	stale := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 2}, c, "preview-before-first-created", 200)
	released.Do(func() { close(release) })
	if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("首次1Gi发布失败")
	}
	clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 2}, c, "preview-after-first-created", 409)
	clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": stale["previewId"], "draftRevision": 2, "overwriteDrift": false}, c, "publish-stale-capacity", 409)
	f.mu.Lock()
	defer f.mu.Unlock()
	spec := f.objects["monitoring/fixed-race"]["spec"].(map[string]any)
	capacity := spec["storage"].(map[string]any)["volumeClaimTemplate"].(map[string]any)["spec"].(map[string]any)["resources"].(map[string]any)["requests"].(map[string]any)["storage"]
	if capacity != "1Gi" {
		t.Fatal("已创建容量被在途草稿改写")
	}
}

// 可能已发但尚无UID时也锁定版本容量，不能等到首次成功回读后才建立约束。

// REG18-HEADERS-009：合法Agent remoteWrite.headers任意值必须投影脱敏，原值仍参与漂移指纹。
