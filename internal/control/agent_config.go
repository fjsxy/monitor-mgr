// Agent配置保留显式选择模式与optional集合；CR只输出固定版本支持的字段。
package control

import (
	"encoding/json"
	"math"
	"net/url"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"time"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/validation"
)

type AgentConfig struct {
	Name               string            `json:"name"`
	Namespace          string            `json:"namespace"`
	DesiredState       string            `json:"desiredState"`
	Shards             int32             `json:"shards"`
	Resources          Resources         `json:"resources"`
	Storage            AgentStorage      `json:"storage"`
	ServiceAccountName string            `json:"serviceAccountName"`
	Associations       AgentAssociations `json:"associations"`
	RemoteWrite        []RemoteEndpoint  `json:"remoteWrite"`
}
type AgentStorage struct {
	Mode        string  `json:"mode"`
	Capacity    *string `json:"capacity"`
	WhenScaled  string  `json:"whenScaled"`
	WhenDeleted string  `json:"whenDeleted"`
}
type AgentAssociations struct {
	ServiceMonitor AssociationRule `json:"serviceMonitor"`
	PodMonitor     AssociationRule `json:"podMonitor"`
	Probe          AssociationRule `json:"probe"`
	ScrapeConfig   AssociationRule `json:"scrapeConfig"`
}
type AssociationRule struct {
	Object    SelectorRule `json:"object"`
	Namespace SelectorRule `json:"namespace"`
}
type SelectorRule struct {
	Mode     string              `json:"mode"`
	Selector *AgentLabelSelector `json:"selector"`
}
type AgentLabelSelector struct {
	MatchLabels      map[string]string      `json:"matchLabels"`
	MatchExpressions []AgentLabelExpression `json:"matchExpressions"`
}
type AgentLabelExpression struct {
	Key      string   `json:"key"`
	Operator string   `json:"operator"`
	Values   []string `json:"values"`
}
type SecretReference struct {
	Name string `json:"name"`
	Key  string `json:"key"`
}
type RemoteAuth struct {
	Mode        string           `json:"mode"`
	Username    *SecretReference `json:"username"`
	Password    *SecretReference `json:"password"`
	Credentials *SecretReference `json:"credentials"`
}
type AgentTLS struct {
	CA         *SecretReference `json:"ca"`
	Cert       *SecretReference `json:"cert"`
	Key        *SecretReference `json:"key"`
	ServerName *string          `json:"serverName"`
}
type RemoteEndpoint struct {
	URL           string     `json:"url"`
	Name          *string    `json:"name"`
	RemoteTimeout *string    `json:"remoteTimeout"`
	Auth          RemoteAuth `json:"auth"`
	TLS           *AgentTLS  `json:"tls"`
}

// transportValue忽略omitempty但尊重字段名，把Thrift nil与显式空集合带到独立平台配置。
func transportValue(v reflect.Value) any {
	if !v.IsValid() {
		return nil
	}
	if v.Kind() == reflect.Pointer || v.Kind() == reflect.Interface {
		if v.IsNil() {
			return nil
		}
		return transportValue(v.Elem())
	}
	switch v.Kind() {
	case reflect.Struct:
		m := map[string]any{}
		for n := 0; n < v.NumField(); n++ {
			f := v.Type().Field(n)
			if f.PkgPath != "" {
				continue
			}
			name := strings.Split(f.Tag.Get("json"), ",")[0]
			if name == "-" {
				continue
			}
			if name == "" {
				name = f.Name
			}
			m[name] = transportValue(v.Field(n))
		}
		return m
	case reflect.Map:
		if v.IsNil() {
			return nil
		}
		m := map[string]any{}
		it := v.MapRange()
		for it.Next() {
			m[it.Key().String()] = transportValue(it.Value())
		}
		return m
	case reflect.Slice:
		if v.IsNil() {
			return nil
		}
		items := make([]any, v.Len())
		for n := range items {
			items[n] = transportValue(v.Index(n))
		}
		return items
	default:
		return v.Interface()
	}
}
func decodeAgent(r *rpc.AgentConfiguration) (*AgentConfig, error) {
	if r == nil || r.Resources == nil || r.Storage == nil || r.Associations == nil || r.Associations.ServiceMonitor == nil || r.Associations.PodMonitor == nil || r.Associations.Probe == nil || r.Associations.ScrapeConfig == nil {
		return nil, fieldError("agent", "必须提供完整类型化配置")
	}
	raw, e := json.Marshal(transportValue(reflect.ValueOf(r)))
	if e != nil {
		return nil, fieldError("agent", "配置格式错误")
	}
	var c AgentConfig
	if json.Unmarshal(raw, &c) != nil {
		return nil, fieldError("agent", "配置格式错误")
	}
	if e = c.validate(); e != nil {
		return nil, e
	}
	return &c, nil
}
func (c *AgentConfig) rpc() *rpc.AgentConfiguration {
	r := new(rpc.AgentConfiguration)
	raw, _ := json.Marshal(c)
	json.Unmarshal(raw, r)
	return r
}
func (c *AgentConfig) identity() ResourceIdentity {
	return ResourceIdentity{AgentComponent, "monitoring.coreos.com/v1alpha1", "PrometheusAgent", "Namespaced", c.Namespace, c.Name, c.Namespace}
}
func (c *AgentConfig) draft(rev int64) *rpc.ConfigurationDraft {
	return &rpc.ConfigurationDraft{Revision: rev, Agent: c.rpc(), Yaml: yamlText(c.document())}
}
func (c *AgentConfig) version(v *rpc.PublicationVersion) {
	v.Agent = c.rpc()
	v.Yaml = yamlText(c.document())
}
func (c *AgentConfig) document() map[string]any {
	return map[string]any{"apiVersion": "monitoring.coreos.com/v1alpha1", "kind": "PrometheusAgent", "metadata": map[string]any{"name": c.Name, "namespace": c.Namespace}, "spec": c.spec()}
}
func (c *AgentConfig) warnings(cr map[string]any) []string {
	return []string{"关联匹配和CR就绪不证明实际采集或remote_write样本交付"}
}

