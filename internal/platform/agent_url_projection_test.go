package platform_test

import (
	"bytes"
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"gopkg.in/yaml.v3"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/cloudwego/kitex/client"
	"github.com/fjsxy/monitor-mgr/internal/platform"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"github.com/fjsxy/monitor-mgr/kitex_gen/control/controlservice"
)

// 投影负例不能在断言失败时输出响应体，否则测试报告自身会泄漏正在检测的认证串。
func projectionCall(t *testing.T, p *platform.Runtime, method, path string, body any, cookie *http.Cookie, key string, want int) map[string]any {
	t.Helper()
	raw, err := json.Marshal(body)
	if err != nil {
		t.Fatal("测试请求无法编码")
	}
	req, err := http.NewRequest(method, p.URL()+"/api/v1"+path, bytes.NewReader(raw))
	if err != nil {
		t.Fatal("测试请求无法建立")
	}
	req.Header.Set("Origin", p.URL())
	req.Header.Set("Idempotency-Key", key)
	req.AddCookie(cookie)
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal("测试HTTP请求失败")
	}
	defer res.Body.Close()
	data, err := io.ReadAll(res.Body)
	if err != nil {
		t.Fatal("测试HTTP响应不可读")
	}
	if res.StatusCode != want {
		t.Fatalf("投影请求状态不符：got=%d want=%d", res.StatusCode, want)
	}
	var out map[string]any
	if json.Unmarshal(data, &out) != nil {
		t.Fatal("测试HTTP响应无法解码")
	}
	return out
}

// 输出只记泄漏与否；临时marker、完整URL及原数据库仅在进程内用于比较。
func assertProjectionSafe(t *testing.T, area string, value any, markers []string) {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal("安全投影证据无法编码")
	}
	for _, marker := range markers {
		if bytes.Contains(raw, []byte(marker)) {
			t.Errorf("%s含认证marker：leaked=true", area)
		}
	}
}

