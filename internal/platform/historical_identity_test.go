// 历史事实经公开入口建库，再用真实旧结构验证升级后的公开行为。
package platform_test

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/cloudwego/kitex/client"
	"github.com/fjsxy/monitor-mgr/internal/platform"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"github.com/fjsxy/monitor-mgr/kitex_gen/control/controlservice"
	ginkgo "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

func checkTestHistoricalCreatorEvidence(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I26-01/02/03：真实 v10 schema 只从唯一且一致的 CREATE_INSTANCE actor 补齐创建用户")
	for _, variant := range []string{"reliable-ksm", "reliable-agent", "renamed-ksm", "renamed-agent", "v11", "v11-known-admin", "v11-known-user", "missing", "conflict", "wrong-target", "wrong-cluster", "wrong-id", "wrong-component", "missing-actor", "malformed"} {
		ginkgo.By("历史证据输入：" + variant)
		_, kubeconfig := ksmTarget(t)
		cfg := platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"}
		p, e := platform.Start(context.Background(), cfg)
		if e != nil {
			t.Fatal(e)
		}
		request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
		admin := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
		creator := readJSON(t, p, "/session", admin)["account"].(map[string]any)["id"].(string)
		user, owner := identityUser(t, p, admin, "history-owner", "USER")
		reg := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "history", "ownerId": owner, "context": "explicit", "kubeconfig": kubeconfig}, admin, "history-cluster", 202)
		cluster := awaitClusterOperation(t, p, admin, reg["operationId"].(string))["clusterId"].(string)
		body := map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("history")}
		if variant == "reliable-agent" || variant == "renamed-agent" {
			body = map[string]any{"component": "Prometheus Agent", "agent": agentConfig("history", "monitoring")}
		}
		creatingActor := admin
		if variant == "v11-known-user" {
			creatingActor = user
			creator = owner
		}
		instance := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", body, creatingActor, "history-instance", 201)
		id := instance["id"].(string)
		if variant == "renamed-ksm" {
			config := ksmConfig("renamed-history")
			config["targetNamespace"] = "renamed-ns"
			clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": config}, admin, "rename", 200)
		}
		if variant == "renamed-agent" {
			clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "agent": agentConfig("renamed-history", "renamed-ns")}, admin, "rename", 200)
		}
		if e = p.Close(context.Background()); e != nil {
			t.Fatal(e)
		}
		db, e := sql.Open("sqlite", filepath.Join(cfg.DataDir, "platform.db"))
		if e != nil {
			t.Fatal(e)
		}
		// 关键：真实删除历史不存在的字段；旧创建结果也删除新增身份投影，不能偷用新字段或当前角色。
		knownRole := instance["creatorRole"]
		delete(instance, "creatorUserId")
		delete(instance, "creatorRole")
		delete(instance, "creatorIdentityKnown")
		delete(instance, "canManage")
		delete(instance, "readOnlyReason")
		legacySQL := dropResourceProgressSchema + dropInstanceCreatorSchema + `DELETE FROM schema_migrations WHERE version>10;`
		if strings.HasPrefix(variant, "v11") {
			original, e := os.ReadFile("testdata/legacy/creator-v11.sql")
			Expect(e).NotTo(HaveOccurred())
			legacySQL = dropResourceProgressSchema + dropInstanceCreatorSchema + string(original) + `DELETE FROM schema_migrations WHERE version>11;`
		}
		if _, e = db.Exec(legacySQL); e != nil {
			t.Fatal(e)
		}
		if variant == "v11-known-admin" || variant == "v11-known-user" {
			_, e = db.Exec(`UPDATE instances SET creator_user_id=?,creator_role=? WHERE id=?`, creator, knownRole, id)
			Expect(e).NotTo(HaveOccurred())
		}
		switch variant {
		case "missing":
			_, e = db.Exec(`DELETE FROM command_results WHERE action='CREATE_INSTANCE'`)
		case "wrong-target":
			_, e = db.Exec(`UPDATE command_results SET target_id='another-cluster' WHERE action='CREATE_INSTANCE'`)
		case "wrong-cluster":
			instance["clusterId"] = "another-cluster"
		case "wrong-id":
			instance["id"] = "nonexistent-instance"
		case "wrong-component":
			instance["component"] = "Prometheus Agent"
		case "missing-actor":
			_, e = db.Exec(`UPDATE command_results SET actor_id='missing-account' WHERE action='CREATE_INSTANCE'`)
		}
		if e != nil {
			t.Fatal(e)
		}
		raw, _ := json.Marshal(instance)
		if variant == "malformed" {
			raw = []byte(`{"id":`)
		}
		if _, e = db.Exec(`UPDATE command_results SET result_json=? WHERE action='CREATE_INSTANCE'`, raw); e != nil {
			t.Fatal(e)
		}
		if variant == "conflict" {
			_, e = db.Exec(`INSERT INTO command_results SELECT ?,action,target_id,'conflict-key',request_digest,result_json,created_at FROM command_results WHERE action='CREATE_INSTANCE'`, owner)
			if e != nil {
				t.Fatal(e)
			}
		}
		db.Close()
		p, e = platform.Start(context.Background(), cfg)
		if e != nil {
			t.Fatal(e)
		}
		got := readJSON(t, p, "/instances/"+id, user)
		expected := ""
		if variant == "reliable-ksm" || variant == "reliable-agent" || variant == "renamed-ksm" || variant == "renamed-agent" || variant == "v11" {
			expected = creator
		}
		expectedRole, expectedManage := "", false
		if variant == "v11-known-admin" {
			expected, expectedRole = creator, "ADMIN"
		}
		if variant == "v11-known-user" {
			expected, expectedRole, expectedManage = creator, "USER", true
		}
		if got["creatorUserId"] != expected || got["creatorRole"] != expectedRole || got["creatorIdentityKnown"] != (expectedRole != "") || got["canManage"] != expectedManage {
			t.Fatalf("%s 创建事实错误：%v", variant, got)
		}
		ginkgo.AddReportEntry("I26-creator-"+variant, "公开 HTTP 升级后创建事实和管理能力符合预期")
		p.Close(context.Background())
	}
}

