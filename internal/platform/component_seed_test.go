package platform_test

import (
	"context"
	"database/sql"
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"github.com/fjsxy/monitor-mgr/internal/platform"
)

// REG18-SHARED-003：从外部API返回的秘密也不能进入平台发现/YAML读模型。
func TestSharedTargetSensitiveReadProjection(t *testing.T) {
	p, c, id, f := ksmPublishedFixture(t)
	cluster := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)["clusterId"].(string)
	f.mu.Lock()
	f.cr = map[string]any{"apiVersion": "monitoring.fjsxy.github.io/v1alpha1", "kind": "KubeStateMetrics", "metadata": map[string]any{"name": "outside", "uid": "outside-uid", "annotations": map[string]any{"kubectl.kubernetes.io/last-applied-configuration": "inline-secret-value"}}, "spec": map[string]any{"targetNamespace": "monitoring", "bearerToken": "inline-secret-value", "remoteWrite": []any{map[string]any{"url": "https://user:inline-secret-value@example.invalid/write"}}}}
	f.mu.Unlock()
	list := clusterCall(t, p, "GET", "/clusters/"+cluster+"/instances", nil, c, "", 200)
	raw, _ := json.Marshal(list)
	if strings.Contains(string(raw), "inline-secret-value") {
		t.Fatal("目标敏感内容泄漏到HTTP发现/YAML")
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if !strings.Contains(f.cr["metadata"].(map[string]any)["annotations"].(map[string]any)["kubectl.kubernetes.io/last-applied-configuration"].(string), "inline-secret-value") {
		t.Fatal("脱敏修改了真实目标metadata")
	}
}

// REG18-SHARED-001：真实v4旧库预览必须升级成安全指纹，且原previewId仍能发布。
func TestSharedSchema4PreviewSurvivesUpgrade(t *testing.T) {
	_, target := ksmTarget(t)
	dir := t.TempDir()
	cfg := platform.Config{DataDir: dir, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"}
	p, e := platform.Start(context.Background(), cfg)
	if e != nil {
		t.Fatal(e)
	}
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	owner := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"].(string)
	op := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "legacy", "ownerId": owner, "context": "explicit", "kubeconfig": target}, c, "register", 202)
	cl := awaitClusterOperation(t, p, c, op["operationId"].(string))["clusterId"].(string)
	i := clusterCall(t, p, "POST", "/clusters/"+cl+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("legacy")}, c, "create", 201)
	id := i["id"].(string)
	preview := ksmPreview(t, p, c, id, 1)
	p.Close(context.Background())
	db, e := sql.Open("sqlite", filepath.Join(dir, "platform.db"))
	if e != nil {
		t.Fatal(e)
	}
	// 精确恢复v4形状；所有真实业务数据由公开HTTP/Kitex创建。
	var cols int
	db.QueryRow(`SELECT count(*) FROM pragma_table_info('instances') WHERE name='cr_namespace'`).Scan(&cols)
	if cols > 0 {
		_, e = db.Exec(`PRAGMA foreign_keys=OFF; DROP TABLE deletion_previews;DROP TABLE seen_resources; DROP TABLE deletion_operations; DROP TABLE operation_lifecycle; ALTER TABLE execution_attempts DROP COLUMN revision; ALTER TABLE execution_attempts DROP COLUMN sequence; ALTER TABLE execution_attempts DROP COLUMN actor_id; ALTER TABLE execution_attempts DROP COLUMN message; CREATE TABLE legacy_instances(id TEXT PRIMARY KEY,cluster_id TEXT NOT NULL REFERENCES clusters(id),component TEXT NOT NULL,name TEXT NOT NULL,target_namespace TEXT NOT NULL,uid TEXT NOT NULL DEFAULT '',lifecycle TEXT NOT NULL DEFAULT 'DRAFT',current_version_id TEXT NOT NULL DEFAULT '',activity_at TEXT NOT NULL,UNIQUE(cluster_id,component,name)); INSERT INTO legacy_instances SELECT id,cluster_id,component,name,target_namespace,uid,lifecycle,current_version_id,activity_at FROM instances; DROP TABLE instances; ALTER TABLE legacy_instances RENAME TO instances; DROP TABLE instance_source_observations; DELETE FROM schema_migrations WHERE version>4;`)
		if e != nil {
			t.Fatal(e)
		}
	}
	// v4首次创建预览绑定不存在，而不是把当前目标重算成有效。
	if _, e = db.Exec(`UPDATE publication_previews SET target_spec='null' WHERE id=?`, preview["previewId"]); e != nil {
		t.Fatal(e)
	}
	// REG18-SHARED-002：迁移中途失败须整体回滚，而不是删除旧预览解围。
	if _, e = db.Exec(`UPDATE publication_previews SET target_spec='invalid-legacy-json' WHERE id=?`, preview["previewId"]); e != nil {
		t.Fatal(e)
	}
	db.Close()
	if bad, err := platform.Start(context.Background(), cfg); err == nil {
		bad.Close(context.Background())
		t.Fatal("受损旧预览未阻止迁移")
	}
	db, e = sql.Open("sqlite", filepath.Join(dir, "platform.db"))
	if e != nil {
		t.Fatal(e)
	}
	var oldVersion int
	db.QueryRow(`SELECT max(version) FROM schema_migrations`).Scan(&oldVersion)
	var nsColumns int
	db.QueryRow(`SELECT count(*) FROM pragma_table_info('instances') WHERE name='cr_namespace'`).Scan(&nsColumns)
	backups, _ := filepath.Glob(filepath.Join(dir, "backups", "schema-4-to-*-*"))
	if oldVersion != 4 || nsColumns != 0 || len(backups) == 0 {
		t.Fatal("迁移失败未保留原库和一致快照")
	}
	if _, e = db.Exec(`UPDATE publication_previews SET target_spec='null' WHERE id=?`, preview["previewId"]); e != nil {
		t.Fatal(e)
	}
	// 原库存在孤立关系时，foreign_key_check必须使整次迁移失败。
	if _, e = db.Exec(`PRAGMA foreign_keys=OFF; INSERT INTO drafts(instance_id,revision,config) VALUES('orphan-history',1,'{}')`); e != nil {
		t.Fatal(e)
	}
	db.Close()
	if bad, err := platform.Start(context.Background(), cfg); err == nil {
		bad.Close(context.Background())
		t.Fatal("外键违规仍提交迁移")
	}
	db, e = sql.Open("sqlite", filepath.Join(dir, "platform.db"))
	if e != nil {
		t.Fatal(e)
	}
	db.QueryRow(`SELECT max(version) FROM schema_migrations`).Scan(&oldVersion)
	if oldVersion != 4 {
		t.Fatal("FK失败未回滚schema版本")
	}
	if _, e = db.Exec(`DELETE FROM drafts WHERE instance_id='orphan-history'`); e != nil {
		t.Fatal(e)
	}
	db.Close()
	p, e = platform.Start(context.Background(), cfg)
	if e != nil {
		t.Fatal(e)
	}
	defer p.Close(context.Background())
	db, e = sql.Open("sqlite", filepath.Join(dir, "platform.db"))
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	var stored string
	if e = db.QueryRow(`SELECT target_spec FROM publication_previews WHERE id=?`, preview["previewId"]).Scan(&stored); e != nil {
		t.Fatal(e)
	}
	if !strings.HasPrefix(stored, "hmac:v1:") {
		t.Fatal("旧预览未迁移成带版本指纹")
	}
	identityTx, e := db.Begin()
	if e != nil {
		t.Fatal(e)
	}
	insert := `INSERT INTO instances(id,cluster_id,component,name,target_namespace,activity_at,cr_namespace) VALUES(?,?,?,?,?,'now',?)`
	if _, e = identityTx.Exec(insert, "ksm-duplicate", cl, "kube-state-metrics", "legacy", "other-namespace", ""); e == nil {
		t.Fatal("KSM cluster scope身份被部署namespace分裂")
	}
	for _, ns := range []string{"one", "two"} {
		if _, e = identityTx.Exec(insert, "agent-"+ns, cl, "Prometheus Agent", "same-name", ns, ns); e != nil {
			t.Fatal("Agent跨namespace同名被拒绝")
		}
	}
	if _, e = identityTx.Exec(insert, "agent-duplicate", cl, "Prometheus Agent", "same-name", "one", "one"); e == nil {
		t.Fatal("Agent同namespace同名未冲突")
	}
	identityTx.Rollback()
	fk, e := db.Query(`PRAGMA foreign_key_check`)
	if e != nil {
		t.Fatal(e)
	}
	broken := fk.Next()
	fk.Close()
	if broken {
		t.Fatal("迁移破坏历史FK")
	}

	body := map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}
	published := clusterCall(t, p, "POST", "/instances/"+id+"/publications", body, c, "publish-old-preview", 202)
	if awaitClusterOperation(t, p, c, published["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("升级使原安全预览失效")
	}
}

