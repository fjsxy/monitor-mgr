package platform_test

import (
	"context"
	"errors"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/platform"
	"github.com/fjsxy/monitor-mgr/internal/teststeps"
	ginkgo "github.com/onsi/ginkgo/v2"
)

// lifecycleFixture 经真实 HTTP/Kitex 建立实例，并只在应用持久派发边界提供确定性屏障。
func lifecycleFixture(t ginkgo.GinkgoTInterface, boundary func(string, string) error, clock ...func() time.Time) (*platform.Runtime, *http.Cookie, string, *ksmFixture) {
	t.Helper()
	f, cfg := ksmTarget(t)
	cfgPlatform := platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", OperationBoundary: boundary}
	if len(clock) > 0 {
		cfgPlatform.Now = clock[0]
	}
	p, err := platform.Start(context.Background(), cfgPlatform)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { p.Close(context.Background()) })
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	owner := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"].(string)
	o := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "lifecycle", "ownerId": owner, "context": "explicit", "kubeconfig": cfg}, c, "register", 202)
	op := awaitClusterOperation(t, p, c, o["operationId"].(string))
	if op["state"] != "SUCCEEDED" {
		t.Fatal(op)
	}
	i := clusterCall(t, p, "POST", "/clusters/"+op["clusterId"].(string)+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("lifecycle")}, c, "create", 201)
	return p, c, i["id"].(string), f
}

func lifecyclePublish(t ginkgo.GinkgoTInterface, p *platform.Runtime, c *http.Cookie, id string) map[string]any {
	t.Helper()
	preview := ksmPreview(t, p, c, id, 1)
	return clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "publish", 202)
}

