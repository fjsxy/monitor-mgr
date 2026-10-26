package platform_test

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/cloudwego/kitex/client"
	"github.com/fjsxy/monitor-mgr/internal/platform"
	"github.com/fjsxy/monitor-mgr/internal/teststeps"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"github.com/fjsxy/monitor-mgr/kitex_gen/control/controlservice"
	ginkgo "github.com/onsi/ginkgo/v2"
)

func ksmConfig(name string) map[string]any {
	return map[string]any{"name": name, "targetNamespace": "monitoring", "deploymentMode": "Deployment", "desiredState": "Running", "resources": map[string]any{"requestCPU": "100m", "requestMemory": "250Mi", "limitMemory": "500Mi"}, "collectors": map[string]any{"mode": "Default"}, "metricFilter": map[string]any{"mode": "All"}, "namespaceFilter": map[string]any{"mode": "All"}, "advancedResources": false}
}
func ksmCluster(t ginkgo.GinkgoTInterface) (*platform.Runtime, *http.Cookie, string) {
	t.Helper()
	_, cfg, _ := clusterAPI(t, 200, false)
	p, c, owner := clusterPlatform(t)
	op := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "ksm", "ownerId": owner, "context": "explicit", "kubeconfig": cfg}, c, "cluster", 202)
	o := awaitClusterOperation(t, p, c, op["operationId"].(string))
	if o["state"] != "SUCCEEDED" {
		t.Fatal(o)
	}
	return p, c, o["clusterId"].(string)
}

// 日志由HTTP与Kitex后台goroutine写入，断言读取同样同步，避免测试记录器自身竞态。
type ksmLogBuffer struct {
	mu     sync.Mutex
	buffer bytes.Buffer
}

func (b *ksmLogBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buffer.Write(p)
}
func (b *ksmLogBuffer) String() string { b.mu.Lock(); defer b.mu.Unlock(); return b.buffer.String() }

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestK10DraftNameAndRevision(t ginkgo.GinkgoTInterface) {
	// 接下来创建并更新 KSM 草稿，核验名称与 revision 并发保护。
	ginkgo.By("创建并更新 KSM 草稿，核验名称与 revision 并发保护")

	p, c, cluster := ksmCluster(t)
	for _, name := range []string{"", "中文", "invalid_foo", "-foo"} {
		clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig(name)}, c, "bad-"+name, 422)
	}
	created := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("first.ksm")}, c, "new", 201)
	id := created["id"].(string)
	if id == "first.ksm" {
		t.Fatal("平台ID不得使用CR名称")
	}
	draft := clusterCall(t, p, "GET", "/instances/"+id+"/draft", nil, c, "", 200)
	if draft["revision"] != float64(1) {
		t.Fatal(draft)
	}
	cfg := ksmConfig("first.ksm")
	cfg["resources"].(map[string]any)["requestCPU"] = "200m"
	body := map[string]any{"expectedDraftRevision": 1, "ksm": cfg}
	saved := clusterCall(t, p, "PUT", "/instances/"+id+"/draft", body, c, "save", 200)
	if saved["revision"] != float64(2) {
		t.Fatal(saved)
	}
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", body, c, "save-race", 409)
	copy := clusterCall(t, p, "GET", "/instances/"+id+"/copy-template", nil, c, "", 200)
	if copy["ksm"].(map[string]any)["name"] != "" {
		t.Fatal("复制不得复用CR身份")
	}
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestF04EncryptedSchema3WrongKeyDoesNotMigrate(t ginkgo.GinkgoTInterface) {
	// 接下来用错误密钥打开旧加密数据库，确认拒绝迁移。
	ginkgo.By("用错误密钥打开旧加密数据库，确认拒绝迁移")

	_, config, _ := clusterAPI(t, 200, false)
	dir := t.TempDir()
	cfg := platform.Config{DataDir: dir, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"}
	p, e := platform.Start(context.Background(), cfg)
	if e != nil {
		t.Fatal(e)
	}
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	owner := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"].(string)
	op := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "fixture", "ownerId": owner, "context": "explicit", "kubeconfig": config}, c, "register", 202)
	if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("前置凭据失败")
	}
	p.Close(context.Background())
	db, e := sql.Open("sqlite", filepath.Join(dir, "platform.db"))
	if e != nil {
		t.Fatal(e)
	}
	// 固定 schema3 旧库前置；测试裁去 schema4 及之后的新关系，不触碰既有密文。
	for _, q := range []string{dropAgentStorageSchema, "DROP TABLE IF EXISTS instance_activity_counter", "DROP TABLE IF EXISTS deletion_previews", "DROP TABLE IF EXISTS seen_resources", "DROP TABLE IF EXISTS deletion_operations", "DROP TABLE IF EXISTS operation_lifecycle", "DROP TABLE IF EXISTS instance_source_observations", "DROP TABLE discovered_instances", "DROP TABLE instance_observations", "DROP TABLE instance_protections", "DROP TABLE execution_attempts", "DROP TABLE publication_operations", "DROP TABLE publication_previews", "DROP TABLE publication_versions", "DROP TABLE drafts", "DROP TABLE instances", "DELETE FROM schema_migrations WHERE version>=4"} {
		if _, e = db.Exec(q); e != nil {
			t.Fatal(e)
		}
	}
	db.Close()
	before, _ := filepath.Glob(filepath.Join(dir, "backups", "*"))
	key, _ := os.ReadFile(filepath.Join(dir, "master.key"))
	wrong := append([]byte(nil), key...)
	wrong[0] ^= 1
	os.WriteFile(filepath.Join(dir, "master.key"), wrong, 0600)
	if bad, e := platform.Start(context.Background(), cfg); e == nil {
		bad.Close(context.Background())
		t.Fatal("错误密钥启动成功")
	}
	db, _ = sql.Open("sqlite", filepath.Join(dir, "platform.db"))
	defer db.Close()
	var version int
	db.QueryRow("SELECT max(version) FROM schema_migrations").Scan(&version)
	after, _ := filepath.Glob(filepath.Join(dir, "backups", "*"))
	if version != 3 || len(after) != len(before) {
		t.Fatalf("错误密钥先迁移了库：version=%d backups=%d→%d", version, len(before), len(after))
	}
	os.WriteFile(filepath.Join(dir, "master.key"), key, 0600)
	restored, e := platform.Start(context.Background(), cfg)
	if e != nil {
		t.Fatal(e)
	}
	defer restored.Close(context.Background())
	clusterCall(t, restored, "GET", "/clusters", nil, c, "", 200)
}