// historicalFixture 保存真实旧库并重启，只有测试夹具变更 schema，不绕过公开业务入口。
func historicalFixture(t ginkgo.GinkgoTInterface, boundary func(string, string) error, knownCreator ...bool) (*platform.Runtime, *http.Cookie, *http.Cookie, string, string, *ksmFixture, platform.Config) {
	t.Helper()
	target, kubeconfig := ksmTarget(t)
	cfg := platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", OperationBoundary: boundary}
	p, e := platform.Start(context.Background(), cfg)
	if e != nil {
		t.Fatal(e)
	}
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	admin := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	user, owner := identityUser(t, p, admin, "history-manager", "USER")
	reg := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "history", "ownerId": owner, "context": "explicit", "kubeconfig": kubeconfig}, admin, "history-cluster", 202)
	cluster := awaitClusterOperation(t, p, admin, reg["operationId"].(string))["clusterId"].(string)
	i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("lifecycle")}, admin, "history-instance", 201)
	p.Close(context.Background())
	db, e := sql.Open("sqlite", filepath.Join(cfg.DataDir, "platform.db"))
	if e != nil {
		t.Fatal(e)
	}
	legacySQL := dropResourceProgressSchema + dropInstanceCreatorSchema + `DELETE FROM schema_migrations WHERE version>10;`
	if len(knownCreator) == 0 || !knownCreator[0] {
		legacySQL += `DELETE FROM command_results WHERE action='CREATE_INSTANCE';`
	}
	_, e = db.Exec(legacySQL)
	db.Close()
	if e != nil {
		t.Fatal(e)
	}
	p, e = platform.Start(context.Background(), cfg)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { p.Close(context.Background()) })
	return p, admin, user, i["id"].(string), cluster, target, cfg
}

