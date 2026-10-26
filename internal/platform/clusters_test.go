package platform_test

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/platform"
	"github.com/fjsxy/monitor-mgr/internal/teststeps"
	ginkgo "github.com/onsi/ginkgo/v2"
	"gopkg.in/yaml.v3"
)

// The controlled TLS API is the external system boundary; every platform call still crosses generated Kitex.
func clusterAPI(t ginkgo.GinkgoTInterface, ssrCode int, anonymous bool, beforeSSR ...func(*http.Request)) (*httptest.Server, string, *atomic.Int64) {
	t.Helper()
	calls := new(atomic.Int64)
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/apis/apiextensions.k8s.io/v1/customresourcedefinitions/kubestatemetrics.monitoring.fjsxy.github.io":
			json.NewEncoder(w).Encode(collectionCRDFixture(t))
		case "/apis/authentication.k8s.io/v1/selfsubjectreviews":
			for _, f := range beforeSSR {
				f(r)
			}
			if ssrCode != 200 {
				w.WriteHeader(ssrCode)
				io.WriteString(w, `{"message":"controlled failure"}`)
				return
			}
			name := "fixture-user"
			if anonymous {
				name = "system:anonymous"
			}
			json.NewEncoder(w).Encode(map[string]any{"status": map[string]any{"userInfo": map[string]any{"username": name, "groups": []string{"system:authenticated"}}}})
		case "/api/v1/namespaces/kube-system":
			io.WriteString(w, `{"metadata":{"uid":"fixture-cluster-uid"}}`)
		case "/api/v1/namespaces":
			io.WriteString(w, `{"items":[{"metadata":{"name":"monitoring"}},{"metadata":{"name":"kube-system"}}]}`)
		case "/version":
			io.WriteString(w, `{"gitVersion":"v1.28.15"}`)
		case "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews":
			io.WriteString(w, `{"status":{"allowed":true}}`)
		default:
			io.WriteString(w, `{"items":[]}`)
		}
	}))
	t.Cleanup(srv.Close)
	pem := base64.StdEncoding.EncodeToString([]byte(pemCert(srv.Certificate().Raw)))
	config := `{"apiVersion":"v1","kind":"Config","clusters":[{"name":"target","cluster":{"server":"` + srv.URL + `","certificate-authority-data":"` + pem + `"}}],"users":[{"name":"identity","user":{"token":"fixture-sensitive-token"}}],"contexts":[{"name":"explicit","context":{"cluster":"target","user":"identity"}}]}`
	return srv, config, calls
}
func pemCert(raw []byte) string {
	return "-----BEGIN CERTIFICATE-----\n" + base64.StdEncoding.EncodeToString(raw) + "\n-----END CERTIFICATE-----\n"
}
func clusterPlatform(t ginkgo.GinkgoTInterface) (*platform.Runtime, *http.Cookie, string) {
	t.Helper()
	p, e := platform.Start(context.Background(), platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"})
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { p.Close(context.Background()) })
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	cookie := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	id := readJSON(t, p, "/session", cookie)["account"].(map[string]any)["id"].(string)
	return p, cookie, id
}
func clusterCall(t ginkgo.GinkgoTInterface, p *platform.Runtime, method, path string, body any, cookie *http.Cookie, key string, want int) map[string]any {
	t.Helper()
	raw, _ := json.Marshal(body)
	req, _ := http.NewRequest(method, p.URL()+"/api/v1"+path, strings.NewReader(string(raw)))
	req.Header.Set("Origin", p.URL())
	req.Header.Set("Idempotency-Key", key)
	if cookie != nil {
		req.AddCookie(cookie)
	}
	resp, e := http.DefaultClient.Do(req)
	if e != nil {
		t.Fatal(e)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != want {
		t.Fatalf("%s %s got %d want %d: %s", method, path, resp.StatusCode, want, b)
	}
	var v map[string]any
	if e = json.Unmarshal(b, &v); e != nil {
		t.Fatal(e)
	}
	return v
}
func awaitClusterOperation(t ginkgo.GinkgoTInterface, p *platform.Runtime, c *http.Cookie, id string) map[string]any {
	t.Helper()
	deadline := time.Now().Add(12 * time.Second)
	for time.Now().Before(deadline) {
		v := clusterCall(t, p, "GET", "/operations/"+id, nil, c, "", 200)
		state := v["state"].(string)
		if state == "SUCCEEDED" || state == "FAILED" || state == "WAITING_CONFIRMATION" {
			return v
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("operation did not finish")
	return nil
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestC01PersistentClusterRegistrationThroughRPC(t ginkgo.GinkgoTInterface) {
	// 接下来通过真实 RPC 注册集群，再核验数据库中的注册结果。
	ginkgo.By("通过真实 RPC 注册集群，再核验数据库中的注册结果")

	_, config, _ := clusterAPI(t, 200, false)
	p, c, owner := clusterPlatform(t)
	body := map[string]any{"name": "fixture", "ownerId": owner, "context": "explicit", "kubeconfig": config}
	accepted := clusterCall(t, p, "POST", "/clusters", body, c, "register-1", 202)
	id := accepted["operationId"].(string)
	replay := clusterCall(t, p, "POST", "/clusters", body, c, "register-1", 202)
	if replay["operationId"] != id {
		t.Fatal("idempotent acceptance changed operation")
	}
	op := awaitClusterOperation(t, p, c, id)
	if op["state"] != "SUCCEEDED" {
		t.Fatalf("registration failed: %v", op)
	}
	list := clusterCall(t, p, "GET", "/clusters", nil, c, "", 200)["clusters"].([]any)
	if len(list) != 1 {
		t.Fatalf("wanted one cluster: %v", list)
	}
	cl := list[0].(map[string]any)
	if cl["identity"] != "fixture-user" || cl["uid"] != "fixture-cluster-uid" || cl["credentialRevision"] != float64(1) {
		t.Fatalf("wrong verified identity/revision: %v", cl)
	}
	ns := clusterCall(t, p, "GET", "/clusters/"+cl["id"].(string)+"/namespaces", nil, c, "", 200)
	if len(ns["namespaces"].([]any)) != 2 {
		t.Fatal("namespace metadata missing")
	}
	body["name"] = "other"
	clusterCall(t, p, "POST", "/clusters", body, c, "register-1", 409)
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestC03UnverifiedIdentityCannotRegister(t ginkgo.GinkgoTInterface) {
	// 接下来逐项构造身份核验失败，确认集群不能被注册。
	ginkgo.By("逐项构造身份核验失败，确认集群不能被注册")

	for _, tc := range []struct {
		name string
		code int
		anon bool
	}{{"missing", 404, false}, {"forbidden", 403, false}, {"anonymous", 200, true}, {"error", 500, false}} {
		teststeps.Step(tc.name, func() {
			_, cfg, _ := clusterAPI(t, tc.code, tc.anon)
			p, c, owner := clusterPlatform(t)
			o := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "fixture", "ownerId": owner, "context": "explicit", "kubeconfig": cfg}, c, "register", 202)
			final := awaitClusterOperation(t, p, c, o["operationId"].(string))
			if final["state"] != "FAILED" {
				t.Fatalf("unverified identity accepted: %v", final)
			}
			if len(clusterCall(t, p, "GET", "/clusters", nil, c, "", 200)["clusters"].([]any)) != 0 {
				t.Fatal("failed candidate became registered")
			}
		})
	}
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestC09RejectSelectedContextDependenciesBeforeNetworking(t ginkgo.GinkgoTInterface) {
	// 接下来逐项构造不支持的 kubeconfig 依赖，确认联网前拒绝。
	ginkgo.By("逐项构造不支持的 kubeconfig 依赖，确认联网前拒绝")

	for _, name := range []string{"http", "skip-tls", "external-ca", "external-cert", "token-file", "exec", "auth-provider", "proxy"} {
		teststeps.Step(name, func() {
			_, cfg, calls := clusterAPI(t, 200, false)
			var doc map[string]any
			json.Unmarshal([]byte(cfg), &doc)
			cl := doc["clusters"].([]any)[0].(map[string]any)["cluster"].(map[string]any)
			u := doc["users"].([]any)[0].(map[string]any)["user"].(map[string]any)
			switch name {
			case "http":
				cl["server"] = "http://127.0.0.1:1"
			case "skip-tls":
				cl["insecure-skip-tls-verify"] = true
			case "external-ca":
				cl["certificate-authority"] = "/private/must-not-read"
			case "external-cert":
				u["client-certificate"] = "/private/must-not-read"
				u["client-key"] = "/private/must-not-read"
			case "token-file":
				u["tokenFile"] = "/private/must-not-read"
			case "exec":
				u["exec"] = map[string]any{"command": "touch", "args": []string{"/private/must-not-execute"}}
			case "auth-provider":
				u["auth-provider"] = map[string]string{"name": "gcp"}
			case "proxy":
				cl["proxy-url"] = "https://proxy.invalid"
			}
			raw, _ := json.Marshal(doc)
			p, c, owner := clusterPlatform(t)
			clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "fixture", "ownerId": owner, "context": "explicit", "kubeconfig": string(raw)}, c, "unsafe", 422)
			if calls.Load() != 0 {
				t.Fatal("unsafe config reached target")
			}
		})
	}
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestClusterCredentialLifecycleAndImmediateOwnership(t ginkgo.GinkgoTInterface) {
	// 接下来替换集群凭据与归属，核验权限立即生效。
	ginkgo.By("替换集群凭据与归属，核验权限立即生效")

	_, cfg, _ := clusterAPI(t, 200, false)
	p, c, owner := clusterPlatform(t)
	o := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "fixture", "ownerId": owner, "context": "explicit", "kubeconfig": cfg}, c, "register", 202)
	done := awaitClusterOperation(t, p, c, o["operationId"].(string))
	id := done["clusterId"].(string)
	clusterCall(t, p, "POST", "/accounts", map[string]any{"username": "alice", "password": "temporary-password", "role": "USER"}, c, "alice", 201)
	accounts := readJSON(t, p, "/accounts", c)["accounts"].([]any)
	aliceID := accounts[1].(map[string]any)["id"].(string)
	alice := request(t, p, "POST", "/sessions", `{"username":"alice","password":"temporary-password"}`, nil, 201).Cookies()[0]
	request(t, p, "POST", "/session/password", `{"currentPassword":"temporary-password","newPassword":"personal-password"}`, alice, 200)
	alice = request(t, p, "POST", "/sessions", `{"username":"alice","password":"personal-password"}`, nil, 201).Cookies()[0]
	clusterCall(t, p, "GET", "/clusters/"+id, nil, alice, "", 403)
	transferred := clusterCall(t, p, "POST", "/clusters/"+id+"/transfer", map[string]any{"ownerId": aliceID}, c, "transfer", 200)
	if transferred["id"] != id {
		t.Fatal("transfer changed cluster identity")
	}
	clusterCall(t, p, "GET", "/clusters/"+id, nil, alice, "", 200)
	history := clusterCall(t, p, "GET", "/clusters/"+id+"/operations", nil, alice, "", 200)["operations"].([]any)
	found := false
	for _, item := range history {
		m := item.(map[string]any)
		if m["action"] == "TRANSFER" {
			if m["previousOwnerId"] != owner || m["ownerId"] != aliceID {
				t.Fatalf("transfer history lost previous/new owner: %v", m)
			}
			found = true
		}
	}
	if !found {
		t.Fatal("transfer history missing")
	}
	bad := strings.Replace(cfg, "fixture-sensitive-token", "bad-candidate-token", 1)
	// A candidate with an untrusted CA must fail without replacing the effective credential.
	var doc map[string]any
	json.Unmarshal([]byte(bad), &doc)
	doc["clusters"].([]any)[0].(map[string]any)["cluster"].(map[string]any)["tls-server-name"] = "wrong.example.invalid"
	raw, _ := json.Marshal(doc)
	cand := clusterCall(t, p, "POST", "/clusters/"+id+"/credentials", map[string]any{"context": "explicit", "kubeconfig": string(raw)}, c, "bad-candidate", 202)
	if awaitClusterOperation(t, p, c, cand["operationId"].(string))["state"] != "FAILED" {
		t.Fatal("bad candidate accepted")
	}
	cl := clusterCall(t, p, "GET", "/clusters/"+id, nil, c, "", 200)
	if cl["credentialRevision"] != float64(1) {
		t.Fatal("failed candidate replaced original")
	}
	clusterCall(t, p, "GET", "/clusters/"+id+"/namespaces", nil, alice, "", 200)
	clusterCall(t, p, "POST", "/clusters/"+id+"/transfer", map[string]any{"ownerId": owner}, c, "transfer-back", 200)
	clusterCall(t, p, "GET", "/clusters/"+id+"/operations", nil, alice, "", 403)
	clusterCall(t, p, "POST", "/clusters/"+id+"/unregister", map[string]any{}, c, "unregister", 200)
	clusterCall(t, p, "GET", "/clusters/"+id+"/namespaces", nil, c, "", 409)
	react := clusterCall(t, p, "POST", "/clusters/"+id+"/reactivate", map[string]any{"ownerId": aliceID, "context": "explicit", "kubeconfig": cfg}, c, "reactivate", 202)
	r := awaitClusterOperation(t, p, c, react["operationId"].(string))
	if r["state"] != "SUCCEEDED" || r["clusterId"] != id {
		t.Fatal("reactivation failed to retain identity")
	}
	clusterCall(t, p, "GET", "/clusters/"+id+"/namespaces", nil, alice, "", 200)
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestClusterSecretsRecoverOnlyWithOriginalKeyAndPreserveSession(t ginkgo.GinkgoTInterface) {
	// 接下来重启并替换加密密钥，核验凭据恢复限制及会话保留。
	ginkgo.By("重启并替换加密密钥，核验凭据恢复限制及会话保留")

	_, cfg, _ := clusterAPI(t, 200, false)
	dir := t.TempDir()
	pcfg := platform.Config{DataDir: dir, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"}
	p, e := platform.Start(context.Background(), pcfg)
	if e != nil {
		t.Fatal(e)
	}
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	owner := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"].(string)
	o := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "fixture", "ownerId": owner, "context": "explicit", "kubeconfig": cfg}, c, "register", 202)
	final := awaitClusterOperation(t, p, c, o["operationId"].(string))
	id := final["clusterId"].(string)
	p.Close(context.Background())
	// At-rest confidentiality is an explicit external filesystem seam, not an alternate business assertion.
	entries, _ := os.ReadDir(dir)
	for _, entry := range entries {
		if entry.IsDir() || entry.Name() == "master.key" {
			continue
		}
		data, _ := os.ReadFile(filepath.Join(dir, entry.Name()))
		if bytes.Contains(data, []byte("fixture-sensitive-token")) {
			t.Fatal("plaintext credential persisted outside encrypted envelope")
		}
	}
	keyPath := filepath.Join(dir, "master.key")
	key, e := os.ReadFile(keyPath)
	if e != nil {
		t.Fatal(e)
	}
	if e = os.Rename(keyPath, keyPath+".original"); e != nil {
		t.Fatal(e)
	}
	if restarted, e := platform.Start(context.Background(), pcfg); e == nil {
		restarted.Close(context.Background())
		t.Fatal("missing key accepted")
	}
	if _, e = os.Stat(keyPath); !os.IsNotExist(e) {
		t.Fatal("replacement key created")
	}
	os.WriteFile(keyPath, bytes.Repeat([]byte{42}, 32), 0600)
	if restarted, e := platform.Start(context.Background(), pcfg); e == nil {
		restarted.Close(context.Background())
		t.Fatal("wrong key accepted existing encrypted cluster")
	}
	os.Remove(keyPath)
	os.Rename(keyPath+".original", keyPath)
	got, _ := os.ReadFile(keyPath)
	if !bytes.Equal(key, got) {
		t.Fatal("original key changed")
	}
	p, e = platform.Start(context.Background(), pcfg)
	if e != nil {
		t.Fatal(e)
	}
	defer p.Close(context.Background())
	clusterCall(t, p, "GET", "/clusters/"+id+"/namespaces", nil, c, "", 200)
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestClusterUnfinishedChangesBlockTransferAndUnregisterAcrossKinds(t ginkgo.GinkgoTInterface) {
	// 接下来构造未完成操作，阻止跨组件的归属转移和注销。
	ginkgo.By("构造未完成操作，阻止跨组件的归属转移和注销")

	_, cfg, _ := clusterAPI(t, 200, false)
	dir := t.TempDir()
	p, e := platform.Start(context.Background(), platform.Config{DataDir: dir, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"})
	if e != nil {
		t.Fatal(e)
	}
	defer p.Close(context.Background())
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	owner := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"].(string)
	o := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "fixture", "ownerId": owner, "context": "explicit", "kubeconfig": cfg}, c, "register", 202)
	done := awaitClusterOperation(t, p, c, o["operationId"].(string))
	id := done["clusterId"].(string)
	// An external persistence fixture represents later ticket kinds; assertions use only HTTP.
	db, e := sql.Open("sqlite", filepath.Join(dir, "platform.db"))
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	for _, kind := range []string{"PUBLISH", "DELETE", "FUTURE_CHANGE"} {
		opid := "unfinished-" + kind
		_, e = db.Exec(`INSERT INTO operations(id,action,state,cluster_id,actor_id,owner_id,name,message,created_at,updated_at) VALUES(?,?,'RESULT_UNCONFIRMED',?,?,?,'fixture','','2026-01-01','2026-01-01')`, opid, kind, id, owner, owner)
		if e != nil {
			t.Fatal(e)
		}
		clusterCall(t, p, "POST", "/clusters/"+id+"/transfer", map[string]any{"ownerId": owner}, c, "blocked-transfer-"+kind, 409)
		clusterCall(t, p, "POST", "/clusters/"+id+"/unregister", map[string]any{}, c, "blocked-unregister-"+kind, 409)
		db.Exec(`DELETE FROM operations WHERE id=?`, opid)
	}
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestClusterCanRegisterWithRestrictedComponentCapabilities(t ginkgo.GinkgoTInterface) {
	// 接下来使用受限组件权限注册，核验平台准确记录能力边界。
	ginkgo.By("使用受限组件权限注册，核验平台准确记录能力边界")

	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/apis/authentication.k8s.io/v1/selfsubjectreviews":
			io.WriteString(w, `{"status":{"userInfo":{"username":"restricted-reader"}}}`)
		case "/api/v1/namespaces/kube-system":
			io.WriteString(w, `{"metadata":{"uid":"restricted-cluster-uid"}}`)
		case "/version":
			io.WriteString(w, `{"gitVersion":"v1.28.15"}`)
		default:
			w.WriteHeader(403)
			io.WriteString(w, `{}`)
		}
	}))
	defer srv.Close()
	ca := base64.StdEncoding.EncodeToString([]byte(pemCert(srv.Certificate().Raw)))
	cfg := `{"clusters":[{"name":"c","cluster":{"server":"` + srv.URL + `","certificate-authority-data":"` + ca + `"}}],"users":[{"name":"u","user":{"token":"fixture-token"}}],"contexts":[{"name":"explicit","context":{"cluster":"c","user":"u"}}]}`
	p, c, owner := clusterPlatform(t)
	op := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "restricted", "ownerId": owner, "context": "explicit", "kubeconfig": cfg}, c, "restricted", 202)
	done := awaitClusterOperation(t, p, c, op["operationId"].(string))
	if done["state"] != "SUCCEEDED" {
		t.Fatalf("business permissions incorrectly blocked registration: %v", done)
	}
	cl := clusterCall(t, p, "GET", "/clusters/"+done["clusterId"].(string), nil, c, "", 200)
	for _, v := range cl["capabilities"].([]any) {
		cap := v.(map[string]any)
		if cap["allowed"] != false || cap["reason"] == "" {
			t.Fatalf("restricted component claimed available: %v", cap)
		}
	}
	clusterCall(t, p, "GET", "/clusters/"+done["clusterId"].(string)+"/namespaces", nil, c, "", 403)
}

// 使用已公开契约的最小独立 schema 夹具；完整原发布包只由真实 E2E 私有读取。
func collectionCRDFixture(t ginkgo.GinkgoTInterface) map[string]any {
	t.Helper()
	data, e := os.ReadFile("testdata/ksm-companion-capability.yaml")
	if e != nil {
		t.Fatal(e)
	}
	var out map[string]any
	if e = yaml.Unmarshal(data, &out); e != nil {
		t.Fatal(e)
	}
	return out
}
