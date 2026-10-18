# 集群生命周期

集群业务通过 `/api/v1/clusters` 进入真实生成 Thrift/Kitex，管控服务统一授权并访问 SQLite 和目标 API。所有业务变更要求 `Idempotency-Key`；注册、更新凭据和重新启用先在事务内保存操作与 AES-GCM 候选，再返回 `202` 和 `operationId`。`GET /api/v1/operations/{operationId}` 按当前归属授权，原页面关闭不取消任务。

上传后使用 `POST /api/v1/clusters/contexts` 读取 context 名称，明确选择后才提交。仅物化所选 context 的内嵌 CA/cert/key/token，静态拒绝外部依赖后才构造 HTTPS 客户端；不使用 kubeconfig 插件、环境 proxy 或平台 Pod 身份。TLS、非匿名 SelfSubjectReview 与 kube-system Namespace UID 通过后才登记。SSR 与 SSAR 为非持久化审查，不创建业务资源。

凭据以 clusterId / operationId 为 AES-GCM AAD 分别保存当前与候选密文。历史只记录身份、修订、操作者和归属前后值。坏候选或 UID 不同保留当前值；注销清除当前与所有候选，只保留历史。启动验证当前与候选密文，缺原密钥或错误密钥均停止。数据库和原密钥须分别备份，不能用新密钥替代。

组件绿色表示注册时的基础 CRD、Kubernetes 支持版本与业务权限检查通过；灰色附原因。发布仍须按实际 namespace、动作和依赖核验。`namespaces` 与 `references` 元信息读取错误不会转为空列表，Secret 仅返回名称及 key 名，不返回 value。目标不可达不影响平台数据库/RPC ready。

`HasUnfinishedChangesTx` 按所有非终结操作状态保护转移与注销，含未来发布/删除与未知状态。后续票向同一操作保护边界扩展，不能只检查本票凭据操作。每次工作执行及切换事务都读取发起账号的当前权限；Session 寿命不取消已受理操作。退出先停止 worker，重启不自动继续排队/核验中的操作；管理员通过 `POST /api/v1/operations/{operationId}/continue` 明确继续只读核验。凭据恢复不会触发其他操作重放。

验证：

- `go test -race ./...`、`go vet ./...`；受控 TLS API 位于外部系统边界，全部业务请求经过真实 Kitex。
- `BASE_URL=... CLUSTER_KUBECONFIG=/private/embedded.json npm --prefix web run test:e2e -- clusters.spec.ts`：真实 Chromium 对注册/转移/重新启用的过期表单逐一验证停用归属用户拒绝；fixture 配置须为 JSON，使用内嵌材料且从平台 Pod 可达。缺 fixture 会明确 skip，不能记通过。
- `scripts/acceptance/clusters.py --help`：Python 3 + PyYAML 6.0.3，提供固定 kubectl、两个私有目标 kubeconfig、两个 Pod 可达配置、已预载 Linux arm64 产品 image/source 与输出目录。每个 K 子例建立独立 namespace/PVC/平台 Pod，保存脱敏证据后 finally 清理；T06 仅作为测试 fixture 移走和恢复原密钥。工具不会创建 kind 或安装 Operator，不能指向用户已有环境。

本票逐 23 稳定子例的运行证据由执行输出报告；计划目录继续保留 planned，单票成功不替代完整 LOCAL_FULL/CI_QUICK/CI_SMOKE。
