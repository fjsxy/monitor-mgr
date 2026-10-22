package platform_test

import (
	"context"
	"database/sql"
	"net/http"
	"path/filepath"
	"sync/atomic"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/platform"
	ginkgo "github.com/onsi/ginkgo/v2"
)

// REG19-ACTIVITY-001：真实入口上的活动顺序独立于墙钟，并跨原目录重启保留。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestWorkbenchActivitySequencePersists(t ginkgo.GinkgoTInterface) {
	// 接下来REG19-ACTIVITY-001：真实入口上的活动顺序独立于墙钟，并跨原目录重启保留。
	ginkgo.By("REG19-ACTIVITY-001：真实入口上的活动顺序独立于墙钟，并跨原目录重启保留。")

	_, target := ksmTarget(t)
	var stamp atomic.Int64
	stamp.Store(time.Date(2026, 10, 19, 1, 0, 0, 0, time.UTC).UnixNano())
	cfg := platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", Now: func() time.Time { return time.Unix(0, stamp.Load()) }}
	p, e := platform.Start(context.Background(), cfg)
	if e != nil {
		t.Fatal(e)
	}
	defer func() { p.Close(context.Background()) }()
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	owner := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"].(string)
	op := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "workbench", "ownerId": owner, "context": "explicit", "kubeconfig": target}, c, "register", 202)
	cl := awaitClusterOperation(t, p, c, op["operationId"].(string))["clusterId"].(string)
	create := func(name string) string {
		return clusterCall(t, p, "POST", "/clusters/"+cl+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig(name)}, c, name, 201)["id"].(string)
	}
	a := create("clock-first")
	stamp.Add(-int64(time.Second))
	b := create("clock-second")
	assertOrder := func(want string) {
		t.Helper()
		list := clusterCall(t, p, "GET", "/clusters/"+cl+"/instances", nil, c, "", 200)["instances"].([]any)
		if len(list) != 2 || list[0].(map[string]any)["id"] != want {
			t.Fatalf("最近真实活动应排序第一，时钟不能改变顺序: got=%v want=%s", list, want)
		}
	}
	assertOrder(b)
	clusterCall(t, p, "POST", "/instances/"+a+"/editing-activity", nil, c, "edit-a", 200)
	assertOrder(a)
	clusterCall(t, p, "PUT", "/instances/"+b+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": ksmConfig("clock-second")}, c, "save-b", 200)
	assertOrder(b)
	for _, path := range []string{"/instances/" + a, "/instances/" + a + "/draft", "/instances/" + a + "/runtime"} {
		clusterCall(t, p, "GET", path, nil, c, "", http.StatusOK)
	}
	assertOrder(b)
	if e = p.Close(context.Background()); e != nil {
		t.Fatal(e)
	}
	p, e = platform.Start(context.Background(), cfg)
	if e != nil {
		t.Fatal(e)
	}
	assertOrder(b)
	if e = p.Close(context.Background()); e != nil {
		t.Fatal(e)
	}
	// 真实旧版本结构前置：业务数据全部来自HTTP，移除仅v9的DDL再启动迁移。
	db, err := sql.Open("sqlite", filepath.Join(cfg.DataDir, "platform.db"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec(dropInstanceCreatorSchema + dropAgentStorageSchema + `DROP INDEX instance_activity_order;ALTER TABLE instances DROP COLUMN activity_sequence;DROP TABLE instance_activity_counter;DELETE FROM schema_migrations WHERE version>8`); err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec(`UPDATE instances SET activity_at='2026-10-19T01:01:00Z' WHERE id=?`, b); err != nil {
		t.Fatal(err)
	}
	db.Close()
	p, e = platform.Start(context.Background(), cfg)
	if e != nil {
		t.Fatal(e)
	}
	assertOrder(b)
	stamp.Add(-int64(time.Second))
	clusterCall(t, p, "POST", "/instances/"+a+"/editing-activity", nil, c, "edit-after-restart", 200)
	assertOrder(a)
	// 原schema8在同一毫秒内仍有可判定顺序，不能让浮点时间丢纳秒后回退为ID排序。
	if e = p.Close(context.Background()); e != nil {
		t.Fatal(e)
	}
	db, err = sql.Open("sqlite", filepath.Join(cfg.DataDir, "platform.db"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec(dropInstanceCreatorSchema + dropAgentStorageSchema + `DROP INDEX instance_activity_order;ALTER TABLE instances DROP COLUMN activity_sequence;DROP TABLE instance_activity_counter;DELETE FROM schema_migrations WHERE version>8`); err != nil {
		t.Fatal(err)
	}
	earlier, later := a, b
	if a < b {
		earlier, later = b, a
	}
	if _, err = db.Exec(`UPDATE instances SET activity_at=? WHERE id=?`, "2026-10-19T01:00:00.123000001Z", earlier); err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec(`UPDATE instances SET activity_at=? WHERE id=?`, "2026-10-19T01:00:00.123000999Z", later); err != nil {
		t.Fatal(err)
	}
	db.Close()
	p, e = platform.Start(context.Background(), cfg)
	if e != nil {
		t.Fatal(e)
	}
	assertOrder(later)
}
