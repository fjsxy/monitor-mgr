// monitor-mgr 在单进程内启动 HTTP、Kitex 与持久化 worker，并处理有期限的退出。
package main

import (
	"context"
	"flag"
	"github.com/fjsxy/monitor-mgr/internal/platform"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"
)

// buildSource 由镜像构建注入，用于记录实际运行来源。
var buildSource = "development"

// main 读取部署参数，等待平台就绪，并在收到退出信号后有序停止。
func main() {
	cfg := platform.Config{}
	flag.StringVar(&cfg.DataDir, "data-dir", "./data", "SQLite WAL 与原密钥目录")
	flag.StringVar(&cfg.HTTPAddr, "http", ":8080", "HTTP 地址")
	flag.StringVar(&cfg.RPCAddr, "rpc", "127.0.0.1:8888", "Kitex loopback 地址")
	flag.StringVar(&cfg.PublicOrigin, "origin", "", "浏览器公开 Origin，省略时按 HTTP Host 校验")
	flag.StringVar(&cfg.StaticDir, "static", "web/dist", "React 构建目录")
	flag.DurationVar(&cfg.SessionTTL, "session-ttl", 8*time.Hour, "Session 绝对寿命")
	flag.Parse()
	cfg.Logger = slog.New(slog.NewJSONHandler(os.Stdout, nil))
	p, err := platform.Start(context.Background(), cfg)
	if err != nil {
		cfg.Logger.Error("平台启动失败", "error", err)
		os.Exit(1)
	}
	cfg.Logger.Info("平台已就绪", "http", cfg.HTTPAddr, "rpc", cfg.RPCAddr, "source", buildSource)
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, syscall.SIGTERM, syscall.SIGINT)
	<-signals
	// 关键：退出等待有界，恢复状态由运行时保存，不能把终止信号视为已受理操作的撤销。
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err = p.Close(ctx); err != nil {
		cfg.Logger.Error("平台退出等待结束", "error", err)
	}
}
