package platform_test

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"net/http"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/cloudwego/kitex/client"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"github.com/fjsxy/monitor-mgr/kitex_gen/control/controlservice"

	"github.com/fjsxy/monitor-mgr/internal/platform"
	ginkgo "github.com/onsi/ginkgo/v2"
)

// identityUser 只通过公开账号与会话接口准备当前身份。
func identityUser(t ginkgo.GinkgoTInterface, p *platform.Runtime, admin *http.Cookie, name, role string) (*http.Cookie, string) {
	t.Helper()
	account := clusterCall(t, p, "POST", "/accounts", map[string]any{"username": name, "password": "temporary-password", "role": role}, admin, "create-"+name, 201)
	cookie := request(t, p, "POST", "/sessions", `{"username":"`+name+`","password":"temporary-password"}`, nil, 201).Cookies()[0]
	request(t, p, "POST", "/session/password", `{"currentPassword":"temporary-password","newPassword":"personal-password"}`, cookie, 200)
	cookie = request(t, p, "POST", "/sessions", `{"username":"`+name+`","password":"personal-password"}`, nil, 201).Cookies()[0]
	return cookie, account["id"].(string)
}

// I25-01/02：创建关系由认证身份固定，exporter 和 collector 共用相同契约。
func checkTestInstanceCreatorIdentity(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I25-01/02：exporter 与 collector 固定实际创建用户和当时角色")
	p, admin, cluster := ksmCluster(t)
	adminID := readJSON(t, p, "/session", admin)["account"].(map[string]any)["id"].(string)
	for _, component := range []string{"kube-state-metrics", "Prometheus Agent"} {
		body := map[string]any{"component": component}
		if component == "kube-state-metrics" {
			body["ksm"] = ksmConfig("creator-ksm")
		} else {
			body["agent"] = agentConfig("creator-agent", "monitoring")
		}
		for _, field := range []string{"creatorUserId", "creatorRole"} {
			forged := map[string]any{"component": component, field: "forged"}
			if body["ksm"] != nil {
				forged["ksm"] = body["ksm"]
			} else {
				forged["agent"] = body["agent"]
			}
			clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", forged, admin, "forged-"+field+bodyName(body), 400)
		}
		created := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", body, admin, "creator-"+bodyName(body), 201)
		for _, value := range []map[string]any{created, readJSON(t, p, "/instances/"+created["id"].(string), admin)} {
			if value["creatorUserId"] != adminID || value["creatorRole"] != "ADMIN" || value["creatorIdentityKnown"] != true || value["canManage"] != true {
				t.Fatalf("实例未固定创建身份：%v", value)
			}
		}
	}
}

// bodyName 让测试命令键只使用实际合法 CR name。
func bodyName(body map[string]any) string {
	if ksm, ok := body["ksm"].(map[string]any); ok {
		return ksm["name"].(string)
	}
	return body["agent"].(map[string]any)["name"].(string)
}