type ksmFixture struct {
	migrationNamespaces     []any
	migrationAgentPages     map[string]map[string]any
	migrationAgentFailToken string
	migrationAgents         []any
	migrationAgentFailure   bool
	legacySM                map[string]any
	crd                     map[string]any
	smReadFailure           bool
	smReads                 int
	smReadPaths             []string
	smWrites                int
	identityGate            chan struct{}
	identityEntered         chan struct{}
	identityOnce            sync.Once
	identityFailure         bool
	readDelay               time.Duration
	clusterUID              string
	targetConfiguration     string
	inventoryPages          map[string]map[string]any
	inventoryFailure        string
	mu                      sync.Mutex
	cr                      map[string]any
	writes                  int
	mutationRequests        int
	lastWrite               []byte
	rejectWrite             int
	dropResponse            bool
	owned                   map[string][]any
	deleteCount             int
	deleteRequests          int
	lastDelete              map[string]any
	retainCR                bool
	retainPod               bool
	crReadFailure           bool
	crReads                 int
	observationGate         chan struct{}
	observationEntered      chan struct{}
	observationCanceled     chan struct{}
	observationOnce         sync.Once
	readFailure             bool
	readFailureOnDelete     bool
	block                   chan struct{}
	entered                 chan struct{}
	readGate                chan struct{}
	readEntered             chan struct{}
	readOnce                sync.Once
}

