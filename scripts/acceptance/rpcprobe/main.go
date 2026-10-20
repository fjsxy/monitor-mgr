// rpcprobe 是验收fixture，在平台Pod内从stdin接收短期Session；只输出安全结果，不能成为产品旁路。
package main

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/cloudwego/kitex/client"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"github.com/fjsxy/monitor-mgr/kitex_gen/control/controlservice"
	"net"
	"os"
	"strings"
	"time"
)

type input struct {
	Mode        string                `json:"mode"`
	Address     string                `json:"address"`
	InstanceIDs []string              `json:"instanceIds"`
	Markers     []string              `json:"markers"`
	Token       string                `json:"token"`
	ClusterID   string                `json:"clusterId"`
	InstanceID  string                `json:"instanceId"`
	Config      *rpc.KSMConfiguration `json:"ksm"`
}

func main() {
	var in input
	if json.NewDecoder(os.Stdin).Decode(&in) != nil {
		os.Exit(2)
	}
	address := "127.0.0.1:8888"
	if in.Mode == "projection" {
		host, _, err := net.SplitHostPort(in.Address)
		if err != nil || host != "127.0.0.1" {
			os.Exit(2)
		}
		address = in.Address
	}
	c, e := controlservice.NewClient("acceptance-direct", client.WithHostPorts(address), client.WithRPCTimeout(10*time.Second))
	if e != nil {
		os.Exit(2)
	}
	if in.Mode == "projection" {
		projectionReads(c, in)
		return
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

// projectionReads仅走生成的只读RPC；认证串通过stdin进入内存，成功与失败都只输出布尔证据。
func projectionReads(c controlservice.Client, in input) {
	ctx := &rpc.RequestContext{RequestId: "projection-direct", SessionToken: &in.Token}
	noError, safe, count := true, true, 0
	check := func(value any, err error) {
		count++
		if err != nil {
			noError = false
			return
		}
		raw, err := json.Marshal(value)
		if err != nil {
			noError = false
			return
		}
		for _, marker := range in.Markers {
			if strings.Contains(string(raw), marker) {
				safe = false
			}
		}
	}
	list, err := c.ListInstances(context.Background(), ctx, &rpc.ClusterID{Id: in.ClusterID})
	check(list, err)
	for _, id := range in.InstanceIDs {
		request := &rpc.InstanceID{Id: id}
		detail, err := c.GetInstance(context.Background(), ctx, request)
		check(detail, err)
		refreshed, err := c.RefreshInstance(context.Background(), ctx, request)
		check(refreshed, err)
		runtime, err := c.GetRuntime(context.Background(), ctx, request)
		check(runtime, err)
		versions, err := c.ListVersions(context.Background(), ctx, request)
		check(versions, err)
	}
	json.NewEncoder(os.Stdout).Encode(map[string]any{"noRPCError": noError, "noMarkerInReadModels": safe, "readCount": count})
}
