package platform_test

import (
	"context"
	"database/sql"
	"errors"
	"net/http"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/platform"
)

// deletionConfirmation 通过公开HTTP获取已确认范围，不能用当前version冒充删除预览。
func deletionConfirmation(t *testing.T, p *platform.Runtime, c *http.Cookie, id string) map[string]any {
	t.Helper()
	preview := clusterCall(t, p, "POST", "/instances/"+id+"/deletion-previews", map[string]any{}, c, "", 200)
	return map[string]any{"versionId": preview["versionId"], "previewId": preview["previewId"]}
}

// REG17-DELETE-003：状态自然变化可继续，目标或原UID影响范围变化必须重新预览。
func TestDeletionPreviewBindsTargetAndImpact(t *testing.T) {
	for _, variant := range []string{"status-only", "before-acceptance-target", "before-dispatch-target", "before-dispatch-impact", "same-range-rollout"} {
		t.Run(variant, func(t *testing.T) {
			var armed atomic.Bool
			entered, release := make(chan struct{}), make(chan struct{})
			var once, released sync.Once
			defer released.Do(func() { close(release) })
			p, c, id, f := lifecycleFixture(t, func(stage, id string) error {
				if armed.Load() && stage == "beforeDispatch" {
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
			body := deletionConfirmation(t, p, c, id)
			clusterCall(t, p, "POST", "/instances/"+id+"/deletions", map[string]any{"versionId": pub["versionId"]}, c, "legacy-delete", 422)
			if variant == "before-acceptance-target" {
				f.mu.Lock()
				f.cr["spec"].(map[string]any)["targetNamespace"] = "moved"
				f.mu.Unlock()
				clusterCall(t, p, "POST", "/instances/"+id+"/deletions", body, c, "delete", 409)
			} else {
				armed.Store(true)
				op := clusterCall(t, p, "POST", "/instances/"+id+"/deletions", body, c, "delete", 202)
				select {
				case <-entered:
				case <-time.After(3 * time.Second):
					t.Fatal("删除未进入持久派发屏障")
				}
				f.mu.Lock()
				switch variant {
				case "before-dispatch-target":
					f.cr["spec"].(map[string]any)["targetNamespace"] = "moved"
				case "before-dispatch-impact":
					f.owned["/api/v1/namespaces/moved/pods"] = []any{map[string]any{"metadata": map[string]any{"name": "moved-pod", "namespace": "moved", "uid": "moved-pod-uid", "ownerReferences": []any{map[string]any{"uid": "created-uid", "controller": true}}}}}
				case "same-range-rollout":
					f.owned["/api/v1/namespaces/monitoring/pods"] = []any{map[string]any{"metadata": map[string]any{"name": "rolling", "namespace": "monitoring", "uid": "rolling-new-uid", "ownerReferences": []any{map[string]any{"uid": "rs-uid", "controller": true}}}}}
				}
				f.cr["metadata"].(map[string]any)["resourceVersion"] = "rv-status-new"
				f.cr["status"] = map[string]any{"observedGeneration": 99}
				f.mu.Unlock()
				released.Do(func() { close(release) })
				state := "SUCCEEDED"
				if variant == "before-dispatch-target" || variant == "before-dispatch-impact" {
					state = "FAILED"
				}
				awaitLifecycleState(t, p, c, op["operationId"].(string), state)
			}
			f.mu.Lock()
			deletes := f.deleteRequests
			f.mu.Unlock()
			want := 1
			if variant == "before-acceptance-target" || variant == "before-dispatch-target" || variant == "before-dispatch-impact" {
				want = 0
			}
			if deletes != want {
				t.Fatalf("目标/影响预览边界失效：DELETE=%d，期望%d", deletes, want)
			}
		})
	}
}

// 旧v6未发删除升级后仍受保护，显式再确认只能沿原operation/UID/version建立新attempt。
func TestDeletionPreviewSchema6UnsentCanContinue(t *testing.T) {
	f, target := ksmTarget(t)
	dir := t.TempDir()
	var hold atomic.Bool
	cfg := platform.Config{DataDir: dir, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", OperationBoundary: func(stage, id string) error {
		if hold.Load() && stage == "beforeDispatch" {
			return errors.New("旧版本未发fixture")
		}
		return nil
	}}
	p, e := platform.Start(context.Background(), cfg)
	if e != nil {
		t.Fatal(e)
	}
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	owner := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"].(string)
	registration := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "old-delete", "ownerId": owner, "context": "explicit", "kubeconfig": target}, c, "register", 202)
	cluster := awaitClusterOperation(t, p, c, registration["operationId"].(string))["clusterId"].(string)
	instance := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("lifecycle")}, c, "create", 201)
	id := instance["id"].(string)
	publication := lifecyclePublish(t, p, c, id)
	awaitLifecycleState(t, p, c, publication["operationId"].(string), "SUCCEEDED")
	original := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
	body := deletionConfirmation(t, p, c, id)
	hold.Store(true)
	deletion := clusterCall(t, p, "POST", "/instances/"+id+"/deletions", body, c, "delete", 202)
	oid := deletion["operationId"].(string)
	awaitLifecycleState(t, p, c, oid, "CHECKING")
	p.Close(context.Background())
	db, e := sql.Open("sqlite", filepath.Join(dir, "platform.db"))
	if e != nil {
		t.Fatal(e)
	}
	// 关键：移除v7表、列和版本记录，保持原操作/尝试/保护与加密凭据，构造精确旧shape。
	if _, e = db.Exec(`PRAGMA foreign_keys=OFF;DROP INDEX instance_activity_order;ALTER TABLE instances DROP COLUMN activity_sequence;DROP TABLE instance_activity_counter;DROP TABLE deletion_previews;ALTER TABLE deletion_operations DROP COLUMN preview_id;DELETE FROM schema_migrations WHERE version>6;`); e != nil {
		t.Fatal(e)
	}
	db.Close()
	hold.Store(false)
	p, e = platform.Start(context.Background(), cfg)
	if e != nil {
		t.Fatal(e)
	}
	defer p.Close(context.Background())
	wait := awaitLifecycleState(t, p, c, oid, "WAITING_CONFIRMATION")
	f.mu.Lock()
	deletes := f.deleteRequests
	f.mu.Unlock()
	if deletes != 0 {
		t.Fatal("旧未发删除自动重放")
	}
	revision := lifecycleRevision(t, p, c, oid)
	clusterCall(t, p, "POST", "/operations/"+oid+"/continue-unsent", revision, c, "missing-preview", 422)
	clusterCall(t, p, "POST", "/instances/"+id+"/deletion-previews", map[string]any{}, c, "", 409)
	preview := clusterCall(t, p, "POST", "/instances/"+id+"/deletion-previews", map[string]any{"operationId": oid}, c, "", 200)
	if preview["uid"] != original["uid"] || preview["versionId"] != publication["versionId"] {
		t.Fatal("新预览替换原身份", preview)
	}
	revision["deletePreviewId"] = preview["previewId"]
	next := clusterCall(t, p, "POST", "/operations/"+oid+"/continue-unsent", revision, c, "continue-delete", 202)
	if next["operationId"] != oid || next["versionId"] != publication["versionId"] || next["attemptId"] == wait["attemptId"] {
		t.Fatal("继续未保留原操作", next)
	}
	done := awaitLifecycleState(t, p, c, oid, "SUCCEEDED")
	replay := clusterCall(t, p, "POST", "/operations/"+oid+"/continue-unsent", revision, c, "continue-delete", 202)
	if replay["state"] != done["state"] || replay["revision"] != done["revision"] {
		t.Fatal("删除继续幂等返回旧状态", replay)
	}
	attempts := clusterCall(t, p, "GET", "/operations/"+oid+"/attempts", nil, c, "", 200)["attempts"].([]any)
	if len(attempts) != 2 || attempts[1].(map[string]any)["sequence"] != float64(2) {
		t.Fatal("旧尝试未保留", attempts)
	}
	retained := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
	if retained["uid"] != original["uid"] || retained["currentVersionId"] != "" {
		t.Fatal("删除改写历史身份", retained)
	}
	// 已删除实例隐藏当前版本入口，原版本仍作为不可变历史保留。
	versions := clusterCall(t, p, "GET", "/instances/"+id+"/versions", nil, c, "", 200)["versions"].([]any)
	if len(versions) != 1 || versions[0].(map[string]any)["versionId"] != publication["versionId"] {
		t.Fatal("删除丢失原版本历史", versions)
	}
	// 受限旧库fixture同时证明只读投影没有清除持久的原版本身份。
	db, e = sql.Open("sqlite", filepath.Join(dir, "platform.db"))
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	var persistedVersion string
	if e = db.QueryRow("SELECT current_version_id FROM instances WHERE id=?", id).Scan(&persistedVersion); e != nil || persistedVersion != publication["versionId"] {
		t.Fatal("删除清除了持久版本身份", persistedVersion, e)
	}
}
