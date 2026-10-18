// Package platform 组装HTTP入口与真实loopback Kitex服务，业务规则由管控服务统一执行。
package platform

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/cloudwego/kitex/client"
	"github.com/cloudwego/kitex/pkg/endpoint"
	"github.com/cloudwego/kitex/pkg/remote"
	"github.com/cloudwego/kitex/server"
	"github.com/fjsxy/monitor-mgr/internal/control"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"github.com/fjsxy/monitor-mgr/kitex_gen/control/controlservice"
)

// Config 收集进程监听、持久目录、会话寿命与受控测试边界。
type Config struct {
	// OperationBoundary 仅供确定性进程/事务故障测试，不由 HTTP 或运行参数开放。
	OperationBoundary                                   func(stage, operationID string) error
	RPCDialer                                           remote.Dialer
	DataDir, HTTPAddr, RPCAddr, PublicOrigin, StaticDir string
	SessionTTL                                          time.Duration
	ObservationInterval                                 *time.Duration
	// ObservationTicks 仅供受控时钟测试；生产使用应用自身的周期调度。
	ObservationTicks <-chan time.Time
	Now              func() time.Time
	Logger           *slog.Logger
}

// Runtime 组合 HTTP adapter、真实 loopback Kitex 服务与单实例管控存储。
type Runtime struct {
	cfg     Config
	store   *control.Store
	rpc     server.Server
	client  controlservice.Client
	http    *http.Server
	ln      net.Listener
	rpcAddr string
	mu      sync.Mutex
	closed  bool
}

// Start 依次建立单 writer、监听器和真实 RPC，完成数据库/RPC 探测后才提供 HTTP。
func Start(ctx context.Context, cfg Config) (p *Runtime, err error) {
	if cfg.ObservationInterval != nil && (*cfg.ObservationInterval < time.Second || *cfg.ObservationInterval > 20*time.Second || *cfg.ObservationInterval%time.Second != 0) {
		return nil, errors.New("CR轮询间隔必须为1–20整秒")
	}
	if cfg.HTTPAddr == "" {
		cfg.HTTPAddr = ":8080"
	}
	if cfg.RPCAddr == "" {
		cfg.RPCAddr = "127.0.0.1:8888"
	}
	if cfg.Logger == nil {
		cfg.Logger = slog.Default()
	}
	// 关键：RPC 仅监听 loopback，浏览器业务经 HTTP adapter 传递真实会话，不能旁路授权。
	host, _, e := net.SplitHostPort(cfg.RPCAddr)
	if e != nil || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() {
		return nil, errors.New("RPC 必须监听 loopback IP")
	}
	store, err := control.Open(cfg.DataDir, cfg.Now, cfg.SessionTTL)
	if err != nil {
		return nil, err
	}
	store.OperationBoundary = cfg.OperationBoundary
	if cfg.ObservationInterval != nil {
		store.ObservationInterval = *cfg.ObservationInterval
	}
	store.ObservationTicks = cfg.ObservationTicks
	rpcLn, err := net.Listen("tcp", cfg.RPCAddr)
	if err != nil {
		store.Close()
		return nil, err
	}
	httpLn, err := net.Listen("tcp", cfg.HTTPAddr)
	if err != nil {
		rpcLn.Close()
		store.Close()
		return nil, err
	}
	p = &Runtime{cfg: cfg, store: store, ln: httpLn, rpcAddr: rpcLn.Addr().String()}
	p.rpc = controlservice.NewServer(store, server.WithListener(rpcLn), server.WithExitWaitTime(time.Second), server.WithMiddleware(func(next endpoint.Endpoint) endpoint.Endpoint {
		return func(ctx context.Context, req, resp any) error {
			if !store.Accepting() {
				// 关键：通过生成Result的声明异常返回业务拒绝，不能把退出包装成transport错误。
				if result, ok := resp.(interface{ SetError(*rpc.APIError) }); ok {
					result.SetError(&rpc.APIError{Code: "UNAVAILABLE", Message: "平台退出中，停止接收业务请求"})
					return nil
				}
				return control.Error("UNAVAILABLE", "平台退出中，停止接收业务请求")
			}
			if args, ok := req.(interface{ GetCtx() *rpc.RequestContext }); ok && args.GetCtx() != nil {
				cfg.Logger.Info("管控服务请求", "requestId", args.GetCtx().RequestId, "transport", "kitex-server")
			}
			return next(ctx, req, resp)
		}
	}))
	rpcErr := make(chan error, 1)
	go func() { rpcErr <- p.rpc.Run() }()
	clientOptions := []client.Option{client.WithHostPorts(p.rpcAddr), client.WithRPCTimeout(10 * time.Second)}
	if cfg.RPCDialer != nil {
		clientOptions = append(clientOptions, client.WithDialer(cfg.RPCDialer))
	}
	p.client, err = controlservice.NewClient("monitor-mgr-control", clientOptions...)
	if err == nil {
		// 关键：启动就绪必须经过真实生成 client 和服务端数据库，不能由监听成功代替。
		probeCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
		defer cancel()
		for {
			_, err = p.client.Probe(probeCtx, &rpc.RequestContext{RequestId: control.ID()})
			if err == nil {
				break
			}
			select {
			case <-probeCtx.Done():
				err = errors.New("真实 RPC 探测失败")
				goto failed
			case e := <-rpcErr:
				err = fmt.Errorf("RPC 启动失败: %w", e)
				goto failed
			case <-time.After(10 * time.Millisecond):
			}
		}
	}
	if err != nil {
		goto failed
	}
	if err = store.StartWorkers(); err != nil {
		goto failed
	}
	p.http = &http.Server{Handler: http.TimeoutHandler(http.HandlerFunc(p.handle), 15*time.Second, `{"code":"DEADLINE_EXCEEDED","message":"请求超时"}`), ReadHeaderTimeout: 5 * time.Second}
	go p.http.Serve(httpLn)
	return p, nil
failed:
	p.rpc.Stop()
	rpcLn.Close()
	httpLn.Close()
	store.Close()
	return nil, err
}