// REG18-SHARED-003：隐藏秘密仍须参与配置变化判定；仅RV/status变化不能使预览过期。
func TestSharedHMACDetectsHiddenConfigAndPreservesMetadata(t *testing.T) {
	p, c, id, f := ksmPublishedFixture(t)
	first := ksmPreview(t, p, c, id, 1)
	op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": first["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "initial", 202)
	if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("前置发布失败")
	}
	f.mu.Lock()
	f.cr["spec"].(map[string]any)["bearerToken"] = "hidden-original-value"
	f.cr["metadata"].(map[string]any)["annotations"] = map[string]any{"kubectl.kubernetes.io/last-applied-configuration": "preserved-sensitive-annotation"}
	f.mu.Unlock()
	preview := ksmPreview(t, p, c, id, 1)
	if preview["drift"] != true || strings.Contains(preview["leftYAML"].(string), "hidden-original-value") || strings.Contains(preview["leftYAML"].(string), "preserved-sensitive-annotation") {
		t.Fatal("漂移预览未安全投影")
	}
	f.mu.Lock()
	f.cr["spec"].(map[string]any)["bearerToken"] = "hidden-changed-value"
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": true}, c, "changed-secret", 409)
	preview = ksmPreview(t, p, c, id, 1)
	f.mu.Lock()
	f.cr["metadata"].(map[string]any)["resourceVersion"] = "rv-only"
	f.cr["status"] = map[string]any{"availablePods": float64(0)}
	f.mu.Unlock()
	op = clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": true}, c, "rv-only", 202)
	if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("仅RV/status变化使预览失效")
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.cr["metadata"].(map[string]any)["annotations"].(map[string]any)["kubectl.kubernetes.io/last-applied-configuration"] != "preserved-sensitive-annotation" {
		t.Fatal("脱敏改写了目标metadata")
	}
}