// I25-03/04/05/06：同集群普通用户可读受保护配置，各 HTTP/RPC 变更入口独立拒绝。
func checkTestAdminInstanceWriteProtection(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I25-03/04/05/06：普通用户不能经 HTTP 或直接 RPC 变更管理员实例")
	p, admin, id, target := ksmPublishedFixture(t)
	instance := readJSON(t, p, "/instances/"+id, admin)
	cluster := instance["clusterId"].(string)
	user, userID := identityUser(t, p, admin, "owner", "USER")
	clusterCall(t, p, "POST", "/clusters/"+cluster+"/transfer", map[string]any{"ownerId": userID}, admin, "transfer-owner", 200)
	target.mu.Lock()
	target.rejectWrite = 403
	target.mu.Unlock()
	op := lifecyclePublish(t, p, admin, id)
	if done := awaitClusterOperation(t, p, admin, op["operationId"].(string)); done["state"] != "FAILED" {
		t.Fatal(done)
	}
	detail := readJSON(t, p, "/instances/"+id, user)
	if detail["canManage"] != false || !strings.Contains(detail["readOnlyReason"].(string), "管理员") || detail["readOnly"] != false {
		t.Fatal("管理权限与纳管归属混淆", detail)
	}
	for _, path := range []string{"/clusters/" + cluster + "/instances", "/clusters/" + cluster + "/instances?page=1"} {
		values := readJSON(t, p, path, user)["instances"].([]any)
		if len(values) != 1 || values[0].(map[string]any)["canManage"] != false {
			t.Fatal("列表未投影管理权限", values)
		}
	}
	readJSON(t, p, "/instances/"+id+"/draft", user)
	clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, user, "refresh", 200)
	for _, tc := range []struct {
		method, path string
		body         any
	}{
		{"PUT", "/draft", map[string]any{"expectedDraftRevision": 1, "ksm": ksmConfig("instance")}},
		{"DELETE", "", nil},
		{"POST", "/editing-activity", map[string]any{}},
		{"POST", "/previews", map[string]any{"draftRevision": 1}},
		{"POST", "/publications", map[string]any{"previewId": "ignored", "draftRevision": 1, "overwriteDrift": false}},
		{"POST", "/deletion-previews", map[string]any{}},
		{"POST", "/deletions", map[string]any{"versionId": op["versionId"], "previewId": "ignored"}},
	} {
		clusterCall(t, p, tc.method, "/instances/"+id+tc.path, tc.body, user, "denied"+strings.ReplaceAll(tc.path, "/", "-"), 403)
	}
	for _, action := range []string{"retry", "continue-unsent", "cancel"} {
		clusterCall(t, p, "POST", "/operations/"+op["operationId"].(string)+"/"+action, lifecycleRevision(t, p, user, op["operationId"].(string)), user, "denied-"+action, 403)
	}
	cli, err := controlservice.NewClient("instance-protection", client.WithHostPorts(p.RPCAddress()))
	if err != nil {
		t.Fatal(err)
	}
	key, session := "direct-protection", user.Value
	c := &rpc.RequestContext{RequestId: key, SessionToken: &session, IdempotencyKey: &key}
	raw, _ := json.Marshal(ksmConfig("instance"))
	cfg := new(rpc.KSMConfiguration)
	json.Unmarshal(raw, cfg)
	instanceID := &rpc.InstanceID{Id: id}
	lifecycle := &rpc.LifecycleRequest{OperationId: op["operationId"].(string)}
	for _, tc := range []struct {
		name string
		run  func() error
	}{
		{"SaveDraft", func() error {
			_, e := cli.SaveDraft(context.Background(), c, &rpc.SaveDraftRequest{Id: id, ExpectedDraftRevision: 1, Ksm: cfg})
			return e
		}},
		{"DeleteDraft", func() error { _, e := cli.DeleteDraft(context.Background(), c, instanceID); return e }},
		{"RecordEditingActivity", func() error { _, e := cli.RecordEditingActivity(context.Background(), c, instanceID); return e }},
		{"PreviewPublication", func() error {
			_, e := cli.PreviewPublication(context.Background(), c, &rpc.PreviewRequest{Id: id, DraftRevision: 1})
			return e
		}},
		{"PublishConfiguration", func() error {
			_, e := cli.PublishConfiguration(context.Background(), c, &rpc.PublishRequest{Id: id, DraftRevision: 1})
			return e
		}},
		{"PreviewDeletion", func() error {
			_, e := cli.PreviewDeletion(context.Background(), c, &rpc.DeletionPreviewRequest{Id: id})
			return e
		}},
		{"DeleteInstance", func() error {
			_, e := cli.DeleteInstance(context.Background(), c, &rpc.DeleteInstanceRequest{Id: id})
			return e
		}},
		{"RetryLifecycle", func() error { _, e := cli.RetryLifecycle(context.Background(), c, lifecycle); return e }},
		{"ContinueLifecycle", func() error { _, e := cli.ContinueLifecycle(context.Background(), c, lifecycle); return e }},
		{"CancelLifecycle", func() error { _, e := cli.CancelLifecycle(context.Background(), c, lifecycle); return e }},
	} {
		var apiErr *rpc.APIError
		if e := tc.run(); !errors.As(e, &apiErr) || apiErr.Code != "FORBIDDEN" {
			t.Fatalf("%s 未执行实例保护：%v", tc.name, e)
		}
	}
	if rev := readJSON(t, p, "/instances/"+id+"/draft", user)["revision"]; rev != float64(1) {
		t.Fatal("拒绝请求仍修改草稿", rev)
	}
}

