// rpcprobe 是验收fixture，在平台Pod内从stdin接收短期Session；只输出安全结果，不能成为产品旁路。
package main

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/cloudwego/kitex/client"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"github.com/fjsxy/monitor-mgr/kitex_gen/control/controlservice"
	"os"
	"time"
)

type input struct {
	Token      string                `json:"token"`
	ClusterID  string                `json:"clusterId"`
	InstanceID string                `json:"instanceId"`
	Config     *rpc.KSMConfiguration `json:"ksm"`
}

func main() {
	var in input
	if json.NewDecoder(os.Stdin).Decode(&in) != nil {
		os.Exit(2)
	}
	c, e := controlservice.NewClient("acceptance-direct", client.WithHostPorts("127.0.0.1:8888"), client.WithRPCTimeout(10*time.Second))
	if e != nil {
		os.Exit(2)
	}
	key := "direct-isolation"
	ctx := &rpc.RequestContext{RequestId: "direct-isolation", SessionToken: &in.Token, IdempotencyKey: &key}
	out := []map[string]any{}
	check := func(name string, e error) {
		code := "OK"
		if e != nil {
			var a *rpc.APIError
			if errors.As(e, &a) {
				code = a.Code
			} else {
				code = "TRANSPORT_ERROR"
			}
		}
		out = append(out, map[string]any{"method": name, "code": code})
	}
	_, e = c.ListInstances(context.Background(), ctx, &rpc.ClusterID{Id: in.ClusterID})
	check("ListInstances", e)
	_, e = c.GetInstance(context.Background(), ctx, &rpc.InstanceID{Id: in.InstanceID})
	check("GetInstance", e)
	_, e = c.GetDraft(context.Background(), ctx, &rpc.InstanceID{Id: in.InstanceID})
	check("GetDraft", e)
	_, e = c.SaveDraft(context.Background(), ctx, &rpc.SaveDraftRequest{Id: in.InstanceID, ExpectedDraftRevision: 1, Ksm: in.Config})
	check("SaveDraft", e)
	_, e = c.PublishConfiguration(context.Background(), ctx, &rpc.PublishRequest{Id: in.InstanceID, PreviewId: "forged-preview", DraftRevision: 1})
	check("PublishConfiguration", e)
	_, e = c.DeleteDraft(context.Background(), ctx, &rpc.InstanceID{Id: in.InstanceID})
	check("DeleteDraft", e)
	_, e = c.GetRuntime(context.Background(), ctx, &rpc.InstanceID{Id: in.InstanceID})
	check("GetRuntime", e)
	_, e = c.ListVersions(context.Background(), ctx, &rpc.InstanceID{Id: in.InstanceID})
	check("ListVersions", e)
	json.NewEncoder(os.Stdout).Encode(out)
}
