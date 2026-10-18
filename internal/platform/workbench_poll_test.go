package platform_test

import (
	"context"
	"fmt"
	"net/http"
	"sync"
	"testing"
	"time"

	"github.com/fjsxy/monitor-mgr/internal/platform"
)

// R01.intervals：服务端调度不依赖页面，默认10秒；公开启动配置精确限定1–20秒。
func TestR01WorkbenchIntervals(t *testing.T) {
	for _, seconds := range []int{0, 21, -1} {
		interval := time.Duration(seconds) * time.Second
		p, e := platform.Start(context.Background(), platform.Config{DataDir: t.TempDir(), HTTPAddr: "127.0.0.1:0", RPCAddr: "127.0.0.1:0", ObservationInterval: &interval})
		if e == nil {
			p.Close(context.Background())
			t.Fatalf("越界轮询间隔被接受:%d", seconds)
		}
	}
	for _, seconds := range []int{1, 10, 20} {
		t.Run(fmt.Sprint(seconds), func(t *testing.T) {
			ticks := make(chan time.Time, 1)
			p, c, id, f, clock := workbenchSourceFixture(t, func(cfg *platform.Config) {
				cfg.ObservationTicks = ticks
				if seconds != 10 {
					d := time.Duration(seconds) * time.Second
					cfg.ObservationInterval = &d
				}
			})
			f.mu.Lock()
			before := f.crReads
			f.mu.Unlock()
			start := clock.Load()
			clock.Store(start + int64(time.Duration(seconds-1)*time.Second))
			ticks <- time.Unix(0, clock.Load())
			time.Sleep(30 * time.Millisecond)
			f.mu.Lock()
			early := f.crReads
			f.mu.Unlock()
			if early != before {
				t.Fatal("后台早于配置周期读取CR")
			}
			clock.Store(start + int64(time.Duration(seconds)*time.Second))
			ticks <- time.Unix(0, clock.Load())
			deadline := time.Now().Add(2 * time.Second)
			for {
				f.mu.Lock()
				n := f.crReads
				f.mu.Unlock()
				if n > before {
					break
				}
				if time.Now().After(deadline) {
					t.Fatal("未打开页面时后台周期没有读取CR")
				}
				time.Sleep(5 * time.Millisecond)
			}
			instance := clusterCall(t, p, "GET", "/instances/"+id, nil, c, "", 200)
			if workbenchSource(t, instance, "CR")["lastSuccessAt"] != time.Unix(0, clock.Load()).UTC().Format(time.RFC3339Nano) {
				t.Fatal("后台来源时间未更新")
			}
		})
	}
}

// R06：并发手动读取同一真实目标只产生一个GET；每个HTTP调用仍独立鉴权。
func TestR06WorkbenchCoalescesReads(t *testing.T) {
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
