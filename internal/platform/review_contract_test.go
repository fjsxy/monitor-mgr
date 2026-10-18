// 提前评审回归均从公开HTTP经过生成Kitex访问真实SQLite/受控TLS目标。
package platform_test

import (
	"context"
	"encoding/json"
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/platform"
)

// 注册/候选/人工继续都先持久受理；命中只能找回当前任务，不再触发核验。
func TestReviewCredentialReplayCurrent(t *testing.T) {
	for _, action := range []string{"REGISTER", "CREDENTIAL", "REACTIVATE", "CONTINUE"} {
		for _, state := range []string{"SUCCEEDED", "FAILED", "WAITING_CONFIRMATION"} {
			t.Run(action+"/"+state, func(t *testing.T) {
				var gated atomic.Bool
				entered := make(chan struct{}, 2)
				release := make(chan struct{})
				defer close(release)
				gate := func(r *http.Request) {
					if gated.Load() {
						select {
						case entered <- struct{}{}:
						default:
						}
						select {
						case <-release:
						case <-r.Context().Done():
						}
					}
				}
				_, good, _ := clusterAPI(t, 200, false, gate)
				_, bad, _ := clusterAPI(t, 403, false, gate)
				cfg := platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"}
				var p *platform.Runtime
				start := func() {
					var e error
					p, e = platform.Start(context.Background(), cfg)
					if e != nil {
						t.Fatal(e)
					}
				}
				start()
				t.Cleanup(func() { p.Close(context.Background()) })
				request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
				c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
				owner := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"].(string)
				cluster := ""
				if action != "REGISTER" {
					initial := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "initial", "ownerId": owner, "context": "explicit", "kubeconfig": good}, c, "initial", 202)
					cluster = awaitClusterOperation(t, p, c, initial["operationId"].(string))["clusterId"].(string)
				}
				if action == "REACTIVATE" {
					clusterCall(t, p, "POST", "/clusters/"+cluster+"/unregister", map[string]any{}, c, "archive", 200)
				}
				material := good
				if state == "FAILED" {
					material = bad
				}
				path := "/clusters"
				body := map[string]any{"context": "explicit", "kubeconfig": material}
				if action == "REGISTER" {
					body["name"] = "review"
					body["ownerId"] = owner
				} else if action == "REACTIVATE" {
					path = "/clusters/" + cluster + "/reactivate"
					body["ownerId"] = owner
				} else {
					path = "/clusters/" + cluster + "/credentials"
				}
				if state == "WAITING_CONFIRMATION" || action == "CONTINUE" {
					gated.Store(true)
				}
				accepted := clusterCall(t, p, "POST", path, body, c, "review-command", 202)
				oid := accepted["operationId"].(string)
				if gated.Load() {
					select {
					case <-entered:
					case <-time.After(2 * time.Second):
						t.Fatal("候选身份核验未到屏障")
					}
					stop, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
					p.Close(stop)
					cancel()
					start()
				}
				if action == "CONTINUE" {
					if state != "WAITING_CONFIRMATION" {
						gated.Store(false)
					}
					path = "/operations/" + oid + "/continue"
					body = map[string]any{}
					clusterCall(t, p, "POST", path, body, c, "review-continue", 202)
					if state == "WAITING_CONFIRMATION" {
						select {
						case <-entered:
						case <-time.After(2 * time.Second):
							t.Fatal("继续核验未到屏障")
						}
						p.Close(context.Background())
						start()
					}
				}
				current := awaitClusterOperation(t, p, c, oid)
				if current["state"] != state {
					t.Fatalf("前置状态错误: %v want %s", current["state"], state)
				}
				key := "review-command"
				if action == "CONTINUE" {
					key = "review-continue"
				}
				replay := clusterCall(t, p, "POST", path, body, c, key, 202)
				if replay["operationId"] != oid || replay["state"] != current["state"] || replay["clusterId"] != current["clusterId"] {
					t.Fatalf("幂等命中返回受理快照而非当前operation: state=%v current=%v", replay["state"], current["state"])
				}
				// 当前未认证请求即使具有同一命令键，也不能获取缓存结果。
				clusterCall(t, p, "POST", path, body, nil, key, 401)
			})
		}
	}
}

// reviewAccount通过真实账号流程得到可用会话，避免把mustChangePassword账号当授权正控。
func reviewAccount(t *testing.T, p *platform.Runtime, admin *http.Cookie, name, role string) (string, *http.Cookie) {
	t.Helper()
	a := clusterCall(t, p, "POST", "/accounts", map[string]any{"username": name, "password": "temporary-password", "role": role}, admin, "account-"+name, 201)
	c := request(t, p, "POST", "/sessions", `{"username":"`+name+`","password":"temporary-password"}`, nil, 201).Cookies()[0]
	request(t, p, "POST", "/session/password", `{"currentPassword":"temporary-password","newPassword":"personal-password"}`, c, 200)
	c = request(t, p, "POST", "/sessions", `{"username":"`+name+`","password":"personal-password"}`, nil, 201).Cookies()[0]
	return a["id"].(string), c
}

func TestReviewCapabilitiesRoute(t *testing.T) {
	p, c, cluster := ksmCluster(t)
	current := clusterCall(t, p, "GET", "/clusters/"+cluster, nil, c, "", 200)
	path := "/clusters/" + cluster + "/capabilities"
	check := func(t *testing.T, c *http.Cookie) {
		req, _ := http.NewRequest("GET", p.URL()+"/api/v1"+path, nil)
		req.AddCookie(c)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != 200 {
			t.Fatalf("能力入口状态 %d", resp.StatusCode)
		}
		var caps []any
		if e := json.NewDecoder(resp.Body).Decode(&caps); e != nil {
			t.Fatal(e)
		}
		a, _ := json.Marshal(current["capabilities"])
		b, _ := json.Marshal(caps)
		if string(a) != string(b) {
			t.Fatal("规范入口未返回同一安全能力投影")
		}
	}
	t.Run("admin", func(t *testing.T) { check(t, c) })
	aid, alice := reviewAccount(t, p, c, "cap-owner", "USER")
	t.Run("other-owner", func(t *testing.T) { clusterCall(t, p, "GET", path, nil, alice, "", 403) })
	t.Run("unauthenticated", func(t *testing.T) { clusterCall(t, p, "GET", path, nil, nil, "", 401) })
	t.Run("missing", func(t *testing.T) { clusterCall(t, p, "GET", "/clusters/missing/capabilities", nil, c, "", 404) })
	clusterCall(t, p, "POST", "/clusters/"+cluster+"/transfer", map[string]any{"ownerId": aid}, c, "cap-transfer", 200)
	t.Run("owner", func(t *testing.T) { check(t, alice) })
	clusterCall(t, p, "PATCH", "/accounts/"+aid, map[string]any{"enabled": false}, c, "disable-owner", 200)
	t.Run("disabled", func(t *testing.T) { clusterCall(t, p, "GET", path, nil, alice, "", 401) })
}
