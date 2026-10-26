// 历史迁移只经公开 HTTP/Kitex 观察；SQL 仅构造独立旧版本的原始夹具。
package platform_test

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/cloudwego/kitex/client"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"github.com/fjsxy/monitor-mgr/kitex_gen/control/controlservice"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/platform"
	ginkgo "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	"gopkg.in/yaml.v3"
)

// migrationFixture 将公开发布转换成独立旧 v12 结构，保留父成功和旧 SM 原始发送事实。
func migrationFixture(t ginkgo.GinkgoTInterface, state string, rename ...bool) (*platform.Runtime, *http.Cookie, *http.Cookie, string, *ksmFixture, platform.Config, map[string]any) {
	t.Helper()
	p, a, u, id, _, f, cfg := historicalFixture(t, nil, true)
	var op map[string]any
	if len(rename) > 0 && rename[0] {
		f.mu.Lock()
		f.migrationNamespaces = []any{map[string]any{"metadata": map[string]any{"name": "monitoring-two", "uid": "new-ns"}}}
		f.mu.Unlock()
		config := ksmConfig("renamed-history")
		config["targetNamespace"] = "monitoring-two"
		clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": config}, a, "rename-first", 200)
		preview := ksmPreview(t, p, a, id, 2)
		op = clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"draftRevision": 2, "previewId": preview["previewId"]}, a, "rename-publish", 202)
	} else {
		op = lifecyclePublish(t, p, a, id)
	}
	done := awaitLifecycleState(t, p, a, op["operationId"].(string), "SUCCEEDED")
	var document map[string]any
	Expect(yaml.Unmarshal([]byte(done["resources"].([]any)[0].(map[string]any)["target"].(map[string]any)["yaml"].(string)), &document)).To(Succeed())
	delete(document["spec"].(map[string]any), "serviceMonitor")
	raw, _ := json.Marshal(document)
	Expect(p.Close(context.Background())).To(Succeed())
	db, e := sql.Open("sqlite", filepath.Join(cfg.DataDir, "platform.db"))
	Expect(e).NotTo(HaveOccurred())
	_, e = db.Exec(dropResourceProgressSchema + `DROP TABLE IF EXISTS instance_management_confirmations;DELETE FROM schema_migrations WHERE version>11;`)
	Expect(e).NotTo(HaveOccurred())
	// 旧KSM契约没有采集意图；真实旧fixture的配置、冻结spec与实际父对象同时裁去新字段。
	_, e = db.Exec(`UPDATE drafts SET config=json_remove(config,'$.serviceMonitor');UPDATE publication_versions SET config=json_remove(config,'$.serviceMonitor'),effective_spec=json_remove(effective_spec,'$.serviceMonitor');UPDATE publication_previews SET desired_spec=json_remove(desired_spec,'$.serviceMonitor');`)
	Expect(e).NotTo(HaveOccurred())
	f.mu.Lock()
	delete(f.cr["spec"].(map[string]any), "serviceMonitor")
	f.mu.Unlock()
	ddl, e := os.ReadFile("testdata/legacy/resources-v12.sql")
	Expect(e).NotTo(HaveOccurred())
	_, e = db.Exec(string(ddl))
	Expect(e).NotTo(HaveOccurred())
	task, e := os.ReadFile("testdata/legacy/task-sm-v12.sql")
	Expect(e).NotTo(HaveOccurred())
	uid := "old-sm-uid"
	if state == "IDENTITY_UNCERTAIN" {
		uid = ""
	}
	task = []byte(strings.ReplaceAll(string(task), "kubestatemetrics/lifecycle", "kubestatemetrics/"+document["metadata"].(map[string]any)["name"].(string)))
	_, e = db.Exec(string(task), sql.Named("version", op["versionId"]), sql.Named("operation", op["operationId"]), sql.Named("attempt", done["attemptId"]), sql.Named("instance", id), sql.Named("document", raw), sql.Named("uid", uid), sql.Named("state", state), sql.Named("operationState", state), sql.Named("possiblySent", true), sql.Named("currentVersion", op["versionId"]))
	Expect(e).NotTo(HaveOccurred())
	_, e = db.Exec(`INSERT INTO schema_migrations VALUES(12)`)
	Expect(e).NotTo(HaveOccurred())
	Expect(db.Close()).To(Succeed())
	f.mu.Lock()
	f.legacySM = map[string]any{"apiVersion": "monitoring.coreos.com/v1", "kind": "ServiceMonitor", "metadata": map[string]any{"name": "old-sm", "namespace": "old-monitoring", "uid": "old-sm-uid", "resourceVersion": "old-rv", "labels": map[string]any{"monitor-mgr.io/user-id": "legacy-user"}}, "spec": map[string]any{"selector": map[string]any{"matchLabels": map[string]any{"legacy": "frozen"}}, "endpoints": []any{map[string]any{"port": "metrics"}}}}
	f.mu.Unlock()
	p, e = platform.Start(context.Background(), cfg)
	Expect(e).NotTo(HaveOccurred())
	t.Cleanup(func() { p.Close(context.Background()) })
	return p, a, u, id, f, cfg, op
}

// 原 SUCCEEDED SM 和不同名称也必须进入盘点，不只展示 pending 子集。
func checkHistoricalMigrationInventory(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I29-U06.inventory-success：原成功步骤、原路径和独立当前 UID/RV/spec 可读")
	p, a, _, id, f, _, op := migrationFixture(t, "SUCCEEDED")
	inventory := clusterCall(t, p, "GET", "/instances/"+id+"/historical-migration", nil, a, "", 200)
	Expect(inventory["instanceId"]).To(Equal(id))
	Expect(inventory["resources"]).To(HaveLen(2))
	resources := inventory["resources"].([]any)
	sm := resources[1].(map[string]any)
	Expect(sm["operationId"]).To(Equal(op["operationId"]))
	Expect(sm["objectPath"]).To(Equal("/apis/monitoring.coreos.com/v1/namespaces/old-monitoring/servicemonitors/old-sm"))
	Expect(sm["originalUid"]).To(Equal("old-sm-uid"))
	Expect(sm["currentUid"]).To(Equal("old-sm-uid"))
	Expect(sm["resourceVersion"]).To(Equal("old-rv"))
	Expect(sm["state"]).To(Equal("SUCCEEDED"))
	ginkgo.By("I29-U08.frozen-bytes：独立旧DDL文档字节指纹保持，不能按JSON等价重算")
	Expect(sm["documentFingerprint"]).To(Equal("sha256:6cb7d3ed9b5587421e3ac99ff7535171b903e0ddfb64c06d670fb9a87ee6f740"))
	ginkgo.AddReportEntry("I29-U08.frozen-bytes", "原始冻结byte指纹与独立固定fixture摘要一致")
	Expect(sm["frozenYaml"]).To(ContainSubstring("legacy: frozen"))
	f.mu.Lock()
	defer f.mu.Unlock()
	Expect(f.smWrites).To(BeZero())
	ginkgo.AddReportEntry("I29-U06.inventory-success", "公开盘点保留已成功旧 SM 原执行事实并独立显示实时资源")
}

