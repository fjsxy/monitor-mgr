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
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestF01Agent(t ginkgo.GinkgoTInterface) {
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
}

// F03通过HTTP与直接RPC验证不同非法输入，拒绝发生在任何CR写入前。

// REG18-VALID-006：CRD格式之外还需校验Prometheus duration可表示范围。