func checkTestHistoricalManagementConfirmation(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I26-04/05/06/07：管理员独立确认管理权限，留存 actor/time/result 且不改历史事实")
	p, admin, user, id, cluster, _, cfg := historicalFixture(t, nil)
	adminID := readJSON(t, p, "/session", admin)["account"].(map[string]any)["id"].(string)
	path := "/instances/" + id + "/management-confirmations"
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": ksmConfig("lifecycle")}, user, "denied", 403)
	clusterCall(t, p, "POST", path, map[string]any{"allowClusterManagers": true}, user, "user-confirm", 403)
	for _, input := range []map[string]any{{}, {"allowClusterManagers": true, "creatorRole": "USER"}, {"allowClusterManagers": "true"}} {
		clusterCall(t, p, "POST", path, input, admin, "invalid", 400)
	}
	result := clusterCall(t, p, "POST", path, map[string]any{"allowClusterManagers": true}, admin, "allow", 200)
	confirmation, ok := result["managementConfirmation"].(map[string]any)
	if !ok || confirmation["actorId"] != adminID || confirmation["confirmedAt"] == "" || confirmation["allowClusterManagers"] != true || result["creatorRole"] != "" || result["creatorUserId"] != "" {
		t.Fatal("确认混入历史身份或缺少留痕", result)
	}
	replay := clusterCall(t, p, "POST", path, map[string]any{"allowClusterManagers": true}, admin, "allow", 200)
	if replay["managementConfirmation"].(map[string]any)["confirmedAt"] != confirmation["confirmedAt"] {
		t.Fatal("幂等确认重复")
	}
	clusterCall(t, p, "POST", path, map[string]any{"allowClusterManagers": false}, admin, "allow", 409)
	for _, url := range []string{"/instances/" + id, "/clusters/" + cluster + "/instances", "/clusters/" + cluster + "/instances?page=1"} {
		got := readJSON(t, p, url, user)
		if raw, ok := got["instances"]; ok {
			got = raw.([]any)[0].(map[string]any)
		}
		if got["canManage"] != true || got["creatorRole"] != "" || got["creatorUserId"] != "" || got["managementConfirmation"] == nil {
			t.Fatal("读模型权限确认未生效", got)
		}
	}
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": ksmConfig("lifecycle")}, user, "allowed-save", 200)
	p.Close(context.Background())
	restarted, e := platform.Start(context.Background(), cfg)
	if e != nil {
		t.Fatal(e)
	}
	defer restarted.Close(context.Background())
	p = restarted
	got := readJSON(t, p, "/instances/"+id, user)
	if got["canManage"] != true || got["managementConfirmation"] == nil {
		t.Fatal("重启丢失确认", got)
	}
	denied := clusterCall(t, p, "POST", path, map[string]any{"allowClusterManagers": false}, admin, "restrict", 200)
	if denied["managementConfirmation"].(map[string]any)["allowClusterManagers"] != false {
		t.Fatal("拒绝结果未保存", denied)
	}
	got = readJSON(t, p, "/instances/"+id, user)
	if got["canManage"] != false || !strings.Contains(got["readOnlyReason"].(string), "管理员已确认") {
		t.Fatal("撤销未限制普通用户", got)
	}
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 2, "ksm": ksmConfig("lifecycle")}, user, "revoked-save", 403)
}
func checkTestHistoricalManagementDirectRPC(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I26-06/07：真实直接 RPC 重新认证管理员且不能弱化已知创建角色")
	p, admin, user, id, cluster, _, _ := historicalFixture(t, nil)
	cli, e := controlservice.NewClient("historical-management", client.WithHostPorts(p.RPCAddress()))
	if e != nil {
		t.Fatal(e)
	}
	key, session := "direct-confirm", user.Value
	c := &rpc.RequestContext{RequestId: key, SessionToken: &session, IdempotencyKey: &key}
	_, e = cli.ConfirmInstanceManagement(context.Background(), c, &rpc.ConfirmInstanceManagementRequest{Id: id, AllowClusterManagers: true})
	var apiErr *rpc.APIError
	if !errors.As(e, &apiErr) || apiErr.Code != "FORBIDDEN" {
		t.Fatal("直接RPC越权确认", e)
	}
	for _, actor := range []*http.Cookie{admin, user} {
		created := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("known-" + actor.Value[:6])}, actor, "known", 201)
		clusterCall(t, p, "POST", "/instances/"+created["id"].(string)+"/management-confirmations", map[string]any{"allowClusterManagers": true}, admin, "known-confirm", 409)
	}
	clusterCall(t, p, "POST", "/instances/"+id+"/management-confirmations", map[string]any{"allowClusterManagers": true}, admin, "admin-confirm", 200)
	other, _ := identityUser(t, p, admin, "other-admin", "ADMIN")
	adminID := readJSON(t, p, "/session", admin)["account"].(map[string]any)["id"].(string)
	clusterCall(t, p, "PATCH", "/accounts/"+adminID, map[string]any{"role": "USER"}, other, "demote-confirmer", 200)
	demoted := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	clusterCall(t, p, "POST", "/instances/"+id+"/management-confirmations", map[string]any{"allowClusterManagers": true}, demoted, "admin-confirm", 403)
	// I26-06：注销后的确认入口停止写入，原确认仍可作为只读历史查看。
	clusterCall(t, p, "POST", "/clusters/"+cluster+"/unregister", map[string]any{}, other, "unregister-confirmation", 200)
	clusterCall(t, p, "POST", "/instances/"+id+"/management-confirmations", map[string]any{"allowClusterManagers": false}, other, "inactive-confirm", 409)
	historical := readJSON(t, p, "/instances/"+id, other)
	Expect(historical["canManage"]).To(BeFalse())
	Expect(historical["managementConfirmation"].(map[string]any)["allowClusterManagers"]).To(BeTrue())
	ginkgo.AddReportEntry("I26-06/07", "HTTP/RPC 当前管理员授权、已知角色、外部及注销实例边界均通过")
}

