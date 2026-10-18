package platform_test

import (
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"
)

func agentConfig(name, namespace string) map[string]any {
	rule := func() map[string]any {
		return map[string]any{"object": map[string]any{"mode": "None"}, "namespace": map[string]any{"mode": "Own"}}
	}
	return map[string]any{"name": name, "namespace": namespace, "desiredState": "Running", "shards": 1, "resources": map[string]any{"requestCPU": "200m", "requestMemory": "512Mi", "limitMemory": "1Gi"}, "storage": map[string]any{"mode": "PVC", "capacity": "1Gi", "whenScaled": "Retain", "whenDeleted": "Retain"}, "serviceAccountName": "prometheus-agent", "associations": map[string]any{"serviceMonitor": rule(), "podMonitor": rule(), "probe": rule(), "scrapeConfig": rule()}, "remoteWrite": []any{map[string]any{"url": "http://receiver.example.invalid/write", "auth": map[string]any{"mode": "None"}}}}
}

// A03/F01：保存typed Agent只创建平台草稿，跨namespace同名保持独立。
func TestAgentTypedDraftIdentityAndDefaults(t *testing.T) {
	p, c, cluster := ksmCluster(t)
	var ids []string
	for _, ns := range []string{"one", "two"} {
		i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": agentConfig("same-name", ns)}, c, "create-"+ns, 201)
		id := i["id"].(string)
		ids = append(ids, id)
		if i["type"] != "collector" || i["crNamespace"] != ns || i["scope"] != "Namespaced" {
			t.Fatal("Agent身份读模型错误")
		}
		d := clusterCall(t, p, "GET", "/instances/"+id+"/draft", nil, c, "", 200)
		a := d["agent"].(map[string]any)
		if a["namespace"] != ns || a["shards"] != float64(1) {
			t.Fatal("类型化草稿丢字段")
		}
	}
	if ids[0] == ids[1] {
		t.Fatal("跨namespace共享实例ID")
	}
	clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": agentConfig("same-name", "one")}, c, "duplicate", 409)
	copy := clusterCall(t, p, "GET", "/instances/"+ids[0]+"/copy-template", nil, c, "", 200)
	if copy["agent"].(map[string]any)["name"] != "" {
		t.Fatal("复制复用原身份")
	}
}

// A01/A04/F01：默认Agent发布仍经过同一真实Kitex/版本/attempt/worker链，四selector省略不能丢remoteWrite。
func TestAgentDefaultPublicationWithDependencies(t *testing.T) {
	f, target := agentTarget(t)
	p, c, owner := clusterPlatform(t)
	op := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "agent", "ownerId": owner, "context": "explicit", "kubeconfig": target}, c, "register", 202)
	cluster := awaitClusterOperation(t, p, c, op["operationId"].(string))["clusterId"].(string)
	i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": agentConfig("collector", "monitoring")}, c, "create", 201)
	id := i["id"].(string)
	preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "preview", 200)
	if !strings.Contains(preview["rightYAML"].(string), "v2.55.1") {
		t.Fatal("平台版本预设未显式输出")
	}
	op = clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "publish", 202)
	if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("Agent默认发布失败")
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	cr := f.objects["monitoring/collector"]
	if cr == nil {
		t.Fatal("未发到Agent namespacecollection")
	}
	spec := cr["spec"].(map[string]any)
	for _, key := range []string{"serviceMonitorSelector", "podMonitorSelector", "probeSelector", "scrapeConfigSelector"} {
		if _, exists := spec[key]; exists {
			t.Fatal("None被映射成空selector全选")
		}
	}
	if len(spec["remoteWrite"].([]any)) != 1 || spec["version"] != "v2.55.1" || spec["replicas"] != float64(1) || spec["paused"] != false {
		t.Fatal("默认Agent配置错误")
	}
	if !strings.Contains(preview["rightYAML"].(string), "1Gi") {
		t.Fatal("默认PVC容量未映射")
	}
}

type agentFixture struct {
	mu      sync.Mutex
	objects map[string]map[string]any
	hook    func(http.ResponseWriter, *http.Request) bool
}

