// Agent依赖检查分开平台、Operator和采集SA身份；不创建Secret/SA/RBAC/StorageClass。
package control

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"net/url"
	"strings"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"k8s.io/apimachinery/pkg/labels"
)

type collectionList struct {
	Metadata struct {
		Continue string `json:"continue"`
	} `json:"metadata"`
	Items []map[string]any `json:"items"`
}

// agentDependenciesFromSpec只读取冻结规格中的依赖，mapper升级不能改变旧版本的SA、关联或Secret引用。
func agentDependenciesFromSpec(original *AgentConfig, spec map[string]any) (*AgentConfig, error) {
	copy := *original
	copy.Associations = associationConfigFromCR(map[string]any{"metadata": map[string]any{"name": original.Name, "namespace": original.Namespace}, "spec": spec}).Associations
	copy.ServiceAccountName = objectString(spec, "serviceAccountName")
	if copy.ServiceAccountName == "" {
		return nil, Error("CONFLICT", "冻结Agent ServiceAccount缺失")
	}
	ref := func(raw any) *SecretReference {
		m, _ := raw.(map[string]any)
		if m == nil {
			return nil
		}
		return &SecretReference{Name: objectString(m, "name"), Key: objectString(m, "key")}
	}
	mode, capacity := agentStorageIdentity(spec)
	if mode == "" {
		return nil, Error("CONFLICT", "冻结Agent存储模式缺失")
	}
	replicas, shards, e := agentCounts(spec)
	if e != nil {
		return nil, e
	}
	copy.Shards = shards
	copy.DesiredState = "Running"
	if replicas == 0 {
		copy.DesiredState = "Suspended"
	}
	copy.Storage.Mode = mode
	copy.Storage.Capacity = nil
	if mode == "PVC" {
		copy.Storage.Capacity = &capacity
	}
	copy.RemoteWrite = []RemoteEndpoint{}
	for _, raw := range anySlice(spec["remoteWrite"]) {
		m, ok := raw.(map[string]any)
		if !ok {
			return nil, Error("CONFLICT", "冻结remoteWrite不可读")
		}
		r := RemoteEndpoint{URL: objectString(m, "url"), Auth: RemoteAuth{Mode: "None"}}
		if basic, ok := m["basicAuth"].(map[string]any); ok {
			r.Auth = RemoteAuth{Mode: "BasicAuth", Username: ref(basic["username"]), Password: ref(basic["password"])}
			if r.Auth.Username == nil || r.Auth.Password == nil {
				return nil, Error("CONFLICT", "冻结BasicAuth引用不可读")
			}
		}
		if auth, ok := m["authorization"].(map[string]any); ok {
			r.Auth = RemoteAuth{Mode: "Bearer", Credentials: ref(auth["credentials"])}
			if r.Auth.Credentials == nil {
				return nil, Error("CONFLICT", "冻结Bearer引用不可读")
			}
		}
		if tls, ok := m["tlsConfig"].(map[string]any); ok {
			r.TLS = &AgentTLS{CA: ref(objectMap(tls, "ca")["secret"]), Cert: ref(objectMap(tls, "cert")["secret"]), Key: ref(tls["keySecret"])}
		}
		copy.RemoteWrite = append(copy.RemoteWrite, r)
	}
	if len(copy.RemoteWrite) == 0 {
		return nil, Error("CONFLICT", "冻结remoteWrite为空")
	}
	return &copy, nil
}

// listObjects只有完整分页成功才返回集合，失败不能被当作零匹配或对象不存在。
func (t *Target) listObjects(ctx context.Context, path string) ([]map[string]any, error) {
	return t.listObjectsWithOptionalAPI(ctx, path, false)
}