func checkTestHistoricalManagementDispatchRevocation(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I26-05：管理员撤销独立权限确认后，已受理且未发送的普通用户任务不能写入")
	entered, release := make(chan struct{}), make(chan struct{})
	var once, released sync.Once
	defer released.Do(func() { close(release) })
	p, admin, user, id, _, target, _ := historicalFixture(t, func(stage, id string) error {
		if stage == "beforeDispatch" {
			once.Do(func() { close(entered) })
			<-release
		}
		return nil
	}, true)
	path := "/instances/" + id + "/management-confirmations"
	clusterCall(t, p, "POST", path, map[string]any{"allowClusterManagers": true}, admin, "allow-dispatch", 200)
	op := lifecyclePublish(t, p, user, id)
	select {
	case <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("未到派发屏障")
	}
	clusterCall(t, p, "POST", path, map[string]any{"allowClusterManagers": false}, admin, "restrict-dispatch", 200)
	released.Do(func() { close(release) })
	got := awaitClusterOperation(t, p, admin, op["operationId"].(string))
	if got["state"] != "FAILED" || !strings.Contains(got["message"].(string), "角色未知") {
		t.Fatal("后台沿用已撤销确认", got)
	}
	target.mu.Lock()
	defer target.mu.Unlock()
	if target.writes != 0 {
		t.Fatal("撤销后仍发送实际写入", target.writes)
	}
}

