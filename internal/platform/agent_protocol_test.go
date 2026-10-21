package platform_test

import (
	"context"
	"encoding/json"
	"errors"
	"strings"

	"github.com/cloudwego/gopkg/protocol/thrift"
	"github.com/cloudwego/kitex/client"
	"github.com/fjsxy/monitor-mgr/internal/teststeps"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"github.com/fjsxy/monitor-mgr/kitex_gen/control/controlservice"
	ginkgo "github.com/onsi/ginkgo/v2"
)

func agentRPCConfig(t ginkgo.GinkgoTInterface, value map[string]any) *rpc.AgentConfiguration {
	t.Helper()
	raw, e := json.Marshal(value)
	if e != nil {
		t.Fatal(e)
	}
	c := new(rpc.AgentConfiguration)
	if json.Unmarshal(raw, c) != nil {
		t.Fatal("测试typed配置无效")
	}
	return c
}
func requireAgentFieldError(t ginkgo.GinkgoTInterface, e error) {
	t.Helper()
	var a *rpc.APIError
	if !errors.As(e, &a) || a.Code != "INVALID_INPUT" || len(a.FieldErrors) == 0 {
		t.Fatalf("缺少稳定字段错误：%v", e)
	}
	raw, _ := json.Marshal(a)
	if strings.Contains(string(raw), "unknown-sensitive-value") {
		t.Fatal("错误暴露未知字段值")
	}
}

// F01每个子例经过锁定FastCodec及实际generated client→Kitex，不用JSON模拟Thrift往返。
var _ = ginkgo.It("TestF01Agent", func() {
	t := ginkgo.GinkgoT()
	// 接下来F01每个子例经过锁定FastCodec及实际generated client→Kitex，不用JSON模拟Thrift往返。
	ginkgo.By("F01每个子例经过锁定FastCodec及实际generated client→Kitex，不用JSON模拟Thrift往返。")

	for _, variant := range []string{"absent", "empty-object", "empty-list", "unknown-mode"} {
		teststeps.Step(variant, func() {
			p, c, cluster := ksmCluster(t)
			cli, e := controlservice.NewClient("agent-protocol", client.WithHostPorts(p.RPCAddress()))
			if e != nil {
				t.Fatal(e)
			}
			key, token := "protocol-"+variant, c.Value
			ctx := &rpc.RequestContext{RequestId: key, SessionToken: &token, IdempotencyKey: &key}
			request := &rpc.CreateInstanceRequest{ClusterId: cluster, Component: "Prometheus Agent"}
			if variant == "absent" {
				raw := thrift.FastMarshal(request)
				decoded := new(rpc.CreateInstanceRequest)
				if _, e = decoded.FastRead(raw); e != nil || decoded.Agent != nil {
					t.Fatal("optional absent变为对象")
				}
				_, e = cli.CreateInstance(context.Background(), ctx, decoded)
				requireAgentFieldError(t, e)
				return
			}
			value := agentConfig("protocol", "monitoring")
			rules := value["associations"].(map[string]any)
			rule := rules["serviceMonitor"].(map[string]any)
			switch variant {
			case "empty-object":
				rule["object"] = map[string]any{"mode": "All"}
			case "empty-list":
				rule["object"] = map[string]any{"mode": "Labels", "selector": map[string]any{"matchExpressions": []any{map[string]any{"key": "enabled", "operator": "Exists", "values": []string{}}}}}
			case "unknown-mode":
				rule["object"] = map[string]any{"mode": "Unknown"}
			}
			request.Agent = agentRPCConfig(t, value)
			decoded := new(rpc.CreateInstanceRequest)
			if _, e = decoded.FastRead(thrift.FastMarshal(request)); e != nil {
				t.Fatal(e)
			}
			created, e := cli.CreateInstance(context.Background(), ctx, decoded)
			if variant == "unknown-mode" {
				requireAgentFieldError(t, e)
				request.Agent.Associations.ServiceMonitor.Object.Mode = ""
				_, e = cli.CreateInstance(context.Background(), ctx, request)
				requireAgentFieldError(t, e)
				return
			}
			if e != nil {
				t.Fatal(e)
			}
			draft, e := cli.GetDraft(context.Background(), ctx, &rpc.InstanceID{Id: created.Id})
			if e != nil {
				t.Fatal(e)
			}
			if variant == "empty-object" {
				if !strings.Contains(draft.Yaml, "serviceMonitorSelector: {}") || strings.Contains(draft.Yaml, "podMonitorSelector:") {
					t.Fatal("all空对象与None省略未保持")
				}
			}
			if variant == "empty-list" {
				values := draft.Agent.Associations.ServiceMonitor.Object.Selector.MatchExpressions[0].Values
				if values == nil || len(values) != 0 {
					t.Fatal("显式空集合经RPC/DB往返丢失")
				}
				if !strings.Contains(draft.Yaml, "values: []") {
					t.Fatal("空集合到CR/YAML丢失")
				}
			}
		})
	}
})

