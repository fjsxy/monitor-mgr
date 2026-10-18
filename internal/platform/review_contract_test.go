// 提前评审回归均从公开HTTP经过生成Kitex访问真实SQLite/受控TLS目标。
package platform_test

import (
	"context"
	"encoding/json"
	"errors"
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
				t.Run("current-state", func(t *testing.T) {
					replay := clusterCall(t, p, "POST", path, body, c, key, 202)
					if replay["operationId"] != oid || replay["state"] != current["state"] || replay["clusterId"] != current["clusterId"] {
						t.Fatalf("幂等命中返回受理快照而非当前operation: state=%v current=%v", replay["state"], current["state"])
					}
				})
				t.Run("unauthenticated", func(t *testing.T) { clusterCall(t, p, "POST", path, body, nil, key, 401) })
				if action != "CONTINUE" {
					t.Run("changed-content", func(t *testing.T) {
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
				t.Run("revoked-role", func(t *testing.T) { clusterCall(t, p, "POST", path, body, c, key, 403) })
				clusterCall(t, p, "PATCH", "/accounts/"+owner, map[string]any{"enabled": false}, backup, "disable-actor", 200)
				t.Run("disabled", func(t *testing.T) { clusterCall(t, p, "POST", path, body, c, key, 401) })
				t.Run("no-second-verification", func(t *testing.T) {
					if reviews.Load() != beforeReviews {
						t.Fatal("幂等命中再次核验候选")
					}
				})
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

func TestReviewDeleteRoute(t *testing.T) {
	for _, variant := range []string{"draft", "confirmed", "missing-preview", "stale-impact", "replaced-uid", "other-owner", "idempotent"} {
		t.Run(variant, func(t *testing.T) {
			p, c, id, f := lifecycleFixture(t, nil)
			if variant == "draft" {
				clusterCall(t, p, "DELETE", "/instances/"+id, nil, c, "delete-draft", 200)
				return
			}
			publication := lifecyclePublish(t, p, c, id)
			if awaitClusterOperation(t, p, c, publication["operationId"].(string))["state"] != "SUCCEEDED" {
				t.Fatal("发布前置失败")
			}
			body := deletionConfirmation(t, p, c, id)
			want := 202
			switch variant {
			case "missing-preview":
				delete(body, "previewId")
				want = 422
			case "stale-impact":
				f.mu.Lock()
				f.cr["spec"].(map[string]any)["targetNamespace"] = "changed"
				f.mu.Unlock()
				want = 409
			case "replaced-uid":
				f.mu.Lock()
				f.cr["metadata"].(map[string]any)["uid"] = "foreign-new-uid"
				f.mu.Unlock()
				want = 409
			case "other-owner":
				_, c = reviewAccount(t, p, c, "delete-other", "USER")
				want = 403
			}
			op := clusterCall(t, p, "DELETE", "/instances/"+id, body, c, "canonical-delete", want)
			if want != 202 {
				f.mu.Lock()
				defer f.mu.Unlock()
				if f.deleteCount != 0 {
					t.Fatal("拒绝入口仍派发删除")
				}
				return
			}
			done := awaitClusterOperation(t, p, c, op["operationId"].(string))
			if done["state"] != "SUCCEEDED" {
				t.Fatal("规范入口删除未完成")
			}
			if variant == "idempotent" {
				replay := clusterCall(t, p, "DELETE", "/instances/"+id, body, c, "canonical-delete", 202)
				if replay["operationId"] != op["operationId"] || replay["state"] != "SUCCEEDED" {
					t.Fatal("删除命中不是当前原任务")
				}
			}
			f.mu.Lock()
			defer f.mu.Unlock()
			if f.deleteCount != 1 || f.lastDelete["preconditions"].(map[string]any)["uid"] != "created-uid" || f.lastDelete["propagationPolicy"] != "Foreground" {
				t.Fatal("规范入口未保留单次原UID条件删除")
			}
		})
	}
}

func TestReviewContinueRoute(t *testing.T) {
	for _, action := range []string{"PUBLISH", "DELETE"} {
		variants := []string{"unsent", "wrong-revision", "other-owner", "possibly-sent"}
		if action == "DELETE" {
			variants = append(variants, "stale-preview")
		}
		for _, variant := range variants {
			t.Run(action+"/"+variant, func(t *testing.T) {
				f, target := ksmTarget(t)
				var hold atomic.Bool
				entered := make(chan struct{}, 1)
				stage := "beforeDispatch"
				if variant == "possibly-sent" {
					stage = "afterSendMarker"
				}
				cfg := platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", OperationBoundary: func(s, id string) error {
					if hold.Load() && s == stage {
						select {
						case entered <- struct{}{}:
						default:
						}
						return errors.New("明确持久边界停止")
					}
					return nil
				}}
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
				reg := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "review-continue", "ownerId": owner, "context": "explicit", "kubeconfig": target}, c, "register", 202)
				cluster := awaitClusterOperation(t, p, c, reg["operationId"].(string))["clusterId"].(string)
				instance := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("lifecycle")}, c, "create", 201)
				id := instance["id"].(string)
				if action == "DELETE" {
					initial := lifecyclePublish(t, p, c, id)
					awaitLifecycleState(t, p, c, initial["operationId"].(string), "SUCCEEDED")
				}
				hold.Store(true)
				var accepted map[string]any
				if action == "PUBLISH" {
					accepted = lifecyclePublish(t, p, c, id)
				} else {
					accepted = clusterCall(t, p, "POST", "/instances/"+id+"/deletions", deletionConfirmation(t, p, c, id), c, "delete", 202)
				}
				oid := accepted["operationId"].(string)
				select {
				case <-entered:
				case <-time.After(2 * time.Second):
					t.Fatal("未到持久派发边界")
				}
				p.Close(context.Background())
				hold.Store(false)
				start()
				op := clusterCall(t, p, "GET", "/operations/"+oid, nil, c, "", 200)
				body := lifecycleRevision(t, p, c, oid)
				want := 202
				if variant == "possibly-sent" {
					want = 409
				} else if op["state"] != "WAITING_CONFIRMATION" {
					t.Fatal("重启未发任务未等待确认", op["state"])
				}
				if action == "DELETE" && variant != "possibly-sent" {
					dp := clusterCall(t, p, "POST", "/instances/"+id+"/deletion-previews", map[string]any{"operationId": oid}, c, "", 200)
					body["deletePreviewId"] = dp["previewId"]
				}
				switch variant {
				case "wrong-revision":
					body["expectedOperationRevision"] = body["expectedOperationRevision"].(float64) + 100
					want = 409
				case "other-owner":
					_, c = reviewAccount(t, p, c, "continue-other", "USER")
					want = 403
				case "stale-preview":
					f.mu.Lock()
					f.cr["spec"].(map[string]any)["targetNamespace"] = "changed"
					f.mu.Unlock()
					want = 409
				}
				if action == "PUBLISH" && variant == "unsent" {
					newDraft := ksmConfig("lifecycle")
					newDraft["resources"].(map[string]any)["requestCPU"] = "999m"
					clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": newDraft}, c, "new-draft", 200)
				}
				next := clusterCall(t, p, "POST", "/operations/"+oid+"/continue", body, c, "canonical-continue", want)
				if want != 202 {
					f.mu.Lock()
					defer f.mu.Unlock()
					if f.deleteRequests != 0 || (action == "PUBLISH" && f.writes != 0) {
						t.Fatal("拒绝continue仍重放了目标写")
					}
					return
				}
				if next["operationId"] != oid || next["versionId"] != op["versionId"] || next["attemptId"] == op["attemptId"] {
					t.Fatal("continue未保留原operation/版本及新增attempt")
				}
				done := awaitLifecycleState(t, p, c, oid, "SUCCEEDED")
				replay := clusterCall(t, p, "POST", "/operations/"+oid+"/continue", body, c, "canonical-continue", 202)
				if replay["state"] != done["state"] || replay["attemptId"] != done["attemptId"] {
					t.Fatal("continue同键未回读原任务当前尝试")
				}
				attempts := clusterCall(t, p, "GET", "/operations/"+oid+"/attempts", nil, c, "", 200)["attempts"].([]any)
				if len(attempts) != 2 {
					t.Fatal("原尝试未保留")
				}
			})
		}
	}
}
