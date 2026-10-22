package platform_test

import (
	"context"
	"fmt"
	"github.com/fjsxy/monitor-mgr/internal/platform"
	"strings"
	"sync"
	"time"

	ginkgo "github.com/onsi/ginkgo/v2"
)

// I28-U01 从公开 HTTP 验证草稿零写及配套配置随明确发布冻结。
func checkCompanionPublication(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I28-U01：草稿零写，预览和发布包含实例创建用户的配套 ServiceMonitor")
	p, c, id, f := ksmPublishedFixture(t)
	instance := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
	f.mu.Lock()
	writes := f.writes
	f.mu.Unlock()
	if writes != 0 {
		t.Fatal("草稿写入目标", writes)
	}
	preview := ksmPreview(t, p, c, id, 1)
	yaml := preview["rightYAML"].(string)
	if !strings.Contains(yaml, "kind: ServiceMonitor") || !strings.Contains(yaml, "monitor-mgr.io/user-id: "+instance["creatorUserId"].(string)) || !strings.Contains(yaml, "port: http-metrics") {
		t.Fatal("预览缺少配套配置", preview)
	}
	op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1}, c, "companion", 202)
	done := awaitClusterOperation(t, p, c, op["operationId"].(string))
	if done["state"] != "SUCCEEDED" {
		t.Fatal(done)
	}
	resources := done["resources"].([]any)
	if len(resources) != 2 || resources[1].(map[string]any)["target"].(map[string]any)["kind"] != "ServiceMonitor" || resources[1].(map[string]any)["confirmedUid"] == "" {
		t.Fatal("未冻结独立SM资源", done)
	}
	version := clusterCall(t, p, "GET", "/instances/"+id+"/versions/"+op["versionId"].(string), nil, c, "", 200)
	if len(version["resources"].([]any)) != 2 {
		t.Fatal(version)
	}
}

// I28-U02/U03：使用已锁定 Operator 的已知字面值验证公开预览，不重算产品映射。
func checkCompanionContract(t ginkgo.GinkgoTInterface) {
	for _, tc := range []struct{ name, identity, mode string }{
		{"first.ksm", "first-ksm-48d880a9", "Deployment"},
		{"stateful", "stateful", "StatefulSetSharded"},
		{"daemon", "daemon", "DaemonSetSharded"},
		{strings.Repeat("a", 60), "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-11ee3912", "Deployment"},
	} {
		ginkgo.By("I28-U02：Operator Service 契约 · " + tc.mode + " · " + tc.name)
		p, c, id, _ := ksmPublishedFixture(t)
		cfg := ksmConfig(tc.name)
		cfg["deploymentMode"] = tc.mode
		if tc.mode == "StatefulSetSharded" {
			cfg["shards"] = 2
		}
		if tc.mode == "DaemonSetSharded" {
			cfg["collectors"] = map[string]any{"mode": "Include", "names": []string{"pods", "nodes"}}
		}
		clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": cfg}, c, "name", 200)
		yaml := ksmPreview(t, p, c, id, 2)["rightYAML"].(string)
		if !strings.Contains(yaml, "app.kubernetes.io/instance: "+tc.identity) || !strings.Contains(yaml, "namespace: monitoring") || !strings.Contains(yaml, "path: /metrics") || strings.Contains(yaml, "monitor-in-hand/role:") || strings.Contains(yaml, "monitor-in-hand/endpoint-group:") {
			t.Fatal("选择器不能覆盖本实例所有Service", yaml)
		}
	}
	for _, variant := range []string{"crd", "namespace", "create-permission", "foreign", "preview-race"} {
		ginkgo.By("I28-U03：ServiceMonitor 预检 · " + variant)
		p, c, id, f := ksmPublishedFixture(t)
		var preview map[string]any
		if variant == "preview-race" {
			preview = ksmPreview(t, p, c, id, 1)
		}
		f.mu.Lock()
		if variant == "namespace" {
			f.missingNamespace = true
		}
		if variant == "crd" {
			f.smUnavailable = true
		}
		if variant == "create-permission" {
			f.denySM = "create"
		}
		if variant == "foreign" || variant == "preview-race" {
			f.sm = map[string]any{"metadata": map[string]any{"name": "monitor-mgr-instance", "namespace": "monitoring", "uid": "foreign"}, "spec": map[string]any{}}
		}
		f.mu.Unlock()
		if preview != nil {
			clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1}, c, "raced", 409)
		} else {
			status := 409
			if variant == "create-permission" {
				status = 403
			}
			if variant == "crd" || variant == "namespace" {
				status = 404
			}
			clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "", status)
		}
		f.mu.Lock()
		writes := f.writes
		smWrites := f.smWrites
		f.mu.Unlock()
		if writes != 0 || smWrites != 0 {
			t.Fatal("预检失败仍写目标")
		}
	}

	ginkgo.By("I28-U03：已确认配套资源缺少 update 权限时预览拒绝")
	p, c, id, f := ksmPublishedFixture(t)
	first := lifecyclePublish(t, p, c, id)
	awaitLifecycleState(t, p, c, first["operationId"].(string), "SUCCEEDED")
	f.mu.Lock()
	f.denySM = "update"
	before := f.writes
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "", 403)
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.writes != before {
		t.Fatal("权限预检失败仍写组件")
	}
}

