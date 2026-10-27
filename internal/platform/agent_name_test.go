package platform_test

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/platform"
	ginkgo "github.com/onsi/ginkgo/v2"
)

// REG21-AGENT-NAME-001：创建/保存/预览经真实HTTP→Kitex，固定Operator派生标签由同版Kubernetes校验。

// 旧库只读与已排队worker是独立入口：保留原名称/版本，但任何新派发都不能绕过预算。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestAgentRevisionNameBudgetLegacyAndWorker(t ginkgo.GinkgoTInterface) {
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
		// 此历史场景的原名称本来就很长；冻结路径必须同步，不能制造当前身份与原资源错绑。
		{`UPDATE version_resources SET document=json_set(document,'$.metadata.name',?),object_path=collection_path||'/'||? WHERE version_id IN (SELECT id FROM publication_versions WHERE instance_id=?)`, []any{longName, longName, id}},
		{`UPDATE operation_resources SET document=json_set(document,'$.metadata.name',?),object_path=collection_path||'/'||? WHERE operation_id=?`, []any{longName, longName, op["operationId"]}},
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
}
