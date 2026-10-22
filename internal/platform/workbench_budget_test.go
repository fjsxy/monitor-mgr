package platform_test

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"time"

	"github.com/cloudwego/kitex/client"
	"github.com/cloudwego/kitex/pkg/remote"
	headermeta "github.com/cloudwego/kitex/pkg/remote/transmeta"
	"github.com/cloudwego/kitex/pkg/transmeta"
	"github.com/cloudwego/kitex/transport"
	"github.com/fjsxy/monitor-mgr/internal/platform"
	"github.com/fjsxy/monitor-mgr/internal/teststeps"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"github.com/fjsxy/monitor-mgr/kitex_gen/control/controlservice"
	ginkgo "github.com/onsi/ginkgo/v2"
)

// R06：三个真实TLS边界同时阻塞，断言全局8/集群4及排队也计入8秒，不用私有limiter断言。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestR06WorkbenchConcurrentBudgets(t ginkgo.GinkgoTInterface) {
	// 接下来R06：三个真实TLS边界同时阻塞，断言全局8/集群4及排队也计入8秒，不用私有limiter断言。
	ginkgo.By("R06：三个真实TLS边界同时阻塞，断言全局8/集群4及排队也计入8秒，不用私有limiter断言。")

	p, c, owner := clusterPlatform(t)
	var mu sync.Mutex
	active, maximum, calls := 0, 0, 0
	per, peaks := map[int]int{}, map[int]int{}
	wake := make(chan struct{}, 32)
	gate := make(chan struct{})
	defer close(gate)
	clusters := []string{}
	for n := 0; n < 3; n++ {
		index := n
		base, _, _ := clusterAPI(t, 200, false)
		target := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path == "/api/v1/namespaces/kube-system" {
				json.NewEncoder(w).Encode(map[string]any{"metadata": map[string]any{"uid": fmt.Sprintf("budget-cluster-%d", index)}})
				return
			}
			if r.Method == "GET" && strings.HasSuffix(r.URL.Path, "/serviceaccounts") {
				mu.Lock()
				active++
				per[index]++
				calls++
				if active > maximum {
					maximum = active
				}
				if per[index] > peaks[index] {
					peaks[index] = per[index]
				}
				mu.Unlock()
				select {
				case wake <- struct{}{}:
				default:
				}
				defer func() { mu.Lock(); active--; per[index]--; mu.Unlock() }()
				select {
				case <-gate:
					json.NewEncoder(w).Encode(map[string]any{"items": []any{}})
				case <-r.Context().Done():
				}
				return
			}
			base.Config.Handler.ServeHTTP(w, r)
		}))
		t.Cleanup(target.Close)
		ca := base64.StdEncoding.EncodeToString([]byte(pemCert(target.Certificate().Raw)))
		private := `{"clusters":[{"name":"t","cluster":{"server":"` + target.URL + `","certificate-authority-data":"` + ca + `"}}],"users":[{"name":"u","user":{"token":"fixture"}}],"contexts":[{"name":"explicit","context":{"cluster":"t","user":"u"}}]}`
		registered := clusterCall(t, p, "POST", "/clusters", map[string]any{"name": fmt.Sprintf("budget-%d", n), "ownerId": owner, "context": "explicit", "kubeconfig": private}, c, fmt.Sprintf("register-%d", n), 202)
		done := awaitClusterOperation(t, p, c, registered["operationId"].(string))
		if done["state"] != "SUCCEEDED" {
			t.Fatal("预算前置注册失败", done["state"])
		}
		clusters = append(clusters, done["clusterId"].(string))
	}
	type result struct {
		status  int
		elapsed time.Duration
		failed  bool
	}
	results := make(chan result, 15)
	for group, cluster := range clusters {
		for n := 0; n < 5; n++ {
			go func(cluster string, group, n int) {
				defer ginkgo.GinkgoRecover()
				start := time.Now()
				req, _ := http.NewRequest("GET", p.URL()+fmt.Sprintf("/api/v1/clusters/%s/references?kind=ServiceAccount&namespace=q-%d-%d", cluster, group, n), nil)
				req.AddCookie(c)
				resp, e := http.DefaultClient.Do(req)
				if e != nil {
					results <- result{failed: true, elapsed: time.Since(start)}
					return
				}
				resp.Body.Close()
				results <- result{status: resp.StatusCode, elapsed: time.Since(start)}
			}(cluster, group, n)
		}
	}
	timeout := time.NewTimer(3 * time.Second)
	defer timeout.Stop()
	for {
		mu.Lock()
		n := active
		mu.Unlock()
		if n == 8 {
			break
		}
		select {
		case <-wake:
		case <-timeout.C:
			t.Fatal("没有达到8个实际在途请求")
		}
	}
	time.Sleep(60 * time.Millisecond)
	for n := 0; n < 15; n++ {
		select {
		case r := <-results:
			if r.failed || r.status != 502 || r.elapsed < 7500*time.Millisecond || r.elapsed > 9500*time.Millisecond {
				t.Fatalf("预算未覆盖排队或RPC/HTTP失控: %+v", r)
			}
		case <-time.After(11 * time.Second):
			t.Fatal("请求没有在原预算内结束")
		}
	}
	mu.Lock()
	defer mu.Unlock()
	if maximum > 8 || calls > 15 {
		t.Fatalf("全局并发/内部重试放大: max=%d calls=%d", maximum, calls)
	}
	for cluster, peak := range peaks {
		if peak > 4 {
			t.Fatalf("单集群并发超限: cluster=%d max=%d", cluster, peak)
		}
	}
}