func lifecycleRevision(t ginkgo.GinkgoTInterface, p *platform.Runtime, c *http.Cookie, id string) map[string]any {
	t.Helper()
	o := clusterCall(t, p, "GET", "/operations/"+id, nil, c, "", 200)
	as := clusterCall(t, p, "GET", "/operations/"+id+"/attempts", nil, c, "", 200)["attempts"].([]any)
	for _, raw := range as {
		a := raw.(map[string]any)
		if a["attemptId"] == o["attemptId"] {
			return map[string]any{"expectedOperationRevision": o["revision"], "expectedAttemptId": a["attemptId"], "expectedAttemptRevision": a["revision"]}
		}
	}
	t.Fatal("当前 attempt 不可读")
	return nil
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestP08CancellationCompetesWithPersistentDispatch(t ginkgo.GinkgoTInterface) {
	// 接下来控制取消与派发竞争，验证持久化边界只允许一个结果。
	ginkgo.By("控制取消与派发竞争，验证持久化边界只允许一个结果")

	for _, tc := range []struct {
		name, stage string
		status      int
	}{{"cancel-wins", "beforeDispatch", 200}, {"dispatch-wins", "afterSendMarker", 409}} {
		teststeps.Step(tc.name, func() {
			entered, release := make(chan struct{}), make(chan struct{})
			var once, released sync.Once
			defer released.Do(func() { close(release) })
			p, c, id, f := lifecycleFixture(t, func(stage, id string) error {
				if stage == tc.stage {
					once.Do(func() { close(entered) })
					<-release
				}
				return nil
			})
			o := lifecyclePublish(t, p, c, id)
			select {
			case <-entered:
			case <-time.After(3 * time.Second):
				t.Fatal("未到达应用派发屏障")
			}
			op := o["operationId"].(string)
			body := lifecycleRevision(t, p, c, op)
			cancelled := clusterCall(t, p, "POST", "/operations/"+op+"/cancel", body, c, "cancel", tc.status)
			released.Do(func() { close(release) })
			if tc.status == 200 {
				if cancelled["state"] != "CANCELLED" {
					t.Fatal(cancelled)
				}
				replayed := clusterCall(t, p, "POST", "/operations/"+op+"/cancel", body, c, "cancel", 200)
				current := clusterCall(t, p, "GET", "/operations/"+op, nil, c, "", 200)
				if replayed["state"] != current["state"] || replayed["revision"] != current["revision"] || replayed["attemptId"] != current["attemptId"] {
					t.Fatal(replayed)
				}
				time.Sleep(50 * time.Millisecond)
				f.mu.Lock()
				writes := f.writes
				f.mu.Unlock()
				if writes != 0 {
					t.Fatalf("取消胜出却写入%d次", writes)
				}
			} else if done := awaitClusterOperation(t, p, c, op); done["state"] != "SUCCEEDED" {
				t.Fatal(done)
			}
		})
	}
}

// P07：确定拒绝后，重试固定原版本且保留失败尝试，不能偷换为保存的新草稿。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestP07RetryUsesFrozenVersionAndCurrentRevisions(t ginkgo.GinkgoTInterface) {
	// 接下来P07：确定拒绝后，重试固定原版本且保留失败尝试，不能偷换为保存的新草稿。
	ginkgo.By("P07：确定拒绝后，重试固定原版本且保留失败尝试，不能偷换为保存的新草稿。")

	p, c, id, f := lifecycleFixture(t, nil)
	f.mu.Lock()
	f.rejectWrite = 422
	f.mu.Unlock()
	op := lifecyclePublish(t, p, c, id)
	oid := op["operationId"].(string)
	done := awaitClusterOperation(t, p, c, oid)
	if done["state"] != "FAILED" {
		t.Fatal(done)
	}
	cfg := ksmConfig("lifecycle")
	cfg["resources"].(map[string]any)["requestCPU"] = "900m"
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": cfg}, c, "new-draft", 200)
	body := lifecycleRevision(t, p, c, oid)
	wrong := map[string]any{}
	for k, v := range body {
		wrong[k] = v
	}
	wrong["expectedAttemptId"] = "old-attempt"
	clusterCall(t, p, "POST", "/operations/"+oid+"/retry", wrong, c, "wrong", 409)
	f.mu.Lock()
	f.rejectWrite = 0
	f.mu.Unlock()
	retried := clusterCall(t, p, "POST", "/operations/"+oid+"/retry", body, c, "retry", 202)
	if retried["operationId"] != oid || retried["versionId"] != op["versionId"] || retried["attemptId"] == op["attemptId"] {
		t.Fatal(retried)
	}
	replay := clusterCall(t, p, "POST", "/operations/"+oid+"/retry", body, c, "retry", 202)
	if replay["attemptId"] != retried["attemptId"] {
		t.Fatal("重试幂等未保留尝试")
	}
	if done = awaitClusterOperation(t, p, c, oid); done["state"] != "SUCCEEDED" {
		t.Fatal(done)
	}
	replay = clusterCall(t, p, "POST", "/operations/"+oid+"/retry", body, c, "retry", 202)
	if replay["state"] != done["state"] || replay["revision"] != done["revision"] || replay["attemptId"] != done["attemptId"] {
		t.Fatal("同键重试必须回读当前操作", replay, done)
	}
	attempts := clusterCall(t, p, "GET", "/operations/"+oid+"/attempts", nil, c, "", 200)["attempts"].([]any)
	if len(attempts) != 2 || attempts[0].(map[string]any)["state"] != "FAILED" || attempts[1].(map[string]any)["sequence"] != float64(2) {
		t.Fatal(attempts)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.writes != 1 || f.cr["spec"].(map[string]any)["podResources"].(map[string]any)["requests"].(map[string]any)["cpu"] != "100m" {
		t.Fatal("重试替换了固定来源", f.cr)
	}
}

func awaitLifecycleState(t ginkgo.GinkgoTInterface, p *platform.Runtime, c *http.Cookie, id string, states ...string) map[string]any {
	t.Helper()
	until := time.Now().Add(12 * time.Second)
	for time.Now().Before(until) {
		o := clusterCall(t, p, "GET", "/operations/"+id, nil, c, "", 200)
		for _, state := range states {
			if o["state"] == state {
				return o
			}
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("操作未到达预期状态", states)
	return nil
}

// P10/P11：目标已执行但响应丢失，已知UID只读确认，首次创建绝不按同名补认。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestPublicationUnknownOutcomeNeverReplays(t ginkgo.GinkgoTInterface) {
	// 接下来P10/P11：目标已执行但响应丢失，已知UID只读确认，首次创建绝不按同名补认。
	ginkgo.By("P10/P11：目标已执行但响应丢失，已知UID只读确认，首次创建绝不按同名补认。")

	for _, known := range []bool{false, true} {
		teststeps.Step(map[bool]string{false: "first-create", true: "known-uid"}[known], func() {
			p, c, id, f := lifecycleFixture(t, nil)
			if known {
				op := lifecyclePublish(t, p, c, id)
				if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
					t.Fatal(op)
				}
				cfg := ksmConfig("lifecycle")
				cfg["resources"].(map[string]any)["requestCPU"] = "200m"
				clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": cfg}, c, "edit", 200)
			}
			rev := 1
			if known {
				rev = 2
			}
			preview := ksmPreview(t, p, c, id, rev)
			f.mu.Lock()
			f.dropResponse = true
			f.mu.Unlock()
			o := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": rev, "overwriteDrift": false}, c, "unknown", 202)
			oid := o["operationId"].(string)
			if !known {
				awaitLifecycleState(t, p, c, oid, "IDENTITY_UNCERTAIN")
			} else {
				awaitLifecycleState(t, p, c, oid, "RESULT_UNCERTAIN", "SUCCEEDED")
			}
			clusterCall(t, p, "POST", "/operations/"+oid+"/retry", lifecycleRevision(t, p, c, oid), c, "forbidden-retry", 409)
			clusterCall(t, p, "POST", "/operations/"+oid+"/refresh", nil, c, "refresh", 200)
			if known {
				awaitLifecycleState(t, p, c, oid, "SUCCEEDED")
			} else {
				awaitLifecycleState(t, p, c, oid, "IDENTITY_UNCERTAIN")
			}
			f.mu.Lock()
			writes := f.writes
			f.mu.Unlock()
			expected := 1
			if known {
				expected = 2
			}
			if writes != expected {
				t.Fatal("不确定请求被重放", writes)
			}
		})
	}
}

// T08：退出先撤销就绪和新受理资格，已发送任务仍保留真实结果核验依据。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestT08ShutdownRejectsNewAcceptanceBeforeDrain(t ginkgo.GinkgoTInterface) {
	// 接下来T08：退出先撤销就绪和新受理资格，已发送任务仍保留真实结果核验依据。
	ginkgo.By("T08：退出先撤销就绪和新受理资格，已发送任务仍保留真实结果核验依据。")

	entered, release := make(chan struct{}), make(chan struct{})
	var once, released sync.Once
	defer released.Do(func() { close(release) })
	p, c, id, _ := lifecycleFixture(t, func(stage, id string) error {
		if stage == "afterSendMarker" {
			once.Do(func() { close(entered) })
			<-release
		}
		return nil
	})
	o := lifecyclePublish(t, p, c, id)
	_ = o
	select {
	case <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("未进入已发持久点")
	}
	closed := make(chan struct{})
	go func() {
		defer ginkgo.GinkgoRecover()
		ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
		defer cancel()
		p.Close(ctx)
		close(closed)
	}()
	time.Sleep(20 * time.Millisecond)
	req, _ := http.NewRequest("POST", p.URL()+"/api/v1/instances/"+id+"/editing-activity", nil)
	req.Header.Set("Origin", p.URL())
	req.Header.Set("Idempotency-Key", "after-stop")
	req.AddCookie(c)
	resp, e := http.DefaultClient.Do(req)
	if e != nil {
		t.Fatal(e)
	}
	resp.Body.Close()
	if resp.StatusCode != 503 {
		t.Fatalf("退出排空期间仍受理新变更：%d", resp.StatusCode)
	}
	released.Do(func() { close(release) })
	select {
	case <-closed:
	case <-time.After(3 * time.Second):
		t.Fatal("退出未在排空后保存并关闭")
	}
}

