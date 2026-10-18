package control

import (
	"encoding/json"
	"fmt"
	"regexp"
	"sort"
	"strings"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"gopkg.in/yaml.v3"
	"k8s.io/apimachinery/pkg/api/resource"
	"k8s.io/apimachinery/pkg/util/validation"
)

// KSMConfig 是平台草稿；隐藏的角色值不属于有效 CR spec。
type KSMConfig struct {
	Name                 string             `json:"name"`
	TargetNamespace      string             `json:"targetNamespace"`
	DeploymentMode       string             `json:"deploymentMode"`
	DesiredState         string             `json:"desiredState"`
	Resources            Resources          `json:"resources"`
	Collectors           Selection          `json:"collectors"`
	MetricFilter         MetricSelection    `json:"metricFilter"`
	NamespaceFilter      NamespaceSelection `json:"namespaceFilter"`
	AdvancedResources    bool               `json:"advancedResources"`
	Shards               *int32             `json:"shards,omitempty"`
	NodeResources        *RoleResources     `json:"nodeResources,omitempty"`
	ClusterResources     *RoleResources     `json:"clusterResources,omitempty"`
	UnscheduledResources *RoleResources     `json:"unscheduledResources,omitempty"`
}
type Resources struct {
	RequestCPU    *string `json:"requestCPU,omitempty"`
	RequestMemory *string `json:"requestMemory,omitempty"`
	LimitCPU      *string `json:"limitCPU,omitempty"`
	LimitMemory   *string `json:"limitMemory,omitempty"`
}
type Selection struct {
	Mode  string   `json:"mode"`
	Names []string `json:"names,omitempty"`
}
type MetricSelection struct {
	Mode     string   `json:"mode"`
	Patterns []string `json:"patterns,omitempty"`
}
type NamespaceSelection struct {
	Mode         string   `json:"mode"`
	Names        []string `json:"names,omitempty"`
	NamePatterns []string `json:"namePatterns,omitempty"`
}
type RoleResources struct {
	Mode      string     `json:"mode"`
	Resources *Resources `json:"resources,omitempty"`
}

var CollectorCatalog = []string{"pods", "deployments", "statefulsets", "daemonsets", "nodes", "namespaces", "services", "persistentvolumeclaims", "persistentvolumes", "jobs", "cronjobs", "replicasets", "configmaps", "secrets", "endpoints", "ingresses", "horizontalpodautoscalers", "certificatesigningrequests", "leases", "limitranges", "mutatingwebhookconfigurations", "networkpolicies", "poddisruptionbudgets", "replicationcontrollers", "resourcequotas", "storageclasses", "validatingwebhookconfigurations", "volumeattachments"}