// I25-07/08/12：当前角色与集群归属变化不改写实例创建事实，也不引入普通用户自有实例限制。
func checkTestInstanceCreatorRoleSnapshot(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I25-07/08/12：升降权和集群转移不改写创建身份")
	p, admin, cluster := ksmCluster(t)
	adminID := readJSON(t, p, "/session", admin)["account"].(map[string]any)["id"].(string)
	protected := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("protected")}, admin, "protected", 201)
	user, userID := identityUser(t, p, admin, "creator", "USER")
	outsider, _ := identityUser(t, p, admin, "outsider", "USER")
	clusterCall(t, p, "POST", "/clusters/"+cluster+"/transfer", map[string]any{"ownerId": userID}, admin, "to-creator", 200)
	for _, component := range []string{"kube-state-metrics", "Prometheus Agent"} {
		body := map[string]any{"component": component}
		if component == "kube-state-metrics" {
			body["ksm"] = ksmConfig("user-ksm")
		} else {
			body["agent"] = agentConfig("user-agent", "monitoring")
		}
		created := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", body, user, "user-"+bodyName(body), 201)
		if created["creatorUserId"] != userID || created["creatorRole"] != "USER" || created["canManage"] != true {
			t.Fatal(created)
		}
		clusterCall(t, p, "GET", "/instances/"+created["id"].(string), nil, outsider, "", 403)
	}
	clusterCall(t, p, "PATCH", "/accounts/"+userID, map[string]any{"role": "ADMIN"}, admin, "promote", 200)
	promoted := request(t, p, "POST", "/sessions", `{"username":"creator","password":"personal-password"}`, nil, 201).Cookies()[0]
	clusterCall(t, p, "PATCH", "/accounts/"+adminID, map[string]any{"role": "USER"}, promoted, "demote", 200)
	clusterCall(t, p, "POST", "/clusters/"+cluster+"/transfer", map[string]any{"ownerId": adminID}, promoted, "to-demoted", 200)
	demoted := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	got := readJSON(t, p, "/instances/"+protected["id"].(string), demoted)
	if got["creatorRole"] != "ADMIN" || got["creatorUserId"] != adminID || got["canManage"] != false {
		t.Fatal("降级后保护失效", got)
	}
	clusterCall(t, p, "PUT", "/instances/"+protected["id"].(string)+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": ksmConfig("protected")}, demoted, "denied-demoted", 403)
	values := readJSON(t, p, "/clusters/"+cluster+"/instances?page=1", demoted)["instances"].([]any)
	for _, raw := range values {
		value := raw.(map[string]any)
		if value["creatorUserId"] != userID {
			continue
		}
		if value["creatorRole"] != "USER" || value["canManage"] != true {
			t.Fatal("升权重写创建身份", value)
		}
		body := map[string]any{"expectedDraftRevision": 1}
		draft := readJSON(t, p, "/instances/"+value["id"].(string)+"/draft", demoted)
		if value["type"] == "collector" {
			body["agent"] = draft["agent"]
		} else {
			body["ksm"] = draft["ksm"]
		}
		clusterCall(t, p, "PUT", "/instances/"+value["id"].(string)+"/draft", body, demoted, "other-user-"+value["id"].(string), 200)
	}
	if readJSON(t, p, "/instances/"+protected["id"].(string), promoted)["canManage"] != true {
		t.Fatal("当前管理员失去管理权限")
	}
}

