// 历史 exporter 从真实 v10 数据库升级后，只有新的明确发布才能加入配套采集配置。
package platform_test

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"net/http"
	"path/filepath"
	"strings"
	"sync/atomic"

	"github.com/cloudwego/kitex/client"
	"github.com/fjsxy/monitor-mgr/internal/platform"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"github.com/fjsxy/monitor-mgr/kitex_gen/control/controlservice"
	ginkgo "github.com/onsi/ginkgo/v2"
)

// legacyExporterFixture 先走公开业务，再真实裁掉历史不存在的结构；不靠回拨版本标记伪造旧库。
func legacyExporterFixture(t ginkgo.GinkgoTInterface, state string, known bool) (*platform.Runtime, *http.Cookie, string, *ksmFixture, platform.Config, map[string]any, map[string]any) {
	t.Helper()
	f, kubeconfig := ksmTarget(t)
	var paused atomic.Bool
	cfg := platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", OperationBoundary: func(stage, id string) error {
		if paused.Load() && stage == "beforeDispatch" {
			return errors.New("历史未发送夹具")
		}
		return nil
	}}
	p, e := platform.Start(context.Background(), cfg)
	if e != nil {
		t.Fatal(e)
	}
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	owner := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"]
	reg := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "legacy-exporter", "ownerId": owner, "context": "explicit", "kubeconfig": kubeconfig}, c, "register", 202)
	cluster := awaitClusterOperation(t, p, c, reg["operationId"].(string))["clusterId"].(string)
	i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("legacy-exporter")}, c, "create", 201)
	id := i["id"].(string)
	first := lifecyclePublish(t, p, c, id)
	awaitLifecycleState(t, p, c, first["operationId"].(string), "SUCCEEDED")
	op := first
	if state != "succeeded" {
		next := ksmConfig("legacy-exporter")
		next["resources"].(map[string]any)["requestCPU"] = "200m"
		clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": next}, c, "edit", 200)
		f.mu.Lock()
		f.rejectWrite = 0
		f.dropResponse = state == "sent"
		if state == "failed" {
			f.rejectWrite = 422
		}
		f.mu.Unlock()
		paused.Store(state == "unsent")
		preview := ksmPreview(t, p, c, id, 2)
		op = clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 2}, c, "legacy-pending", 202)
		expected := map[string]string{"failed": "FAILED", "unsent": "CHECKING", "sent": "RESULT_UNCERTAIN"}[state]
		awaitLifecycleState(t, p, c, op["operationId"].(string), expected)
	}
	if e = p.Close(context.Background()); e != nil {
		t.Fatal(e)
	}
	// 关键：当前产品曾创建的 SM 仅是夹具前置，明确核对本夹具原 UID 后移除，才代表旧单资源集群。
	f.mu.Lock()
	if f.sm == nil || f.sm["metadata"].(map[string]any)["uid"] != "service-monitor-uid" {
		t.Fatal("旧集群夹具缺少自己确认的SM身份")
	}
	f.sm = nil
	f.smWrites = 0
	f.smRequests = 0
	f.rejectWrite = 0
	f.dropResponse = false
	f.mu.Unlock()
	db, e := sql.Open("sqlite", filepath.Join(cfg.DataDir, "platform.db"))
	if e != nil {
		t.Fatal(e)
	}
	q := dropInstanceCreatorSchema + `DELETE FROM schema_migrations WHERE version>10;
UPDATE command_results SET result_json=json_remove(result_json,'$.creatorUserId','$.creatorRole','$.creatorIdentityKnown','$.canManage','$.readOnlyReason','$.managementConfirmation') WHERE action='CREATE_INSTANCE';
UPDATE publication_previews SET storage_fingerprint='' WHERE instance_id IN (SELECT id FROM instances WHERE component='kube-state-metrics');`
	if !known {
		q += `DELETE FROM command_results WHERE action='CREATE_INSTANCE';`
	}
	if _, e = db.Exec(q); e != nil {
		t.Fatal(e)
	}
	db.Close()
	paused.Store(false)
	p, e = platform.Start(context.Background(), cfg)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { p.Close(context.Background()) })
	return p, c, id, f, cfg, first, op
}

