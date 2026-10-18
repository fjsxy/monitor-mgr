package control

import (
	"context"
	"sync"
)

type targetLimits struct {
	global   chan struct{}
	mu       sync.Mutex
	clusters map[string]chan struct{}
}

func newTargetLimits() *targetLimits {
	return &targetLimits{global: make(chan struct{}, 8), clusters: map[string]chan struct{}{}}
}
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