// 旧 SM 的处理确认只保存独立事实；未消失的原对象与未知 UID 不能被补认。
func checkHistoricalMigrationResolutions(t ginkgo.GinkgoTInterface) {
	for _, state := range []string{"SUCCEEDED", "IDENTITY_UNCERTAIN"} {
		ginkgo.By("I29-U06.resolve-" + state + "：外部处理前拒绝，GET404后独立留痕")
		p, a, u, id, f, _, op := migrationFixture(t, state)
		inventory := clusterCall(t, p, "GET", "/instances/"+id+"/historical-migration", nil, a, "", 200)
		sm := inventory["resources"].([]any)[1].(map[string]any)
		body := map[string]any{"snapshotId": inventory["snapshotId"], "operationId": op["operationId"], "resourceKey": sm["resourceKey"], "objectPath": sm["objectPath"], "originalUid": sm["originalUid"], "documentFingerprint": sm["documentFingerprint"], "confirmExternalHandling": true}
		path := "/instances/" + id + "/historical-servicemonitor-resolutions"
		clusterCall(t, p, "POST", path, body, u, "ordinary", 403)
		clusterCall(t, p, "POST", path, body, a, "before-cleanup", 409)
		f.mu.Lock()
		f.legacySM = nil
		f.mu.Unlock()
		confirmation := clusterCall(t, p, "POST", path, body, a, "after-cleanup", 200)
		Expect(confirmation["resolvedBy"]).NotTo(BeEmpty())
		Expect(confirmation["resolvedAt"]).NotTo(BeEmpty())
		Expect(confirmation["originalUid"]).To(Equal(sm["originalUid"]))
		Expect(clusterCall(t, p, "POST", path, body, a, "after-cleanup", 200)).To(Equal(confirmation))
		old := clusterCall(t, p, "GET", "/operations/"+op["operationId"].(string), nil, a, "", 200)
		oldSM := old["resources"].([]any)[1].(map[string]any)
		Expect(oldSM["state"]).To(Equal(state))
		Expect(oldSM["possiblySent"]).To(BeTrue())
		Expect(oldSM["confirmedUid"]).To(Equal(sm["originalUid"]))
		f.mu.Lock()
		Expect(f.smWrites).To(BeZero())
		f.mu.Unlock()
		ginkgo.AddReportEntry("I29-U06.resolve-"+state, "确认独立留痕且不改变旧执行事实")
	}
}

