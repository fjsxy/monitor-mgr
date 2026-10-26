package platform_test

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"github.com/fjsxy/monitor-mgr/internal/platform"
	ginkgo "github.com/onsi/ginkgo/v2"
	"gopkg.in/yaml.v3"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync/atomic"
)

// I27-U01/C02：真实公开发布应返回唯一实际写入 CR 的冻结目标和独立结果。
func checkSingleCRResourceProgress(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I27-U01/C02：单 CR 的冻结文档、原 UID 与发送边界通过公开 API 可读")
	p, c, id, f := lifecycleFixture(t, nil)
	op := lifecyclePublish(t, p, c, id)
	done := awaitLifecycleState(t, p, c, op["operationId"].(string), "SUCCEEDED")
	resources, ok := done["resources"].([]any)
	if !ok || len(resources) != 1 {
		t.Fatal("新模式只能包含实际写入的 component CR", done)
	}
	r := resources[0].(map[string]any)
	target := r["target"].(map[string]any)
	if target["kind"] != "KubeStateMetrics" || target["name"] != "lifecycle" || r["confirmedUid"] != "created-uid" || r["possiblySent"] != true || r["state"] != "SUCCEEDED" {
		t.Fatal(r)
	}
	v := clusterCall(t, p, "GET", "/instances/"+id+"/versions/"+op["versionId"].(string), nil, c, "", http.StatusOK)
	if len(v["resources"].([]any)) != 1 || !strings.Contains(target["yaml"].(string), "targetNamespace: monitoring") {
		t.Fatal(v, r)
	}
	f.mu.Lock()
	writes := f.writes
	generation := f.cr["metadata"].(map[string]any)["generation"]
	f.cr["status"] = map[string]any{"observedGeneration": generation, "phase": "NotReady", "desiredPods": float64(1), "availablePods": float64(0), "conditions": []any{map[string]any{"type": "Ready", "status": "False", "reason": "ServiceMonitorReconcileFailed", "observedGeneration": generation}}}
	f.mu.Unlock()
	if writes != 1 {
		t.Fatal("单 CR 发布应只有一次写入", writes)
	}
	// I27-C04：Operator 故障只进入观察，不能把成功 CR 发布变成第二份可重试资源。
	ginkgo.By("I27-C04：Operator 子资源故障与已成功 CR 写入分别表达")
	clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, c, "refresh-observation", 200)
	runtime := clusterCall(t, p, "GET", "/instances/"+id+"/runtime", nil, c, "", 200)
	current := clusterCall(t, p, "GET", "/operations/"+op["operationId"].(string), nil, c, "", 200)
	if current["state"] != "SUCCEEDED" || runtime["summary"] != "NotReady" || runtime["error"] != "ServiceMonitorReconcileFailed" {
		t.Fatal("CR 写入与 Operator 观察没有独立表达", current, runtime)
	}
	f.mu.Lock()
	writes, smWrites := f.writes, f.smWrites
	f.mu.Unlock()
	if writes != 1 || smWrites != 0 {
		t.Fatal("Operator 观察故障触发平台补写", writes, smWrites)
	}
}

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
			writes = func() int { f.mu.Lock(); defer f.mu.Unlock(); return f.writes }
			verifyTarget = func(uid string) {
				f.mu.Lock()
				defer f.mu.Unlock()
				if f.cr == nil || f.cr["metadata"].(map[string]any)["name"] != name || f.cr["metadata"].(map[string]any)["uid"] != uid || f.cr["spec"].(map[string]any)["targetNamespace"] != namespace {
					t.Error("新 KSM 身份与目标组件不一致")
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

// I27-C01：独立旧 schema/任务夹具仅描述升级前持久事实；所有读取和恢复动作经过公开 HTTP/Kitex。
func checkLegacySMNeverDispatches(t ginkgo.GinkgoTInterface) {
	for _, variant := range []string{"queued", "sent", "unknown", "succeeded"} {
		for _, legacyVersion := range []int{12, 13} {
			ginkgo.By(fmt.Sprintf("I27-C01.%s/v%d：旧 SM 步骤所有恢复入口只读并显示历史待处理", variant, legacyVersion))
			f, target := ksmTarget(t)
			config := platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"}
			p, e := platform.Start(context.Background(), config)
			if e != nil {
				t.Fatal(e)
			}
			request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
			c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
			owner := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"].(string)
			reg := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "legacy-sm", "ownerId": owner, "context": "explicit", "kubeconfig": target}, c, "register", 202)
			cl := awaitLifecycleState(t, p, c, reg["operationId"].(string), "SUCCEEDED")["clusterId"].(string)
			i := clusterCall(t, p, "POST", "/clusters/"+cl+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("lifecycle")}, c, "create", 201)
			id := i["id"].(string)
			accepted := lifecyclePublish(t, p, c, id)
			oid := accepted["operationId"].(string)
			done := awaitLifecycleState(t, p, c, oid, "SUCCEEDED")
			var document map[string]any
			if e = yaml.Unmarshal([]byte(done["resources"].([]any)[0].(map[string]any)["target"].(map[string]any)["yaml"].(string)), &document); e != nil {
				t.Fatal(e)
			}
			raw, _ := json.Marshal(document)
			p.Close(context.Background())
			db, e := sql.Open("sqlite", filepath.Join(config.DataDir, "platform.db"))
			if e != nil {
				t.Fatal(e)
			}
			if _, e = db.Exec(`DROP TABLE IF EXISTS instance_management_confirmations;DROP TABLE historical_resource_observations;DROP TABLE resource_progress;DROP TABLE operation_resources;DROP TABLE version_resources;DROP TABLE instance_resource_identities;DELETE FROM schema_migrations WHERE version>11;`); e != nil {
				t.Fatal(e)
			}
			var creatorColumns int
			if e = db.QueryRow(`SELECT count(*) FROM pragma_table_info('instances') WHERE name='creator_user_id'`).Scan(&creatorColumns); e != nil {
				t.Fatal(e)
			}
			if creatorColumns == 0 {
				legacy, e := os.ReadFile("testdata/legacy/creator-v11.sql")
				if e != nil {
					t.Fatal(e)
				}
				if _, e = db.Exec(string(legacy)); e != nil {
					t.Fatal(e)
				}
			}
			schema, e := os.ReadFile("testdata/legacy/resources-v12.sql")
			if e != nil {
				t.Fatal(e)
			}
			if _, e = db.Exec(string(schema)); e != nil {
				t.Fatal(e)
			}
			uid, state, opState, sent := "", "QUEUED", "CHECKING", false
			if variant != "queued" {
				state = "RESULT_UNCERTAIN"
				opState = "RESULT_UNCERTAIN"
				sent = true
			}
			if variant == "sent" || variant == "succeeded" {
				uid = "old-sm-uid"
			}
			if variant == "succeeded" {
				state = "SUCCEEDED"
				opState = "SUCCEEDED"
			}
			task, e := os.ReadFile("testdata/legacy/task-sm-v12.sql")
			if e != nil {
				t.Fatal(e)
			}
			if _, e = db.Exec(string(task), sql.Named("version", accepted["versionId"]), sql.Named("operation", oid), sql.Named("attempt", done["attemptId"]), sql.Named("instance", id), sql.Named("document", raw), sql.Named("uid", uid), sql.Named("state", state), sql.Named("operationState", opState), sql.Named("possiblySent", sent), sql.Named("currentVersion", func() any {
				if variant == "succeeded" {
					return accepted["versionId"]
				}
				return ""
			}())); e != nil {
				t.Fatal(e)
			}
			if _, e = db.Exec(`INSERT INTO schema_migrations VALUES(12)`); e != nil {
				t.Fatal(e)
			}
			if legacyVersion == 13 {
				legacy, e := os.ReadFile("testdata/legacy/management-v13.sql")
				if e != nil {
					t.Fatal(e)
				}
				if _, e = db.Exec(string(legacy)); e != nil {
					t.Fatal(e)
				}
				if _, e = db.Exec(`INSERT INTO instance_management_confirmations(instance_id,actor_id,confirmed_at,allow_cluster_managers) VALUES(?,?,'2026-10-22T01:00:00Z',0);INSERT INTO schema_migrations VALUES(13)`, id, owner); e != nil {
					t.Fatal(e)
				}
			}
			db.Close()
			f.mu.Lock()
			f.legacySM = map[string]any{"apiVersion": "monitoring.coreos.com/v1", "kind": "ServiceMonitor", "metadata": map[string]any{"name": "old-sm", "namespace": "old-monitoring", "uid": "old-sm-uid"}, "spec": map[string]any{"selector": map[string]any{"matchLabels": map[string]any{"legacy": "frozen"}}, "endpoints": []any{map[string]any{"port": "metrics"}}}}
			before := f.writes
			f.mu.Unlock()
			for restart := 0; restart < 2; restart++ {
				p, e = platform.Start(context.Background(), config)
				if e != nil {
					t.Fatal(e)
				}
				op := clusterCall(t, p, "POST", "/operations/"+oid+"/refresh", nil, c, "refresh", 200)
				history := clusterCall(t, p, "GET", "/operations?instanceId="+id, nil, c, "", 200)["operations"].([]any)
				if len(history) != 1 || history[0].(map[string]any)["operationId"] != oid || history[0].(map[string]any)["resources"].([]any)[1].(map[string]any)["historicalPending"] != (variant != "succeeded") {
					t.Fatal("按 instanceID 无法盘点旧 SM 原任务", history)
				}
				rs := op["resources"].([]any)
				if len(rs) != 2 {
					t.Fatal("旧资源集合被替换", op)
				}
				r := rs[1].(map[string]any)
				wantState := "HISTORICAL_PENDING"
				if variant == "succeeded" {
					wantState = "SUCCEEDED"
				}
				if r["state"] != state || r["confirmedUid"] != uid || r["possiblySent"] != sent || r["historicalPending"] != (variant != "succeeded") || r["observation"] == nil || op["state"] != wantState {
					t.Fatal("历史事实被覆盖或未显式待处理", op)
				}
				if rs[0].(map[string]any)["state"] != "SUCCEEDED" {
					t.Fatal("成功组件被操作级状态覆盖", rs)
				}
				for _, action := range []string{"retry", "continue-unsent", "cancel"} {
					clusterCall(t, p, "POST", "/operations/"+oid+"/"+action, lifecycleRevision(t, p, c, oid), c, action, 409)
				}
				attempts := clusterCall(t, p, "GET", "/operations/"+oid+"/attempts", nil, c, "", 200)["attempts"].([]any)
				if len(attempts) != 1 || attempts[0].(map[string]any)["state"] != opState || attempts[0].(map[string]any)["possiblySent"] != true {
					t.Fatal("旧任务尝试被改写", attempts)
				}
				if restart == 0 && variant != "queued" {
					ginkgo.By("I28-R04/I28-U08：不同名称的旧成功或结果不明 SM 阻断新启用，不按 pending 筛选")
					cfg := ksmConfig("lifecycle")
					cfg["serviceMonitor"] = map[string]any{"enabled": true, "interval": "30s", "scrapeTimeout": "10s"}
					clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": cfg}, c, "new-intent", 200)
					denied := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 2}, c, "legacy-block", 409)
					if !strings.Contains(denied["message"].(string), "旧平台 ServiceMonitor") {
						t.Fatal("新启用没有经过全部历史SM门禁", denied)
					}
				}
				p.Close(context.Background())
			}
			f.mu.Lock()
			writes, smWrites, reads := f.writes, f.smWrites, f.smReads
			paths := append([]string{}, f.smReadPaths...)
			f.mu.Unlock()
			for _, path := range paths {
				if path != "/apis/monitoring.coreos.com/v1/namespaces/old-monitoring/servicemonitors/old-sm" {
					t.Fatal("旧资源读取采用了当前 namespace", paths)
				}
			}
			if writes != before || smWrites != 0 || reads < 2 {
				t.Fatal("旧任务恢复非只读", before, writes, smWrites, reads)
			}
			if legacyVersion == 13 {
				db, e = sql.Open("sqlite", filepath.Join(config.DataDir, "platform.db"))
				if e != nil {
					t.Fatal(e)
				}
				var actor, at string
				var allow int
				if e = db.QueryRow(`SELECT actor_id,confirmed_at,allow_cluster_managers FROM instance_management_confirmations WHERE instance_id=?`, id).Scan(&actor, &at, &allow); e != nil || actor != owner || at != "2026-10-22T01:00:00Z" || allow != 0 {
					t.Fatal("旧 v13 确认记录被改写", e, actor, at, allow)
				}
				db.Close()
			}
		}
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
		wantResourceState := wantState
		if variant == "known-uid" {
			wantResourceState = "SUCCEEDED"
		}
		if progress["state"] != wantResourceState || progress["confirmedUid"] != wantUID || progress["possiblySent"] != true {
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

// I27-U02/C02：Agent 发布、删除使用同一单 CR 进度，存储结论与 CR 执行结果独立。
func checkAgentSingleCRResourceProgress(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I27-U02/C02：Agent 原 namespace/name/UID 和 UID/RV Foreground 删除通过公开 API 保留")
	p, c, cluster, _, f := agentSafetyFixture(t, nil)
	var writes, smWrites atomic.Int64
	f.mu.Lock()
	f.hook = func(w http.ResponseWriter, r *http.Request) bool {
		if strings.Contains(r.URL.Path, "/servicemonitors") && r.Method != "GET" {
			smWrites.Add(1)
		}
		if strings.Contains(r.URL.Path, "/prometheusagents") && r.Method != "GET" {
			writes.Add(1)
		}
		if r.Method == "DELETE" && strings.HasSuffix(r.URL.Path, "/prometheusagents/progress-agent") {
			var body map[string]any
			if json.NewDecoder(r.Body).Decode(&body) != nil {
				w.WriteHeader(400)
				return true
			}
			pre := body["preconditions"].(map[string]any)
			if pre["uid"] != "uid-monitoring/progress-agent" || pre["resourceVersion"] != "1" || body["propagationPolicy"] != "Foreground" {
				w.WriteHeader(409)
				return true
			}
			f.mu.Lock()
			delete(f.objects, "monitoring/progress-agent")
			f.mu.Unlock()
			w.Write([]byte(`{}`))
			return true
		}
		return false
	}
	f.mu.Unlock()
	cfg := agentConfig("progress-agent", "monitoring")
	cfg["storage"] = map[string]any{"mode": "emptyDir", "whenScaled": "Retain", "whenDeleted": "Retain"}
	instance := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": cfg}, c, "create-agent", 201)
	id := instance["id"].(string)
	preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "preview-agent", 200)
	pub := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"draftRevision": 1, "previewId": preview["previewId"]}, c, "publish-agent", 202)
	awaitLifecycleState(t, p, c, pub["operationId"].(string), "SUCCEEDED")
	deletion := clusterCall(t, p, "POST", "/instances/"+id+"/deletions", deletionConfirmation(t, p, c, id), c, "delete-agent", 202)
	awaitLifecycleState(t, p, c, deletion["operationId"].(string), "SUCCEEDED")
	for _, op := range []map[string]any{pub, deletion} {
		current := clusterCall(t, p, "GET", "/operations/"+op["operationId"].(string), nil, c, "", 200)
		rs := current["resources"].([]any)
		if len(rs) != 1 {
			t.Fatal(current)
		}
		r := rs[0].(map[string]any)
		target := r["target"].(map[string]any)
		if target["kind"] != "PrometheusAgent" || target["namespace"] != "monitoring" || target["name"] != "progress-agent" || r["confirmedUid"] != "uid-monitoring/progress-agent" || r["possiblySent"] != true || r["state"] != "SUCCEEDED" {
			t.Fatal(r)
		}
	}
	if writes.Load() != 2 || smWrites.Load() != 0 {
		t.Fatal("Agent 单 CR 生命周期存在额外写入", writes.Load(), smWrites.Load())
	}
}