// deletionGraph 用原UID建立DP→RS→Pod控制器链，Pod残留时不再依赖已消失父对象的名称。
func deletionGraph() map[string][]any {
	obj := func(name, uid, parent string) any {
		return map[string]any{"metadata": map[string]any{"name": name, "uid": uid, "namespace": "monitoring", "ownerReferences": []any{map[string]any{"uid": parent, "controller": true}}}}
	}
	return map[string][]any{"/apis/apps/v1/namespaces/monitoring/deployments": {obj("work", "work-uid", "created-uid")}, "/apis/apps/v1/namespaces/monitoring/replicasets": {obj("rs", "rs-uid", "work-uid")}, "/api/v1/namespaces/monitoring/pods": {obj("pod", "pod-uid", "rs-uid")}}
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestP14ForegroundDeletionKeepsUIDGraphProtection(t ginkgo.GinkgoTInterface) {
	// 接下来逐项构造删除残留与身份替换，核验前台删除的 UID 保护。
	ginkgo.By("逐项构造删除残留与身份替换，核验前台删除的 UID 保护")

	for _, variant := range []string{"finalizer", "pod-remains", "read-failure", "delete-response-drop", "complete"} {
		teststeps.Step(variant, func() {
			p, c, id, f := lifecycleFixture(t, nil)
			pub := lifecyclePublish(t, p, c, id)
			if awaitClusterOperation(t, p, c, pub["operationId"].(string))["state"] != "SUCCEEDED" {
				t.Fatal(pub)
			}
			f.mu.Lock()
			f.owned = deletionGraph()
			f.retainCR = variant == "finalizer"
			f.retainPod = variant == "pod-remains"
			f.readFailureOnDelete = variant == "read-failure"
			f.dropResponse = variant == "delete-response-drop"
			if f.retainCR {
				f.cr["metadata"].(map[string]any)["finalizers"] = []any{"fixture/finalizer"}
			}
			f.mu.Unlock()
			o := clusterCall(t, p, "POST", "/instances/"+id+"/deletions", deletionConfirmation(t, p, c, id), c, "delete", 202)
			oid := o["operationId"].(string)
			if variant == "complete" || variant == "delete-response-drop" {
				awaitLifecycleState(t, p, c, oid, "SUCCEEDED")
			} else {
				awaitLifecycleState(t, p, c, oid, "DELETION_VERIFYING", "DELETION_BLOCKED")
				clusterCall(t, p, "POST", "/operations/"+oid+"/retry", lifecycleRevision(t, p, c, oid), c, "retry-delete", 409)
				instance := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
				cluster := instance["clusterId"].(string)
				clusterCall(t, p, "POST", "/clusters/"+cluster+"/unregister", map[string]any{}, c, "unregister", 409)
				f.mu.Lock()
				if variant == "finalizer" && len(f.cr["metadata"].(map[string]any)["finalizers"].([]any)) != 1 {
					t.Fatal("平台移除了finalizer")
				}
				f.cr = nil
				f.owned = nil
				f.readFailure = false
				f.mu.Unlock()
				clusterCall(t, p, "POST", "/operations/"+oid+"/refresh", nil, c, "verify", 200)
				awaitLifecycleState(t, p, c, oid, "SUCCEEDED")
			}
			i := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
			if i["lifecycle"] != "DELETED" {
				t.Fatal(i)
			}
			runtime := clusterCall(t, p, "GET", "/instances/"+id+"/runtime", nil, c, "", 200)
			if runtime["summary"] != "Deleted" {
				t.Fatal("删除冒充运行意图", runtime)
			}
			f.mu.Lock()
			defer f.mu.Unlock()
			if f.deleteCount != 1 || f.lastDelete["propagationPolicy"] != "Foreground" || f.lastDelete["preconditions"].(map[string]any)["uid"] != "created-uid" {
				t.Fatal("删除未保留唯一条件请求", f.lastDelete, f.deleteCount)
			}
		})
	}
}

// I06：后台执行使用账号当前权限，原Cookie到期仅使新的用户请求失效。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestI06CookieExpiryDoesNotCancelAcceptedOperation(t ginkgo.GinkgoTInterface) {
	// 接下来I06：后台执行使用账号当前权限，原Cookie到期仅使新的用户请求失效。
	ginkgo.By("I06：后台执行使用账号当前权限，原Cookie到期仅使新的用户请求失效。")

	entered, release := make(chan struct{}), make(chan struct{})
	var once, released sync.Once
	defer released.Do(func() { close(release) })
	var seconds atomic.Int64
	seconds.Store(time.Now().Unix())
	p, c, id, f := lifecycleFixture(t, func(stage, id string) error {
		if stage == "beforeDispatch" {
			once.Do(func() { close(entered) })
			<-release
		}
		return nil
	}, func() time.Time { return time.Unix(seconds.Load(), 0) })
	op := lifecyclePublish(t, p, c, id)
	select {
	case <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("没有派发屏障")
	}
	seconds.Add(9 * 3600)
	clusterCall(t, p, "GET", "/operations/"+op["operationId"].(string), nil, c, "", 401)
	fresh := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	released.Do(func() { close(release) })
	if done := awaitClusterOperation(t, p, fresh, op["operationId"].(string)); done["state"] != "SUCCEEDED" {
		t.Fatal(done)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.writes != 1 {
		t.Fatal("Cookie过期改变了已受理写入", f.writes)
	}
}

// P13：真实子进程在两个持久点退出，重启复用原卷/密钥/会话并禁止隐式重放。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestP13RestartUsesPersistentSentBoundary(t ginkgo.GinkgoTInterface) {
	// 接下来P13：真实子进程在两个持久点退出，重启复用原卷/密钥/会话并禁止隐式重放。
	ginkgo.By("P13：真实子进程在两个持久点退出，重启复用原卷/密钥/会话并禁止隐式重放。")

	if stage := os.Getenv("MM_P13_STAGE"); stage != "" {
		p, e := platform.Start(context.Background(), platform.Config{DataDir: os.Getenv("MM_P13_DIR"), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", OperationBoundary: func(s, id string) error {
			if s == stage {
				os.Exit(97)
			}
			return nil
		}})
		if e != nil {
			t.Fatal(e)
		}
		c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
		clusterCall(t, p, "POST", "/instances/"+os.Getenv("MM_P13_ID")+"/publications", map[string]any{"previewId": os.Getenv("MM_P13_PREVIEW"), "draftRevision": 1, "overwriteDrift": false}, c, "restart-op", 202)
		time.Sleep(10 * time.Second)
		t.Fatal("没有触发崩溃屏障")
		return
	}
	for _, stage := range []string{"beforeDispatch", "afterSendMarker"} {
		teststeps.Step(stage, func() {
			f, target := ksmTarget(t)
			dir := t.TempDir()
			cfg := platform.Config{DataDir: dir, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"}
			p, e := platform.Start(context.Background(), cfg)
			if e != nil {
				t.Fatal(e)
			}
			request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
			c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
			owner := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"].(string)
			o := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "restart", "ownerId": owner, "context": "explicit", "kubeconfig": target}, c, "reg", 202)
			cl := awaitClusterOperation(t, p, c, o["operationId"].(string))["clusterId"].(string)
			i := clusterCall(t, p, "POST", "/clusters/"+cl+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("restart")}, c, "create", 201)
			id := i["id"].(string)
			preview := ksmPreview(t, p, c, id, 1)
			p.Close(context.Background())
			cmd := exec.Command(os.Args[0], "-test.run=^TestPlatform$", "-ginkgo.focus=(^| )UT-11 ", "-ginkgo.fail-on-empty")
			cmd.Env = append(os.Environ(), "MM_P13_STAGE="+stage, "MM_P13_DIR="+dir, "MM_P13_ID="+id, "MM_P13_PREVIEW="+preview["previewId"].(string))
			raw, e := cmd.CombinedOutput()
			var exit *exec.ExitError
			if !errors.As(e, &exit) || exit.ExitCode() != 97 {
				t.Fatalf("崩溃位置错误：%v %s", e, raw)
			}
			p, e = platform.Start(context.Background(), cfg)
			if e != nil {
				t.Fatal(e)
			}
			defer p.Close(context.Background())
			ops := clusterCall(t, p, "GET", "/operations?instanceId="+id+"&idempotencyKey=restart-op", nil, c, "", 200)["operations"].([]any)
			if len(ops) != 1 {
				t.Fatal(ops)
			}
			op := ops[0].(map[string]any)
			oid := op["operationId"].(string)
			resource := resourceProgress(t, p, c, oid)
			if resource["possiblySent"] != (stage == "afterSendMarker") || resource["confirmedUid"] != "" {
				t.Fatal("I27-U07/U08：重启丢失原资源发送边界", resource)
			}
			f.mu.Lock()
			writes := f.writes
			f.mu.Unlock()
			if writes != 0 {
				t.Fatal("重启重放了请求", writes)
			}
			if stage == "beforeDispatch" {
				if op["state"] != "WAITING_CONFIRMATION" {
					t.Fatal(op)
				}
				cfgNew := ksmConfig("restart")
				cfgNew["resources"].(map[string]any)["requestCPU"] = "999m"
				clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": cfgNew}, c, "new-draft", 200)
				body := lifecycleRevision(t, p, c, oid)
				next := clusterCall(t, p, "POST", "/operations/"+oid+"/continue-unsent", body, c, "continue", 202)
				if next["attemptId"] == op["attemptId"] || next["versionId"] != op["versionId"] {
					t.Fatal(next)
				}
				done := awaitClusterOperation(t, p, c, oid)
				if done["state"] != "SUCCEEDED" {
					t.Fatal(done)
				}
				replay := clusterCall(t, p, "POST", "/operations/"+oid+"/continue-unsent", body, c, "continue", 202)
				if replay["state"] != done["state"] || replay["revision"] != done["revision"] || replay["attemptId"] != done["attemptId"] {
					t.Fatal("同键继续必须回读当前操作", replay, done)
				}
			} else {
				if op["state"] != "IDENTITY_UNCERTAIN" {
					t.Fatal(op)
				}
				body := lifecycleRevision(t, p, c, oid)
				clusterCall(t, p, "POST", "/operations/"+oid+"/retry", body, c, "retry", 409)
				clusterCall(t, p, "POST", "/operations/"+oid+"/continue-unsent", body, c, "continue", 409)
				clusterCall(t, p, "POST", "/operations/"+oid+"/cancel", body, c, "cancel", 409)
			}
		})
	}
}