// 父 CR 的原身份与旧 SM 处置分开；只有独立核验可释放精确旧操作互斥。
func checkHistoricalParentRelease(t ginkgo.GinkgoTInterface) {
	for _, variant := range []string{"known", "partial", "partial-original-only", "unknown-present", "unknown-absent", "newer-uid", "newer-version", "drift"} {
		ginkgo.By("I29-U07.parent-" + variant)
		p, a, _, id, f, cfg, op := migrationFixture(t, "IDENTITY_UNCERTAIN")
		if variant != "known" && variant != "drift" {
			Expect(p.Close(context.Background())).To(Succeed())
			db, e := sql.Open("sqlite", filepath.Join(cfg.DataDir, "platform.db"))
			Expect(e).NotTo(HaveOccurred())
			switch variant {
			case "partial":
				_, e = db.Exec(`UPDATE instances SET uid='',current_version_id='' WHERE id=?`, id)
			case "partial-original-only":
				_, e = db.Exec(`UPDATE instances SET uid='',current_version_id='' WHERE id=$instance;UPDATE resource_progress SET confirmed_uid='',state='IDENTITY_UNCERTAIN' WHERE attempt_id=(SELECT current_attempt_id FROM operation_lifecycle WHERE operation_id=$operation) AND resource_key='component'`, sql.Named("instance", id), sql.Named("operation", op["operationId"]))
			case "unknown-present", "unknown-absent":
				_, e = db.Exec(`UPDATE instances SET uid='',current_version_id='' WHERE id=$instance;UPDATE operation_resources SET original_uid='' WHERE operation_id=$operation AND resource_key='component';UPDATE resource_progress SET confirmed_uid='',state='IDENTITY_UNCERTAIN' WHERE attempt_id=(SELECT current_attempt_id FROM operation_lifecycle WHERE operation_id=$operation) AND resource_key='component'`, sql.Named("instance", id), sql.Named("operation", op["operationId"]))
			case "newer-uid":
				_, e = db.Exec(`UPDATE instances SET uid='newer-confirmed' WHERE id=?`, id)
			case "newer-version":
				_, e = db.Exec(`INSERT INTO publication_versions SELECT 'newer-version',instance_id,source_revision,config,effective_spec,created_at FROM publication_versions WHERE id=$version;UPDATE instances SET uid='',current_version_id='newer-version' WHERE id=$instance`, sql.Named("version", op["versionId"]), sql.Named("instance", id))
			}
			Expect(e).NotTo(HaveOccurred())
			Expect(db.Close()).To(Succeed())
			p, e = platform.Start(context.Background(), cfg)
			Expect(e).NotTo(HaveOccurred())
			t.Cleanup(func() { p.Close(context.Background()) })
		}
		if variant == "drift" {
			f.mu.Lock()
			f.cr["spec"].(map[string]any)["enabled"] = false
			f.mu.Unlock()
		}
		if variant == "unknown-absent" {
			f.mu.Lock()
			f.cr = nil
			f.mu.Unlock()
		}
		inventory := clusterCall(t, p, "GET", "/instances/"+id+"/historical-migration", nil, a, "", 200)
		bodyFor := func(resource map[string]any) map[string]any {
			return map[string]any{"snapshotId": inventory["snapshotId"], "operationId": resource["operationId"], "resourceKey": resource["resourceKey"], "objectPath": resource["objectPath"], "originalUid": resource["originalUid"], "documentFingerprint": resource["documentFingerprint"], "confirmExternalHandling": true}
		}
		rs := inventory["resources"].([]any)
		f.mu.Lock()
		f.legacySM = nil
		f.mu.Unlock()
		clusterCall(t, p, "POST", "/instances/"+id+"/historical-servicemonitor-resolutions", bodyFor(rs[1].(map[string]any)), a, "resolve-sm", 200)
		status := 409
		if variant == "known" || variant == "partial" || variant == "unknown-absent" {
			status = 200
		}
		result := clusterCall(t, p, "POST", "/instances/"+id+"/historical-operation-releases", bodyFor(rs[0].(map[string]any)), a, "release-parent", status)
		if status == 200 {
			Expect(result["parentReleased"]).To(BeTrue())
			current := clusterCall(t, p, "GET", "/instances/"+id, nil, a, "", 200)
			actor := readJSON(t, p, "/session", a)["account"].(map[string]any)["id"]
			clusterCall(t, p, "POST", "/clusters/"+current["clusterId"].(string)+"/transfer", map[string]any{"ownerId": actor}, a, "resolved-publication-transfer", 200)
			if variant == "unknown-absent" {
				Expect(current["uid"]).To(Equal(""))
			} else {
				Expect(current["uid"]).To(Equal("created-uid"))
			}
			if variant == "partial" {
				Expect(current["currentVersionId"]).To(Equal(""))
			}
			config := ksmConfig("lifecycle")
			config["serviceMonitor"] = map[string]any{"enabled": true}
			clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": config}, a, "enable", 200)
			if variant == "partial" {
				f.mu.Lock()
				f.cr["spec"].(map[string]any)["enabled"] = false
				f.mu.Unlock()
			}
			preview := ksmPreview(t, p, a, id, 2)
			if variant == "partial" {
				Expect(preview["drift"]).To(BeTrue())
			}
			fresh := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 2, "overwriteDrift": true}, a, "new-publish", 202)
			Expect(awaitLifecycleState(t, p, a, fresh["operationId"].(string), "SUCCEEDED")["resources"]).To(HaveLen(1))
		}
		old := clusterCall(t, p, "GET", "/operations/"+op["operationId"].(string), nil, a, "", 200)
		Expect(old["resources"].([]any)[1].(map[string]any)["state"]).To(Equal("IDENTITY_UNCERTAIN"))
		f.mu.Lock()
		Expect(f.smWrites).To(BeZero())
		f.mu.Unlock()
		ginkgo.AddReportEntry("I29-U07.parent-"+variant, "父身份独立核验且旧任务未知事实不变")
	}
}

// 类型化确认绑定原快照；HTTP、直接RPC和幂等命中都不能绕过当前授权或原身份。
func checkHistoricalMigrationBoundaries(t ginkgo.GinkgoTInterface) {
	p, a, u, id, f, _, op := migrationFixture(t, "IDENTITY_UNCERTAIN")
	inv := clusterCall(t, p, "GET", "/instances/"+id+"/historical-migration", nil, a, "", 200)
	sm := inv["resources"].([]any)[1].(map[string]any)
	body := map[string]any{"snapshotId": inv["snapshotId"], "operationId": op["operationId"], "resourceKey": sm["resourceKey"], "objectPath": sm["objectPath"], "originalUid": sm["originalUid"], "documentFingerprint": sm["documentFingerprint"], "confirmExternalHandling": true}
	path := "/instances/" + id + "/historical-servicemonitor-resolutions"
	for _, key := range []string{"snapshotId", "operationId", "resourceKey", "objectPath", "originalUid", "documentFingerprint"} {
		ginkgo.By("I29-U06.resolve-binding." + key)
		bad := map[string]any{}
		for k, v := range body {
			bad[k] = v
		}
		bad[key] = "wrong"
		clusterCall(t, p, "POST", path, bad, a, "wrong-"+key, 409)
		ginkgo.AddReportEntry("I29-U06.resolve-binding."+key, "错绑定拒绝")
	}
	for _, variant := range []string{"foreign", "forbidden"} {
		ginkgo.By("I29-U06.resolve-still-present." + variant)
		f.mu.Lock()
		f.legacySM["metadata"].(map[string]any)["uid"] = "replacement"
		f.smReadFailure = variant == "forbidden"
		f.mu.Unlock()
		clusterCall(t, p, "POST", path, body, a, "present-"+variant, 409)
		ginkgo.AddReportEntry("I29-U06.resolve-still-present."+variant, "对象仍在或读未知不当作404")
	}
	f.mu.Lock()
	f.smReadFailure = false
	f.legacySM = nil
	f.mu.Unlock()
	first := clusterCall(t, p, "POST", path, body, a, "resolved", 200)
	Expect(first["originalUid"]).To(Equal(""))
	changed := map[string]any{}
	for k, v := range body {
		changed[k] = v
	}
	changed["confirmExternalHandling"] = false
	clusterCall(t, p, "POST", path, changed, a, "resolved", 409)
	ginkgo.By("I29-U06.resolve-authorization.rpc：普通用户不能通过Kitex确认")
	cli, e := controlservice.NewClient("historical-migration-test", client.WithHostPorts(p.RPCAddress()))
	Expect(e).NotTo(HaveOccurred())
	session := u.Value
	key := "rpc-ordinary"
	_, e = cli.ResolveHistoricalServiceMonitor(context.Background(), &rpc.RequestContext{SessionToken: &session, IdempotencyKey: &key}, &rpc.HistoricalMigrationConfirmationRequest{Id: id, SnapshotId: inv["snapshotId"].(string), OperationId: op["operationId"].(string), ResourceKey: sm["resourceKey"].(string), ObjectPath: sm["objectPath"].(string), OriginalUid: "", DocumentFingerprint: sm["documentFingerprint"].(string), ConfirmExternalHandling: true})
	var apiError *rpc.APIError
	Expect(errors.As(e, &apiError)).To(BeTrue())
	Expect(apiError.Code).To(Equal("FORBIDDEN"))
	ginkgo.AddReportEntry("I29-U06.resolve-authorization.rpc", "直接RPC复核当前管理员权限")
	// 用另一个管理员执行确认，再降级账号，旧键仍不能回放。
	other, actor := identityUser(t, p, a, "migration-admin", "ADMIN")
	clusterCall(t, p, "POST", path, body, other, "admin-replay", 200)
	clusterCall(t, p, "PATCH", "/accounts/"+actor, map[string]any{"role": "USER", "enabled": true}, a, "downgrade-admin", 200)
	clusterCall(t, p, "POST", path, body, other, "admin-replay", 401)
	ginkgo.AddReportEntry("I29-U06.resolve-authorization.demoted", "失权后幂等键不能绕过授权")
	// 所有已处理旧路径仍被逐次读取，同名替代会阻止新启用。
	ginkgo.By("I29-U06.resolve-race.preview：确认后旧path重现阻止新启用")
	f.mu.Lock()
	f.legacySM = map[string]any{"metadata": map[string]any{"uid": "reappeared", "name": "old-sm", "namespace": "old-monitoring"}}
	f.mu.Unlock()
	cfg := ksmConfig("lifecycle")
	cfg["serviceMonitor"] = map[string]any{"enabled": true}
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": cfg}, a, "enable-after-resolution", 200)
	clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 2}, a, "reappeared", 409)
	f.mu.Lock()
	Expect(f.smWrites).To(BeZero())
	f.mu.Unlock()
	ginkgo.AddReportEntry("I29-U06.resolve-race.preview", "resolution不免除逐次旧路径GET")
}