// PROJECTION013经真实HTTP→生成Kitex→SQLite及TLS目标检验失败关闭；DB检查属于已批准的秘密不落盘边界。
func TestAgentMalformedURLReadProjection(t *testing.T) {
	for _, tc := range []struct{ name, field string }{{"url-parse-error", "url"}, {"proxy-parse-error", "proxyUrl"}} {
		t.Run(tc.name, func(t *testing.T) {
			f, target := agentTarget(t)
			var writes atomic.Int32
			f.hook = func(_ http.ResponseWriter, r *http.Request) bool {
				if strings.Contains(r.URL.Path, "/prometheusagents") && r.Method != "GET" {
					writes.Add(1)
				}
				return false
			}
			dir := t.TempDir()
			var logs ksmLogBuffer
			logger, oldLogger := slog.New(slog.NewJSONHandler(&logs, nil)), slog.Default()
			slog.SetDefault(logger)
			t.Cleanup(func() { slog.SetDefault(oldLogger) })
			p, err := platform.Start(context.Background(), platform.Config{DataDir: dir, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", Logger: logger})
			if err != nil {
				t.Fatal("平台启动失败")
			}
			t.Cleanup(func() { p.Close(context.Background()) })
			request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
			cookie := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
			call := func(method, path string, body any, key string, want int) map[string]any {
				return projectionCall(t, p, method, path, body, cookie, key, want)
			}
			owner := call("GET", "/session", nil, "", 200)["account"].(map[string]any)["id"].(string)
			registration := call("POST", "/clusters", map[string]any{"name": "projection", "ownerId": owner, "context": "explicit", "kubeconfig": target}, "register", 202)
			cluster := awaitClusterOperation(t, p, cookie, registration["operationId"].(string))["clusterId"].(string)
			created := call("POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": agentConfig("projection", "monitoring")}, "create", 201)
			id := created["id"].(string)
			initial := call("POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, "initial-preview", 200)
			accepted := call("POST", "/instances/"+id+"/publications", map[string]any{"previewId": initial["previewId"], "draftRevision": 1, "overwriteDrift": false}, "initial-publication", 202)
			if awaitClusterOperation(t, p, cookie, accepted["operationId"].(string))["state"] != "SUCCEEDED" {
				t.Fatal("合法Agent前置发布失败")
			}
			db, err := sql.Open("sqlite", "file:"+filepath.Join(dir, "platform.db")+"?mode=ro")
			if err != nil {
				t.Fatal("只读证据DB打开失败")
			}
			defer db.Close()
			var frozenBefore string
			if db.QueryRow("SELECT effective_spec FROM publication_versions WHERE instance_id=?", id).Scan(&frozenBefore) != nil {
				t.Fatal("冻结版本读取失败")
			}
			markers := []string{}
			for range 2 {
				b := make([]byte, 24)
				if _, err = rand.Read(b); err != nil {
					t.Fatal("临时marker生成失败")
				}
				markers = append(markers, hex.EncodeToString(b))
			}
			malformed := func(marker string) string { return "https://reader:" + marker + "@receiver.example.invalid/%zz" }
			f.mu.Lock()
			cr := f.objects["monitoring/projection"]
			cr["spec"].(map[string]any)["remoteWrite"].([]any)[0].(map[string]any)[tc.field] = malformed(markers[0])
			// 正控固定成功解析分支：普通URL保持原样，合法userinfo仅隐藏认证部分。
			cr["spec"].(map[string]any)["remoteWrite"] = append(cr["spec"].(map[string]any)["remoteWrite"].([]any),
				map[string]any{"url": "https://plain.example.invalid/write", tc.field: "https://plain.example.invalid/write"},
				map[string]any{"url": "https://safe.example.invalid/write", tc.field: "https://reader:" + markers[0] + "@safe.example.invalid/write"})
			cr["metadata"].(map[string]any)["annotations"] = map[string]any{"preserve": "original-metadata"}
			cr["metadata"].(map[string]any)["finalizers"] = []any{"fixture.example.invalid/preserve"}
			raw, _ := json.Marshal(cr)
			var external map[string]any
			json.Unmarshal(raw, &external)
			external["metadata"].(map[string]any)["name"] = "outside-projection"
			external["metadata"].(map[string]any)["uid"] = "outside-projection-uid"
			f.objects["monitoring/outside-projection"] = external
			originalMeta, _ := json.Marshal(cr["metadata"])
			outsideMeta, _ := json.Marshal(external["metadata"])
			f.mu.Unlock()
			beforeWrites := writes.Load()
			listing := call("GET", "/clusters/"+cluster+"/instances", nil, "", 200)
			assertProjectionSafe(t, "HTTP发现", listing, markers)
			outsideID := ""
			for _, v := range listing["instances"].([]any) {
				i := v.(map[string]any)
				if i["name"] == "outside-projection" && i["readOnly"] == true {
					outsideID = i["id"].(string)
				}
			}
			if outsideID == "" {
				t.Fatal("外部Agent未发现")
			}
			for _, instance := range []string{id, outsideID} {
				for _, path := range []string{"", "/runtime", "/versions"} {
					assertProjectionSafe(t, "HTTP详情/运行/版本", call("GET", "/instances/"+instance+path, nil, "", 200), markers)
				}
				assertProjectionSafe(t, "HTTP刷新", call("POST", "/instances/"+instance+"/refresh", map[string]any{}, "refresh-"+instance, 200), markers)
				assertProjectionSafe(t, "HTTP历史", call("GET", "/operations?instanceId="+instance, nil, "", 200), markers)
			}
			preview := call("POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, "malformed-preview", 200)
			assertProjectionSafe(t, "HTTP左右YAML", preview, markers)
			var left map[string]any
			if yaml.Unmarshal([]byte(preview["leftYAML"].(string)), &left) != nil {
				t.Fatal("左右YAML无法解码")
			}
			endpoints := left["spec"].(map[string]any)["remoteWrite"].([]any)
			if endpoints[0].(map[string]any)[tc.field] != "[敏感内容已隐藏]" {
				t.Error("解析失败URL未整值隐藏")
			}
			if endpoints[1].(map[string]any)[tc.field] != "https://plain.example.invalid/write" {
				t.Error("合法普通URL被读投影改变")
			}
			valid, parseErr := url.Parse(endpoints[2].(map[string]any)[tc.field].(string))
			if parseErr != nil || valid.User == nil || valid.User.Username() != "[已隐藏]" || valid.Host != "safe.example.invalid" || valid.Path != "/write" {
				t.Error("合法userinfo原有遮蔽行为改变")
			}
			cli, err := controlservice.NewClient("projection-direct", client.WithHostPorts(p.RPCAddress()))
			if err != nil {
				t.Fatal("真实RPC client建立失败")
			}
			token := cookie.Value
			ctx := &rpc.RequestContext{SessionToken: &token}
			rpcList, err := cli.ListInstances(context.Background(), ctx, &rpc.ClusterID{Id: cluster})
			if err != nil {
				t.Fatal("直接RPC发现失败")
			}
			assertProjectionSafe(t, "直接RPC发现", rpcList, markers)
			for _, instance := range []string{id, outsideID} {
				detail, err := cli.GetInstance(context.Background(), ctx, &rpc.InstanceID{Id: instance})
				if err != nil {
					t.Fatal("直接RPC详情失败")
				}
				assertProjectionSafe(t, "直接RPC详情/YAML", detail, markers)
				runtime, err := cli.RefreshInstance(context.Background(), ctx, &rpc.InstanceID{Id: instance})
				if err != nil {
					t.Fatal("直接RPC刷新失败")
				}
				assertProjectionSafe(t, "直接RPC刷新", runtime, markers)
				versions, err := cli.ListVersions(context.Background(), ctx, &rpc.InstanceID{Id: instance})
				if err != nil {
					t.Fatal("直接RPC版本失败")
				}
				assertProjectionSafe(t, "直接RPC版本", versions, markers)
			}
			// 关键：遮蔽后的显示相同仍须用原raw HMAC识别认证值变化，旧preview不能借此获得写许可。
			f.mu.Lock()
			cr["spec"].(map[string]any)["remoteWrite"].([]any)[0].(map[string]any)[tc.field] = malformed(markers[1])
			f.mu.Unlock()
			call("POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": true}, "changed-hidden-marker", 409)
			assertProjectionSafe(t, "再次发现", call("GET", "/clusters/"+cluster+"/instances", nil, "", 200), markers)
			f.mu.Lock()
			currentMeta, _ := json.Marshal(cr["metadata"])
			currentOutside, _ := json.Marshal(external["metadata"])
			rawValue := cr["spec"].(map[string]any)["remoteWrite"].([]any)[0].(map[string]any)[tc.field]
			f.mu.Unlock()
			if !reflect.DeepEqual(originalMeta, currentMeta) || !reflect.DeepEqual(outsideMeta, currentOutside) || rawValue != malformed(markers[1]) || writes.Load() != beforeWrites {
				t.Fatal("读投影改变目标metadata/raw或产生集群写")
			}
			var frozenAfter string
			if db.QueryRow("SELECT effective_spec FROM publication_versions WHERE instance_id=?", id).Scan(&frozenAfter) != nil || frozenAfter != frozenBefore {
				t.Fatal("读投影改写历史effective_spec")
			}
			for _, q := range []string{"SELECT cr FROM instance_observations", "SELECT document FROM discovered_instances", "SELECT target_spec FROM publication_previews"} {
				rows, err := db.Query(q)
				if err != nil {
					t.Fatal("投影缓存读取失败")
				}
				for rows.Next() {
					var raw []byte
					if rows.Scan(&raw) != nil {
						t.Fatal("投影缓存不可读")
					}
					assertProjectionSafe(t, "SQLite读模型", string(raw), markers)
				}
				if rows.Err() != nil {
					t.Fatal("投影缓存遍历失败")
				}
				rows.Close()
			}
			paths, _ := filepath.Glob(filepath.Join(dir, "platform.db*"))
			for _, path := range paths {
				raw, err := os.ReadFile(path)
				if err != nil {
					t.Fatal("DB/WAL证据不可读")
				}
				for _, marker := range markers {
					if bytes.Contains(raw, []byte(marker)) {
						t.Error("SQLite/ WAL含认证marker：leaked=true")
					}
				}
			}
			assertProjectionSafe(t, "安全日志", logs.String(), markers)
		})
	}
}
