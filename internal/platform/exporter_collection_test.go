// 配套采集测试只经公开 HTTP/真实 Kitex/SQLite 与受控 TLS 目标验证业务。
package platform_test

import (
	"encoding/json"
	"fmt"
	"github.com/fjsxy/monitor-mgr/internal/platform"
	"net/http"
	"strings"
	"sync"
	"time"

	ginkgo "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
)

// 新草稿与不可变版本仅表达父 CR 的采集意图，不能再生成第二份资源。
func checkExporterCollectionIntent(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I28-U01：新 exporter 默认开启；草稿零写且仅冻结父 CR 与固定创建者标签")
	p, c, id, f := ksmPublishedFixture(t)
	instance := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
	draft := clusterCall(t, p, "GET", "/instances/"+id+"/draft", nil, c, "", 200)
	cfg := draft["ksm"].(map[string]any)
	Expect(cfg).To(HaveKeyWithValue("serviceMonitor", map[string]any{"enabled": true, "interval": "30s", "scrapeTimeout": "10s"}))
	f.mu.Lock()
	writes := f.mutationRequests
	f.mu.Unlock()
	Expect(writes).To(BeZero())
	preview := ksmPreview(t, p, c, id, 1)
	Expect(preview["rightYAML"]).To(ContainSubstring("serviceMonitor:"))
	Expect(preview["rightYAML"]).To(ContainSubstring("monitor-mgr.io/user-id: " + instance["creatorUserId"].(string)))
	op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "collection-publish", 202)
	done := awaitClusterOperation(t, p, c, op["operationId"].(string))
	Expect(done["state"]).To(Equal("SUCCEEDED"))
	resources := done["resources"].([]any)
	Expect(resources).To(HaveLen(1))
	Expect(resources[0].(map[string]any)["target"].(map[string]any)["kind"]).To(Equal("KubeStateMetrics"))
	version := clusterCall(t, p, "GET", "/instances/"+id+"/versions/"+op["versionId"].(string), nil, c, "", 200)
	Expect(version["resources"]).To(HaveLen(1))
	Expect(version["yaml"]).To(ContainSubstring("serviceMonitor:"))
	Expect(strings.Count(version["yaml"].(string), "kind:")).To(Equal(1))
	f.mu.Lock()
	defer f.mu.Unlock()
	var sent map[string]any
	Expect(json.Unmarshal(f.lastWrite, &sent)).To(Succeed())
	monitor := sent["spec"].(map[string]any)["serviceMonitor"].(map[string]any)
	Expect(monitor["labels"]).To(Equal(map[string]any{"monitor-mgr.io/user-id": instance["creatorUserId"]}))
	Expect(f.smWrites).To(BeZero())
}

// 旧 schema 可能接受请求却裁掉字段，必须在任何 CR 写入之前识别。
func checkExporterCollectionCapability(t ginkgo.GinkgoTInterface) {
	checkExporterOperatorRuntime(t)
	for _, field := range []string{"spec.serviceMonitor", "spec.serviceMonitor.enabled", "spec.serviceMonitor.interval", "status.serviceMonitor.resource.uid", "status.serviceMonitor.state"} {
		ginkgo.By("I28-U03.crd：实际 served schema 缺失/错误 " + field + " 时零业务写拒绝")
		p, c, id, f := ksmPublishedFixture(t)
		f.mu.Lock()
		version := f.crd["spec"].(map[string]any)["versions"].([]any)[0].(map[string]any)
		schema := version["schema"].(map[string]any)["openAPIV3Schema"].(map[string]any)
		for _, part := range strings.Split(field, ".") {
			schema = schema["properties"].(map[string]any)[part].(map[string]any)
		}
		switch field {
		case "spec.serviceMonitor.enabled":
			schema["default"] = true
		case "status.serviceMonitor.state":
			schema["enum"] = []any{"Ready"}
		default:
			schema["type"] = "array"
		}
		f.mu.Unlock()
		response := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "invalid-schema", 409)
		Expect(response["code"]).To(Equal("CONFLICT"))
		f.mu.Lock()
		writes, smWrites := f.mutationRequests, f.smWrites
		f.mu.Unlock()
		Expect(writes).To(BeZero())
		Expect(smWrites).To(BeZero())
	}
}