func agentTarget(t *testing.T) (*agentFixture, string) {
	t.Helper()
	base, _, _ := clusterAPI(t, 200, false)
	f := &agentFixture{objects: map[string]map[string]any{}}
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		f.mu.Lock()
		hook := f.hook
		f.mu.Unlock()
		if hook != nil && hook(w, r) {
			return
		}
		switch {
		case r.URL.Path == "/api/v1/namespaces/monitoring":
			io.WriteString(w, `{"metadata":{"name":"monitoring","uid":"ns-uid"}}`)
		case r.URL.Path == "/api/v1/namespaces/monitoring/serviceaccounts/prometheus-agent":
			io.WriteString(w, `{"metadata":{"name":"prometheus-agent","uid":"sa-uid"}}`)
		case strings.HasPrefix(r.URL.Path, "/api/v1/namespaces/") && strings.Contains(r.URL.Path, "/persistentvolumeclaims/"):
			w.WriteHeader(404)
			io.WriteString(w, `{}`)
		case r.URL.Path == "/apis/storage.k8s.io/v1/storageclasses":
			io.WriteString(w, `{"items":[{"metadata":{"name":"standard","annotations":{"storageclass.kubernetes.io/is-default-class":"true"}},"provisioner":"fixture.io/dynamic"}]}`)
		case r.URL.Path == "/apis/apps/v1/deployments":
			io.WriteString(w, `{"items":[{"metadata":{"name":"prometheus-operator","namespace":"operators","uid":"operator-uid","labels":{"app.kubernetes.io/version":"0.83.0"}},"spec":{"template":{"spec":{"serviceAccountName":"prometheus-operator","containers":[{"name":"prometheus-operator","image":"quay.io/prometheus-operator/prometheus-operator:v0.83.0","args":[]}]}}}}]}`)
		case r.URL.Path == "/apis/apps/v1/namespaces/operators/replicasets":
			io.WriteString(w, `{"items":[{"metadata":{"name":"operator-rs","uid":"operator-rs-uid","ownerReferences":[{"kind":"Deployment","uid":"operator-uid","controller":true}]}}]}`)
		case r.URL.Path == "/api/v1/namespaces/operators/pods":
			io.WriteString(w, `{"items":[{"metadata":{"name":"operator-pod","uid":"operator-pod-uid","ownerReferences":[{"kind":"ReplicaSet","uid":"operator-rs-uid","controller":true}]},"spec":{"serviceAccountName":"prometheus-operator","containers":[{"name":"prometheus-operator","image":"quay.io/prometheus-operator/prometheus-operator:v0.83.0","args":[]}]},"status":{"phase":"Running","containerStatuses":[{"name":"prometheus-operator","ready":true,"containerID":"containerd://operator-instance"}]}}]}`)
		case r.URL.Path == "/api/v1/namespaces/operators/pods/operator-pod":
			io.WriteString(w, `{"metadata":{"name":"operator-pod","uid":"operator-pod-uid"},"status":{"containerStatuses":[{"name":"prometheus-operator","ready":true,"containerID":"containerd://operator-instance"}]}}`)
		case strings.HasSuffix(r.URL.Path, "/proxy/metrics"):
			io.WriteString(w, "prometheus_operator_build_info{version=\"0.83.0\",revision=\"5cf2f5d\"} 1\nprometheus_operator_ready{controller=\"prometheus-agent\"} 1\nprometheus_operator_kubernetes_client_http_request_duration_seconds_count{endpoint=\"/apis/monitoring.coreos.com/v1alpha1/scrapeconfigs\"} 1\n")
		case r.URL.Path == "/apis/authorization.k8s.io/v1/subjectaccessreviews":
			io.WriteString(w, `{"status":{"allowed":true}}`)
		case r.URL.Path == "/apis/monitoring.coreos.com/v1alpha1/prometheusagents":
			f.mu.Lock()
			items := []any{}
			for _, cr := range f.objects {
				items = append(items, cr)
			}
			f.mu.Unlock()
			json.NewEncoder(w).Encode(map[string]any{"items": items})
		case strings.Contains(r.URL.Path, "/prometheusagents") && strings.Contains(r.URL.Path, "/namespaces/"):
			parts := strings.Split(r.URL.Path, "/")
			ns := parts[5]
			f.mu.Lock()
			defer f.mu.Unlock()
			if len(parts) == 7 {
				if r.Method == "GET" {
					json.NewEncoder(w).Encode(map[string]any{"items": []any{}})
					return
				}
				var body map[string]any
				json.NewDecoder(r.Body).Decode(&body)
				m := body["metadata"].(map[string]any)
				key := ns + "/" + m["name"].(string)
				if f.objects[key] != nil {
					w.WriteHeader(409)
					return
				}
				m["uid"] = "uid-" + key
				m["resourceVersion"] = "1"
				m["generation"] = 1
				// 固定v0.83.0 CRD在真实API默认这两项；响应确认必须识别等价默认值。
				spec := body["spec"].(map[string]any)
				if _, ok := spec["portName"]; !ok {
					spec["portName"] = "web"
				}
				if _, ok := spec["scrapeInterval"]; !ok {
					spec["scrapeInterval"] = "30s"
				}
				body["status"] = map[string]any{"replicas": 1, "availableReplicas": 1, "conditions": []any{map[string]any{"type": "Available", "status": "True", "observedGeneration": 1}}}
				f.objects[key] = body
				json.NewEncoder(w).Encode(body)
				return
			}
			key := ns + "/" + parts[7]
			if f.objects[key] == nil {
				w.WriteHeader(404)
				io.WriteString(w, `{}`)
				return
			}
			if r.Method == "PUT" {
				var body map[string]any
				json.NewDecoder(r.Body).Decode(&body)
				meta := body["metadata"].(map[string]any)
				old := f.objects[key]["metadata"].(map[string]any)
				if meta["uid"] != old["uid"] || meta["resourceVersion"] != old["resourceVersion"] {
					w.WriteHeader(409)
					io.WriteString(w, `{}`)
					return
				}
				rv, _ := strconv.Atoi(old["resourceVersion"].(string))
				meta["resourceVersion"] = strconv.Itoa(rv + 1)
				f.objects[key] = body
			}
			json.NewEncoder(w).Encode(f.objects[key])
		default:
			base.Config.Handler.ServeHTTP(w, r)
		}
	}))
	t.Cleanup(srv.Close)
	pem := base64.StdEncoding.EncodeToString([]byte(pemCert(srv.Certificate().Raw)))
	target := `{"apiVersion":"v1","kind":"Config","clusters":[{"name":"target","cluster":{"server":"` + srv.URL + `","certificate-authority-data":"` + pem + `"}}],"users":[{"name":"identity","user":{"token":"fixture-sensitive-token"}}],"contexts":[{"name":"explicit","context":{"cluster":"target","user":"identity"}}]}`
	return f, target
}