// 当前Agent真实选择器和namespace标签范围都参与盘点，列表不完整不能当作零影响。
func checkHistoricalAgentImpact(t ginkgo.GinkgoTInterface) {
	p, a, _, id, f, _, _ := migrationFixture(t, "SUCCEEDED", true)
	agent := func(name, ns string, namespaceSelector any) map[string]any {
		return map[string]any{"apiVersion": "monitoring.coreos.com/v1alpha1", "kind": "PrometheusAgent", "metadata": map[string]any{"name": name, "namespace": ns, "uid": name + "-uid"}, "spec": map[string]any{"serviceMonitorSelector": map[string]any{"matchExpressions": []any{map[string]any{"key": "monitor-mgr.io/user-id", "operator": "In", "values": []any{"legacy-user"}}}}, "serviceMonitorNamespaceSelector": namespaceSelector}}
	}
	f.mu.Lock()
	f.migrationNamespaces = []any{map[string]any{"metadata": map[string]any{"name": "old-monitoring", "uid": "old-ns", "labels": map[string]any{"team": "metrics"}}}}
	f.migrationAgentPages = map[string]map[string]any{"": {"items": []any{agent("own", "old-monitoring", nil), agent("outside", "elsewhere", nil)}, "metadata": map[string]any{"continue": "next"}}, "next": {"items": []any{agent("cross", "elsewhere", map[string]any{"matchLabels": map[string]any{"team": "metrics"}})}}}
	f.mu.Unlock()
	ginkgo.By("I29-U06.inventory-agent/I29-U08.rename-ksm：合法首次发布前改名与namespace，旧SM取自己的原冻结路径")
	inv := clusterCall(t, p, "GET", "/instances/"+id+"/historical-migration", nil, a, "", 200)
	Expect(inv["impactComplete"]).To(BeTrue())
	resources := inv["resources"].([]any)
	Expect(resources[0].(map[string]any)["objectPath"]).To(Equal("/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics/renamed-history"))
	Expect(resources[1].(map[string]any)["objectPath"]).To(Equal("/apis/monitoring.coreos.com/v1/namespaces/old-monitoring/servicemonitors/old-sm"))
	Expect(inv["agents"]).To(HaveLen(3))
	for _, raw := range inv["agents"].([]any) {
		item := raw.(map[string]any)
		if item["name"] == "outside" {
			Expect(item["selectedPaths"]).To(BeEmpty())
		} else {
			Expect(item["selectedPaths"]).To(HaveLen(1))
		}
	}
	ginkgo.AddReportEntry("I29-U06.inventory-agent", "完整分页、跨namespace标签与对象表达式分别核验")
	ginkgo.AddReportEntry("I29-U08.rename-ksm", "稳定instance ID保留creator，父原path与SM冻结namespace独立")
	f.mu.Lock()
	f.migrationAgentFailToken = "next"
	f.mu.Unlock()
	ginkgo.By("I29-U06.inventory-unknown.pagination：后页Forbidden不能算无Agent")
	incomplete := clusterCall(t, p, "GET", "/instances/"+id+"/historical-migration", nil, a, "", 200)
	Expect(incomplete["impactComplete"]).To(BeFalse())
	Expect(incomplete["error"]).NotTo(BeEmpty())
	ginkgo.AddReportEntry("I29-U06.inventory-unknown.pagination", "分页失败保持影响未确认")
}