// listObjectsWithOptionalAPI 仅允许可选API首页确实404；后续缺页仍是未知。
func (t *Target) listObjectsWithOptionalAPI(ctx context.Context, path string, optional bool) ([]map[string]any, error) {
	out := []map[string]any{}
	next := ""
	seen := map[string]bool{}
	for {
		var list collectionList
		sep := "?"
		if strings.Contains(path, "?") {
			sep = "&"
		}
		if e := t.Request(ctx, "GET", path+sep+"limit=500&continue="+url.QueryEscape(next), nil, &list); e != nil {
			if optional && next == "" && apiCode(e) == "TARGET_NOT_FOUND" {
				return out, nil
			}
			return nil, e
		}
		// 关键：缺items不是空集合；辅助身份/策略缺页不能作为安全放行或已清理的证据。
		if list.Items == nil {
			return nil, Error("TARGET_ERROR", "列表响应不完整，不能确认资源集合")
		}
		out = append(out, list.Items...)
		if list.Metadata.Continue == "" {
			return out, nil
		}
		if seen[list.Metadata.Continue] {
			return nil, Error("TARGET_ERROR", "分页未前进，结果不完整")
		}
		seen[list.Metadata.Continue] = true
		next = list.Metadata.Continue
	}
}
func (t *Target) subjectAccess(ctx context.Context, user, namespace, group, resource, verb string) error {
	attr := map[string]string{"group": group, "resource": resource, "verb": verb}
	if namespace != "" {
		attr["namespace"] = namespace
	}
	kind, path := "SelfSubjectAccessReview", "selfsubjectaccessreviews"
	spec := map[string]any{"resourceAttributes": attr}
	if user != "" {
		kind, path = "SubjectAccessReview", "subjectaccessreviews"
		spec["user"] = user
		parts := strings.Split(user, ":")
		if len(parts) == 4 && parts[0] == "system" && parts[1] == "serviceaccount" {
			spec["groups"] = []string{"system:authenticated", "system:serviceaccounts", "system:serviceaccounts:" + parts[2]}
		}
	}
	var review struct {
		Status struct {
			Allowed         bool   `json:"allowed"`
			Denied          bool   `json:"denied"`
			EvaluationError string `json:"evaluationError"`
		} `json:"status"`
	}
	if e := t.Request(ctx, "POST", "/apis/authorization.k8s.io/v1/"+path, map[string]any{"apiVersion": "authorization.k8s.io/v1", "kind": kind, "spec": spec}, &review); e != nil {
		return Error("TARGET_FORBIDDEN", "依赖身份权限无法核验")
	}
	if review.Status.EvaluationError != "" {
		return Error("TARGET_ERROR", "依赖身份权限评估失败")
	}
	if !review.Status.Allowed {
		return Error("TARGET_FORBIDDEN", "依赖身份缺少所需权限："+resource+"/"+verb)
	}
	return nil
}

type operatorAccess struct {
	namespace, name, uid, user string
	args                       map[string]string
	deployment                 map[string]any
}

