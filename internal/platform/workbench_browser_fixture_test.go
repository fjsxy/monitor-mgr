package platform_test

import (
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"strings"
	"sync"

	ginkgo "github.com/onsi/ginkgo/v2"
)

// TestWorkbenchBrowserFixture 仅提供受控TLS目标上的真实Host应用诊断，不访问真实集群或计入Pod B。

// 私有故障控制仅作用于受控TLS目标；业务HTTP/RPC与SQLite均为真实平台，dryRun保持可用。
func workbenchControlledTarget(t ginkgo.GinkgoTInterface, target, token string) (string, map[string]any) {
	t.Helper()
	var cfg map[string]any
	if json.Unmarshal([]byte(target), &cfg) != nil {
		t.Fatal("诊断目标配置不可读")
	}
	cluster := cfg["clusters"].([]any)[0].(map[string]any)["cluster"].(map[string]any)
	upstream, e := url.Parse(cluster["server"].(string))
	if e != nil {
		t.Fatal("诊断目标URL不可读")
	}
	ca, e := base64.StdEncoding.DecodeString(cluster["certificate-authority-data"].(string))
	if e != nil {
		t.Fatal("诊断目标CA不可读")
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(ca) {
		t.Fatal("诊断目标CA无效")
	}
	proxy := httputil.NewSingleHostReverseProxy(upstream)
	transport := &http.Transport{TLSClientConfig: &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12}}
	t.Cleanup(transport.CloseIdleConnections)
	proxy.Transport = transport
	var mu sync.Mutex
	reject := ""
	tlsServer := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		blocked := reject != "" && r.URL.Path == reject && r.URL.Query().Get("dryRun") == "" && r.Method != "GET"
		mu.Unlock()
		if blocked {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(403)
			io.WriteString(w, `{"kind":"Status","status":"Failure","reason":"Forbidden"}`)
			return
		}
		proxy.ServeHTTP(w, r)
	}))
	t.Cleanup(tlsServer.Close)
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" || r.Header.Get("X-Fault-Control") != token {
			w.WriteHeader(403)
			return
		}
		var request struct {
			RejectWritePath string `json:"rejectWritePath"`
		}
		if r.URL.Path != "/faults" || json.NewDecoder(r.Body).Decode(&request) != nil || (!strings.HasPrefix(request.RejectWritePath, "/apis/") && request.RejectWritePath != "") {
			w.WriteHeader(400)
			return
		}
		mu.Lock()
		reject = request.RejectWritePath
		mu.Unlock()
		io.WriteString(w, `{}`)
	}))
	t.Cleanup(control.Close)
	cluster["server"] = tlsServer.URL
	cluster["certificate-authority-data"] = base64.StdEncoding.EncodeToString([]byte(pemCert(tlsServer.Certificate().Raw)))
	raw, _ := json.Marshal(cfg)
	return string(raw), map[string]any{"baseURL": control.URL, "controlToken": token}
}
