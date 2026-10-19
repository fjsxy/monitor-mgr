// 组件接缝只承载配置/资源差异；事务、尝试、派发和恢复始终共用一套引擎。
package control

import (
	"context"
	"encoding/json"
	"net/url"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

const KSMComponent = "kube-state-metrics"
const AgentComponent = "Prometheus Agent"

// ResourceIdentity 分开描述CR身份与工作负载部署位置，cluster-scoped CR没有namespace。
type ResourceIdentity struct{ Component, APIVersion, Kind, Scope, Namespace, Name, DeploymentNamespace string }

type configuration interface {
	identity() ResourceIdentity
	spec() map[string]any
	document() map[string]any
	draft(int64) *rpc.ConfigurationDraft
	version(*rpc.PublicationVersion)
	warnings(map[string]any) []string
}

// componentAdapter 是生命周期的唯一组件入口；后续组件不复制发布/删除执行器。
type componentAdapter struct{ component, apiVersion, kind, resource, scope string }

func componentFor(name string) componentAdapter {
	if name == AgentComponent {
		return componentAdapter{name, "monitoring.coreos.com/v1alpha1", "PrometheusAgent", "prometheusagents", "Namespaced"}
	}
	return componentAdapter{KSMComponent, ksmAPI, "KubeStateMetrics", "kubestatemetrics", "Cluster"}
}
func (a componentAdapter) collectionPath(namespace string) string {
	path := "/apis/" + a.apiVersion
	if a.scope == "Namespaced" && namespace != "" {
		path += "/namespaces/" + url.PathEscape(namespace)
	}
	return path + "/" + a.resource
}
func (a componentAdapter) objectPath(i *rpc.ObservationInstance) string {
	return a.collectionPath(instanceCRNamespace(i)) + "/" + url.PathEscape(i.Name)
}
func instanceCRNamespace(i *rpc.ObservationInstance) string {
	if i.Component == AgentComponent {
		return i.TargetNamespace
	}
	return ""
}
func (a componentAdapter) normalizeSpec(spec map[string]any) string {
	if a.component == AgentComponent {
		var m map[string]any
		json.Unmarshal([]byte(canonical(spec)), &m)
		if m == nil {
			m = map[string]any{}
		}
		// 固定v0.83.0 CRD默认portName/scrapeInterval；只补合法默认，绝不合并selector nil与{}。
		for k, v := range map[string]any{"mode": "StatefulSet", "replicas": 1, "shards": 1, "paused": false, "portName": "web", "scrapeInterval": "30s"} {
			if _, ok := m[k]; !ok {
				m[k] = v
			}
		}
		return canonical(m)
	}
	return normalizedSpec(spec)
}
func (a componentAdapter) interpret(cr map[string]any, now string) *rpc.InstanceRuntime {
	if a.component == AgentComponent {
		return interpretAgent(cr, now)
	}
	return interpretKSM(cr, now)
}
func (t *Target) readComponent(ctx context.Context, i *rpc.ObservationInstance) (map[string]any, error) {
	var out map[string]any
	e := t.Request(ctx, "GET", componentFor(i.Component).objectPath(i), nil, &out)
	if apiCode(e) == "TARGET_NOT_FOUND" {
		return nil, nil
	}
	return out, e
}
func (c *KSMConfig) identity() ResourceIdentity {
	return ResourceIdentity{KSMComponent, ksmAPI, "KubeStateMetrics", "Cluster", "", c.Name, c.TargetNamespace}
}
func (c *KSMConfig) document() map[string]any { return ksmCR(c.Name, c.spec()) }
func (c *KSMConfig) draft(rev int64) *rpc.ConfigurationDraft {
	return &rpc.ConfigurationDraft{Revision: rev, Ksm: c.rpc(), Yaml: yamlText(c.document())}
}
func (c *KSMConfig) version(v *rpc.PublicationVersion) {
	v.Ksm = c.rpc()
	v.Yaml = yamlText(c.document())
}
func (c *KSMConfig) warnings(cr map[string]any) []string {
	w := []string{}
	if c.DeploymentMode != "Deployment" {
		w = append(w, "experimental 模式；切换会重建 workload 并出现采集空窗")
	}
	if cr != nil && objectString(objectMap(objectMap(cr, "spec"), "deployment"), "mode") != c.DeploymentMode {
		w = append(w, "模式切换由 Operator 清理旧 workload 后建立新 workload，不承诺无损")
	}
	if c.NamespaceFilter.Mode == "Include" && len(c.NamespaceFilter.Names) == 0 {
		w = append(w, "正则可能零匹配，零匹配会停止全部 workload，不会自动改为 All")
	}
	return w
}
func decodeStoredConfiguration(component string, raw []byte) (configuration, error) {
	if component == AgentComponent {
		var c AgentConfig
		if json.Unmarshal(raw, &c) != nil {
			return nil, Error("UNAVAILABLE", "Agent来源配置不可读")
		}
		return &c, nil
	}
	var cfg KSMConfig
	if component != KSMComponent || json.Unmarshal(raw, &cfg) != nil {
		return nil, Error("UNAVAILABLE", "类型化来源配置不可读")
	}
	return &cfg, nil
}
func decodeVersionConfiguration(component string, v *rpc.PublicationVersion) (configuration, error) {
	if component == AgentComponent {
		return decodeAgentWithNamePolicy(v.Agent, false)
	}
	return decodeKSM(v.Ksm)
}

// decodeRequestConfiguration先校验组件分支互斥，避免字段默认为另一组件或泄入任意CR。
func decodeRequestConfiguration(component string, ksm *rpc.KSMConfiguration, agent *rpc.AgentConfiguration) (configuration, error) {
	if component == KSMComponent {
		if agent != nil {
			return nil, fieldError("agent", "组件与配置分支不符")
		}
		return decodeKSM(ksm)
	}
	if component == AgentComponent {
		if ksm != nil {
			return nil, fieldError("ksm", "组件与配置分支不符")
		}
		return decodeAgent(agent)
	}
	return nil, fieldError("component", "不支持的组件")
}
