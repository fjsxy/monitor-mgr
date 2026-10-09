# 一期 kubeconfig 注册与本地 kind 连通契约研究

研究日期：2026-10-10。对应决策任务：[核验 kubeconfig 注册与本地 kind 连通契约](https://github.com/fjsxy/monitor-mgr/issues/3)。

本文为后续决策提供事实，不选定数据库、运行形态或凭据支持政策。已确认本地运行、平台管理员注册、内置账号、一个集群只有一个集群所属用户，并复用现有 CR 与 Operator。用语与下发边界依据主工作区的 `GLOSSARY.md` 和 `docs/adr/0001-observability-integration-boundary.md`、`docs/adr/0002-publication-and-resource-ownership.md`。

## 影响决策的结论

**建议候选**：一期采用宿主机后端、明确选择一个 context、内嵌凭据；不执行上传配置中的插件或按其路径读取文件。注册分层检查连通、身份、CR 能力和权限；平台生成集群 ID，以远端 `kube-system` UID 辅助查重，凭据先验证后替换。该路线直接覆盖本地 kind，并减少环境依赖，仍须用户确认。以下给出事实依据与限制。

以下逐项给出原始来源。源码核验样本固定为 [client-go `v0.34.1`](https://github.com/kubernetes/client-go/tree/35bde1b16479d24449ef9d96d578c0dadc679c37) 和 [kind `v0.30.0`](https://github.com/kubernetes-sigs/kind/tree/9a205e8c8540557602240f8766d3c95c51c23c4c)；这两个版本只是可复核的取证基线，不是一期支持版本决定。官方在线文档按研究日读取。

## kubeconfig 的输入与导入语义

### context 必须成为显式输入

**事实**：一个 context 关联 cluster、user 和默认 namespace；默认使用 `current-context`，显式 context 可以覆盖它。`KUBECONFIG` 可以指定多个文件并合并，未指定时 CLI 会读取默认文件。cluster、user、context 的名称都是配置内的引用名称，不是远端集群证明。见 [Kubernetes kubeconfig 组织规则](https://kubernetes.io/docs/concepts/configuration/organize-cluster-access-kubeconfig/#context)。

**事实**：`clientcmd.Load(bytes)` 只反序列化给定字节，不读取默认配置；`NewNonInteractiveClientConfig` 可以使用明确的 context。`RESTConfigFromKubeConfig` 本身没有独立的“让用户选择 context”参数，使用传入配置的当前 context。见 [client-go `loader.go` 的 `Load`](https://github.com/kubernetes/client-go/blob/35bde1b16479d24449ef9d96d578c0dadc679c37/tools/clientcmd/loader.go#L398) 与 [`client_config.go` 的构造函数和 `getContextName`](https://github.com/kubernetes/client-go/blob/35bde1b16479d24449ef9d96d578c0dadc679c37/tools/clientcmd/client_config.go#L89)。

**建议**：上传后只解析 context 列表和引用关系，由管理员明确选择一个 context；单 context 可预选，多 context 不静默选第一个。注册完成后保存选择结果与必要的 cluster/user 内容，不保留无关集群凭据。任何请求都不合并平台进程的 `KUBECONFIG` 或默认配置。context 的默认 namespace 仅作为候选值，不能代替产品确定的 CR 目标 namespace。

### 哪些配置能随上传文件独立使用

| 输入 | 已核验事实 | 对注册契约的影响 |
| --- | --- | --- |
| `certificate-authority-data`、`client-certificate-data`、`client-key-data` | 内嵌 PEM 数据；对应 `*-data` 优先于文件引用。[kubeconfig v1 字段](https://kubernetes.io/docs/reference/config-api/kubeconfig.v1/#AuthInfo) | 不需要上传者的证书文件；仍需检查 CA、证书与密钥是否可解析、证书与密钥是否匹配。 |
| `certificate-authority`、`client-certificate`、`client-key` | 引用文件路径；文件加载规则按原始 kubeconfig 所在位置解析相对路径。[文件引用规则](https://kubernetes.io/docs/concepts/configuration/organize-cluster-access-kubeconfig/#file-references) | 浏览器上传字节不携带这些文件或原目录语义。平台不能据路径自行读取宿主机文件。 |
| `token` | 内嵌 bearer token，是认证材料。[AuthInfo 定义](https://github.com/kubernetes/client-go/blob/35bde1b16479d24449ef9d96d578c0dadc679c37/tools/clientcmd/api/types.go#L118) | 可持久化为平台凭据，但导入不会提供自动续期机制。 |
| `tokenFile` | 指向 bearer token 文件；与 `token` 共存时，周期读取成功的文件值优先。[kubeconfig v1 `tokenFile`](https://kubernetes.io/docs/reference/config-api/kubeconfig.v1/#AuthInfo) | 是运行时文件依赖，不是导入时快照；支持它需要明确文件归属、访问边界及轮换责任。 |
| `exec` | client-go 执行配置中的 command/args，并继承宿主环境及额外 env；插件 stdout 返回凭据，stderr 可以输出到进程 stderr。[`exec.go` 的 `refreshCredsLocked`](https://github.com/kubernetes/client-go/blob/35bde1b16479d24449ef9d96d578c0dadc679c37/plugin/pkg/client/auth/exec/exec.go#L384) | 单纯“非交互 client”不等于禁止插件执行。支持它需要安装、信任、执行隔离和日志政策。 |
| `auth-provider` | 使用注册到 client-go 二进制中的 provider；未注册则报错，provider 可使用配置持久化器保存更新。[`rest/plugin.go`](https://github.com/kubernetes/client-go/blob/35bde1b16479d24449ef9d96d578c0dadc679c37/rest/plugin.go#L32) | 文件格式能表示 provider，不代表平台二进制支持它，更不代表可以离线续期。 |

**事实**：`Load(bytes)` 不设置文件来源路径；`ResolveLocalPaths` 仅对带 `LocationOfOrigin` 的条目进行路径解析。`ClientConfig()` 在只有 `tokenFile` 时直接调用 `os.ReadFile`，因此拒绝外部路径必须发生在构造可用 client 配置之前。见 [client-go 路径加载源码](https://github.com/kubernetes/client-go/blob/35bde1b16479d24449ef9d96d578c0dadc679c37/tools/clientcmd/loader.go#L465) 与 [token 文件读取源码](https://github.com/kubernetes/client-go/blob/35bde1b16479d24449ef9d96d578c0dadc679c37/tools/clientcmd/client_config.go#L247)。

**事实**：`MinifyConfig` 只保留当前 context 的引用条目；`FlattenConfig` 会实际读文件，只内嵌 CA、cert/key，不处理 `tokenFile`、`exec` 或 `auth-provider`。不能把 flatten 当作上传安全检查。见 [client-go `api/helpers.go`](https://github.com/kubernetes/client-go/blob/35bde1b16479d24449ef9d96d578c0dadc679c37/tools/clientcmd/api/helpers.go#L39)。

**建议**：一期从 HTTPS、内嵌 CA、内嵌 cert/key 和明确的 `tls-server-name` 开始；token 是否纳入另行确认。拒绝路径及插件依赖、不安全 TLS、basic auth 和 impersonation，无关条目不传给 client。Kubernetes 警示不可信 kubeconfig 可造成代码执行或文件暴露，这要求平台在 client 构造前决定输入白名单。见 [官方 kubeconfig 信任提示](https://kubernetes.io/docs/tasks/access-application-cluster/configure-access-multiple-clusters/)。

## 宿主机与容器访问 kind 的差别

**事实**：kind 默认 `apiServerAddress` 为 `127.0.0.1`，默认选择随机空闲端口，方便多个本地集群并存；官方不建议为方便连接而将 kind API 暴露到公网。见 [kind Networking / API Server](https://kind.sigs.k8s.io/docs/user/configuration/#api-server)。

**事实**：kind 导出 external kubeconfig 时，把节点内 kubeadm 配置的 server 改为 provider 的宿主机 endpoint；internal kubeconfig 保留内部 endpoint。`kind get kubeconfig --internal` 是正式 CLI 能力。见 [kind kubeconfig 导出源码](https://github.com/kubernetes-sigs/kind/blob/9a205e8c8540557602240f8766d3c95c51c23c4c/pkg/cluster/internal/kubeconfig/kubeconfig.go#L63) 与 [`get kubeconfig` flags](https://github.com/kubernetes-sigs/kind/blob/9a205e8c8540557602240f8766d3c95c51c23c4c/pkg/cmd/kind/get/kubeconfig/kubeconfig.go#L47)。

**事实**：Docker Desktop 容器位于独立虚拟网络；`host.docker.internal` 用于连接宿主机服务。Linux 的 `host-gateway` 和 Mac/Windows 的 Desktop 解析方式有所区别。见 [Docker Desktop 网络结构](https://docs.docker.com/desktop/features/networking/)、[连接宿主机服务](https://docs.docker.com/desktop/features/networking/networking-how-tos/#connect-a-container-to-a-service-on-the-host) 与 [Compose 的 host-gateway](https://docs.docker.com/compose/how-tos/networking/#custom-dns-with-extra_hosts)。

下表为上述事实推导出的候选运行路线，未在本研究中实际连通验证：

| 后端运行位置 | 可用路线 | 需要解决的约束 |
| --- | --- | --- |
| 宿主机进程 | 使用 kind 的 external kubeconfig | 保持 loopback 与原 TLS 信息；kind 重建后 endpoint/凭据可能变化，需要重新核验。是一期本地场景的最短路线。 |
| 与 kind 节点共享容器网络的后端容器 | 使用 internal kubeconfig | 后端必须能解析并路由内部 endpoint；固定容器网络/provider 是测试环境契约。不能凭 `--internal` 假定任意网络中的容器可达。 |
| 经宿主机转发的后端容器 | 将连接地址明确改为 `host.docker.internal:<port>` 等宿主机可达地址 | 同时核验宿主服务实际监听地址、Docker provider、路由和 TLS 名称。DNS 可解析不代表端口可达。 |

**事实**：`tls-server-name` 非空时用该值校验服务端证书；为空时用连接 hostname。CA 信任与名称匹配是两个要求。kind 核验样本的 kubeadm 模板在 API server 额外 SAN 中包含 `localhost` 与 `APIServerAddress`，不会自动为地址改写追加 `host.docker.internal`。见 [kubeconfig Cluster TLS 字段](https://kubernetes.io/docs/reference/config-api/kubeconfig.v1/#Cluster)、[client-go TLSConfigFor](https://github.com/kubernetes/client-go/blob/35bde1b16479d24449ef9d96d578c0dadc679c37/transport/transport.go#L69) 与 [kind kubeadm 模板](https://github.com/kubernetes-sigs/kind/blob/9a205e8c8540557602240f8766d3c95c51c23c4c/pkg/cluster/internal/kubeadm/config.go#L324)。

**推论**：若默认 kind 证书有 `localhost` SAN，改写 server 后保留 CA 并设置 `tls-server-name: localhost` 可以解决名称校验问题；这不能解决网络不可达，也不能保证经过自定义 kind/kubeadm patches 的证书仍符合该假设。禁止自动改为 `insecure-skip-tls-verify: true` 来掩盖错误。地址覆盖若获准，必须由管理员显式提供并保存，不能后台猜测或静默改写。

**事实**：kubeconfig `proxy-url` 会改变 API 请求路径；未设置时 client 可以从环境变量构造代理。见 [kubeconfig ProxyURL 定义](https://kubernetes.io/docs/reference/config-api/kubeconfig.v1/#Cluster)。**建议**：一期本地路线显式决定是否继承后端代理环境，对 kind endpoint 采用明确的直连策略；否则同一个文件在不同进程环境下可能表现不同。具体策略待确认。

## 注册需要哪些 API 检查

**事实**：默认 RBAC 将 `/version` 等公开信息接口授予 authenticated 与 unauthenticated groups。因此 `/version` 返回成功不能证明上传凭据已作为预期用户完成认证。见 [Kubernetes 默认 RBAC 角色](https://kubernetes.io/docs/reference/access-authn-authz/rbac/#api-discovery-roles) 与 [`system:public-info-viewer` 的实际 URL 与 binding](https://github.com/kubernetes/kubernetes/blob/v1.34.1/plugin/pkg/auth/authorizer/rbac/bootstrappolicy/policy.go#L314)。

**事实**：`SelfSubjectReview` 返回 API server 识别的当前客户端身份，稳定于 Kubernetes `v1.28`；结果也反映 impersonation。请求不持久化为可读取资源。见 [Kubernetes 客户端身份 API](https://kubernetes.io/docs/reference/access-authn-authz/authentication/#api-access-to-authentication-information-for-a-client)。

**事实**：Discovery 返回服务的 group/version、resource、scope 和支持 verbs；它不表示当前用户获得这些操作的权限。`SelfSubjectAccessReview` 针对当前用户的一个具体操作返回授权结果；对 namespaced 资源不填 namespace 意味着查询所有 namespaces。见 [Kubernetes Discovery API](https://kubernetes.io/docs/concepts/overview/kubernetes-api/#discovery-api)、[权限检查](https://kubernetes.io/docs/reference/access-authn-authz/authorization/#checking-api-access) 与 [SelfSubjectAccessReview API](https://kubernetes.io/docs/reference/kubernetes-api/definitions/self-subject-access-review-v1-authorization/)。

**建议的最小检查顺序**：

| 层次 | 操作 | 能证明什么 | 不能证明什么 |
| --- | --- | --- | --- |
| 本地校验 | 解析上传、确定 context、检查白名单与证书/key 格式 | 输入可安全进入受支持的 client 构造流程 | 网络、认证与授权 |
| 连接与 TLS | 通过后端实际网络位置请求 `/version` | 当次 endpoint、TLS 与 API 应答可用，获得版本信息 | 凭据身份、CR 权限、组件正常运行 |
| Kubernetes 身份 | Discovery 确认后请求 `SelfSubjectReview`，拒绝匿名结果 | 当次 Kubernetes principal，可区分平台账号与集群凭据身份 | 哪些 CR 操作被允许 |
| 远端关联 | `GET /api/v1/namespaces/kube-system`，读取 `metadata.uid` | 当次读权限与 Namespace 对象身份 | 永久不变的全局 cluster ID |
| CR 能力 | 请求相应 `/apis/<group>/<version>`，检查资源名、kind、scope、served version；按需要读取 schema | 所需 CR API 是否被服务，字段契约是否可候选使用 | Operator 是否存活、调谐或数据面是否成功 |
| 业务权限 | 对实际目标 group/resource/namespace/verb 分别执行 `SelfSubjectAccessReview`；读取已有采集配置 CR 做读能力检查 | 当次允许执行的动作，以及实际读取是否成功 | admission、quota、写入竞争、后续 RBAC 变化与调谐结果 |

**建议的权限边界**：管理 CR 的 `get/list`、按状态回读策略需要的 `watch`、下发实现实际使用的 `create/update/patch`，以及批准删除功能后才需要的 `delete`。一期只关联已有采集配置 CR，不要求其写权限。读取完整 CR 即可获得 `status`，平台不应要求修改 `/status`。若 remote_write 方案需要 Secret 写入，权限留给该方案决定。动作映射依据 [Kubernetes API 操作](https://kubernetes.io/docs/reference/using-api/api-concepts/) 与 [RBAC subresources](https://kubernetes.io/docs/reference/access-authn-authz/rbac/#referring-to-resources)，不是已批准的 RBAC 清单；group/version/plural/scope 留给 CR 契约核验。

**建议**：分别记录检查结果与 `checkedAt`；状态回读失败标记过期。授权允许不代表 schema/admission、写入竞争或 Operator 生效；实际下发处理前两者，CR `status` 回读验证后者。服务端 dry-run 只作发布前校验候选，不证明组件可运行。见 [API dry-run 语义](https://kubernetes.io/docs/reference/using-api/api-concepts/#dry-run)。

**待决定**：是否要求整个注册检查全部通过，还是允许保存尚未具备 CR 能力的集群并明确显示“不可下发”。对于不支持或不允许 `SelfSubjectReview` 的版本，必须标为“身份无法核验”或选择其他受限验证契约，不能把 `/version` 成功伪装成已核验身份。

## 集群身份、重复注册与重建

**事实**：Kubernetes 为每个对象生成 UID，用于区分同名对象的不同历史实例；`kube-system` 是初始化 Namespace 之一。该 UID 的规范含义是对象身份，不是 Kubernetes 定义的 cluster ID。见 [Object Names and IDs / UIDs](https://kubernetes.io/docs/concepts/overview/working-with-objects/names/#uids) 与 [初始 namespaces](https://kubernetes.io/docs/concepts/overview/working-with-objects/namespaces/#initial-namespaces)。

**推论**：普通本地 kind 集群在生命周期内，可用 `kube-system.metadata.uid` 作为低成本的远端关联锚点。新建同名 kind 集群会创建新对象，而文件名、context 名与显示名称可以继续相同；endpoint 也可能被其他实例复用。因此名称与 endpoint 只能是显示/连接信息。CA fingerprint 是信任配置摘要，不应作为永久 cluster ID：证书材料可轮换，且 Namespace UID 本身也不是面对数据克隆/恢复的独立身份认证协议。

**建议**：平台 ID 与远端 UID 分开保存；相同 UID 返回重复注册冲突，通过独立入口替换凭据。相同名称/endpoint、不同 UID 按重建冲突处理，不自动继承资源归属或继续下发；缺少 UID 不降级到文件名查重。并发查重需要数据库约束或事务。若要求覆盖数据克隆/恢复后的严格实例区分，需另议平台写入专用标记及其恢复语义，会增加注册权限和资源生命周期责任。

## 凭据轮换与失效

**事实**：内嵌 cert/key 在 client-go transport 中作为静态证书使用；纯 cert/key 文件引用存在重新加载机制，但 CA 读取和 TLS pool 构造发生在 transport 构造阶段。不能把数据库更新或新文件内容等同于已存活 client、连接和 watch 自动切换。见 [client-go TLS 文件加载及静态/动态证书分支](https://github.com/kubernetes/client-go/blob/35bde1b16479d24449ef9d96d578c0dadc679c37/transport/transport.go#L121)。

**事实**：`tokenFile` 的文件 token source 会缓存并周期重读，读失败可保留之前成功的缓存 token；这不是申请新 token。`exec` 按返回的过期时间缓存结果，并可在 `401` 后触发刷新，其能力依赖插件。见 [client-go token source](https://github.com/kubernetes/client-go/blob/35bde1b16479d24449ef9d96d578c0dadc679c37/transport/token_source.go#L62) 与 [exec 缓存及刷新](https://github.com/kubernetes/client-go/blob/35bde1b16479d24449ef9d96d578c0dadc679c37/plugin/pkg/client/auth/exec/exec.go#L315)。

**事实**：ServiceAccount 的 TokenRequest token 有有效期；Pod projected token 的自动轮换由 kubelet/相关投影机制承担。把一个 token 字符串上传并保存，不会继承原文件的轮换机制。kubeadm 内嵌管理员证书续期后，已复制的 kubeconfig 也需要更新。见 [ServiceAccount 凭据机制](https://kubernetes.io/docs/concepts/security/service-accounts/#manually-retrieve-serviceaccount-credentials) 与 [kubeadm 管理员 kubeconfig 更新](https://kubernetes.io/docs/tasks/administer-cluster/kubeadm/kubeadm-certs/#copying-the-administrator-certificate-optional)。

**建议**：一期把凭据更新作为管理员显式操作：导入候选凭据 → 在真实后端运行位置重新检查网络/TLS/身份/远端 UID/能力 → 成功后原子切换当前凭据版本 → 重建 client 并重启该集群的状态回读。验证失败不覆盖旧凭据；切换后回读失败保留最后一次成功状态和时间，并标记过期。凭据版本与发布版本分开，不更改集群所属用户或重新下发观测配置。

证书 `NotAfter` 可作到期提示；任意 token 不一定有可信的统一到期字段。运行时以 API 结果为准，不承诺自动续期。旧凭据保留及回退政策留给存储与安全决策。

## 失败脱敏与持久化约束

**事实**：kubeconfig 包含私钥、token、password，并允许插件参数、环境变量和 provider 配置。client-go 对若干 String 输出及 `datapolicy` 字段做脱敏，但通用 kubeconfig 结构仍可包含未标注的插件 env、extension 或 URL 信息。见 [AuthInfo / ExecConfig 源码](https://github.com/kubernetes/client-go/blob/35bde1b16479d24449ef9d96d578c0dadc679c37/tools/clientcmd/api/types.go#L102) 与 [`RedactSecrets` 的字段标签规则](https://github.com/kubernetes/client-go/blob/35bde1b16479d24449ef9d96d578c0dadc679c37/tools/clientcmd/api/helpers.go#L180)。

**建议**：页面、审计和日志采用输出白名单，不返回配置原文、完整 rest.Config、Authorization、证书/key/token、插件输出或未经处理的错误正文。输出阶段、类别、HTTP code、资源动作、时间、关联 ID，以及去掉 userinfo/query/fragment 的 endpoint、context 名、缺少的能力和权限。上传者输入按不可信文本处理。

候选错误类别应至少区分：输入格式错误、未支持的凭据形式、网络不可达/超时、TLS 校验失败、Kubernetes `401`、Kubernetes `403`、无法核验远端身份、CR API 未安装/版本不兼容，以及重复注册。`401` 可能表示多种凭据无效原因，页面不应无证据地断言“token 已过期”；`403` 也不能简化为“集群不可达”。

**建议**：集群凭据与观测配置/账号授权分开建模，只保存选定 context 所需材料；敏感内容以可解密密文保存，密钥与数据库/备份分离。页面不回显凭据；Base64 只是编码。具体加密、密钥来源、备份和删除机制留给后续安全决策。

## 仍需用户决定

| 决策 | 推荐候选与理由 | 选择会影响什么 |
| --- | --- | --- |
| 本地运行形态 | 一期后端作为宿主机进程，直接使用 kind external endpoint | 启动工具、打包方式、容器网络与 TLS 地址覆盖是否进入一期 |
| 凭据支持范围 | 优先内嵌 CA + cert/key；token 按需要显式纳入；拒绝路径及插件依赖 | 上传格式、到期提示、轮换责任及错误提示 |
| 网络覆盖与代理 | 一期采用明确直连；如支持 endpoint/TLS name 覆盖则显式保存 | kubeconfig 可移植性、平台权限范围、网络诊断与本地验证矩阵 |
| 注册完成门槛 | 要求身份核验；把 CR 能力/业务权限的通过与失败分别展示，决定是否允许受限登记 | 集群列表可见状态、“可下发”的计算及缺少 Operator 的处理 |
| 远端关联与重复注册 | 要求 `get namespaces/kube-system`，以 UID 查重并显式处理重建 | 最小 RBAC、并发注册、重新注册与 owner 延续规则 |
| 凭据持久化与替换 | 单独凭据版本、先验证后切换、密文保存且密钥独立 | 数据库设计、本地启动秘密、备份、删除、回退与审计 |

以上候选需要通过 Wayfinder 的人类决策任务确认；研究票只能关闭事实核验，不等于批准候选方案。

## 核验边界

本研究完成官方文档、固定版本源码与已有领域决策的只读核验，没有读取任何真实 kubeconfig、token、证书私钥、Keychain 或默认凭据文件，没有执行凭据插件、联系集群或启动 kind。容器网络路线是源码与文档推导，尚无本机运行证据。后续确定运行形态、支持版本和输入政策后，才应在隔离测试凭据及本地 kind 上验证注册、重启、重复注册、权限不足、TLS 错误、凭据替换和状态过期。
