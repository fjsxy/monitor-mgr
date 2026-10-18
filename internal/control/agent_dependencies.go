// Agent依赖检查分开平台、Operator和采集SA身份；不创建Secret/SA/RBAC/StorageClass。
package control

import (
	"context"
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

// listObjects只有完整分页成功才返回集合，失败不能被当作零匹配或对象不存在。
func (t *Target) listObjects(ctx context.Context, path string) ([]map[string]any, error) {
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
			return nil, e
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

func argumentMap(raw any) map[string]string {
	m := map[string]string{}
	values, _ := raw.([]any)
	for n := 0; n < len(values); n++ {
		v, _ := values[n].(string)
		if !strings.HasPrefix(v, "--") {
			continue
		}
		p := strings.SplitN(strings.TrimPrefix(v, "--"), "=", 2)
		if len(p) == 2 {
			m[p[0]] = p[1]
		} else if n+1 < len(values) {
			next, _ := values[n+1].(string)
			if !strings.HasPrefix(next, "--") {
				m[p[0]] = next
				n++
			}
		}
	}
	return m
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
			o := operatorAccess{namespace: objectString(meta, "namespace"), name: objectString(meta, "name"), uid: objectString(meta, "uid"), args: argumentMap(container["args"]), deployment: d}
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
		return Error("CONFLICT", "Agent ServiceAccount不存在或不可读")
	}
	if objectString(objectMap(sa, "metadata"), "name") != cfg.ServiceAccountName {
		return Error("CONFLICT", "Agent ServiceAccount身份无法确认")
	}
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
	for _, endpoint := range cfg.RemoteWrite {
		for _, ref := range endpoint.references() {
			var secret struct {
				Data map[string]any `json:"data"`
			}
			if e := t.Request(ctx, "GET", "/api/v1/namespaces/"+url.PathEscape(cfg.Namespace)+"/secrets/"+url.PathEscape(ref.Name), nil, &secret); e != nil {
				return Error("CONFLICT", "remoteWrite Secret不存在或不可读")
			}
			if _, exists := secret.Data[ref.Key]; !exists {
				return fieldError("agent.remoteWrite.secretRef", "Secret所需key不存在")
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