// I28-U04：确定失败只补齐第二份资源，已成功组件需先核验且不回滚、不采用新草稿。
func checkCompanionPartialRetry(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I28-U04：ServiceMonitor 拒绝后保留 exporter，人工仅补齐原冻结资源")
	p, c, id, f := lifecycleFixture(t, nil)
	f.mu.Lock()
	f.rejectSM = 422
	f.mu.Unlock()
	op := lifecyclePublish(t, p, c, id)
	oid := op["operationId"].(string)
	done := awaitLifecycleState(t, p, c, oid, "FAILED")
	resources := done["resources"].([]any)
	if resources[0].(map[string]any)["state"] != "SUCCEEDED" || resources[1].(map[string]any)["state"] != "FAILED" {
		t.Fatal("部分结果丢失", done)
	}
	instance := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
	if instance["uid"] != "created-uid" || instance["lifecycle"] != "DEPLOYED" {
		t.Fatal("成功exporter未保留", instance)
	}
	cfg := ksmConfig("lifecycle")
	cfg["resources"].(map[string]any)["requestCPU"] = "900m"
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": cfg}, c, "new-draft", 200)
	f.mu.Lock()
	f.rejectSM = 0
	f.mu.Unlock()
	retry := clusterCall(t, p, "POST", "/operations/"+oid+"/retry", lifecycleRevision(t, p, c, oid), c, "repair", 202)
	awaitLifecycleState(t, p, c, oid, "SUCCEEDED")
	if retry["versionId"] != op["versionId"] {
		t.Fatal("重试换版本")
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.writes != 1 || f.smWrites != 1 || f.cr["spec"].(map[string]any)["podResources"].(map[string]any)["requests"].(map[string]any)["cpu"] != "100m" {
		t.Fatal("重试未跳过成功组件或采用新草稿", f.writes, f.smWrites)
	}
}

// I28-U05：第二资源实际写入但丢响应，真实平台重开后仅用独立原 UID 核验。
func checkCompanionUnknownRecovery(t ginkgo.GinkgoTInterface) {
	for _, known := range []bool{false, true} {
		ginkgo.By(fmt.Sprintf("I28-U05：ServiceMonitor 丢响应与平台重启 · knownUID=%v", known))
		f, target := ksmTarget(t)
		config := platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"}
		p, e := platform.Start(context.Background(), config)
		if e != nil {
			t.Fatal(e)
		}
		request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
		c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
		owner := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"]
		reg := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "sm-recovery", "ownerId": owner, "context": "explicit", "kubeconfig": target}, c, "register", 202)
		cluster := awaitClusterOperation(t, p, c, reg["operationId"].(string))["clusterId"].(string)
		i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("recovery")}, c, "create", 201)
		id := i["id"].(string)
		rev := 1
		if known {
			first := lifecyclePublish(t, p, c, id)
			awaitLifecycleState(t, p, c, first["operationId"].(string), "SUCCEEDED")
			cfg := ksmConfig("recovery")
			cfg["resources"].(map[string]any)["requestCPU"] = "200m"
			clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": cfg}, c, "edit", 200)
			rev = 2
		}
		preview := ksmPreview(t, p, c, id, rev)
		f.mu.Lock()
		f.dropSM = true
		f.mu.Unlock()
		op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": rev}, c, "unknown-sm", 202)
		oid := op["operationId"].(string)
		state := "IDENTITY_UNCERTAIN"
		if known {
			state = "RESULT_UNCERTAIN"
		}
		awaitLifecycleState(t, p, c, oid, state)
		p.Close(context.Background())
		f.mu.Lock()
		f.dropSM = false
		f.smReadFailure = false
		before := f.smRequests
		componentWrites := f.writes
		f.mu.Unlock()
		p, e = platform.Start(context.Background(), config)
		if e != nil {
			t.Fatal(e)
		}
		clusterCall(t, p, "POST", "/operations/"+oid+"/refresh", nil, c, "", 200)
		if known {
			state = "SUCCEEDED"
		}
		done := awaitLifecycleState(t, p, c, oid, state)
		rs := done["resources"].([]any)
		sm := rs[1].(map[string]any)
		uid := ""
		if known {
			uid = "service-monitor-uid"
		}
		if rs[0].(map[string]any)["state"] != "SUCCEEDED" || sm["state"] != state || sm["confirmedUid"] != uid || sm["possiblySent"] != true {
			t.Fatal("第二资源恢复身份错误", done)
		}
		clusterCall(t, p, "POST", "/operations/"+oid+"/retry", lifecycleRevision(t, p, c, oid), c, "forbid", 409)
		f.mu.Lock()
		after := f.smRequests
		writes := f.writes
		f.mu.Unlock()
		if before != after || componentWrites != writes {
			t.Fatal("只读核验重放了资源写入")
		}
		p.Close(context.Background())
	}
}