func ksmTarget(t ginkgo.GinkgoTInterface) (*ksmFixture, string) {
	t.Helper()
	base, _, _ := clusterAPI(t, 200, false)
	f := &ksmFixture{crd: collectionCRDFixture(t)}
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if strings.HasPrefix(r.URL.Path, "/api/v1/namespaces") {
			f.mu.Lock()
			namespaces := f.migrationNamespaces
			f.mu.Unlock()
			if namespaces != nil {
				if r.URL.Path == "/api/v1/namespaces" {
					json.NewEncoder(w).Encode(map[string]any{"items": namespaces})
					return
				}
				for _, raw := range namespaces {
					ns := raw.(map[string]any)
					if r.URL.Path == "/api/v1/namespaces/"+ns["metadata"].(map[string]any)["name"].(string) {
						json.NewEncoder(w).Encode(ns)
						return
					}
				}
			}
		}
		if r.URL.Path == "/apis/monitoring.coreos.com/v1alpha1/prometheusagents" {
			f.mu.Lock()
			defer f.mu.Unlock()
			if f.migrationAgentFailure {
				w.WriteHeader(403)
				return
			}
			if f.migrationAgentPages != nil {
				token := r.URL.Query().Get("continue")
				if token != "" && token == f.migrationAgentFailToken {
					w.WriteHeader(403)
					return
				}
				json.NewEncoder(w).Encode(f.migrationAgentPages[token])
				return
			}
			items := f.migrationAgents
			if items == nil {
				items = []any{}
			}
			json.NewEncoder(w).Encode(map[string]any{"items": items})
			return
		}
		if r.URL.Path == "/apis/apiextensions.k8s.io/v1/customresourcedefinitions/kubestatemetrics.monitoring.fjsxy.github.io" {
			f.mu.Lock()
			defer f.mu.Unlock()
			json.NewEncoder(w).Encode(f.crd)
			return
		}
		if strings.Contains(r.URL.Path, "/servicemonitors") {
			f.mu.Lock()
			defer f.mu.Unlock()
			if r.Method != "GET" {
				f.smWrites++
				w.WriteHeader(500)
				return
			}
			if f.smReadFailure {
				w.WriteHeader(403)
				return
			}
			if strings.HasSuffix(r.URL.Path, "/servicemonitors") {
				items := []any{}
				if f.legacySM != nil {
					items = append(items, f.legacySM)
				}
				json.NewEncoder(w).Encode(map[string]any{"items": items})
				return
			}
			f.smReads++
			f.smReadPaths = append(f.smReadPaths, r.URL.Path)
			if f.legacySM == nil {
				w.WriteHeader(404)
				return
			}
			json.NewEncoder(w).Encode(f.legacySM)
			return
		}
		f.mu.Lock()
		delay := f.readDelay
		f.mu.Unlock()
		if r.Method == "GET" && delay > 0 {
			timer := time.NewTimer(delay)
			defer timer.Stop()
			select {
			case <-timer.C:
			case <-r.Context().Done():
				return
			}
		}
		if r.URL.Path == "/api/v1/namespaces/kube-system" {
			f.mu.Lock()
			if f.identityGate != nil {
				gate := f.identityGate
				f.identityOnce.Do(func() { close(f.identityEntered) })
				f.mu.Unlock()
				select {
				case <-gate:
				case <-r.Context().Done():
					return
				}
				f.mu.Lock()
			}
			if f.identityFailure {
				f.mu.Unlock()
				w.WriteHeader(403)
				io.WriteString(w, `{}`)
				return
			}
			uid := f.clusterUID
			f.mu.Unlock()
			if uid != "" {
				json.NewEncoder(w).Encode(map[string]any{"metadata": map[string]any{"uid": uid}})
				return
			}
		}
		if r.URL.Path == "/api/v1/namespaces/monitoring" {
			f.mu.Lock()
			gate, entered := f.readGate, f.readEntered
			f.mu.Unlock()
			if gate != nil {
				f.readOnce.Do(func() { close(entered) })
				select {
				case <-gate:
				case <-r.Context().Done():
					return
				}
			}
			io.WriteString(w, `{"metadata":{"name":"monitoring","uid":"namespace-uid"}}`)
			return
		}
		if (strings.HasPrefix(r.URL.Path, "/apis/apps/v1/") || strings.HasSuffix(r.URL.Path, "/pods")) && r.Method == "GET" {
			f.mu.Lock()
			defer f.mu.Unlock()
			if f.readFailure {
				w.WriteHeader(403)
				io.WriteString(w, `{}`)
				return
			}
			items := f.owned[r.URL.Path]
			if !strings.Contains(r.URL.Path, "/namespaces/") {
				items = nil
				for path, objects := range f.owned {
					if strings.HasSuffix(path, "/"+r.URL.Path[strings.LastIndex(r.URL.Path, "/")+1:]) {
						items = append(items, objects...)
					}
				}
			}
			if items == nil {
				items = []any{}
			}
			json.NewEncoder(w).Encode(map[string]any{"items": items})
			return
		}
		if !strings.HasPrefix(r.URL.Path, "/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics") {
			base.Config.Handler.ServeHTTP(w, r)
			return
		}
		f.mu.Lock()
		defer f.mu.Unlock()
		if r.Method == "GET" {
			if strings.HasSuffix(r.URL.Path, "kubestatemetrics") {
				if f.inventoryPages != nil {
					token := r.URL.Query().Get("continue")
					if f.inventoryFailure != "" && f.inventoryFailure == token {
						w.WriteHeader(403)
						io.WriteString(w, `{}`)
						return
					}
					json.NewEncoder(w).Encode(f.inventoryPages[token])
					return
				}

				items := []any{}
				if f.cr != nil {
					items = append(items, f.cr)
				}
				json.NewEncoder(w).Encode(map[string]any{"items": items})
				return
			}
			f.crReads++
			if f.observationGate != nil {
				gate := f.observationGate
				f.observationOnce.Do(func() { close(f.observationEntered) })
				f.mu.Unlock()
				select {
				case <-gate:
				case <-r.Context().Done():
					if f.observationCanceled != nil {
						close(f.observationCanceled)
					}
				}
				f.mu.Lock()
			}

			if f.crReadFailure {
				w.WriteHeader(403)
				io.WriteString(w, `{}`)
				return
			}
			if f.cr == nil {
				w.WriteHeader(404)
				io.WriteString(w, `{}`)
				return
			}
			json.NewEncoder(w).Encode(f.cr)
			return
		}
		// 记录进入外部 API 的全部组件写请求，包含被拒绝的请求。
		f.mutationRequests++
		if f.rejectWrite != 0 {
			w.WriteHeader(f.rejectWrite)
			io.WriteString(w, `{}`)
			return
		}
		var body map[string]any
		json.NewDecoder(r.Body).Decode(&body)
		if r.Method == "DELETE" {
			f.deleteRequests++
			if f.cr == nil {
				w.WriteHeader(404)
				return
			}
			meta := f.cr["metadata"].(map[string]any)
			pre := body["preconditions"].(map[string]any)
			if pre["uid"] != meta["uid"] || pre["resourceVersion"] != meta["resourceVersion"] || body["propagationPolicy"] != "Foreground" {
				w.WriteHeader(409)
				return
			}
			f.deleteCount++
			f.lastDelete = body
			if f.retainCR {
				meta["deletionTimestamp"] = "2026-10-18T00:00:00Z"
			} else {
				f.cr = nil
			}
			for path := range f.owned {
				if !f.retainPod || !strings.HasSuffix(path, "/pods") {
					f.owned[path] = nil
				}
			}
			if f.readFailureOnDelete {
				f.readFailure = true
			}
			if f.dropResponse {
				panic(http.ErrAbortHandler)
			}
			io.WriteString(w, `{"kind":"Status","status":"Success"}`)
			return
		}
		if r.Method == "POST" && f.cr != nil {
			w.WriteHeader(409)
			io.WriteString(w, `{}`)
			return
		}
		if r.Method == "PUT" {
			if f.cr == nil {
				w.WriteHeader(404)
				return
			}
			old := f.cr["metadata"].(map[string]any)
			m := body["metadata"].(map[string]any)
			if m["uid"] != old["uid"] || m["resourceVersion"] != old["resourceVersion"] {
				w.WriteHeader(409)
				return
			}
		}
		// 仅在外部TLS目标边界保存原请求快照，早于模拟API补UID/RV响应。
		f.lastWrite, _ = json.Marshal(body)
		f.writes++
		meta := body["metadata"].(map[string]any)
		meta["uid"] = "created-uid"
		meta["resourceVersion"] = fmt.Sprint(f.writes)
		meta["generation"] = float64(f.writes)
		f.cr = body
		if f.block != nil {
			close(f.entered)
			b := f.block
			f.mu.Unlock()
			<-b
			f.mu.Lock()
		}
		if f.dropResponse {
			panic(http.ErrAbortHandler)
		}
		json.NewEncoder(w).Encode(body)
	}))
	t.Cleanup(srv.Close)
	pem := base64.StdEncoding.EncodeToString([]byte(pemCert(srv.Certificate().Raw)))
	f.targetConfiguration = `{"clusters":[{"name":"t","cluster":{"server":"` + srv.URL + `","certificate-authority-data":"` + pem + `"}}],"users":[{"name":"u","user":{"token":"fixture"}}],"contexts":[{"name":"explicit","context":{"cluster":"t","user":"u"}}]}`
	return f, f.targetConfiguration
}
func ksmPublishedFixture(t ginkgo.GinkgoTInterface) (*platform.Runtime, *http.Cookie, string, *ksmFixture) {
	t.Helper()
	f, cfg := ksmTarget(t)
	p, c, owner := clusterPlatform(t)
	o := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "fixture", "ownerId": owner, "context": "explicit", "kubeconfig": cfg}, c, "reg", 202)
	op := awaitClusterOperation(t, p, c, o["operationId"].(string))
	if op["state"] != "SUCCEEDED" {
		t.Fatal(op)
	}
	created := clusterCall(t, p, "POST", "/clusters/"+op["clusterId"].(string)+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("instance")}, c, "new", 201)
	return p, c, created["id"].(string), f
}
func ksmPreview(t ginkgo.GinkgoTInterface, p *platform.Runtime, c *http.Cookie, id string, rev int) map[string]any {
	return clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": rev}, c, "preview", 200)
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestP02PublicationFreezesSourceAndP03Idempotency(t ginkgo.GinkgoTInterface) {
	// 接下来发布草稿后继续编辑并重试，核验冻结版本与幂等。
	ginkgo.By("发布草稿后继续编辑并重试，核验冻结版本与幂等")

	p, c, id, f := ksmPublishedFixture(t)
	preview := ksmPreview(t, p, c, id, 1)
	body := map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}
	f.mu.Lock()
	f.block = make(chan struct{})
	f.entered = make(chan struct{})
	f.mu.Unlock()
	op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", body, c, "publish", 202)
	select {
	case <-f.entered:
	case <-time.After(3 * time.Second):
		t.Fatal("没有真实写请求")
	}
	cfg := ksmConfig("instance")
	cfg["resources"].(map[string]any)["requestCPU"] = "300m"
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": cfg}, c, "save-next", 200)
	replay := clusterCall(t, p, "POST", "/instances/"+id+"/publications", body, c, "publish", 202)
	if replay["operationId"] != op["operationId"] {
		t.Fatal("重复受理")
	}
	clusterCall(t, p, "POST", "/instances/"+id+"/publications", body, c, "another", 409)
	instance := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
	if instance["currentVersionId"] != "" {
		t.Fatal("确认前提前提升版本")
	}
	close(f.block)
	done := awaitClusterOperation(t, p, c, op["operationId"].(string))
	if done["state"] != "SUCCEEDED" {
		t.Fatal(done)
	}
	v := clusterCall(t, p, "GET", "/instances/"+id+"/versions/"+op["versionId"].(string), nil, c, "", 200)
	if v["sourceDraftRevision"] != float64(1) || v["ksm"].(map[string]any)["resources"].(map[string]any)["requestCPU"] != "100m" {
		t.Fatal(v)
	}
	history := clusterCall(t, p, "GET", "/operations?instanceId="+id, nil, c, "", 200)["operations"].([]any)
	if len(history) != 1 {
		t.Fatal("实例历史未返回固定操作", history)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.writes != 1 {
		t.Fatal(f.writes)
	}
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestExternalKSMReadOnlyAndFailedList(t ginkgo.GinkgoTInterface) {
	// 接下来发现外部 KSM 并注入列表失败，核验只读与错误边界。
	ginkgo.By("发现外部 KSM 并注入列表失败，核验只读与错误边界")

	p, c, id, f := ksmPublishedFixture(t)
	managed := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
	cluster := managed["clusterId"].(string)
	f.mu.Lock()
	f.cr = map[string]any{"apiVersion": "monitoring.fjsxy.github.io/v1alpha1", "kind": "KubeStateMetrics", "metadata": map[string]any{"name": "outside", "uid": "external-uid", "generation": 1}, "spec": map[string]any{"targetNamespace": "monitoring", "enabled": true}}
	f.mu.Unlock()
	list := clusterCall(t, p, "GET", "/clusters/"+cluster+"/instances", nil, c, "", 200)["instances"].([]any)
	if len(list) != 2 {
		t.Fatalf("未发现外部实例：%v", list)
	}
	external := list[1].(map[string]any)
	if external["readOnly"] != true || external["ownership"] != "unmanaged" {
		t.Fatal(external)
	}
	ext := external["id"].(string)
	// I26-06：管理员也不能通过历史权限确认接管外部实例。
	clusterCall(t, p, "POST", "/instances/"+ext+"/management-confirmations", map[string]any{"allowClusterManagers": true}, c, "external-confirm", 403)
	detail := clusterCall(t, p, "GET", "/instances/"+ext, nil, c, "", 200)
	if !strings.Contains(detail["yaml"].(string), "external-uid") {
		t.Fatal("只读YAML丢失")
	}
	clusterCall(t, p, "PUT", "/instances/"+ext+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": ksmConfig("outside")}, c, "save", 403)
	clusterCall(t, p, "POST", "/instances/"+ext+"/previews", map[string]any{"draftRevision": 1}, c, "preview", 403)
	clusterCall(t, p, "DELETE", "/instances/"+ext, nil, c, "delete", 403)
	if len(clusterCall(t, p, "GET", "/instances/"+ext+"/versions", nil, c, "", 200)["versions"].([]any)) != 0 {
		t.Fatal("外部实例伪造发布历史")
	}
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestP04DraftAndTargetPreviewRaces(t ginkgo.GinkgoTInterface) {
	// 接下来预览后分别修改草稿与目标，确认旧预览不能发布。
	ginkgo.By("预览后分别修改草稿与目标，确认旧预览不能发布")

	p, c, id, f := ksmPublishedFixture(t)
	old := ksmPreview(t, p, c, id, 1)
	cfg := ksmConfig("instance")
	cfg["resources"].(map[string]any)["requestCPU"] = "150m"
	clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": cfg}, c, "edit", 200)
	clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": old["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "stale", 409)
	preview := ksmPreview(t, p, c, id, 2)
	body := map[string]any{"previewId": preview["previewId"], "draftRevision": 2, "overwriteDrift": false}
	op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", body, c, "publish", 202)
	if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("publish failed")
	}
	stale := ksmPreview(t, p, c, id, 2)
	f.mu.Lock()
	f.cr["spec"].(map[string]any)["suspended"] = true
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": stale["previewId"], "draftRevision": 2, "overwriteDrift": true}, c, "target-race", 409)
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestP05DriftOverwritePreservesMetadataAndP06Equivalent(t ginkgo.GinkgoTInterface) {
	// 接下来覆盖配置漂移并比较等价配置，核验元数据保留。
	ginkgo.By("覆盖配置漂移并比较等价配置，核验元数据保留")

	p, c, id, f := ksmPublishedFixture(t)
	p1 := ksmPreview(t, p, c, id, 1)
	b := map[string]any{"previewId": p1["previewId"], "draftRevision": 1, "overwriteDrift": false}
	o := clusterCall(t, p, "POST", "/instances/"+id+"/publications", b, c, "first", 202)
	if awaitClusterOperation(t, p, c, o["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("first failed")
	}
	f.mu.Lock()
	m := f.cr["metadata"].(map[string]any)
	m["labels"] = map[string]any{"external": "keep"}
	m["annotations"] = map[string]any{"external-note": "keep"}
	m["finalizers"] = []any{"external/finalizer"}
	f.cr["status"] = map[string]any{"arbitrary": "keep"}
	f.cr["spec"].(map[string]any)["unsupportedExtra"] = map[string]any{"value": "remove"}
	f.cr["spec"].(map[string]any)["enabled"] = false
	beforePreview, _ := json.Marshal(f.cr)
	f.mu.Unlock()
	drift := ksmPreview(t, p, c, id, 1)
	if drift["drift"] != true || !strings.Contains(drift["leftYAML"].(string), "unsupportedExtra") {
		t.Fatal("漂移未完整显示")
	}
	// REG17-YAML-PRESERVE.ksm-existing：确认diff必须保留实际PUT仍保留的全部非spec字段。
	right := publicationYAMLDocument(t, drift["rightYAML"].(string))
	assertPublicationNonSpecPreserved(t, right, publicationJSONDocument(t, beforePreview))
	if right["spec"].(map[string]any)["unsupportedExtra"] != nil || right["spec"].(map[string]any)["enabled"] != true {
		t.Fatal("右侧未显示覆盖后的完整受支持spec")
	}
	f.mu.Lock()
	afterPreview, _ := json.Marshal(f.cr)
	f.mu.Unlock()
	if string(afterPreview) != string(beforePreview) {
		t.Fatal("预览意外改写原目标CR")
	}
	b["previewId"] = drift["previewId"]
	clusterCall(t, p, "POST", "/instances/"+id+"/publications", b, c, "normal", 409)
	b["overwriteDrift"] = true
	o = clusterCall(t, p, "POST", "/instances/"+id+"/publications", b, c, "overwrite", 202)
	if awaitClusterOperation(t, p, c, o["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("overwrite failed")
	}
	f.mu.Lock()
	sent := append([]byte(nil), f.lastWrite...)
	f.mu.Unlock()
	assertPublicationDocumentEqual(t, right, publicationJSONDocument(t, sent))
	f.mu.Lock()
	if f.cr["spec"].(map[string]any)["unsupportedExtra"] != nil || f.cr["metadata"].(map[string]any)["labels"].(map[string]any)["external"] != "keep" || f.cr["status"].(map[string]any)["arbitrary"] != "keep" || len(f.cr["metadata"].(map[string]any)["finalizers"].([]any)) != 1 {
		t.Fatal("覆盖破坏非平台内容")
	}
	f.mu.Unlock()
	equivalent := ksmPreview(t, p, c, id, 1)
	f.mu.Lock()
	delete(f.cr["spec"].(map[string]any), "suspended")
	f.cr["metadata"].(map[string]any)["resourceVersion"] = "rv-only"
	f.cr["status"] = map[string]any{"statusOnly": "changed"}
	f.mu.Unlock()
	b["previewId"] = equivalent["previewId"]
	b["overwriteDrift"] = false
	o = clusterCall(t, p, "POST", "/instances/"+id+"/publications", b, c, "equiv", 202)
	if awaitClusterOperation(t, p, c, o["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("等价默认值/status/RV错误冲突")
	}
	cli, e := controlservice.NewClient("fixed-target-direct", client.WithHostPorts(p.RPCAddress()))
	if e != nil {
		t.Fatal(e)
	}
	directKey := "fixed-target"
	token := c.Value
	for _, field := range []string{"name", "targetNamespace"} {
		cfg := ksmConfig("instance")
		cfg[field] = "other"
		clusterCall(t, p, "PUT", "/instances/"+id+"/draft", map[string]any{"expectedDraftRevision": 1, "ksm": cfg}, c, "fixed-"+field, 409)
		raw, _ := json.Marshal(cfg)
		var typed rpc.KSMConfiguration
		json.Unmarshal(raw, &typed)
		_, e := cli.SaveDraft(context.Background(), &rpc.RequestContext{RequestId: "fixed-target", SessionToken: &token, IdempotencyKey: &directKey}, &rpc.SaveDraftRequest{Id: id, ExpectedDraftRevision: 1, Ksm: &typed})
		var api *rpc.APIError
		if !errors.As(e, &api) || api.Code != "CONFLICT" {
			t.Fatal("RPC允许修改固定身份", e)
		}
	}
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestF05PublicationCrashBoundaries(t ginkgo.GinkgoTInterface) {
	// 接下来在事务提交前后模拟崩溃，核验发布任务恢复边界。
	ginkgo.By("在事务提交前后模拟崩溃，核验发布任务恢复边界")

	if stage := os.Getenv("MM_F05_STAGE"); stage != "" {
		p, e := platform.Start(context.Background(), platform.Config{DataDir: os.Getenv("MM_F05_DIR"), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", OperationBoundary: func(s, id string) error {
			if s == stage {
				os.Exit(97)
			}
			return nil
		}})
		if e != nil {
			t.Fatal(e)
		}
		c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
		clusterCall(t, p, "POST", "/instances/"+os.Getenv("MM_F05_INSTANCE")+"/publications", map[string]any{"previewId": os.Getenv("MM_F05_PREVIEW"), "draftRevision": 1, "overwriteDrift": false}, c, "crash-key", 202)
		t.Fatal("未触发崩溃边界")
		return
	}
	for _, stage := range []string{"beforeAcceptanceCommit", "afterAcceptanceCommit"} {
		teststeps.Step(stage, func() {
			f, target := ksmTarget(t)
			dir := t.TempDir()
			cfg := platform.Config{DataDir: dir, HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0"}
			p, e := platform.Start(context.Background(), cfg)
			if e != nil {
				t.Fatal(e)
			}
			request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
			c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
			owner := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"].(string)
			op := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "fixture", "ownerId": owner, "context": "explicit", "kubeconfig": target}, c, "register", 202)
			cl := awaitClusterOperation(t, p, c, op["operationId"].(string))["clusterId"].(string)
			i := clusterCall(t, p, "POST", "/clusters/"+cl+"/instances", map[string]any{"component": "kube-state-metrics", "ksm": ksmConfig("instance")}, c, "create", 201)
			id := i["id"].(string)
			preview := ksmPreview(t, p, c, id, 1)
			p.Close(context.Background())
			cmd := exec.Command(os.Args[0], "-test.run=^TestPlatform$", "-ginkgo.focus=(^| )UT-09 ", "-ginkgo.fail-on-empty")
			cmd.Env = append(os.Environ(), "MM_F05_STAGE="+stage, "MM_F05_DIR="+dir, "MM_F05_INSTANCE="+id, "MM_F05_PREVIEW="+preview["previewId"].(string))
			raw, e := cmd.CombinedOutput()
			var exited *exec.ExitError
			if !errors.As(e, &exited) || exited.ExitCode() != 97 {
				t.Fatalf("没有在正确边界退出：%v %s", e, raw)
			}
			reopened, e := platform.Start(context.Background(), cfg)
			if e != nil {
				t.Fatal(e)
			}
			defer reopened.Close(context.Background())
			found := clusterCall(t, reopened, "GET", "/operations?instanceId="+id+"&idempotencyKey=crash-key", nil, c, "", 200)["operations"].([]any)
			versions := clusterCall(t, reopened, "GET", "/instances/"+id+"/versions", nil, c, "", 200)["versions"].([]any)
			f.mu.Lock()
			writes := f.writes
			f.mu.Unlock()
			if writes != 0 {
				t.Fatal("重启自动重放")
			}
			count := 0
			if stage == "afterAcceptanceCommit" {
				count = 1
			}
			if len(found) != count || len(versions) != count {
				t.Fatalf("受理关系不完整 %d/%d want%d", len(found), len(versions), count)
			}
			if count == 1 {
				old := found[0].(map[string]any)
				if old["state"] != "WAITING_CONFIRMATION" {
					t.Fatal(old)
				}
				replay := clusterCall(t, reopened, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "crash-key", 202)
				if replay["operationId"] != old["operationId"] {
					t.Fatal("崩溃后重复受理")
				}
				attempts := clusterCall(t, reopened, "GET", "/operations/"+old["operationId"].(string)+"/attempts", nil, c, "", 200)["attempts"].([]any)
				if len(attempts) != 1 || attempts[0].(map[string]any)["possiblySent"] != false {
					t.Fatal(attempts)
				}
				request(t, reopened, "POST", "/operations/"+old["operationId"].(string)+"/continue", `{}`, c, 409)
			}
		})
	}
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestR03KSMEffectiveZeroValuesAndMissingStatus(t ginkgo.GinkgoTInterface) {
	// 接下来分别构造 KSM 零副本、停用和无匹配状态，核验运行摘要。
	ginkgo.By("分别构造 KSM 零副本、停用和无匹配状态，核验运行摘要")

	p, c, id, f := ksmPublishedFixture(t)
	preview := ksmPreview(t, p, c, id, 1)
	o := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"draftRevision": 1, "previewId": preview["previewId"], "overwriteDrift": false}, c, "publish", 202)
	awaitClusterOperation(t, p, c, o["operationId"].(string))
	unknown := clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, c, "refresh", 200)
	if unknown["summary"] != "Unknown" || unknown["desiredPods"] != nil {
		t.Fatal(unknown)
	}
	for _, tc := range []struct{ name, desired, reason, phase, ready, summary string }{{"ksm-suspended", "Suspended", "Suspended", "Ready", "True", "Suspended"}, {"ksm-disabled", "Disabled", "Disabled", "Disabled", "False", "Disabled"}, {"ksm-no-match", "Running", "NoNamespaceMatched", "Ready", "True", "NoMatch"}} {
		teststeps.Step(tc.name, func() {
			f.mu.Lock()
			spec := f.cr["spec"].(map[string]any)
			spec["enabled"] = tc.desired != "Disabled"
			spec["suspended"] = tc.desired == "Suspended"
			f.cr["status"] = map[string]any{"observedGeneration": float64(1), "phase": tc.phase, "deploymentMode": "Deployment", "conditions": []any{map[string]any{"type": "Ready", "status": tc.ready, "reason": tc.reason, "observedGeneration": float64(1)}}}
			f.mu.Unlock()
			got := clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, c, "refresh-"+tc.name, 200)
			if got["summary"] != tc.summary || got["availablePods"] != float64(0) || got["desiredPods"] != float64(0) {
				t.Fatal(got)
			}
			f.mu.Lock()
			f.cr["status"].(map[string]any)["observedGeneration"] = float64(0)
			f.mu.Unlock()
			old := clusterCall(t, p, "POST", "/instances/"+id+"/refresh", nil, c, "stale-"+tc.name, 200)
			if old["summary"] != "Unknown" || old["desiredPods"] != nil {
				t.Fatal("旧generation误当当前", old)
			}
		})
	}
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestF02KSMHTTPHasRealRPCAndCannotBypassUnavailableRPC(t ginkgo.GinkgoTInterface) {
	// 接下来关闭真实 RPC 传输，确认 HTTP 无法绕过 RPC 执行业务。
	ginkgo.By("关闭真实 RPC 传输，确认 HTTP 无法绕过 RPC 执行业务")

	d := new(switchableDialer)
	var logs ksmLogBuffer
	p, e := platform.Start(context.Background(), platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", RPCDialer: d, Logger: slog.New(slog.NewJSONHandler(&logs, nil))})
	if e != nil {
		t.Fatal(e)
	}
	defer p.Close(context.Background())
	request(t, p, "POST", "/initialization", `{"username":"admin","password":"first-password-123"}`, nil, 201)
	c := request(t, p, "POST", "/sessions", `{"username":"admin","password":"first-password-123"}`, nil, 201).Cookies()[0]
	req, _ := http.NewRequest("GET", p.URL()+"/api/v1/clusters", nil)
	req.AddCookie(c)
	res, e := http.DefaultClient.Do(req)
	if e != nil {
		t.Fatal(e)
	}
	io.Copy(io.Discard, res.Body)
	res.Body.Close()
	id := res.Header.Get("X-Request-Id")
	if id == "" || strings.Count(logs.String(), `"requestId":"`+id+`"`) != 2 || !strings.Contains(logs.String(), "kitex-server") {
		t.Fatal("没有同requestId的HTTP/Kitex证据")
	}
	d.breakConnections()
	request(t, p, "GET", "/clusters", "", c, 503)
	request(t, p, "POST", "/clusters/forged-cluster/instances", `{"component":"kube-state-metrics","ksm":{}}`, c, 503)
}

// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestPublicationPreflightDoesNotBlockCurrentAccountRevocation(t ginkgo.GinkgoTInterface) {
	// 接下来在发布预检期间撤销账号权限，核验授权不被阻塞。
	ginkgo.By("在发布预检期间撤销账号权限，核验授权不被阻塞")

	p, c, id, f := ksmPublishedFixture(t)
	actor := readJSON(t, p, "/session", c)["account"].(map[string]any)["id"].(string)
	clusterCall(t, p, "POST", "/accounts", map[string]any{"username": "other-admin", "password": "temporary-password", "role": "ADMIN"}, c, "other-admin", 201)
	other := request(t, p, "POST", "/sessions", `{"username":"other-admin","password":"temporary-password"}`, nil, 201).Cookies()[0]
	request(t, p, "POST", "/session/password", `{"currentPassword":"temporary-password","newPassword":"personal-password"}`, other, 200)
	other = request(t, p, "POST", "/sessions", `{"username":"other-admin","password":"personal-password"}`, nil, 201).Cookies()[0]
	preview := ksmPreview(t, p, c, id, 1)
	f.mu.Lock()
	f.readGate = make(chan struct{})
	f.readEntered = make(chan struct{})
	f.mu.Unlock()
	op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "publish", 202)
	select {
	case <-f.readEntered:
	case <-time.After(3 * time.Second):
		t.Fatal("未进入预检读取")
	}
	result := make(chan int, 1)
	go func() {
		defer ginkgo.GinkgoRecover()
		req, _ := http.NewRequest("PATCH", p.URL()+"/api/v1/accounts/"+actor, strings.NewReader(`{"enabled":false}`))
		req.AddCookie(other)
		req.Header.Set("Origin", p.URL())
		req.Header.Set("Idempotency-Key", "revoke-current-actor")
		resp, e := http.DefaultClient.Do(req)
		if e != nil {
			result <- 0
			return
		}
		io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
		result <- resp.StatusCode
	}()
	timely := true
	select {
	case status := <-result:
		if status != 200 {
			t.Error(status)
		}
	case <-time.After(2 * time.Second):
		timely = false
		t.Error("只读预检持有派发保护，阻止当前账号及时撤权")
	}
	close(f.readGate)
	if !timely {
		<-result
	}
	done := awaitClusterOperation(t, p, other, op["operationId"].(string))
	if timely && done["state"] != "FAILED" {
		t.Fatal("撤权后仍派发", done)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if timely && f.writes != 0 {
		t.Fatal("撤权后仍产生KSM写")
	}
}
