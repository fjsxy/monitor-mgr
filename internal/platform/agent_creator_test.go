package platform_test

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"github.com/fjsxy/monitor-mgr/internal/platform"
	. "github.com/onsi/gomega"
	"net/http"
	"path/filepath"
	"strings"
	"sync/atomic"

	"github.com/cloudwego/kitex/client"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"github.com/fjsxy/monitor-mgr/kitex_gen/control/controlservice"
	ginkgo "github.com/onsi/ginkgo/v2"
)

// I30-U01/03：快捷配置落到原生selector，服务端持续按实例创建事实独立授权。
func checkAgentCreatorAssociation(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I30-U01/03：None默认不变，明确user标签保存和管理员编辑固定，HTTP/RPC不能绕过保护")
	_, target := agentTarget(t)
	p, admin, owner := clusterPlatform(t)
	op := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "creator-association", "ownerId": owner, "context": "explicit", "kubeconfig": target}, admin, "register-creator", 202)
	cluster := awaitClusterOperation(t, p, admin, op["operationId"].(string))["clusterId"].(string)
	user, userID := identityUser(t, p, admin, "collector-owner", "USER")
	clusterCall(t, p, "POST", "/clusters/"+cluster+"/transfer", map[string]any{"ownerId": userID}, admin, "transfer-collector", 200)
	adminID := readJSON(t, p, "/session", admin)["account"].(map[string]any)["id"].(string)
	for index, actor := range []*http.Cookie{user, admin} {
		name := []string{"user-collector", "admin-collector"}[index]
		cfg := agentConfig(name, "monitoring")
		i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": cfg}, actor, "create-"+name, 201)
		id := i["id"].(string)
		creator := []string{userID, adminID}[index]
		if i["creatorUserId"] != creator {
			t.Fatal("创建者不是实际actor")
		}
		ginkgo.By("I30-U01.none：" + name)
		initial := readJSON(t, p, "/instances/"+id+"/draft", actor)
		if strings.Contains(initial["yaml"].(string), "serviceMonitorSelector:") {
			t.Fatal("None默认被改写")
		}
		ginkgo.By("I30-U01." + []string{"user", "admin"}[index] + "：原生关联与冻结")
		rule := cfg["associations"].(map[string]any)["serviceMonitor"].(map[string]any)
		rule["object"] = map[string]any{"mode": "Labels", "selector": map[string]any{"matchLabels": map[string]string{"monitor-mgr.io/user-id": creator}, "matchExpressions": []any{map[string]any{"key": "enabled", "operator": "DoesNotExist", "values": []string{}}}}}
		clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "agent": cfg}, actor, "select-"+name, 200)
		ginkgo.By("I30-U01.edit：管理员只改CPU，目标与表达式不变")
		cfg["resources"].(map[string]any)["requestCPU"] = "210m"
		saved := clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 2, "agent": cfg}, admin, "admin-edit-"+name, 200)
		typed := agentRPCConfig(t, saved["agent"].(map[string]any))
		if typed.Associations.ServiceMonitor.Object.Selector.MatchLabels["monitor-mgr.io/user-id"] != creator || len(typed.Associations.ServiceMonitor.Object.Selector.MatchExpressions) != 1 {
			t.Fatal("保存丢失固定用户或自定义表达式")
		}
		pv := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 3}, admin, "preview-"+name, 200)
		if !strings.Contains(pv["rightYAML"].(string), creator) {
			t.Fatal("预览未采用明确user ID")
		}
		pub := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": pv["previewId"], "draftRevision": 3, "overwriteDrift": false}, admin, "publish-"+name, 202)
		if awaitClusterOperation(t, p, admin, pub["operationId"].(string))["state"] != "SUCCEEDED" {
			t.Fatal("原生用户关联发布失败")
		}
		v := readJSON(t, p, "/instances/"+id+"/versions/"+pub["versionId"].(string), admin)
		if agentRPCConfig(t, v["agent"].(map[string]any)).Associations.ServiceMonitor.Object.Selector.MatchLabels["monitor-mgr.io/user-id"] != creator {
			t.Fatal("冻结版本改写目标用户")
		}
		ginkgo.AddReportEntry("I30-U01."+[]string{"user", "admin"}[index], "公开HTTP/RPC/草稿/版本固定创建用户")
		if index == 1 {
			ginkgo.By("I30-U03.http：标签相同不能经HTTP修改管理员Agent")
			clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 3, "agent": cfg}, user, "denied-association", 403)
			ginkgo.By("I30-U03.rpc：直接Kitex不能绕过保护")
			cli, e := controlservice.NewClient("creator-association", client.WithHostPorts(p.RPCAddress()))
			if e != nil {
				t.Fatal(e)
			}
			token, key := user.Value, "direct-association"
			_, e = cli.SaveDraft(context.Background(), &rpc.RequestContext{SessionToken: &token, RequestId: key, IdempotencyKey: &key}, &rpc.SaveDraftRequest{Id: id, ExpectedDraftRevision: 3, Agent: agentRPCConfig(t, cfg)})
			var a *rpc.APIError
			if !errors.As(e, &a) || a.Code != "FORBIDDEN" {
				t.Fatal("直接RPC用采集标签绕过管理员实例保护", e)
			}
		}
	}
	ginkgo.By("I30-U01.transfer/role：归属与账号角色变化不重算原标签")
	clusterCall(t, p, "POST", "/clusters/"+cluster+"/transfer", map[string]any{"ownerId": adminID}, admin, "back", 200)
	clusterCall(t, p, "PATCH", "/accounts/"+userID, map[string]any{"role": "ADMIN"}, admin, "promote", 200)
	clusterCall(t, p, "PATCH", "/accounts/"+userID, map[string]any{"role": "USER"}, admin, "demote", 200)
	rows := clusterCall(t, p, "GET", "/clusters/"+cluster+"/instances", nil, admin, "", 200)["instances"].([]any)
	for _, raw := range rows {
		v := raw.(map[string]any)
		if v["name"] == "user-collector" {
			Expect(v["creatorUserId"]).To(Equal(userID))
			Expect(v["creatorRole"]).To(Equal("USER"))
			d := readJSON(t, p, "/instances/"+v["id"].(string)+"/draft", admin)
			Expect(agentRPCConfig(t, d["agent"].(map[string]any)).Associations.ServiceMonitor.Object.Selector.MatchLabels["monitor-mgr.io/user-id"]).To(Equal(userID))
		}
	}
	ginkgo.AddReportEntry("I30-U01.transfer", "归属变化后目标保持")
	ginkgo.AddReportEntry("I30-U01.role", "账号角色升降后目标保持")
	for _, id := range []string{"I30-U01.none", "I30-U01.edit", "I30-U03.http", "I30-U03.rpc"} {
		ginkgo.AddReportEntry(id, "固定创建用户与独立实例保护输入分别执行")
	}
}