// duration 与受控标签都在公开配置接缝拒绝；不让非法值进入不可变版本。
func checkExporterCollectionParameters(t ginkgo.GinkgoTInterface) {
	p, c, id, f := ksmPublishedFixture(t)
	for n, input := range []map[string]any{
		{"enabled": true, "interval": "0s", "scrapeTimeout": "1s"},
		{"enabled": true, "interval": "-1s", "scrapeTimeout": "1s"},
		{"enabled": true, "interval": "nonsense", "scrapeTimeout": "1s"},
		{"enabled": true, "interval": "10s", "scrapeTimeout": "11s"},
		{"enabled": true, "interval": "30s", "scrapeTimeout": "0"},
	} {
		ginkgo.By("I28-R02：非法采集参数输入 " + fmt.Sprint(n))
		cfg := ksmConfig("instance")
		cfg["serviceMonitor"] = input
		response := clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": cfg}, c, fmt.Sprint("bad-monitor", n), 422)
		Expect(response["code"]).To(Equal("INVALID_INPUT"))
	}
	ginkgo.By("I28-R01：任意归属 labels 不接受")
	cfg := ksmConfig("instance")
	cfg["serviceMonitor"] = map[string]any{"enabled": true, "labels": map[string]string{"monitor-mgr.io/user-id": "foreign"}}
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": cfg}, c, "foreign-label", 400)
	ginkgo.By("I28-R02：合法复合duration往返保存")
	cfg["serviceMonitor"] = map[string]any{"enabled": true, "interval": "1m30s", "scrapeTimeout": "250ms"}
	saved := clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": cfg}, c, "valid-monitor", 200)
	Expect(saved["ksm"].(map[string]any)["serviceMonitor"]).To(Equal(cfg["serviceMonitor"]))
	f.mu.Lock()
	defer f.mu.Unlock()
	Expect(f.mutationRequests).To(BeZero())
	Expect(f.smWrites).To(BeZero())
}

// observationFixture 只模拟 Operator 公开 status 与真实子对象；名字故意不符合任何派生规则。
func collectionObservationFixture(f *ksmFixture, uid string) {
	f.cr["status"] = map[string]any{"serviceMonitor": map[string]any{"observedGeneration": f.cr["metadata"].(map[string]any)["generation"], "state": "Ready", "reason": "ReconcileSucceeded", "message": "配套配置已收敛", "resource": map[string]any{"apiVersion": "monitoring.coreos.com/v1", "kind": "ServiceMonitor", "namespace": "monitoring", "name": "operator-chosen.independent", "uid": uid}}, "conditions": []any{map[string]any{"type": "ServiceMonitorReady", "status": "True", "observedGeneration": f.cr["metadata"].(map[string]any)["generation"]}}}
	f.legacySM = map[string]any{"apiVersion": "monitoring.coreos.com/v1", "kind": "ServiceMonitor", "metadata": map[string]any{"name": "operator-chosen.independent", "namespace": "monitoring", "uid": uid, "resourceVersion": "42", "ownerReferences": []any{map[string]any{"apiVersion": "monitoring.fjsxy.github.io/v1alpha1", "kind": "KubeStateMetrics", "name": f.cr["metadata"].(map[string]any)["name"], "uid": "created-uid", "controller": true}}}, "spec": map[string]any{"endpoints": []any{map[string]any{"port": "arbitrary-metrics"}}}}
}

// collectionSource 通过公开实例接口读取配套来源，确保读取模型经真实 RPC。
func collectionSource(t ginkgo.GinkgoTInterface, p *platform.Runtime, c *http.Cookie, id string) map[string]any {
	t.Helper()
	v := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
	for _, raw := range v["sources"].([]any) {
		s := raw.(map[string]any)
		if s["source"] == "ServiceMonitor" {
			return s
		}
	}
	t.Fatal("缺少独立配套观察来源")
	return nil
}