// assertLegacyFrozen 从公开版本与操作读模型确认历史资源集合没有被追溯改变。
func assertLegacyFrozen(t ginkgo.GinkgoTInterface, p *platform.Runtime, c *http.Cookie, id string, op map[string]any) {
	t.Helper()
	v := readJSON(t, p, "/instances/"+id+"/versions/"+op["versionId"].(string), c)
	resources := v["resources"].([]any)
	o := readJSON(t, p, "/operations/"+op["operationId"].(string), c)
	if len(resources) != 1 || resources[0].(map[string]any)["kind"] != "KubeStateMetrics" || len(o["resources"].([]any)) != 1 {
		t.Fatal("旧冻结集合被追溯追加", v, o)
	}
}

func checkHistoricalCompanionPublication(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I29-U01/U02：真实v10升级、创建者补齐、权限确认及轮询零SM写，新明确发布才补建")
	p, c, id, f, _, first, _ := legacyExporterFixture(t, "succeeded", true)
	original := readJSON(t, p, "/instances/"+id, c)
	creator := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"]
	if original["creatorUserId"] != creator || original["creatorRole"] != "" || original["uid"] != "created-uid" {
		t.Fatal("历史事实错误", original)
	}
	assertLegacyFrozen(t, p, c, id, first)
	other, _ := identityUser(t, p, c, "legacy-publisher", "ADMIN")
	clusterCall(t, p, "POST", "/instances/"+id+"/management-confirmations", map[string]any{"allowClusterManagers": true}, other, "confirm", 200)
	for n := 0; n < 3; n++ {
		clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, other, "", 200)
		clusterCall(t, p, "POST", "/operations/"+first["operationId"].(string)+"/refresh", nil, other, "", 200)
	}
	f.mu.Lock()
	before := f.writes
	if f.smRequests != 0 || f.sm != nil {
		t.Fatal("升级或只读入口隐式补建")
	}
	f.mu.Unlock()
	preview := ksmPreview(t, p, other, id, 1)
	if !strings.Contains(preview["rightYAML"].(string), "monitor-mgr.io/user-id: "+creator.(string)) {
		t.Fatal("误用当前发布者身份", preview)
	}
	op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1}, other, "explicit-backfill", 202)
	done := awaitLifecycleState(t, p, other, op["operationId"].(string), "SUCCEEDED")
	if op["versionId"] == first["versionId"] || len(done["resources"].([]any)) != 2 {
		t.Fatal("新旧发布未区分", done)
	}
	v := readJSON(t, p, "/instances/"+id+"/versions/"+op["versionId"].(string), other)
	if len(v["resources"].([]any)) != 2 {
		t.Fatal("新版本缺少SM", v)
	}
	assertLegacyFrozen(t, p, other, id, first)
	f.mu.Lock()
	defer f.mu.Unlock()
	var body map[string]any
	json.Unmarshal(f.lastWrite, &body)
	meta := body["metadata"].(map[string]any)
	if f.writes != before+1 || meta["uid"] != "created-uid" || meta["resourceVersion"] == nil || f.smRequests != 1 || f.sm["metadata"].(map[string]any)["labels"].(map[string]any)["monitor-mgr.io/user-id"] != creator {
		t.Fatal("补建未保留原UID/RV或创建关系", body)
	}
}