// I30-U02：公开预览必须区分无匹配、读取失败与Operator权限，namespace独立于对象标签。
func checkAgentCreatorMatching(t ginkgo.GinkgoTInterface) {
	ginkgo.By("I30-U02：自定义表达式、跨namespace和动态新增保持，零匹配/读失败/权限独立")
	f, target := agentTarget(t)
	p, admin, owner := clusterPlatform(t)
	op := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "creator-matching", "ownerId": owner, "context": "explicit", "kubeconfig": target}, admin, "register", 202)
	cluster := awaitClusterOperation(t, p, admin, op["operationId"].(string))["clusterId"].(string)
	creator := readJSON(t, p, "/session", admin)["account"].(map[string]any)["id"].(string)
	var mode atomic.Int32
	var permissionVerb atomic.Int32
	verbs := []string{"get", "list", "watch"}
	var added atomic.Bool
	f.mu.Lock()
	f.hook = func(w http.ResponseWriter, r *http.Request) bool {
		if r.URL.Path == "/api/v1/namespaces" {
			json.NewEncoder(w).Encode(map[string]any{"items": []any{map[string]any{"metadata": map[string]any{"name": "monitoring"}}, map[string]any{"metadata": map[string]any{"name": "other", "labels": map[string]string{"scope": "collect"}}}}})
			return true
		}
		if r.URL.Path == "/apis/authorization.k8s.io/v1/subjectaccessreviews" && (mode.Load() == 3 || mode.Load() == 6) {
			var request map[string]any
			if json.NewDecoder(r.Body).Decode(&request) != nil {
				w.WriteHeader(400)
				return true
			}
			spec := request["spec"].(map[string]any)
			attrs := spec["resourceAttributes"].(map[string]any)
			resource, user := "servicemonitors", "system:serviceaccount:operators:prometheus-operator"
			if mode.Load() == 6 {
				resource = "pods"
				user = "system:serviceaccount:monitoring:prometheus-agent"
			}
			deny := spec["user"] == user && attrs["resource"] == resource && attrs["verb"] == verbs[permissionVerb.Load()]
			json.NewEncoder(w).Encode(map[string]any{"status": map[string]bool{"allowed": !deny}})
			return true
		}
		if mode.Load() == 5 && strings.HasSuffix(r.URL.Path, "/servicemonitors") {
			w.WriteHeader(403)
			return true
		}
		if strings.HasSuffix(r.URL.Path, "/servicemonitors") {
			if mode.Load() == 2 {
				w.WriteHeader(500)
				return true
			}
			ns := strings.Split(r.URL.Path, "/")[5]
			items := []any{}
			if mode.Load() != 1 && ns == "other" {
				for _, name := range []string{"one", "two", "excluded"} {
					if name == "two" && !added.Load() {
						continue
					}
					labels := map[string]string{"monitor-mgr.io/user-id": creator}
					if name == "excluded" {
						labels["disabled"] = "yes"
					}
					items = append(items, map[string]any{"metadata": map[string]any{"namespace": ns, "name": name, "uid": name + "-uid", "labels": labels}})
				}
			}
			json.NewEncoder(w).Encode(map[string]any{"items": items})
			return true
		}
		if mode.Load() == 4 && r.URL.Path == "/api/v1/namespaces/operators/pods" {
			json.NewEncoder(w).Encode(map[string]any{"items": []any{operatorPodDoc("operator-pod", []string{"--namespaces=monitoring"})}})
			return true
		}

		if mode.Load() == 4 && r.URL.Path == "/apis/apps/v1/deployments" {
			json.NewEncoder(w).Encode(operatorDoc([]string{"--namespaces=monitoring"}))
			return true
		}
		if mode.Load() == 5 && strings.HasSuffix(r.URL.Path, "/servicemonitors") {
			w.WriteHeader(403)
			return true
		}
		return false
	}
	f.mu.Unlock()
	cfg := agentConfig("creator-matching", "monitoring")
	rule := cfg["associations"].(map[string]any)["serviceMonitor"].(map[string]any)
	rule["object"] = map[string]any{"mode": "Labels", "selector": map[string]any{"matchLabels": map[string]string{"monitor-mgr.io/user-id": creator}, "matchExpressions": []any{map[string]any{"key": "disabled", "operator": "DoesNotExist"}}}}
	i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": cfg}, admin, "create", 201)
	id := i["id"].(string)
	preview := func(rev int) map[string]any {
		value := clusterCall(t, p, "POST", "/instances/"+id+"/association-previews", map[string]any{"draftRevision": rev}, admin, "preview", 200)
		for _, r := range value["sources"].([]any) {
			v := r.(map[string]any)
			if v["source"] == "association:ServiceMonitor" {
				return v
			}
		}
		t.Fatal("缺匹配来源")
		return nil
	}
	count := func(s map[string]any) int { matches, _ := s["matches"].([]any); return len(matches) }
	text := func(s map[string]any, key string) string { v, _ := s[key].(string); return v }
	ginkgo.By("I30-U02.own：只在其他namespace有目标时Own零匹配成功")
	own := preview(1)
	if count(own) != 0 || text(own, "lastSuccessAt") == "" || text(own, "error") != "" {
		t.Fatal("Own范围零匹配不独立", own)
	}
	rule["namespace"] = map[string]any{"mode": "Labels", "selector": map[string]any{"matchLabels": map[string]string{"scope": "collect"}}}
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "agent": cfg}, admin, "cross", 200)
	ginkgo.By("I30-U02.namespace-labels：显式范围跨namespace")
	if count(preview(2)) != 1 {
		t.Fatal("跨namespace表达式未筛选")
	}
	ginkgo.By("I30-U02.dynamic：同修订新增SM动态出现")
	added.Store(true)
	if count(preview(2)) != 2 {
		t.Fatal("新增同用户SM未动态发现")
	}

	ginkgo.By("I30-U02.namespace-all：namespace All独立映射空对象")
	rule["namespace"] = map[string]any{"mode": "All"}
	revision := 3
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": revision - 1, "agent": cfg}, admin, "all-ns", 200)
	Expect(count(preview(revision))).To(Equal(2))
	Expect(readJSON(t, p, "/instances/"+id+"/draft", admin)["yaml"]).To(ContainSubstring("serviceMonitorNamespaceSelector: {}"))
	ginkgo.AddReportEntry("I30-U02.namespace-all", "All范围与对象规则独立")
	for _, item := range []struct {
		op     string
		values []string
		count  int
		caseID string
	}{{"In", []string{"yes"}, 1, "in"}, {"NotIn", []string{"yes"}, 2, "notin"}, {"Exists", []string{}, 1, "exists"}, {"DoesNotExist", []string{}, 2, "notexists"}} {
		ginkgo.By("I30-U02.expression-" + item.caseID)
		rule["object"] = map[string]any{"mode": "Labels", "selector": map[string]any{"matchLabels": map[string]string{"monitor-mgr.io/user-id": creator}, "matchExpressions": []any{map[string]any{"key": "disabled", "operator": item.op, "values": item.values}}}}
		clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": revision, "agent": cfg}, admin, "expression-"+item.caseID, 200)
		revision++
		Expect(count(preview(revision))).To(Equal(item.count))
		ginkgo.AddReportEntry("I30-U02.expression-"+item.caseID, "HTTP→RPC→SQLite规则保留且匹配集合正确")
	}
	ginkgo.By("I30-U02.object-all：仅明确用户操作才选择全部对象")
	rule["object"] = map[string]any{"mode": "All"}
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": revision, "agent": cfg}, admin, "all-object", 200)
	revision++
	Expect(count(preview(revision))).To(Equal(3))
	Expect(readJSON(t, p, "/instances/"+id+"/draft", admin)["yaml"]).To(ContainSubstring("serviceMonitorSelector: {}"))
	ginkgo.AddReportEntry("I30-U02.object-all", "原生All明确保留")
	rule["object"] = map[string]any{"mode": "Labels", "selector": map[string]any{"matchLabels": map[string]string{"monitor-mgr.io/user-id": creator}, "matchExpressions": []any{map[string]any{"key": "disabled", "operator": "DoesNotExist"}}}}
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": revision, "agent": cfg}, admin, "reset", 200)
	revision++
	var readFailure string
	for _, m := range []int32{1, 2, 3} {
		mode.Store(m)
		ginkgo.By("I30-U02." + map[int32]string{1: "zero", 2: "read-failure", 3: "operator-permission"}[m])
		s := preview(revision)
		if m == 1 {
			if count(s) != 0 || text(s, "error") != "" || text(s, "lastSuccessAt") == "" {
				t.Fatal("空成功被误判失败", s)
			}
		} else if text(s, "error") == "" || text(s, "lastSuccessAt") != "" {
			t.Fatal("读失败/权限不足被误判空成功", s)
		}
		if m == 2 {
			Expect(text(s, "error")).To(HavePrefix("TARGET_ERROR"))
			readFailure = text(s, "error")
		}
		ginkgo.AddReportEntry("I30-U02."+map[int32]string{1: "zero", 2: "read-failure", 3: "operator-permission"}[m], "公开预览错误/成功来源已独立验证")
		if m == 3 && text(s, "error") == readFailure {
			t.Fatal("读取失败与权限不足原因被合并", s)
		}
	}
	for _, m := range []int32{4, 5} {
		mode.Store(m)
		caseID := map[int32]string{4: "operator-scope", 5: "forbidden"}[m]
		ginkgo.By("I30-U02." + caseID)
		v := preview(revision)
		Expect(text(v, "lastSuccessAt")).To(BeEmpty())
		Expect(text(v, "error")).NotTo(BeEmpty())
		if m == 4 {
			Expect(text(v, "error")).To(ContainSubstring("namespace"))
		}
		if m == 5 {
			Expect(text(v, "error")).To(HavePrefix("TARGET_FORBIDDEN"))
		}
		ginkgo.AddReportEntry("I30-U02."+caseID, "可见范围或真实读取权限受限，不显示零匹配成功")
	}

	for _, role := range []string{"operator", "agent"} {
		for n, verb := range verbs {
			permissionVerb.Store(int32(n))
			mode.Store(3)
			if role == "agent" {
				mode.Store(6)
			}
			caseID := "I30-U02.operator-permission." + role + "-" + verb
			ginkgo.By(caseID)
			if role == "operator" {
				value := preview(revision)
				Expect(text(value, "error")).To(HavePrefix("TARGET_FORBIDDEN"))
				Expect(text(value, "error")).To(ContainSubstring("servicemonitors/" + verb))
				Expect(text(value, "lastSuccessAt")).To(BeEmpty())
			} else {
				value := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": revision}, admin, "agent-permission-"+verb, 403)
				Expect(value["code"]).To(Equal("TARGET_FORBIDDEN"))
				Expect(value["message"]).To(ContainSubstring("pods/" + verb))
			}
			ginkgo.AddReportEntry(caseID, "实际公开预览按独立SA/resource/verb拒绝；其他权限允许")
		}
	}

	for _, caseID := range []string{"I30-U02.own", "I30-U02.namespace-labels", "I30-U02.dynamic"} {
		ginkgo.AddReportEntry(caseID, "对应公开行为输入与结果分别执行；保留By步骤")
	}

}