var prometheusDurationRE = regexp.MustCompile(`^(0|([0-9]+y)?([0-9]+w)?([0-9]+d)?([0-9]+h)?([0-9]+m)?([0-9]+s)?([0-9]+ms)?)$`)

func (c *AgentConfig) validate() error {
	if len(validation.IsDNS1123Label(c.Name)) > 0 || len("prom-agent-"+c.Name+"-db") > 63 {
		return fieldError("agent.name", "名称须满足Agent派生资源DNS label约束，单shard最多49字符")
	}
	if len(validation.IsDNS1123Label(c.Namespace)) > 0 {
		return fieldError("agent.namespace", "请选择合法既有namespace")
	}
	if c.Shards < 1 {
		return fieldError("agent.shards", "必须为正整数")
	}
	if c.Shards > 1 {
		return fieldError("agent.shards", "多shard生命周期尚未开放")
	}
	if c.DesiredState != "Running" {
		return fieldError("agent.desiredState", "当前只支持运行；挂起需先实现实际存储策略保护")
	}
	if e := c.Resources.validate("agent.resources"); e != nil {
		return e
	}
	if c.Storage.Mode != "PVC" && c.Storage.Mode != "emptyDir" {
		return fieldError("agent.storage.mode", "只支持PVC或emptyDir")
	}
	if c.Storage.Mode == "PVC" {
		if c.Storage.Capacity == nil {
			return fieldError("agent.storage.capacity", "PVC容量必填")
		}
		q, e := resource.ParseQuantity(*c.Storage.Capacity)
		if e != nil || q.Sign() <= 0 {
			return fieldError("agent.storage.capacity", "PVC容量必须为正quantity")
		}
	} else if c.Storage.Capacity != nil {
		return fieldError("agent.storage.capacity", "emptyDir不设置PVC容量")
	}

	for _, p := range []string{c.Storage.WhenScaled, c.Storage.WhenDeleted} {
		if p != "Retain" && p != "Delete" {
			return fieldError("agent.storage", "保留策略必须为Retain或Delete")
		}
	}
	if len(validation.IsDNS1123Subdomain(c.ServiceAccountName)) > 0 {
		return fieldError("agent.serviceAccountName", "请选择合法既有ServiceAccount")
	}
	for kind, r := range c.associationRules() {
		if e := r.Object.validate("None", "agent.associations."+kind+".object"); e != nil {
			return e
		}
		if e := r.Namespace.validate("Own", "agent.associations."+kind+".namespace"); e != nil {
			return e
		}
	}
	if len(c.RemoteWrite) == 0 {
		return fieldError("agent.remoteWrite", "至少配置一个endpoint")
	}
	names := map[string]bool{}
	for n, r := range c.RemoteWrite {
		_ = n
		u, e := url.Parse(r.URL)
		if e != nil || u.Hostname() == "" || u.Scheme != "http" && u.Scheme != "https" || u.User != nil || u.Fragment != "" {
			return fieldError("agent.remoteWrite.url", "须为不含内嵌认证的HTTP(S) URL")
		}
		if r.Name != nil {
			if *r.Name == "" || names[*r.Name] {
				return fieldError("agent.remoteWrite.name", "可选name须非空且唯一")
			}
			names[*r.Name] = true
		}
		if r.RemoteTimeout != nil && (*r.RemoteTimeout == "" || !validPrometheusDuration(*r.RemoteTimeout)) {
			return fieldError("agent.remoteWrite.remoteTimeout", "须为合法Prometheus duration")
		}
		switch r.Auth.Mode {
		case "None":
			if r.Auth.Username != nil || r.Auth.Password != nil || r.Auth.Credentials != nil {
				return fieldError("agent.remoteWrite.auth", "None不能携带认证参数")
			}
		case "BasicAuth":
			if r.Auth.Username == nil || r.Auth.Password == nil || r.Auth.Credentials != nil {
				return fieldError("agent.remoteWrite.auth", "BasicAuth须成对username/password引用且认证互斥")
			}
		case "Bearer":
			if r.Auth.Credentials == nil || r.Auth.Username != nil || r.Auth.Password != nil {
				return fieldError("agent.remoteWrite.auth", "Bearer须credentials引用且认证互斥")
			}
		default:
			return fieldError("agent.remoteWrite.auth.mode", "缺失或未知认证模式")
		}
		if r.TLS != nil {
			if (r.TLS.Cert == nil) != (r.TLS.Key == nil) {
				return fieldError("agent.remoteWrite.tls", "客户端证书和私钥必须配对")
			}
			if r.TLS.ServerName != nil && *r.TLS.ServerName == "" {
				return fieldError("agent.remoteWrite.tls.serverName", "serverName不能是空字符串")
			}
		}
		for _, ref := range r.references() {
			if len(validation.IsDNS1123Subdomain(ref.Name)) > 0 || len(validation.IsConfigMapKey(ref.Key)) > 0 {
				return fieldError("agent.remoteWrite.secretRef", "Secret name/key无效")
			}
		}
	}
	return nil
}
func (c *AgentConfig) associationRules() map[string]AssociationRule {
	return map[string]AssociationRule{"ServiceMonitor": c.Associations.ServiceMonitor, "PodMonitor": c.Associations.PodMonitor, "Probe": c.Associations.Probe, "ScrapeConfig": c.Associations.ScrapeConfig}
}
func (r SelectorRule) validate(empty, path string) error {
	switch r.Mode {
	case empty, "All":
		if r.Selector != nil {
			return fieldError(path, "当前模式不能携带标签参数")
		}
		return nil
	case "Labels":
		if r.Selector == nil {
			return fieldError(path, "Labels必须显式提供selector")
		}
	default:
		return fieldError(path+".mode", "缺失或未知选择模式")
	}
	m := r.Selector.meta()
	if _, e := metav1.LabelSelectorAsSelector(m); e != nil {
		return fieldError(path, "非法LabelSelector组合")
	}
	for k, v := range m.MatchLabels {
		if len(validation.IsQualifiedName(k)) > 0 || len(validation.IsValidLabelValue(v)) > 0 {
			return fieldError(path, "非法标签名称或值")
		}
	}
	for _, x := range m.MatchExpressions {
		if len(validation.IsQualifiedName(x.Key)) > 0 {
			return fieldError(path, "非法标签名称")
		}
		for _, v := range x.Values {
			if len(validation.IsValidLabelValue(v)) > 0 {
				return fieldError(path, "非法标签值")
			}
		}
	}
	return nil
}
func (s *AgentLabelSelector) meta() *metav1.LabelSelector {
	m := &metav1.LabelSelector{MatchLabels: s.MatchLabels}
	for _, e := range s.MatchExpressions {
		m.MatchExpressions = append(m.MatchExpressions, metav1.LabelSelectorRequirement{Key: e.Key, Operator: metav1.LabelSelectorOperator(e.Operator), Values: e.Values})
	}
	return m
}
func (r SelectorRule) cr() map[string]any {
	if r.Mode == "None" || r.Mode == "Own" {
		return nil
	}
	m := map[string]any{}
	if r.Mode == "All" {
		return m
	}
	if r.Selector.MatchLabels != nil {
		m["matchLabels"] = r.Selector.MatchLabels
	}
	if r.Selector.MatchExpressions != nil {
		// 关键：CRD会裁剪非nullable的null；仅CR投影省略nil值，显式[]及typed/RPC/DB presence保持。
		expressions := make([]map[string]any, 0, len(r.Selector.MatchExpressions))
		for _, expression := range r.Selector.MatchExpressions {
			value := map[string]any{"key": expression.Key, "operator": expression.Operator}
			if expression.Values != nil {
				value["values"] = expression.Values
			}
			expressions = append(expressions, value)
		}
		m["matchExpressions"] = expressions
	}
	return m
}
func (r RemoteEndpoint) references() []*SecretReference {
	refs := []*SecretReference{}
	for _, ref := range []*SecretReference{r.Auth.Username, r.Auth.Password, r.Auth.Credentials} {
		if ref != nil {
			refs = append(refs, ref)
		}
	}
	if r.TLS != nil {
		for _, ref := range []*SecretReference{r.TLS.CA, r.TLS.Cert, r.TLS.Key} {
			if ref != nil {
				refs = append(refs, ref)
			}
		}
	}
	return refs
}
func (r *SecretReference) cr() map[string]any { return map[string]any{"name": r.Name, "key": r.Key} }
func (c *AgentConfig) spec() map[string]any {
	spec := map[string]any{"mode": "StatefulSet", "version": "v2.55.1", "image": "quay.io/prometheus/prometheus@sha256:2659f4c2ebb718e7695cb9b25ffa7d6be64db013daba13e05c875451cf51b0d3", "shards": c.Shards, "replicas": 1, "paused": false, "serviceAccountName": c.ServiceAccountName, "resources": c.Resources.cr(), "storage": map[string]any{"emptyDir": map[string]any{}}}
	if c.Storage.Mode == "PVC" {
		spec["storage"] = map[string]any{"volumeClaimTemplate": map[string]any{"spec": map[string]any{"accessModes": []string{"ReadWriteOnce"}, "resources": map[string]any{"requests": map[string]any{"storage": *c.Storage.Capacity}}}}}
		spec["persistentVolumeClaimRetentionPolicy"] = map[string]any{"whenScaled": c.Storage.WhenScaled, "whenDeleted": c.Storage.WhenDeleted}
	}
	for kind, rule := range c.associationRules() {
		prefix := strings.ToLower(kind[:1]) + kind[1:]
		if x := rule.Object.cr(); x != nil {
			spec[prefix+"Selector"] = x
		}
		if x := rule.Namespace.cr(); x != nil {
			spec[prefix+"NamespaceSelector"] = x
		}
	}
	endpoints := []any{}
	for _, r := range c.RemoteWrite {
		m := map[string]any{"url": r.URL}
		if r.Name != nil {
			m["name"] = *r.Name
		}
		if r.RemoteTimeout != nil {
			m["remoteTimeout"] = *r.RemoteTimeout
		}
		if r.Auth.Mode == "BasicAuth" {
			m["basicAuth"] = map[string]any{"username": r.Auth.Username.cr(), "password": r.Auth.Password.cr()}
		}
		if r.Auth.Mode == "Bearer" {
			m["authorization"] = map[string]any{"type": "Bearer", "credentials": r.Auth.Credentials.cr()}
		}
		if r.TLS != nil {
			tls := map[string]any{}
			if r.TLS.CA != nil {
				tls["ca"] = map[string]any{"secret": r.TLS.CA.cr()}
			}
			if r.TLS.Cert != nil {
				tls["cert"] = map[string]any{"secret": r.TLS.Cert.cr()}
				tls["keySecret"] = r.TLS.Key.cr()
			}
			if r.TLS.ServerName != nil {
				tls["serverName"] = *r.TLS.ServerName
			}
			m["tlsConfig"] = tls
		}
		endpoints = append(endpoints, m)
	}
	spec["remoteWrite"] = endpoints
	return spec
}

