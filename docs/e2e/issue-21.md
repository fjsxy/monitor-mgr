# #21 E2E 测试用例

实施前 E2E 计划；发布并回读后才能开始实现。 本票对全部子例执行最终门禁复跑，不改变各业务票的主责任归属；分别运行 LOCAL_FULL/CI_FAST/CI_SMOKE 中适用的 207/58/17 个执行键。

所属任务：[自动化验收与交付门禁](https://github.com/fjsxy/monitor-mgr/issues/21)。本地副本：`docs/e2e/issue-21.md`。

以下为计划，不代表执行通过。完整层级、profile、前置、清理与证据要求继承固定 `docs/acceptance/phase1.json`；稳定子例及原定语义保持不变。每例独立建立前置，只操作本 runId 资源，故障 finally 恢复，凭据不得进入报告。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| T01.primary | 固定运行来源 | 按profile准备集群和依赖；回读节点、CRD、controller镜像及构建revision。 本子例输入：按profile准备集群和依赖；回读节点、CRD、controller镜像及构建revision。。 | 均符合锁定版本/摘要；main为1控制面2worker；FULL另有1控制面数据集群；缺锁或不Ready即环境失败。 | #21 |
| T02.primary | 平台实际运行在管控集群 | 启动单平台Pod；经HTTP Service/port-forward请求，并记录生成client与RPC server调用证据。 本子例输入：启动单平台Pod；经HTTP Service/port-forward请求，并记录生成client与RPC server调用证据。。 | 单Go进程真实经过Kitex；HTTP可达；RPC只监听Pod loopback且无Service端口；无宿主机业务旁路。 | #21 |
| T03.primary | 两集群均可部署观测组件 | 从平台Pod分别注册管控/数据集群，在两边各下发KSM和Agent并读取对象。 本子例输入：从平台Pod分别注册管控/数据集群，在两边各下发KSM和Agent并读取对象。。 | 目标UID独立；CR/workload出现在所选API；本地Operator分别调谐；同集群平台也经过注册授权。 | #21 |
| T04.loopback | 识别Pod不可达地址和TLS错误 | 分别提交指向平台Pod自身loopback的API地址、错误CA或名称的测试kubeconfig。 本子例输入：API endpoint 指向平台 Pod 自身未监听的 loopback 地址。 | 连通失败，不保存为有效注册 | #21 |
| T04.wrong-ca | 识别Pod不可达地址和TLS错误 | 分别提交指向平台Pod自身loopback的API地址、错误CA或名称的测试kubeconfig。 本子例输入：目标可达，但 CA 错误。 | TLS 校验失败，不能降级跳过校验 | #21 |
| T04.wrong-name | 识别Pod不可达地址和TLS错误 | 分别提交指向平台Pod自身loopback的API地址、错误CA或名称的测试kubeconfig。 本子例输入：目标可达，但 TLS serverName 不匹配。 | 名称核验失败，不能自动更改 endpoint | #21 |
| T05.primary | 平台Pod重建保留管理状态 | 保存账号/草稿/历史/幂等记录和密文；删除平台Pod；使用原卷与密钥重建。 本子例输入：保存账号/草稿/历史/幂等记录和密文；删除平台Pod；使用原卷与密钥重建。。 | 数据、原密钥及有效Session恢复；SQLite可读写；任务按恢复规则处理，不因Pod消失被当作删除。 | #21 |
| T06.primary | 缺密钥不能破坏已有密文 | 在有凭据密文的测试卷上移走密钥后启动，再恢复原密钥。 本子例输入：在有凭据密文的测试卷上移走密钥后启动，再恢复原密钥。。 | 缺密钥时启动明确失败且不生成替代密钥；恢复原密钥后可解密；文件owner/mode符合0600要求。 | #21 |
| T07.db | 就绪必须覆盖DB与真实RPC | 参数化注入数据库不可用、RPC探测失败、listener占用及单目标API断联。 本子例输入：数据库无法打开或探测失败。 | 不 ready，清理半启动资源 | #21 |
| T07.rpc | 就绪必须覆盖DB与真实RPC | 参数化注入数据库不可用、RPC探测失败、listener占用及单目标API断联。 本子例输入：真实 RPC 探测失败。 | 不 ready，静态页面可访问不能代替业务探测 | #21 |
| T07.listener | 就绪必须覆盖DB与真实RPC | 参数化注入数据库不可用、RPC探测失败、listener占用及单目标API断联。 本子例输入：HTTP 或 RPC 所需 listener 已被占用，分别执行。 | 不 ready，另一半 listener 被清理 | #21 |
| T07.target-offline | 就绪必须覆盖DB与真实RPC | 参数化注入数据库不可用、RPC探测失败、listener占用及单目标API断联。 本子例输入：只有一个已注册目标 API 断联。 | 平台仍 ready，目标状态单独失败 | #21 |
| T08.primary | 退出不误报取消或失败 | 在有排队/发送中任务时终止平台Pod；观测停止接收、等待及重启记录。 本子例输入：在有排队/发送中任务时终止平台Pod；观测停止接收、等待及重启记录。。 | 停止新操作/派发，默认等待10秒；未知结果落盘、DB最后关闭；恢复不自动重放。 | #21 |
| I01.primary | 初始化与临时密码流程 | 空库仅首次初始化管理员；由管理员创建临时密码用户，首次登录、改密后重新登录。 本子例输入：空库仅首次初始化管理员；由管理员创建临时密码用户，首次登录、改密后重新登录。。 | 已有账号时不能再次初始化；密码以哈希保存；临时密码用户首次登录必须改密，旧Session失效，改密后正常访问自身资源。 | #21 |
| I02.default-expiry | Session绝对到期与Cookie安全 | 用可控时钟验证默认8小时及配置值；检查Set-Cookie、JSON、错误和日志。 本子例输入：Session 默认 8 小时，连续轮询跨越到期时刻。 | 到期后 401，轮询不能延长绝对寿命 | #21 |
| I02.configured-expiry | Session绝对到期与Cookie安全 | 用可控时钟验证默认8小时及配置值；检查Set-Cookie、JSON、错误和日志。 本子例输入：使用不同的合法 Session 寿命配置。 | 按配置绝对到期，重启保留仍有效会话 | #21 |
| I02.cookie | Session绝对到期与Cookie安全 | 用可控时钟验证默认8小时及配置值；检查Set-Cookie、JSON、错误和日志。 本子例输入：检查 Set-Cookie、同源变更请求、响应和日志。 | HttpOnly；SameSite/Path 与入口相容，HTTPS 时 Secure；Origin/CSRF 检查有效，token 仅专用响应头交付 | #21 |
| I03.primary | HTTP与直接RPC都隔离账号 | 两用户分别归属两个集群；逐一尝试对方的列表、详情、保存、下发和删除。 本子例输入：两用户分别归属两个集群；逐一尝试对方的列表、详情、保存、下发和删除。。 | 列表只含授权对象；越权命令明确拒绝、无副作用；伪造账号/角色或换传输入口不能绕过。 | #21 |
| I04.primary | 归属转移立即改变权限 | 管理员转移无在途任务的集群，再用新旧所属用户访问其草稿与历史。 本子例输入：管理员转移无在途任务的集群，再用新旧所属用户访问其草稿与历史。。 | 原用户立即失权；新用户接收原配置/历史；其他管理员仍有全局权限；审计保留前后归属。 | #21 |
| I05.primary | 任务写前按当前权限授权 | 受理后在派发前停用/撤权发起账号；另在请求已发后撤权。 本子例输入：受理后在派发前停用/撤权发起账号；另在请求已发后撤权。。 | 未发写被阻止；已发请求继续核验真实结果、不回滚；后台读取不依赖原用户Session。 | #21 |
| I06.primary | Cookie到期不取消已受理任务 | 持久化任务后令其Session到期，保持账号权限有效并执行任务。 本子例输入：持久化任务后令其Session到期，保持账号权限有效并执行任务。。 | 旧用户请求401；任务仍按当前账号权限完成或如实核验，不因Cookie到期自动取消。 | #21 |
| I07.reset | 账号生命周期与最后管理员保护 | 参数化重置/停用/变更角色；尝试停用或降级最后启用管理员，并指定停用用户为归属。 本子例输入：管理员重置密码。 | 旧 Session 失效，历史保留 | #21 |
| I07.disable | 账号生命周期与最后管理员保护 | 参数化重置/停用/变更角色；尝试停用或降级最后启用管理员，并指定停用用户为归属。 本子例输入：管理员停用普通账号。 | 旧 Session 失效，已有归属保留且由管理员管理 | #21 |
| I07.role-change | 账号生命周期与最后管理员保护 | 参数化重置/停用/变更角色；尝试停用或降级最后启用管理员，并指定停用用户为归属。 本子例输入：授予或撤销管理员角色。 | 旧 Session 失效，新请求遵守当前角色 | #21 |
| I07.last-admin-disable | 账号生命周期与最后管理员保护 | 参数化重置/停用/变更角色；尝试停用或降级最后启用管理员，并指定停用用户为归属。 本子例输入：停用最后一名启用管理员。 | 拒绝且仍保留一名启用管理员 | #21 |
| I07.last-admin-demote | 账号生命周期与最后管理员保护 | 参数化重置/停用/变更角色；尝试停用或降级最后启用管理员，并指定停用用户为归属。 本子例输入：撤销最后一名启用管理员的角色。 | 拒绝且仍保留一名启用管理员 | #21 |
| I07.disabled-owner | 账号生命周期与最后管理员保护 | 参数化重置/停用/变更角色；尝试停用或降级最后启用管理员，并指定停用用户为归属。 本子例输入：注册、转移、重新启用时分别指定停用用户。 | 各入口拒绝停用归属用户 | #21 |
| C01.client-cert | 有效注册不创建观测负载 | 用内嵌HTTPS CA和cert/key或token、明确context注册；检查SSR、UID和资源变化。 本子例输入：明确 context，HTTPS 内嵌 CA 和客户端证书/私钥。 | 身份与 UID 核验通过后保存，不创建观测负载 | #21 |
| C01.token | 有效注册不创建观测负载 | 用内嵌HTTPS CA和cert/key或token、明确context注册；检查SSR、UID和资源变化。 本子例输入：明确 context，HTTPS 内嵌 CA 与有效 token。 | 核验有效非匿名身份，不将平台账号当 Kubernetes 身份 | #21 |
| C02.primary | 同集群不同context不能重复注册 | 以另一context/凭据再次注册同一kube-system Namespace UID。 本子例输入：以另一context/凭据再次注册同一kube-system Namespace UID。。 | 报重复关联，原clusterId、归属和历史保留；引导更新凭据，不另建重复记录。 | #21 |
| C03.missing | 注册拒绝未核验身份 | 测试API分别返回SSR缺失、Forbidden、匿名和错误；其他version/权限审查仍成功。 本子例输入：SSR API 不存在，其他公开接口成功。 | 拒绝注册，不伪称已运行 Kubernetes 1.19 | #21 |
| C03.forbidden | 注册拒绝未核验身份 | 测试API分别返回SSR缺失、Forbidden、匿名和错误；其他version/权限审查仍成功。 本子例输入：SSR 返回 Forbidden。 | 拒绝注册，明确权限原因 | #21 |
| C03.anonymous | 注册拒绝未核验身份 | 测试API分别返回SSR缺失、Forbidden、匿名和错误；其他version/权限审查仍成功。 本子例输入：SSR 返回匿名身份。 | 拒绝注册，不以 SSAR 成功代替 | #21 |
| C03.error | 注册拒绝未核验身份 | 测试API分别返回SSR缺失、Forbidden、匿名和错误；其他version/权限审查仍成功。 本子例输入：SSR 返回受控服务错误。 | 拒绝注册，不保存为已生效 | #21 |
| C04.ksm-crd | 缺CRD可登记但不能下发 | 在独占数据集群fixture中移除相应CRD，使用有效身份注册/重检后发起下发。 本子例输入：独占 fixture 缺 KubeStateMetrics CRD。 | 允许登记，KSM 下发受阻并说明原因 | #21 |
| C04.agent-crd | 缺CRD可登记但不能下发 | 在独占数据集群fixture中移除相应CRD，使用有效身份注册/重检后发起下发。 本子例输入：独占 fixture 缺 PrometheusAgent CRD。 | 允许登记，Agent 下发受阻并说明原因 | #21 |
| C05.primary | 按实际namespace核验权限 | 使用有身份/UID读取权限、但无某目标namespace写权限的测试凭据登记并发布。 本子例输入：使用有身份/UID读取权限、但无某目标namespace写权限的测试凭据登记并发布。。 | 登记允许且展示受限；目标发布被拒绝、无越权资源；其他有权namespace行为不被混淆。 | #21 |
| C06.bad-candidate | 凭据验证后才切换 | 参数化验证坏凭据、同集群好凭据，并在待确认任务中更新；记录切换前后读取。 本子例输入：候选凭据无法核验。 | 原凭据不被替换 | #21 |
| C06.same-uid | 凭据验证后才切换 | 参数化验证坏凭据、同集群好凭据，并在待确认任务中更新；记录切换前后读取。 本子例输入：候选凭据核验为原 UID，原任务结果待确认。 | 切换后可恢复读取，不自动重放写请求 | #21 |
| C07.primary | 注销与重新启用保留资源历史 | 有在途任务时尝试转移/注销；结束后注销，检查凭据清除；重新上传有效kubeconfig并指定启用用户后重新启用。 本子例输入：有在途任务时尝试转移/注销；结束后注销，检查凭据清除；重新上传有效kubeconfig并指定启用用户后重新启用。。 | 在途时拒绝；注销清除平台保存的集群凭据、停止同步/下发，保留CR、workload及历史；重新启用核对原UID/身份与启用归属后恢复读取，不自动下发。 | #21 |
| C08.primary | 凭据指向新UID不能继承旧集群 | 用数据集群凭据更新管控集群记录，UID与原值不同。 本子例输入：用数据集群凭据更新管控集群记录，UID与原值不同。。 | 拒绝按原集群切换/继承；旧身份历史保留，新集群须走独立注册。 | #21 |
| C09.http | 拒绝不支持的kubeconfig依赖 | 分别输入HTTP、跳过TLS、依赖外部证书路径、tokenFile、exec/auth-provider或proxy的配置。 本子例输入：HTTP API endpoint。 | 拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | #21 |
| C09.skip-tls | 拒绝不支持的kubeconfig依赖 | 分别输入HTTP、跳过TLS、依赖外部证书路径、tokenFile、exec/auth-provider或proxy的配置。 本子例输入：insecure-skip-tls-verify。 | 拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | #21 |
| C09.external-ca | 拒绝不支持的kubeconfig依赖 | 分别输入HTTP、跳过TLS、依赖外部证书路径、tokenFile、exec/auth-provider或proxy的配置。 本子例输入：外部 CA 路径。 | 拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | #21 |
| C09.external-cert | 拒绝不支持的kubeconfig依赖 | 分别输入HTTP、跳过TLS、依赖外部证书路径、tokenFile、exec/auth-provider或proxy的配置。 本子例输入：外部客户端证书/私钥路径。 | 拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | #21 |
| C09.token-file | 拒绝不支持的kubeconfig依赖 | 分别输入HTTP、跳过TLS、依赖外部证书路径、tokenFile、exec/auth-provider或proxy的配置。 本子例输入：tokenFile。 | 拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | #21 |
| C09.exec | 拒绝不支持的kubeconfig依赖 | 分别输入HTTP、跳过TLS、依赖外部证书路径、tokenFile、exec/auth-provider或proxy的配置。 本子例输入：exec 插件。 | 拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | #21 |
| C09.auth-provider | 拒绝不支持的kubeconfig依赖 | 分别输入HTTP、跳过TLS、依赖外部证书路径、tokenFile、exec/auth-provider或proxy的配置。 本子例输入：auth-provider。 | 拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | #21 |
| C09.proxy | 拒绝不支持的kubeconfig依赖 | 分别输入HTTP、跳过TLS、依赖外部证书路径、tokenFile、exec/auth-provider或proxy的配置。 本子例输入：proxy 配置。 | 拒绝所选 context 的不支持依赖，不执行插件、读取外部文件或降级 TLS | #21 |
| K01.primary | KSM多实例与外部只读 | 创建两个自管实例和一个外部CR；只修改/删除其中一个自管实例。 本子例输入：创建两个自管实例和一个外部CR；只修改/删除其中一个自管实例。。 | 按各自name/UID隔离；另一实例不变；外部实例不产生平台发布历史且不能编辑、下发、删除或接管。 | #21 |
| K02.primary | Deployment与资源预设实际映射 | 保存并下发默认KSM Deployment；回读CR、容器resources和Ready Pod。 本子例输入：保存并下发默认KSM Deployment；回读CR、容器resources和Ready Pod。。 | 单副本Deployment；省略分片分支；声明对应100m/250Mi/500Mi预设；真实Pod就绪，预设不被称为已验证容量。 | #21 |
| K03.primary | StatefulSet分片映射 | 选择StatefulSetSharded并设置2个shards后下发。 本子例输入：选择StatefulSetSharded并设置2个shards后下发。。 | 仅相应分片分支；StatefulSet副本等于2且实际Pod收敛；小于2的输入被拒绝。 | #21 |
| K04.primary | DaemonSet三角色拓扑 | 选择DaemonSetSharded及pods+非Pod collector后下发，读取工作负载。 本子例输入：选择DaemonSetSharded及pods+非Pod collector后下发，读取工作负载。。 | 出现node DaemonSet及cluster/unscheduled两个Deployment；无手工数量字段；非法collector组合不能下发。 | #21 |
| K05.primary | 模式切换遵循替换边界 | 依次切换Deployment、StatefulSetSharded、DaemonSetSharded并记录workload UID事件。 本子例输入：依次切换Deployment、StatefulSetSharded、DaemonSetSharded并记录workload UID事件。。 | 旧模式工作负载先消失，再形成新模式；不残留互相冲突的旧负载，不承诺无损滚动。 | #21 |
| K06.node | 角色资源继承与整块覆盖 | 分别启用继承、独立整块覆盖，关闭再开启高级选项并下发。 本子例输入：node 角色依次继承、整块独立覆盖、关闭再开启高级配置。 | 继承不生成覆盖块；独立时整块替换；关闭不下发且保留草稿值，再开启恢复 | #21 |
| K06.cluster | 角色资源继承与整块覆盖 | 分别启用继承、独立整块覆盖，关闭再开启高级选项并下发。 本子例输入：cluster 角色依次继承、整块独立覆盖、关闭再开启高级配置。 | 继承不生成覆盖块；独立时整块替换；关闭不下发且保留草稿值，再开启恢复 | #21 |
| K06.unscheduled | 角色资源继承与整块覆盖 | 分别启用继承、独立整块覆盖，关闭再开启高级选项并下发。 本子例输入：unscheduled 角色依次继承、整块独立覆盖、关闭再开启高级配置。 | 继承不生成覆盖块；独立时整块替换；关闭不下发且保留草稿值，再开启恢复 | #21 |
| K07.default-all | collector与指标过滤映射 | 参数化Default/明确collector和指标All/Allow/Deny等已支持模式，检查CR及容器参数。 本子例输入：collectors=Default，metricFilter=All。 | 仅映射该模式启用的 names/patterns；Default/All 保持省略，Include 非空；Allow/Deny 保留正则含义 | #21 |
| K07.default-allow | collector与指标过滤映射 | 参数化Default/明确collector和指标All/Allow/Deny等已支持模式，检查CR及容器参数。 本子例输入：collectors=Default，metricFilter=Allow。 | 仅映射该模式启用的 names/patterns；Default/All 保持省略，Include 非空；Allow/Deny 保留正则含义 | #21 |
| K07.default-deny | collector与指标过滤映射 | 参数化Default/明确collector和指标All/Allow/Deny等已支持模式，检查CR及容器参数。 本子例输入：collectors=Default，metricFilter=Deny。 | 仅映射该模式启用的 names/patterns；Default/All 保持省略，Include 非空；Allow/Deny 保留正则含义 | #21 |
| K07.include-all | collector与指标过滤映射 | 参数化Default/明确collector和指标All/Allow/Deny等已支持模式，检查CR及容器参数。 本子例输入：collectors=Include，metricFilter=All。 | 仅映射该模式启用的 names/patterns；Default/All 保持省略，Include 非空；Allow/Deny 保留正则含义 | #21 |
| K07.include-allow | collector与指标过滤映射 | 参数化Default/明确collector和指标All/Allow/Deny等已支持模式，检查CR及容器参数。 本子例输入：collectors=Include，metricFilter=Allow。 | 仅映射该模式启用的 names/patterns；Default/All 保持省略，Include 非空；Allow/Deny 保留正则含义 | #21 |
| K07.include-deny | collector与指标过滤映射 | 参数化Default/明确collector和指标All/Allow/Deny等已支持模式，检查CR及容器参数。 本子例输入：collectors=Include，metricFilter=Deny。 | 仅映射该模式启用的 names/patterns；Default/All 保持省略，Include 非空；Allow/Deny 保留正则含义 | #21 |
| K08.all | namespace选择及合法零匹配 | 分别验证已有literal names与namePatterns的并集；零匹配子例清空names，仅使用当前无匹配的namePatterns。 本子例输入：namespaceFilter=All。 | names/namePatterns 省略，不伪造明确全集 | #21 |
| K08.union | namespace选择及合法零匹配 | 分别验证已有literal names与namePatterns的并集；零匹配子例清空names，仅使用当前无匹配的namePatterns。 本子例输入：已有 literal names 与匹配完整 namespace 名称的 namePatterns 同时配置。 | 按并集去重，不自动添加模式外 namespace | #21 |
| K08.dynamic-pattern | namespace选择及合法零匹配 | 分别验证已有literal names与namePatterns的并集；零匹配子例清空names，仅使用当前无匹配的namePatterns。 本子例输入：names=[]，增删被 namePatterns 匹配的 namespace。 | 匹配集合动态改变，CR/运行状态按真实读取解释 | #21 |
| K08.zero | namespace选择及合法零匹配 | 分别验证已有literal names与namePatterns的并集；零匹配子例清空names，仅使用当前无匹配的namePatterns。 本子例输入：names=[]，仅提供当前无匹配的 namePatterns。 | 有效状态最终确认无匹配、0/0；不自动放宽为 All | #21 |
| K09.primary | 运行挂起停用独立于保存 | 依次保存但不发、明确下发挂起/恢复/停用；检查CR与负载。 本子例输入：依次保存但不发、明确下发挂起/恢复/停用；检查CR与负载。。 | 仅保存不改集群；发布后按各意图收敛；状态、数量和发布结果分别表达。 | #21 |
| K10.name | 名称与不可变字段在后端校验 | 直接HTTP/RPC传入中文/非法名称、重复身份，首次成功创建后改name或targetNamespace，或传原始YAML/未知字段。 本子例输入：分别经 HTTP/直接 RPC 提交空名称、中文或非法 DNS subdomain。 | 字段级拒绝，不自动生成名称 | #21 |
| K10.collision | 名称与不可变字段在后端校验 | 直接HTTP/RPC传入中文/非法名称、重复身份，首次成功创建后改name或targetNamespace，或传原始YAML/未知字段。 本子例输入：分别经 HTTP/直接 RPC 提交重复 CR 身份。 | 冲突，不接管已有 CR | #21 |
| K10.fixed-target | 名称与不可变字段在后端校验 | 直接HTTP/RPC传入中文/非法名称、重复身份，首次成功创建后改name或targetNamespace，或传原始YAML/未知字段。 本子例输入：首次成功创建 CR 后尝试更改 name 或 spec.targetNamespace。 | 拒绝修改固定身份；不把仅保存草稿当作已部署 | #21 |
| K10.unsupported | 名称与不可变字段在后端校验 | 直接HTTP/RPC传入中文/非法名称、重复身份，首次成功创建后改name或targetNamespace，或传原始YAML/未知字段。 本子例输入：分别提交原始 YAML 或未知配置字段。 | 拒绝透传，不写集群 | #21 |
| A01.primary | Agent实际StatefulSet与预设 | 默认运行配置下发后读取Agent、StatefulSet和Pod容器声明。 本子例输入：默认运行配置下发后读取Agent、StatefulSet和Pod容器声明。。 | 每shard正常1副本、paused=false；200m/512Mi/1Gi声明正确；真实工作负载就绪。 | #21 |
| A02.primary | Agent shards与挂起恢复 | 设置多个shards，下发挂起再恢复；回读每组StatefulSet及Pod数量。 本子例输入：设置多个shards，下发挂起再恢复；回读每组StatefulSet及Pod数量。。 | 每shard单副本；运行/挂起分别replicas=1/0，paused保持false；实际数量与运行摘要一致。 | #21 |
| A03.primary | Agent实例与存储身份隔离 | 创建多个Agent及外部Agent，同名配置在不同namespace/集群分别操作。 本子例输入：创建多个Agent及外部Agent，同名配置在不同namespace/集群分别操作。。 | 实例、UID及各卷独立；不越界；外部只读不生成平台发布/重试历史。 | #21 |
| A04.primary | 默认PVC为每卷1Gi | 以默认StorageClass和平台默认存储发布Agent；读取PVC请求、绑定及Pod挂载。 本子例输入：以默认StorageClass和平台默认存储发布Agent；读取PVC请求、绑定及Pod挂载。。 | PVC每卷请求1Gi、RWO、实际Bound并被目标Pod挂载；不沿用旧fixture的64Mi。 | #21 |
| A05.primary | 无默认StorageClass阻止PVC发布 | 在独占数据集群fixture取消默认StorageClass，使用默认PVC配置预览/发布。 本子例输入：在独占数据集群fixture取消默认StorageClass，使用默认PVC配置预览/发布。。 | 明确依赖受阻且不创建Agent负载；原草稿保留；恢复默认class后明确重试可继续。 | #21 |
| A06.primary | emptyDir不创建PVC | 首次创建时选择emptyDir并下发、挂起和删除。 本子例输入：首次创建时选择emptyDir并下发、挂起和删除。。 | Pod使用emptyDir；不创建WAL PVC，不显示虚构的PVC保留/删除结果。 | #21 |
| A07.retain | whenScaled作用于挂起缩零 | 参数化Retain/Delete，先单独发布并核验实际策略，再挂起。 本子例输入：先确认实际 whenScaled=Retain，再挂起缩零。 | Retain 保留原 PVC | #21 |
| A07.delete | whenScaled作用于挂起缩零 | 参数化Retain/Delete，先单独发布并核验实际策略，再挂起。 本子例输入：先确认实际 whenScaled=Delete，再挂起缩零。 | Delete 最终删除相应 PVC，独立保存证据 | #21 |
| A08.retain | whenDeleted作用于删除 | 参数化Retain/Delete，先确认策略再删除Agent，分别读取CR、workload、Pod和PVC。 本子例输入：先确认实际 whenDeleted=Retain，再删除 Agent。 | CR/workload/Pod 全消失才完成；PVC 保留不阻止完成 | #21 |
| A08.delete | whenDeleted作用于删除 | 参数化Retain/Delete，先确认策略再删除Agent，分别读取CR、workload、Pod和PVC。 本子例输入：先确认实际 whenDeleted=Delete，再删除 Agent。 | 实例完成与 PVC 删除分别回读，不以 CR 消失代替卷核验 | #21 |
| A09.retain | 减少shards的实际PVC结果 | 运行多shard，确认whenDeleted策略后减少shards，观察被移除StatefulSet及卷。 本子例输入：多个 shard 已运行且 whenDeleted=Retain，然后减少 shards。 | 仅已移除 StatefulSet 的卷按 whenDeleted 处理，保留 shard 不受损；不能误用 whenScaled | #21 |
| A09.delete | 减少shards的实际PVC结果 | 运行多shard，确认whenDeleted策略后减少shards，观察被移除StatefulSet及卷。 本子例输入：多个 shard 已运行且 whenDeleted=Delete，然后减少 shards。 | 仅已移除 StatefulSet 的卷按 whenDeleted 处理，保留 shard 不受损；不能误用 whenScaled | #21 |
| A10.initial-capacity | 容量与模式变更必须新实例 | 首次创建前自定义容量；创建后尝试改容量/存储模式/name/namespace，再按新实例重建。 本子例输入：首次创建前使用非默认合法容量。 | 新 PVC 请求为该容量 | #21 |
| A10.fixed-capacity | 容量与模式变更必须新实例 | 首次创建前自定义容量；创建后尝试改容量/存储模式/name/namespace，再按新实例重建。 本子例输入：创建成功后修改容量。 | 后端拒绝，不虚报旧 PVC 扩容成功 | #21 |
| A10.fixed-mode | 容量与模式变更必须新实例 | 首次创建前自定义容量；创建后尝试改容量/存储模式/name/namespace，再按新实例重建。 本子例输入：创建成功后切换 PVC/emptyDir。 | 后端拒绝，需要新实例 | #21 |
| A10.fixed-identity | 容量与模式变更必须新实例 | 首次创建前自定义容量；创建后尝试改容量/存储模式/name/namespace，再按新实例重建。 本子例输入：创建成功后修改 namespace/name。 | 后端拒绝，不迁移原资源 | #21 |
| A10.new-instance | 容量与模式变更必须新实例 | 首次创建前自定义容量；创建后尝试改容量/存储模式/name/namespace，再按新实例重建。 本子例输入：复制旧配置后提供新 name/身份并创建。 | 新 CR UID 和新 PVC，不复用 Retain 残留卷 | #21 |
| A11.servicemonitor-none-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ServiceMonitor：object=none，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.servicemonitor-none-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ServiceMonitor：object=none，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.servicemonitor-none-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ServiceMonitor：object=none，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.servicemonitor-all-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ServiceMonitor：object=all，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.servicemonitor-all-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ServiceMonitor：object=all，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.servicemonitor-all-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ServiceMonitor：object=all，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.servicemonitor-labels-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ServiceMonitor：object=labels，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.servicemonitor-labels-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ServiceMonitor：object=labels，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.servicemonitor-labels-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ServiceMonitor：object=labels，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.podmonitor-none-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：PodMonitor：object=none，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.podmonitor-none-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：PodMonitor：object=none，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.podmonitor-none-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：PodMonitor：object=none，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.podmonitor-all-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：PodMonitor：object=all，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.podmonitor-all-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：PodMonitor：object=all，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.podmonitor-all-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：PodMonitor：object=all，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.podmonitor-labels-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：PodMonitor：object=labels，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.podmonitor-labels-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：PodMonitor：object=labels，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.podmonitor-labels-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：PodMonitor：object=labels，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.probe-none-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：Probe：object=none，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.probe-none-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：Probe：object=none，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.probe-none-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：Probe：object=none，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.probe-all-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：Probe：object=all，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.probe-all-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：Probe：object=all，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.probe-all-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：Probe：object=all，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.probe-labels-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：Probe：object=labels，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.probe-labels-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：Probe：object=labels，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.probe-labels-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：Probe：object=labels，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.scrapeconfig-none-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ScrapeConfig：object=none，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.scrapeconfig-none-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ScrapeConfig：object=none，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.scrapeconfig-none-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ScrapeConfig：object=none，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.scrapeconfig-all-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ScrapeConfig：object=all，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.scrapeconfig-all-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ScrapeConfig：object=all，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.scrapeconfig-all-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ScrapeConfig：object=all，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.scrapeconfig-labels-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ScrapeConfig：object=labels，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.scrapeconfig-labels-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ScrapeConfig：object=labels，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A11.scrapeconfig-labels-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ScrapeConfig：object=labels，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #21 |
| A12.http-none | remoteWrite与认证TLS正确引用 | 配置HTTP/HTTPS、多endpoint、BasicAuth/Bearer及TLS引用；读Agent和实际生成配置。 本子例输入：HTTP endpoint，无认证。 | 合法 URL 映射正确，不能声称样本已到达 | #21 |
| A12.https-basic | remoteWrite与认证TLS正确引用 | 配置HTTP/HTTPS、多endpoint、BasicAuth/Bearer及TLS引用；读Agent和实际生成配置。 本子例输入：HTTPS endpoint，BasicAuth username/password Secret key 引用。 | 引用与受控生成配置正确，平台不保存 Secret 内容 | #21 |
| A12.https-bearer | remoteWrite与认证TLS正确引用 | 配置HTTP/HTTPS、多endpoint、BasicAuth/Bearer及TLS引用；读Agent和实际生成配置。 本子例输入：HTTPS endpoint，authorization Bearer credentials Secret 引用。 | 不退化为明文 token 字段；证据脱敏 | #21 |
| A12.tls-ca | remoteWrite与认证TLS正确引用 | 配置HTTP/HTTPS、多endpoint、BasicAuth/Bearer及TLS引用；读Agent和实际生成配置。 本子例输入：HTTPS endpoint，Secret CA 引用。 | 保持 TLS 校验开启且引用准确 | #21 |
| A12.tls-client | remoteWrite与认证TLS正确引用 | 配置HTTP/HTTPS、多endpoint、BasicAuth/Bearer及TLS引用；读Agent和实际生成配置。 本子例输入：HTTPS endpoint，成对客户端证书/私钥和 serverName。 | 配对及 serverName 映射正确，秘密不泄露 | #21 |
| A12.multiple | remoteWrite与认证TLS正确引用 | 配置HTTP/HTTPS、多endpoint、BasicAuth/Bearer及TLS引用；读Agent和实际生成配置。 本子例输入：多个 endpoint，分别设置唯一可选 name、合法 remoteTimeout。 | 全部受支持字段保持，重复 name 拒绝；不开放 queue/headers/writeRelabel 等高级透传 | #21 |
| A13.missing-secret | 缺失Secret key或SA不能下发 | 参数化缺Secret、key和ServiceAccount后发起预览/发布，再修复相应fixture。 本子例输入：配置引用不存在的 Secret。 | 依赖错误，阻止发布 | #21 |
| A13.missing-key | 缺失Secret key或SA不能下发 | 参数化缺Secret、key和ServiceAccount后发起预览/发布，再修复相应fixture。 本子例输入：Secret 存在但所需 key 缺失。 | 明确 key 错误，不能 optional 绕过 | #21 |
| A13.forbidden-secret | 缺失Secret key或SA不能下发 | 参数化缺Secret、key和ServiceAccount后发起预览/发布，再修复相应fixture。 本子例输入：Secret 存在但访问被拒绝。 | 不能当作依赖可用或空值，不泄露内容 | #21 |
| A13.missing-sa | 缺失Secret key或SA不能下发 | 参数化缺Secret、key和ServiceAccount后发起预览/发布，再修复相应fixture。 本子例输入：ServiceAccount 不存在。 | 阻止发布，平台不自动补建 | #21 |
| A13.insufficient-sa | 缺失Secret key或SA不能下发 | 参数化缺Secret、key和ServiceAccount后发起预览/发布，再修复相应fixture。 本子例输入：已有 ServiceAccount 明确缺少所需采集权限。 | 显示原因并阻止发布，不自动补 RBAC | #21 |
| P01.primary | 保存不产生集群写 | 记录平台集群mutation请求及CR spec，修改并保存草稿后回读；将独立status更新与保存动作分别取证。 本子例输入：记录平台集群mutation请求及CR spec，修改并保存草稿后回读；将独立status更新与保存动作分别取证。。 | 仅DB草稿修订推进，平台集群写请求为0、CR spec未因保存变化；独立status/RV可变化，页面不显示已下发。 | #21 |
| P02.primary | 不可变发布与来源快照 | 受理草稿vN发布后继续保存新草稿；读取固定版本、任务及最终CR。 本子例输入：受理草稿vN发布后继续保存新草稿；读取固定版本、任务及最终CR。。 | 受理版本与sourceDraftRevision冻结；新草稿不污染该次发布；确认写入前不提升最近写入版本。 | #21 |
| P03.same | 重复点击只受理一次 | 并发发送同键同内容，再以同键不同内容和另一个变更请求提交。 本子例输入：同账号同动作同目标，同键同内容并发。 | 返回同一 operationId 与当前结果，实际副作用不重复 | #21 |
| P03.different-content | 重复点击只受理一次 | 并发发送同键同内容，再以同键不同内容和另一个变更请求提交。 本子例输入：同作用域同键但内容不同。 | 409 幂等冲突 | #21 |
| P03.another-active | 重复点击只受理一次 | 并发发送同键同内容，再以同键不同内容和另一个变更请求提交。 本子例输入：实例已有未结束操作时使用不同键发起变更。 | 拒绝第二变更，仍可保存新草稿 | #21 |
| P04.draft-race | 过期修订或预览不能提交别版 | 并发保存同修订；预览后修改草稿或目标配置再确认。 本子例输入：两个客户端以同一 expectedDraftRevision 保存。 | 只有一个成功，另一冲突并保留本地编辑 | #21 |
| P04.draft-after-preview | 过期修订或预览不能提交别版 | 并发保存同修订；预览后修改草稿或目标配置再确认。 本子例输入：预览后另存新修订，再确认旧预览。 | 要求新预览，不能静默换版 | #21 |
| P04.target-after-preview | 过期修订或预览不能提交别版 | 并发保存同修订；预览后修改草稿或目标配置再确认。 本子例输入：预览后实际目标配置改变。 | 配置基线冲突，需重新确认 | #21 |
| P05.supported-drift | 漂移覆盖与非平台元数据保护 | 外部改配置并添加额外spec及非平台labels/annotations；先普通发布，再明确覆盖。 本子例输入：外部修改已支持 spec，同时保留非平台元数据。 | 阻止普通发布，明确覆盖后恢复受支持 spec 并保留非平台 labels/annotations | #21 |
| P05.extra-spec | 漂移覆盖与非平台元数据保护 | 外部改配置并添加额外spec及非平台labels/annotations；先普通发布，再明确覆盖。 本子例输入：外部添加平台不支持 spec 字段。 | diff 左侧显示将删除字段；明确覆盖不得删除 status/finalizers | #21 |
| P06.status-only | status变化不重复确认配置 | 预览后仅改变status/RV或等价默认表现，再提交同配置；另改变实际spec。 本子例输入：预览后只变 status。 | 重读新 RV 后可继续，无需重复确认配置 | #21 |
| P06.rv-only | status变化不重复确认配置 | 预览后仅改变status/RV或等价默认表现，再提交同配置；另改变实际spec。 本子例输入：配置等价，仅 RV 推进。 | 仍使用原 UID 与当前 RV 条件 | #21 |
| P06.default-equivalent | status变化不重复确认配置 | 预览后仅改变status/RV或等价默认表现，再提交同配置；另改变实际spec。 本子例输入：仅固定 CRD 语义下的等价默认值变化。 | 不误报漂移；selector nil/{} 不可当作等价 | #21 |
| P06.spec-change | status变化不重复确认配置 | 预览后仅改变status/RV或等价默认表现，再提交同配置；另改变实际spec。 本子例输入：预览后实际 spec 变化。 | 必须新预览，不能静默覆盖 | #21 |
| P07.primary | 失败重试仍用原固定版本 | 制造确定未成功的失败，保存新草稿后人工重试旧操作。 本子例输入：制造确定未成功的失败，保存新草稿后人工重试旧操作。。 | operationId/原发布版本保留、新attemptId增加；失败历史不抹掉，写入的是原配置而非新草稿。 | #21 |
| P08.cancel-wins | 取消与派发竞争只有一个结果 | 以可控派发屏障并发取消与发送资格提交，再放行。 本子例输入：取消先于派发资格提交。 | 已取消且没有集群写请求 | #21 |
| P08.dispatch-wins | 取消与派发竞争只有一个结果 | 以可控派发屏障并发取消与发送资格提交，再放行。 本子例输入：派发资格先提交并持久化可能已发送。 | 取消不报成功，按真实结果核验 | #21 |
| P09.response-drop | 受理响应丢失仍可找回任务 | 真实受理后丢弃HTTP响应/令请求超时，按请求键查询并重复提交同键。 本子例输入：事务已受理，HTTP 响应被丢弃。 | 按请求键找回同一任务，不创建第二任务 | #21 |
| P09.deadline | 受理响应丢失仍可找回任务 | 真实受理后丢弃HTTP响应/令请求超时，按请求键查询并重复提交同键。 本子例输入：事务已受理，受理 HTTP/RPC deadline 到期。 | 不取消已受理任务，同键仍返回原操作 | #21 |
| P10.primary | 已知UID更新响应丢失先核验 | 真实API执行更新后由测试transport丢弃响应；观察恢复读取。 本子例输入：真实API执行更新后由测试transport丢弃响应；观察恢复读取。。 | 进入待确认并保留保护；仅核验原UID配置，不自动重复写；确认后记录真实写入结果。 | #21 |
| P11.primary | 首次创建身份不明不能补认 | 创建在API生效但平台未保存UID时丢响应；令同名配置/labels完全匹配。 本子例输入：创建在API生效但平台未保存UID时丢响应；令同名配置/labels完全匹配。。 | 保持资源身份无法确认、拒绝后续更新/删除，不凭同名/内容/labels自动认领或补UID。 | #21 |
| P12.new-uid | 同名新UID与原CR缺失受保护 | 参数化外部替换同名新UID、外部删除原CR，再操作原平台实例。 本子例输入：外部删除原 CR 后以同名新 UID 重建。 | 原实例不得更新/删除新 CR，身份冲突保留历史 | #21 |
| P12.missing | 同名新UID与原CR缺失受保护 | 参数化外部替换同名新UID、外部删除原CR，再操作原平台实例。 本子例输入：外部删除原 CR 且不重建。 | 显示资源缺失，不自动重建；workload 仍需独立核验 | #21 |
| P13.unsent | Pod重启不重放任务 | 分别在明确未发写、可能已发写持久点终止平台Pod并用原卷重建。 本子例输入：在持久状态能证明未发写时重启 Pod。 | 等待人工继续/取消，继续产生新 attempt 且重新授权 | #21 |
| P13.possibly-sent | Pod重启不重放任务 | 分别在明确未发写、可能已发写持久点终止平台Pod并用原卷重建。 本子例输入：派发资格已提交，无法排除已发写时重启 Pod。 | 只恢复核验，不重放，不按超时解除保护 | #21 |
| P14.policy-pending | 清理阻塞和策略未生效不能报成功 | 参数化未采用新PVC策略的危险操作、带finalizer的删除、CR已没但Pod残留及辅助读取失败。 本子例输入：策略尚未作用于相关 StatefulSet 就挂起/减少 shards/删除。 | 先阻止，单独发布策略并确认后才能继续 | #21 |
| P14.finalizer | 清理阻塞和策略未生效不能报成功 | 参数化未采用新PVC策略的危险操作、带finalizer的删除、CR已没但Pod残留及辅助读取失败。 本子例输入：原 CR 带阻塞 finalizer，条件 foreground deletion。 | 保持清理中/受阻，平台不强删 finalizer | #21 |
| P14.pod-remains | 清理阻塞和策略未生效不能报成功 | 参数化未采用新PVC策略的危险操作、带finalizer的删除、CR已没但Pod残留及辅助读取失败。 本子例输入：原 CR 已消失但对应 workload/Pod 残留。 | 不能报告删除完成 | #21 |
| P14.read-failure | 清理阻塞和策略未生效不能报成功 | 参数化未采用新PVC策略的危险操作、带finalizer的删除、CR已没但Pod残留及辅助读取失败。 本子例输入：辅助清理核验权限/网络失败。 | 未知不是消失，保留保护 | #21 |
| P14.delete-response-drop | 清理阻塞和策略未生效不能报成功 | 参数化未采用新PVC策略的危险操作、带finalizer的删除、CR已没但Pod残留及辅助读取失败。 本子例输入：真实 API 处理删除后由受控 transport 丢弃响应。 | 恢复原 UID 与对应资源核验，不重发删除，不操作同名新 UID | #21 |
| R01.intervals | 轮询与过期边界可判定 | 可控时钟检查默认10秒、配置1/20秒及越界值；跨越成功观察30秒阈值并注入读取失败。 本子例输入：分别配置 1/10/20 秒与越界 0/21 秒。 | 边界合法，越界拒绝，默认仍 10 秒 | #21 |
| R01.freshness | 轮询与过期边界可判定 | 可控时钟检查默认10秒、配置1/20秒及越界值；跨越成功观察30秒阈值并注入读取失败。 本子例输入：可控时间分别为距成功 29/30/31 秒。 | 只有超过 30 秒才过期，不自动放宽 | #21 |
| R01.early-error | 轮询与过期边界可判定 | 可控时钟检查默认10秒、配置1/20秒及越界值；跨越成功观察30秒阈值并注入读取失败。 本子例输入：成功后不足 30 秒立即读取失败。 | 立即当前未知并显示历史，不等待阈值 | #21 |
| R02.primary | 单目标断联不污染其他目标 | 成功观测两集群后仅阻断一个目标API，再恢复。 本子例输入：成功观测两集群后仅阻断一个目标API，再恢复。。 | 受影响来源立即未知并保留历史；其他集群继续更新；平台整体ready，恢复后重新核对身份。 | #21 |
| R03.missing-status | 缺status与合法零值分开 | 参数化无status、字段合法省略、有效挂起/停用/零匹配及已删除。 本子例输入：整个 status 缺失。 | 运行摘要未知，不显示 0/0 可用 | #21 |
| R03.incomplete-status | 缺status与合法零值分开 | 参数化无status、字段合法省略、有效挂起/停用/零匹配及已删除。 本子例输入：status 不足以对应当前资源/配置。 | 未知，不能仅凭 Ready/Available 当作运行 | #21 |
| R03.ksm-suspended | 缺status与合法零值分开 | 参数化无status、字段合法省略、有效挂起/停用/零匹配及已删除。 本子例输入：有效状态确认 KSM 挂起且合法零值省略。 | 解释为挂起，不能把意图即刻当完成 | #21 |
| R03.ksm-disabled | 缺status与合法零值分开 | 参数化无status、字段合法省略、有效挂起/停用/零匹配及已删除。 本子例输入：有效状态确认 KSM 停用。 | 解释为停用，保留 CR 与历史 | #21 |
| R03.ksm-no-match | 缺status与合法零值分开 | 参数化无status、字段合法省略、有效挂起/停用/零匹配及已删除。 本子例输入：有效状态确认 namespace 零匹配。 | 解释为无匹配且 0/0 | #21 |
| R03.agent-zero | 缺status与合法零值分开 | 参数化无status、字段合法省略、有效挂起/停用/零匹配及已删除。 本子例输入：Agent replicas=0 且有效状态确认。 | 挂起与零副本分开解释，不因 Available=True 显示运行中 | #21 |
| R03.agent-paused | 缺status与合法零值分开 | 参数化无status、字段合法省略、有效挂起/停用/零匹配及已删除。 本子例输入：外部只读 Agent paused=true，仍有 Pod。 | 不能声称 Pod 已停止 | #21 |
| R03.deleted | 缺status与合法零值分开 | 参数化无status、字段合法省略、有效挂起/停用/零匹配及已删除。 本子例输入：原 CR/workload/Pod 全部核验消失。 | 生命周期已删除，不能冒充挂起或可用 | #21 |
| R04.primary | 部分副本就绪合并未就绪 | 令两副本中一个不可Ready，读取CR摘要及详情故障。 本子例输入：令两副本中一个不可Ready，读取CR摘要及详情故障。。 | 显示未就绪及实际1/2；保留故障原因，不再出现独立部分可用筛选或状态。 | #21 |
| R05.page | 批次部分失败不能伪造不存在 | 分页/批量读取中令某页或某项失败，同时其余对象成功。 本子例输入：分页 List 中一页失败。 | 未完整核验项未知，不按缺失/零匹配处理 | #21 |
| R05.item | 批次部分失败不能伪造不存在 | 分页/批量读取中令某页或某项失败，同时其余对象成功。 本子例输入：批量结果中一个目标失败而其他成功。 | 逐实例/来源记录独立时间和错误 | #21 |
| R06.primary | 请求预算及并发不被内部放大 | 用阻塞测试API与可控时钟发起大量请求、手动和后台重复刷新。 本子例输入：用阻塞测试API与可控时钟发起大量请求、手动和后台重复刷新。。 | HTTP/RPC/K8s遵守15/10/8秒及剩余预算；全局≤8、单集群≤4；同目标合并，不自动重放写请求。 | #21 |
| R07.pvc | 辅助来源新鲜度独立 | 令CR持续成功而PVC/关联预览读取被拒绝，再修复权限。 本子例输入：CR 成功，PVC 读取失败。 | PVC 历史/错误独立，CR 成功不能刷新 PVC 时间 | #21 |
| R07.pod | 辅助来源新鲜度独立 | 令CR持续成功而PVC/关联预览读取被拒绝，再修复权限。 本子例输入：CR 成功，Pod 读取失败。 | Pod 历史/错误独立，不能据此判清理完成 | #21 |
| R07.association | 辅助来源新鲜度独立 | 令CR持续成功而PVC/关联预览读取被拒绝，再修复权限。 本子例输入：CR 成功，采集关联预览读取失败。 | 关联未知不是零匹配，恢复按来源更新时间 | #21 |
| R08.primary | 重启首次读取前显示未知 | 有历史可用时重建平台Pod，并在首次读取前阻断目标API。 本子例输入：有历史可用时重建平台Pod，并在首次读取前阻断目标API。。 | 显示历史与当前未知，不能直接恢复可用标记；首次成功读取后才恢复当前判断。 | #21 |
| U01.primary | C方案实际页面与信息层级 | 浏览器经平台Service访问真实构建；检查1137×954页面、表头、两级集群信息与统计。 本子例输入：浏览器经平台Service访问真实构建；检查1137×954页面、表头、两级集群信息与统计。。 | 品牌为可观测管控平台；名称下有namespace，类型/组件及状态分列；无横向溢出、重复名称或Operator状态报告。 | #21 |
| U02.primary | 统一添加与名称行为 | 由单一添加入口按类型选择两类组件；尝试中文/空名、保存、发布及复制。 本子例输入：由单一添加入口按类型选择两类组件；尝试中文/空名、保存、发布及复制。。 | 展示exporter/collector与具体组件；名称直接成为CR name且不自动生成；创建后锁定，复制须新身份。 | #21 |
| U03.primary | 多选及轮询保持编辑位置 | 在KSM抽屉连续选常用collectors/集群namespace，滚动并等待后台刷新。 本子例输入：在KSM抽屉连续选常用collectors/集群namespace，滚动并等待后台刷新。。 | 常用项在前；多选来自所选集群；内容、焦点和滚动不跳回顶部，读取失败保留选择并提示。 | #21 |
| U04.primary | 搜索筛选和最近操作排序 | 组合名称字面搜索/类型/组件/状态、制造零结果并清除；编辑后刷新/等待后台完成。 本子例输入：组合名称字面搜索/类型/组件/状态、制造零结果并清除；编辑后刷新/等待后台完成。。 | AND及大小写规则正确，组件随类型收窄；统计不随筛选；显式操作置前、未纳管置底，查看和轮询不改顺序。 | #21 |
| U05.primary | 版本入口是原来源配置 | 保留未保存编辑，点击最近写入版本，再返回当前草稿；提交新版本及制造失败。 本子例输入：保留未保存编辑，点击最近写入版本，再返回当前草稿；提交新版本及制造失败。。 | 只读快照不被新草稿污染；返回后编辑保留；仅确认写入提升版本，失败/未部署/未纳管不虚构当前版本。 | #21 |
| U06.primary | YAML预览及确认后导航 | 预览两类CR的左右diff（含数组/selector），确认下发并查看运行信息与尝试历史。 本子例输入：预览两类CR的左右diff（含数组/selector），确认下发并查看运行信息与尝试历史。。 | YAML可解析且无孤立对象项横杠；确认立即进入运行信息，已接受/CR写入/运行分开，实际CR与预览语义一致。 | #21 |
| F01.absent | Thrift往返保留显式配置语义 | 用实际锁定生成参数往返编码外层缺失、空对象、空集合、显式模式及未知枚举。 本子例输入：实际生成类型的缺失 optional 外层配置。 | 缺失不意外变为空对象 | #21 |
| F01.empty-object | Thrift往返保留显式配置语义 | 用实际锁定生成参数往返编码外层缺失、空对象、空集合、显式模式及未知枚举。 本子例输入：显式全选模式产生空 selector 对象。 | {} 与省略保持不同语义 | #21 |
| F01.empty-list | Thrift往返保留显式配置语义 | 用实际锁定生成参数往返编码外层缺失、空对象、空集合、显式模式及未知枚举。 本子例输入：合法显式清空集合与模式配套。 | 清空语义保持，非法组合拒绝 | #21 |
| F01.unknown-mode | Thrift往返保留显式配置语义 | 用实际锁定生成参数往返编码外层缺失、空对象、空集合、显式模式及未知枚举。 本子例输入：未知枚举或缺失必需显式 mode。 | 拒绝，不默认退化为全选或不选 | #21 |
| F02.primary | 真实RPC链路不能被HTTP旁路 | 启动真实Kitex client/server与SQLite测试库；记录同requestId的调用；令RPC不可用。 本子例输入：启动真实Kitex client/server与SQLite测试库；记录同requestId的调用；令RPC不可用。。 | 正常HTTP业务抵达server并执行业务授权；RPC不可用时无直接DB/K8s成功旁路；伪造角色/身份无效。 | #21 |
| F03.quantity | 后端字段校验及稳定错误 | 直接发送非法quantity/正则/selector/URL/字段组合和未知字段，绕过页面禁用。 本子例输入：分别经 HTTP/直接 RPC 提交 非法 quantity、负数或 request>limit。 | 稳定字段级错误与 HTTP 类别，CR 无写入，敏感值不泄漏 | #21 |
| F03.regexp | 后端字段校验及稳定错误 | 直接发送非法quantity/正则/selector/URL/字段组合和未知字段，绕过页面禁用。 本子例输入：分别经 HTTP/直接 RPC 提交 Go regexp 不接受的正则/含逗号指标项。 | 稳定字段级错误与 HTTP 类别，CR 无写入，敏感值不泄漏 | #21 |
| F03.selector | 后端字段校验及稳定错误 | 直接发送非法quantity/正则/selector/URL/字段组合和未知字段，绕过页面禁用。 本子例输入：分别经 HTTP/直接 RPC 提交 非法 LabelSelector 或不相容模式。 | 稳定字段级错误与 HTTP 类别，CR 无写入，敏感值不泄漏 | #21 |
| F03.url | 后端字段校验及稳定错误 | 直接发送非法quantity/正则/selector/URL/字段组合和未知字段，绕过页面禁用。 本子例输入：分别经 HTTP/直接 RPC 提交 非法 URL、零 remoteWrite endpoint 或重复 name。 | 稳定字段级错误与 HTTP 类别，CR 无写入，敏感值不泄漏 | #21 |
| F03.tls-pair | 后端字段校验及稳定错误 | 直接发送非法quantity/正则/selector/URL/字段组合和未知字段，绕过页面禁用。 本子例输入：分别经 HTTP/直接 RPC 提交 未配对客户端证书/私钥或非法认证组合。 | 稳定字段级错误与 HTTP 类别，CR 无写入，敏感值不泄漏 | #21 |
| F03.unknown-field | 后端字段校验及稳定错误 | 直接发送非法quantity/正则/selector/URL/字段组合和未知字段，绕过页面禁用。 本子例输入：分别经 HTTP/直接 RPC 提交 不支持字段、资源键或高级透传。 | 稳定字段级错误与 HTTP 类别，CR 无写入，敏感值不泄漏 | #21 |
| F04.success | 迁移失败保留原数据库 | 在带历史与密文的库上制作一致性快照，分别执行成功迁移与受控失败。 本子例输入：对带历史与密文的库执行成功迁移。 | 关系、历史和可解密性保留 | #21 |
| F04.failure | 迁移失败保留原数据库 | 在带历史与密文的库上制作一致性快照，分别执行成功迁移与受控失败。 本子例输入：一致性快照后受控迁移失败。 | 保留原库与恢复快照，不替换为空库 | #21 |
| F05.before-commit | 幂等与任务受理事务原子 | 在版本、操作、尝试、去重/保护事务的提交边界注入崩溃，并重复相同请求键。 本子例输入：事务提交前崩溃。 | 要么完整未提交，不留下半个受理状态 | #21 |
| F05.after-commit | 幂等与任务受理事务原子 | 在版本、操作、尝试、去重/保护事务的提交边界注入崩溃，并重复相同请求键。 本子例输入：提交后、返回前崩溃。 | 同键重启查询原完整结果，不产生第二操作；仍执行当前授权 | #21 |
