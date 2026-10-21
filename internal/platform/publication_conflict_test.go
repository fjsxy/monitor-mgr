package platform_test

import (
	"context"
	"database/sql"
	"fmt"
	"path/filepath"
	"sync/atomic"

	"github.com/fjsxy/monitor-mgr/internal/platform"
	"github.com/fjsxy/monitor-mgr/internal/teststeps"
	ginkgo "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
)

// checkPublicationStatusConflict 在真实 HTTP/Kitex Worker 的读取与 PUT 间改变目标，锁定集群发现的并发时序。
func checkPublicationStatusConflict(t ginkgo.GinkgoTInterface) {
	for _, mode := range []string{"status", "spec", "uid", "permission", "credential", "continuous"} {
		teststeps.Step("条件写冲突："+mode, func() {
			f, kubeconfig := ksmTarget(t)
			dir := t.TempDir()
			var armed atomic.Bool
			var sends atomic.Int32
			cfg := platform.Config{DataDir: dir, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"}
			cfg.OperationBoundary = func(stage, id string) error {
				if !armed.Load() {
					return nil
				}
				if stage == "afterSendMarker" {
					n := sends.Add(1)
					if n == 1 || mode == "continuous" {
						f.mu.Lock()
						f.cr["metadata"].(map[string]any)["resourceVersion"] = fmt.Sprintf("concurrent-%d", n)
						f.cr["status"] = map[string]any{"availablePods": 0}
						if mode == "spec" {
							f.cr["spec"].(map[string]any)["enabled"] = false
						}
						if mode == "uid" {
							f.cr["metadata"].(map[string]any)["uid"] = "replacement-uid"
						}
						f.mu.Unlock()
					}
				}
				// 关键：明确收到拒绝后才改变平台授权，不能在持有派发保护时等待撤权。
				if stage == "afterRejectedConflict" && (mode == "permission" || mode == "credential") {
					db, e := sql.Open("sqlite", filepath.Join(dir, "platform.db"))
					if e != nil {
						return e
					}
					defer db.Close()
					statement := "UPDATE accounts SET enabled=0"
					if mode == "credential" {
						statement = "UPDATE clusters SET credential_revision=credential_revision+1"
					}
					_, e = db.Exec(statement)
					return e
				}
				return nil
			}
			p, e := platform.Start(context.Background(), cfg)
			if e != nil {
				t.Fatal(e)
			}
			t.Cleanup(func() { p.Close(context.Background()) })
			request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
			c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
			owner := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"]
			reg := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "conflict", "ownerId": owner, "context": "explicit", "kubeconfig": kubeconfig}, c, "register", 202)
			registered := awaitClusterOperation(t, p, c, reg["operationId"].(string))
			if registered["state"] != "SUCCEEDED" {
				t.Fatal(registered)
			}
			created := clusterCall(t, p, "POST", "/clusters/"+registered["clusterId"].(string)+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("conflict")}, c, "create", 201)
			id := created["id"].(string)
			op := lifecyclePublish(t, p, c, id)
			awaitLifecycleState(t, p, c, op["operationId"].(string), "SUCCEEDED")
			desired := ksmConfig("conflict")
			desired["resources"].(map[string]any)["requestCPU"] = "200m"
			clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": desired}, c, "draft", 200)
			preview := ksmPreview(t, p, c, id, 2)
			armed.Store(true)
			op = clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"draftRevision": 2, "previewId": preview["previewId"], "overwriteDrift": false}, c, "publish-conflict", 202)
			// 撤权会同时使原 Cookie 失效，使用独立数据库只读终态，不能把 401 当作 Worker 结果。
			db, e := sql.Open("sqlite", filepath.Join(dir, "platform.db"))
			if e != nil {
				t.Fatal(e)
			}
			defer db.Close()
			state := waitConflictState(t, db, op["operationId"].(string))
			expected := "FAILED"
			expectedSends := int32(1)
			expectedWrites := 1
			if mode == "status" {
				expected = "SUCCEEDED"
				expectedSends = 2
				expectedWrites = 2
			}
			if mode == "continuous" {
				expectedSends = 3
			}
			if state != expected || sends.Load() != expectedSends {
				t.Fatalf("%s：state=%s sends=%d，预期 %s/%d", mode, state, sends.Load(), expected, expectedSends)
			}
			var attempts int
			if e = db.QueryRow("SELECT COUNT(*) FROM execution_attempts WHERE operation_id=?", op["operationId"]).Scan(&attempts); e != nil || attempts != 1 {
				t.Fatalf("冲突不得创建新 attempt：%d %v", attempts, e)
			}
			f.mu.Lock()
			defer f.mu.Unlock()
			if f.writes != expectedWrites {
				t.Fatalf("成功 mutation=%d，预期 %d", f.writes, expectedWrites)
			}
			if mode == "status" && (f.cr["metadata"].(map[string]any)["uid"] != "created-uid" || f.cr["spec"].(map[string]any)["podResources"].(map[string]any)["requests"].(map[string]any)["cpu"] != "200m" || f.cr["status"].(map[string]any)["availablePods"] != float64(0)) {
				t.Fatal("未保留原 UID、当前 status 或冻结 spec")
			}
		})
	}
}

// waitConflictState 轮询真实 Worker 持久终态，避免在已撤权场景依赖会话读取。
func waitConflictState(t ginkgo.GinkgoTInterface, db *sql.DB, id string) string {
	t.Helper()
	var state string
	Eventually(func() string {
		if e := db.QueryRow("SELECT state FROM operations WHERE id=?", id).Scan(&state); e != nil {
			t.Fatal(e)
		}
		return state
	}, "5s", "10ms").Should(SatisfyAny(Equal("SUCCEEDED"), Equal("FAILED")))
	return state
}
