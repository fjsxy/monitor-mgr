package platform_test

import (
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/cloudwego/kitex/client"
	"github.com/cloudwego/netpoll"
	"github.com/fjsxy/monitor-mgr/internal/platform"
	"github.com/fjsxy/monitor-mgr/internal/teststeps"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"github.com/fjsxy/monitor-mgr/kitex_gen/control/controlservice"
	ginkgo "github.com/onsi/ginkgo/v2"
)

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestI01InitializeOnlyEmptyDatabase(t ginkgo.GinkgoTInterface) {
	// 接下来初始化空库，并拒绝再次初始化已有平台。
	ginkgo.By("初始化空库，并拒绝再次初始化已有平台")

	p, err := platform.Start(context.Background(), platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"})
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close(context.Background())
	for i, want := range []int{201, 409} {
		req, _ := http.NewRequest("POST", p.URL()+"/api/v1/initialization", strings.NewReader(`{"username":"admin","password":"first-password-123"}`))
		req.Header.Set("Origin", p.URL())
		req.Header.Set("Idempotency-Key", time.Now().UTC().Format(time.RFC3339Nano))
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

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestI01TemporaryPasswordMustChangeAndRevokesSession(t ginkgo.GinkgoTInterface) {
	// 接下来使用临时密码登录、修改密码并核验旧会话失效。
	ginkgo.By("使用临时密码登录、修改密码并核验旧会话失效")

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

func request(t ginkgo.GinkgoTInterface, p *platform.Runtime, method, path, body string, cookie *http.Cookie, want int) *http.Response {
	t.Helper()
	req, _ := http.NewRequest(method, p.URL()+"/api/v1"+path, strings.NewReader(body))
	req.Header.Set("Origin", p.URL())
	req.Header.Set("Idempotency-Key", time.Now().UTC().Format(time.RFC3339Nano))
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

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestI07ResetDisableRoleAndLastAdministrator(t ginkgo.GinkgoTInterface) {
	// 接下来重置密码、禁用账号和变更角色，并保护最后一名管理员。
	ginkgo.By("重置密码、禁用账号和变更角色，并保护最后一名管理员")

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

func readJSON(t ginkgo.GinkgoTInterface, p *platform.Runtime, path string, cookie *http.Cookie) map[string]any {
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

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestI02AbsoluteExpiryAndPersistence(t ginkgo.GinkgoTInterface) {
	// 接下来推进受控时钟并重启平台，验证会话绝对过期与持久化。
	ginkgo.By("推进受控时钟并重启平台，验证会话绝对过期与持久化")

	for _, tc := range []struct {
		name string
		ttl  time.Duration
	}{{"default-expiry", 0}, {"configured-expiry", 2 * time.Hour}} {
		teststeps.Step(tc.name, func() {
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

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestI02OriginAndDirectRPCEnforceAuthorization(t ginkgo.GinkgoTInterface) {
	// 接下来分别通过 HTTP Origin 与直接 RPC 验证授权边界。
	ginkgo.By("分别通过 HTTP Origin 与直接 RPC 验证授权边界")

	// RPC 后台写日志时，断言快照也使用同一把锁，避免记录器自身产生 race。
	var logs ksmLogBuffer
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
	req.Header.Set("Idempotency-Key", time.Now().UTC().Format(time.RFC3339Nano))
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

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestI07ConcurrentDemotionsRetainAdministrator(t ginkgo.GinkgoTInterface) {
	// 接下来并发降级管理员，验证事务始终保留一名管理员。
	ginkgo.By("并发降级管理员，验证事务始终保留一名管理员")

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
			defer ginkgo.GinkgoRecover()
			req, _ := http.NewRequest("PATCH", p.URL()+"/api/v1/accounts/"+ids[i], strings.NewReader(`{"role":"USER"}`))
			req.Header.Set("Origin", p.URL())
			req.Header.Set("Idempotency-Key", time.Now().UTC().Format(time.RFC3339Nano))
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

type unavailableRPC struct{}

func (unavailableRPC) DialTimeout(string, string, time.Duration) (net.Conn, error) {
	return nil, errors.New("controlled transport unavailable")
}

type cancelingRPC struct{ cancel context.CancelFunc }

func (d cancelingRPC) DialTimeout(string, string, time.Duration) (net.Conn, error) {
	d.cancel()
	return nil, errors.New("controlled startup cancellation")
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestI02HTTPSOriginSetsSecureCookie(t ginkgo.GinkgoTInterface) {
	// 接下来使用 HTTPS Origin 登录，核验 Cookie 的 Secure 属性。
	ginkgo.By("使用 HTTPS Origin 登录，核验 Cookie 的 Secure 属性")

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

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestAccountMutationsAreDurablyIdempotentAndStillAuthorize(t ginkgo.GinkgoTInterface) {
	// 接下来重试账号写操作，验证持久化幂等与当前授权仍生效。
	ginkgo.By("重试账号写操作，验证持久化幂等与当前授权仍生效")

	dir := t.TempDir()
	cfg := platform.Config{DataDir: dir, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"}
	p, err := platform.Start(context.Background(), cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { p.Close(context.Background()) }()
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	cookie := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	send := func(body, key string, want int) map[string]any {
		t.Helper()
		req, _ := http.NewRequest("POST", p.URL()+"/api/v1/accounts", strings.NewReader(body))
		req.Header.Set("Origin", p.URL())
		req.Header.Set("Idempotency-Key", time.Now().UTC().Format(time.RFC3339Nano))
		req.Header.Set("Idempotency-Key", key)
		req.AddCookie(cookie)
		res, e := http.DefaultClient.Do(req)
		if e != nil {
			t.Fatal(e)
		}
		defer res.Body.Close()
		var value map[string]any
		json.NewDecoder(res.Body).Decode(&value)
		if res.StatusCode != want {
			t.Fatalf("幂等请求得到 %d，期待 %d", res.StatusCode, want)
		}
		return value
	}
	body := `{"username":"idempotent-user","password":"temporary-password","role":"USER"}`
	first := send(body, "create-unique-key", 201)
	second := send(body, "create-unique-key", 201)
	if first["id"] != second["id"] {
		t.Fatal("同键产生不同账号")
	}
	send(`{"username":"different-user","password":"temporary-password","role":"USER"}`, "create-unique-key", 409)
	send(`{"username":"different-user","password":"short","role":"UNKNOWN"}`, "create-unique-key", 409)
	p.Close(context.Background())
	p, err = platform.Start(context.Background(), cfg)
	if err != nil {
		t.Fatal(err)
	}
	third := send(body, "create-unique-key", 201)
	if third["id"] != first["id"] {
		t.Fatal("重启丢失幂等结果")
	}
	request(t, p, "DELETE", "/session", "", cookie, 200)
	send(body, "create-unique-key", 401)
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestSchemaOneMigrationPreservesSessionAndBacksUpBeforeDDL(t ginkgo.GinkgoTInterface) {
	// 接下来迁移旧数据库，核验 DDL 前备份及原会话保留。
	ginkgo.By("迁移旧数据库，核验 DDL 前备份及原会话保留")

	dir := t.TempDir()
	key := bytes.Repeat([]byte{7}, 32)
	if err := os.WriteFile(filepath.Join(dir, "master.key"), key, 0600); err != nil {
		t.Fatal(err)
	}
	db, err := sql.Open("sqlite", filepath.Join(dir, "platform.db"))
	if err != nil {
		t.Fatal(err)
	}
	for _, q := range []string{`CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)`, `INSERT INTO schema_migrations VALUES(1)`, `CREATE TABLE accounts(id TEXT PRIMARY KEY,username TEXT NOT NULL UNIQUE,password_hash BLOB NOT NULL,role TEXT NOT NULL,enabled INTEGER NOT NULL,must_change INTEGER NOT NULL,auth_version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)`, `CREATE TABLE sessions(digest TEXT PRIMARY KEY,account_id TEXT NOT NULL,auth_version INTEGER NOT NULL,expires_at TEXT NOT NULL)`, `CREATE TABLE audit(id TEXT PRIMARY KEY,actor_id TEXT,action TEXT NOT NULL,target_id TEXT,created_at TEXT NOT NULL)`} {
		if _, err = db.Exec(q); err != nil {
			t.Fatal(err)
		}
	}
	if _, err = db.Exec(`INSERT INTO accounts VALUES('fixture-admin','admin',?,'ADMIN',1,0,1,'2026-01-01T00:00:00Z')`, []byte("$2a$10$L7sUQdVEfNpnBzKsaRo7oOmyJTLoxzdutn/dufIEbTCd.YQFAvmKO")); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256([]byte("schema-one-session-token"))
	if _, err = db.Exec(`INSERT INTO sessions VALUES(?,'fixture-admin',1,'2099-01-01T00:00:00Z')`, hex.EncodeToString(sum[:])); err != nil {
		t.Fatal(err)
	}
	db.Close()
	// An unusable backup destination must fail before any schema writes.
	if err = os.WriteFile(filepath.Join(dir, "backups"), []byte("blocked"), 0600); err != nil {
		t.Fatal(err)
	}
	cfg := platform.Config{DataDir: dir, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"}
	p, err := platform.Start(context.Background(), cfg)
	if err == nil {
		p.Close(context.Background())
		t.Fatal("无法快照仍升级数据库")
	}
	os.Remove(filepath.Join(dir, "backups"))
	p, err = platform.Start(context.Background(), cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close(context.Background())
	request(t, p, "GET", "/session", "", &http.Cookie{Name: "monitor_session", Value: "schema-one-session-token"}, 200)
	snapshots, err := filepath.Glob(filepath.Join(dir, "backups", "schema-1-to-*-*.db"))
	if err != nil || len(snapshots) != 1 {
		t.Fatalf("缺一致快照: %v %v", snapshots, err)
	}
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestT07DatabaseCorruptionNeverStartsReady(t ginkgo.GinkgoTInterface) {
	// 接下来破坏数据库后启动平台，确认不会报告就绪。
	ginkgo.By("破坏数据库后启动平台，确认不会报告就绪")

	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "master.key"), bytes.Repeat([]byte{9}, 32), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "platform.db"), []byte("not-a-sqlite-database"), 0600); err != nil {
		t.Fatal(err)
	}
	p, err := platform.Start(context.Background(), platform.Config{DataDir: dir, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"})
	if err == nil {
		p.Close(context.Background())
		t.Fatal("损坏数据库仍 ready")
	}
}

type switchableDialer struct {
	mu          sync.Mutex
	failed      bool
	connections []net.Conn
}

func (d *switchableDialer) DialTimeout(network, address string, timeout time.Duration) (net.Conn, error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.failed {
		return nil, errors.New("controlled RPC transport unavailable")
	}
	conn, err := netpoll.DialConnection(network, address, timeout)
	if err == nil {
		d.connections = append(d.connections, conn)
	}
	return conn, err
}
func (d *switchableDialer) breakConnections() {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.failed = true
	for _, conn := range d.connections {
		conn.Close()
	}
}