// sameAgentStorage按quantity语义锁定容量，来源为已确认版本，不跟可变草稿比较。
func sameAgentStorage(a, b *AgentConfig) bool {
	if a.Storage.Mode != b.Storage.Mode || a.Namespace != b.Namespace || a.Name != b.Name {
		return false
	}
	if a.Storage.Capacity == nil || b.Storage.Capacity == nil {
		return a.Storage.Capacity == nil && b.Storage.Capacity == nil
	}
	x, e := resource.ParseQuantity(*a.Storage.Capacity)
	if e != nil {
		return false
	}
	y, e := resource.ParseQuantity(*b.Storage.Capacity)
	return e == nil && x.Cmp(y) == 0
}

// validPrometheusDuration保留y/w/d语法并逐段检查乘加溢出，与Prometheus的time.Duration范围一致。
func validPrometheusDuration(value string) bool {
	if !prometheusDurationRE.MatchString(value) || value == "" {
		return false
	}
	if value == "0" {
		return true
	}
	units := map[string]uint64{"ms": uint64(time.Millisecond), "s": uint64(time.Second), "m": uint64(time.Minute), "h": uint64(time.Hour), "d": uint64(24 * time.Hour), "w": uint64(7 * 24 * time.Hour), "y": uint64(365 * 24 * time.Hour)}
	parts := regexp.MustCompile(`([0-9]+)(ms|[ywdhms])`).FindAllStringSubmatch(value, -1)
	total := uint64(0)
	for _, part := range parts {
		number, e := strconv.ParseUint(part[1], 10, 64)
		unit := units[part[2]]
		if e != nil || unit == 0 || number > (uint64(math.MaxInt64)-total)/unit {
			return false
		}
		total += number * unit
	}
	return len(parts) > 0
}