// I28-U06：组件成功不会授予下一份资源永久派发权限。
func checkCompanionDispatchAuthorization(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I28-U06：exporter 成功后降权，第二资源写入前再次授权")
	entered, release := make(chan struct{}), make(chan struct{})
	var once, released sync.Once
	defer released.Do(func() { close(release) })
	p, c, id, f := lifecycleFixture(t, func(stage, id string) error {
		if stage == "beforeResourceDispatch:service-monitor" {
			once.Do(func() { close(entered) })
			<-release
		}
		return nil
	})
	actor := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"].(string)
	other, _ := identityUser(t, p, c, "companion-admin", "ADMIN")
	op := lifecyclePublish(t, p, c, id)
	select {
	case <-entered:
	case <-time.After(4 * time.Second):
		t.Fatal("未进入第二资源派发边界")
	}
	clusterCall(t, p, "PATCH", "/accounts/"+actor, map[string]any{"role": "USER"}, other, "demote", 200)
	released.Do(func() { close(release) })
	done := awaitLifecycleState(t, p, other, op["operationId"].(string), "FAILED")
	rs := done["resources"].([]any)
	if rs[0].(map[string]any)["state"] != "SUCCEEDED" || rs[1].(map[string]any)["possiblySent"] != false {
		t.Fatal("失权污染前序结果", done)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.writes != 1 || f.smRequests != 0 {
		t.Fatal("第二资源失权仍写入", f.writes, f.smRequests)
	}
}

// I28-U07：配套资源漂移和独立 UID 均参与预览/派发保护，整体删除包含两份 CR。
func checkCompanionIdentityAndDeletion(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I28-U07：独立 ServiceMonitor UID、漂移覆盖与整体删除")
	p, c, id, f := lifecycleFixture(t, nil)
	first := lifecyclePublish(t, p, c, id)
	awaitLifecycleState(t, p, c, first["operationId"].(string), "SUCCEEDED")
	f.mu.Lock()
	f.sm["metadata"].(map[string]any)["uid"] = "replacement"
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "", 409)
	clusterCall(t, p, "POST", "/instances/"+id+"/deletion-previews", map[string]any{}, c, "", 409)
	f.mu.Lock()
	f.sm["metadata"].(map[string]any)["uid"] = "service-monitor-uid"
	f.sm["spec"].(map[string]any)["jobLabel"] = "foreign"
	f.mu.Unlock()
	preview := ksmPreview(t, p, c, id, 1)
	if preview["drift"] != true {
		t.Fatal("配套漂移未呈现", preview)
	}
	body := map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}
	clusterCall(t, p, "POST", "/instances/"+id+"/publications", body, c, "no-overwrite", 409)
	body["overwriteDrift"] = true
	next := clusterCall(t, p, "POST", "/instances/"+id+"/publications", body, c, "overwrite", 202)
	awaitLifecycleState(t, p, c, next["operationId"].(string), "SUCCEEDED")
	deletion := clusterCall(t, p, "POST", "/instances/"+id+"/deletions", deletionConfirmation(t, p, c, id), c, "delete-both", 202)
	done := awaitLifecycleState(t, p, c, deletion["operationId"].(string), "SUCCEEDED")
	rs := done["resources"].([]any)
	if len(rs) != 2 || rs[1].(map[string]any)["state"] != "SUCCEEDED" {
		t.Fatal("整体删除未确认两资源", done)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.cr != nil || f.sm != nil || f.smDeleteRequests != 1 || f.lastSMDelete["preconditions"].(map[string]any)["uid"] != "service-monitor-uid" {
		t.Fatal("删除没有绑定SM原UID", f.lastSMDelete)
	}
}