// R03.agent-paused：外部paused仅停止调谐，已有Pod不能被宣称停止。
func TestR03AgentPausedReadOnly(t *testing.T) {
	f, target := agentTarget(t)
	p, c, owner := clusterPlatform(t)
	op := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": "agent", "ownerId": owner, "context": "explicit", "kubeconfig": target}, c, "register", 202)
	cluster := awaitClusterOperation(t, p, c, op["operationId"].(string))["clusterId"].(string)
	f.mu.Lock()
	f.objects["monitoring/outside"] = map[string]any{"apiVersion": "monitoring.coreos.com/v1alpha1", "kind": "PrometheusAgent", "metadata": map[string]any{"name": "outside", "namespace": "monitoring", "uid": "outside-agent", "generation": 1}, "spec": map[string]any{"paused": true, "replicas": 1, "shards": 1}, "status": map[string]any{"paused": true, "replicas": 1, "availableReplicas": 1, "conditions": []any{map[string]any{"type": "Available", "status": "True", "observedGeneration": 1}}}}
	f.mu.Unlock()
	list := clusterCall(t, p, "GET", "/clusters/"+cluster+"/instances", nil, c, "", 200)["instances"].([]any)
	if len(list) != 1 {
		t.Fatal("未发现外部Agent")
	}
	i := list[0].(map[string]any)
	r := i["runtime"].(map[string]any)
	if i["readOnly"] != true || i["type"] != "collector" || r["summary"] == "Suspended" || r["paused"] != true {
		t.Fatal("paused被错误解释为停止Pod")
	}
	clusterCall(t, p, "PUT", "/instances/"+i["id"].(string)+"/draft", map[string]any{"expectedDraftRevision": 1, "agent": agentConfig("outside", "monitoring")}, c, "cannot-adopt", 403)
}