// F03通过HTTP与直接RPC验证不同非法输入，拒绝发生在任何CR写入前。
var _ = ginkgo.It("TestF03Agent", func() {
	t := ginkgo.GinkgoT()
	// 接下来F03通过HTTP与直接RPC验证不同非法输入，拒绝发生在任何CR写入前。
	ginkgo.By("F03通过HTTP与直接RPC验证不同非法输入，拒绝发生在任何CR写入前。")

	cases := []struct {
		name   string
		change func(map[string]any)
	}{
		{"selector", func(v map[string]any) {
			v["associations"].(map[string]any)["serviceMonitor"].(map[string]any)["object"] = map[string]any{"mode": "Labels", "selector": map[string]any{"matchLabels": map[string]string{"bad key": "value"}}}
		}},
		{"selector-mode", func(v map[string]any) {
			v["associations"].(map[string]any)["podMonitor"].(map[string]any)["object"] = map[string]any{"mode": "None", "selector": map[string]any{}}
		}},
		{"url", func(v map[string]any) {
			v["remoteWrite"].([]any)[0].(map[string]any)["url"] = "ftp://example.invalid/secret"
		}},
		{"zero-endpoints", func(v map[string]any) { v["remoteWrite"] = []any{} }},
		{"duplicate-name", func(v map[string]any) {
			v["remoteWrite"] = []any{map[string]any{"url": "http://example.invalid/a", "name": "same", "auth": map[string]any{"mode": "None"}}, map[string]any{"url": "https://example.invalid/b", "name": "same", "auth": map[string]any{"mode": "None"}}}
		}},
		{"tls-pair", func(v map[string]any) {
			v["remoteWrite"].([]any)[0].(map[string]any)["tls"] = map[string]any{"cert": map[string]any{"name": "certs", "key": "tls.crt"}}
		}},
		{"auth-pair", func(v map[string]any) {
			v["remoteWrite"].([]any)[0].(map[string]any)["auth"] = map[string]any{"mode": "BasicAuth", "username": map[string]any{"name": "auth", "key": "user"}}
		}},
	}
	for _, test := range cases {
		teststeps.Step(test.name, func() {
			p, c, cluster := ksmCluster(t)
			value := agentConfig("invalid", "monitoring")
			test.change(value)
			result := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": value}, c, "http-invalid", 422)
			if result["code"] != "INVALID_INPUT" || len(result["fieldErrors"].(map[string]any)) == 0 {
				t.Fatal("HTTP字段拒绝无稳定错误")
			}
			cli, e := controlservice.NewClient("agent-invalid", client.WithHostPorts(p.RPCAddress()))
			if e != nil {
				t.Fatal(e)
			}
			key, token := "rpc-invalid", c.Value
			request := &rpc.CreateInstanceRequest{ClusterId: cluster, Component: "Prometheus Agent", Agent: agentRPCConfig(t, value)}
			_, e = cli.CreateInstance(context.Background(), &rpc.RequestContext{RequestId: key, SessionToken: &token, IdempotencyKey: &key}, request)
			requireAgentFieldError(t, e)
			list := clusterCall(t, p, "GET", "/clusters/"+cluster+"/instances", nil, c, "", 200)["instances"].([]any)
			if len(list) != 0 {
				t.Fatal("非法配置产生实例或写入")
			}
		})
	}
	teststeps.Step("unknown-wire", func() {
		p, c, cluster := ksmCluster(t)
		cli, e := controlservice.NewClient("agent-unknown", client.WithHostPorts(p.RPCAddress()))
		if e != nil {
			t.Fatal(e)
		}
		key, token := "unknown", c.Value
		request := &rpc.CreateInstanceRequest{ClusterId: cluster, Component: "Prometheus Agent", Agent: agentRPCConfig(t, agentConfig("unknown", "monitoring"))}
		injectUnknownWireField(t, request.Agent.RemoteWrite[0].Auth)
		_, e = cli.CreateInstance(context.Background(), &rpc.RequestContext{RequestId: key, SessionToken: &token, IdempotencyKey: &key}, request)
		requireAgentFieldError(t, e)
	})
})

// REG18-VALID-006：CRD格式之外还需校验Prometheus duration可表示范围。
var _ = ginkgo.It("TestAgentDurationRange", func() {
	t := ginkgo.GinkgoT()
	// 接下来REG18-VALID-006：CRD格式之外还需校验Prometheus duration可表示范围。
	ginkgo.By("REG18-VALID-006：CRD格式之外还需校验Prometheus duration可表示范围。")

	for _, duration := range []string{"1y2w3d4h5m6s7ms", "1m1d", "1s1s", "999999999999999999999999y"} {
		teststeps.Step(duration, func() {
			p, c, cluster := ksmCluster(t)
			value := agentConfig("duration", "monitoring")
			value["remoteWrite"].([]any)[0].(map[string]any)["remoteTimeout"] = duration
			status := 422
			if duration == "1y2w3d4h5m6s7ms" {
				status = 201
			}
			clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": value}, c, "duration-http", status)
			cli, e := controlservice.NewClient("duration-rpc", client.WithHostPorts(p.RPCAddress()))
			if e != nil {
				t.Fatal(e)
			}
			key, token := "duration-rpc", c.Value
			_, e = cli.CreateInstance(context.Background(), &rpc.RequestContext{RequestId: key, SessionToken: &token, IdempotencyKey: &key}, &rpc.CreateInstanceRequest{ClusterId: cluster, Component: "Prometheus Agent", Agent: agentRPCConfig(t, value)})
			if status == 422 {
				requireAgentFieldError(t, e)
			} else if e != nil {
				var apiError *rpc.APIError
				if !errors.As(e, &apiError) || apiError.Code != "CONFLICT" {
					t.Fatal("合法Prometheus duration被拒绝")
				}
			}
		})
	}
})