// I28-U04：原成功 exporter 当前被替换或漂移时，不沿用历史成功事实补写 SM。
func checkCompanionRetryRechecksExporter(t ginkgo.GinkgoTInterface) {
	for _, variant := range []string{"uid", "drift"} {
		ginkgo.By("I28-U04：补齐前核验 exporter · " + variant)
		p, c, id, f := lifecycleFixture(t, nil)
		f.mu.Lock()
		f.rejectSM = 422
		f.mu.Unlock()
		op := lifecyclePublish(t, p, c, id)
		oid := op["operationId"].(string)
		awaitLifecycleState(t, p, c, oid, "FAILED")
		f.mu.Lock()
		f.rejectSM = 0
		if variant == "uid" {
			f.cr["metadata"].(map[string]any)["uid"] = "foreign-exporter"
		} else {
			f.cr["spec"].(map[string]any)["enabled"] = false
		}
		before := f.smRequests
		f.mu.Unlock()
		clusterCall(t, p, "POST", "/operations/"+oid+"/retry", lifecycleRevision(t, p, c, oid), c, "repair", 202)
		awaitLifecycleState(t, p, c, oid, "RESULT_UNCERTAIN")
		f.mu.Lock()
		after := f.smRequests
		f.mu.Unlock()
		if before != after {
			t.Fatal("前序身份不明仍补写SM")
		}
	}
}

