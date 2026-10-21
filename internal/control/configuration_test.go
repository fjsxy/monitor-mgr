// 配置 UT 直接调用领域规则，避免每个映射输入重复启动 HTTP、Kitex 和数据库。
package control

import (
	"encoding/json"
	"reflect"
	"strings"

	ginkgo "github.com/onsi/ginkgo/v2"
)

func textPointer(value string) *string { return &value }

var _ = ginkgo.It("UT-05 KSM 配置规则与映射", func() {
	t := ginkgo.GinkgoT()
	// 接下来用三个异构配置覆盖模式、collector 和指标过滤的独立分支。
	for n, mode := range []string{"Deployment", "StatefulSetSharded", "DaemonSetSharded"} {
		ginkgo.By("校验并映射 " + mode)
		c := KSMConfig{Name: "metrics", TargetNamespace: "monitoring", DeploymentMode: mode, DesiredState: "Running", Resources: Resources{RequestCPU: textPointer("100m"), RequestMemory: textPointer("250Mi"), LimitMemory: textPointer("500Mi")}, Collectors: Selection{Mode: "Default"}, MetricFilter: MetricSelection{Mode: "All"}, NamespaceFilter: NamespaceSelection{Mode: "All"}}
		if n == 1 {
			shards := int32(2)
			c.Shards = &shards
		}
		if n > 0 {
			c.Collectors = Selection{Mode: "Include", Names: []string{"pods", "nodes"}}
			c.MetricFilter = MetricSelection{Mode: []string{"", "Allow", "Deny"}[n], Patterns: []string{"kube_.*"}}
		}
		if n == 2 {
			c.AdvancedResources = true
			c.NodeResources = &RoleResources{Mode: "Override", Resources: &Resources{RequestCPU: textPointer("110m")}}
			c.ClusterResources = &RoleResources{Mode: "Override", Resources: &Resources{RequestCPU: textPointer("120m")}}
			c.UnscheduledResources = &RoleResources{Mode: "Override", Resources: &Resources{RequestCPU: textPointer("130m")}}
		}
		if e := c.validate(); e != nil {
			t.Fatal(e)
		}
		spec := c.spec()
		deployment := spec["deployment"].(map[string]any)
		if deployment["mode"] != mode || spec["collectors"].(map[string]any)["mode"] != c.Collectors.Mode || spec["metricFilter"].(map[string]any)["mode"] != c.MetricFilter.Mode {
			t.Fatal(spec)
		}
		if n == 1 && deployment["statefulSetSharded"].(map[string]any)["shards"] != int32(2) {
			t.Fatal(spec)
		}
		if n == 2 {
			ds := deployment["daemonSetSharded"].(map[string]any)
			for role, cpu := range map[string]string{"nodePodResources": "110m", "clusterPodResources": "120m", "unscheduledPodResources": "130m"} {
				if ds[role].(map[string]any)["requests"].(map[string]string)["cpu"] != cpu {
					t.Fatal(ds)
				}
			}
			c.AdvancedResources = false
			if len(c.spec()["deployment"].(map[string]any)["daemonSetSharded"].(map[string]any)) != 0 || c.NodeResources == nil {
				t.Fatal("关闭高级字段必须保留草稿但排除有效CR")
			}
		}
		// 接下来复用同一配置检查非法 quantity、上下界和正则，不能只比较状态码。
		for _, bad := range []string{"quantity", "limit", "regexp"} {
			raw, _ := json.Marshal(c)
			var invalid KSMConfig
			json.Unmarshal(raw, &invalid)
			switch bad {
			case "quantity":
				invalid.Resources.RequestCPU = textPointer("bad")
			case "limit":
				invalid.Resources.LimitMemory = textPointer("1Mi")
			case "regexp":
				invalid.MetricFilter = MetricSelection{Mode: "Allow", Patterns: []string{"["}}
			}
			if e := invalid.validate(); e == nil {
				t.Fatal("非法输入未拒绝", bad)
			}
		}
	}
	// 关键：未知 wire 字段递归拒绝；空容器与 nil 则必须可区分并允许。
	checkTestUnknownFieldGuardContainersAndNil(t)
})