// argumentMap只解释无歧义字面参数；Go停止点、集合重复及Kubelet展开不能被当作全scope。
func argumentMap(raw any) (map[string]string, error) {
	m := map[string]string{}
	unsupported := func() (map[string]string, error) {
		return nil, Error("CONFLICT", "Operator参数语法暂不支持，无法可靠确认实际范围；请使用无展开且无重复的明确参数")
	}
	values, ok := raw.([]any)
	if raw != nil && !ok {
		return unsupported()
	}
	stringsFlags := map[string]bool{"namespaces": true, "deny-namespaces": true, "prometheus-instance-namespaces": true, "prometheus-instance-selector": true, "controller-id": true}
	boolFlags := map[string]bool{"kubelet-endpoints": true, "kubelet-endpointslice": true, "tls-insecure": true}
	for n := 0; n < len(values); n++ {
		v, ok := values[n].(string)
		if !ok || !strings.HasPrefix(v, "-") || strings.HasPrefix(v, "---") || v == "-" || v == "--" || strings.Contains(v, "$(") {
			return unsupported()
		}
		p := strings.SplitN(strings.TrimLeft(v, "-"), "=", 2)
		if p[0] == "" {
			return unsupported()
		}
		if _, duplicate := m[p[0]]; duplicate && stringsFlags[p[0]] {
			return unsupported()
		}
		if len(p) == 2 {
			m[p[0]] = p[1]
		} else if stringsFlags[p[0]] && n+1 < len(values) {
			next, ok := values[n+1].(string)
			if !ok || strings.HasPrefix(next, "-") || strings.Contains(next, "$(") {
				return unsupported()
			}
			m[p[0]] = next
			n++
		} else if boolFlags[p[0]] {
			// Go bool flag不消费空格后的false/true；下一轮会明确拒绝该停止点。
			m[p[0]] = "true"
		} else {
			return unsupported()
		}
	}
	return m, nil
}
func inCSV(list, value string) bool {
	for _, v := range strings.Split(list, ",") {
		if v == value {
			return true
		}
	}
	return false
}
func (o operatorAccess) visible(namespace string, instance bool) bool {
	allow := o.args["namespaces"]
	if instance && o.args["prometheus-instance-namespaces"] != "" {
		allow = o.args["prometheus-instance-namespaces"]
	}
	return (allow == "" || inCSV(allow, namespace)) && !inCSV(o.args["deny-namespaces"], namespace)
}
func (t *Target) agentOperator(ctx context.Context, cfg *AgentConfig) (*operatorAccess, error) {
	objects, e := t.listObjects(ctx, "/apis/apps/v1/deployments?labelSelector="+url.QueryEscape("app.kubernetes.io/name=prometheus-operator"))
	if e != nil {
		return nil, Error("TARGET_ERROR", "无法读取Prometheus Operator实际配置")
	}
	found := []operatorAccess{}
	for _, d := range objects {
		meta := objectMap(d, "metadata")
		pod := objectMap(objectMap(objectMap(d, "spec"), "template"), "spec")
		containers, _ := pod["containers"].([]any)
		for _, raw := range containers {
			container, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			image := objectString(container, "image")
			if !strings.Contains(image, "prometheus-operator/prometheus-operator") {
				continue
			}
			version := objectString(objectMap(meta, "labels"), "app.kubernetes.io/version")
			if version != "0.83.0" || (!strings.HasSuffix(image, ":v0.83.0") && !strings.Contains(image, "@sha256:")) {
				continue
			}
			args, e := argumentMap(container["args"])
			if e != nil {
				return nil, e
			}
			o := operatorAccess{namespace: objectString(meta, "namespace"), name: objectString(meta, "name"), uid: objectString(meta, "uid"), args: args, deployment: d}
			o.user = "system:serviceaccount:" + o.namespace + ":" + objectString(pod, "serviceAccountName")
			if !o.visible(cfg.Namespace, true) || o.args["controller-id"] != "" {
				continue
			}
			selector, e := labels.Parse(o.args["prometheus-instance-selector"])
			if e != nil || !selector.Matches(labels.Set{}) {
				continue
			}
			found = append(found, o)
		}
	}
	if len(found) != 1 {
		return nil, Error("CONFLICT", "无法唯一确认固定v0.83.0 Operator的Agent可见范围")
	}
	return &found[0], nil
}
func (t *Target) agentDependencies(ctx context.Context, cl *rpc.Cluster, cfg *AgentConfig, create bool) error {
	if create {
		if e := t.checkNewAgentClaims(ctx, cfg); e != nil {
			return e
		}
	}
	var ns map[string]any
	if e := t.Request(ctx, "GET", "/api/v1/namespaces/kube-system", nil, &ns); e != nil {
		return e
	}
	if objectString(objectMap(ns, "metadata"), "uid") != cl.Uid {
		return Error("CONFLICT", "集群UID已变化")
	}
	if e := t.Request(ctx, "GET", "/api/v1/namespaces/"+url.PathEscape(cfg.Namespace), nil, &ns); e != nil {
		return e
	}
	if objectString(objectMap(ns, "metadata"), "name") != cfg.Namespace {
		return Error("CONFLICT", "Agent namespace无法确认")
	}
	if _, e := t.listObjects(ctx, componentFor(AgentComponent).collectionPath(cfg.Namespace)); e != nil {
		return Error("CONFLICT", "PrometheusAgent CRD或读取权限不可用")
	}
	verb := "update"
	if create {
		verb = "create"
	}
	if e := t.subjectAccess(ctx, "", cfg.Namespace, "monitoring.coreos.com", "prometheusagents", verb); e != nil {
		return e
	}
	var sa map[string]any
	if e := t.Request(ctx, "GET", "/api/v1/namespaces/"+url.PathEscape(cfg.Namespace)+"/serviceaccounts/"+url.PathEscape(cfg.ServiceAccountName), nil, &sa); e != nil {
		return dependencyReadError(e, "agent.serviceAccountName", "ServiceAccount")
	}
	if objectString(objectMap(sa, "metadata"), "name") != cfg.ServiceAccountName {
		return Error("CONFLICT", "Agent ServiceAccount身份无法确认")
	}
	if cfg.Storage.Mode == "PVC" {
		classes, e := t.listObjects(ctx, "/apis/storage.k8s.io/v1/storageclasses")
		if e != nil {
			return Error("CONFLICT", "默认StorageClass无法读取")
		}
		available := false
		for _, sc := range classes {
			a := objectMap(objectMap(sc, "metadata"), "annotations")
			if (a["storageclass.kubernetes.io/is-default-class"] == "true" || a["storageclass.beta.kubernetes.io/is-default-class"] == "true") && objectString(sc, "provisioner") != "" && objectString(sc, "provisioner") != "kubernetes.io/no-provisioner" {
				available = true
			}
		}
		if !available {
			return Error("CONFLICT", "PVC发布需要可用默认动态StorageClass")
		}
	}

	// 关键：Secret值只保留在当前栈的内存缓存，既不写配置/观察表，也不进入错误正文。
	secrets := map[string]map[string][]byte{}
	for _, endpoint := range cfg.RemoteWrite {
		for _, ref := range endpoint.references() {
			if _, loaded := secrets[ref.Name]; !loaded {
				var secret struct {
					Data map[string][]byte `json:"data"`
				}
				if e := t.Request(ctx, "GET", "/api/v1/namespaces/"+url.PathEscape(cfg.Namespace)+"/secrets/"+url.PathEscape(ref.Name), nil, &secret); e != nil {
					return dependencyReadError(e, "agent.remoteWrite.secretRef", "Secret")
				}
				secrets[ref.Name] = secret.Data
			}
			if _, ok := secrets[ref.Name][ref.Key]; !ok {
				return fieldError("agent.remoteWrite.secretRef", "Secret所需key不存在")
			}
		}
		if endpoint.TLS != nil {
			tlsConfig := endpoint.TLS
			if tlsConfig.CA != nil {
				pool := x509.NewCertPool()
				if !pool.AppendCertsFromPEM(secrets[tlsConfig.CA.Name][tlsConfig.CA.Key]) {
					return fieldError("agent.remoteWrite.tls.ca", "CA引用不是有效PEM证书")
				}
			}
			if tlsConfig.Cert != nil {
				if _, e := tls.X509KeyPair(secrets[tlsConfig.Cert.Name][tlsConfig.Cert.Key], secrets[tlsConfig.Key.Name][tlsConfig.Key.Key]); e != nil {
					return fieldError("agent.remoteWrite.tls", "客户端证书和私钥内容无效或不匹配")
				}
			}
		}
	}
	operator, e := t.agentOperator(ctx, cfg)
	if e != nil {
		return e
	}
	if e = t.verifyOperatorRuntime(ctx, operator, cfg.Associations.ScrapeConfig.Object.Mode != "None"); e != nil {
		return e
	}
	for _, kind := range associationKinds {
		rule := cfg.associationRules()[kind.kind]
		if rule.Object.Mode == "None" {
			continue
		}
		namespaces, e := t.associationNamespaces(ctx, cfg.Namespace, rule.Namespace)
		if e != nil {
			return e
		}
		for _, namespace := range namespaces {
			if !operator.visible(namespace, false) {
				if rule.Object.Mode != "None" {
					return Error("CONFLICT", "采集配置namespace不在Operator可见范围")
				}
				continue
			}
			for _, v := range []string{"get", "list", "watch"} {
				if e = t.subjectAccess(ctx, operator.user, namespace, "monitoring.coreos.com", kind.resource, v); e != nil {
					return e
				}
			}
		}
		objects, e := t.matchAssociation(ctx, cfg, kind)
		if e != nil {
			return e
		}

		if e = t.verifyAgentDiscoveryPermissions(ctx, cfg, kind.kind, objects); e != nil {
			return e
		}
	}
	return nil
}

// dependencyReadError保留NotFound/Forbidden/读取失败分类，字段错误不包含目标返回体或Secret值。
func dependencyReadError(cause error, field, kind string) error {
	code, message := apiCode(cause), kind+"读取失败"
	if code == "TARGET_NOT_FOUND" {
		message = kind + "不存在"
	}
	if code == "TARGET_FORBIDDEN" {
		message = kind + "访问被拒绝"
	}
	return &rpc.APIError{Code: code, Message: message, FieldErrors: map[string]string{field: message}}
}