// I28-R02/R03：异步 Foreground 删除按独立资源恢复，只读核验不会隐式派发尚未发送的组件。
func checkCompanionDeletionRecovery(t ginkgo.GinkgoTInterface) {
	for _, delayedSM := range []bool{false, true} {
		ginkgo.By(fmt.Sprintf("I28-R02/R03：原 UID 两资源删除恢复 · 配套资源异步=%v", delayedSM))
		p, c, id, f := lifecycleFixture(t, nil)
		published := lifecyclePublish(t, p, c, id)
		awaitLifecycleState(t, p, c, published["operationId"].(string), "SUCCEEDED")
		f.mu.Lock()
		f.retainSM, f.retainCR = delayedSM, true
		f.owned = deletionGraph()
		f.cr["metadata"].(map[string]any)["finalizers"] = []any{"fixture/hold"}
		f.mu.Unlock()
		deletion := clusterCall(t, p, "POST", "/instances/"+id+"/deletions", deletionConfirmation(t, p, c, id), c, "delete-recovery", 202)
		oid := deletion["operationId"].(string)
		blocked := awaitLifecycleState(t, p, c, oid, "DELETION_BLOCKED")
		if delayedSM {
			rs := blocked["resources"].([]any)
			if rs[0].(map[string]any)["possiblySent"] != false || rs[1].(map[string]any)["possiblySent"] != true {
				t.Fatal("SM 未清理时错误派发组件", blocked)
			}
			f.mu.Lock()
			before := f.deleteRequests
			f.sm = nil
			f.mu.Unlock()
			if before != 0 {
				t.Fatal("组件未等配套资源结束", before)
			}
			clusterCall(t, p, "POST", "/operations/"+oid+"/refresh", nil, c, "read-sm", 200)
			waiting := awaitLifecycleState(t, p, c, oid, "WAITING_CONFIRMATION")
			for n := 0; n < 3; n++ {
				clusterCall(t, p, "POST", "/operations/"+oid+"/refresh", nil, c, "read-unsent", 200)
			}
			f.mu.Lock()
			requests, smRequests := f.deleteRequests, f.smDeleteRequests
			f.mu.Unlock()
			if requests != 0 || smRequests != 1 {
				t.Fatal("只读恢复隐式发送或重放删除", requests, smRequests)
			}
			// 关键：重新确认当前删除影响后，显式继续同一版本的新尝试；旧 SM 成功记录不能重放。
			preview := clusterCall(t, p, "POST", "/instances/"+id+"/deletion-previews", map[string]any{"operationId": oid}, c, "preview-unsent", 200)
			body := lifecycleRevision(t, p, c, oid)
			body["deletePreviewId"] = preview["previewId"]
			continued := clusterCall(t, p, "POST", "/operations/"+oid+"/continue-unsent", body, c, "continue-component", 202)
			if continued["versionId"] != published["versionId"] || continued["attemptId"] == waiting["attemptId"] {
				t.Fatal("继续未绑定原版本的新尝试", continued, waiting)
			}
			blocked = awaitLifecycleState(t, p, c, oid, "DELETION_BLOCKED")
		}
		rs := blocked["resources"].([]any)
		component, sm := rs[0].(map[string]any), rs[1].(map[string]any)
		if component["possiblySent"] != true || component["confirmedUid"] != "created-uid" || sm["state"] != "SUCCEEDED" || sm["confirmedUid"] != "service-monitor-uid" {
			t.Fatal("原组件 finalizer 阶段未确认两份独立资源", blocked)
		}
		instance := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
		if instance["lifecycle"] == "DELETED" {
			t.Fatal("原组件仍存在却结束整体删除", instance)
		}
		f.mu.Lock()
		f.cr, f.owned = nil, nil
		f.mu.Unlock()
		clusterCall(t, p, "POST", "/operations/"+oid+"/refresh", nil, c, "read-component", 200)
		awaitLifecycleState(t, p, c, oid, "SUCCEEDED")
		instance = clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
		if instance["lifecycle"] != "DELETED" {
			t.Fatal("完整原图清空后未结束删除", instance)
		}
		f.mu.Lock()
		smCount, count := f.smDeleteRequests, f.deleteRequests
		smUID := f.lastSMDelete["preconditions"].(map[string]any)["uid"]
		componentUID := f.lastDelete["preconditions"].(map[string]any)["uid"]
		f.mu.Unlock()
		if smCount != 1 || count != 1 || smUID != "service-monitor-uid" || componentUID != "created-uid" {
			t.Fatal("删除丢失原 UID 或重放", smCount, count)
		}
	}
}
