package platform_test

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/fjsxy/monitor-mgr/internal/platform"
)

// F04：精确v5旧库保留由HTTP/Kitex创建的密文、会话、固定版本、已成功历史和未发保护。
func TestF04LifecycleSchema5Upgrade(t *testing.T) {
	for _, fail := range []bool{false, true} {
		name := "success"
		if fail {
			name = "failure"
		}
		t.Run(name, func(t *testing.T) {
			f, target := ksmTarget(t)
			_ = f
			dir := t.TempDir()
			var paused atomic.Bool
			cfg := platform.Config{DataDir: dir, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", OperationBoundary: func(stage, id string) error {
				if paused.Load() && stage == "beforeDispatch" {
					return errors.New("受控未派发退出")
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
			o := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "upgrade", "ownerId": owner, "context": "explicit", "kubeconfig": target}, c, "register", 202)
			cl := awaitClusterOperation(t, p, c, o["operationId"].(string))["clusterId"].(string)
			i := clusterCall(t, p, "POST", "/clusters/"+cl+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("lifecycle")}, c, "create", 201)
			id := i["id"].(string)
			first := lifecyclePublish(t, p, c, id)
			awaitLifecycleState(t, p, c, first["operationId"].(string), "SUCCEEDED")
			next := ksmConfig("lifecycle")
			next["resources"].(map[string]any)["requestCPU"] = "200m"
			clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": next}, c, "edit", 200)
			preview := ksmPreview(t, p, c, id, 2)
			paused.Store(true)
			pending := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 2, "overwriteDrift": false}, c, "pending", 202)
			oid := pending["operationId"].(string)
			awaitLifecycleState(t, p, c, oid, "CHECKING")
			p.Close(context.Background())
			key, e := os.ReadFile(filepath.Join(dir, "master.key"))
			if e != nil {
				t.Fatal(e)
			}
			db, e := sql.Open("sqlite", filepath.Join(dir, "platform.db"))
			if e != nil {
				t.Fatal(e)
			}
			// 关键：裁除所有v6关系/字段，留下精确v5形状；不把v6残余伪装成旧库。
			_, e = db.Exec(`PRAGMA foreign_keys=OFF;DROP TABLE seen_resources;DROP TABLE deletion_operations;DROP TABLE operation_lifecycle;ALTER TABLE execution_attempts DROP COLUMN revision;ALTER TABLE execution_attempts DROP COLUMN sequence;ALTER TABLE execution_attempts DROP COLUMN actor_id;ALTER TABLE execution_attempts DROP COLUMN message;DELETE FROM schema_migrations WHERE version>5;`)
			if e != nil {
				t.Fatal(e)
			}
			var historicalSpec []byte
			if e = db.QueryRow(`SELECT effective_spec FROM publication_versions WHERE id=?`, pending["versionId"]).Scan(&historicalSpec); e != nil {
				t.Fatal(e)
			}
			var oldMapping map[string]any
			if json.Unmarshal(historicalSpec, &oldMapping) != nil {
				t.Fatal("旧冻结映射不可读")
			}
			oldMapping["podResources"].(map[string]any)["requests"].(map[string]any)["cpu"] = "300m"
			historicalSpec, _ = json.Marshal(oldMapping)
			if _, e = db.Exec(`UPDATE publication_versions SET effective_spec=? WHERE id=?`, historicalSpec, pending["versionId"]); e != nil {
				t.Fatal(e)
			}
			if _, e = db.Exec(`UPDATE publication_previews SET desired_spec=? WHERE id=?`, historicalSpec, preview["previewId"]); e != nil {
				t.Fatal(e)
			}
			if fail {
				if _, e = db.Exec(`CREATE TABLE seen_resources(controlled_failure TEXT)`); e != nil {
					t.Fatal(e)
				}
			}
			db.Close()
			paused.Store(false)
			if fail {
				if bad, err := platform.Start(context.Background(), cfg); err == nil {
					bad.Close(context.Background())
					t.Fatal("受控迁移失败未阻止启动")
				}
				db, e = sql.Open("sqlite", filepath.Join(dir, "platform.db"))
				if e != nil {
					t.Fatal(e)
				}
				var version, protected, columns int
				db.QueryRow(`SELECT max(version) FROM schema_migrations`).Scan(&version)
				db.QueryRow(`SELECT count(*) FROM instance_protections WHERE operation_id=?`, oid).Scan(&protected)
				db.QueryRow(`SELECT count(*) FROM pragma_table_info('execution_attempts') WHERE name='revision'`).Scan(&columns)
				snapshots, _ := filepath.Glob(filepath.Join(dir, "backups", "schema-5-to-6-*"))
				if version != 5 || protected != 1 || columns != 0 || len(snapshots) == 0 {
					t.Fatal("迁移失败改变旧库或丢失恢复快照", version, protected, columns, len(snapshots))
				}
				for _, path := range snapshots {
					info, _ := os.Stat(path)
					if info.Mode().Perm() != 0600 {
						t.Fatal("恢复快照权限错误")
					}
				}
				db.Exec(`DROP TABLE seen_resources`)
				db.Close()
			}
			p, e = platform.Start(context.Background(), cfg)
			if e != nil {
				t.Fatal(e)
			}
			defer p.Close(context.Background())
			after, e := os.ReadFile(filepath.Join(dir, "master.key"))
			if e != nil || !bytes.Equal(key, after) {
				t.Fatal("升级替换原密钥")
			}
			clusterCall(t, p, "GET", "/session", nil, c, "", 200)
			clusterCall(t, p, "GET", "/clusters/"+cl+"/namespaces", nil, c, "", 200)
			versions := clusterCall(t, p, "GET", "/instances/"+id+"/versions", nil, c, "", 200)["versions"].([]any)
			if len(versions) != 2 {
				t.Fatal("升级丢失版本历史")
			}
			version := clusterCall(t, p, "GET", "/instances/"+id+"/versions/"+pending["versionId"].(string), nil, c, "", 200)
			if !strings.Contains(version["yaml"].(string), "300m") {
				t.Fatal("历史版本YAML被当前mapper重算", version["yaml"])
			}
			recovered := awaitLifecycleState(t, p, c, oid, "WAITING_CONFIRMATION")
			if recovered["versionId"] != pending["versionId"] {
				t.Fatal(recovered)
			}
			continued := clusterCall(t, p, "POST", "/operations/"+oid+"/continue-unsent", lifecycleRevision(t, p, c, oid), c, "continue", 202)
			if continued["attemptId"] == pending["attemptId"] {
				t.Fatal("恢复未建立新尝试")
			}
			awaitLifecycleState(t, p, c, oid, "SUCCEEDED")
			attempts := clusterCall(t, p, "GET", "/operations/"+oid+"/attempts", nil, c, "", http.StatusOK)["attempts"].([]any)
			f.mu.Lock()
			cpu := f.cr["spec"].(map[string]any)["podResources"].(map[string]any)["requests"].(map[string]any)["cpu"]
			f.mu.Unlock()
			if cpu != "300m" {
				t.Fatal("旧操作写入了新mapper结果", cpu)
			}
			if len(attempts) != 2 {
				t.Fatal("升级或恢复丢失尝试", attempts)
			}
		})
	}
}