// 父发布与配套观察独立，合法重建 UID 不触发父实例身份丢失。
func checkExporterCollectionObservation(t ginkgo.GinkgoTInterface) {
	p, c, id, f := ksmPublishedFixture(t)
	op := lifecyclePublish(t, p, c, id)
	Expect(awaitClusterOperation(t, p, c, op["operationId"].(string))["state"]).To(Equal("SUCCEEDED"))
	ginkgo.By("I28-U04/I28-U02.deployment：仅按 status 引用和 parent/controller UID 读取独立配套对象")
	f.mu.Lock()
	collectionObservationFixture(f, "companion-uid-a")
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, c, "observe", 200)
	source := collectionSource(t, p, c, id)
	Expect(source["historical"]).To(BeFalse())
	companion := source["companion"].(map[string]any)
	Expect(companion["state"]).To(Equal("Ready"))
	Expect(companion["parentUid"]).To(Equal("created-uid"))
	Expect(companion["resource"].(map[string]any)["uid"]).To(Equal("companion-uid-a"))
	Expect(companion["yaml"]).To(ContainSubstring("arbitrary-metrics"))
	ginkgo.By("I28-U04.uid：Operator 合法重建子 UID 后父 UID/版本不变")
	f.mu.Lock()
	collectionObservationFixture(f, "companion-uid-b")
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, c, "rebuilt", 200)
	v := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
	Expect(v["uid"]).To(Equal("created-uid"))
	Expect(v["currentVersionId"]).To(Equal(op["versionId"]))
	Expect(collectionSource(t, p, c, id)["companion"].(map[string]any)["resource"].(map[string]any)["uid"]).To(Equal("companion-uid-b"))
	ginkgo.By("I28-U04.drift：旧 generation 只报未收敛")
	f.mu.Lock()
	f.cr["status"].(map[string]any)["serviceMonitor"].(map[string]any)["observedGeneration"] = float64(0)
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, c, "old-generation", 200)
	Expect(collectionSource(t, p, c, id)["companion"].(map[string]any)["state"]).To(Equal("Reconciling"))
	ginkgo.By("I28-R03：读取失败立即标记历史，不能拿父 CR 成功续期配套")
	f.mu.Lock()
	collectionObservationFixture(f, "companion-uid-b")
	f.smReadFailure = true
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, c, "forbidden", 200)
	source = collectionSource(t, p, c, id)
	Expect(source["error"]).To(ContainSubstring("FORBIDDEN"))
	Expect(source["historical"]).To(BeTrue())
	ginkgo.By("I28-U03.foreign：错 namespace/UID/controller 引用不能读取或认领")
	f.mu.Lock()
	f.smReadFailure = false
	f.legacySM["metadata"].(map[string]any)["ownerReferences"].([]any)[0].(map[string]any)["uid"] = "foreign-parent"
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, c, "foreign", 200)
	Expect(collectionSource(t, p, c, id)["error"]).To(ContainSubstring("CONFLICT"))
	for _, field := range []string{"apiVersion", "kind", "namespace", "name", "uid"} {
		ginkgo.By("I28-U03.foreign：完整status引用字段拒绝错误 " + field)
		f.mu.Lock()
		collectionObservationFixture(f, "companion-uid-b")
		ref := f.cr["status"].(map[string]any)["serviceMonitor"].(map[string]any)["resource"].(map[string]any)
		ref[field] = "foreign-value"
		f.mu.Unlock()
		clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, c, "invalid-ref-"+field, 200)
		Expect(collectionSource(t, p, c, id)["historical"]).To(BeTrue())
	}
	ginkgo.By("I28-U04：Operator Degraded 与父下发结果分开")
	f.mu.Lock()
	collectionObservationFixture(f, "companion-uid-b")
	st := f.cr["status"].(map[string]any)
	monitor := st["serviceMonitor"].(map[string]any)
	monitor["state"] = "Degraded"
	monitor["reason"] = "PermissionDenied"
	monitor["message"] = "Operator 配套写入失败"
	delete(monitor, "resource")
	st["conditions"].([]any)[0].(map[string]any)["status"] = "False"
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, c, "degraded", 200)
	Expect(collectionSource(t, p, c, id)["companion"].(map[string]any)["state"]).To(Equal("Degraded"))
	Expect(clusterCall(t, p, "GET", "/operations/"+op["operationId"].(string), nil, c, "", 200)["state"]).To(Equal("SUCCEEDED"))
	f.mu.Lock()
	defer f.mu.Unlock()
	Expect(f.smWrites).To(BeZero())
	Expect(f.smReadPaths).To(ContainElement("/apis/monitoring.coreos.com/v1/namespaces/monitoring/servicemonitors/operator-chosen.independent"))
}

