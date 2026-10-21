package platform_test

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/platform"
	"github.com/fjsxy/monitor-mgr/internal/teststeps"
	ginkgo "github.com/onsi/ginkgo/v2"
	metavalidation "k8s.io/apimachinery/pkg/apis/meta/v1/validation"
	"k8s.io/apimachinery/pkg/util/validation/field"
)

// REG21-AGENT-NAME-001：创建/保存/预览经真实HTTP→Kitex，固定Operator派生标签由同版Kubernetes校验。
var _ = ginkgo.It("TestAgentRevisionNameBudgetHTTP", func() {
	t := ginkgo.GinkgoT()
	// 接下来REG21-AGENT-NAME-001：创建/保存/预览经真实HTTP→Kitex，固定Operator派生标签由同版Kubernetes校验。
	ginkgo.By("REG21-AGENT-NAME-001：创建/保存/预览经真实HTTP→Kitex，固定Operator派生标签由同版Kubernetes校验。")

	_, target := agentTarget(t)
	p, cookie, owner := clusterPlatform(t)
	op := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "name-budget", "ownerId": owner, "context": "explicit", "kubeconfig": target}, cookie, "register", 202)
	cluster := awaitClusterOperation(t, p, cookie, op["operationId"].(string))["clusterId"].(string)
	for index, tc := range []struct {
		name       string
		length     int
		shards     int
		lastSuffix string
		valid      bool
	}{
		{"single-41", 41, 1, "", true},
		{"single-42", 42, 1, "", false},
		{"original-44", 44, 1, "", false},
		{"two-33", 33, 2, "-shard-1", true},
		{"two-34", 34, 2, "-shard-1", false},
		{"ten-33", 33, 10, "-shard-9", true},
		{"eleven-32", 32, 11, "-shard-10", true},
		{"eleven-33", 33, 11, "-shard-10", false},
	} {
		teststeps.Step(tc.name, func() {
			name := strings.Repeat(string(rune('a'+index)), tc.length)
			cfg := agentConfig(name, "monitoring")
			cfg["shards"] = tc.shards
			// 关键：10位hash来自固定StatefulSet controller的uint32上界，不沿用有时更短的样本hash。
			label := "prom-agent-" + name + tc.lastSuffix + "-2147483647"
			validLabel := len(metavalidation.ValidateLabels(map[string]string{"controller-revision-hash": label}, field.NewPath("metadata", "labels"))) == 0
			if validLabel != tc.valid {
				t.Fatal("固定Kubernetes标签边界与独立用例预期不符")
			}
			want := 422
			if tc.valid {
				want = 201
			}
			created := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": cfg}, cookie, "create-"+tc.name, want)
			if !tc.valid {
				if created["code"] != "INVALID_INPUT" || !strings.Contains(fmt.Sprint(created["fieldErrors"]), "agent.name") {
					t.Fatal("超长名称必须返回agent.name字段错误")
				}
				return
			}
			id := created["id"].(string)
			draft := clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"agent": cfg, "expectedDraftRevision": 1}, cookie, "save-"+tc.name, 200)
			if draft["agent"].(map[string]any)["name"] != name {
				t.Fatal("合法用户名称不能截断或替换")
			}
			clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 2}, cookie, "preview-"+tc.name, 200)
			cfg["name"] = name + "x"
			clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"agent": cfg, "expectedDraftRevision": 2}, cookie, "invalid-save-"+tc.name, 422)
		})
	}
})

