package platform_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"github.com/cloudwego/kitex/client"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"github.com/fjsxy/monitor-mgr/kitex_gen/control/controlservice"
	"io"
	"log/slog"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/platform"
)

func TestI01InitializeOnlyEmptyDatabase(t *testing.T) {
	p, err := platform.Start(context.Background(), platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"})
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close(context.Background())
	for i, want := range []int{201, 409} {
		req, _ := http.NewRequest("POST", p.URL()+"/api/v1/initialization", strings.NewReader(`{"username":"admin","password":"first-password-123"}`))
		req.Header.Set("Origin", p.URL())
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != want {
			t.Fatalf("attempt %d got %d want %d", i, resp.StatusCode, want)
		}
	}
}

func TestI01TemporaryPasswordMustChangeAndRevokesSession(t *testing.T) {
	p, err := platform.Start(context.Background(), platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"})
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close(context.Background())
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	admin := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	request(t, p, "POST", "/accounts", `{"username":"alice","password":"temporary-password","role":"USER"}`, admin, 201)
	alice := request(t, p, "POST", "/sessions", `{"username":"alice","password":"temporary-password"}`, nil, 201).Cookies()[0]
	request(t, p, "GET", "/accounts", "", alice, 403)
	request(t, p, "POST", "/session/password", `{"currentPassword":"temporary-password","newPassword":"personal-password"}`, alice, 200)
	request(t, p, "GET", "/session", "", alice, 401)
	alice = request(t, p, "POST", "/sessions", `{"username":"alice","password":"personal-password"}`, nil, 201).Cookies()[0]
	request(t, p, "GET", "/session", "", alice, 200)
	request(t, p, "GET", "/accounts", "", alice, 403)
}
func request(t *testing.T, p *platform.Runtime, method, path, body string, cookie *http.Cookie, want int) *http.Response {
	t.Helper()
	req, _ := http.NewRequest(method, p.URL()+"/api/v1"+path, strings.NewReader(body))
	req.Header.Set("Origin", p.URL())
	if cookie != nil {
		req.AddCookie(cookie)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	data, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != want {
		t.Fatalf("%s %s got %d want %d: %s", method, path, resp.StatusCode, want, data)
	}
	return resp
}

func TestI07ResetDisableRoleAndLastAdministrator(t *testing.T) {
	p, err := platform.Start(context.Background(), platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"})
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close(context.Background())
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	admin := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	session := readJSON(t, p, "/session", admin)
	adminID := session["account"].(map[string]any)["id"].(string)
	request(t, p, "PATCH", "/accounts/"+adminID, `{"enabled":false}`, admin, 409)
	request(t, p, "PATCH", "/accounts/"+adminID, `{"role":"USER"}`, admin, 409)
	request(t, p, "POST", "/accounts", `{"username":"alice","password":"temporary-password","role":"USER"}`, admin, 201)
	accounts := readJSON(t, p, "/accounts", admin)["accounts"].([]any)
	aliceID := accounts[1].(map[string]any)["id"].(string)
	alice := request(t, p, "POST", "/sessions", `{"username":"alice","password":"temporary-password"}`, nil, 201).Cookies()[0]
	request(t, p, "POST", "/accounts/"+aliceID+"/password-reset", `{"password":"reset-password-123"}`, admin, 200)
	request(t, p, "GET", "/session", "", alice, 401)
	alice = request(t, p, "POST", "/sessions", `{"username":"alice","password":"reset-password-123"}`, nil, 201).Cookies()[0]
	request(t, p, "PATCH", "/accounts/"+aliceID, `{"role":"ADMIN"}`, admin, 200)
	request(t, p, "GET", "/session", "", alice, 401)
	alice = request(t, p, "POST", "/sessions", `{"username":"alice","password":"reset-password-123"}`, nil, 201).Cookies()[0]
	request(t, p, "PATCH", "/accounts/"+aliceID, `{"enabled":false}`, admin, 200)
	request(t, p, "GET", "/session", "", alice, 401)
	request(t, p, "POST", "/sessions", `{"username":"alice","password":"reset-password-123"}`, nil, 401)
	if len(readJSON(t, p, "/accounts", admin)["accounts"].([]any)) != 2 {
		t.Fatal("停用不应删除账号")
	}
}
func readJSON(t *testing.T, p *platform.Runtime, path string, cookie *http.Cookie) map[string]any {
	t.Helper()
	req, _ := http.NewRequest("GET", p.URL()+"/api/v1"+path, nil)
	req.AddCookie(cookie)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("读取 %s: %d", path, resp.StatusCode)
	}
	var data map[string]any
	if err = json.NewDecoder(resp.Body).Decode(&data); err != nil {
		t.Fatal(err)
	}
	return data
}

func TestI02AbsoluteExpiryAndPersistence(t *testing.T) {
	for _, tc := range []struct {
		name string
		ttl  time.Duration
	}{{"default-expiry", 0}, {"configured-expiry", 2 * time.Hour}} {
		t.Run(tc.name, func(t *testing.T) {
			var clock atomic.Int64
			clock.Store(time.Date(2026, 10, 18, 0, 0, 0, 0, time.UTC).UnixNano())
			now := func() time.Time { return time.Unix(0, clock.Load()) }
			dir := t.TempDir()
			cfg := platform.Config{DataDir: dir, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", SessionTTL: tc.ttl, Now: now}
			p, err := platform.Start(context.Background(), cfg)
			if err != nil {
				t.Fatal(err)
			}
			request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
			login := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201)
			cookie := login.Cookies()[0]
			if !cookie.HttpOnly || cookie.SameSite != http.SameSiteStrictMode || cookie.Path != "/" {
				t.Fatalf("Cookie 安全属性无效: %+v", cookie)
			}
			ttl := tc.ttl
			if ttl == 0 {
				ttl = 8 * time.Hour
			}
			clock.Add(int64(ttl - time.Second))
			request(t, p, "GET", "/session", "", cookie, 200)
			p.Close(context.Background())
			p, err = platform.Start(context.Background(), cfg)
			if err != nil {
				t.Fatal(err)
			}
			defer p.Close(context.Background())
			request(t, p, "GET", "/session", "", cookie, 200)
			clock.Add(int64(time.Second))
			request(t, p, "GET", "/session", "", cookie, 401)
		})
	}
}
func TestI02OriginAndDirectRPCEnforceAuthorization(t *testing.T) {
	var logs bytes.Buffer
	logger := slog.New(slog.NewJSONHandler(&logs, nil))
	p, err := platform.Start(context.Background(), platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", Logger: logger})
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close(context.Background())
	req, _ := http.NewRequest("POST", p.URL()+"/api/v1/initialization", strings.NewReader(`{"username":"admin","password":"first-password-123"}`))
	req.Header.Set("Origin", "https://foreign.invalid")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 403 {
		t.Fatalf("cross-origin got %d", resp.StatusCode)
	}
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	req, _ = http.NewRequest("POST", p.URL()+"/api/v1/sessions", strings.NewReader(`{"username":"admin","password":"first-password-123"}`))
	req.Header.Set("Origin", p.URL())
	resp, err = http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	secret := resp.Cookies()[0].Value
	if strings.Contains(string(body), secret) || strings.Contains(string(body), "first-password-123") || strings.Contains(logs.String(), secret) || strings.Contains(logs.String(), "first-password-123") {
		t.Fatal("凭据泄漏")
	}
	cli, err := controlservice.NewClient("direct-test", client.WithHostPorts(p.RPCAddress()))
	if err != nil {
		t.Fatal(err)
	}
	_, err = cli.ListAccounts(context.Background(), &rpc.RequestContext{RequestId: "direct-no-session"})
	var apiErr *rpc.APIError
	if !errors.As(err, &apiErr) || apiErr.Code != "UNAUTHENTICATED" {
		t.Fatalf("直接 RPC 绕过会话: %v", err)
	}
	request(t, p, "DELETE", "/session", "", resp.Cookies()[0], 200)
	request(t, p, "GET", "/session", "", resp.Cookies()[0], 401)
}
func TestI07ConcurrentDemotionsRetainAdministrator(t *testing.T) {
	p, err := platform.Start(context.Background(), platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"})
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close(context.Background())
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	admin := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	request(t, p, "POST", "/accounts", `{"username":"second","password":"temporary-password","role":"ADMIN"}`, admin, 201)
	second := request(t, p, "POST", "/sessions", `{"username":"second","password":"temporary-password"}`, nil, 201).Cookies()[0]
	request(t, p, "POST", "/session/password", `{"currentPassword":"temporary-password","newPassword":"personal-password"}`, second, 200)
	second = request(t, p, "POST", "/sessions", `{"username":"second","password":"personal-password"}`, nil, 201).Cookies()[0]
	data := readJSON(t, p, "/accounts", admin)["accounts"].([]any)
	ids := []string{data[0].(map[string]any)["id"].(string), data[1].(map[string]any)["id"].(string)}
	cookies := []*http.Cookie{admin, second}
	statuses := make(chan int, 2)
	for i := range ids {
		go func(i int) {
			req, _ := http.NewRequest("PATCH", p.URL()+"/api/v1/accounts/"+ids[i], strings.NewReader(`{"role":"USER"}`))
			req.Header.Set("Origin", p.URL())
			req.AddCookie(cookies[i])
			resp, e := http.DefaultClient.Do(req)
			if e != nil {
				statuses <- 0
				return
			}
			resp.Body.Close()
			statuses <- resp.StatusCode
		}(i)
	}
	counts := map[int]int{}
	counts[<-statuses]++
	counts[<-statuses]++
	if counts[200] != 1 || counts[409] != 1 {
		t.Fatalf("并发降级: %v", counts)
	}
}

func TestT07StartupFailureCleansListenersAndRejectsSecondWriter(t *testing.T) {
	p, err := platform.Start(context.Background(), platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"})
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close(context.Background())
	for _, which := range []string{"http", "rpc"} {
		t.Run(which, func(t *testing.T) {
			held, e := net.Listen("tcp", "127.0.0.1:0")
			if e != nil {
				t.Fatal(e)
			}
			defer held.Close()
			free, e := net.Listen("tcp", "127.0.0.1:0")
			if e != nil {
				t.Fatal(e)
			}
			freeAddr := free.Addr().String()
			free.Close()
			cfg := platform.Config{DataDir: t.TempDir(), HTTPAddr: freeAddr, RPCAddr: freeAddr}
			if which == "http" {
				cfg.HTTPAddr = held.Addr().String()
			} else {
				cfg.RPCAddr = held.Addr().String()
			}
			if _, e = platform.Start(context.Background(), cfg); e == nil {
				t.Fatal("应拒绝已占 listener")
			}
			again, e := net.Listen("tcp", freeAddr)
			if e != nil {
				t.Fatal("半启动遗留 listener", e)
			}
			again.Close()
		})
	}
	dir := t.TempDir()
	first, err := platform.Start(context.Background(), platform.Config{DataDir: dir, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"})
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close(context.Background())
	if _, err = platform.Start(context.Background(), platform.Config{DataDir: dir, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"}); err == nil {
		t.Fatal("同数据目录允许第二 writer")
	}
	bad := filepath.Join(t.TempDir(), "file")
	if err = os.WriteFile(bad, []byte("not-directory"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err = platform.Start(context.Background(), platform.Config{DataDir: bad, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"}); err == nil {
		t.Fatal("数据库不可用仍启动成功")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err = platform.Start(ctx, platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"}); err == nil {
		t.Fatal("真实 RPC 探测失败仍启动成功")
	}
}

func TestUnknownAPIStaysJSONWhenServingFrontend(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "index.html"), []byte("frontend"), 0600); err != nil {
		t.Fatal(err)
	}
	p, err := platform.Start(context.Background(), platform.Config{DataDir: t.TempDir(), StaticDir: dir, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"})
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close(context.Background())
	resp, err := http.Get(p.URL() + "/api/v1/unknown")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 404 || !strings.Contains(resp.Header.Get("Content-Type"), "application/json") {
		t.Fatalf("未知 API 被静态页面替代: %d %s", resp.StatusCode, resp.Header.Get("Content-Type"))
	}
}

type unavailableRPC struct{}

func (unavailableRPC) DialTimeout(string, string, time.Duration) (net.Conn, error) {
	return nil, errors.New("controlled transport unavailable")
}
func TestT07ActualRPCProbeRequiredBeforeReady(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	p, err := platform.Start(ctx, platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", RPCDialer: unavailableRPC{}})
	if err == nil {
		p.Close(context.Background())
		t.Fatal("RPC transport 不可用仍 ready")
	}
}

func TestI02HTTPSOriginSetsSecureCookie(t *testing.T) {
	p, err := platform.Start(context.Background(), platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", PublicOrigin: "https://platform.example.invalid"})
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close(context.Background())
	for _, path := range []string{"initialization", "sessions"} {
		req, _ := http.NewRequest("POST", p.URL()+"/api/v1/"+path, strings.NewReader(`{"username":"admin","password":"first-password-123"}`))
		req.Header.Set("Origin", "https://platform.example.invalid")
		resp, e := http.DefaultClient.Do(req)
		if e != nil {
			t.Fatal(e)
		}
		resp.Body.Close()
		if resp.StatusCode != 201 {
			t.Fatalf("HTTPS入口返回%d", resp.StatusCode)
		}
		if path == "sessions" && (len(resp.Cookies()) != 1 || !resp.Cookies()[0].Secure) {
			t.Fatal("HTTPS入口未标记Secure Cookie")
		}
	}
}