// I25-09：在真实受理后、worker 派发前降级管理员，保留集群所有权也必须阻止写入。
func checkTestInstanceDispatchReauthorizesCreatorProtection(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I25-09：受理后降级管理员，在每次实际写入前复核实例权限")
	entered, release := make(chan struct{}), make(chan struct{})
	var once, released sync.Once
	defer released.Do(func() { close(release) })
	p, admin, id, target := lifecycleFixture(t, func(stage, id string) error {
		if stage == "beforeDispatch" {
			once.Do(func() { close(entered) })
			<-release
		}
		return nil
	})
	actorID := readJSON(t, p, "/session", admin)["account"].(map[string]any)["id"].(string)
	other, _ := identityUser(t, p, admin, "dispatch-admin", "ADMIN")
	op := lifecyclePublish(t, p, admin, id)
	select {
	case <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("未到派发屏障")
	}
	clusterCall(t, p, "PATCH", "/accounts/"+actorID, map[string]any{"role": "USER"}, other, "dispatch-demote", 200)
	released.Do(func() { close(release) })
	done := awaitClusterOperation(t, p, other, op["operationId"].(string))
	if done["state"] != "FAILED" || !strings.Contains(done["message"].(string), "管理员创建") {
		t.Fatal("降级后仍取得派发资格", done)
	}
	target.mu.Lock()
	defer target.mu.Unlock()
	if target.writes != 0 {
		t.Fatal("失权后产生目标写入", target.writes)
	}
}

// I25-10：请求已发后撤权只影响后续写入，原 UID 的结果仍由只读恢复确认。
func checkTestInstanceSentResultSurvivesRevocation(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I25-10：已发送后降级并丢失响应，仍只读核验且不重放")
	p, admin, id, target := ksmPublishedFixture(t)
	actor := readJSON(t, p, "/session", admin)["account"].(map[string]any)["id"].(string)
	other, _ := identityUser(t, p, admin, "recovery-admin", "ADMIN")
	first := lifecyclePublish(t, p, admin, id)
	awaitLifecycleState(t, p, admin, first["operationId"].(string), "SUCCEEDED")
	cfg := ksmConfig("instance")
	cfg["resources"].(map[string]any)["requestCPU"] = "200m"
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": cfg}, admin, "next-draft", 200)
	preview := ksmPreview(t, p, admin, id, 2)
	gate := make(chan struct{})
	var released sync.Once
	defer released.Do(func() { close(gate) })
	target.mu.Lock()
	target.block = gate
	target.entered = make(chan struct{})
	target.dropResponse = true
	entered := target.entered
	target.mu.Unlock()
	op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 2, "overwriteDrift": false}, admin, "next-publish", 202)
	select {
	case <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("真实请求尚未发出")
	}
	clusterCall(t, p, "PATCH", "/accounts/"+actor, map[string]any{"role": "USER"}, other, "after-send-demote", 200)
	released.Do(func() { close(gate) })
	demoted := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	for n := 0; n < 5; n++ {
		clusterCall(t, p, "POST", "/operations/"+op["operationId"].(string)+"/refresh", nil, demoted, "read-result", 200)
	}
	awaitLifecycleState(t, p, demoted, op["operationId"].(string), "SUCCEEDED")
	if readJSON(t, p, "/instances/"+id, demoted)["canManage"] != false {
		t.Fatal("失权后仍可管理")
	}
	target.mu.Lock()
	defer target.mu.Unlock()
	if target.writes != 2 {
		t.Fatal("失权后重放或回滚了写入", target.writes)
	}
}

// dropInstanceCreatorSchema 仅用于构造真实历史 schema；不能只改版本标记而保留新字段。
const dropInstanceCreatorSchema = `ALTER TABLE instances DROP COLUMN creator_user_id;ALTER TABLE instances DROP COLUMN creator_role;`

