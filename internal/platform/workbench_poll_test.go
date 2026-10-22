package platform_test

import (
	"net/http"
	"sync"
	"time"

	ginkgo "github.com/onsi/ginkgo/v2"
)

// R01.intervals：服务端调度不依赖页面，默认10秒；公开启动配置精确限定1–20秒。

// R06：并发手动读取同一真实目标只产生一个GET；每个HTTP调用仍独立鉴权。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestR06WorkbenchCoalescesReads(t ginkgo.GinkgoTInterface) {
	// 接下来R06：并发手动读取同一真实目标只产生一个GET；每个HTTP调用仍独立鉴权。
	ginkgo.By("R06：并发手动读取同一真实目标只产生一个GET；每个HTTP调用仍独立鉴权。")

	p, c, id, f, _ := workbenchSourceFixture(t)
	gate := make(chan struct{})
	entered := make(chan struct{})
	f.mu.Lock()
	before := f.crReads
	f.observationGate = gate
	f.observationEntered = entered
	f.mu.Unlock()
	defer func() {
		select {
		case <-gate:
		default:
			close(gate)
		}
	}()
	var wg sync.WaitGroup
	responses := make(chan int, 16)
	for n := 0; n < 16; n++ {
		wg.Add(1)
		go func() {
			defer ginkgo.GinkgoRecover()
			defer wg.Done()
			req, _ := http.NewRequest("POST", p.URL()+"/api/v1/instances/"+id+"/refresh", nil)
			req.Header.Set("Origin", p.URL())
			req.AddCookie(c)
			resp, e := http.DefaultClient.Do(req)
			if e != nil {
				responses <- 0
				return
			}
			responses <- resp.StatusCode
			resp.Body.Close()
		}()
	}
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("未访问真实目标")
	}
	time.Sleep(75 * time.Millisecond)
	f.mu.Lock()
	during := f.crReads
	f.mu.Unlock()
	close(gate)
	wg.Wait()
	close(responses)
	for status := range responses {
		if status != 200 {
			t.Fatal("合并读取调用失败", status)
		}
	}
	if during-before != 1 {
		t.Fatalf("相同目标并发读取未合并:实际GET=%d", during-before)
	}
}

// R06：下游不得使用超过短调用方剩余预算的8秒新预算。
