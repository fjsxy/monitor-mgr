# 本地单 Go 进程同时提供 HTTP 入口与 Kitex RPC 服务

> 部署位置已于 2026-10-18 由 [ADR0014](0014-control-cluster-platform-deployment.md) 修订为管控集群中的平台 Pod；以下宿主机与双 loopback 描述为原决策。单 Go 进程、真实 HTTP→Kitex 和管控服务统一职责继续有效。HTTP 改为 Pod 可达监听，RPC 保持 Pod loopback。

用户确定一期后端使用 Go、RPC 通信使用 Kitex，并接受单 Go 进程的本地启动方式，因此 HTTP 入口和一个 Kitex 管控服务在同一进程内分别监听 loopback 地址，HTTP 入口通过生成的 Kitex client 发起真实 RPC 调用，以同时满足 RPC 要求和本地启动约束。

HTTP 层提供 React 构建产物、协议转换与会话传递，管控服务统一执行身份与归属权限校验、数据库访问、配置下发和状态同步，以使权限及管理状态保持一个责任边界；具体 IDL、接口及后台任务契约由后续决策明确。