// REG17-DELETE-001：外部漂移到另一namespace后，父对象先消失的Pod仍属于原UID清理图。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestDeletionCrossNamespaceOwnerUIDRemainsProtected(t ginkgo.GinkgoTInterface) {
	// 接下来REG17-DELETE-001：外部漂移到另一namespace后，父对象先消失的Pod仍属于原UID清理图。
	ginkgo.By("REG17-DELETE-001：外部漂移到另一namespace后，父对象先消失的Pod仍属于原UID清理图。")

	p, c, id, f := lifecycleFixture(t, nil)
	pub := lifecyclePublish(t, p, c, id)
	awaitLifecycleState(t, p, c, pub["operationId"].(string), "SUCCEEDED")
	f.mu.Lock()
	f.owned = deletionGraph()
	moved := map[string][]any{}
	for path, objects := range f.owned {
		for _, raw := range objects {
			raw.(map[string]any)["metadata"].(map[string]any)["namespace"] = "elsewhere"
		}
		moved[strings.ReplaceAll(path, "monitoring", "elsewhere")] = objects
	}
	f.owned = moved
	f.retainPod = true
	f.mu.Unlock()
	op := clusterCall(t, p, "POST", "/instances/"+id+"/deletions", deletionConfirmation(t, p, c, id), c, "delete", 202)
	done := awaitLifecycleState(t, p, c, op["operationId"].(string), "SUCCEEDED", "DELETION_BLOCKED")
	if done["state"] != "DELETION_BLOCKED" {
		t.Fatal("漏读另一namespace的原UID链Pod", done)
	}
	f.mu.Lock()
	f.owned = nil
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/operations/"+op["operationId"].(string)+"/refresh", nil, c, "refresh", 200)
	awaitLifecycleState(t, p, c, op["operationId"].(string), "SUCCEEDED")
}