// 原配套即使 owner 被改仍按已观察 UID 留在清理图，平台只等待其消失。
func checkExporterCollectionDeletion(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I28-U07：父删除后配套原 UID 未消失不能提前完成，平台 SM DELETE 为0")
	p, c, id, f := ksmPublishedFixture(t)
	op := lifecyclePublish(t, p, c, id)
	Expect(awaitClusterOperation(t, p, c, op["operationId"].(string))["state"]).To(Equal("SUCCEEDED"))
	f.mu.Lock()
	collectionObservationFixture(f, "companion-delete-uid")
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, c, "record-companion", 200)
	preview := clusterCall(t, p, "POST", "/instances/"+id+"/deletion-previews", map[string]any{}, c, "delete-preview", 200)
	del := clusterCall(t, p, "POST", "/instances/"+id+"/deletions", map[string]any{"versionId": op["versionId"], "previewId": preview["previewId"]}, c, "delete-parent", 202)
	done := awaitLifecycleState(t, p, c, del["operationId"].(string), "DELETION_BLOCKED", "SUCCEEDED")
	Expect(done["state"]).To(Equal("DELETION_BLOCKED"))
	f.mu.Lock()
	f.legacySM = nil
	f.mu.Unlock()
	done = clusterCall(t, p, "POST", "/operations/"+del["operationId"].(string)+"/refresh", nil, c, "gone", 200)
	Expect(done["state"]).To(Equal("SUCCEEDED"))
	f.mu.Lock()
	defer f.mu.Unlock()
	Expect(f.smWrites).To(BeZero())
	Expect(f.deleteCount).To(Equal(1))
}

// 管理确认只授予管理权限，不能补造归属标签所需的历史创建身份。
func checkExporterUnknownCreator(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I28-R01：历史 creator 未知，管理员权限确认后仍不能启用配套采集")
	p, admin, user, id, _, f, _ := historicalFixture(t, nil)
	current := clusterCall(t, p, "GET", "/instances/"+id, nil, admin, "", 200)
	Expect(current["creatorUserId"]).To(BeEmpty())
	clusterCall(t, p, "POST", "/instances/"+id+"/management-confirmations", map[string]any{"allowClusterManagers": true}, admin, "confirm-management-only", 200)
	current = clusterCall(t, p, "GET", "/instances/"+id, nil, user, "", 200)
	Expect(current["canManage"]).To(BeTrue())
	Expect(current["creatorUserId"]).To(BeEmpty())
	cfg := ksmConfig("lifecycle")
	cfg["serviceMonitor"] = map[string]any{"enabled": true, "interval": "30s", "scrapeTimeout": "10s"}
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": cfg}, user, "explicit-new-collection", 200)
	for n, actor := range []*http.Cookie{admin, user} {
		denied := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 2}, actor, fmt.Sprint("unknown-creator-", n), 409)
		Expect(denied["message"]).To(ContainSubstring("创建用户未知"))
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	Expect(f.mutationRequests).To(BeZero())
	Expect(f.smWrites).To(BeZero())
}

// exporterRuntimeFixture 表达已批准 RC4 的实际运行身份，不以 tag 或 CRD 冒充进程能力。
func exporterRuntimeFixture() map[string]map[string]any {
	image := "docker.io/fjsxy/monitor-in-hand@sha256:1a9ec41de21c7b2b633ee7179f5f3f4c1a9e098f1dc4e74a9e720dbee73f19ff"
	container := map[string]any{"name": "manager", "image": image, "args": []any{"--leader-elect=true", "--health-probe-bind-address=:8081", "--log-level=info"}}
	metadata := func(name, uid string) map[string]any {
		return map[string]any{"name": name, "namespace": "operator-system", "uid": uid}
	}
	dep := map[string]any{"metadata": metadata("monitor-in-hand", "operator-dep"), "spec": map[string]any{"template": map[string]any{"spec": map[string]any{"serviceAccountName": "operator", "containers": []any{container}}}}}
	rsMeta := metadata("operator-rs", "operator-rs")
	rsMeta["ownerReferences"] = []any{map[string]any{"kind": "Deployment", "uid": "operator-dep", "controller": true}}
	rs := map[string]any{"metadata": rsMeta}
	podMeta := metadata("operator-pod", "operator-pod")
	podMeta["ownerReferences"] = []any{map[string]any{"kind": "ReplicaSet", "uid": "operator-rs", "controller": true}}
	podContainer := map[string]any{}
	for key, value := range container {
		podContainer[key] = value
	}
	podContainer["volumeMounts"] = []any{map[string]any{"name": "kube-api-access-fixture", "mountPath": "/var/run/secrets/kubernetes.io/serviceaccount", "readOnly": true}}
	pod := map[string]any{"metadata": podMeta, "spec": map[string]any{"serviceAccountName": "operator", "containers": []any{podContainer}}, "status": map[string]any{"phase": "Running", "containerStatuses": []any{map[string]any{"name": "manager", "ready": true, "containerID": "containerd://original", "imageID": "docker-pullable://docker.io/fjsxy/monitor-in-hand@sha256:0eadb4af76f5a1c72eb3f5bb4b8372eab43dbabb0a4dfb194866d58cf854ebde"}}}}
	return map[string]map[string]any{"/apis/apps/v1/deployments": {"items": []any{dep}}, "/apis/apps/v1/namespaces/operator-system/replicasets": {"items": []any{rs}}, "/api/v1/namespaces/operator-system/pods": {"items": []any{pod}}, "/api/v1/namespaces/operator-system/pods/operator-pod": pod}
}

