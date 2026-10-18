// 本文件统一限制目标 API 并发，让不同集群共享平台请求容量。
package control

import (
	"context"
	"sync"
)

// targetLimits 管理全局与各集群的并发通道。
type targetLimits struct {
	global   chan struct{}
	mu       sync.Mutex
	clusters map[string]chan struct{}
	reads    map[string]*targetRead
}

// newTargetLimits 设置全局 8、每集群 4 个并发请求名额。
func newTargetLimits() *targetLimits {
	return &targetLimits{global: make(chan struct{}, 8), clusters: map[string]chan struct{}{}, reads: map[string]*targetRead{}}
}

// acquire 在同一调用预算内等待两个名额，并返回成对释放函数。
// 关键：排队消耗调用方剩余预算，取消时归还已占名额，不能重新计时或泄漏容量。
func (l *targetLimits) acquire(ctx context.Context, id string) (func(), error) {
	l.mu.Lock()
	lane := l.clusters[id]
	if lane == nil {
		lane = make(chan struct{}, 4)
		l.clusters[id] = lane
	}
	l.mu.Unlock()
	select {
	case lane <- struct{}{}:
	case <-ctx.Done():
		return nil, Error("TARGET_UNAVAILABLE", "目标 API 排队预算已耗尽")
	}
	select {
	case l.global <- struct{}{}:
		return func() { <-l.global; <-lane }, nil
	case <-ctx.Done():
		<-lane
		return nil, Error("TARGET_UNAVAILABLE", "目标 API 排队预算已耗尽")
	}
}

// targetRead 只保存一次在途GET的原始内存响应，不持久化秘密，也不复用已完成结果。
type targetRead struct {
	done chan struct{}
	raw  []byte
	err  error
}

func (l *targetLimits) read(ctx context.Context, key string, work func() ([]byte, error)) ([]byte, error) {
	l.mu.Lock()
	if flight, ok := l.reads[key]; ok {
		l.mu.Unlock()
		select {
		case <-flight.done:
			return flight.raw, flight.err
		case <-ctx.Done():
			return nil, Error("TARGET_UNAVAILABLE", "目标读取等待预算已耗尽")
		}
	}
	flight := &targetRead{done: make(chan struct{})}
	l.reads[key] = flight
	l.mu.Unlock()
	// 关键：leader沿用最初调用的剩余预算；加入者不能延长它，写请求从不进入合并分支。
	flight.raw, flight.err = work()
	l.mu.Lock()
	delete(l.reads, key)
	close(flight.done)
	l.mu.Unlock()
	return flight.raw, flight.err
}
