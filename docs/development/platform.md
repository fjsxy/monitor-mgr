# 平台部署与账号管理

本入口覆盖平台账号、集群与异步实例生命周期。产品使用 React/TypeScript/Vite 与单 Go 进程；HTTP adapter 只映射 DTO、Cookie 与安全错误，每个账号请求通过生成的 Thrift client 连接实际 Kitex server，服务统一授权和访问 SQLite。`requestId` 相同的 `http-to-kitex` 与 `kitex-server` 日志用于确认调用链，不记录请求体、密码或 token。

## 固定构建

Go 1.27.1；Kitex runtime/tool v0.16.3；thriftgo v0.4.4；modernc SQLite v1.60.1；bcrypt 来自 golang.org/x/crypto v0.58.0。完整 Go 依赖由 go.mod/go.sum 固定。前端 Node 26.4.0、React 19.3.1、TypeScript 7.0.2、Vite 8.3.4、Playwright 1.64.1；完整前端依赖由 web/package-lock.json 固定。Dockerfile 同时固定三个基础镜像的 registry digest。

重新生成：

```sh
./scripts/generate-rpc.sh
git diff --exit-code -- kitex_gen
```

参数 `-module github.com/fjsxy/monitor-mgr -thrift no_default_serdes,keep_unknown_fields idl/control.thrift` 生成类型化 client/server 与 binary codec；没有 generic JSON RPC，也没有 local caller。兼容扩展新增 optional 字段，不复用已发布字段编号。当前 Go 1.27 下 CloudWeGo 动态 JSON 加速发出 fallback 警告，使用标准 JSON 路径；真实 Thrift 编解码和网络调用另有测试，不据此宣称性能结论。

## 本地运行

```sh
(cd web && npm ci && npm run build)
go run ./cmd/monitor-mgr -data-dir ./data -http 127.0.0.1:8080 -rpc 127.0.0.1:8888
```

访问 `http://127.0.0.1:8080`；空库初始化管理员，然后登录。管理员创建账号时设置临时密码，新用户必须先改密。`-session-ttl 8h` 是默认绝对寿命，读取和轮询不续期；登录和会话响应仅返回账号与到期时间，凭据仅由 `Set-Cookie` 交付。部署在 HTTPS 入口时设置 `-origin https://<入口>`，生成 Secure Cookie，代理必须保留同源入口；不能配置任意来源或反射请求 Origin。

数据目录包含 `platform.db`、WAL/SHM、`writer.lock` 和独立 `master.key` 文件。目录必须保持完整；密钥为 32 字节、0600、owner 与实际运行 UID 一致。已有数据库缺少原密钥会拒绝启动。单 writer 文件锁在同一数据目录阻止第二个进程；不能据此宣称跨不同数据副本的多 writer 安全。平台卷必须实际支持 SQLite WAL、锁和同步。

## 管控集群部署

需先提供符合 WAL/锁语义的持久化存储，默认示例使用动态 StorageClass；PVC Bound 或 RWO 不能代替文件系统验证。命令必须显式传入目标 kubeconfig、context 与独占 namespace，避免操作用户其他集群。

```sh
SOURCE_SHA=$(git rev-parse HEAD)
docker build --platform linux/arm64 --provenance=false --sbom=false \
  --build-arg SOURCE_SHA="$SOURCE_SHA" -t "monitor-mgr:$SOURCE_SHA" .
kind load docker-image "monitor-mgr:$SOURCE_SHA" --name "$KIND_CLUSTER"
"$KUBECTL" --kubeconfig "$KUBECONFIG" --context "$CONTEXT" create namespace "$NAMESPACE"
sed "s/monitor-mgr:development/monitor-mgr:$SOURCE_SHA/" deploy/platform.yaml |
  "$KUBECTL" --kubeconfig "$KUBECONFIG" --context "$CONTEXT" -n "$NAMESPACE" apply -f -
"$KUBECTL" --kubeconfig "$KUBECONFIG" --context "$CONTEXT" -n "$NAMESPACE" rollout status deployment/monitor-mgr
"$KUBECTL" --kubeconfig "$KUBECONFIG" --context "$CONTEXT" -n "$NAMESPACE" port-forward service/monitor-mgr 18080:8080
```

示例固定 replicas=1、Recreate、UID/GID=10001，HTTP Service 只公开 8080；Kitex 只绑定 Pod loopback 127.0.0.1:8888。关闭 Pod 身份自动挂载，目标集群必须经后续明确注册。数据卷保留整个数据目录；无需 shell、init container 或 root chown。底层卷需支持 fsGroup 或预先以正确 owner 准备。Pod 重建应在卷可用节点完成；不承诺节点级 HA。

