package platform_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
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
		initial := readJSON(t, p, "/instances/"+id+"/draft", actor)
		if strings.Contains(initial["yaml"].(string), "serviceMonitorSelector:") {
			t.Fatal("None默认被改写")
		}
		rule := cfg["associations"].(map[string]any)["serviceMonitor"].(map[string]any)
		rule["object"] = map[string]any{"mode": "Labels", "selector": map[string]any{"matchLabels": map[string]string{"monitor-mgr.io/user-id": creator}, "matchExpressions": []any{map[string]any{"key": "enabled", "operator": "DoesNotExist", "values": []string{}}}}}
		clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "agent": cfg}, actor, "select-"+name, 200)
		// 管理员只改普通字段，不能将已保存的原生user标签重新绑定到当前登录用户。
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
		if index == 1 {
			clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 3, "agent": cfg}, user, "denied-association", 403)
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
	var added atomic.Bool
	f.mu.Lock()
	f.hook = func(w http.ResponseWriter, r *http.Request) bool {
		if r.URL.Path == "/api/v1/namespaces" {
			json.NewEncoder(w).Encode(map[string]any{"items": []any{map[string]any{"metadata": map[string]any{"name": "monitoring"}}, map[string]any{"metadata": map[string]any{"name": "other", "labels": map[string]string{"scope": "collect"}}}}})
			return true
		}
		if r.URL.Path == "/apis/authorization.k8s.io/v1/subjectaccessreviews" && mode.Load() == 3 {
			json.NewEncoder(w).Encode(map[string]any{"status": map[string]bool{"allowed": false}})
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
	own := preview(1)
	if count(own) != 0 || text(own, "lastSuccessAt") == "" || text(own, "error") != "" {
		t.Fatal("Own范围零匹配不独立", own)
	}
	rule["namespace"] = map[string]any{"mode": "Labels", "selector": map[string]any{"matchLabels": map[string]string{"scope": "collect"}}}
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "agent": cfg}, admin, "cross", 200)
	if count(preview(2)) != 1 {
		t.Fatal("跨namespace表达式未筛选")
	}
	added.Store(true)
	if count(preview(2)) != 2 {
		t.Fatal("新增同用户SM未动态发现")
	}
	var readFailure string
	for _, m := range []int32{1, 2, 3} {
		mode.Store(m)
		s := preview(2)
		if m == 1 {
			if count(s) != 0 || text(s, "error") != "" || text(s, "lastSuccessAt") == "" {
				t.Fatal("空成功被误判失败", s)
			}
		} else if text(s, "error") == "" || text(s, "lastSuccessAt") != "" {
			t.Fatal("读失败/权限不足被误判空成功", s)
		}
		if m == 2 {
			readFailure = text(s, "error")
		}
		if m == 3 && text(s, "error") == readFailure {
			t.Fatal("读取失败与权限不足原因被合并", s)
		}
	}
}