func fieldError(field, message string) error {
	return &rpc.APIError{Code: "INVALID_INPUT", Message: field + "：" + message, FieldErrors: map[string]string{field: message}}
}
func decodeKSM(r *rpc.KSMConfiguration) (*KSMConfig, error) {
	if r == nil || r.Resources == nil || r.Collectors == nil || r.MetricFilter == nil || r.NamespaceFilter == nil {
		return nil, fieldError("ksm", "必须提供完整类型化配置")
	}
	raw, _ := json.Marshal(r)
	var c KSMConfig
	if json.Unmarshal(raw, &c) != nil {
		return nil, fieldError("ksm", "配置格式错误")
	}
	if e := c.validate(); e != nil {
		return nil, e
	}
	return &c, nil
}
func (c *KSMConfig) rpc() *rpc.KSMConfiguration {
	raw, _ := json.Marshal(c)
	r := new(rpc.KSMConfiguration)
	json.Unmarshal(raw, r)
	return r
}
func (r Resources) validate(path string) error {
	for _, pair := range []struct {
		name     string
		req, lim *string
	}{{"CPU", r.RequestCPU, r.LimitCPU}, {"Memory", r.RequestMemory, r.LimitMemory}} {
		var request resource.Quantity
		if pair.req != nil {
			q, e := resource.ParseQuantity(*pair.req)
			if e != nil || q.Sign() < 0 || *pair.req == "" {
				return fieldError(path+".request"+pair.name, "须为非负 Kubernetes quantity，空值应省略")
			}
			request = q
		}
		if pair.lim != nil {
			q, e := resource.ParseQuantity(*pair.lim)
			if e != nil || q.Sign() < 0 || *pair.lim == "" {
				return fieldError(path+".limit"+pair.name, "须为非负 Kubernetes quantity，空值应省略")
			}
			if pair.req == nil {
				return fieldError(path+".request"+pair.name, "设置 limit 时必须明确 request")
			}
			if request.Cmp(q) > 0 {
				return fieldError(path+".limit"+pair.name, "limit 必须大于或等于 request")
			}
		}
	}
	return nil
}
func uniqueNames(names []string, allowed []string) bool {
	seen := map[string]bool{}
	for _, n := range names {
		if seen[n] {
			return false
		}
		seen[n] = true
		if allowed != nil {
			found := false
			for _, a := range allowed {
				found = found || a == n
			}
			if !found {
				return false
			}
		}
	}
	return true
}
func (c *KSMConfig) validate() error {
	if len(validation.IsDNS1123Subdomain(c.Name)) != 0 {
		return fieldError("name", "须为 1–253 字符 Kubernetes DNS subdomain")
	}
	if len(validation.IsDNS1123Label(c.TargetNamespace)) != 0 {
		return fieldError("targetNamespace", "须为合法 namespace 名称")
	}
	if c.DesiredState != "Running" && c.DesiredState != "Suspended" && c.DesiredState != "Disabled" {
		return fieldError("desiredState", "请选择运行、挂起或停用")
	}
	switch c.DeploymentMode {
	case "Deployment":
		if c.Shards != nil {
			return fieldError("shards", "Deployment 不接受 shards")
		}
	case "StatefulSetSharded":
		if c.Shards == nil || *c.Shards < 2 {
			return fieldError("shards", "StatefulSetSharded 至少 2 shards")
		}
	case "DaemonSetSharded":
		if c.Shards != nil {
			return fieldError("shards", "DaemonSetSharded 不接受 shards")
		}
	default:
		return fieldError("deploymentMode", "不支持的部署模式")
	}
	if e := c.Resources.validate("resources"); e != nil {
		return e
	}
	switch c.Collectors.Mode {
	case "Default":
		if len(c.Collectors.Names) > 0 {
			return fieldError("collectors.names", "Default 应省略 names")
		}
	case "Include":
		if len(c.Collectors.Names) == 0 || !uniqueNames(c.Collectors.Names, CollectorCatalog) {
			return fieldError("collectors.names", "须从 28 项 catalog 非空且不重复选择")
		}
	default:
		return fieldError("collectors.mode", "必须明确 Default 或 Include")
	}
	if c.DeploymentMode == "DaemonSetSharded" && c.Collectors.Mode == "Include" {
		pods := false
		for _, n := range c.Collectors.Names {
			pods = pods || n == "pods"
		}
		if !pods || len(c.Collectors.Names) < 2 {
			return fieldError("collectors.names", "DaemonSetSharded 必须包含 pods 和至少一个非 Pod collector")
		}
	}
	switch c.MetricFilter.Mode {
	case "All":
		if len(c.MetricFilter.Patterns) > 0 {
			return fieldError("metricFilter.patterns", "All 应省略 patterns")
		}
	case "Allow", "Deny":
		if len(c.MetricFilter.Patterns) == 0 {
			return fieldError("metricFilter.patterns", "至少一个正则")
		}
		for _, p := range c.MetricFilter.Patterns {
			if p == "" || strings.Contains(p, ",") {
				return fieldError("metricFilter.patterns", "单项非空且不含逗号")
			}
			if _, e := regexp.Compile(p); e != nil {
				return fieldError("metricFilter.patterns", "须为合法 Go regexp")
			}
		}
	default:
		return fieldError("metricFilter.mode", "必须明确 All、Allow 或 Deny")
	}
	switch c.NamespaceFilter.Mode {
	case "All":
		if len(c.NamespaceFilter.Names) > 0 || len(c.NamespaceFilter.NamePatterns) > 0 {
			return fieldError("namespaceFilter", "All 应省略 names/namePatterns")
		}
	case "Include":
		if len(c.NamespaceFilter.Names)+len(c.NamespaceFilter.NamePatterns) == 0 {
			return fieldError("namespaceFilter", "至少一个 literal 或正则")
		}
		if !uniqueNames(c.NamespaceFilter.Names, nil) {
			return fieldError("namespaceFilter.names", "不能重复")
		}
		for _, n := range c.NamespaceFilter.Names {
			if len(validation.IsDNS1123Label(n)) > 0 {
				return fieldError("namespaceFilter.names", "非法 namespace 名称")
			}
		}
		for _, p := range c.NamespaceFilter.NamePatterns {
			if p == "" {
				return fieldError("namespaceFilter.namePatterns", "正则不能为空")
			}
			if _, e := regexp.Compile("^(?:" + p + ")$"); e != nil {
				return fieldError("namespaceFilter.namePatterns", "须为合法 Go regexp")
			}
		}
	default:
		return fieldError("namespaceFilter.mode", "必须明确 All 或 Include")
	}
	for name, role := range map[string]*RoleResources{"nodeResources": c.NodeResources, "clusterResources": c.ClusterResources, "unscheduledResources": c.UnscheduledResources} {
		if role == nil {
			continue
		}
		if role.Mode != "Inherit" && role.Mode != "Override" {
			return fieldError(name+".mode", "必须明确 Inherit 或 Override")
		}
		if role.Mode == "Override" && role.Resources == nil {
			return fieldError(name+".resources", "独立配置必须提供完整资源块")
		}
		if role.Resources != nil {
			if e := role.Resources.validate(name + ".resources"); e != nil {
				return e
			}
		}
	}
	return nil
}
func (r Resources) cr() map[string]any {
	out := map[string]any{}
	req, lim := map[string]string{}, map[string]string{}
	if r.RequestCPU != nil {
		req["cpu"] = *r.RequestCPU
	}
	if r.RequestMemory != nil {
		req["memory"] = *r.RequestMemory
	}
	if r.LimitCPU != nil {
		lim["cpu"] = *r.LimitCPU
	}
	if r.LimitMemory != nil {
		lim["memory"] = *r.LimitMemory
	}
	if len(req) > 0 {
		out["requests"] = req
	}
	if len(lim) > 0 {
		out["limits"] = lim
	}
	return out
}
func (c *KSMConfig) spec() map[string]any {
	deployment := map[string]any{"mode": c.DeploymentMode}
	switch c.DeploymentMode {
	case "StatefulSetSharded":
		deployment["statefulSetSharded"] = map[string]any{"shards": *c.Shards}
	case "DaemonSetSharded":
		ds := map[string]any{}
		if c.AdvancedResources {
			for k, v := range map[string]*RoleResources{"nodePodResources": c.NodeResources, "clusterPodResources": c.ClusterResources, "unscheduledPodResources": c.UnscheduledResources} {
				if v != nil && v.Mode == "Override" {
					ds[k] = v.Resources.cr()
				}
			}
		}
		deployment["daemonSetSharded"] = ds
	}
	collectors := map[string]any{"mode": c.Collectors.Mode}
	if c.Collectors.Mode == "Include" {
		collectors["names"] = c.Collectors.Names
	}
	metric := map[string]any{"mode": c.MetricFilter.Mode}
	if c.MetricFilter.Mode != "All" {
		metric["patterns"] = c.MetricFilter.Patterns
	}
	ns := map[string]any{"mode": c.NamespaceFilter.Mode}
	if c.NamespaceFilter.Mode == "Include" {
		if len(c.NamespaceFilter.Names) > 0 {
			ns["names"] = c.NamespaceFilter.Names
		}
		if len(c.NamespaceFilter.NamePatterns) > 0 {
			ns["namePatterns"] = c.NamespaceFilter.NamePatterns
		}
	}
	return map[string]any{"targetNamespace": c.TargetNamespace, "enabled": c.DesiredState != "Disabled", "suspended": c.DesiredState == "Suspended", "deployment": deployment, "podResources": c.Resources.cr(), "collectors": collectors, "metricFilter": metric, "namespaceFilter": ns}
}