// URL 返回当前 HTTP 监听地址，供测试和本地接入使用。
func (p *Runtime) URL() string { return "http://" + p.ln.Addr().String() }

// RPCAddress 返回实际 loopback RPC 地址，供明确的协议边界测试使用。
func (p *Runtime) RPCAddress() string { return p.rpcAddr }

// Close 先阻止新派发，再关闭 HTTP、RPC 和持久化存储。
func (p *Runtime) Close(ctx context.Context) error {
	p.mu.Lock()
	if p.closed {
		p.mu.Unlock()
		return nil
	}
	p.closed = true
	p.mu.Unlock()
	// 关键：先保存 worker 恢复状态再释放数据库，退出不自动重放或把未确认结果取消。
	drainCtx, cancelDrain := context.WithTimeout(ctx, 10*time.Second)
	defer cancelDrain()
	p.store.StopWorkersContext(drainCtx)
	var err error
	if p.http != nil {
		err = p.http.Shutdown(ctx)
	}
	p.rpc.Stop()
	p.store.Close()
	return err
}

// handle 执行 HTTP 协议转换、Cookie/Origin 防护和 requestId 传播；业务通过 Kitex 执行。
func (p *Runtime) handle(w http.ResponseWriter, r *http.Request) {
	requestID := control.ID()
	w.Header().Set("X-Request-Id", requestID)
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if r.URL.Path == "/readyz" {
		p.mu.Lock()
		closed := p.closed
		p.mu.Unlock()
		if closed {
			http.Error(w, "not ready", 503)
			return
		}
		_, err := p.client.Probe(r.Context(), &rpc.RequestContext{RequestId: requestID})
		if err != nil {
			http.Error(w, "not ready", 503)
			return
		}
		w.WriteHeader(200)
		w.Write([]byte("ready"))
		return
	}
	p.mu.Lock()
	closing := p.closed
	p.mu.Unlock()
	if closing && strings.HasPrefix(r.URL.Path, "/api/") {
		p.writeError(w, control.Error("UNAVAILABLE", "平台退出中，停止接收业务请求"), requestID)
		return
	}
	// 关键：变更请求先校验 Origin，HttpOnly Cookie 不作为跨站提交的授权替代品。
	if r.Method != "GET" && r.Method != "HEAD" {
		origin := p.cfg.PublicOrigin
		if origin == "" {
			scheme := "http"
			if r.TLS != nil {
				scheme = "https"
			}
			origin = scheme + "://" + r.Host
		}
		if r.Header.Get("Origin") != origin {
			p.writeError(w, control.Error("FORBIDDEN", "来源校验失败"), requestID)
			return
		}
	}
	// 只转发会话凭据和幂等键；账号角色及归属由 RPC 服务按当前数据库判定。
	ctx := &rpc.RequestContext{RequestId: requestID}
	if key := r.Header.Get("Idempotency-Key"); key != "" {
		ctx.IdempotencyKey = &key
	}
	if cookie, err := r.Cookie("monitor_session"); err == nil {
		ctx.SessionToken = &cookie.Value
	}
	p.cfg.Logger.Info("业务请求", "requestId", requestID, "transport", "http-to-kitex", "method", r.Method, "path", r.URL.Path)
	// 关键：实例入口同样只调用真实RPC，不以数据库/目标API旁路降级。
	if p.instanceRoutes(w, r, ctx, requestID) {
		return
	}
	switch r.Method + " " + r.URL.Path {
	case "GET /api/v1/initialization":
		result, err := p.client.Probe(r.Context(), ctx)
		p.respond(w, 200, result, err, requestID)
	case "POST /api/v1/initialization":
		var body struct {
			Username string `json:"username"`
			Password string `json:"password"`
		}
		if err := decode(r, &body); err != nil {
			p.writeError(w, err, requestID)
			return
		}
		result, err := p.client.Initialize(r.Context(), ctx, &rpc.InitializeRequest{Username: body.Username, Password: body.Password})
		p.respond(w, 201, accountDTO(result), err, requestID)
	case "POST /api/v1/sessions":
		var body struct {
			Username string `json:"username"`
			Password string `json:"password"`
		}
		if err := decode(r, &body); err != nil {
			p.writeError(w, err, requestID)
			return
		}
		result, err := p.client.Login(r.Context(), ctx, &rpc.LoginRequest{Username: body.Username, Password: body.Password})
		if err != nil {
			p.writeError(w, err, requestID)
			return
		}
		expires, _ := time.Parse(time.RFC3339Nano, result.ExpiresAt)
		// 关键：会话只通过专用 HttpOnly Set-Cookie 交付，不能进入 JSON 或脚本可读内容。
		http.SetCookie(w, &http.Cookie{Name: "monitor_session", Value: result.SessionToken, Path: "/", HttpOnly: true, Secure: r.TLS != nil || strings.HasPrefix(p.cfg.PublicOrigin, "https://"), SameSite: http.SameSiteStrictMode, Expires: expires})
		p.respond(w, 201, map[string]any{"account": accountDTO(result.Account), "expiresAt": result.ExpiresAt}, nil, requestID)
	case "GET /api/v1/session":
		result, err := p.client.GetSession(r.Context(), ctx)
		if err != nil {
			p.writeError(w, err, requestID)
			return
		}
		p.respond(w, 200, map[string]any{"account": accountDTO(result.Account), "expiresAt": result.ExpiresAt}, nil, requestID)
	case "DELETE /api/v1/session":
		_, err := p.client.Logout(r.Context(), ctx)
		if err == nil {
			http.SetCookie(w, &http.Cookie{Name: "monitor_session", Value: "", Path: "/", HttpOnly: true, Secure: r.TLS != nil || strings.HasPrefix(p.cfg.PublicOrigin, "https://"), SameSite: http.SameSiteStrictMode, MaxAge: -1})
		}
		p.respond(w, 200, map[string]bool{"loggedOut": err == nil}, err, requestID)
	case "POST /api/v1/session/password":
		var body struct {
			CurrentPassword string `json:"currentPassword"`
			NewPassword     string `json:"newPassword"`
		}
		if err := decode(r, &body); err != nil {
			p.writeError(w, err, requestID)
			return
		}
		_, err := p.client.ChangePassword(r.Context(), ctx, &rpc.ChangePasswordRequest{CurrentPassword: body.CurrentPassword, NewPassword_: body.NewPassword})
		p.respond(w, 200, map[string]bool{"changed": err == nil}, err, requestID)
	case "GET /api/v1/accounts":
		result, err := p.client.ListAccounts(r.Context(), ctx)
		if err != nil {
			p.writeError(w, err, requestID)
			return
		}
		accounts := make([]any, 0, len(result.Accounts))
		for _, a := range result.Accounts {
			accounts = append(accounts, accountDTO(a))
		}
		p.respond(w, 200, map[string]any{"accounts": accounts}, nil, requestID)
	case "POST /api/v1/accounts":
		var body struct {
			Username string `json:"username"`
			Password string `json:"password"`
			Role     string `json:"role"`
		}
		if err := decode(r, &body); err != nil {
			p.writeError(w, err, requestID)
			return
		}
		result, err := p.client.CreateAccount(r.Context(), ctx, &rpc.CreateAccountRequest{Username: body.Username, Password: body.Password, Role: body.Role})
		p.respond(w, 201, accountDTO(result), err, requestID)
	default:
		if strings.HasPrefix(r.URL.Path, "/api/v1/clusters") || strings.HasPrefix(r.URL.Path, "/api/v1/operations/") {
			p.clusterRoutes(w, r, ctx, requestID)
			return
		}
		if p.cfg.StaticDir != "" && r.Method == "GET" && !strings.HasPrefix(r.URL.Path, "/api/") {
			http.FileServer(http.Dir(p.cfg.StaticDir)).ServeHTTP(w, r)
			return
		}
		if strings.HasPrefix(r.URL.Path, "/api/v1/accounts/") {
			p.accountMutation(w, r, ctx, requestID)
			return
		}
		p.writeError(w, control.Error("NOT_FOUND", "入口不存在"), requestID)
	}
}

