// 提前评审回归均从公开HTTP经过生成Kitex访问真实SQLite/受控TLS目标。
package platform_test

import (
	"context"
	"net/http"
	"sync/atomic"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/platform"
	"github.com/fjsxy/monitor-mgr/internal/teststeps"
	ginkgo "github.com/onsi/ginkgo/v2"
)

// 注册/候选/人工继续都先持久受理；命中只能找回当前任务，不再触发核验。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestReviewCredentialReplayCurrent(t ginkgo.GinkgoTInterface) {
	// 接下来注册/候选/人工继续都先持久受理；命中只能找回当前任务，不再触发核验。
	ginkgo.By("注册/候选/人工继续都先持久受理；命中只能找回当前任务，不再触发核验。")

	for _, action := range []string{"REGISTER", "CREDENTIAL", "REACTIVATE", "CONTINUE"} {
		for _, state := range []string{"SUCCEEDED", "FAILED", "WAITING_CONFIRMATION"} {
			teststeps.Step(action+"/"+state, func() {
				var gated atomic.Bool
				var reviews atomic.Int64
				entered := make(chan struct{}, 2)
				release := make(chan struct{})
				defer close(release)
				gate := func(r *http.Request) {
					reviews.Add(1)
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
				beforeReviews := reviews.Load()
				teststeps.Step("current-state", func() {
					replay := clusterCall(t, p, "POST", path, body, c, key, 202)
					if replay["operationId"] != oid || replay["state"] != current["state"] || replay["clusterId"] != current["clusterId"] {
						t.Fatalf("幂等命中返回受理快照而非当前operation: state=%v current=%v", replay["state"], current["state"])
					}
				})
				teststeps.Step("unauthenticated", func() { clusterCall(t, p, "POST", path, body, nil, key, 401) })
				if action != "CONTINUE" {
					teststeps.Step("changed-content", func() {
						changed := map[string]any{}
						for k, v := range body {
							changed[k] = v
						}
						changed["context"] = "changed"
						clusterCall(t, p, "POST", path, changed, c, key, 409)
					})
				}
				_, backup := reviewAccount(t, p, c, "replay-backup", "ADMIN")
				clusterCall(t, p, "PATCH", "/accounts/"+owner, map[string]any{"role": "USER"}, backup, "revoke-role", 200)
				c = request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
				teststeps.Step("revoked-role", func() { clusterCall(t, p, "POST", path, body, c, key, 403) })
				clusterCall(t, p, "PATCH", "/accounts/"+owner, map[string]any{"enabled": false}, backup, "disable-actor", 200)
				teststeps.Step("disabled", func() { clusterCall(t, p, "POST", path, body, c, key, 401) })
				teststeps.Step("no-second-verification", func() {
					if reviews.Load() != beforeReviews {
						t.Fatal("幂等命中再次核验候选")
					}
				})
			})
		}
	}
}

// reviewAccount通过真实账号流程得到可用会话，避免把mustChangePassword账号当授权正控。
func reviewAccount(t ginkgo.GinkgoTInterface, p *platform.Runtime, admin *http.Cookie, name, role string) (string, *http.Cookie) {
	t.Helper()
	a := clusterCall(t, p, "POST", "/accounts", map[string]any{"username": name, "password": "temporary-password", "role": role}, admin, "account-"+name, 201)
	c := request(t, p, "POST", "/sessions", `{"username":"`+name+`","password":"temporary-password"}`, nil, 201).Cookies()[0]
	request(t, p, "POST", "/session/password", `{"currentPassword":"temporary-password","newPassword":"personal-password"}`, c, 200)
	c = request(t, p, "POST", "/sessions", `{"username":"`+name+`","password":"personal-password"}`, nil, 201).Cookies()[0]
	return a["id"].(string), c
}