// checkExporterOperatorRuntime 经公开预览拒绝缺失、旧版本及无法核实的实际 controller。
func checkExporterOperatorRuntime(t ginkgo.GinkgoTInterface) {
	for _, variant := range []string{"absent", "old", "tag-only", "wrong-digest", "not-ready", "read-denied", "env", "command"} {
		ginkgo.By("I23-R09.runtime-" + variant)
		p, a, id, f := ksmPublishedFixture(t)
		f.mu.Lock()
		pod := f.exporterRuntime["/api/v1/namespaces/operator-system/pods/operator-pod"]
		c := pod["spec"].(map[string]any)["containers"].([]any)[0].(map[string]any)
		status := pod["status"].(map[string]any)["containerStatuses"].([]any)[0].(map[string]any)
		switch variant {
		case "absent":
			f.exporterRuntime["/apis/apps/v1/deployments"] = map[string]any{"items": []any{}}
		case "old":
			c["image"] = "docker.io/fjsxy/monitor-in-hand:0.1.0"
		case "tag-only":
			c["image"] = "docker.io/fjsxy/monitor-in-hand:0.2.0-rc.4"
		case "wrong-digest":
			status["imageID"] = "docker.io/fjsxy/monitor-in-hand@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
		case "not-ready":
			status["ready"] = false
		case "read-denied":
			f.exporterRuntimeDenied = true
		case "env":
			c["env"] = []any{map[string]any{"name": "KUBERNETES_SERVICE_HOST", "value": "other.invalid"}}
		case "command":
			c["command"] = []any{"/other-process"}
		}
		f.mu.Unlock()
		response := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, a, "runtime-"+variant, 409)
		Expect(response["code"]).To(Equal("CONFLICT"))
		f.mu.Lock()
		Expect(f.mutationRequests).To(BeZero())
		Expect(f.smWrites).To(BeZero())
		f.mu.Unlock()
	}
	// 预览时的运行能力不能作为稍后发布的永久凭据。
	p, a, id, f := ksmPublishedFixture(t)
	preview := ksmPreview(t, p, a, id, 1)
	f.mu.Lock()
	f.exporterRuntime["/apis/apps/v1/deployments"] = map[string]any{"items": []any{}}
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"draftRevision": 1, "previewId": preview["previewId"]}, a, "runtime-recheck", 409)

	ginkgo.By("I23-R09.disabled：关闭配套采集不新增运行 controller 依赖")
	config := ksmConfig("instance")
	config["serviceMonitor"] = map[string]any{"enabled": false}
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": config}, a, "runtime-disable", 200)
	ksmPreview(t, p, a, id, 2)
}

// checkExporterOperatorDispatch 验证受理时的运行证据不能绕过后台实际写入前的再次预检。
func checkExporterOperatorDispatch(t ginkgo.GinkgoTInterface) {
	entered, release := make(chan struct{}), make(chan struct{})
	var once, released sync.Once
	defer released.Do(func() { close(release) })
	p, a, id, f := lifecycleFixture(t, func(stage, id string) error {
		if stage == "beforeDispatch" {
			once.Do(func() { close(entered) })
			<-release
		}
		return nil
	})
	op := lifecyclePublish(t, p, a, id)
	select {
	case <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("未到实际派发屏障")
	}
	f.mu.Lock()
	f.exporterRuntime["/apis/apps/v1/deployments"] = map[string]any{"items": []any{}}
	f.mu.Unlock()
	released.Do(func() { close(release) })
	done := awaitLifecycleState(t, p, a, op["operationId"].(string), "FAILED")
	Expect(done["message"]).To(ContainSubstring("exporter Operator"))
	f.mu.Lock()
	defer f.mu.Unlock()
	Expect(f.mutationRequests).To(BeZero())
	Expect(f.smWrites).To(BeZero())
}