// decode 读取有大小边界的单个 JSON 对象，并拒绝未知字段和尾随输入。
func decode(r *http.Request, v any) error {
	decoder := json.NewDecoder(io.LimitReader(r.Body, 65537))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(v); err == nil {
		var extra any
		if err := decoder.Decode(&extra); err == io.EOF {
			return nil
		}
	}
	// 关键：解析错误只指向整个 request，不回显解码器错误中的未知字段名或敏感原值。
	return &rpc.APIError{Code: "BAD_REQUEST", Message: "JSON 格式无效", FieldErrors: map[string]string{"request": "JSON 格式无效或包含不支持的字段"}}
}

// accountDTO 显式输出账号的非敏感 HTTP 字段。
func accountDTO(a *rpc.Account) any {
	if a == nil {
		return nil
	}
	return map[string]any{"id": a.Id, "username": a.Username, "role": a.Role, "enabled": a.Enabled, "mustChangePassword": a.MustChangePassword, "createdAt": a.CreatedAt}
}

// respond 统一写入成功 JSON，业务错误交由安全错误映射处理。
func (p *Runtime) respond(w http.ResponseWriter, status int, v any, err error, id string) {
	if err != nil {
		p.writeError(w, err, id)
		return
	}
	raw, e := safeHTTPJSON(v)
	if e != nil {
		p.writeError(w, control.Error("UNAVAILABLE", "响应无法编码"), id)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	w.Write(raw)

}

// writeError 仅向页面返回安全业务错误与 requestId，不暴露底层异常或凭据。
func (p *Runtime) writeError(w http.ResponseWriter, err error, id string) {
	code, message, status := "UNAVAILABLE", "服务暂不可用", 503
	var apiErr *rpc.APIError
	if errors.As(err, &apiErr) {
		code, message = apiErr.Code, apiErr.Message
		switch code {
		case "BAD_REQUEST":
			status = 400
		case "UNAUTHENTICATED":
			status = 401
		case "FORBIDDEN", "PASSWORD_CHANGE_REQUIRED":
			status = 403
		case "NOT_FOUND":
			status = 404
		case "ALREADY_INITIALIZED", "CONFLICT", "LAST_ADMIN":
			status = 409
		case "TARGET_CONFLICT":
			status = 409
		case "TARGET_REJECTED":
			status = 422
		case "TARGET_FORBIDDEN":
			status = 403
		case "TARGET_NOT_FOUND":
			status = 404
		case "TARGET_TLS", "TARGET_ERROR", "TARGET_UNAUTHENTICATED", "TARGET_UNAVAILABLE":
			status = 502
		case "INVALID_INPUT":
			status = 422
		}
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	body := map[string]any{"code": code, "message": message, "requestId": id}
	if apiErr != nil && len(apiErr.FieldErrors) > 0 {
		body["fieldErrors"] = apiErr.FieldErrors
	}
	json.NewEncoder(w).Encode(body)
}

// accountMutation 将账号路径和 HTTP 请求转换为类型化 RPC 变更。
func (p *Runtime) accountMutation(w http.ResponseWriter, r *http.Request, ctx *rpc.RequestContext, requestID string) {
	path := strings.TrimPrefix(r.URL.Path, "/api/v1/accounts/")
	parts := strings.Split(path, "/")
	id := parts[0]
	if len(parts) == 1 && r.Method == "PATCH" {
		var body struct {
			Role    *string `json:"role"`
			Enabled *bool   `json:"enabled"`
		}
		if err := decode(r, &body); err != nil {
			p.writeError(w, err, requestID)
			return
		}
		result, err := p.client.UpdateAccount(r.Context(), ctx, &rpc.UpdateAccountRequest{Id: id, Role: body.Role, Enabled: body.Enabled})
		p.respond(w, 200, accountDTO(result), err, requestID)
		return
	}
	if len(parts) == 2 && parts[1] == "password-reset" && r.Method == "POST" {
		var body struct {
			Password string `json:"password"`
		}
		if err := decode(r, &body); err != nil {
			p.writeError(w, err, requestID)
			return
		}
		_, err := p.client.ResetPassword(r.Context(), ctx, &rpc.ResetPasswordRequest{Id: id, Password: body.Password})
		p.respond(w, 200, map[string]bool{"reset": err == nil}, err, requestID)
		return
	}
	p.writeError(w, control.Error("NOT_FOUND", "入口不存在"), requestID)
}