// I29-U03：管理员可确认管理权限，但不能补造未知的历史创建用户。
func checkHistoricalCompanionUnknownCreator(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I29-U03：历史创建用户未知时，HTTP和直接Kitex都拒绝配套发布且零业务写")
	p, c, id, f, _, first, _ := legacyExporterFixture(t, "succeeded", false)
	clusterCall(t, p, "POST", "/instances/"+id+"/management-confirmations", map[string]any{"allowClusterManagers": true}, c, "allow-unknown", 200)
	current := readJSON(t, p, "/instances/"+id, c)
	if current["creatorUserId"] != "" || current["canManage"] != true {
		t.Fatal("确认补造了创建用户", current)
	}
	f.mu.Lock()
	before := f.writes
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "", 409)
	clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": "historical-preview", "draftRevision": 1}, c, "unknown-http", 409)
	cli, e := controlservice.NewClient("historical-companion", client.WithHostPorts(p.RPCAddress()))
	if e != nil {
		t.Fatal(e)
	}
	key, session := "unknown-rpc", c.Value
	ctx := &rpc.RequestContext{RequestId: key, SessionToken: &session, IdempotencyKey: &key}
	_, e = cli.PreviewPublication(context.Background(), ctx, &rpc.PreviewRequest{Id: id, DraftRevision: 1})
	var apiErr *rpc.APIError
	if !errors.As(e, &apiErr) || apiErr.Code != "CONFLICT" || !strings.Contains(apiErr.Message, "创建用户未知") {
		t.Fatal("RPC预览未保护未知身份", e)
	}
	_, e = cli.PublishConfiguration(context.Background(), ctx, &rpc.PublishRequest{Id: id, PreviewId: "historical-preview", DraftRevision: 1})
	if !errors.As(e, &apiErr) || apiErr.Code != "CONFLICT" || !strings.Contains(apiErr.Message, "创建用户未知") {
		t.Fatal("RPC发布未保护未知身份", e)
	}
	assertLegacyFrozen(t, p, c, id, first)
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.writes != before || f.smRequests != 0 {
		t.Fatal("未知身份写入了目标")
	}
}

// I29-U04：人工重试/继续仍消费原冻结目标；新草稿和新配套默认值不能影响旧操作。
func checkHistoricalCompanionOldOperations(t ginkgo.GinkgoTInterface) {
	for _, state := range []string{"failed", "unsent", "sent"} {
		ginkgo.By("I29-U04：旧操作升级与恢复 · " + state)
		p, c, id, f, _, first, op := legacyExporterFixture(t, state, true)
		oid := op["operationId"].(string)
		f.mu.Lock()
		// 关键：旧冻结操作不依赖新SM的CRD或创建权限，恢复不能转而执行新发布预检。
		f.smUnavailable = true
		f.denySM = "create"
		before := f.writes
		f.mu.Unlock()
		assertLegacyFrozen(t, p, c, id, first)
		assertLegacyFrozen(t, p, c, id, op)
		clusterCall(t, p, "POST", "/operations/"+oid+"/refresh", nil, c, "", 200)
		if state == "sent" {
			awaitLifecycleState(t, p, c, oid, "SUCCEEDED")
		} else {
			expected, action := "FAILED", "retry"
			if state == "unsent" {
				expected, action = "WAITING_CONFIRMATION", "continue-unsent"
			}
			awaitLifecycleState(t, p, c, oid, expected)
			f.mu.Lock()
			if f.writes != before || f.smRequests != 0 {
				t.Fatal("升级或轮询重放旧操作")
			}
			f.mu.Unlock()
			next := ksmConfig("legacy-exporter")
			next["resources"].(map[string]any)["requestCPU"] = "900m"
			clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 2, "ksm": next}, c, "new-draft", 200)
			again := clusterCall(t, p, "POST", "/operations/"+oid+"/"+action, lifecycleRevision(t, p, c, oid), c, "repair-original", 202)
			if again["versionId"] != op["versionId"] || again["attemptId"] == op["attemptId"] {
				t.Fatal("旧操作重建了版本", again)
			}
			awaitLifecycleState(t, p, c, oid, "SUCCEEDED")
			before++
		}
		assertLegacyFrozen(t, p, c, id, op)
		f.mu.Lock()
		if f.writes != before || f.smRequests != 0 || f.sm != nil || f.cr["spec"].(map[string]any)["podResources"].(map[string]any)["requests"].(map[string]any)["cpu"] != "200m" {
			t.Fatal("旧操作带入新SM或新草稿", f.writes, f.smRequests)
		}
		f.mu.Unlock()
	}
}

