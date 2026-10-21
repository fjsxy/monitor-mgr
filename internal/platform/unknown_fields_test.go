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

// 注入实际未知 Thrift wire 字段，再由生成的 FastRead 解码；不经过会丢弃未知字段的 JSON 转换。
func injectUnknownWireField(t ginkgo.GinkgoTInterface, value thrift.FastCodec) {
	t.Helper()
	raw := thrift.FastMarshal(value)
	if len(raw) == 0 || raw[len(raw)-1] != byte(thrift.STOP) {
		t.Fatal("前置结构缺少 Thrift STOP")
	}
	raw = thrift.Binary.AppendFieldBegin(raw[:len(raw)-1], thrift.STRING, 32000)
	raw = thrift.Binary.AppendString(raw, "unknown-sensitive-value")
	raw = thrift.Binary.AppendFieldStop(raw)
	if _, err := value.FastRead(raw); err != nil {
		t.Fatal(err)
	}
}

// F03 同时约束 HTTP 与真实 generated client→Kitex server，不把反序列化丢字段当作拒绝。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestF03KSMUnknownFieldsHTTPAndRPC(t ginkgo.GinkgoTInterface) {
	// 接下来F03 同时约束 HTTP 与真实 generated client→Kitex server，不把反序列化丢字段当作拒绝。
	ginkgo.By("F03 同时约束 HTTP 与真实 generated client→Kitex server，不把反序列化丢字段当作拒绝。")

	p, cookie, instanceID, target := ksmPublishedFixture(t)
	instance := clusterCall(t, p, "GET", "/instances/"+instanceID, nil, cookie, "", 200)
	clusterID := instance["clusterId"].(string)
	cli, err := controlservice.NewClient("unknown-fields-direct", client.WithHostPorts(p.RPCAddress()))
	if err != nil {
		t.Fatal(err)
	}
	key, token := "unknown-fields", cookie.Value
	callContext := &rpc.RequestContext{RequestId: "unknown-fields", SessionToken: &token, IdempotencyKey: &key}
	newRequest := func(t ginkgo.GinkgoTInterface, name string) *rpc.CreateInstanceRequest {
		t.Helper()
		raw, err := json.Marshal(ksmConfig(name))
		if err != nil {
			t.Fatal(err)
		}
		var config rpc.KSMConfiguration
		if err = json.Unmarshal(raw, &config); err != nil {
			t.Fatal(err)
		}
		return &rpc.CreateInstanceRequest{ClusterId: clusterID, Component: "kube-state-metrics", Ksm: &config}
	}
	wantInvalid := func(t ginkgo.GinkgoTInterface, err error, field string) {
		t.Helper()
		var apiError *rpc.APIError
		if !errors.As(err, &apiError) || apiError.Code != "INVALID_INPUT" || apiError.FieldErrors[field] == "" {
			t.Fatalf("未知 wire 字段未返回稳定字段级拒绝 %q：%v", field, err)
		}
		if strings.Contains(apiError.Message, "unknown-sensitive-value") {
			t.Fatal("错误泄漏未知字段值")
		}
		for _, message := range apiError.FieldErrors {
			if strings.Contains(message, "unknown-sensitive-value") {
				t.Fatal("字段错误泄漏未知字段值")
			}
		}
	}

	teststeps.Step("HTTP拒绝不支持字段与资源键", func() {
		for _, level := range []string{"request", "ksm", "resources"} {
			config := ksmConfig("unknown-http")
			body := map[string]any{"component": "kube-state-metrics", "ksm": config}
			switch level {
			case "request":
				body["rawYAML"] = "unknown-sensitive-value"
			case "ksm":
				config["image"] = "unknown-sensitive-value"
			case "resources":
				config["resources"].(map[string]any)["extendedResource"] = "unknown-sensitive-value"
			}
			result := clusterCall(t, p, "POST", "/clusters/"+clusterID+"/instances", body, cookie, "http-unknown-"+level, 400)
			if result["code"] != "BAD_REQUEST" || strings.Contains(result["message"].(string), "unknown-sensitive-value") {
				t.Fatal("HTTP 未稳定拒绝未知字段或泄漏原值")
			}
			fields, ok := result["fieldErrors"].(map[string]any)
			if !ok || fields["request"] != "JSON 格式无效或包含不支持的字段" {
				t.Fatal("HTTP 未知字段缺少稳定 fieldErrors.request")
			}
			raw, err := json.Marshal(fields)
			if err != nil || strings.Contains(string(raw), "unknown-sensitive-value") || strings.Contains(string(raw), "rawYAML") || strings.Contains(string(raw), "extendedResource") {
				t.Fatal("HTTP 字段错误泄漏未知字段名或值")
			}
		}
	})

	teststeps.Step("请求未知wire字段", func() {
		request := newRequest(t, "unknown-request")
		injectUnknownWireField(t, request)
		_, err := cli.CreateInstance(context.Background(), callContext, request)
		wantInvalid(t, err, "request")
	})
	teststeps.Step("嵌套配置未知wire字段", func() {
		request := newRequest(t, "unknown-config")
		injectUnknownWireField(t, request.Ksm)
		_, err := cli.CreateInstance(context.Background(), callContext, request)
		wantInvalid(t, err, "request.ksm")
	})
	teststeps.Step("嵌套资源未知wire字段", func() {
		request := newRequest(t, "unknown-resource")
		injectUnknownWireField(t, request.Ksm.Resources)
		_, err := cli.CreateInstance(context.Background(), callContext, request)
		wantInvalid(t, err, "request.ksm.resources")
	})
	teststeps.Step("关闭的高级资源也不能透传未知字段", func() {
		request := newRequest(t, "unknown-advanced")
		request.Ksm.NodeResources = &rpc.RoleResources{Mode: "Override", Resources: &rpc.ContainerResources{}}
		injectUnknownWireField(t, request.Ksm.NodeResources.Resources)
		_, err := cli.CreateInstance(context.Background(), callContext, request)
		wantInvalid(t, err, "request.ksm.nodeResources.resources")
	})
	teststeps.Step("当前认证优先于未知字段拒绝", func() {
		request := newRequest(t, "unknown-unauthenticated")
		injectUnknownWireField(t, request)
		unauthenticated := &rpc.RequestContext{RequestId: "unknown-unauthenticated", IdempotencyKey: &key}
		injectUnknownWireField(t, unauthenticated)
		_, err := cli.CreateInstance(context.Background(), unauthenticated, request)
		var apiError *rpc.APIError
		if !errors.As(err, &apiError) || apiError.Code != "UNAUTHENTICATED" {
			t.Fatalf("未知字段绕过了当前认证优先级：%v", err)
		}
	})
	teststeps.Step("认证上下文未知wire字段", func() {
		ctx := &rpc.RequestContext{RequestId: "unknown-context", SessionToken: &token, IdempotencyKey: &key}
		injectUnknownWireField(t, ctx)
		_, err := cli.CreateInstance(context.Background(), ctx, newRequest(t, "unknown-context"))
		wantInvalid(t, err, "context")
	})
	teststeps.Step("同键命中前仍拒绝嵌套未知字段", func() {
		replayKey := "unknown-wire-replay"
		ctx := &rpc.RequestContext{RequestId: "unknown-replay", SessionToken: &token, IdempotencyKey: &replayKey}
		created, err := cli.CreateInstance(context.Background(), ctx, newRequest(t, "known-replay"))
		if err != nil {
			t.Fatal(err)
		}
		request := newRequest(t, "known-replay")
		injectUnknownWireField(t, request.Ksm.Resources)
		_, err = cli.CreateInstance(context.Background(), ctx, request)
		wantInvalid(t, err, "request.ksm.resources")
		replayed, err := cli.CreateInstance(context.Background(), ctx, newRequest(t, "known-replay"))
		if err != nil || replayed.Id != created.Id {
			t.Fatalf("合法幂等结果被未知字段请求改写：%v", err)
		}
	})

	instances := clusterCall(t, p, "GET", "/clusters/"+clusterID+"/instances", nil, cookie, "", 200)["instances"].([]any)
	if len(instances) != 2 {
		t.Fatal("拒绝未知字段后产生了平台实例")
	}
	target.mu.Lock()
	defer target.mu.Unlock()
	if target.writes != 0 {
		t.Fatal("未知字段请求产生了集群写入")
	}
}
