package platform_test

import (
	"context"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/platform"
	ginkgo "github.com/onsi/ginkgo/v2"
)

// TestWorkbenchBrowserFixture 仅提供受控TLS目标上的真实Host应用诊断，不访问真实集群或计入Pod B。
var _ = ginkgo.It("TestWorkbenchBrowserFixture", func() {
	t := ginkgo.GinkgoT()
	// 接下来TestWorkbenchBrowserFixture 仅提供受控TLS目标上的真实Host应用诊断，不访问真实集群或计入Pod B。
	ginkgo.By("TestWorkbenchBrowserFixture 仅提供受控TLS目标上的真实Host应用诊断，不访问真实集群或计入Pod B。")

	path := os.Getenv("WORKBENCH_HOST_FIXTURE")
	stop := os.Getenv("WORKBENCH_HOST_STOP")
	if path == "" || stop == "" {
		t.Skip("仅显式Host浏览器诊断启用")
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatal("诊断fixture必须使用新私有文件")
	}
	data := make([]byte, 24)
	if _, err := rand.Read(data); err != nil {
		t.Fatal("诊断凭据生成失败")
	}
	password := hex.EncodeToString(data)
	static, err := filepath.Abs("../../web/dist")
	if err != nil {
		t.Fatal("静态构建目录不可读取")
	}
	config := platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", StaticDir: static, Logger: slog.New(slog.NewTextHandler(io.Discard, nil))}
	p, err := platform.Start(context.Background(), config)
	if err != nil {
		t.Fatal("诊断平台启动失败")
	}
	defer func() { p.Close(context.Background()) }()
	body, _ := json.Marshal(map[string]any{"username": "admin", "password": password})
	request(t, p, "POST", "/initialization", string(body), nil, 201)
	cookie := request(t, p, "POST", "/sessions", string(body), nil, 201).Cookies()[0]
	owner := readJSON(t, p, "/session", cookie)["account"].(map[string]any)["id"].(string)
	var firstTarget *ksmFixture
	clusters := []string{}
	controls := []map[string]any{}
	for index, name := range []string{"ui19-control", "ui19-data"} {
		var target string
		if index == 0 {
			f, value := ksmTarget(t)
			firstTarget = f
			f.mu.Lock()
			f.clusterUID = "ui19-target-0"
			f.mu.Unlock()
			target = value
		} else {
			f, value := agentTarget(t)
			f.mu.Lock()
			f.hook = func(w http.ResponseWriter, r *http.Request) bool {
				if r.URL.Path == "/api/v1/namespaces/kube-system" {
					io.WriteString(w, `{"metadata":{"uid":"ui19-target-1"}}`)
					return true
				}
				return false
			}
			f.mu.Unlock()
			target = value
		}
		target, control := workbenchControlledTarget(t, target, password)
		controls = append(controls, control)
		operation := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": name, "ownerId": owner, "context": "explicit", "kubeconfig": target}, cookie, name, 202)
		result := awaitClusterOperation(t, p, cookie, operation["operationId"].(string))
		if result["state"] != "SUCCEEDED" {
			t.Fatal("受控TLS目标注册失败")
		}
		clusters = append(clusters, result["clusterId"].(string))
	}
	fixture := map[string]any{"baseURL": p.URL(), "adminUsername": "admin", "adminPassword": password, "clusterId": clusters[0], "clusterName": "ui19-control", "secondClusterId": clusters[1], "secondClusterName": "ui19-data", "namespace": "monitoring", "runId": "host-ui19", "diagnosticOnly": true}
	switch os.Getenv("WORKBENCH_HOST_CASE") {
	case "U04.primary":
		name := "ui19-baseline"
		created := clusterCall(t, p, "POST", "/clusters/"+clusters[0]+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig(name)}, cookie, "baseline", 201)
		id := created["id"].(string)
		preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, cookie, "baseline-preview", 200)
		op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"draftRevision": 1, "previewId": preview["previewId"], "overwriteDrift": false}, cookie, "baseline-publish", 202)
		if awaitClusterOperation(t, p, cookie, op["operationId"].(string))["state"] != "SUCCEEDED" {
			t.Fatal("Host分页前置发布失败")
		}
		fixture["initialStatistics"] = map[string]any{"managed": 1, "unmanaged": 1}
	case "U05.primary":
		versions := []any{}
		for index, component := range []string{"kube-state-metrics", "Prometheus Agent"} {
			name := fmt.Sprintf("ui19-version-%d", index)
			key := "ksm"
			cfg := ksmConfig(name)
			path := "/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics/" + name
			cpu := "100m"
			if index == 1 {
				key = "agent"
				cfg = agentConfig(name, "monitoring")
				path = "/apis/monitoring.coreos.com/v1alpha1/namespaces/monitoring/prometheusagents/" + name
				cpu = "200m"
			}
			created := clusterCall(t, p, "POST", "/clusters/"+clusters[index]+"/instances", map[string]any{"component": component, key: cfg}, cookie, name, 201)
			id := created["id"].(string)
			preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, cookie, "preview-"+name, 200)
			op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, cookie, "publish-"+name, 202)
			if awaitClusterOperation(t, p, cookie, op["operationId"].(string))["state"] != "SUCCEEDED" {
				t.Fatal("Host版本前置发布失败")
			}
			controls[index]["writePath"] = path
			versions = append(versions, map[string]any{"id": id, "component": component, "name": name, "namespace": "monitoring", "clusterName": []string{"ui19-control", "ui19-data"}[index], "expectedRequestCPU": cpu, "expectedSourceDraftRevision": 1, "failureControl": controls[index]})
		}
		fixture["versionedInstances"] = versions
	case "REG19-HTTP-I64-001":
		created := clusterCall(t, p, "POST", "/clusters/"+clusters[0]+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("ui19-large-revision")}, cookie, "large", 201)
		id := created["id"].(string)
		// 关键：业务通过真实HTTP建立，唯独已批准的旧库大修订前置在平台关闭后离线注入。
		if err := p.Close(context.Background()); err != nil {
			t.Fatal("旧库诊断关闭失败")
		}
		db, e := sql.Open("sqlite", filepath.Join(config.DataDir, "platform.db"))
		if e != nil {
			t.Fatal("旧库诊断打开失败")
		}
		_, e = db.Exec(`UPDATE drafts SET revision=9007199254740992 WHERE instance_id=?`, id)
		db.Close()
		if e != nil {
			t.Fatal("旧库修订设置失败")
		}
		p, err = platform.Start(context.Background(), config)
		if err != nil {
			t.Fatal("旧库诊断重开失败")
		}
		fixture["baseURL"] = p.URL()
		fixture["largeRevisionInstanceId"] = id
		fixture["largeRevision"] = "9007199254740992"
		fixture["expectedLargeRevision"] = "9007199254740993"
	}
	if os.Getenv("WORKBENCH_HOST_CASE") == "U04.primary" || os.Getenv("WORKBENCH_HOST_CASE") == "U05.primary" {
		firstTarget.mu.Lock()
		raw, _ := json.Marshal(firstTarget.cr)
		var outside map[string]any
		json.Unmarshal(raw, &outside)
		outside["metadata"] = map[string]any{"name": "ui19-external", "uid": "ui19-external-uid", "resourceVersion": "1", "generation": 1}
		firstTarget.inventoryPages = map[string]map[string]any{"": {"items": []any{firstTarget.cr, outside}}}
		firstTarget.mu.Unlock()
		list := clusterCall(t, p, "GET", "/clusters/"+clusters[0]+"/instances", nil, cookie, "", 200)
		external := []any{}
		for _, value := range list["instances"].([]any) {
			item := value.(map[string]any)
			if item["readOnly"] == true {
				external = append(external, map[string]any{"id": item["id"], "name": item["name"]})
			}
		}
		if len(external) != 1 {
			t.Fatal("Host外部发现前置未建立")
		}
		fixture["unmanagedInstances"] = external
	}

	raw, _ := json.Marshal(fixture)
	// 关键：凭据仅进0600临时文件；ready输出只有URL，退出立即清理，不暴露业务HTTP替身。
	if err := os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal("私有诊断fixture写入失败")
	}
	defer os.Remove(path)
	fmt.Println("WORKBENCH_READY", p.URL())
	timer := time.NewTimer(10 * time.Minute)
	defer timer.Stop()
	ticker := time.NewTicker(200 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			if _, err := os.Stat(stop); err == nil {
				return
			}
		case <-timer.C:
			t.Fatal("Host诊断生命周期超时，已清理")
		}
	}
})

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