// I29-U05：历史补建与新 exporter 共享身份和未知结果保护，不建立特殊接管通道。
func checkHistoricalCompanionProtection(t ginkgo.GinkgoTInterface) {
	for _, variant := range []string{"uid", "drift", "foreign", "permission", "lost"} {
		ginkgo.By("I29-U05：历史明确补建安全边界 · " + variant)
		p, c, id, f, cfg, first, _ := legacyExporterFixture(t, "succeeded", true)
		f.mu.Lock()
		before := f.writes
		switch variant {
		case "uid":
			f.cr["metadata"].(map[string]any)["uid"] = "foreign-exporter"
		case "drift":
			f.cr["spec"].(map[string]any)["enabled"] = false
		case "foreign":
			f.sm = map[string]any{"metadata": map[string]any{"uid": "foreign-sm", "name": "monitor-mgr-legacy-exporter", "namespace": "monitoring"}, "spec": map[string]any{}}
		case "permission":
			f.denySM = "create"
		}
		f.mu.Unlock()
		if variant == "uid" || variant == "foreign" || variant == "permission" {
			status := 409
			if variant == "permission" {
				status = 403
			}
			clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "", status)
			f.mu.Lock()
			if before != f.writes || f.smRequests != 0 {
				t.Fatal("历史预检失败仍写目标")
			}
			f.mu.Unlock()
		} else {
			preview := ksmPreview(t, p, c, id, 1)
			body := map[string]any{"draftRevision": 1, "previewId": preview["previewId"], "overwriteDrift": false}
			if variant == "drift" {
				if preview["drift"] != true {
					t.Fatal("历史漂移未展示", preview)
				}
				clusterCall(t, p, "POST", "/instances/"+id+"/publications", body, c, "no-overwrite", 409)
				f.mu.Lock()
				if before != f.writes || f.smRequests != 0 {
					t.Fatal("未确认漂移仍写目标")
				}
				f.mu.Unlock()
				body["overwriteDrift"] = true
			} else {
				f.mu.Lock()
				f.dropSM = true
				f.mu.Unlock()
			}
			op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", body, c, "explicit-backfill", 202)
			if variant == "drift" {
				awaitLifecycleState(t, p, c, op["operationId"].(string), "SUCCEEDED")
			} else {
				oid := op["operationId"].(string)
				awaitLifecycleState(t, p, c, oid, "IDENTITY_UNCERTAIN")
				p.Close(context.Background())
				f.mu.Lock()
				f.dropSM = false
				f.smReadFailure = false
				requests := f.smRequests
				componentWrites := f.writes
				f.mu.Unlock()
				restarted, e := platform.Start(context.Background(), cfg)
				if e != nil {
					t.Fatal(e)
				}
				defer restarted.Close(context.Background())
				p = restarted
				clusterCall(t, p, "POST", "/operations/"+oid+"/refresh", nil, c, "", 200)
				done := awaitLifecycleState(t, p, c, oid, "IDENTITY_UNCERTAIN")
				rs := done["resources"].([]any)
				if rs[0].(map[string]any)["state"] != "SUCCEEDED" || rs[1].(map[string]any)["confirmedUid"] != "" || readJSON(t, p, "/instances/"+id, c)["uid"] != "created-uid" {
					t.Fatal("丢响应丢失原exporter或认领SM", done)
				}
				clusterCall(t, p, "POST", "/operations/"+oid+"/retry", lifecycleRevision(t, p, c, oid), c, "forbid-replay", 409)
				f.mu.Lock()
				if f.smRequests != requests || f.writes != componentWrites {
					t.Fatal("未知SM恢复重放目标")
				}
				f.mu.Unlock()
			}
		}
		assertLegacyFrozen(t, p, c, id, first)
	}
}
