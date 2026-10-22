// 历史创建事实从持久命令证据恢复；测试从公开入口建历史，再裁除新结构形成独立旧库。
package platform_test

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"github.com/cloudwego/kitex/client"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"github.com/fjsxy/monitor-mgr/kitex_gen/control/controlservice"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/platform"
	ginkgo "github.com/onsi/ginkgo/v2"
)

func checkTestHistoricalCreatorEvidence(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I26-01/02/03：真实 v10 schema 只从唯一且一致的 CREATE_INSTANCE actor 补齐创建用户")
	for _, variant := range []string{"reliable-ksm", "reliable-agent", "missing", "conflict", "wrong-target", "wrong-name", "wrong-component", "wrong-namespace", "missing-actor", "malformed"} {
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
		if variant == "reliable-agent" {
			body = map[string]any{"component": "Prometheus Agent", "agent": agentConfig("history", "monitoring")}
		}
		instance := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", body, admin, "history-instance", 201)
		id := instance["id"].(string)
		if e = p.Close(context.Background()); e != nil {
			t.Fatal(e)
		}
		db, e := sql.Open("sqlite", filepath.Join(cfg.DataDir, "platform.db"))
		if e != nil {
			t.Fatal(e)
		}
		// 关键：真实删除历史不存在的字段；旧创建结果也删除新增身份投影，不能偷用新字段或当前角色。
		delete(instance, "creatorUserId")
		delete(instance, "creatorRole")
		delete(instance, "creatorIdentityKnown")
		delete(instance, "canManage")
		delete(instance, "readOnlyReason")
		if _, e = db.Exec(dropInstanceCreatorSchema + `DELETE FROM schema_migrations WHERE version>10;`); e != nil {
			t.Fatal(e)
		}
		switch variant {
		case "missing":
			_, e = db.Exec(`DELETE FROM command_results WHERE action='CREATE_INSTANCE'`)
		case "wrong-target":
			_, e = db.Exec(`UPDATE command_results SET target_id='another-cluster' WHERE action='CREATE_INSTANCE'`)
		case "wrong-name":
			instance["name"] = "another"
		case "wrong-component":
			instance["component"] = "Prometheus Agent"
		case "wrong-namespace":
			instance["targetNamespace"] = "another"
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
		if variant == "reliable-ksm" || variant == "reliable-agent" {
			expected = creator
		}
		if got["creatorUserId"] != expected || got["creatorRole"] != "" || got["creatorIdentityKnown"] != false || got["canManage"] != false {
			t.Fatalf("%s 创建事实错误：%v", variant, got)
		}
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
	legacySQL := dropInstanceCreatorSchema + `DELETE FROM schema_migrations WHERE version>10;`
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

func checkTestHistoricalMigrationRollback(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I26-08：新迁移失败保留真实旧 schema、原密文/UID/版本/任务/审计和一致快照")
	p, admin, user, id, cluster, target, cfg := historicalFixture(t, nil, true)
	op := lifecyclePublish(t, p, admin, id)
	awaitLifecycleState(t, p, admin, op["operationId"].(string), "SUCCEEDED")
	before := readJSON(t, p, "/instances/"+id, admin)
	version := readJSON(t, p, "/instances/"+id+"/versions/"+op["versionId"].(string), admin)
	// 旧v10契约只冻结组件；当前产品先建出的SM属于夹具转换，不能留在目标冒充旧部署。
	originalComponent := version["resources"].([]any)[0]
	delete(version, "resources")
	target.mu.Lock()
	target.sm = nil
	target.mu.Unlock()
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
	// 仅夹具故意制造DDL冲突，验证整体事务回滚；不以写版本号冒充旧结构。
	_, e = db.Exec(dropInstanceCreatorSchema + `DELETE FROM schema_migrations WHERE version>10;CREATE TABLE instance_management_confirmations(conflict TEXT);`)
	db.Close()
	if e != nil {
		t.Fatal(e)
	}
	failed, e := platform.Start(context.Background(), cfg)
	if e == nil {
		failed.Close(context.Background())
		t.Fatal("冲突DDL仍启动成功")
	}
	verify := func(source string) {
		old, e := sql.Open("sqlite", source)
		if e != nil {
			t.Fatal(e)
		}
		defer old.Close()
		var schema, auditCount int
		var afterCipher []byte
		var uid string
		if e = old.QueryRow(`SELECT max(version) FROM schema_migrations`).Scan(&schema); e != nil || schema != 10 {
			t.Fatal("失败推进旧库版本", schema, e)
		}
		if e = old.QueryRow(`SELECT credential FROM clusters WHERE id=?`, cluster).Scan(&afterCipher); e != nil || !bytes.Equal(cipher, afterCipher) {
			t.Fatal("失败改变密文", e)
		}
		if e = old.QueryRow(`SELECT uid FROM instances WHERE id=?`, id).Scan(&uid); e != nil || uid != before["uid"] {
			t.Fatal("失败改变UID", e)
		}
		if e = old.QueryRow(`SELECT count(*) FROM audit`).Scan(&auditCount); e != nil || auditCount != audits {
			t.Fatal("失败改变审计关系", e)
		}
		if _, e = old.Query(`SELECT creator_user_id FROM instances`); e == nil {
			t.Fatal("失败留下新字段")
		}
	}
	verify(path)
	backups, e := filepath.Glob(filepath.Join(cfg.DataDir, "backups", "schema-10-to-*.db"))
	if e != nil || len(backups) == 0 {
		t.Fatal("缺少迁移前快照", e)
	}
	// historicalFixture 已成功升级过一次，取修改时间最新的快照核验本次失败前原库。
	var latest string
	var latestTime time.Time
	for _, backup := range backups {
		info, e := os.Stat(backup)
		if e != nil {
			t.Fatal(e)
		}
		if latest == "" || info.ModTime().After(latestTime) {
			latest = backup
			latestTime = info.ModTime()
		}
	}
	verify(latest)
	db, e = sql.Open("sqlite", path)
	if e != nil {
		t.Fatal(e)
	}
	_, e = db.Exec(`DROP TABLE instance_management_confirmations`)
	db.Close()
	if e != nil {
		t.Fatal(e)
	}
	restored, e := platform.Start(context.Background(), cfg)
	if e != nil {
		t.Fatal(e)
	}
	defer restored.Close(context.Background())
	got := readJSON(t, restored, "/instances/"+id, user)
	if got["uid"] != before["uid"] || got["creatorUserId"] != before["creatorUserId"] || got["creatorRole"] != "" {
		t.Fatal("恢复改变实例事实", got)
	}
	afterVersion := readJSON(t, restored, "/instances/"+id+"/versions/"+op["versionId"].(string), user)
	resources := afterVersion["resources"].([]any)
	expectedResource, _ := json.Marshal(originalComponent)
	actualResource, _ := json.Marshal(resources[0])
	if len(resources) != 1 || !bytes.Equal(expectedResource, actualResource) {
		t.Fatal("旧冻结版本偷偷追加配套资源", afterVersion)
	}
	delete(afterVersion, "resources")
	a, _ := json.Marshal(version)
	b, _ := json.Marshal(afterVersion)
	if !bytes.Equal(a, b) {
		t.Fatal("迁移改变冻结版本")
	}
	if readJSON(t, restored, "/operations/"+op["operationId"].(string), user)["state"] != "SUCCEEDED" {
		t.Fatal("迁移改变原任务")
	}
}
