package platform_test

import (
	"bytes"
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync/atomic"

	"github.com/cloudwego/kitex/client"
	"github.com/fjsxy/monitor-mgr/internal/platform"
	"github.com/fjsxy/monitor-mgr/internal/teststeps"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"github.com/fjsxy/monitor-mgr/kitex_gen/control/controlservice"
	ginkgo "github.com/onsi/ginkgo/v2"
	"gopkg.in/yaml.v3"
)

// 投影负例不能在断言失败时输出响应体，否则测试报告自身会泄漏正在检测的认证串。
func projectionCall(t ginkgo.GinkgoTInterface, p *platform.Runtime, method, path string, body any, cookie *http.Cookie, key string, want int) map[string]any {
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
func assertProjectionSafe(t ginkgo.GinkgoTInterface, area string, value any, markers []string) {
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
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestAgentMalformedURLReadProjection(t ginkgo.GinkgoTInterface) {
	// 接下来PROJECTION013经真实HTTP→生成Kitex→SQLite及TLS目标检验失败关闭；DB检查属于已批准的秘密不落盘边界。
	ginkgo.By("PROJECTION013经真实HTTP→生成Kitex→SQLite及TLS目标检验失败关闭；DB检查属于已批准的秘密不落盘边界。")

	for _, tc := range []struct{ name, field string }{{"url-parse-error", "url"}, {"proxy-parse-error", "proxyUrl"}} {
		teststeps.Step(tc.name, func() {
			f, target := agentTarget(t)
			var writes atomic.Int32
			var offline atomic.Bool
			f.hook = func(w http.ResponseWriter, r *http.Request) bool {
				if offline.Load() {
					w.WriteHeader(http.StatusServiceUnavailable)
					return true
				}
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
			// 受控复现真实Operator解析错误：认证URL也可能被复制进status自由文本。
			status := cr["status"].(map[string]any)
			available := status["conditions"].([]any)[0].(map[string]any)
			available["status"], available["reason"], available["message"] = "False", "WorkloadUnavailable", "Waiting for workload readiness"
			status["conditions"] = append(status["conditions"].([]any), map[string]any{
				"type": "Reconciled", "status": "False", "reason": "InvalidConfiguration",
				"message": "generating config failed: parse " + malformed(markers[0]) + ": invalid URL escape",
			})
			cr["metadata"].(map[string]any)["annotations"] = map[string]any{"preserve": "original-metadata"}
			cr["metadata"].(map[string]any)["finalizers"] = []any{"fixture.example.invalid/preserve"}
			raw, _ := json.Marshal(cr)
			var external map[string]any
			json.Unmarshal(raw, &external)
			external["metadata"].(map[string]any)["name"] = "outside-projection"
			external["metadata"].(map[string]any)["uid"] = "outside-projection-uid"
			f.objects["monitoring/outside-projection"] = external
			originalMeta, _ := json.Marshal(cr["metadata"])
			originalStatus, _ := json.Marshal(cr["status"])
			outsideMeta, _ := json.Marshal(external["metadata"])
			f.mu.Unlock()
			beforeWrites := writes.Load()
			listing := call("GET", "/clusters/"+cluster+"/instances", nil, "", 200)
			listingContains, managedRuntimeContains, externalYAMLContains, externalFound := false, false, false, false
			for _, value := range listing["instances"].([]any) {
				item := value.(map[string]any)
				encoded, _ := json.Marshal(item)
				for _, marker := range markers {
					listingContains = listingContains || strings.Contains(string(encoded), marker)
				}
				if item["name"] == "projection" {
					runtime, _ := json.Marshal(item["runtime"])
					for _, marker := range markers {
						managedRuntimeContains = managedRuntimeContains || strings.Contains(string(runtime), marker)
					}
				}
				if item["name"] == "outside-projection" {
					externalFound = true
					for _, marker := range markers {
						externalYAMLContains = externalYAMLContains || strings.Contains(item["yaml"].(string), marker)
					}
				}
			}
			call("POST", "/instances/"+id+"/refresh", map[string]any{}, "reason-positive-control", 200)
			managed := call("GET", "/instances/"+id+"/runtime", nil, "", 200)
			if managed["error"] != "WorkloadUnavailable" {
				t.Error("普通故障reason未保留")
			}
			t.Logf("listingContainsMarker=%t externalFound=%t managedRuntimeErrorContainsMarker=%t unmanagedYAMLContainsMarker=%t", listingContains, externalFound, managedRuntimeContains, externalYAMLContains)
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
			conditions := left["status"].(map[string]any)["conditions"].([]any)
			if conditions[0].(map[string]any)["reason"] != "WorkloadUnavailable" || conditions[0].(map[string]any)["message"] != "Waiting for workload readiness" || left["metadata"].(map[string]any)["annotations"].(map[string]any)["preserve"] != "original-metadata" {
				t.Error("普通故障文本或用户metadata被隐藏")
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
			currentStatus, _ := json.Marshal(cr["status"])
			currentOutside, _ := json.Marshal(external["metadata"])
			rawValue := cr["spec"].(map[string]any)["remoteWrite"].([]any)[0].(map[string]any)[tc.field]
			f.mu.Unlock()
			if !reflect.DeepEqual(originalMeta, currentMeta) || !reflect.DeepEqual(originalStatus, currentStatus) || !reflect.DeepEqual(outsideMeta, currentOutside) || rawValue != malformed(markers[1]) || writes.Load() != beforeWrites {
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
			assertProjectionSafe(t, "安全日志", logs.String(), markers)
			// 同一status来源还可通过reason派生Runtime.Error，不能只保护YAML。
			f.mu.Lock()
			for _, object := range []map[string]any{cr, external} {
				object["status"].(map[string]any)["conditions"].([]any)[0].(map[string]any)["reason"] = malformed(markers[0])
			}
			f.mu.Unlock()
			for _, instance := range []string{id, outsideID} {
				runtime := call("POST", "/instances/"+instance+"/refresh", map[string]any{}, "reason-refresh-"+instance, 200)
				assertProjectionSafe(t, "reason派生HTTP运行", runtime, markers)
				if runtime["error"] != "[敏感内容已隐藏]" {
					t.Error("认证reason未明确派生安全隐藏标记")
				}
				assertProjectionSafe(t, "reason派生HTTP详情", call("GET", "/instances/"+instance, nil, "", 200), markers)
				direct, err := cli.RefreshInstance(context.Background(), ctx, &rpc.InstanceID{Id: instance})
				if err != nil {
					t.Fatal("reason派生直接RPC刷新失败")
				}
				assertProjectionSafe(t, "reason派生直接RPC运行", direct, markers)
				if direct.Error != "[敏感内容已隐藏]" {
					t.Error("直接RPC认证reason未明确返回安全隐藏标记")
				}
			}
			assertProjectionSafe(t, "reason派生HTTP发现", call("GET", "/clusters/"+cluster+"/instances", nil, "", 200), markers)
			directListing, err := cli.ListInstances(context.Background(), ctx, &rpc.ClusterID{Id: cluster})
			if err != nil {
				t.Fatal("reason派生直接RPC发现失败")
			}
			assertProjectionSafe(t, "reason派生直接RPC发现", directListing, markers)
			f.mu.Lock()
			for _, object := range []map[string]any{cr, external} {
				if object["status"].(map[string]any)["conditions"].([]any)[0].(map[string]any)["reason"] != malformed(markers[0]) || writes.Load() != beforeWrites {
					t.Error("运行投影修改原reason或写入目标")
				}
			}
			f.mu.Unlock()
			var oldRuntime []byte
			if db.QueryRow(`SELECT runtime FROM instance_observations WHERE instance_id=?`, id).Scan(&oldRuntime) != nil {
				t.Fatal("reason运行缓存不可读")
			}
			assertProjectionSafe(t, "reason派生运行缓存", string(oldRuntime), markers)
			assertProjectionSafe(t, "reason派生安全日志", logs.String(), markers)
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
			// 已存在的schema7可能保存原始状态文本；目标离线时首次读取也必须安全。
			cached := call("GET", "/instances/"+outsideID, nil, "", 200)
			rawOutside, _ := yaml.Marshal(external)
			cached["yaml"] = string(rawOutside)
			cached["runtime"].(map[string]any)["error"] = malformed(markers[0])
			legacy, _ := json.Marshal(cached)
			rawManaged, _ := json.Marshal(cr)
			var saved map[string]any
			if json.Unmarshal(oldRuntime, &saved) != nil {
				t.Fatal("旧运行缓存无法解码")
			}
			saved["runtime"].(map[string]any)["error"] = malformed(markers[0])
			legacyRuntime, _ := json.Marshal(saved)
			db.Close()
			if p.Close(context.Background()) != nil {
				t.Fatal("升级前平台关闭失败")
			}
			offline.Store(true)
			projectionSchema7Restart(t, dir, id, outsideID, cookie, legacy, rawManaged, legacyRuntime, markers, logger)
		})
	}
}

// projectionSchema7Restart 用真实旧cache验证数据迁移、失败原子性和离线首次HTTP/RPC响应。
func projectionSchema7Restart(t ginkgo.GinkgoTInterface, dir, id, outside string, cookie *http.Cookie, legacy, rawManaged, legacyRuntime []byte, markers []string, logger *slog.Logger) {
	t.Helper()
	db, err := sql.Open("sqlite", filepath.Join(dir, "platform.db"))
	if err != nil {
		t.Fatal("旧缓存证据库不可打开")
	}
	defer db.Close()
	if _, err = db.Exec(dropInstanceCreatorSchema + dropAgentStorageSchema + `DROP INDEX instance_activity_order;ALTER TABLE instances DROP COLUMN activity_sequence;DROP TABLE instance_activity_counter;DELETE FROM schema_migrations WHERE version>7`); err != nil {
		t.Fatal("schema7前置版本无法恢复")
	}
	if _, err = db.Exec(`UPDATE instance_observations SET cr=?,runtime=? WHERE instance_id=?`, rawManaged, legacyRuntime, id); err != nil {
		t.Fatal("旧观测缓存不可恢复")
	}
	if _, err = db.Exec(`UPDATE discovered_instances SET document='invalid-legacy-cache' WHERE id=?`, outside); err != nil {
		t.Fatal("迁移回滚前置不可恢复")
	}
	var effective, hmac string
	if db.QueryRow(`SELECT effective_spec FROM publication_versions WHERE instance_id=?`, id).Scan(&effective) != nil || db.QueryRow(`SELECT target_spec FROM publication_previews WHERE instance_id=? ORDER BY id LIMIT 1`, id).Scan(&hmac) != nil || !strings.HasPrefix(hmac, "hmac:v1:") {
		t.Fatal("升级前历史基线不可读")
	}
	cfg := platform.Config{DataDir: dir, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", Logger: logger}
	bad, err := platform.Start(context.Background(), cfg)
	if err == nil {
		bad.Close(context.Background())
		t.Fatal("损坏旧cache未阻止迁移")
	}
	assertProjectionSafe(t, "迁移失败错误", err.Error(), markers)
	var version int
	var rolledBack, rolledRuntime []byte
	if db.QueryRow(`SELECT max(version) FROM schema_migrations`).Scan(&version) != nil || db.QueryRow(`SELECT cr,runtime FROM instance_observations WHERE instance_id=?`, id).Scan(&rolledBack, &rolledRuntime) != nil || version != 7 || !bytes.Equal(rolledBack, rawManaged) || !bytes.Equal(rolledRuntime, legacyRuntime) {
		t.Fatal("cache修复失败未整体回滚")
	}
	backups, _ := filepath.Glob(filepath.Join(dir, "backups", "schema-7-to-*-*.db"))
	if len(backups) == 0 {
		t.Fatal("真实数据迁移缺少原库一致快照")
	}
	for _, backup := range backups {
		info, err := os.Stat(backup)
		if err != nil || info.Mode().Perm() != 0600 {
			t.Fatal("迁移快照权限不安全")
		}
		old, err := sql.Open("sqlite", "file:"+backup+"?mode=ro")
		if err != nil {
			t.Fatal("迁移快照不可读")
		}
		var oldCR, oldRuntime []byte
		var oldEffective, oldHMAC string
		valid := old.QueryRow(`SELECT max(version) FROM schema_migrations`).Scan(&version) == nil && version == 7 && old.QueryRow(`SELECT cr,runtime FROM instance_observations WHERE instance_id=?`, id).Scan(&oldCR, &oldRuntime) == nil && bytes.Equal(oldCR, rawManaged) && bytes.Equal(oldRuntime, legacyRuntime) && old.QueryRow(`SELECT effective_spec FROM publication_versions WHERE instance_id=?`, id).Scan(&oldEffective) == nil && old.QueryRow(`SELECT target_spec FROM publication_previews WHERE instance_id=? ORDER BY id LIMIT 1`, id).Scan(&oldHMAC) == nil && oldEffective == effective && oldHMAC == hmac
		old.Close()
		if !valid {
			t.Fatal("迁移快照与原库历史基线不一致")
		}
	}
	if _, err = db.Exec(`UPDATE discovered_instances SET document=? WHERE id=?`, legacy, outside); err != nil {
		t.Fatal("有效旧cache不可恢复")
	}
	p, err := platform.Start(context.Background(), cfg)
	if err != nil {
		t.Fatal("有效旧cache迁移失败")
	}
	defer p.Close(context.Background())
	first := projectionCall(t, p, "GET", "/instances/"+outside, nil, cookie, "", 200)
	assertProjectionSafe(t, "离线首次HTTP旧cache", first, markers)
	var document map[string]any
	if yaml.Unmarshal([]byte(first["yaml"].(string)), &document) != nil {
		t.Fatal("升级后旧YAML不可读")
	}
	condition := document["status"].(map[string]any)["conditions"].([]any)[0].(map[string]any)
	if condition["reason"] != "[敏感内容已隐藏]" || condition["message"] != "Waiting for workload readiness" || document["metadata"].(map[string]any)["annotations"].(map[string]any)["preserve"] != "original-metadata" {
		t.Fatal("旧cache修复隐藏普通诊断或用户metadata")
	}
	cli, err := controlservice.NewClient("projection-offline", client.WithHostPorts(p.RPCAddress()))
	if err != nil {
		t.Fatal("离线首次RPC client失败")
	}
	token := cookie.Value
	result, err := cli.GetInstance(context.Background(), &rpc.RequestContext{SessionToken: &token}, &rpc.InstanceID{Id: outside})
	if err != nil {
		t.Fatal("离线首次RPC旧cache读取失败")
	}
	assertProjectionSafe(t, "离线首次RPC旧cache", result, markers)
	var afterEffective, afterHMAC string
	if db.QueryRow(`SELECT max(version) FROM schema_migrations`).Scan(&version) != nil || version < 11 || db.QueryRow(`SELECT effective_spec FROM publication_versions WHERE instance_id=?`, id).Scan(&afterEffective) != nil || db.QueryRow(`SELECT target_spec FROM publication_previews WHERE instance_id=? ORDER BY id LIMIT 1`, id).Scan(&afterHMAC) != nil || effective != afterEffective || hmac != afterHMAC {
		t.Fatal("数据迁移改变历史effective_spec、HMAC或版本未提交")
	}
	for _, query := range []string{`SELECT cr FROM instance_observations`, `SELECT runtime FROM instance_observations`, `SELECT document FROM discovered_instances`} {
		rows, err := db.Query(query)
		if err != nil {
			t.Fatal("旧cache修复结果不可读")
		}
		for rows.Next() {
			var raw []byte
			if rows.Scan(&raw) != nil {
				t.Fatal("旧cache修复结果不可解码")
			}
			assertProjectionSafe(t, "升级后持久cache", string(raw), markers)
		}
		if rows.Err() != nil {
			t.Fatal("旧cache修复结果遍历失败")
		}
		rows.Close()
	}
}