var _ = ginkgo.It("UT-06 Agent selector 与发送配置", func() {
	t := ginkgo.GinkgoT()
	selector := &AgentLabelSelector{MatchLabels: map[string]string{"team": "obs"}}
	// 接下来一次验证公共 object/namespace 规则的 9 组，不按四种采集 CR 重复部署。
	for _, object := range []string{"None", "All", "Labels"} {
		for _, namespace := range []string{"Own", "All", "Labels"} {
			ginkgo.By("object=" + object + " namespace=" + namespace)
			obj, ns := SelectorRule{Mode: object}, SelectorRule{Mode: namespace}
			if object == "Labels" {
				obj.Selector = selector
			}
			if namespace == "Labels" {
				ns.Selector = selector
			}
			if obj.validate("None", "object") != nil || ns.validate("Own", "namespace") != nil {
				t.Fatal(obj, ns)
			}
			for _, rule := range []SelectorRule{obj, ns} {
				v := rule.cr()
				if (rule.Mode == "None" || rule.Mode == "Own") != (v == nil) {
					t.Fatal(rule, v)
				}
				if rule.Mode == "All" && len(v) != 0 {
					t.Fatal(v)
				}
				if rule.Mode == "Labels" && !reflect.DeepEqual(v["matchLabels"], selector.MatchLabels) {
					t.Fatal(v)
				}
			}
		}
	}
	for _, operator := range []string{"Exists", "DoesNotExist"} {
		for _, values := range [][]string{nil, {}} {
			ginkgo.By("保持 " + operator + " 的 nil/空集合")
			rule := SelectorRule{Mode: "Labels", Selector: &AgentLabelSelector{MatchExpressions: []AgentLabelExpression{{Key: "team", Operator: operator, Values: values}}}}
			if e := rule.validate("None", "object"); e != nil {
				t.Fatal(e)
			}
			projected := rule.cr()["matchExpressions"].([]map[string]any)[0]
			_, present := projected["values"]
			if present != (values != nil) {
				t.Fatal(projected)
			}
		}
	}
	bad := SelectorRule{Mode: "Labels", Selector: &AgentLabelSelector{MatchExpressions: []AgentLabelExpression{{Key: "team", Operator: "In"}}}}
	if bad.validate("None", "object") == nil {
		t.Fatal("In 的空 values 未拒绝")
	}
	rule := AssociationRule{Object: SelectorRule{Mode: "None"}, Namespace: SelectorRule{Mode: "Own"}}
	ref := func(key string) *SecretReference { return &SecretReference{Name: "agent-auth", Key: key} }
	c := AgentConfig{Name: "agent", Namespace: "monitoring", DesiredState: "Running", Shards: 1, Resources: Resources{RequestCPU: textPointer("200m")}, Storage: AgentStorage{Mode: "PVC", Capacity: textPointer("1Gi"), WhenScaled: "Retain", WhenDeleted: "Retain"}, ServiceAccountName: "agent", Associations: AgentAssociations{rule, rule, rule, rule}, RemoteWrite: []RemoteEndpoint{
		{URL: "https://receiver.invalid/none", Auth: RemoteAuth{Mode: "None"}},
		{URL: "https://receiver.invalid/basic", Auth: RemoteAuth{Mode: "BasicAuth", Username: ref("username"), Password: ref("password")}},
		{URL: "https://receiver.invalid/bearer", Auth: RemoteAuth{Mode: "Bearer", Credentials: ref("credentials")}, TLS: &AgentTLS{CA: ref("ca"), Cert: ref("cert"), Key: ref("key")}},
	}}
	ginkgo.By("映射三种认证和同 namespace Secret 引用")
	if e := c.validate(); e != nil {
		t.Fatal(e)
	}
	spec := c.spec()
	endpoints := spec["remoteWrite"].([]any)
	if len(endpoints) != 3 || endpoints[1].(map[string]any)["basicAuth"] == nil || endpoints[2].(map[string]any)["authorization"] == nil {
		t.Fatal(spec)
	}
	raw, _ := json.Marshal(spec)
	if strings.Contains(string(raw), "secretValue") {
		t.Fatal("CR 不得携带 Secret 内容")
	}
	c.RemoteWrite[2].TLS.Key = nil
	if c.validate() == nil {
		t.Fatal("不成对证书未拒绝")
	}
	c.RemoteWrite[2].TLS = nil
	c.RemoteWrite[0].URL = "file:///etc/passwd"
	if c.validate() == nil {
		t.Fatal("非法 URL 未拒绝")
	}
})
