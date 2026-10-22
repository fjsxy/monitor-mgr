# 页面使用 HTTP JSON，管控 RPC 使用 Thrift，并先持久化耗时操作

一期页面采用 `/api/v1` HTTP JSON REST API，HTTP 入口通过生成的 Thrift 类型化 client/server 发起真实 Kitex RPC；配置以显式类型表达，selector 的省略与空对象保持不同语义，避免将协议对象或当前草稿直接当成 CR 与发布版本。

登录采用服务端 Session 与 HttpOnly Cookie，HTTP 向 RPC 传递会话凭据，Kitex 管控服务校验当前账号、角色和集群归属；保存草稿同步返回，下发、删除等耗时操作先持久化再返回 `operationId`，页面轮询结果，以便请求响应丢失后仍能查询已接受操作，避免重复执行。

用户于 2026-10-16 确认两轮推荐：同进程 worker 使用 SQLite 持久化任务，同一操作固定配置与发布版本、各次执行尝试分别保留；取消和派发原子协调，重启只恢复核验，不自动重放写请求，以避免响应丢失被错误当作可安全重试。Session 默认 8 小时绝对过期且可配置，重启保留有效会话；已受理任务按发起账号当前权限执行，不与原 Cookie 寿命绑定。

HTTP/RPC/Kubernetes 请求预算初值为 15/10/8 秒，Kubernetes 全局/单集群并发为 8/4；CR 默认每 10 秒读取、可配置为 1–20 秒，过期阈值保持 30 秒。数据库和真实 RPC 探测通过后才就绪，退出停止新操作并默认等待 10 秒；完整接口及错误、观测、启停契约见 [确定 HTTP、Kitex RPC 与后台任务的接口及模块边界](https://github.com/fjsxy/monitor-mgr/issues/12#issuecomment-6214104420)。本地文档记录规划决策，不表示已生成接口代码或完成运行验收。

部署位置及 HTTP/RPC 可达范围于 2026-10-18 按 [ADR0014](0014-control-cluster-platform-deployment.md) 修订；本决策中的接口、Session、预算和任务恢复规则继续有效，平台 Pod 重建是恢复验收的实际执行方式。
