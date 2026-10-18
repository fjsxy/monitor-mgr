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
}

// newTargetLimits 设置全局 8、每集群 4 个并发请求名额。
func newTargetLimits() *targetLimits {
	return &targetLimits{global: make(chan struct{}, 8), clusters: map[string]chan struct{}{}}
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