// 旧库只读与已排队worker是独立入口：保留原名称/版本，但任何新派发都不能绕过预算。
var _ = ginkgo.It("TestAgentRevisionNameBudgetLegacyAndWorker", func() {
	t := ginkgo.GinkgoT()
	// 接下来旧库只读与已排队worker是独立入口：保留原名称/版本，但任何新派发都不能绕过预算。
	ginkgo.By("旧库只读与已排队worker是独立入口：保留原名称/版本，但任何新派发都不能绕过预算。")

	f, target := agentTarget(t)
	var writes atomic.Int32
	f.hook = func(w http.ResponseWriter, r *http.Request) bool {
		if strings.Contains(r.URL.Path, "/prometheusagents") && r.Method != "GET" {
			writes.Add(1)
		}
		if r.Method == "DELETE" && strings.Contains(r.URL.Path, "/prometheusagents/") {
			var body map[string]any
			json.NewDecoder(r.Body).Decode(&body)
			if body["propagationPolicy"] != "Foreground" || body["preconditions"].(map[string]any)["uid"] != "legacy-original-agent-uid" {
				t.Error("旧CR删除没有保留原UID/Foreground条件")
				w.WriteHeader(409)
				return true
			}
			f.mu.Lock()
			delete(f.objects, "monitoring/"+r.URL.Path[strings.LastIndex(r.URL.Path, "/")+1:])
			f.mu.Unlock()
			json.NewEncoder(w).Encode(map[string]any{})
			return true
		}
		return false
	}
	entered, release := make(chan struct{}), make(chan struct{})
	var enteredOnce, releaseOnce sync.Once
	t.Cleanup(func() { releaseOnce.Do(func() { close(release) }) })
	dir := t.TempDir()
	p, err := platform.Start(context.Background(), platform.Config{DataDir: dir, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", OperationBoundary: func(stage, id string) error {
		if stage == "beforeDispatch" {
			enteredOnce.Do(func() { close(entered) })
			<-release
		}
		return nil
	}})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { releaseOnce.Do(func() { close(release) }); p.Close(context.Background()) })
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	cookie := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	owner := readJSON(t, p, "/session", cookie)["account"].(map[string]any)["id"].(string)
	reg := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "legacy", "ownerId": owner, "context": "explicit", "kubeconfig": target}, cookie, "register", 202)
	cluster := awaitClusterOperation(t, p, cookie, reg["operationId"].(string))["clusterId"].(string)
	cfg := agentConfig("legacy-agent", "monitoring")
	cfg["storage"].(map[string]any)["mode"] = "emptyDir"
	delete(cfg["storage"].(map[string]any), "capacity")
	i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": cfg}, cookie, "create", 201)
	id := i["id"].(string)
	preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, cookie, "preview", 200)
	op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, cookie, "publish", 202)
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		t.Fatal("未到真实持久化worker派发边界")
	}
	// 只在数据库系统边界构造升级前快照；生产HTTP没有修改不可变版本的入口。
	longName := strings.Repeat("l", 44)
	cfg["name"] = longName
	raw, _ := json.Marshal(cfg)
	db, err := sql.Open("sqlite", filepath.Join(dir, "platform.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	for _, update := range []struct {
		sql  string
		args []any
	}{
		{`UPDATE instances SET name=? WHERE id=?`, []any{longName, id}},
		{`UPDATE drafts SET config=? WHERE instance_id=?`, []any{raw, id}},
		{`UPDATE publication_versions SET config=? WHERE instance_id=?`, []any{raw, id}},
	} {
		if _, err = db.Exec(update.sql, update.args...); err != nil {
			t.Fatal(err)
		}
	}
	releaseOnce.Do(func() { close(release) })
	result := awaitClusterOperation(t, p, cookie, op["operationId"].(string))
	if result["state"] != "FAILED" || writes.Load() != 0 {
		t.Fatal("旧queued版本绕过名称预算并写入目标")
	}
	draft := clusterCall(t, p, "GET", "/instances/"+id+"/draft", nil, cookie, "", 200)
	versions := clusterCall(t, p, "GET", "/instances/"+id+"/versions", nil, cookie, "", 200)["versions"].([]any)
	if len(versions) != 1 {
		t.Fatal("旧发布版本丢失")
	}
	version := versions[0].(map[string]any)
	if draft["agent"].(map[string]any)["name"] != longName || version["agent"].(map[string]any)["name"] != longName || !strings.Contains(version["yaml"].(string), longName) {
		t.Fatal("只读历史被新预算拒绝或静默改名")
	}
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"agent": cfg, "expectedDraftRevision": 1}, cookie, "legacy-save", 422)
	clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, cookie, "legacy-preview", 422)
	clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, cookie, "legacy-publish", 422)
	// 旧CR删除仍按原UID和冻结配置执行；新写入预算不能变成清理阻断。
	var frozen []byte
	if err = db.QueryRow(`SELECT effective_spec FROM publication_versions WHERE instance_id=?`, id).Scan(&frozen); err != nil {
		t.Fatal(err)
	}
	var spec map[string]any
	if err = json.Unmarshal(frozen, &spec); err != nil {
		t.Fatal(err)
	}
	const originalUID = "legacy-original-agent-uid"
	f.mu.Lock()
	f.objects["monitoring/"+longName] = map[string]any{"apiVersion": "monitoring.coreos.com/v1alpha1", "kind": "PrometheusAgent", "metadata": map[string]any{"name": longName, "namespace": "monitoring", "uid": originalUID, "resourceVersion": "1", "generation": 1}, "spec": spec}
	f.mu.Unlock()
	if _, err = db.Exec(`UPDATE instances SET uid=?,lifecycle='DEPLOYED',current_version_id=? WHERE id=?`, originalUID, version["versionId"], id); err != nil {
		t.Fatal(err)
	}
	dp := clusterCall(t, p, "POST", "/instances/"+id+"/deletion-previews", map[string]any{}, cookie, "", 200)
	del := clusterCall(t, p, "POST", "/instances/"+id+"/deletions", map[string]any{"versionId": version["versionId"], "previewId": dp["previewId"]}, cookie, "delete-legacy", 202)
	if awaitClusterOperation(t, p, cookie, del["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("新名称预算阻断旧CR的原UID清理")
	}
})
