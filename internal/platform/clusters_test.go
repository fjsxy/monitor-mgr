package platform_test

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/platform"
)

// The controlled TLS API is the external system boundary; every platform call still crosses generated Kitex.
func clusterAPI(t *testing.T, ssrCode int, anonymous bool) (*httptest.Server, string, *atomic.Int64) {
	t.Helper()
	calls := new(atomic.Int64)
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/apis/authentication.k8s.io/v1/selfsubjectreviews":
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
func clusterPlatform(t *testing.T) (*platform.Runtime, *http.Cookie, string) {
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
func clusterCall(t *testing.T, p *platform.Runtime, method, path string, body any, cookie *http.Cookie, key string, want int) map[string]any {
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
func awaitClusterOperation(t *testing.T, p *platform.Runtime, c *http.Cookie, id string) map[string]any {
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
func TestC01PersistentClusterRegistrationThroughRPC(t *testing.T) {
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
