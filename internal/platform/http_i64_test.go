package platform_test

import (
	"context"
	"database/sql"
	"path/filepath"

	"github.com/cloudwego/kitex/client"
	"github.com/fjsxy/monitor-mgr/internal/platform"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"github.com/fjsxy/monitor-mgr/kitex_gen/control/controlservice"
	ginkgo "github.com/onsi/ginkgo/v2"
)

// REG19-HTTP-I64-001：HTTP不安全修订以字符串精确往返，真实RPC保留i64。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestWorkbenchHTTPInt64RoundTrip(t ginkgo.GinkgoTInterface) {
	// 接下来REG19-HTTP-I64-001：HTTP不安全修订以字符串精确往返，真实RPC保留i64。
	ginkgo.By("REG19-HTTP-I64-001：HTTP不安全修订以字符串精确往返，真实RPC保留i64。")

	_, target := ksmTarget(t)
	cfg := platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"}
	p, e := platform.Start(context.Background(), cfg)
	if e != nil {
		t.Fatal(e)
	}
	defer func() { p.Close(context.Background()) }()
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	owner := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"].(string)
	op := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "i64", "ownerId": owner, "context": "explicit", "kubeconfig": target}, c, "register", 202)
	cl := awaitClusterOperation(t, p, c, op["operationId"].(string))["clusterId"].(string)
	id := clusterCall(t, p, "POST", "/clusters/"+cl+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("large-revision")}, c, "create", 201)["id"].(string)
	small := clusterCall(t, p, "GET", "/instances/"+id+"/draft", nil, c, "", 200)
	if small["revision"] != float64(1) {
		t.Fatal("安全范围应兼容JSON number")
	}
	p.Close(context.Background())
	db, e := sql.Open("sqlite", filepath.Join(cfg.DataDir, "platform.db"))
	if e != nil {
		t.Fatal(e)
	}
	// 受控持久库前置：业务通过HTTP建立，仅把修订推进至无法用JS number表达的边界。
	if _, e = db.Exec(`UPDATE drafts SET revision=9007199254740992 WHERE instance_id=?`, id); e != nil {
		t.Fatal(e)
	}
	db.Close()
	p, e = platform.Start(context.Background(), cfg)
	if e != nil {
		t.Fatal(e)
	}
	large := clusterCall(t, p, "GET", "/instances/"+id+"/draft", nil, c, "", 200)
	if large["revision"] != "9007199254740992" {
		t.Fatalf("HTTP i64必须为精确字符串，got=%v (%T)", large["revision"], large["revision"])
	}
	body := map[string]any{"expectedDraftRevision": large["revision"], "ksm": ksmConfig("large-revision")}
	saved := clusterCall(t, p, "PUT", "/instances/"+id+"/draft", body, c, "save-large", 200)
	if saved["revision"] != "9007199254740993" {
		t.Fatal("修订递增发生舍入", saved["revision"])
	}
	cli, e := controlservice.NewClient("workbench-i64", client.WithHostPorts(p.RPCAddress()))
	if e != nil {
		t.Fatal(e)
	}
	token := c.Value
	direct, e := cli.GetDraft(context.Background(), &rpc.RequestContext{SessionToken: &token}, &rpc.InstanceID{Id: id})
	if e != nil || direct.Revision != 9007199254740993 {
		t.Fatalf("RPC i64不应改变: revision=%v error=%v", direct, e)
	}
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", body, c, "stale-large", 409)
	preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": saved["revision"]}, c, "", 200)
	if preview["draftRevision"] != "9007199254740993" {
		t.Fatal("预览修订丢失精度")
	}
	publication := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"draftRevision": preview["draftRevision"], "previewId": preview["previewId"], "overwriteDrift": false}, c, "large-publish", 202)
	if awaitClusterOperation(t, p, c, publication["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("大修订发布失败")
	}
	snapshot := clusterCall(t, p, "GET", "/instances/"+id+"/versions/"+publication["versionId"].(string), nil, c, "", 200)
	if snapshot["sourceDraftRevision"] != "9007199254740993" {
		t.Fatal("来源版本修订丢失精度")
	}
	for _, invalid := range []any{float64(9007199254740992), "9223372036854775808", "1.5", 1.5, "1e3"} {
		body["expectedDraftRevision"] = invalid
		clusterCall(t, p, "PUT", "/instances/"+id+"/draft", body, c, "invalid", 400)
	}
}
