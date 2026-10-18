// lifecyclefixture 只在验收镜像提供进程屏障；产品HTTP不开放故障控制入口。
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"github.com/fjsxy/monitor-mgr/internal/platform"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"
)

var buildSource = "development"

type gate struct {
	Stage     string `json:"stage"`
	Operation string `json:"operationId"`
	Arrived   bool   `json:"arrived"`
	release   chan struct{}
}

func main() {
	cfg := platform.Config{}
	flag.StringVar(&cfg.DataDir, "data-dir", "/data", "持久目录")
	flag.StringVar(&cfg.HTTPAddr, "http", ":8080", "HTTP入口")
	flag.StringVar(&cfg.RPCAddr, "rpc", "127.0.0.1:8888", "RPC入口")
	flag.StringVar(&cfg.StaticDir, "static", "/web", "页面目录")
	flag.Parse()
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	cfg.Logger = logger
	var mu sync.Mutex
	gates := map[string]*gate{}
	stop := make(chan struct{})
	cfg.OperationBoundary = func(stage, id string) error {
		mu.Lock()
		g := gates[stage]
		if g == nil || g.Arrived {
			mu.Unlock()
			return nil
		}
		g.Arrived = true
		g.Operation = id
		mu.Unlock()
		logger.Info("验收应用屏障", "stage", stage, "operationId", id)
		select {
		case <-g.release:
			return nil
		case <-stop:
			return errors.New("验收退出屏障保持持久恢复状态")
		}
	}
	control := http.NewServeMux()
	control.HandleFunc("/state", func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		json.NewEncoder(w).Encode(gates)
	})
	control.HandleFunc("/arm", func(w http.ResponseWriter, r *http.Request) {
		var b struct {
			Stage string `json:"stage"`
		}
		if r.Method != "POST" || json.NewDecoder(r.Body).Decode(&b) != nil || (b.Stage != "beforeDispatch" && b.Stage != "afterSendMarker") {
			http.Error(w, "invalid fixture rule", 400)
			return
		}
		mu.Lock()
		defer mu.Unlock()
		if old := gates[b.Stage]; old != nil {
			http.Error(w, "reset required", 409)
			return
		}
		gates[b.Stage] = &gate{Stage: b.Stage, release: make(chan struct{})}
		json.NewEncoder(w).Encode(map[string]bool{"armed": true})
	})
	control.HandleFunc("/release", func(w http.ResponseWriter, r *http.Request) {
		var b struct {
			Stage string `json:"stage"`
		}
		if r.Method != "POST" || json.NewDecoder(r.Body).Decode(&b) != nil {
			http.Error(w, "invalid fixture rule", 400)
			return
		}
		mu.Lock()
		defer mu.Unlock()
		g := gates[b.Stage]
		if g != nil {
			close(g.release)
			delete(gates, b.Stage)
		}
		json.NewEncoder(w).Encode(map[string]bool{"released": true})
	})
	control.HandleFunc("/crash", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			w.WriteHeader(405)
			return
		}
		os.Exit(97)
	})
	server := &http.Server{Addr: "127.0.0.1:19091", Handler: control, ReadHeaderTimeout: time.Second}
	go server.ListenAndServe()
	p, e := platform.Start(context.Background(), cfg)
	if e != nil {
		logger.Error("验收平台启动失败", "error", e)
		os.Exit(1)
	}
	logger.Info("验收平台已就绪", "source", buildSource)
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, syscall.SIGTERM, syscall.SIGINT)
	<-signals
	// 关键：屏障模拟10秒仍无响应的在途调用，排空结束后仅退出等待，不伪造取消或失败。
	time.AfterFunc(10*time.Second, func() { close(stop) })
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	p.Close(ctx)
	server.Close()
	logger.Info("验收平台持久状态已保存")
}
