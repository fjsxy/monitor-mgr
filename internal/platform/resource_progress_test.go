package platform_test

import (
	"context"
	"encoding/json"
	"net/http"
	"reflect"
	"strings"
	"sync/atomic"

	"github.com/fjsxy/monitor-mgr/internal/platform"
	ginkgo "github.com/onsi/ginkgo/v2"
)

// I27-R01：首次确定失败后允许更名，但旧版本不能把原目标 UID 绑定到新身份实例。
func checkResourceRetryIdentity(t ginkgo.GinkgoTInterface) {
	for _, variant := range []string{"ksm-name", "ksm-namespace", "agent-name", "agent-namespace"} {
		ginkgo.By("I27-R01." + variant + "：确定失败后变更身份，旧重试零写且新明确发布可成功")
		name, namespace := "lifecycle", "monitoring"
		if strings.HasSuffix(variant, "-name") {
			name = "renamed-after-failure"
		} else {
			namespace = "monitoring-two"
		}
		var p *platform.Runtime
		var c *http.Cookie
		var id, field string
		var cfg map[string]any
		var restore func()
		var writes func() int
		var verifyTarget func(string)
		if strings.HasPrefix(variant, "ksm-") {
			var f *ksmFixture
			p, c, id, f = lifecycleFixture(t, nil)
			f.mu.Lock()
			f.rejectWrite = 422
			f.mu.Unlock()
			field, cfg = "ksm", ksmConfig(name)
			cfg["targetNamespace"] = namespace
			restore = func() { f.mu.Lock(); f.rejectWrite = 0; f.mu.Unlock() }
			writes = func() int { f.mu.Lock(); defer f.mu.Unlock(); return f.writeRequests + f.smRequests }
			verifyTarget = func(uid string) {
				f.mu.Lock()
				defer f.mu.Unlock()
				if f.cr == nil || f.sm == nil || f.cr["metadata"].(map[string]any)["name"] != name || f.cr["metadata"].(map[string]any)["uid"] != uid || f.cr["spec"].(map[string]any)["targetNamespace"] != namespace || f.sm["metadata"].(map[string]any)["namespace"] != namespace {
					t.Error("新 KSM 身份与目标组件/配套资源不一致")
				}
			}
		} else {
			var cluster string
			var f *agentFixture
			p, c, cluster, _, f = agentSafetyFixture(t, nil)
			var reject atomic.Bool
			var requests atomic.Int64
			reject.Store(true)
			f.mu.Lock()
			f.hook = func(w http.ResponseWriter, r *http.Request) bool {
				if r.Method == "POST" && strings.HasSuffix(r.URL.Path, "/prometheusagents") {
					requests.Add(1)
					if reject.Load() {
						w.WriteHeader(422)
						w.Write([]byte(`{}`))
						return true
					}
				}
				if r.URL.Path == "/api/v1/namespaces/monitoring-two" || r.URL.Path == "/api/v1/namespaces/monitoring-two/serviceaccounts/prometheus-agent" {
					name := "monitoring-two"
					if strings.HasSuffix(r.URL.Path, "/prometheus-agent") {
						name = "prometheus-agent"
					}
					json.NewEncoder(w).Encode(map[string]any{"metadata": map[string]any{"name": name, "uid": "new-namespace-fixture"}})
					return true
				}
				return false
			}
			f.mu.Unlock()
			instance := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": agentConfig("lifecycle", "monitoring")}, c, "create-agent", 201)
			id = instance["id"].(string)
			field, cfg = "agent", agentConfig(name, namespace)
			restore = func() { reject.Store(false) }
			writes = func() int { return int(requests.Load()) }
			verifyTarget = func(uid string) {
				f.mu.Lock()
				defer f.mu.Unlock()
				cr := f.objects[namespace+"/"+name]
				if len(f.objects) != 1 || cr == nil || cr["metadata"].(map[string]any)["uid"] != uid {
					t.Error("新 Agent namespaced identity 与目标不一致")
				}
			}
		}
		preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "initial-preview", 200)
		op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1}, c, "rejected", 202)
		oid := op["operationId"].(string)
		awaitLifecycleState(t, p, c, oid, "FAILED")
		versionPath := "/instances/" + id + "/versions/" + op["versionId"].(string)
		frozen := clusterCall(t, p, "GET", versionPath, nil, c, "", 200)
		draft := clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, field: cfg}, c, "rename-rejected", 200)
		restore()
		before := writes()
		rejected := clusterCall(t, p, "POST", "/operations/"+oid+"/retry", lifecycleRevision(t, p, c, oid), c, "stale-identity", 409)
		instance := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
		attempts := clusterCall(t, p, "GET", "/operations/"+oid+"/attempts", nil, c, "", 200)["attempts"].([]any)
		if rejected["code"] != "CONFLICT" || len(attempts) != 1 || instance["name"] != name || instance["targetNamespace"] != namespace || instance["uid"] != "" || writes() != before || !reflect.DeepEqual(draft, clusterCall(t, p, "GET", "/instances/"+id+"/draft", nil, c, "", 200)) || !reflect.DeepEqual(frozen, clusterCall(t, p, "GET", versionPath, nil, c, "", 200)) {
			t.Fatal("旧重试写入目标或改写新草稿、旧版本、实例身份", instance)
		}
		// 关键：拒绝的是旧冻结身份，不锁死合法新草稿；新的明确发布固定新身份并独立保存 UID。
		preview = clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 2}, c, "new-preview", 200)
		fresh := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 2}, c, "new-identity", 202)
		awaitLifecycleState(t, p, c, fresh["operationId"].(string), "SUCCEEDED")
		instance = clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
		if fresh["versionId"] == op["versionId"] || instance["name"] != name || instance["targetNamespace"] != namespace || instance["uid"] == "" || !reflect.DeepEqual(frozen, clusterCall(t, p, "GET", versionPath, nil, c, "", 200)) {
			t.Fatal("新发布错绑身份或改写旧版本", instance)
		}
		verifyTarget(instance["uid"].(string))
	}
}

// resourceProgress 经公开 API 核对资源读模型，不读取业务表替代用户可见结果。
func resourceProgress(t ginkgo.GinkgoTInterface, p *platform.Runtime, c *http.Cookie, oid string) map[string]any {
	t.Helper()
	o := clusterCall(t, p, "GET", "/operations/"+oid, nil, c, "", 200)
	resources, ok := o["resources"].([]any)
	if !ok || len(resources) < 1 || len(resources) > 2 {
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
	if !ok || len(frozen) != 2 || !strings.Contains(frozen[0].(map[string]any)["yaml"].(string), "kind: KubeStateMetrics") {
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
			wantState = "WAITING_CONFIRMATION"
		}
		awaitLifecycleState(t, p, c, oid, wantState)
		progress := resourceProgress(t, p, c, oid)
		wantUID := "created-uid"
		if variant == "first-create" {
			wantUID = ""
		}
		wantResourceState := wantState
		if variant == "known-uid" {
			wantResourceState = "SUCCEEDED"
		}
		if progress["state"] != wantResourceState || progress["confirmedUid"] != wantUID || progress["possiblySent"] != true {
			t.Fatal("重启丢失资源结果", progress)
		}
		versions := clusterCall(t, p, "GET", "/instances/"+id+"/versions/"+accepted["versionId"].(string), nil, c, "", 200)["resources"].([]any)
		if len(versions) != 2 {
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
