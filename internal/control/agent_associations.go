// 采集关联预览只计算动态规则匹配，不代表实际采集目标或数据面成功。
package control

import (
	"context"
	"net/url"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
)

type associationKind struct{ kind, version, resource string }

var associationKinds = []associationKind{{"ServiceMonitor", "v1", "servicemonitors"}, {"PodMonitor", "v1", "podmonitors"}, {"Probe", "v1", "probes"}, {"ScrapeConfig", "v1alpha1", "scrapeconfigs"}}

func selectorLabels(cr map[string]any) labels.Set {
	out := labels.Set{}
	for k, v := range objectMap(objectMap(cr, "metadata"), "labels") {
		if text, ok := v.(string); ok {
			out[k] = text
		}
	}
	return out
}
func (t *Target) associationNamespaces(ctx context.Context, own string, rule SelectorRule) ([]string, error) {
	if rule.Mode == "Own" {
		return []string{own}, nil
	}
	namespaces, e := t.listObjects(ctx, "/api/v1/namespaces")
	if e != nil {
		return nil, e
	}
	selector := labels.Everything()
	if rule.Mode == "Labels" {
		selector, e = metav1.LabelSelectorAsSelector(rule.Selector.meta())
		if e != nil {
			return nil, fieldError("agent.associations.namespace", "非法Namespace标签选择")
		}
	}
	out := []string{}
	for _, ns := range namespaces {
		if selector.Matches(selectorLabels(ns)) {
			name := objectString(objectMap(ns, "metadata"), "name")
			if name == "" {
				return nil, Error("TARGET_ERROR", "Namespace身份不完整")
			}
			out = append(out, name)
		}
	}
	return out, nil
}
func (t *Target) matchAssociation(ctx context.Context, cfg *AgentConfig, kind associationKind) ([]map[string]any, error) {
	rule := cfg.associationRules()[kind.kind]
	if rule.Object.Mode == "None" {
		return []map[string]any{}, nil
	}
	namespaces, e := t.associationNamespaces(ctx, cfg.Namespace, rule.Namespace)
	if e != nil {
		return nil, e
	}
	selector := labels.Nothing()
	if rule.Object.Mode == "All" {
		selector = labels.Everything()
	}
	if rule.Object.Mode == "Labels" {
		selector, e = metav1.LabelSelectorAsSelector(rule.Object.Selector.meta())
		if e != nil {
			return nil, fieldError("agent.associations.object", "非法对象标签选择")
		}
	}
	out := []map[string]any{}
	for _, ns := range namespaces {
		items, e := t.listObjects(ctx, "/apis/monitoring.coreos.com/"+kind.version+"/namespaces/"+url.PathEscape(ns)+"/"+kind.resource)
		if e != nil {
			return nil, e
		}
		for _, item := range items {
			meta := objectMap(item, "metadata")
			if objectString(meta, "name") == "" || objectString(meta, "uid") == "" || objectString(meta, "namespace") != ns {
				return nil, Error("TARGET_ERROR", "采集配置身份不完整")
			}
			if selector.Matches(selectorLabels(item)) {
				out = append(out, item)
			}
		}
	}
	return out, nil
}
func monitorTargetNamespaces(spec map[string]any, own string) []string {
	selector := objectMap(spec, "namespaceSelector")
	if selector["any"] == true {
		return []string{""}
	}
	raw, _ := selector["matchNames"].([]any)
	out := []string{}
	for _, x := range raw {
		if n, ok := x.(string); ok {
			out = append(out, n)
		}
	}
	if len(out) == 0 {
		return []string{own}
	}
	return out
}
func (t *Target) verifyAgentDiscoveryPermissions(ctx context.Context, cfg *AgentConfig, kind string, objects []map[string]any) error {
	user := "system:serviceaccount:" + cfg.Namespace + ":" + cfg.ServiceAccountName
	type requirement struct{ namespace, group, resource string }
	required := map[requirement]bool{}
	add := func(namespaces []string, group string, resources ...string) {
		for _, ns := range namespaces {
			for _, r := range resources {
				required[requirement{ns, group, r}] = true
			}
		}
	}
	for _, obj := range objects {
		spec, meta := objectMap(obj, "spec"), objectMap(obj, "metadata")
		own := objectString(meta, "namespace")
		switch kind {
		case "ServiceMonitor":
			add(monitorTargetNamespaces(spec, own), "", "services", "endpoints", "pods")
		case "PodMonitor":
			add(monitorTargetNamespaces(spec, own), "", "pods")
		case "Probe":
			if ingress := objectMap(objectMap(spec, "targets"), "ingress"); ingress != nil {
				add(monitorTargetNamespaces(ingress, own), "networking.k8s.io", "ingresses")
			}
		case "ScrapeConfig":
			configs, _ := spec["kubernetesSDConfigs"].([]any)
			for _, raw := range configs {
				sd, ok := raw.(map[string]any)
				if !ok {
					continue
				}
				names := []string{}
				for _, v := range anySlice(objectMap(sd, "namespaces")["names"]) {
					if name, ok := v.(string); ok {
						names = append(names, name)
					}
				}
				if len(names) == 0 {
					names = []string{""}
				}
				switch objectString(sd, "role") {
				case "Node":
					add([]string{""}, "", "nodes")
				case "Pod":
					add(names, "", "pods")
				case "Service":
					add(names, "", "services")
				case "Endpoints":
					add(names, "", "endpoints", "services", "pods")
				case "EndpointSlice":
					add(names, "discovery.k8s.io", "endpointslices")
					add(names, "", "services", "pods")
				case "Ingress":
					add(names, "networking.k8s.io", "ingresses")
				default:
					return fieldError("agent.associations.ScrapeConfig", "无法核验所选Kubernetes SD角色")
				}
			}
		}
	}
	for r := range required {
		for _, verb := range []string{"get", "list", "watch"} {
			if e := t.subjectAccess(ctx, user, r.namespace, r.group, r.resource, verb); e != nil {
				return e
			}
		}
	}
	return nil
}
func anySlice(v any) []any { x, _ := v.([]any); return x }