// I26-08：独立 v10/v11 结构在 DDL 冲突时保持原库，可从本次快照恢复并再次启动。
func checkTestHistoricalMigrationRollback(t ginkgo.GinkgoTInterface) {
	for _, legacyVersion := range []int{10, 11} {
		ginkgo.By(fmt.Sprintf("I26-08.v%d：原 UID、版本、任务、密文与审计在失败和恢复后保持", legacyVersion))
		p, admin, user, id, cluster, _, cfg := historicalFixture(t, nil, true)
		op := lifecyclePublish(t, p, admin, id)
		awaitLifecycleState(t, p, admin, op["operationId"].(string), "SUCCEEDED")
		before := readJSON(t, p, "/instances/"+id, admin)
		version := readJSON(t, p, "/instances/"+id+"/versions/"+op["versionId"].(string), admin)
		p.Close(context.Background())
		path := filepath.Join(cfg.DataDir, "platform.db")
		db, e := sql.Open("sqlite", path)
		if e != nil {
			t.Fatal(e)
		}
		var cipher []byte
		var audits int
		if e = db.QueryRow(`SELECT credential FROM clusters WHERE id=?`, cluster).Scan(&cipher); e != nil {
			t.Fatal(e)
		}
		if e = db.QueryRow(`SELECT count(*) FROM audit`).Scan(&audits); e != nil {
			t.Fatal(e)
		}
		legacy := dropResourceProgressSchema + `DROP TABLE instance_management_confirmations;`
		if legacyVersion == 10 {
			legacy += `ALTER TABLE instances DROP COLUMN creator_user_id;ALTER TABLE instances DROP COLUMN creator_role;`
		}
		// 关键：冲突索引同名表使 v15 DDL 真实失败；不绕过旧表结构核验，也不假改 schema 版本。
		legacy += fmt.Sprintf(`DELETE FROM schema_migrations WHERE version>%d;CREATE TABLE instance_management_latest(conflict TEXT);`, legacyVersion)
		if _, e = db.Exec(legacy); e != nil {
			t.Fatal(e)
		}
		db.Close()
		failed, e := platform.Start(context.Background(), cfg)
		if e == nil {
			failed.Close(context.Background())
			t.Fatal("DDL 冲突未阻止启动")
		}
		verify := func(source string) {
			db, e := sql.Open("sqlite", source)
			if e != nil {
				t.Fatal(e)
			}
			defer db.Close()
			var gotVersion, count int
			var gotCipher []byte
			var uid string
			Expect(db.QueryRow(`SELECT max(version) FROM schema_migrations`).Scan(&gotVersion)).To(Succeed())
			Expect(gotVersion).To(Equal(legacyVersion))
			Expect(db.QueryRow(`SELECT credential FROM clusters WHERE id=?`, cluster).Scan(&gotCipher)).To(Succeed())
			Expect(gotCipher).To(Equal(cipher))
			Expect(db.QueryRow(`SELECT uid FROM instances WHERE id=?`, id).Scan(&uid)).To(Succeed())
			Expect(uid).To(Equal(before["uid"]))
			Expect(db.QueryRow(`SELECT count(*) FROM audit`).Scan(&count)).To(Succeed())
			Expect(count).To(Equal(audits))
			Expect(db.QueryRow(`SELECT count(*) FROM sqlite_master WHERE type='table' AND name='instance_management_confirmations'`).Scan(&count)).To(Succeed())
			Expect(count).To(BeZero())
		}
		verify(path)
		backups, e := filepath.Glob(filepath.Join(cfg.DataDir, "backups", fmt.Sprintf("schema-%d-to-16-*.db", legacyVersion)))
		Expect(e).NotTo(HaveOccurred())
		Expect(backups).NotTo(BeEmpty())
		latest := ""
		var latestTime time.Time
		for _, backup := range backups {
			info, e := os.Stat(backup)
			Expect(e).NotTo(HaveOccurred())
			if latest == "" || info.ModTime().After(latestTime) {
				latest = backup
				latestTime = info.ModTime()
			}
		}
		verify(latest)
		db, e = sql.Open("sqlite", path)
		Expect(e).NotTo(HaveOccurred())
		_, e = db.Exec(`DROP TABLE instance_management_latest`)
		Expect(e).NotTo(HaveOccurred())
		db.Close()
		restored, e := platform.Start(context.Background(), cfg)
		Expect(e).NotTo(HaveOccurred())
		got := readJSON(t, restored, "/instances/"+id, user)
		Expect(got["uid"]).To(Equal(before["uid"]))
		Expect(got["creatorUserId"]).To(Equal(before["creatorUserId"]))
		Expect(got["creatorRole"]).To(Equal(""))
		afterVersion := readJSON(t, restored, "/instances/"+id+"/versions/"+op["versionId"].(string), user)
		// 旧单 CR 历史在新只读资源投影之外保持原冻结配置，绝不向旧版本追加采集意图。
		for _, field := range []string{"versionId", "instanceId", "sourceDraftRevision", "ksm", "yaml", "createdAt"} {
			Expect(afterVersion[field]).To(Equal(version[field]), field)
		}
		Expect(afterVersion["resources"].([]any)).To(HaveLen(1))
		Expect(readJSON(t, restored, "/operations/"+op["operationId"].(string), user)["state"]).To(Equal("SUCCEEDED"))
		restored.Close(context.Background())
		ginkgo.AddReportEntry(fmt.Sprintf("I26-08.v%d", legacyVersion), "本次失败原库和一致快照均保留；恢复后公开 UID/版本/任务一致")
	}
}