// 新发布受理后仍核验旧SM路径；已有父UID更新丢响应只读恢复，不重放。
func checkHistoricalNewPublicationRecovery(t ginkgo.GinkgoTInterface) {
	for _, variant := range []string{"old-path-reappeared", "parent-response-lost"} {
		ginkgo.By("I29-U06.resolve-race.worker/I29-U05.lost：" + variant)
		p, a, _, id, f, cfg, op := migrationFixture(t, "IDENTITY_UNCERTAIN")
		inv := clusterCall(t, p, "GET", "/instances/"+id+"/historical-migration", nil, a, "", 200)
		body := func(item map[string]any) map[string]any {
			return map[string]any{"snapshotId": inv["snapshotId"], "operationId": op["operationId"], "resourceKey": item["resourceKey"], "objectPath": item["objectPath"], "originalUid": item["originalUid"], "documentFingerprint": item["documentFingerprint"], "confirmExternalHandling": true}
		}
		f.mu.Lock()
		f.legacySM = nil
		f.mu.Unlock()
		rs := inv["resources"].([]any)
		clusterCall(t, p, "POST", "/instances/"+id+"/historical-servicemonitor-resolutions", body(rs[1].(map[string]any)), a, "resolve", 200)
		clusterCall(t, p, "POST", "/instances/"+id+"/historical-operation-releases", body(rs[0].(map[string]any)), a, "release", 200)
		Expect(p.Close(context.Background())).To(Succeed())
		entered, release := make(chan struct{}), make(chan struct{})
		var once sync.Once
		cfg.OperationBoundary = func(stage, operation string) error {
			if variant == "old-path-reappeared" && stage == "beforeDispatch" {
				once.Do(func() { close(entered) })
				<-release
			}
			return nil
		}
		var e error
		p, e = platform.Start(context.Background(), cfg)
		Expect(e).NotTo(HaveOccurred())
		t.Cleanup(func() { p.Close(context.Background()) })
		config := ksmConfig("lifecycle")
		config["serviceMonitor"] = map[string]any{"enabled": true}
		clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": config}, a, "enable", 200)
		preview := ksmPreview(t, p, a, id, 2)
		f.mu.Lock()
		before := f.mutationRequests
		if variant == "parent-response-lost" {
			f.dropResponse = true
			f.crReadFailure = true
		}
		f.mu.Unlock()
		if variant == "parent-response-lost" { // 先保证受理读取成功，只在持久发送后阻断读取。
			f.mu.Lock()
			f.crReadFailure = false
			f.mu.Unlock()
			cfg.OperationBoundary = nil
		}
		fresh := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 2}, a, "fresh", 202)
		if variant == "old-path-reappeared" {
			select {
			case <-entered:
			case <-time.After(5 * time.Second):
				close(release)
				t.Fatal("未到写前屏障")
			}
			f.mu.Lock()
			f.legacySM = map[string]any{"metadata": map[string]any{"name": "old-sm", "namespace": "old-monitoring", "uid": "replacement"}}
			f.mu.Unlock()
			close(release)
			awaitLifecycleState(t, p, a, fresh["operationId"].(string), "FAILED")
			f.mu.Lock()
			Expect(f.mutationRequests).To(Equal(before))
			f.mu.Unlock()
		} else {
			// 请求生效后丢响应；恢复只读核实已知父UID和新冻结意图。
			eventually := awaitLifecycleState(t, p, a, fresh["operationId"].(string), "RESULT_UNCERTAIN", "SUCCEEDED")
			_ = eventually
			f.mu.Lock()
			f.dropResponse = false
			f.mu.Unlock()
			clusterCall(t, p, "POST", "/operations/"+fresh["operationId"].(string)+"/refresh", nil, a, "refresh", 200)
			awaitLifecycleState(t, p, a, fresh["operationId"].(string), "SUCCEEDED")
			f.mu.Lock()
			Expect(f.mutationRequests).To(Equal(before + 1))
			f.mu.Unlock()
		}
		f.mu.Lock()
		Expect(f.smWrites).To(BeZero())
		f.mu.Unlock()
		ginkgo.AddReportEntry("I29-new-publication."+variant, "实际父写前及丢响应边界保持零SM写与恢复禁重放")
	}
}

// 新迁移DDL失败必须保留真实旧schema、原密文、UID与冻结关系；修复冲突可恢复。
func checkHistoricalMigrationRollback(t ginkgo.GinkgoTInterface) {
	for _, version := range []int{10, 12, 13} {
		ginkgo.By(fmt.Sprintf("I29-U08.rollback.v%d", version))
		var p *platform.Runtime
		var a *http.Cookie
		var id string
		var f *ksmFixture
		var cfg platform.Config
		var op map[string]any
		if version == 10 {
			p, a, _, id, _, f, cfg = historicalFixture(t, nil, true)
			op = lifecyclePublish(t, p, a, id)
			awaitLifecycleState(t, p, a, op["operationId"].(string), "SUCCEEDED")
		} else {
			p, a, _, id, f, cfg, op = migrationFixture(t, "IDENTITY_UNCERTAIN")
		}
		Expect(p.Close(context.Background())).To(Succeed())
		db, e := sql.Open("sqlite", filepath.Join(cfg.DataDir, "platform.db"))
		Expect(e).NotTo(HaveOccurred())
		query := `DROP TABLE historical_operation_releases;DROP TABLE historical_servicemonitor_resolutions;DROP TABLE historical_migration_snapshots;DROP TABLE historical_resource_observations;`
		if version == 10 {
			query = dropResourceProgressSchema + dropInstanceCreatorSchema + `UPDATE drafts SET config=json_remove(config,'$.serviceMonitor');UPDATE publication_versions SET config=json_remove(config,'$.serviceMonitor'),effective_spec=json_remove(effective_spec,'$.serviceMonitor');UPDATE publication_previews SET desired_spec=json_remove(desired_spec,'$.serviceMonitor');`
			f.mu.Lock()
			delete(f.cr["spec"].(map[string]any), "serviceMonitor")
			f.mu.Unlock()
		}
		if version < 13 {
			query += `DROP TABLE IF EXISTS instance_management_confirmations;`
		}
		query += fmt.Sprintf(`DELETE FROM schema_migrations WHERE version>%d;CREATE TABLE historical_servicemonitor_resolutions(conflict TEXT);`, version)
		_, e = db.Exec(query)
		Expect(e).NotTo(HaveOccurred())
		var credential, beforeConfig, beforeSpec []byte
		Expect(db.QueryRow(`SELECT credential FROM clusters WHERE id=(SELECT cluster_id FROM instances WHERE id=?)`, id).Scan(&credential)).To(Succeed())
		Expect(db.QueryRow(`SELECT config,effective_spec FROM publication_versions WHERE id=?`, op["versionId"]).Scan(&beforeConfig, &beforeSpec)).To(Succeed())
		Expect(db.Close()).To(Succeed())
		failed, e := platform.Start(context.Background(), cfg)
		if e == nil {
			failed.Close(context.Background())
		}
		Expect(e).To(HaveOccurred())
		db, e = sql.Open("sqlite", filepath.Join(cfg.DataDir, "platform.db"))
		Expect(e).NotTo(HaveOccurred())
		var current int
		Expect(db.QueryRow(`SELECT max(version) FROM schema_migrations`).Scan(&current)).To(Succeed())
		Expect(current).To(Equal(version))
		var afterCredential, afterConfig, afterSpec []byte
		Expect(db.QueryRow(`SELECT credential FROM clusters WHERE id=(SELECT cluster_id FROM instances WHERE id=?)`, id).Scan(&afterCredential)).To(Succeed())
		Expect(db.QueryRow(`SELECT config,effective_spec FROM publication_versions WHERE id=?`, op["versionId"]).Scan(&afterConfig, &afterSpec)).To(Succeed())
		Expect(afterCredential).To(Equal(credential))
		Expect(afterConfig).To(Equal(beforeConfig))
		Expect(afterSpec).To(Equal(beforeSpec))
		snapshots, e := filepath.Glob(filepath.Join(cfg.DataDir, "backups", fmt.Sprintf("schema-%d-to-16-*.db", version)))
		Expect(e).NotTo(HaveOccurred())
		Expect(snapshots).NotTo(BeEmpty())
		_, e = db.Exec(`DROP TABLE historical_servicemonitor_resolutions`)
		Expect(e).NotTo(HaveOccurred())
		Expect(db.Close()).To(Succeed())
		p, e = platform.Start(context.Background(), cfg)
		Expect(e).NotTo(HaveOccurred())
		t.Cleanup(func() { p.Close(context.Background()) })
		currentInstance := clusterCall(t, p, "GET", "/instances/"+id, nil, a, "", 200)
		Expect(currentInstance["uid"]).To(Equal("created-uid"))
		history := clusterCall(t, p, "GET", "/instances/"+id+"/versions/"+op["versionId"].(string), nil, a, "", 200)
		Expect(history["versionId"]).To(Equal(op["versionId"]))
		f.mu.Lock()
		Expect(f.smWrites).To(BeZero())
		f.mu.Unlock()
		ginkgo.AddReportEntry(fmt.Sprintf("I29-U08.rollback.v%d", version), "真实旧schema失败保持原库、密文、冻结字节，移除冲突后恢复公开历史")
	}
}