`GET /readyz` 每次实际调用生成的 Kitex Probe，Probe 查询数据库。仅 listener 或静态页面可用不会 ready。任一 listener 或 RPC 探测失败回收半启动资源。SIGTERM 先撤销就绪并停止 HTTP/RPC 新业务受理和新派发，默认排空 10 秒；超时后保存明确未发或可能已发状态，再停止 RPC，最后关闭数据库。重启不自动重放；明确未发的任务等待继续/取消，可能已发的任务只恢复原 UID 读取核验。

## 验证与证据

```sh
go test -race ./...
go vet ./...
python3 scripts/validate-acceptance.py
(cd web && npm ci && npm run build && npx playwright install chromium)
BASE_URL=http://127.0.0.1:18080 npm --prefix web run test:e2e
```

浏览器套件在独占环境运行。I01.primary 使用初始空库；每个 I07 子例显式建立管理员基线（空库时初始化），各自创建独立测试账号，不要求先运行 I01。last-admin 用例要求只有初始管理员启用。`ADMIN_PASSWORD` 可在测试进程环境中设置，默认仅测试值。浏览器 trace 关闭以免保留网络请求凭据；成功截图及失败截图仅显示页面，禁止将 Cookie、token、原密钥或密码加入证据。

HTTP/真实 RPC 集成测试覆盖 I01、I02 默认/配置寿命/安全 Cookie、I07 全部账号变更和并发最后管理员保护；直接 RPC 验证无 Session 不能访问账号管理。T07 含数据库无法打开、两种 listener 占用、真实网络 Dialer 不可用及取消的 RPC 探测，均不能 ready。可控时钟和 transport 只隔离已确认的系统边界，业务 handler 与 SQLite 不 mock。浏览器 I07 与真实 Pod T02 的通过须另外取证，不能以 Go 测试替代。

当前接口：GET/POST `/api/v1/initialization`；POST `/sessions`；GET/DELETE `/session`；POST `/session/password`；GET/POST `/accounts`；PATCH `/accounts/{id}`；POST `/accounts/{id}/password-reset`。账号不删除，生命周期动作保存审计；至少一名启用管理员由串行事务检查。

账号创建、角色/启用变更与管理员重置密码必须携带 `Idempotency-Key`，HTTP 通过 optional Thrift `RequestContext.idempotencyKey` 传递。服务在同一事务内先验证当前 Session 与管理员权限，再按 actor/action/target/key 查询；同键同规范化请求返回原安全结果，异内容返回 409，结果与账号变更、审计一起提交。含密码的规范化请求只保存独立 master key 计算的 HMAC-SHA256，不存密码或裸摘要。初始化、登录、退出、本人改密属于认证命令，不缓存会话凭据；改密或自行重置使旧 Session 失效后，旧凭据重复请求仍须 401。

当前 schema 版本为 10；v5 收口独立 CR namespace 与敏感读模型指纹，v6 保存当前执行尝试、修订和原 UID 清理图；v7 将删除预览绑定原 UID、部署版本、目标配置 HMAC 与稳定 namespace 影响范围；v8 修复敏感读投影，v9 在同事务维护持久活动顺序，v10 保存原 Agent/PVC UID 与 ST/Pod 关系、按 attempt 的独立存储核验任务，并为危险 Agent 发布预览绑定实际存储影响 HMAC。删除受理和派发前均重新核验；旧 v6 明确未发删除必须在同一 operation 下重新预览并明确继续，不能自动重放。已有库升级前通过 SQLite `VACUUM INTO` 保存一致快照至数据目录 `backups/`（0600）；所有待应用 DDL 和版本记录一次事务提交。快照失败不迁移，迁移失败保留原库及恢复依据；不初始化替代数据库。新版本启动拒绝不兼容版本，schema1 账号和有效 Session 升级后保持可用。`control.Store.Authorize` / `AuthorizeTx` 从持久会话与当前账号读取角色及失效依据，后续业务必须复用当前授权，不接受浏览器角色断言。

生命周期入口：POST `/operations/{id}/retry`、`/cancel`、`/continue-unsent` 绑定 `expectedOperationRevision`、`expectedAttemptId`、`expectedAttemptRevision`，仍需当前授权及幂等键。普通重试只接受确定失败，同 operationId 和固定发布版本产生新 attempt；旧 attempt 与原发起者保留。POST `/operations/{id}/refresh` 只读核验结果，身份不明不能补认。POST `/instances/{id}/deletion-previews` 只读目标并保存删除确认范围；POST `/instances/{id}/deletions` 使用该 `previewId` 和原 `versionId`，按原 UID/新读 RV 发送唯一 Foreground DELETE；只有原 CR 及持久 owner UID 图中的 workload、ReplicaSet、Pod 全部核验消失才解除保护。平台不直接清理 Pod、PVC 或 finalizer。删除的 retry/continue 需先对同一 operation 重新预览，再显式传 `deletePreviewId`；目标规范化配置或 namespace 影响范围变化须重新确认，status/RV 与同范围 Pod 滚动不产生误冲突。