// wireTimeoutMeta 只在测试生成客户端实际TTHeader上修改预算，不替换服务端或目标适配器。
type wireTimeoutMeta struct {
	remote.MetaHandler
	value string
}

func (h wireTimeoutMeta) WriteMeta(ctx context.Context, msg remote.Message) (context.Context, error) {
	ctx, e := h.MetaHandler.WriteMeta(ctx, msg)
	if e == nil {
		msg.TransInfo().PutTransIntInfo(map[uint16]string{headermeta.RPCTimeout: h.value})
	}
	return ctx, e
}

// R06：请求方缺失/异常/零或夸大metadata也不能把服务端总预算扩大到10秒以上。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestR06WorkbenchServerBudgetMaximum(t ginkgo.GinkgoTInterface) {
	// 接下来复用一个平台和目标适配器，对四类 metadata 走实际 Kitex 编解码。
	p, c, id, f, _ := workbenchSourceFixture(t, func(cfg *platform.Config) { cfg.ObservationTicks = make(chan time.Time) })
	f.mu.Lock()
	f.readDelay = 4 * time.Second
	f.mu.Unlock()
	for _, metadata := range []string{"missing", "100000", "0", "invalid"} {
		teststeps.Step(metadata, func() {
			options := []client.Option{client.WithHostPorts(p.RPCAddress()), client.WithRPCTimeout(15 * time.Second)}
			if metadata != "missing" {
				options = append(options, client.WithTransportProtocol(transport.TTHeader), client.WithMetaHandler(wireTimeoutMeta{transmeta.ClientTTHeaderHandler, metadata}))
			}
			cli, e := controlservice.NewClient("workbench-server-budget", options...)
			if e != nil {
				t.Fatal(e)
			}
			if _, e = cli.Probe(context.Background(), &rpc.RequestContext{RequestId: "metadata-" + metadata}); e != nil {
				t.Fatal("metadata 编解码失败", metadata, e)
			}
			// 关键：服务端上限与继承预算已在纯边界断言；只保留一次完整 10 秒网络计时。
			if metadata != "missing" {
				return
			}
			token := c.Value
			start := time.Now()
			cli.RefreshInstance(context.Background(), &rpc.RequestContext{SessionToken: &token}, &rpc.InstanceID{Id: id})
			elapsed := time.Since(start)
			if elapsed < 9500*time.Millisecond || elapsed > 11500*time.Millisecond {
				t.Fatalf("服务端总预算未限制为10秒：%s", elapsed)
			}
		})
	}
}