// singleCRHistoryFixture 使用真实v10结构，配置来源没有本轮采集字段；所有任务先经公开入口受理。
func singleCRHistoryFixture(t ginkgo.GinkgoTInterface, state string, known bool) (*platform.Runtime, *http.Cookie, string, *ksmFixture, map[string]any, map[string]any) {
	var mode atomic.Value
	mode.Store("")
	var f *ksmFixture
	boundary := func(stage, operation string) error {
		value := mode.Load().(string)
		if value == "unsent" && stage == "beforeDispatch" {
			return errors.New("受控未发送退出")
		}
		if value == "sent" && stage == "afterSendMarker" {
			f.mu.Lock()
			f.dropResponse = true
			f.crReadFailure = true
			f.mu.Unlock()
		}
		return nil
	}
	p, a, _, id, _, target, cfg := historicalFixture(t, boundary, true)
	f = target
	config := ksmConfig("lifecycle")
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": config}, a, "legacy-no-collection", 200)
	preview := ksmPreview(t, p, a, id, 2)
	first := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 2}, a, "legacy-first", 202)
	awaitLifecycleState(t, p, a, first["operationId"].(string), "SUCCEEDED")
	operation := first
	if state != "succeeded" {
		config["resources"].(map[string]any)["requestCPU"] = "200m"
		clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 2, "ksm": config}, a, "legacy-update", 200)
		preview = ksmPreview(t, p, a, id, 3)
		mode.Store(state)
		if state == "failed" {
			f.mu.Lock()
			f.rejectWrite = 422
			f.mu.Unlock()
		}
		operation = clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 3}, a, "legacy-pending", 202)
		expected := map[string]string{"failed": "FAILED", "unsent": "CHECKING", "sent": "RESULT_UNCERTAIN"}[state]
		awaitLifecycleState(t, p, a, operation["operationId"].(string), expected)
	}
	Expect(p.Close(context.Background())).To(Succeed())
	db, e := sql.Open("sqlite", filepath.Join(cfg.DataDir, "platform.db"))
	Expect(e).NotTo(HaveOccurred())
	query := dropResourceProgressSchema + dropInstanceCreatorSchema + `DROP TABLE IF EXISTS instance_management_confirmations;DELETE FROM schema_migrations WHERE version>10;UPDATE command_results SET result_json=json_remove(result_json,'$.creatorUserId','$.creatorRole','$.creatorIdentityKnown','$.canManage','$.readOnlyReason','$.managementConfirmation') WHERE action='CREATE_INSTANCE';`
	if !known {
		query += `DELETE FROM command_results WHERE action='CREATE_INSTANCE';`
	}
	_, e = db.Exec(query)
	Expect(e).NotTo(HaveOccurred())
	Expect(db.Close()).To(Succeed())
	mode.Store("")
	f.mu.Lock()
	f.rejectWrite = 0
	f.dropResponse = false
	f.crReadFailure = false
	f.mu.Unlock()
	p, e = platform.Start(context.Background(), cfg)
	Expect(e).NotTo(HaveOccurred())
	t.Cleanup(func() { p.Close(context.Background()) })
	return p, a, id, f, first, operation
}

