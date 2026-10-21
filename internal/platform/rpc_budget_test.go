package platform

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"time"

	"github.com/cloudwego/kitex/client"
	"github.com/cloudwego/kitex/pkg/remote"
	"github.com/cloudwego/kitex/transport"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"github.com/fjsxy/monitor-mgr/kitex_gen/control/controlservice"
	ginkgo "github.com/onsi/ginkgo/v2"
)

// observedBudgetMeta 只观测真实生成客户端的协议编解码边界，不替换服务端或业务规则。
type observedBudgetMeta struct {
	remote.MetaHandler
	observed chan error
}

func (h observedBudgetMeta) WriteMeta(ctx context.Context, msg remote.Message) (context.Context, error) {
	ctx, e := h.MetaHandler.WriteMeta(ctx, msg)
	h.observed <- e
	return ctx, e
}

// R06的codec边界：不足1ms时在TTHeader编码前拒绝，不能发出0代表无限预算。
func BudgetBoundaryForTest(t ginkgo.GinkgoTInterface) {
	// 接下来直接检查服务端上限与继承的更短预算，只有一个网络请求另做实际计时。
	for _, duration := range []time.Duration{0, 100000 * time.Second, 2 * time.Second} {
		parent := context.Background()
		cancelParent := func() {}
		if duration > 0 {
			parent, cancelParent = context.WithTimeout(parent, duration)
		}
		bounded, cancel := boundedRPCContext(parent)
		deadline, ok := bounded.Deadline()
		limit := 10 * time.Second
		if duration > 0 && duration < limit {
			limit = duration
		}
		remaining := time.Until(deadline)
		if !ok || remaining > limit || remaining < limit-time.Second {
			t.Fatal("服务端预算不符", duration, remaining)
		}
		cancel()
		cancelParent()
	}

	// 接下来R06的codec边界：不足1ms时在TTHeader编码前拒绝，不能发出0代表无限预算。
	ginkgo.By("R06的codec边界：不足1ms时在TTHeader编码前拒绝，不能发出0代表无限预算。")

	p, e := Start(context.Background(), Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", Logger: slog.New(slog.NewTextHandler(io.Discard, nil))})
	if e != nil {
		t.Fatal(e)
	}
	defer p.Close(context.Background())
	observed := make(chan error, 4)
	cli, e := controlservice.NewClient("workbench-small-budget", client.WithHostPorts(p.RPCAddress()), client.WithRPCTimeout(10*time.Second), client.WithTransportProtocol(transport.TTHeader), client.WithMetaHandler(observedBudgetMeta{budgetClientMeta, observed}))
	if e != nil {
		t.Fatal(e)
	}
	if _, e = cli.Probe(context.Background(), &rpc.RequestContext{RequestId: "warm"}); e != nil {
		t.Fatal(e)
	}
	<-observed
	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Microsecond)
	defer cancel()
	_, e = cli.Probe(ctx, &rpc.RequestContext{RequestId: "exhausted"})
	if e == nil {
		t.Fatal("耗尽预算仍调用成功")
	}
	select {
	case err := <-observed:
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatal("没有在metadata阶段拒绝零毫秒预算", err)
		}
	case <-time.After(time.Second):
		t.Fatal("请求没有进入真实TTHeader编码边界")
	}
}