// I25-11：真实 v10 数据库缺少创建角色，迁移不能用当前管理员身份倒推历史。
func checkTestUnknownCreatorMigration(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I25-11：升级真实 v10 schema，未知创建角色默认保护且保留原版本和任务")
	_, target := ksmTarget(t)
	cfg := platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"}
	p, err := platform.Start(context.Background(), cfg)
	if err != nil {
		t.Fatal(err)
	}
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	admin := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	user, userID := identityUser(t, p, admin, "legacy-owner", "USER")
	reg := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "legacy", "ownerId": userID, "context": "explicit", "kubeconfig": target}, admin, "legacy-cluster", 202)
	cluster := awaitClusterOperation(t, p, admin, reg["operationId"].(string))["clusterId"].(string)
	instance := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("lifecycle")}, admin, "legacy-instance", 201)
	id := instance["id"].(string)
	first := lifecyclePublish(t, p, admin, id)
	awaitLifecycleState(t, p, admin, first["operationId"].(string), "SUCCEEDED")
	p.Close(context.Background())
	db, err := sql.Open("sqlite", filepath.Join(cfg.DataDir, "platform.db"))
	if err != nil {
		t.Fatal(err)
	}
	// 关键：删除新版本才存在的字段与身份投影，旧证据缺失时只能保留未知，不能借当前角色补造。
	_, err = db.Exec(dropInstanceCreatorSchema + dropResourceProgressSchema + `DELETE FROM schema_migrations WHERE version>10;DELETE FROM command_results WHERE action='CREATE_INSTANCE';`)
	db.Close()
	if err != nil {
		t.Fatal(err)
	}
	p, err = platform.Start(context.Background(), cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close(context.Background())
	got := readJSON(t, p, "/instances/"+id, user)
	if got["creatorRole"] != "" || got["creatorUserId"] != "" || got["creatorIdentityKnown"] != false || got["canManage"] != false || got["uid"] != "created-uid" {
		t.Fatal("未知创建身份被补造或原身份丢失", got)
	}
	readJSON(t, p, "/instances/"+id+"/draft", user)
	version := readJSON(t, p, "/instances/"+id+"/versions/"+first["versionId"].(string), user)
	resources, ok := version["resources"].([]any)
	if !ok || len(resources) != 1 || resources[0].(map[string]any)["kind"] != "KubeStateMetrics" {
		t.Fatal("I27-U09：真实v10版本被追加或丢失原资源", version)
	}
	progress := resourceProgress(t, p, user, first["operationId"].(string))
	if progress["state"] != "SUCCEEDED" || progress["confirmedUid"] != "created-uid" || progress["possiblySent"] != true {
		t.Fatal("I27-U09：真实v10迁移丢失已确认身份", progress)
	}
	if version["versionId"] != first["versionId"] {
		t.Fatal("迁移改变版本")
	}
	if readJSON(t, p, "/operations/"+first["operationId"].(string), user)["state"] != "SUCCEEDED" {
		t.Fatal("迁移丢失任务")
	}
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": ksmConfig("lifecycle")}, user, "unknown-denied", 403)
	if readJSON(t, p, "/instances/"+id, admin)["canManage"] != true {
		t.Fatal("未知历史阻止当前管理员管理")
	}
}

// I25-09.DELETE：删除沿同一最终写前授权入口，受理不能把旧管理员身份带到实际 DELETE。
func checkTestInstanceDeletionDispatchReauthorizes(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I25-09.DELETE：删除受理后降级不发送实际 DELETE")
	entered, release := make(chan struct{}), make(chan struct{})
	var armed atomic.Bool
	var once, released sync.Once
	defer released.Do(func() { close(release) })
	p, admin, id, target := lifecycleFixture(t, func(stage, id string) error {
		if armed.Load() && stage == "beforeDispatch" {
			once.Do(func() { close(entered) })
			<-release
		}
		return nil
	})
	actorID := readJSON(t, p, "/session", admin)["account"].(map[string]any)["id"].(string)
	other, _ := identityUser(t, p, admin, "delete-admin", "ADMIN")
	first := lifecyclePublish(t, p, admin, id)
	awaitLifecycleState(t, p, admin, first["operationId"].(string), "SUCCEEDED")
	preview := clusterCall(t, p, "POST", "/instances/"+id+"/deletion-previews", map[string]any{}, admin, "delete-preview", 200)
	armed.Store(true)
	op := clusterCall(t, p, "POST", "/instances/"+id+"/deletions", map[string]any{"versionId": first["versionId"], "previewId": preview["previewId"]}, admin, "delete", 202)
	select {
	case <-entered:
	case <-time.After(3 * time.Second):
		t.Fatal("删除未到派发屏障")
	}
	clusterCall(t, p, "PATCH", "/accounts/"+actorID, map[string]any{"role": "USER"}, other, "delete-demote", 200)
	released.Do(func() { close(release) })
	done := awaitClusterOperation(t, p, other, op["operationId"].(string))
	if done["state"] != "FAILED" || !strings.Contains(done["message"].(string), "管理员创建") {
		t.Fatal("删除绕过创建身份保护", done)
	}
	target.mu.Lock()
	defer target.mu.Unlock()
	if target.deleteRequests != 0 || target.cr == nil {
		t.Fatal("失权后删除实际资源")
	}
}