// REG17-ACTIVITY-001：后台完成不是一次显式操作，不应重新置顶实例。

// REG17-SHUTDOWN-001：即使launcher仍占锁，停机起点也必须拒绝真正的直接RPC变更。

// REG17-DELETE-002：预检后外部删除原CR而DELETE返回404时，旧Pod仍须保留保护。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestDeletion404AfterPreflightKeepsSeenPodProtected(t ginkgo.GinkgoTInterface) {
	// 接下来REG17-DELETE-002：预检后外部删除原CR而DELETE返回404时，旧Pod仍须保留保护。
	ginkgo.By("REG17-DELETE-002：预检后外部删除原CR而DELETE返回404时，旧Pod仍须保留保护。")

	entered, release := make(chan struct{}), make(chan struct{})
	var armed atomic.Bool
	var once, released sync.Once
	defer released.Do(func() { close(release) })
	p, c, id, f := lifecycleFixture(t, func(stage, id string) error {
		if armed.Load() && stage == "afterSendMarker" {
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
	armed.Store(true)
	op := clusterCall(t, p, "POST", "/instances/"+id+"/deletions", deletionConfirmation(t, p, c, id), c, "delete", 202)
	select {
	case <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("删除未到达持久发送屏障")
	}
	f.mu.Lock()
	f.cr = nil
	for path := range f.owned {
		if !strings.HasSuffix(path, "/pods") {
			f.owned[path] = nil
		}
	}
	f.mu.Unlock()
	released.Do(func() { close(release) })
	result := awaitLifecycleState(t, p, c, op["operationId"].(string), "FAILED", "DELETION_BLOCKED")
	if result["state"] != "DELETION_BLOCKED" {
		t.Fatal("DELETE404误当清理结束并解锁", result)
	}
	self := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
	clusterCall(t, p, "POST", "/clusters/"+self["clusterId"].(string)+"/unregister", nil, c, "unregister", 409)
	f.mu.Lock()
	f.owned = nil
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/operations/"+op["operationId"].(string)+"/refresh", nil, c, "refresh", 200)
	awaitLifecycleState(t, p, c, op["operationId"].(string), "SUCCEEDED")
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.deleteRequests != 1 {
		t.Fatal("404后重发删除", f.deleteRequests)
	}
}
