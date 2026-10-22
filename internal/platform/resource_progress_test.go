package platform_test

import (
	"context"
	"strings"
	"sync/atomic"

	"github.com/fjsxy/monitor-mgr/internal/platform"
	ginkgo "github.com/onsi/ginkgo/v2"
	"net/http"
)

// resourceProgress 经公开 API 核对资源读模型，不读取业务表替代用户可见结果。
func resourceProgress(t ginkgo.GinkgoTInterface, p *platform.Runtime, c *http.Cookie, oid string) map[string]any {
	t.Helper()
	o := clusterCall(t, p, "GET", "/operations/"+oid, nil, c, "", 200)
	resources, ok := o["resources"].([]any)
	if !ok || len(resources) != 1 {
		t.Fatal("操作未展示原冻结资源集合", o)
	}
	return resources[0].(map[string]any)
}

// I27-U01 验证冻结版本、实际发送边界与已确认 UID 由公共接口独立表达。
func checkResourcePublicationProgress(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I27-U01：KSM 版本与操作保留实际冻结资源及独立进度")
	p, c, id, _ := lifecycleFixture(t, nil)
	accepted := lifecyclePublish(t, p, c, id)
	oid := accepted["operationId"].(string)
	awaitLifecycleState(t, p, c, oid, "SUCCEEDED")
	progress := resourceProgress(t, p, c, oid)
	target := progress["target"].(map[string]any)
	if target["key"] != "component" || target["kind"] != "KubeStateMetrics" || target["name"] != "lifecycle" || target["namespace"] != "" || progress["state"] != "SUCCEEDED" || progress["confirmedUid"] != "created-uid" || progress["possiblySent"] != true {
		t.Fatal("资源身份或结果不完整", progress)
	}
	v := clusterCall(t, p, "GET", "/instances/"+id+"/versions/"+accepted["versionId"].(string), nil, c, "", 200)
	frozen, ok := v["resources"].([]any)
	if !ok || len(frozen) != 1 || !strings.Contains(frozen[0].(map[string]any)["yaml"].(string), "kind: KubeStateMetrics") {
		t.Fatal("版本缺少冻结资源文档", v)
	}
}

// I27-U01/U04/U05/U06：真实进程重启保留每份资源的结果，只核验原 UID 和原冻结配置。
func checkResourceProgressSurvivesRestart(t ginkgo.GinkgoTInterface) {
	for _, variant := range []string{"first-create", "known-uid", "uid-replaced", "drift"} {
		ginkgo.By("I27：重启保留逐资源身份与发送边界 · " + variant)
		f, target := ksmTarget(t)
		var drop atomic.Bool
		config := platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", OperationBoundary: func(stage, id string) error {
			if stage == "afterSendMarker" && drop.Load() {
				f.mu.Lock()
				f.dropResponse = true
				f.readFailure = true
				f.mu.Unlock()
			}
			return nil
		}}
		p, e := platform.Start(context.Background(), config)
		if e != nil {
			t.Fatal(e)
		}
		request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
		c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
		owner := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"].(string)
		registration := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "resources", "ownerId": owner, "context": "explicit", "kubeconfig": target}, c, "register", 202)
		cluster := awaitClusterOperation(t, p, c, registration["operationId"].(string))["clusterId"].(string)
		instance := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("recovery")}, c, "create", 201)
		id := instance["id"].(string)
		revision := 1
		if variant != "first-create" {
			first := lifecyclePublish(t, p, c, id)
			awaitLifecycleState(t, p, c, first["operationId"].(string), "SUCCEEDED")
			desired := ksmConfig("recovery")
			desired["resources"].(map[string]any)["requestCPU"] = "200m"
			clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": desired}, c, "edit", 200)
			revision = 2
		}
		preview := ksmPreview(t, p, c, id, revision)
		drop.Store(true)
		accepted := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": revision, "overwriteDrift": false}, c, "unknown", 202)
		oid := accepted["operationId"].(string)
		wantState := "RESULT_UNCERTAIN"
		if variant == "first-create" {
			wantState = "IDENTITY_UNCERTAIN"
		}
		awaitLifecycleState(t, p, c, oid, wantState)
		p.Close(context.Background())
		f.mu.Lock()
		f.readFailure = false
		if variant == "uid-replaced" {
			f.cr["metadata"].(map[string]any)["uid"] = "external-replacement"
		}
		if variant == "drift" {
			f.cr["spec"].(map[string]any)["podResources"].(map[string]any)["requests"].(map[string]any)["cpu"] = "900m"
		}
		beforeWrites := f.writes
		f.mu.Unlock()
		p, e = platform.Start(context.Background(), config)
		if e != nil {
			t.Fatal(e)
		}
		clusterCall(t, p, "POST", "/operations/"+oid+"/refresh", nil, c, "", 200)
		if variant == "known-uid" {
			wantState = "SUCCEEDED"
		}
		awaitLifecycleState(t, p, c, oid, wantState)
		progress := resourceProgress(t, p, c, oid)
		wantUID := "created-uid"
		if variant == "first-create" {
			wantUID = ""
		}
		if progress["state"] != wantState || progress["confirmedUid"] != wantUID || progress["possiblySent"] != true {
			t.Fatal("重启丢失资源结果", progress)
		}
		versions := clusterCall(t, p, "GET", "/instances/"+id+"/versions/"+accepted["versionId"].(string), nil, c, "", 200)["resources"].([]any)
		if len(versions) != 1 {
			t.Fatal("重启追加了新的资源", versions)
		}
		f.mu.Lock()
		afterWrites := f.writes
		f.mu.Unlock()
		if afterWrites != beforeWrites {
			t.Fatal("只读恢复重放了写请求", beforeWrites, afterWrites)
		}
		p.Close(context.Background())
	}
}
