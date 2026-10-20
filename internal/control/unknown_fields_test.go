package control

import (
	"errors"
	"strings"
	"testing"

	"github.com/cloudwego/gopkg/protocol/thrift"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

// 使用生成类型真实保留的未知 wire 字段，验证递归 guard 的容器边界及错误路径。
func TestUnknownFieldGuardContainersAndNil(t *testing.T) {
	unknown := &rpc.ContainerResources{}
	raw := thrift.Binary.AppendFieldBegin(nil, thrift.STRING, 32000)
	raw = thrift.Binary.AppendString(raw, "sensitive-unknown-value")
	raw = thrift.Binary.AppendFieldStop(raw)
	if _, err := unknown.FastRead(raw); err != nil {
		t.Fatal(err)
	}
	type envelope struct {
		Requests []*rpc.CreateInstanceRequest `json:"requests"`
		Values   map[string]any               `json:"values"`
		hidden   *rpc.ContainerResources
		Ignored  *rpc.ContainerResources `json:"-"`
	}
	var nilResource *rpc.ContainerResources
	cases := []struct {
		name  string
		value any
		field string
	}{
		{"nil", nil, ""},
		{"nil-pointer", nilResource, ""},
		{"nil-containers-and-private-fields", &envelope{Requests: []*rpc.CreateInstanceRequest{nil}, Values: map[string]any{"nil": nilResource}, hidden: unknown, Ignored: unknown}, ""},
		{"list", &envelope{Requests: []*rpc.CreateInstanceRequest{nil, {Ksm: &rpc.KSMConfiguration{Resources: unknown}}}}, "request.requests[1].ksm.resources"},
		{"array", [2]*rpc.ContainerResources{nil, unknown}, "request[1]"},
		{"map-value", &envelope{Values: map[string]any{"sensitive-key": unknown}}, "request.values[*]"},
		{"map-key", map[*rpc.ContainerResources]string{unknown: "sensitive-value"}, "request[*]"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			err := rejectUnknownFields(tc.value, "request")
			if tc.field == "" {
				if err != nil {
					t.Fatalf("合法 nil/私有字段被拒绝：%v", err)
				}
				return
			}
			var apiError *rpc.APIError
			if !errors.As(err, &apiError) || apiError.Code != "INVALID_INPUT" || apiError.FieldErrors[tc.field] == "" {
				t.Fatalf("递归边界未拒绝未知字段 %q：%v", tc.field, err)
			}
			if strings.Contains(apiError.Message, "sensitive") {
				t.Fatal("错误路径泄漏未知字段值或 Map key")
			}
		})
	}
}