const ksmAPI = "monitoring.fjsxy.github.io/v1alpha1"
const ksmPath = "/apis/" + ksmAPI + "/kubestatemetrics"

func ksmCR(name string, spec map[string]any) map[string]any {
	return map[string]any{"apiVersion": ksmAPI, "kind": "KubeStateMetrics", "metadata": map[string]any{"name": name}, "spec": spec}
}
func yamlText(v any) string {
	raw, _ := json.Marshal(v)
	var doc any
	json.Unmarshal(raw, &doc)
	b, e := yaml.Marshal(doc)
	if e != nil {
		return ""
	}
	return string(b)
}
func canonical(v any) string { b, _ := json.Marshal(v); return string(b) }

// 只归一固定 CRD 的准入默认值；未知字段与 nil/{} 均保留。
func normalizedSpec(spec map[string]any) string {
	var copy map[string]any
	json.Unmarshal([]byte(canonical(spec)), &copy)
	if copy == nil {
		copy = map[string]any{}
	}
	for k, v := range map[string]any{"enabled": true, "suspended": false} {
		if _, ok := copy[k]; !ok {
			copy[k] = v
		}
	}
	for k, mode := range map[string]string{"deployment": "Deployment", "collectors": "Default", "metricFilter": "All", "namespaceFilter": "All"} {
		v, exists := copy[k]
		if !exists {
			copy[k] = map[string]any{"mode": mode}
			continue
		}
		if m, ok := v.(map[string]any); ok {
			if _, has := m["mode"]; !has {
				m["mode"] = mode
			}
		}
	}
	return canonical(copy)
}
func sortedUnique(in []string) []string {
	m := map[string]bool{}
	for _, v := range in {
		m[v] = true
	}
	out := []string{}
	for v := range m {
		out = append(out, v)
	}
	sort.Strings(out)
	return out
}

var _ = fmt.Sprintf