// I26-01.v13：从独立旧版 DDL 重建真实历史结构，原确认与创建事实分别保留。
func checkTestHistoricalV13Confirmation(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I26-01.v13：旧 v13 原确认、冻结版本与审计保留，不自动启用采集")
	p, admin, user, id, _, target, cfg := historicalFixture(t, nil, true)
	op := lifecyclePublish(t, p, admin, id)
	awaitLifecycleState(t, p, admin, op["operationId"].(string), "SUCCEEDED")
	before := readJSON(t, p, "/instances/"+id, admin)
	allowed := clusterCall(t, p, "POST", "/instances/"+id+"/management-confirmations", map[string]any{"allowClusterManagers": true}, admin, "legacy-confirm", 200)
	confirmation := allowed["managementConfirmation"].(map[string]any)
	p.Close(context.Background())
	db, e := sql.Open("sqlite", filepath.Join(cfg.DataDir, "platform.db"))
	Expect(e).NotTo(HaveOccurred())
	db.SetMaxOpenConns(1)
	for _, table := range []string{"version_resources", "operation_resources", "resource_progress", "instance_resource_identities"} {
		_, e = db.Exec(`CREATE TEMP TABLE old_` + table + ` AS SELECT * FROM ` + table)
		Expect(e).NotTo(HaveOccurred())
	}
	_, e = db.Exec(dropResourceProgressSchema + `DROP TABLE instance_management_confirmations;DELETE FROM schema_migrations WHERE version>11;`)
	Expect(e).NotTo(HaveOccurred())
	for _, fixture := range []string{"resources-v12.sql", "management-v13.sql"} {
		raw, e := os.ReadFile("testdata/legacy/" + fixture)
		Expect(e).NotTo(HaveOccurred())
		_, e = db.Exec(string(raw))
		Expect(e).NotTo(HaveOccurred())
	}
	for _, table := range []string{"version_resources", "operation_resources", "resource_progress", "instance_resource_identities"} {
		_, e = db.Exec(`INSERT INTO ` + table + ` SELECT * FROM old_` + table)
		Expect(e).NotTo(HaveOccurred())
	}
	_, e = db.Exec(`INSERT INTO schema_migrations VALUES(12),(13);INSERT INTO instance_management_confirmations(instance_id,actor_id,confirmed_at,allow_cluster_managers) VALUES(?,?,?,1)`, id, confirmation["actorId"], confirmation["confirmedAt"])
	Expect(e).NotTo(HaveOccurred())
	db.Close()
	target.mu.Lock()
	writes, smWrites := target.writes, target.smWrites
	target.mu.Unlock()
	restored, e := platform.Start(context.Background(), cfg)
	Expect(e).NotTo(HaveOccurred())
	defer restored.Close(context.Background())
	got := readJSON(t, restored, "/instances/"+id, user)
	Expect(got["managementConfirmation"]).To(Equal(confirmation))
	Expect(got["creatorRole"]).To(Equal(""))
	Expect(got["creatorUserId"]).To(Equal(before["creatorUserId"]))
	Expect(got["canManage"]).To(BeTrue())
	Expect(got["uid"]).To(Equal(before["uid"]))
	clusterCall(t, restored, "POST", "/instances/"+id+"/refresh", map[string]any{}, user, "poll", 200)
	target.mu.Lock()
	defer target.mu.Unlock()
	Expect(target.writes).To(Equal(writes))
	Expect(target.smWrites).To(Equal(smWrites))
	Expect(target.cr["spec"].(map[string]any)).NotTo(HaveKey("serviceMonitor"))
	ginkgo.AddReportEntry("I26-01.v13", "独立旧 v13 DDL 迁移保留确认，公开轮询没有 exporter 或 ServiceMonitor 新写入")
}