// 无旧SM的历史实例不需制造迁移任务，新的明确父发布才启用；旧恢复仍固定原单CR语义。
func checkHistoricalSingleCR(t ginkgo.GinkgoTInterface) {
	for _, known := range []bool{true, false} {
		idCase := "I29-U01/U02"
		if !known {
			idCase = "I29-U03"
		}
		ginkgo.By(idCase)
		p, a, id, f, first, _ := singleCRHistoryFixture(t, "succeeded", known)
		versionPath := "/instances/" + id + "/versions/" + first["versionId"].(string)
		old := clusterCall(t, p, "GET", versionPath, nil, a, "", 200)
		Expect(old["yaml"]).NotTo(ContainSubstring("serviceMonitor:"))
		Expect(old["resources"]).To(HaveLen(1))
		f.mu.Lock()
		before := f.mutationRequests
		f.mu.Unlock()
		clusterCall(t, p, "POST", "/instances/"+id+"/management-confirmations", map[string]any{"allowClusterManagers": true}, a, "allow", 200)
		clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, a, "refresh-instance", 200)
		clusterCall(t, p, "POST", "/operations/"+first["operationId"].(string)+"/refresh", nil, a, "refresh-operation", 200)
		current := clusterCall(t, p, "GET", "/instances/"+id, nil, a, "", 200)
		f.mu.Lock()
		Expect(f.mutationRequests).To(Equal(before))
		Expect(f.smWrites).To(BeZero())
		f.mu.Unlock()
		Expect(current["creatorRole"]).To(Equal(""))
		config := ksmConfig("lifecycle")
		config["serviceMonitor"] = map[string]any{"enabled": true}
		clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 2, "ksm": config}, a, "explicit-enable", 200)
		if !known {
			Expect(current["creatorUserId"]).To(Equal(""))
			clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 3}, a, "unknown-creator", 409)
			cli, e := controlservice.NewClient("unknown-creator", client.WithHostPorts(p.RPCAddress()))
			Expect(e).NotTo(HaveOccurred())
			session := a.Value
			key := "unknown-creator-rpc"
			requestContext := &rpc.RequestContext{SessionToken: &session, IdempotencyKey: &key}
			_, e = cli.PreviewPublication(context.Background(), requestContext, &rpc.PreviewRequest{Id: id, DraftRevision: 3})
			var apiError *rpc.APIError
			Expect(errors.As(e, &apiError)).To(BeTrue())
			Expect(apiError.Code).To(Equal("CONFLICT"))
			ginkgo.AddReportEntry("I29-U03.rpc-preview", "未知creator直连RPC不能启用采集")
			_, e = cli.PublishConfiguration(context.Background(), requestContext, &rpc.PublishRequest{Id: id, PreviewId: "forged", DraftRevision: 3})
			Expect(errors.As(e, &apiError)).To(BeTrue())
			Expect(apiError.Code).To(Equal("CONFLICT"))
			ginkgo.AddReportEntry("I29-U03.rpc-publish", "确认管理权限不补造creator")

		} else {
			creator := current["creatorUserId"]
			other, _ := identityUser(t, p, a, "other-publisher", "ADMIN")
			preview := ksmPreview(t, p, other, id, 3)
			Expect(preview["rightYAML"]).To(ContainSubstring("monitor-mgr.io/user-id: " + creator.(string)))
			published := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 3}, other, "new-explicit", 202)
			done := awaitLifecycleState(t, p, other, published["operationId"].(string), "SUCCEEDED")
			Expect(done["resources"]).To(HaveLen(1))
			Expect(clusterCall(t, p, "GET", "/instances/"+id, nil, other, "", 200)["uid"]).To(Equal("created-uid"))
		}
		Expect(clusterCall(t, p, "GET", versionPath, nil, a, "", 200)).To(Equal(old))
		ginkgo.AddReportEntry(idCase, "真实v10单CR升级无自动启用，新明确父发布和未知creator边界独立")
	}
	for _, state := range []string{"failed", "unsent", "sent"} {
		idCase := "I29-U04." + state
		ginkgo.By(idCase)
		p, a, id, f, _, operation := singleCRHistoryFixture(t, state, true)
		versionPath := "/instances/" + id + "/versions/" + operation["versionId"].(string)
		old := clusterCall(t, p, "GET", versionPath, nil, a, "", 200)
		Expect(old["yaml"]).NotTo(ContainSubstring("serviceMonitor:"))
		f.mu.Lock()
		before := f.mutationRequests
		f.smReadFailure = true
		f.crd = nil
		f.mu.Unlock()
		clusterCall(t, p, "POST", "/operations/"+operation["operationId"].(string)+"/refresh", nil, a, "readonly", 200)
		if state != "sent" {
			status, action := "FAILED", "retry"
			if state == "unsent" {
				status, action = "WAITING_CONFIRMATION", "continue-unsent"
			}
			awaitLifecycleState(t, p, a, operation["operationId"].(string), status)
			config := ksmConfig("lifecycle")
			config["resources"].(map[string]any)["requestCPU"] = "999m"
			clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 3, "ksm": config}, a, "new-draft", 200)
			retried := clusterCall(t, p, "POST", "/operations/"+operation["operationId"].(string)+"/"+action, lifecycleRevision(t, p, a, operation["operationId"].(string)), a, "explicit-original", 202)
			Expect(retried["versionId"]).To(Equal(operation["versionId"]))
			awaitLifecycleState(t, p, a, operation["operationId"].(string), "SUCCEEDED")
			before++
		} else {
			awaitLifecycleState(t, p, a, operation["operationId"].(string), "SUCCEEDED")
		}
		Expect(clusterCall(t, p, "GET", versionPath, nil, a, "", 200)).To(Equal(old))
		f.mu.Lock()
		Expect(f.mutationRequests).To(Equal(before))
		Expect(f.smWrites).To(BeZero())
		Expect(f.cr["spec"].(map[string]any)).NotTo(HaveKey("serviceMonitor"))
		Expect(f.cr["spec"].(map[string]any)["podResources"].(map[string]any)["requests"].(map[string]any)["cpu"]).To(Equal("200m"))
		f.mu.Unlock()
		ginkgo.AddReportEntry(idCase, "原版本单CR恢复不依赖新SM能力，不混入新草稿")
	}
}