## Agent 配置与来源

统一添加入口使用 `component: "Prometheus Agent"` 与类型化 `agent` 分支。默认 StatefulSet、Prometheus v2.55.1 固定镜像、单 shard/单副本、200m CPU/512Mi memory 请求、1Gi memory 上限和每卷1Gi RWO PVC；需要已有 namespace、ServiceAccount 及默认动态 StorageClass。当前配置仅开放 Running、shards=1 和 PVC；创建后容量与存储身份固定，保存、预览、受理及实际派发均重新核对不可变版本。后续运行/存储策略通过同一组件接缝扩展。

四类采集关联分别保存对象 None/All/Labels 和 namespace Own/All/Labels。None/Own 对应省略，All 对应 `{}`；缺失和空对象不能合并。POST `/instances/{id}/association-previews` 绑定 `draftRevision`，只计算草稿匹配；运行来源来自实际 CR，并按 `PVC`、`association:ServiceMonitor`、`association:PodMonitor`、`association:Probe`、`association:ScrapeConfig` 独立保存 UID、成功/尝试时间和失败历史。匹配数、Pod Ready 和 CR 写入都不证明远端已收到样本。

remoteWrite 仅开放 URL、name、remoteTimeout、BasicAuth/Bearer 与 TLS 的 Secret name/key 引用，不回显或持久化 Secret 值。平台身份需能核验目标身份、相关 CR/依赖、SAR，以及读取固定 Operator Deployment/ReplicaSet/Pod 和 `pods/proxy` metrics；Operator 的实际 Pod 身份、范围及 Agent controller 启动证据单独核对。非 None ScrapeConfig 还需同一 Pod 的 informer 请求证据。Operator 参数只解释明确字面形式及已知 scope 字符串，位置参数、重复 scope、未知参数 arity 和未展开的 `$(VAR)` 会明确受阻。采集 SA 的实际发现权限独立检查；平台不自动创建或修补 SA、Secret、RBAC、StorageClass。

重复验收入口为 `make agent-q-receipt`、`make e2e-agent`、`make e2e-agent-b` 和 `make agent-regressions`。后者独立记录补充安全回归，不增加69个 owned 子例；真实 K/B 要求 `E2E_ARGS` 显式提供 kubectl、两份目标 kubeconfig、两个 context、Pod 可达配置、镜像、源码、输出和跨票 API 锁。每例创建独立平台 Pod/数据库及目标 fixture，整批持锁至 finally 完成；CI profile 不能由宿主 LOCAL_FULL 冒充。


Agent 存储与生命周期：`desiredState=Running/Suspended` 映射 `replicas=1/0`，始终 `paused=false`；`shards` 为正整数，每 shard 单副本并使用同一资源声明。挂起使用实际 ST 的 `whenScaled`，减少 shard、删除 Agent 或 Operator 替换 ST 使用 `whenDeleted`。先单独发布策略并等实际 ST 采用，危险操作不同时改变策略；Retain 还要核对本次会消失的 ST/Pod owner UID。缩零保留 ST 时，`whenScaled=Retain/whenDeleted=Delete` 的原 ST ownerRef 合法，但不能承诺以后删 ST 仍保留卷。

首次创建可选 PVC 或 emptyDir；PVC 使用默认 StorageClass/RWO，自定义容量在成功创建或可能已发版本后固定。新实例确定 WAL claim 名称若已有卷，或者读取 Forbidden/超时，均拒绝创建；恢复只允许同原实例可信历史与同 PVC UID，不按同名认领。emptyDir 不创建 PVC，Pod 删除/替换或挂起缩零可能丢失未发 WAL，容器重启本身不删除 emptyDir。

存储任务在持久 `possibly_sent` 同一事务激活，后台沿应用 worker 生命周期只读原 PVC UID，使用当前 ACTIVE 凭据并核对原集群 UID/凭据修订。原 CR/workload/Pod 清空后实例和操作可完成，PVC Retained/Deleting/Deleted/Unknown 分别表达；任务不重建实例 protection。未决 Delete/PVC Unknown 仍保留 transfer/unregister 责任，凭据更新可用于恢复读取；已确认 Retain 是终态，不阻塞集群生命周期。平台不直接删除 PVC、workload 或 finalizer。Agent 存储重复入口为 `make e2e-agent-storage[-q|-reg]`，实际 K 必须传明双集群、镜像来源和 API lane。
