package platform_test

import (
	"context"
	"encoding/json"
	"net/http"
	"reflect"
	"sort"
	"strings"
	"time"

	"github.com/cloudwego/kitex/client"
	"github.com/fjsxy/monitor-mgr/internal/teststeps"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"github.com/fjsxy/monitor-mgr/kitex_gen/control/controlservice"
	ginkgo "github.com/onsi/ginkgo/v2"
)

// SELECTOR012沿HTTP→真实Kitex→SQLite→worker验证CR裁剪；typed空集合与外层selector语义分别断言。
var _ = ginkgo.It("TestAgentSelectorExpressionProjection", func() {
	t := ginkgo.GinkgoT()
	// 接下来SELECTOR012沿HTTP→真实Kitex→SQLite→worker验证CR裁剪；typed空集合与外层selector语义分别断言。
	ginkgo.By("SELECTOR012沿HTTP→真实Kitex→SQLite→worker验证CR裁剪；typed空集合与外层selector语义分别断言。")

	for _, tc := range []struct {
		name, operator string
		values         []string
		matches        []string
	}{
		{"exists-nil", "Exists", nil, []string{"alpha", "gamma"}},
		{"exists-empty", "Exists", []string{}, []string{"alpha", "gamma"}},
		{"not-exists-nil", "DoesNotExist", nil, []string{"missing"}},
		{"not-exists-empty", "DoesNotExist", []string{}, []string{"missing"}},
		{"in", "In", []string{"alpha", "beta"}, []string{"alpha"}},
		{"not-in", "NotIn", []string{"alpha", "beta"}, []string{"gamma", "missing"}},
	} {
		teststeps.Step(tc.name, func() {
			f, target := agentTarget(t)
			writes := 0
			f.hook = func(w http.ResponseWriter, r *http.Request) bool {
				if strings.HasSuffix(r.URL.Path, "/servicemonitors") {
					items := []any{}
					for _, name := range []string{"alpha", "gamma", "missing"} {
						labels := map[string]string{}
						if name != "missing" {
							labels["enabled"] = name
						}
						items = append(items, map[string]any{"metadata": map[string]any{"name": name, "namespace": "monitoring", "uid": "uid-" + name, "labels": labels}, "spec": map[string]any{"selector": map[string]any{}, "endpoints": []any{map[string]any{"port": "http"}}}})
					}
					json.NewEncoder(w).Encode(map[string]any{"items": items})
					return true
				}
				if strings.HasSuffix(r.URL.Path, "/podmonitors") {
					json.NewEncoder(w).Encode(map[string]any{"items": []any{}})
					return true
				}
				if r.Method != "POST" || !strings.HasSuffix(r.URL.Path, "/prometheusagents") {
					return false
				}
				var body map[string]any
				if json.NewDecoder(r.Body).Decode(&body) != nil {
					t.Error("CR请求无法解析")
					w.WriteHeader(422)
					return true
				}
				spec := body["spec"].(map[string]any)
				// 固定v0.83.0结构化CRD的values为非nullable array；只裁剪null，合法[]保留。
				for _, value := range spec["serviceMonitorSelector"].(map[string]any)["matchExpressions"].([]any) {
					expression := value.(map[string]any)
					if expression["values"] == nil {
						delete(expression, "values")
					}
				}
				spec["portName"], spec["scrapeInterval"] = "web", "30s"
				meta := body["metadata"].(map[string]any)
				meta["uid"], meta["resourceVersion"], meta["generation"] = "projection-uid", "1", 1
				f.mu.Lock()
				f.objects["monitoring/"+meta["name"].(string)] = body
				writes++
				f.mu.Unlock()
				w.WriteHeader(201)
				json.NewEncoder(w).Encode(body)
				return true
			}
			p, cookie, owner := clusterPlatform(t)
			registration := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "projection", "ownerId": owner, "context": "explicit", "kubeconfig": target}, cookie, "register", 202)
			cluster := awaitClusterOperation(t, p, cookie, registration["operationId"].(string))["clusterId"].(string)
			cfg := agentConfig("projection", "monitoring")
			expression := map[string]any{"key": "enabled", "operator": tc.operator}
			if tc.values != nil {
				expression["values"] = tc.values
			}
			associations := cfg["associations"].(map[string]any)
			associations["serviceMonitor"].(map[string]any)["object"] = map[string]any{"mode": "Labels", "selector": map[string]any{"matchExpressions": []any{expression}}}
			associations["podMonitor"].(map[string]any)["object"] = map[string]any{"mode": "All"}
			instance := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": cfg}, cookie, "create", 201)
			id := instance["id"].(string)
			cli, err := controlservice.NewClient("selector-projection", client.WithHostPorts(p.RPCAddress()))
			if err != nil {
				t.Fatal(err)
			}
			token := cookie.Value
			draft, err := cli.GetDraft(context.Background(), &rpc.RequestContext{SessionToken: &token}, &rpc.InstanceID{Id: id})
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(draft.Agent.Associations.ServiceMonitor.Object.Selector.MatchExpressions[0].Values, tc.values) {
				t.Fatal("typed/RPC/DB缺失或显式集合语义丢失")
			}
			matches := clusterCall(t, p, "POST", "/instances/"+id+"/association-previews", map[string]any{"draftRevision": 1}, cookie, "associations", 200)
			var names []string
			for _, source := range matches["sources"].([]any) {
				s := source.(map[string]any)
				if s["source"] == "association:ServiceMonitor" {
					for _, match := range s["matches"].([]any) {
						names = append(names, match.(map[string]any)["name"].(string))
					}
				}
			}
			sort.Strings(names)
			if !reflect.DeepEqual(names, tc.matches) {
				t.Fatalf("标签选择语义错误：%v", names)
			}
			preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, cookie, "preview", 200)
			op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, cookie, "publish", 202)
			// 身份待确认是失败终态，必须直接断言，不能由旧cluster helper超时掩盖。
			var done map[string]any
			deadline := time.Now().Add(12 * time.Second)
			for time.Now().Before(deadline) {
				done = clusterCall(t, p, "GET", "/operations/"+op["operationId"].(string), nil, cookie, "", 200)
				state := done["state"]
				if state != "QUEUED" && state != "RUNNING" && state != "CHECKING" && state != "DISPATCHED" {
					break
				}
				time.Sleep(10 * time.Millisecond)
			}
			if done["state"] != "SUCCEEDED" {
				t.Fatalf("结构化CRD裁剪后发布未确认：%v", done["state"])
			}
			version := clusterCall(t, p, "GET", "/instances/"+id+"/versions/"+done["versionId"].(string), nil, cookie, "", 200)
			if strings.Contains(preview["rightYAML"].(string), "values: null") || strings.Contains(version["yaml"].(string), "values: null") {
				t.Fatal("CR投影或冻结版本仍含null values")
			}
			if tc.values != nil && len(tc.values) == 0 && !strings.Contains(version["yaml"].(string), "values: []") {
				t.Fatal("CR/YAML丢失显式空数组")
			}
			f.mu.Lock()
			defer f.mu.Unlock()
			actual := f.objects["monitoring/projection"]["spec"].(map[string]any)
			if _, exists := actual["probeSelector"]; exists {
				t.Fatal("None外层selector不再省略")
			}
			if all, ok := actual["podMonitorSelector"].(map[string]any); !ok || len(all) != 0 {
				t.Fatal("All外层selector不再是空对象")
			}
			got := actual["serviceMonitorSelector"].(map[string]any)["matchExpressions"].([]any)[0].(map[string]any)
			if tc.values == nil {
				if _, exists := got["values"]; exists {
					t.Fatal("nil values未省略")
				}
			} else {
				values, ok := got["values"].([]any)
				if !ok || len(values) != len(tc.values) {
					t.Fatal("CR显式values丢失")
				}
				for i := range values {
					if values[i] != tc.values[i] {
						t.Fatal("CR values内容或顺序改变")
					}
				}
			}
			if writes != 1 {
				t.Fatal("响应确认引发了额外创建或不安全重试")
			}
		})
	}
})