// 历史夹具只裁除旧版不存在的字段；用户行为仍由公开HTTP/真实RPC观察。
func checkAgentUnknownCreatorCustom(t ginkgo.GinkgoTInterface) {
	for _, known := range []bool{true, false} {
		idText := "I30-U03.unknown-user"
		if known {
			idText = "I30-U03.known-user-unknown-role"
		}
		ginkgo.By(idText)
		_, target := agentTarget(t)
		cfg := platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"}
		p, err := platform.Start(context.Background(), cfg)
		Expect(err).NotTo(HaveOccurred())
		request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
		admin := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
		creator := readJSON(t, p, "/session", admin)["account"].(map[string]any)["id"].(string)
		user, userID := identityUser(t, p, admin, "unknown-agent-owner", "USER")
		reg := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "unknown", "ownerId": userID, "context": "explicit", "kubeconfig": target}, admin, "reg", 202)
		cluster := awaitClusterOperation(t, p, admin, reg["operationId"].(string))["clusterId"].(string)
		i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": agentConfig("unknown-agent", "monitoring")}, admin, "create", 201)
		id := i["id"].(string)
		Expect(p.Close(context.Background())).To(Succeed())
		db, err := sql.Open("sqlite", filepath.Join(cfg.DataDir, "platform.db"))
		Expect(err).NotTo(HaveOccurred())
		_, err = db.Exec(dropResourceProgressSchema + dropInstanceCreatorSchema + `DELETE FROM schema_migrations WHERE version>10;`)
		Expect(err).NotTo(HaveOccurred())
		if !known {
			_, err = db.Exec(`DELETE FROM command_results WHERE action='CREATE_INSTANCE'`)
			Expect(err).NotTo(HaveOccurred())
		}
		Expect(db.Close()).To(Succeed())
		p, err = platform.Start(context.Background(), cfg)
		Expect(err).NotTo(HaveOccurred())
		got := readJSON(t, p, "/instances/"+id, user)
		Expect(got["creatorRole"]).To(BeEmpty())
		Expect(got["canManage"]).To(BeFalse())
		Expect(got["creatorIdentityKnown"]).To(BeFalse())
		if known {
			Expect(got["creatorUserId"]).To(Equal(creator))
		} else {
			Expect(got["creatorUserId"]).To(BeEmpty())
		}
		clusterCall(t, p, "POST", "/instances/"+id+"/management-confirmations", map[string]any{"allowClusterManagers": true}, admin, "confirm", 200)
		value := agentConfig("unknown-agent", "monitoring")
		value["associations"].(map[string]any)["serviceMonitor"].(map[string]any)["object"] = map[string]any{"mode": "Labels", "selector": map[string]any{"matchLabels": map[string]string{"team": "custom"}}}
		saved := clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "agent": value}, user, "save", 200)
		Expect(agentRPCConfig(t, saved["agent"].(map[string]any)).Associations.ServiceMonitor.Object.Selector.MatchLabels).To(Equal(map[string]string{"team": "custom"}))
		cli, err := controlservice.NewClient("unknown-custom", client.WithHostPorts(p.RPCAddress()))
		Expect(err).NotTo(HaveOccurred())
		token := user.Value
		read, err := cli.GetDraft(context.Background(), &rpc.RequestContext{SessionToken: &token}, &rpc.InstanceID{Id: id})
		Expect(err).NotTo(HaveOccurred())
		Expect(read.Agent.Associations.ServiceMonitor.Object.Selector.MatchLabels).To(Equal(map[string]string{"team": "custom"}))
		after := readJSON(t, p, "/instances/"+id, user)
		Expect(after["creatorRole"]).To(BeEmpty())
		Expect(after["creatorUserId"]).To(Equal(got["creatorUserId"]))
		ginkgo.AddReportEntry(id, "权限确认不补造身份，custom selector按独立授权保存/真实RPC回读")
		Expect(p.Close(context.Background())).To(Succeed())
	}
}