// checkHistoricalDeletionRelease 将独立旧删除夹具从外部清理核验到明确收尾，未知发送事实仍原样保留。
func checkHistoricalDeletionRelease(t ginkgo.GinkgoTInterface) {
	for _, variant := range []string{"clear", "replacement-parent", "replacement-sm", "workload", "read-denied", "wrong-snapshot", "wrong-uid", "unresolved-sm", "ordinary"} {
		ginkgo.By("I23-R10.delete-" + variant)
		p, a, u, id, f, cfg, op := migrationFixture(t, "IDENTITY_UNCERTAIN")
		Expect(p.Close(context.Background())).To(Succeed())
		db, e := sql.Open("sqlite", filepath.Join(cfg.DataDir, "platform.db"))
		Expect(e).NotTo(HaveOccurred())
		_, e = db.Exec(`INSERT INTO deletion_operations(operation_id,instance_id,version_id,original_uid,cr_path,target_namespace,preview_id) SELECT operation_id,instance_id,version_id,'created-uid','/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics/lifecycle','monitoring','' FROM publication_operations WHERE operation_id=$operation; DELETE FROM publication_operations WHERE operation_id=$operation; UPDATE operations SET action='DELETE',state='DELETION_VERIFYING' WHERE id=$operation; UPDATE resource_progress SET state='DELETION_VERIFYING',possibly_sent=1 WHERE attempt_id=(SELECT current_attempt_id FROM operation_lifecycle WHERE operation_id=$operation)`, sql.Named("operation", op["operationId"]))
		Expect(e).NotTo(HaveOccurred())
		Expect(db.Close()).To(Succeed())
		f.mu.Lock()
		f.cr = nil
		f.legacySM = nil
		f.owned = map[string][]any{}
		f.mu.Unlock()
		p, e = platform.Start(context.Background(), cfg)
		Expect(e).NotTo(HaveOccurred())
		t.Cleanup(func() { p.Close(context.Background()) })
		inv := clusterCall(t, p, "GET", "/instances/"+id+"/historical-migration", nil, a, "", 200)
		rs := inv["resources"].([]any)
		bodyFor := func(v map[string]any) map[string]any {
			return map[string]any{"snapshotId": inv["snapshotId"], "operationId": v["operationId"], "resourceKey": v["resourceKey"], "objectPath": v["objectPath"], "originalUid": v["originalUid"], "documentFingerprint": v["documentFingerprint"], "confirmExternalHandling": true}
		}
		if variant != "unresolved-sm" {
			clusterCall(t, p, "POST", "/instances/"+id+"/historical-servicemonitor-resolutions", bodyFor(rs[1].(map[string]any)), a, "resolve-old-delete-sm", 200)
		}
		before := clusterCall(t, p, "GET", "/operations/"+op["operationId"].(string), nil, a, "", 200)
		f.mu.Lock()
		switch variant {
		case "replacement-parent":
			f.cr = map[string]any{"metadata": map[string]any{"uid": "replacement"}}
		case "replacement-sm":
			f.legacySM = map[string]any{"metadata": map[string]any{"uid": "replacement-sm"}}
		case "workload":
			f.owned["/apis/apps/v1/namespaces/monitoring/deployments"] = []any{map[string]any{"metadata": map[string]any{"name": "leftover", "namespace": "monitoring", "uid": "leftover", "ownerReferences": []any{map[string]any{"uid": "created-uid", "controller": true}}}}}
		case "read-denied":
			f.readFailure = true
		}
		f.mu.Unlock()
		body := bodyFor(rs[0].(map[string]any))
		if variant == "wrong-uid" {
			body["originalUid"] = "replacement"
		}
		if variant == "wrong-snapshot" {
			body["snapshotId"] = "unknown-snapshot"
		}
		status := 409
		client := a
		if variant == "clear" {
			status = 200
		}
		if variant == "ordinary" {
			status = 403
			client = u
		}
		result := clusterCall(t, p, "POST", "/instances/"+id+"/historical-operation-releases", body, client, "release-old-delete", status)
		if variant == "clear" {
			Expect(result["parentReleased"]).To(BeTrue())
			current := clusterCall(t, p, "GET", "/instances/"+id, nil, a, "", 200)
			Expect(current["lifecycle"]).To(Equal("DELETED"))
			Expect(clusterCall(t, p, "POST", "/instances/"+id+"/historical-operation-releases", body, a, "release-old-delete", 200)).To(Equal(result))
		}
		current := clusterCall(t, p, "GET", "/instances/"+id, nil, a, "", 200)
		inventory := clusterCall(t, p, "GET", "/instances/"+id+"/historical-migration", nil, a, "", 200)
		if variant == "clear" {
			Expect(inventory["protectionOperationId"]).To(Equal(""))
		} else {
			Expect(current["lifecycle"]).NotTo(Equal("DELETED"))
			Expect(inventory["protectionOperationId"]).To(Equal(op["operationId"]))
		}
		after := clusterCall(t, p, "GET", "/operations/"+op["operationId"].(string), nil, a, "", 200)
		if variant == "clear" {
			Expect(after["state"]).To(Equal("HISTORICAL_RESOLVED"))
		}
		for n, raw := range before["resources"].([]any) {
			old := raw.(map[string]any)
			now := after["resources"].([]any)[n].(map[string]any)
			for _, key := range []string{"state", "possiblySent", "confirmedUid", "target"} {
				Expect(now[key]).To(Equal(old[key]))
			}
		}
		if variant == "clear" {
			clusterID := current["clusterId"].(string)
			actor := readJSON(t, p, "/session", a)["account"].(map[string]any)["id"]
			clusterCall(t, p, "POST", "/clusters/"+clusterID+"/transfer", map[string]any{"ownerId": actor}, a, "resolved-delete-transfer", 200)
			fresh := clusterCall(t, p, "POST", "/clusters/"+clusterID+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("unresolved-after-history")}, a, "fresh-unresolved", 201)
			f.mu.Lock()
			f.dropResponse = true
			f.mu.Unlock()
			pending := lifecyclePublish(t, p, a, fresh["id"].(string))
			awaitLifecycleState(t, p, a, pending["operationId"].(string), "IDENTITY_UNCERTAIN")
			clusterCall(t, p, "POST", "/clusters/"+clusterID+"/transfer", map[string]any{"ownerId": actor}, a, "other-pending-transfer", 409)
			clusterCall(t, p, "POST", "/clusters/"+clusterID+"/unregister", map[string]any{}, a, "other-pending-unregister", 409)
		}
		f.mu.Lock()
		Expect(f.smWrites).To(BeZero())
		f.mu.Unlock()
	}
}
