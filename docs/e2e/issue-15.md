# #15 E2E 测试用例

本轮新增格式约束的标准化补录；原实施前计划已发布，本表保留原稳定子例。

所属任务：[集群注册、凭据与归属生命周期](https://github.com/fjsxy/monitor-mgr/issues/15)。本地副本：`docs/e2e/issue-15.md`。

以下为计划，不代表执行通过。完整层级、profile、前置、清理与证据要求继承固定 `docs/acceptance/phase1.json`；稳定子例及原定语义保持不变。每例独立建立前置，只操作本 runId 资源，故障 finally 恢复，凭据不得进入报告。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| T04.loopback | 识别Pod不可达地址和TLS错误 | 分别提交指向平台Pod自身loopback的API地址、错误CA或名称的测试kubeconfig。 本子例输入：API endpoint 指向平台 Pod 自身未监听的 loopback 地址。 | 连通失败，不保存为有效注册 | #15 |
| T04.wrong-ca | 识别Pod不可达地址和TLS错误 | 分别提交指向平台Pod自身loopback的API地址、错误CA或名称的测试kubeconfig。 本子例输入：目标可达，但 CA 错误。 | TLS 校验失败，不能降级跳过校验 | #15 |
| T04.wrong-name | 识别Pod不可达地址和TLS错误 | 分别提交指向平台Pod自身loopback的API地址、错误CA或名称的测试kubeconfig。 本子例输入：目标可达，但 TLS serverName 不匹配。 | 名称核验失败，不能自动更改 endpoint | #15 |
| T06.primary | 缺密钥不能破坏已有密文 | 在有凭据密文的测试卷上移走密钥后启动，再恢复原密钥。 本子例输入：在有凭据密文的测试卷上移走密钥后启动，再恢复原密钥。。 | 缺密钥时启动明确失败且不生成替代密钥；恢复原密钥后可解密；文件owner/mode符合0600要求。 | #15 |
| T07.target-offline | 就绪必须覆盖DB与真实RPC | 参数化注入数据库不可用、RPC探测失败、listener占用及单目标API断联。 本子例输入：只有一个已注册目标 API 断联。 | 平台仍 ready，目标状态单独失败 | #15 |
| I07.disabled-owner | 账号生命周期与最后管理员保护 | 参数化重置/停用/变更角色；尝试停用或降级最后启用管理员，并指定停用用户为归属。 本子例输入：注册、转移、重新启用时分别指定停用用户。 | 各入口拒绝停用归属用户 | #15 |
| C01.client-cert | 有效注册不创建观测负载 | 用内嵌HTTPS CA和cert/key或token、明确context注册；检查SSR、UID和资源变化。 本子例输入：明确 context，HTTPS 内嵌 CA 和客户端证书/私钥。 | 身份与 UID 核验通过后保存，不创建观测负载 | #15 |
| C01.token | 有效注册不创建观测负载 | 用内嵌HTTPS CA和cert/key或token、明确context注册；检查SSR、UID和资源变化。 本子例输入：明确 context，HTTPS 内嵌 CA 与有效 token。 | 核验有效非匿名身份，不将平台账号当 Kubernetes 身份 | #15 |
| C02.primary | 同集群不同context不能重复注册 | 以另一context/凭据再次注册同一kube-system Namespace UID。 本子例输入：以另一context/凭据再次注册同一kube-system Namespace UID。。 | 报重复关联，原clusterId、归属和历史保留；引导更新凭据，不另建重复记录。 | #15 |
| C03.missing | 注册拒绝未核验身份 | 测试API分别返回SSR缺失、Forbidden、匿名和错误；其他version/权限审查仍成功。 本子例输入：SSR API 不存在，其他公开接口成功。 | 拒绝注册，不伪称已运行 Kubernetes 1.19 | #15 |
| C03.forbidden | 注册拒绝未核验身份 | 测试API分别返回SSR缺失、Forbidden、匿名和错误；其他version/权限审查仍成功。 本子例输入：SSR 返回 Forbidden。 | 拒绝注册，明确权限原因 | #15 |
| C03.anonymous | 注册拒绝未核验身份 | 测试API分别返回SSR缺失、Forbidden、匿名和错误；其他version/权限审查仍成功。 本子例输入：SSR 返回匿名身份。 | 拒绝注册，不以 SSAR 成功代替 | #15 |
| C03.error | 注册拒绝未核验身份 | 测试API分别返回SSR缺失、Forbidden、匿名和错误；其他version/权限审查仍成功。 本子例输入：SSR 返回受控服务错误。 | 拒绝注册，不保存为已生效 | #15 |
| C06.bad-candidate | 凭据验证后才切换 | 参数化验证坏凭据、同集群好凭据，并在待确认任务中更新；记录切换前后读取。 本子例输入：候选凭据无法核验。 | 原凭据不被替换 | #15 |
| C08.primary | 凭据指向新UID不能继承旧集群 | 用数据集群凭据更新管控集群记录，UID与原值不同。 本子例输入：用数据集群凭据更新管控集群记录，UID与原值不同。。 | 拒绝按原集群切换/继承；旧身份历史保留，新集群须走独立注册。 | #15 |
| C09.http | 拒绝不支持的kubeconfig依赖 | 分别输入HTTP、跳过TLS、依赖外部证书路径、tokenFile、exec/auth-provider或proxy的配置。 本子例输入：HTTP API endpoint。 | 拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | #15 |
| C09.skip-tls | 拒绝不支持的kubeconfig依赖 | 分别输入HTTP、跳过TLS、依赖外部证书路径、tokenFile、exec/auth-provider或proxy的配置。 本子例输入：insecure-skip-tls-verify。 | 拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | #15 |
| C09.external-ca | 拒绝不支持的kubeconfig依赖 | 分别输入HTTP、跳过TLS、依赖外部证书路径、tokenFile、exec/auth-provider或proxy的配置。 本子例输入：外部 CA 路径。 | 拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | #15 |
| C09.external-cert | 拒绝不支持的kubeconfig依赖 | 分别输入HTTP、跳过TLS、依赖外部证书路径、tokenFile、exec/auth-provider或proxy的配置。 本子例输入：外部客户端证书/私钥路径。 | 拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | #15 |
| C09.token-file | 拒绝不支持的kubeconfig依赖 | 分别输入HTTP、跳过TLS、依赖外部证书路径、tokenFile、exec/auth-provider或proxy的配置。 本子例输入：tokenFile。 | 拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | #15 |
| C09.exec | 拒绝不支持的kubeconfig依赖 | 分别输入HTTP、跳过TLS、依赖外部证书路径、tokenFile、exec/auth-provider或proxy的配置。 本子例输入：exec 插件。 | 拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | #15 |
| C09.auth-provider | 拒绝不支持的kubeconfig依赖 | 分别输入HTTP、跳过TLS、依赖外部证书路径、tokenFile、exec/auth-provider或proxy的配置。 本子例输入：auth-provider。 | 拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | #15 |
| C09.proxy | 拒绝不支持的kubeconfig依赖 | 分别输入HTTP、跳过TLS、依赖外部证书路径、tokenFile、exec/auth-provider或proxy的配置。 本子例输入：proxy 配置。 | 拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | #15 |
