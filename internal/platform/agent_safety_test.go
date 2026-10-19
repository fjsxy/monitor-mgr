package platform_test

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/platform"
)

// 安全回归使用同一真实HTTP/Kitex/SQLite边界，派发屏障不冒充网络超时。
func agentSafetyFixture(t *testing.T, boundary func(string, string) error) (*platform.Runtime, *http.Cookie, string, string, *agentFixture) {
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
func TestAgentFixedCapacityRevalidated(t *testing.T) {
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
func TestAgentFixedCapacityPossiblySent(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	var once, released sync.Once
	p, c, cluster, _, _ := agentSafetyFixture(t, func(stage, id string) error {
		if stage == "afterSendMarker" {
			once.Do(func() { close(entered) })
			<-release
		}
		return nil
	})
	defer released.Do(func() { close(release) })
	cfg := agentConfig("sent-capacity", "monitoring")
	i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": cfg}, c, "create", 201)
	id := i["id"].(string)
	preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "preview", 200)
	op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "publish", 202)
	select {
	case <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("未到达持久可能已发屏障")
	}
	cfg["storage"].(map[string]any)["capacity"] = "2Gi"
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "agent": cfg}, c, "change-after-marker", 409)
	released.Do(func() { close(release) })
	if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("原冻结版本发布失败")
	}
}

// REG18-HEADERS-009：合法Agent remoteWrite.headers任意值必须投影脱敏，原值仍参与漂移指纹。
func TestAgentHeadersReadProjection(t *testing.T) {
	p, c, cluster, dir, f := agentSafetyFixture(t, nil)
	i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": agentConfig("headers", "monitoring")}, c, "create", 201)
	id := i["id"].(string)
	preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "preview", 200)
	op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "publish", 202)
	if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("前置发布失败")
	}
	values := []string{"header-authorization-secret", "header-api-key-secret", "arbitrary-header-secret"}
	f.mu.Lock()
	cr := f.objects["monitoring/headers"]
	endpoint := cr["spec"].(map[string]any)["remoteWrite"].([]any)[0].(map[string]any)
	endpoint["headers"] = map[string]any{"Authorization": values[0], "X-API-Key": values[1], "Unpredictable-Name": values[2]}
	endpoint["url"] = "https://remote-user:remote-password@example.invalid/write"
	values = append(values, "remote-password", "legacy-last-applied-secret", "legacy-status-password")
	meta := cr["metadata"].(map[string]any)
	meta["annotations"] = map[string]any{"preserve": "metadata-original", "kubectl.kubernetes.io/last-applied-configuration": "legacy-last-applied-secret"}
	meta["labels"] = map[string]any{"external": "keep"}
	meta["finalizers"] = []any{"external/finalizer"}
	cr["status"].(map[string]any)["arbitrary"] = "keep"
	cr["status"].(map[string]any)["message"] = "legacy https://user:legacy-status-password@example.invalid/write"
	cr["spec"].(map[string]any)["unsupportedExtra"] = map[string]any{"value": "remove"}
	raw, _ := json.Marshal(cr)
	var outside map[string]any
	json.Unmarshal(raw, &outside)
	outside["metadata"].(map[string]any)["name"] = "outside-headers"
	outside["metadata"].(map[string]any)["uid"] = "outside-headers-uid"
	f.objects["monitoring/outside-headers"] = outside
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/refresh", map[string]any{}, c, "refresh", 200)
	listing := clusterCall(t, p, "GET", "/clusters/"+cluster+"/instances", nil, c, "", 200)
	draftBefore, _ := json.Marshal(clusterCall(t, p, "GET", "/instances/"+id+"/draft", nil, c, "", 200))
	preview = projectionCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "headers-preview", 200)
	// REG17-YAML-PRESERVE.agent-existing：以夹具的明确脱敏预期核非spec保留，避免调用产品投影反算结果。
	expected := publicationJSONDocument(t, raw)
	expected["metadata"].(map[string]any)["annotations"].(map[string]any)["kubectl.kubernetes.io/last-applied-configuration"] = "[敏感内容已隐藏]"
	expected["status"].(map[string]any)["message"] = "[敏感内容已隐藏]"
	right := publicationYAMLDocument(t, preview["rightYAML"].(string))
	assertPublicationNonSpecPreserved(t, right, expected)
	if right["spec"].(map[string]any)["unsupportedExtra"] != nil {
		t.Fatal("Agent右侧保留了未知spec")
	}
	f.mu.Lock()
	afterPreview, _ := json.Marshal(f.objects["monitoring/headers"])
	f.mu.Unlock()
	draftAfter, _ := json.Marshal(clusterCall(t, p, "GET", "/instances/"+id+"/draft", nil, c, "", 200))
	if string(raw) != string(afterPreview) || string(draftBefore) != string(draftAfter) {
		t.Fatal("预览意外改写原Agent或草稿")
	}
	output, _ := json.Marshal([]any{listing, preview})
	for _, secret := range values {
		if strings.Contains(string(output), secret) {
			t.Fatal("header值进入HTTP/RPC/YAML输出")
		}
	}
	db, e := sql.Open("sqlite", filepath.Join(dir, "platform.db"))
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	for _, query := range []string{"SELECT cr FROM instance_observations", "SELECT document FROM discovered_instances", "SELECT target_spec FROM publication_previews"} {
		rows, e := db.Query(query)
		if e != nil {
			t.Fatal(e)
		}
		for rows.Next() {
			var raw []byte
			if rows.Scan(&raw) != nil {
				t.Fatal("缓存不可读")
			}
			for _, secret := range values {
				if strings.Contains(string(raw), secret) {
					t.Fatal("header值进入DB")
				}
			}
		}
		if rows.Err() != nil {
			t.Fatal(rows.Err())
		}
		rows.Close()
	}
	f.mu.Lock()
	endpoint["headers"].(map[string]any)["Unpredictable-Name"] = "changed-arbitrary-header-secret"
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": true}, c, "header-value-changed", 409)
	preview = projectionCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "new-header-preview", 200)
	right = publicationYAMLDocument(t, preview["rightYAML"].(string))
	op = clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": true}, c, "overwrite", 202)
	if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("明确覆盖失败")
	}
	f.mu.Lock()
	sent := append([]byte(nil), f.lastWrite...)
	finalRaw, _ := json.Marshal(f.objects["monitoring/headers"])
	f.mu.Unlock()
	written := publicationJSONDocument(t, sent)
	assertPublicationNonSpecPreserved(t, written, publicationJSONDocument(t, raw))
	written["metadata"].(map[string]any)["annotations"].(map[string]any)["kubectl.kubernetes.io/last-applied-configuration"] = "[敏感内容已隐藏]"
	written["status"].(map[string]any)["message"] = "[敏感内容已隐藏]"
	assertPublicationDocumentEqual(t, right, written)
	if publicationJSONDocument(t, finalRaw)["metadata"].(map[string]any)["annotations"].(map[string]any)["preserve"] != "metadata-original" {
		t.Fatal("脱敏改写目标metadata")
	}
}
