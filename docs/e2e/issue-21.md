# #21 完整 E2E 实施计划

依赖#19/#20已关闭，#21已领取。实施前先将本文件全文发布为对应Issue评论并精确回读，再开始实现；本计划不含通过记录，实际执行结果单独保存。固定207行与继承REG的五列逐字保留；原业务票所属Issue列保留来源责任，最终统一复跑/门禁owner均为#21。

来源：固定207五列与#17/#18原REG取Git对象`7e1dc001306ab98180579f7762597ab54cd03464`；#19 REG取已发布评审修复计划`cb79fc517c0fdc2957cbb627eeabe0bbf7227c0b`（修复来源已冻结并交付）；#20最终REG取`21eb65c81c042990fe2c42928bad82e1d4223fe2`；REG21取外部`issue-21-supplemental-reg-plan.md`（23行）和`issue-21-ci-output-boundary-preflight.md`（6行）。后者六项均Q。精确源hash/字符数/键清单另存`docs/e2e/issue-21-plan-sources.json`。所有结果均planned_not_executed，不继承旧绿色状态。

固定profile边界：LOCAL_FULL207、CI_FAST58、CI_SMOKE17，共282个profile/variant键。补充REG只在独立LOCAL_FULL回归集合复跑，100个unique ID、104个ID/layer键，不计入或替代282；固定层与profile不改。若同时统计两套执行责任是282+104=386个键，不能将其称为386个固定验收子例。

| REG来源owner | unique ID | Q键 | K键 | B键 | layer键 |
| --- | --- | --- | --- | --- | --- |
| #17 | 7 | 7 | 0 | 0 | 7 |
| #18 | 29 | 28 | 2 | 2 | 32 |
| #19 | 14 | 12 | 0 | 3 | 15 |
| #20 | 21 | 14 | 7 | 0 | 21 |
| #21 | 29 | 21 | 8 | 0 | 29 |

layer规则：#17七项全Q；#18除PATTERN-011仅B外均有Q，SOURCE-007另B、PROJECTION-013.url-parse-error/proxy-parse-error另K；#19原三项Q与新增九项Q，HTTP-I64-001另B，加两个独立Agent操作B；#20 STORAGE-001七项K，其余十四项Q；#21 SIGNAL-002/003/004、RESULT-005/006、RECOVER-008、LEDGER-009/010八项K，其余21项Q。4个双layer ID须各层独立成功，不能以Q替代B/K。

所有用例独立前置、原UID ledger、同环境lane、正常finally及独立recover；产品默认10s停机与测试资源正常GC预算分开。CI取消/超时不保证finally完整执行；强杀保持原attempt中断与cleanup未知，recover成功不能改旧run为绿。validator/export协议fixture显式标validator-test，不冒充产品或真实K。敏感原始输出在公开console前受保护捕获，原件与安全派生各自hash；artifact白名单不含真实私钥/Session/kubeconfig/SQLite/私有源/镜像层。

## 固定207五列（原文行）

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

## 补充回归：来源#17（最终统一执行#21）

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| REG17-DELETE-001 | KSM跨namespace残留不能漏报 | 将原CR UID控制的workload/ReplicaSet/Pod放到另一namespace；在删除前持久读取其owner UID链，再令父对象先消失并保留Pod。读取全namespace分页候选；最后移除测试残留再核验。 | 原CR消失不能报告完成；跨namespace原UID链残留保持保护，只有完整范围全部核验消失才完成；读取不完整不能当空集。 | #17 |
| REG17-ACTIVITY-001 | 后台删除完成不改变显式操作排序 | 受理带finalizer的删除，记录实例activityAt；再由测试fixture移除阻塞并等待后台完成。 | 后台完成不更新activityAt，排序只反映显式受理/编辑/重试/取消。 | #17 |
| REG17-SHUTDOWN-001 | 停机起点立即关闭直接RPC受理 | 在受控launcher边界阻塞派发循环，开始Close并同时通过真实生成Kitex client提交新变更；放行launcher并等待落盘。 | Close起点后新direct RPC返回UNAVAILABLE，不依赖HTTP closed；无新operation/幂等副作用，已有任务按持久发送边界恢复。 | #17 |
| REG17-DELETE-002 | 发送前原CR消失后DELETE404不能解锁残留 | 原UID存在时完成预检并保存owner UID链，在持久派发屏障后由fixture外部删除原CR并保留Pod；平台DELETE获得404，再读取清理范围。 | 404仅表明原CR不存在，不代表workload/Pod消失；保持只读核验和保护，不普通重试、不重发删除；清理图全部消失后才完成。 | #17 |
| REG17-VERSION-001 | 升级后重试仍执行原冻结有效映射 | 建立精确旧schema fixture，使同一typed来源配置的历史effective_spec与当前mapper默认映射不同；通过公开HTTP读取冻结版本并继续/重试原操作。 | 版本YAML、条件写入spec与响应核验均采用存储的effective_spec，依赖核验仍读取原typed配置；不因新mapper默认值改变原版本。 | #17 |
| REG17-IDEMPOTENCY-001 | 生命周期幂等命中返回操作当前状态 | 分别在retry与continue完成后用原key/body重发，并重发已完成的cancel命令；记录受理快照、GET当前operation及重复命中结果。 | 先检查当前权限；返回同一operation的当前state/revision/currentAttempt而非旧QUEUED受理快照，不新增attempt、不重复写入。 | #17 |
| REG17-DELETE-003 | 删除预览锁定目标配置与影响语义 | 对原UID建立类型化删除预览；分别仅改变status/RV、在受理前将targetNamespace A改B、受理后派发前改变目标spec或实际owner链影响namespace；再确认/放行删除；裁出精确v6明确未发删除库，升级后在同一operation保护下重新预览并继续。 | status/RV自然变化不使预览过期；规范化目标配置或删除影响语义变化必须新预览，旧确认受理/派发均不发DELETE。预览绑定原UID、当前部署版本及完整影响范围，不凭最新RV接受过期删除意图。旧v6任务保留原version/UID与旧attempt，缺preview拒绝且不自动重放；新预览允许同operation重新确认，不造成保护死锁。 | #17 |

## 补充回归：来源#18（最终统一执行#21）

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| REG18-SHARED-001 | 旧KSM预览升级保留安全判定 | 固定v4带账号/凭据/草稿/版本/预览/保护库；启动新版本升级，使用原会话和原previewId确认；另分别修改spec及仅RV/status后确认。 | ID/关系及原预览保留；相同spec或仅RV/status变化可继续，spec变更拒绝；target_spec以带域隔离的HMAC保存，不存原目标敏感值。 | #18 |
| REG18-SHARED-002 | 身份迁移区分CR scope | 从带历史v4库升级，创建KSM跨部署namespace同名及Agent跨CR namespace同名；对同namespace同名重试；注入迁移失败后检查原库和快照。 | KSM同集群同名冲突；Agent跨namespace成功、同namespace冲突；历史FK不变；失败回滚并保留原库/恢复依据。 | #18 |
| REG18-SHARED-003 | 目标敏感字段不反向进入平台 | 在外部和已纳管漂移CR加入inline认证、URL userinfo、敏感last-applied，读取发现/预览/运行及历史，检查DB/HTTP/直接RPC/YAML/日志；敏感值变化后再确认，最后明确覆盖。 | 产品持久化和输出无秘密；敏感值变化使旧预览失效，仅RV/status变化不失效；更新保留原metadata/status/finalizers，不以脱敏改写真实资源。 | #18 |
| REG18-CAP-004 | 后补Agent CRD/RBAC不能冒充controller已启动 | Operator启动时缺Agent支持；随后恢复CRD/权限而不重启，配置SC None的Agent并预览/发布；再重启原固定Operator。 | 仅当前CRD/SAR成功不能放行；原Pod缺Agent ready=1时阻止，重启后按原Pod身份与真实Agent ready证据重新核验。 | #18 |
| REG18-CAP-005 | 后补ScrapeConfig支持不能冒充informer已启动 | Operator启动时缺ScrapeConfig API/权限；随后恢复但不重启，配置非None ScrapeConfig并预览；再重启固定Operator。 | Agent ready本身不足；同一原Pod缺SC资源endpoint请求证据时阻止，重启后Agent ready及SC informer证据同时满足才放行。 | #18 |
| REG18-CAP-004.single-dash-namespace | Go flag单横线namespace范围生效 | 固定Operator实际Pod使用 `-namespaces=other`，Agent位于不同namespace且当前API/SAR可用，发起预览。 | 阻止发布，不将单横线当作全namespace；不创建Agent。 | #18 |
| REG18-CAP-004.single-dash-selector | Go flag单横线实例selector生效 | 实际Pod使用 `-prometheus-instance-selector=team=other`，平台CR不匹配，发起预览。 | 明确范围受阻，不修改CR labels绕过。 | #18 |
| REG18-CAP-004.override | Operator访问身份/目标override不能按Pod SA推断 | 实际Pod带 `--as` 或访问API/TLS override，模板及metrics可读，发起预览。 | 无法确认真实Operator身份/目标时阻止，不把Pod SA的SAR当其真实权限。 | #18 |
| REG18-CAP-004.old-rs | 旧RS的绿色Pod不能代表新template | 同Deployment UID旧RS Pod的实际SA/args与新template不符，而旧Podmetrics绿色，发起预览。 | 阻止，原Pod/template绑定不能被绿色指标绕过。 | #18 |
| REG18-CAP-004.none-short-circuit | None规则不依赖未关联的采集API | 四类None，撤掉ScrapeConfig API/读取权限，保留已运行Agent controller与Agent CR能力后预览。 | 不因SC依赖被拒绝；None不读取全namespace/SC API，不虚称SC informer已验证。 | #18 |
| REG18-VALID-006 | Prometheus Duration校验含范围上限 | 经HTTP和直接RPC分别提交合法 `1y2w3d4h5m6s7ms`、重复/逆序单位及超长年份 `999999999999999999999999y`。 | 合法组合保持；重复/逆序/溢出稳定字段拒绝且CR无写，不退化为Go duration。 | #18 |
| REG18-SOURCE-007 | 草稿关联预览不得覆盖已部署运行来源 | 发布规则A并刷新实际CR关联；保存未发布规则B并预览，再读取运行来源；预览过程中另保存新修订。浏览器分别查看配置区和运行区并继续编辑。 | B仅返回所请求draftRevision的预览，运行来源及时间仍来自A；修订并发变化返回冲突且不落库；UI草稿预览独立保存并标明过期，轮询不覆盖预览，运行页不展示未发布B。 | #18 |
| REG18-FIXED-008 | 首次发布在途保存的草稿不能绕过存储固定字段 | 以1Gi预览并受理首次发布，在实际派发/确认前另保存2Gi草稿；首次写入成功或可能已发后用新草稿预览/确认，并从旧预览/固定版本恢复派发。 | 预览、受理和派发均重新对照已确认或可能已发的冻结版本；原PVC容量不可被改写，目标实际PVC仍1Gi；本例复现容量链，统一fixed-field接缝继续供后续存储模式规则复用，单点SaveDraft校验不构成唯一防线。 | #18 |
| REG18-HEADERS-009 | remoteWrite任意header值不进入平台读模型 | 外部及已纳管目标加入Authorization、X-API-Key及任意自定义header值，读取发现/运行/左右YAML/历史并检查DB/日志；仅修改header值后确认旧预览，最后明确覆盖。 | headers上下文全部值脱敏，HMAC按原配置识别变化；秘密不持久化或输出；Secret refs保持可读，更新不以脱敏覆盖目标metadata/status/finalizers。 | #18 |
| REG18-CAP-010.positional | Go flag首个非参数终止不能误扩大范围 | 实际Pod与模板均为 `--namespaces=other positional --namespaces=`，Agent在monitoring；原Podmetrics与当前SAR均可读，预览/发布。 | 明确unsupported；不继续解析positional后的伪范围，不写Agent，不将解析失败当作全范围。 | #18 |
| REG18-CAP-010.terminator | 双横线终止不能误扩大范围 | 实际Pod与模板均为 `--namespaces=other -- --namespaces=`，Agent在monitoring，发起预览。 | 明确unsupported；不把终止后的参数作为实际范围，不写Agent。 | #18 |
| REG18-CAP-010.duplicate-scope | 重复集合flag不得按last-wins丢失deny范围 | 实际Pod与模板均为 `--deny-namespaces=monitoring --deny-namespaces=other`，Agent在monitoring，发起预览。 | 明确unsupported；固定源码StringSet累加语义不能被普通map覆盖，不写Agent。 | #18 |
| REG18-CAP-010.boolean-arity | Go布尔flag的空格值不能被当作值消费 | 实际Pod与模板均为 `--deny-namespaces=monitoring --kubelet-endpoints false --deny-namespaces=other`，Agent在monitoring；发起预览。 | 明确unsupported；false是Go flag停止点，不能继续采用后面的伪deny范围；不写Agent。 | #18 |
| REG18-CAP-010.known-scope-space | 已知scope字符串的空格形式仍可核验 | 实际Pod和模板均用 `-namespaces monitoring -prometheus-instance-selector ''`，Agent在monitoring；固定controller与依赖可用后预览。 | 正常预览，保留已知字符串参数的单/双横线及空格形式，不把安全拒绝退化为拒绝所有有效参数。 | #18 |
| REG18-CAP-010.env-expansion | Pod声明中的变量引用不能冒充进程实参 | 实际Pod和模板args为 `--deny-namespaces=$(BLOCKED_NS)`，容器env中BLOCKED_NS=monitoring；原Podcontroller/metrics正常，在monitoring预览。 | 明确unsupported；平台不猜测Kubelet展开后的scope，也不读取Secret env值来扩大能力，不写Agent。 | #18 |
| REG18-PATTERN-011 | 初始化、登录与新账号的账号规则在Chromium实际生效 | 在U02真实Chromium中读取初始化/登录和新账号input.pattern，以JavaScript `v` flag实际编译；逐项填写含点、横线、下划线的合法账号及中文、空格、斜线等非法账号，检查patternMismatch并记录console。 | 两处共用明确安全写法；合法账号无patternMismatch，非法字符被拒绝；console无非法正则语法错误，独立输出此B补充证据，不把它计为额外Go回归或owned子例。 | #18 |
| REG18-SELECTOR-012.exists-nil | Exists缺失values不产生API裁剪后的身份误判 | 经HTTP/RPC保存并预览含Exists且values缺失的Labels规则，确认发布；受控API按固定CRD省略null，回读实际CR及操作终态。并检查外层None/All映射。 | CR表达式省略values，发布成功且无IDENTITY_UNCERTAIN；typed缺失语义保留；None仍省略、All仍{}，无额外写入或不安全重试。 | #18 |
| REG18-SELECTOR-012.exists-empty | Exists显式空values保持已约定的空集合语义 | 经HTTP/RPC保存values=[]的Exists规则，检查生成类型、DB草稿、预览YAML及发布后的实际CR。 | typed/RPC/DB中的显式空集合不变，CR保持values: []且发布成功；不把合法空数组改成null或与外层selector省略混同。 | #18 |
| REG18-SELECTOR-012.not-exists-nil | DoesNotExist缺失values不产生身份误判 | 经HTTP/RPC保存并预览含DoesNotExist且values缺失的Labels规则，确认发布；API按固定CRD省略null，再回读操作。 | CR表达式省略values，实际CR与冻结版本一致，发布成功且无IDENTITY_UNCERTAIN；不放宽未知字段或依赖校验。 | #18 |
| REG18-SELECTOR-012.not-exists-empty | DoesNotExist显式空values保持空集合 | 经HTTP/RPC保存values=[]的DoesNotExist规则，检查RPC/DB/预览并完成发布回读。 | 显式[]在typed和CR中保持，发布成功；没有null字段和重复创建，不改变外层None/All。 | #18 |
| REG18-SELECTOR-012.in | In的非空values不可被省略规则吞掉 | 经HTTP/RPC保存In及两个合法values，预览、发布、回读实际CR与冻结版本。 | 非空values内容及顺序保持，正确匹配并发布成功；空值规范化不能改变集合选择含义。 | #18 |
| REG18-SELECTOR-012.not-in | NotIn的非空values不可被省略规则吞掉 | 经HTTP/RPC保存NotIn及两个合法values，预览、发布、回读实际CR与冻结版本。 | 非空values完整保留，正确排除并发布成功；未越过当前集群授权或原UID校验。 | #18 |
| REG18-PROJECTION-013.url-parse-error | 外部Agent的无法解析URL不能回显认证串 | 创建含合法schema字符串、userinfo及非法escape的remoteWrite.url；Q将该URL复制进Operator解析错误message及适用Available=False.reason，并添加普通故障reason/message正控；K注入同一适用reason并回读原值，检查当次两类Runtime.Error明确等于安全隐藏标记；经平台发现、详情、运行刷新、左右YAML/历史及直接RPC读取，检查平台DB/安全日志。保存预览后仅替换临时认证marker（保留旧错误文本），再确认原preview；回读目标原UID、metadata/status；Q再注入schema7旧CR/YAML和两类runtime.Error cache，离线重启后检查首次HTTP/RPC读、迁移版本与历史基线，finally清理本例。 | HTTP/RPC/YAML/DB/日志/公开证据不含认证marker，无法解析URL整值隐藏，含认证串的状态文本和Runtime.Error安全投影；正常非敏感reason/message/用户metadata保留；隐藏值变化仍触发HMAC冲突，不误放旧预览；无读侧目标mutation，原metadata/status不因脱敏被改写；schema7旧cache经真实数据迁移后离线首次读无泄漏，effective_spec及HMAC基线保持。 | #18 |
| REG18-PROJECTION-013.proxy-parse-error | 外部Agent的无法解析proxyUrl同样失败关闭 | 独立fixture使用固定CRD接受的HTTP前缀proxyUrl，含userinfo及非法escape；Q同样复制认证URL到Operator错误message/Available=False.reason；K另由fixture设置合法schema接受的适用reason，确认实际CR原值存在后经managed refresh和external发现读取，防止只保护YAML却漏Runtime.Error；K等待真实Operator status确含当前内存marker后再读公开投影，未观察分支则失败；执行同一公开读路径、DB/日志检查，检查正常非敏感状态正控；隐藏值漂移保留旧错误文本，回读原metadata/status；Q执行同一schema7旧cache离线重启检查，再finally清理。 | proxyUrl解析失败也整值隐藏，含真实Operator错误status及Runtime.Error在内的所有平台读投影和持久缓存无认证marker；branchObserved必须为true，正常非敏感reason/message/用户metadata保留；HMAC仍识别原值变化，旧预览拒绝，不能用解析失败绕过脱敏或修改真实对象；旧schema7读cache离线首次HTTP/RPC响应同样安全，迁移不改历史effective_spec/HMAC。 | #18 |

## 补充回归：来源#19（最终统一执行#21）

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| REG19-ACTIVITY-001 | 显式活动顺序跨同刻、时钟回拨与重启持久正确 | 经真实 HTTP/Kitex 建两实例，在同一时刻及回拨时间交替编辑/保存；反复查看、预览与刷新；保留原目录/密钥/Session 重启后再编辑；旧 schema8 库升级保留草稿及既有活动顺序。 | 最近明确编辑/保存实例靠前；查看/预览/刷新及后台完成不移动；同刻、回拨和重启不逆序；旧库升级保留业务数据。 | #19 |
| REG19-HTTP-I64-001 | HTTP 修订超过安全整数边界仍精确往返 | 经真实 HTTP/Kitex 建立草稿；用受限旧库 fixture 设置修订为 9007199254740992，再经 HTTP 读取/保存并核对直接 RPC i64；覆盖边界小值、十进制字符串、越界/小数/不安全 JSON 数字；浏览器保留修订值并提交。 | 安全范围数值兼容；超界响应为精确十进制字符串；请求字符串按 i64 精确解析，不接受已可能舍入的超界数字；保存冲突不被舍入掩盖；RPC wire 不变，浏览器不以 Number 处理不安全修订。 | #19 |
| REG19-SOURCE-001 | 迟到观察不能跨当前管理范围覆盖来源 | 先成功观测，再在目标 GET 在途时注销集群或切换凭据修订；释放旧读取，查询来源及历史；新有效凭据读取后核对身份。 | 旧 UID/旧凭据修订或已注销范围的迟到结果不能推进来源时间、覆盖新成功或恢复当前可用；读取不重新产生写请求。 | #19 |

| REG19-STORAGE-SCOPE-001 | Q：旧凭据身份预读失败不能推进独立存储任务 | 经真实HTTP建立已发布Agent及Pending卷核验；阻塞原凭据kube-system身份读取，确认正常注销/转移被Pending guard拒绝；另行受理并完成凭据轮换，再释放旧身份读取返回403/网络失败，读取同operation卷结果；最后用当前凭据核验。 | 旧失败不改变卷task lastAttempt、原卷结果/错误或完成状态；当前有效凭据后续只读核验可推进；不重放写、不认领新UID、不放宽Pending注销/转移guard。 | #19 |
| REG19-STORAGE-SCOPE-002 | Q：旧凭据成功结果也受同一提交范围限制 | 同样建立Pending卷核验；令旧目标身份成功后的原PVC读取在途，完成独立凭据轮换后释放旧PVC成功响应，再读同operation卷结果并用当前凭据核验。 | 旧成功不推进task/volume时间或确认Retained/Deleted；当前cluster UID、ACTIVE、credentialRevision与原实例UID比较适用于成功和失败；当前凭据后续核验可确认原卷。 | #19 |
| REG19-HTTP-DELETE-001 | Q：规范DELETE入口复用安全删除受理 | 经HTTP建立未部署草稿并DELETE；另发布实例，创建删除预览，以DELETE /instances/{id}携versionId/previewId与幂等键受理；覆盖缺/过期预览、异主、原UID替换及同键回读。 | 草稿同步200；有效已部署删除202并返回原operation；HMAC影响基线、当前授权、原UID/RV与同实例保护保持，不删除同名新UID，不生成第二次执行；兼容POST/deletions。 | #19 |
| REG19-HTTP-CONTINUE-PUBLISH-001 | Q：规范continue继续明确未发发布 | 建立受理后未派发发布，保留原目录/密钥重启成为WAITING；经POST /operations/{id}/continue提交当前operation/attempt修订，覆盖错误修订、异主和可能已发送输入。 | 明确未发且修订正确返回202并继续原operation/不可变版本，新增attempt并保留原attempt；错误修订/当前无权/可能已发拒绝，不能借alias重放；兼容continue-unsent。 | #19 |
| REG19-HTTP-CONTINUE-DELETE-001 | Q：规范continue继续明确未发删除 | 建立已部署实例并受理删除但阻止派发，重启后以新有效删除预览及原operation/attempt修订请求规范continue；覆盖旧影响预览、错误修订和可能已发送。 | 新预览/原UID/修订正确才202；陈旧影响/HMAC、错误修订、可能已发均拒绝；不另建状态机或解除结果待确认保护。 | #19 |
| REG19-HTTP-CONTINUE-CREDENTIAL-001 | Q：规范continue保留凭据只读恢复 | 受理注册或凭据候选后重启，当前管理员经同一规范continue入口继续；非管理员/禁用账号重送。 | 管理员继续原候选只读验证202，不产生配置写；当前无权先拒绝；与发布/删除按实际operation.action分派。 | #19 |
| REG19-HTTP-CAPABILITIES-001 | Q：规范能力查询使用安全当前授权投影 | 注册带已知组件能力的受控TLS集群；所属用户GET /clusters/{id}/capabilities，再以异主、禁用/未登录与不存在ID读取，并与GetCluster.capabilities比较。 | 合法GET 200返回同一typed capabilities安全投影；异主/禁用/未认证/不存在遵守原错误与授权边界；HTTP不直连DB/Kube。 | #19 |
| REG19-CREDENTIAL-REPLAY-001 | Q：凭据受理幂等命中回读当前operation | 参数化REGISTER/CREDENTIAL/REACTIVATE同键同内容，等待SUCCEEDED/FAILED，另保留原目录重启形成WAITING后重送；切换当前管理员权限再重送，并核对实际目标访问/操作数。 | 返回原operationId的当前状态而非缓存QUEUED；无第二操作/候选/目标写；当前授权先于hit，规范内容变化仍冲突。 | #19 |
| REG19-CONTINUE-REPLAY-001 | Q：凭据继续幂等命中回读当前operation | 对原WAITING候选以固定continue键受理，分别完成成功/失败及再次重启；原键同内容重送，含当前撤权输入。 | 命中返回当前SUCCEEDED/FAILED/WAITING及同operation，不再次入队；当前授权和action白名单仍先于hit。 | #19 |
| REG19-AGENT-DELETE-OP-001 | B：Agent删除受理后保留原operation运行入口 | 真实平台Pod/Operator建立本run Agent，浏览器列表删除确认后检查自动打开的运行页；读取返回operation并观察任务轮询、卷核验及状态相应的LifecycleActions。 | Agent接收selection.operation，与列表受理原ID一致；运行页持续显示原删除任务/独立卷结果及合法核验/恢复入口，不因组件切换丢失任务。 | #19 |
| REG19-AGENT-HISTORY-OP-001 | B：Agent重开历史按原失败版本恢复 | 真实Pod浏览器发布Agent原版本，保存并发布一版配置后精确拒绝写使其FAILED；另存不同新草稿，关闭抽屉再打开下发历史，在原任务行使用合法重试，恢复目标后回读版本与attempt。 | 历史行复用LifecycleActions，重试原operation/不可变发布版本而非当前新草稿；新attempt可确认原版本，当前草稿仍保留；受保护/待确认状态只展示允许的继续、取消或只读核验入口。 | #19 |

## 补充回归：来源#20（最终统一执行#21）

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| REG20-STORAGE-001.orphan | 新实例不能挂载同确定WAL名称的孤立Retain卷 | 独占真实fixture，按候选新名和shard计算WAL claim名；预建无ownerRef的PVC并记录原UID/容量。经HTTP和真实生成RPC分别预览/发布候选；回读目标mutation与PVC UID/ownerRefs。finally由测试工具清理。 | 碰撞明确受阻，无候选CR/workload或挂载、无PVC修改/删除/认领；不能以同容量或无ownerRef放行。 | #20 |
| REG20-STORAGE-001.forbidden | 不能把PVC读取Forbidden当不存在 | 独立真实fixture保留可读CR/ST，撤销仅PVC读取权限；经平台预览/发布候选并保存403证据；恢复本例权限，finally清理。 | 错误明确受阻，无候选创建副作用或PVC认领，恢复后必须重新预览。 | #20 |
| REG20-STORAGE-001.timeout | 不能把PVC读取超时当不存在 | 独立受控代理连接真实目标，先证明基线可读，仅阻断候选PVC读取直到原预算超时；经HTTP/Kitex尝试预览/发布，恢复代理并finally清理。 | 按原预算受阻，无候选创建或PVC改动；超时不转404/空集合，不内部重试写。 | #20 |
| REG20-STORAGE-001.dispatch-race | 干净预览不能绕过派发前新增碰撞 | 独立真实fixture先干净预览，利用既有应用派发屏障在最后依赖重验前由测试工具插入候选确定名称PVC；继续原operation，核对真实读取和mutation证据，finally清理。 | 最后读取发现碰撞即明确未发失败/受阻，原卷不变；不声称检查之后的K8s跨对象原子性。 | #20 |
| REG20-STORAGE-001.new-name | 新的干净名称获得新的CR和卷 | 独立fixture保存孤立旧Retain PVC UID；复制配置填写另一个无碰撞新名，明确预览发布，回读原CR→ST→Pod→PVC UID链和实际挂载，finally清理。 | 新CR与新PVC UID不同于旧卷，原孤卷UID及内容不变；平台未清理旧卷。 | #20 |
| REG20-STORAGE-001.owned-retain-suspend | 旧实例挂起后恢复自己已确认的Retain卷 | 独立真实运行实例并持久记录原CR/ST/Pod/PVC UID；确认实际whenScaled=Retain后挂起、等待原Pod消失，再明确恢复并回读原PVC UID及挂载，finally清理。 | 可信原实例/原PVC UID的挂起恢复允许；不被新实例碰撞规则误拒绝，不自动迁移/删除旧数据。 | #20 |
| REG20-STORAGE-001.owned-retain-shard | 旧实例恢复被移除shard的原Retain卷 | 独立真实多shard实例记录原CR/ST/Pod/PVC UID；确认实际whenDeleted=Retain后减少shard、等待移除ST/Pod消失，再明确恢复该shard并核对原PVC UID与挂载，finally清理。 | 可信原实例/原PVC UID的shard恢复允许，确实复用其历史身份；不认领同名不同UID卷，不迁移/删除旧数据。 | #20 |
| REG20-STORAGE-002.transfer | 未决存储核验期间不丢集群归属责任 | 经真实HTTP→生成Kitex→SQLite和受控TLS API完成原UID Agent删除，使实例/operation已完成，而whenDeleted=Delete原PVC读取Unknown/待核；通过transfer尝试转移。恢复当前凭据/读取并核实原PVC结果，再重新转移。独立Retain正控保留合法原PVC且实际核实Retain后转移。 | pending/Unknown独立卷任务时transfer409，实例与operation仍完成且不重建实例protection；只读核实完成后允许转移，凭据更新不受此责任阻塞。已确认Retain合法残卷是终态，不阻塞转移。 | #20 |
| REG20-STORAGE-002.unregister | 未决存储核验期间不提前清除原凭据 | 独立应用fixture完成原UID Agent删除，使CR/workload/Pod消失、实例/operation完成但Delete/PVC结果未知；调用unregister，再恢复读取并核实原PVC消失后重试。另以实际核实Retain原PVC仍存在作为独立正控。全程公开HTTP/真实Kitex，不直接改业务表制造结果。 | pending/Unknown任务时unregister409、保留可用凭据供只读核验；实例完成不被回滚/延迟。任务核实终态后注销允许；已确认Retain残卷不阻塞注销，平台不删除该卷。 | #20 |
| REG20-STORAGE-003.st-uid-accept | 提交确认前独立更改实际ST UID必须使旧危险预览失效 | Q：公开HTTP→真实Kitex→SQLite/TLS fixture运行原UID PVC Agent，先保存挂起/减shard草稿并预览；提交确认前仅注入实际ST UID变化，再继续原请求。使用原Agent UID控制的新ST，PVC保持同原UID且在新ST原Pod链中，实际策略已采用；证明安全guard可通过，仅确认影响身份变化。保存请求/目标mutation及操作结果。 | 旧预览拒绝受理/派发，目标无新mutation，必须重新预览；分别证明该输入而非一次注入多种变化。ST-UID变体应记录实时安全重检可通过而旧HMAC拒绝的证据。 | #20 |
| REG20-STORAGE-003.st-uid-dispatch | 操作受理后、最后存储重验前独立更改实际ST UID必须使旧危险预览失效 | Q：公开HTTP→真实Kitex→SQLite/TLS fixture运行原UID PVC Agent，先保存挂起/减shard草稿并预览；操作受理后、最后存储重验前仅注入实际ST UID变化，再继续原请求。使用原Agent UID控制的新ST，PVC保持同原UID且在新ST原Pod链中，实际策略已采用；证明安全guard可通过，仅确认影响身份变化。保存请求/目标mutation及操作结果。 | 旧预览拒绝受理/派发，目标无新mutation，必须重新预览；分别证明该输入而非一次注入多种变化。ST-UID变体应记录实时安全重检可通过而旧HMAC拒绝的证据。 | #20 |
| REG20-STORAGE-003.pvc-uid-accept | 提交确认前独立更改原PVC UID必须使旧危险预览失效 | Q：公开HTTP→真实Kitex→SQLite/TLS fixture运行原UID PVC Agent，先保存挂起/减shard草稿并预览；提交确认前仅注入原PVC UID变化，再继续原请求。以同名新PVC UID替换；不认领新UID，不把旧卷消失当新卷授权。保存请求/目标mutation及操作结果。 | 旧预览拒绝受理/派发，目标无新mutation，必须重新预览；分别证明该输入而非一次注入多种变化。ST-UID变体应记录实时安全重检可通过而旧HMAC拒绝的证据。 | #20 |
| REG20-STORAGE-003.pvc-uid-dispatch | 操作受理后、最后存储重验前独立更改原PVC UID必须使旧危险预览失效 | Q：公开HTTP→真实Kitex→SQLite/TLS fixture运行原UID PVC Agent，先保存挂起/减shard草稿并预览；操作受理后、最后存储重验前仅注入原PVC UID变化，再继续原请求。以同名新PVC UID替换；不认领新UID，不把旧卷消失当新卷授权。保存请求/目标mutation及操作结果。 | 旧预览拒绝受理/派发，目标无新mutation，必须重新预览；分别证明该输入而非一次注入多种变化。ST-UID变体应记录实时安全重检可通过而旧HMAC拒绝的证据。 | #20 |
| REG20-STORAGE-003.policy-accept | 提交确认前独立更改实际所用存储策略必须使旧危险预览失效 | Q：公开HTTP→真实Kitex→SQLite/TLS fixture运行原UID PVC Agent，先保存挂起/减shard草稿并预览；提交确认前仅注入实际所用存储策略变化，再继续原请求。仅改变本次所用whenScaled/whenDeleted实际ST值，保留CR UID及其余影响事实。保存请求/目标mutation及操作结果。 | 旧预览拒绝受理/派发，目标无新mutation，必须重新预览；分别证明该输入而非一次注入多种变化。ST-UID变体应记录实时安全重检可通过而旧HMAC拒绝的证据。 | #20 |
| REG20-STORAGE-003.policy-dispatch | 操作受理后、最后存储重验前独立更改实际所用存储策略必须使旧危险预览失效 | Q：公开HTTP→真实Kitex→SQLite/TLS fixture运行原UID PVC Agent，先保存挂起/减shard草稿并预览；操作受理后、最后存储重验前仅注入实际所用存储策略变化，再继续原请求。仅改变本次所用whenScaled/whenDeleted实际ST值，保留CR UID及其余影响事实。保存请求/目标mutation及操作结果。 | 旧预览拒绝受理/派发，目标无新mutation，必须重新预览；分别证明该输入而非一次注入多种变化。ST-UID变体应记录实时安全重检可通过而旧HMAC拒绝的证据。 | #20 |
| REG20-STORAGE-003.status-rv | status/RV自然推进不改变确认影响 | Q：运行原Agent/原ST/原PVC UID与已采用策略，保存危险预览，仅推进CR/ST status和resourceVersion，再确认发布并回读。 | 原安全guard仍通过、旧预览可以使用；不把status/RV放入稳定存储影响摘要。 | #20 |
| REG20-STORAGE-003.same-st-pod-roll | 同ST下Pod自然滚动不改变确认影响 | Q：运行原Agent/原ST/原PVC UID与已采用策略，保存危险预览，仅让同ST UID下原Pod被新Pod UID替换且继续引用原PVC，再确认发布并回读。 | UID父链仍可信，原预览可以使用；保留新旧Pod历史，但不把Pod UID作为确认影响冲突。 | #20 |
| REG20-STORAGE-004.scaled-retain-deleted-delete | 挂起不能把合法保留ST的ownerRef误判为危险 | Q：真实HTTP→生成Kitex→SQLite/TLS运行原Agent，whenScaled=Retain、whenDeleted=Delete且实际ST已采用；原WAL PVC有原ST ownerRef、无Pod ownerRef。保存并明确下发挂起，回读CR replicas=0/paused=false、ST仍为原UID、原Pod消失及独立原PVC结果。 | 挂起允许，原PVC当前同UID保留并核实Retained，但不承诺未来删除ST仍保留；ST ownerRef因本次保留ST而合法，不能误拒绝或错误阻塞transfer/unregister。未发WAL风险与删除策略分别说明。 | #20 |
| REG20-STORAGE-004.delete-with-st-owner | 同一策略组合删除时仍使用whenDeleted而非whenScaled | Q：独立公开API/TLS fixture运行whenScaled=Retain、whenDeleted=Delete且已采用的原Agent/ST/Pod/PVC，保留真实原ST ownerRef。预览并按原UID/RV Foreground删除Agent，先核实实例清空完成，再独立核实原PVC存在/删除。 | 删除允许且原PVC结果按whenDeleted=Delete为Deleting→Deleted；不能因whenScaled=Retain保留该卷或把ST引用当Retain已确认，也不能直接删除PVC。 | #20 |
| REG20-STORAGE-005.pending-suspend | 挂起后的原卷仍承担Delete责任时不提前恢复挂载 | Q：真实HTTP→生成Kitex→SQLite/TLS运行原UID PVC Agent，实际whenScaled=Delete已采用。公开发布挂起，实例变更完成而独立原PVC任务为Deleting，原PVC尚无deletionTimestamp且UID历史可信；保存运行草稿并预览恢复。随后让原PVC返回404，先通过原任务refresh实际核实并持久Deleted，再预览/下发恢复，模拟原Agent owner链产生同名新PVC UID并读取来源。独立whenScaled=Retain正控挂起后原卷Retained，恢复复用原UID。 | 未终结Delete任务的同Agent/原PVC UID不得因历史可信被视为Retain，恢复预览409且无新mutation；仅一次GET404不能跳过旧任务。原任务先独立Deleted后恢复允许，新卷UID与旧卷责任分别保存；Retain正控通过，不阻塞其他卷或已经终态的责任。accept与dispatch复用同一guard，不另造状态旁路。 | #20 |
| REG20-STORAGE-005.pending-shard | 减分片后原卷Delete未决时不提前加回原shard | Q：独立公开HTTP/Kitex/TLS fixture运行2shard原UID PVC Agent，实际whenDeleted=Delete已采用，明确减至1shard；旧shard ST/Pod消失但原PVC仍同UID存在且任务Deleting。保存加回2shard草稿并预览。之后原PVC404先由原任务refresh核实持久Deleted，再明确加回并回读新owner链同名新PVC UID及旧任务结果。独立whenDeleted=Retain正控减分片后Retained，再加回复用原PVC。 | 原shard所用PVC的未结束Delete责任使加回预览409、原CR/卷不被新写；完成原UID404只读核验并落盘Deleted后允许加回，新旧PVC身份独立。Retain正控恢复允许；不能因其他卷未决一律阻塞。共用准入/派发重验，不通过直接业务表写或虚构不可达竞态测试。 | #20 |

## 补充回归：来源#21（最终统一执行#21）

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| REG21-SIGNAL-001.lock-wait | Q：锁等待撤回不创建fixture | 由真实子进程A持lane；启动真实runner子进程B等待同锁，在其取得锁前发协作停止；释放A并读取B的index/ledger/退出码。 | B未创建任何资源、未启动case；完整期望集均非成功/未运行，退出非零；锁释放后不会继续准备fixture。 | #21 |
| REG21-SIGNAL-002.term-after-setup | K：SIGTERM后完成真实fixture清理 | 在独占profile目标启动runner子进程，等平台namespace原UID持久checkpoint后发送SIGTERM；等待自然协作退出，回读该原UID、子进程、index及ledger。 | 停止新case；当前例保留中断、剩余not_run；原UID资源正常finally清理并NotFound、辅助进程退出；整体非零，不将中断记passed。 | #21 |
| REG21-SIGNAL-003.keyboard-interrupt | K：SIGINT/KeyboardInterrupt走同一清理协议 | 新独立fixture达到同一checkpoint后向实际Python子进程发SIGINT，核KeyboardInterrupt路径与退出/清理证据。 | 与TERM独立取证；原错误/中断不被finally异常覆盖；原UID资源清理、余例not_run、退出非零，不绕过收据写入。 | #21 |
| REG21-SIGNAL-004.repeat-during-cleanup | K：重复信号不打断cleanup | 新fixture开始正常清理后用受控cleanup屏障暂停；连续再发TERM/INT，放行屏障并等有界退出。 | cleanup只执行一次，重复信号被记录；不因第二次信号跳出清理；正常GC预算不被缩成force，最终身份/cleanup可回读，run仍非成功。 | #21 |
| REG21-RESULT-005.setup-failure | K：部分setup失败也保存身份并finally | 创建本run真实namespace并持久原UID后，在后续平台setup的受控边界注入异常；保留其余多个待执行固定键。 | 当前键environment_failed，后续not_run全部补齐；已建原UID资源被finally回收；不因Suite构造/setup在try外而缺收据。 | #21 |
| REG21-RESULT-006.cleanup-failure | K：cleanup失败阻断门禁且不掩盖断言失败 | 对独立fixture先制造明确断言失败，再使用实际目标的受限身份触发删除403；保存证据后通过单独受控恢复身份清理原UID并记录独立恢复收据。 | 原断言和403 cleanup失败同时存在；原run非零且完整剩余not_run；后续恢复成功不把原cleanup改success或原run改绿，读取失败不当空集。 | #21 |
| REG21-RESULT-007.before-first | Q：首例前异常退出仍补齐期望键 | 启动真实受控runner子进程，在第一个case前触发明确异常退出；用固定期望集读取并验证该attempt的index。 | 保存setup阶段/真实exit，当前键environment_failed、其余固定键全部not_run；无成功case，不能只因存在index或进程输出称完整门禁成功。 | #21 |
| REG21-RESULT-007.after-first | Q：部分成功后异常退出不拼全绿 | 新独立attempt由真实受控子进程完成一个case后异常退出；核其真实完成记录与完整剩余键。 | 已完成case保留原结果，后续键全部not_run并记录中断；不能缺行、借旧attempt补绿或因为首例通过就报profile成功，整体非零。 | #21 |
| REG21-RECOVER-008.kill-then-recover | K：SIGKILL靠持久ledger恢复 | 独立fixture原UID checkpoint持久后SIGKILL实际runner进程；以新进程调用recover，仅从本run ledger核环境及原UID并正常清理，再回读对象。 | 不声称原finally执行；原run保持interrupted/cleanup未知或失败。recover单独记录原UID已NotFound，不能自动重跑业务或把旧run记成功。 | #21 |
| REG21-LEDGER-009.uid-replaced | K：恢复不删除同名替代UID | 建立原UID ledger后由独立测试fixture删除原对象、创建同名新UID；执行recover并回读替代对象，最后由独立fixture owner清理其替代资源。 | recover检测UID不符并拒绝删除/改写替代对象，原run不通过；替代fixture最终清理单独取证，不能冒充原run恢复成功。 | #21 |
| REG21-LEDGER-010.read-failure | K：恢复读取失败不等于资源已消失 | 新独立原UID fixture持久后，使recover的实际目标身份对读取返回403；执行恢复并观察结果，再由fixture owner恢复权限并清理原UID。 | 读取错误保持未确认/cleanup失败，不能报NotFound或根据前缀盲删；后续恢复另有收据，原失败保留。 | #21 |
| REG21-VALIDATOR-011.missing-key | 缺少必选执行键不能通过 | 独立Q协议fixture：从基线移除一个必选profile/variant执行键。运行实际validator并保存exit及诊断。 | 报告缺失键并非零退出；其他成功记录不抵扣。 | #21 |
| REG21-VALIDATOR-011.duplicate-key | 重复记录不能增加覆盖或覆盖失败 | 独立Q协议fixture：为同一candidate/profile/variant插入第二条记录，分别保留成功/失败。运行实际validator并保存exit及诊断。 | 拒绝重复键；不按最后一条或绿色行覆盖原结果。 | #21 |
| REG21-VALIDATOR-011.unknown-key | 未计划子例不能替代固定子例 | 独立Q协议fixture：插入一个未知variantId并保持声称的总数。运行实际validator并保存exit及诊断。 | 拒绝未知ID，覆盖数不能由总行数决定。 | #21 |
| REG21-VALIDATOR-011.skipped | skip不能冒充执行通过 | 独立Q协议fixture：保留真实Go/浏览器skip事件，顶层记录故意仍标passed。运行实际validator并保存exit及诊断。 | 拒绝skip/缺少实际pass事件，不能只信包退出0。 | #21 |
| REG21-VALIDATOR-011.wrong-source | 旧源码收据不能冒充当前候选 | 独立Q协议fixture：将一个记录的testedCommit替换为另一commit，其余ID和结果不变。运行实际validator并保存exit及诊断。 | 拒绝tested/runner/candidate或镜像revision不一致；不合并旧源绿色结果。 | #21 |
| REG21-VALIDATOR-011.wrong-profile | 其他profile的通过不能替代当前环境 | 独立Q协议fixture：将LOCAL_FULL记录标成CI_FAST，保留原darwin/arm64运行身份。运行实际validator并保存exit及诊断。 | 拒绝环境或profile来源不符，不能按相同variantId复用。 | #21 |
| REG21-VALIDATOR-011.changed-artifact | 修改或缺失证据必须使门禁失败 | 独立Q协议fixture：保持记录的sha256，单独修改一个相对artifact字节。运行实际validator并保存exit及诊断。 | 拒绝实际hash不符，不能只读取声明的sha256。 | #21 |
| REG21-VALIDATOR-011.cleanup-failure | 产品断言通过不掩盖清理失败 | 独立Q协议fixture：基线功能仍passed，cleanup明确success=false，实际runner退出0。运行实际validator并保存exit及诊断。 | validator必须非零且保留cleanup失败，不采信进程0。 | #21 |
| REG21-VALIDATOR-011.contract-mismatch | 同ID错误输入或预期不能借壳通过 | 独立Q协议fixture：保留variantId但修改purpose/steps/expected的指纹。运行实际validator并保存exit及诊断。 | 固定验收契约不符即拒绝；不能将相近用例当同一输入通过。 | #21 |
| REG21-VALIDATOR-011.wrong-image-source | 旧镜像不能冒充新产品源码 | 独立Q协议fixture：仅将镜像config源码标签改为另一commit，保留record candidate；运行实际validator。 | 拒绝镜像revision与候选不一致，不能只信runner参数。 | #21 |
| REG21-VALIDATOR-011.missing-artifact | 缺失证据必须阻止通过 | 独立Q协议fixture：删除一个仍被evidenceRefs引用的文件，运行实际validator。 | 明确证据缺失并非零退出，不从旧目录寻找同名文件。 | #21 |
| REG21-VALIDATOR-011.path-escape | 归档引用不能读取根目录之外的文件 | 独立Q协议fixture：evidenceRefs指向../outside.json且给出该受控外部文件的正确hash，运行实际validator。 | 仍拒绝路径越界；不因文件存在/hash正确放行，不读取用户认证材料。 | #21 |
| REG21-EXPORT-012.console | 子进程敏感输出在公开console前已截断 | 启动实际受控子进程向stdout/stderr输出本例合成认证canary并非零退出，经正式执行捕获路径处理；读取公开摘要及受保护原始日志。 | 原退出/失败不改绿；console/公开摘要没有canary，原日志不进公开白名单；保留稳定ID和exit可定位故障。 | #21 |
| REG21-EXPORT-012.private-source | 私有源目录不能经白名单出口泄露 | 独立临时目录调用实际exporter，尝试未获准的私有源目录夹具（只用本例合成文件，不复制真实私有代码）；再用安全结果/截图/hash目录正控。 | 不安全输入无法形成可发布bundle，退出非零或明确排除且必要证据缺失使门禁失败；安全正控可导出并从新路径复验，不改原件或伪造完整性。 | #21 |
| REG21-EXPORT-012.kubeconfig | kubeconfig不能进入公开bundle | 独立临时目录调用实际exporter，尝试本例合成kubeconfig夹具；再用安全结果/截图/hash目录正控。 | 不安全输入无法形成可发布bundle，退出非零或明确排除且必要证据缺失使门禁失败；安全正控可导出并从新路径复验，不改原件或伪造完整性。 | #21 |
| REG21-EXPORT-012.raw-db | 原始SQLite数据库不能进入公开bundle | 独立临时目录调用实际exporter，尝试本例真实临时SQLite文件；再用安全结果/截图/hash目录正控。 | 不安全输入无法形成可发布bundle，退出非零或明确排除且必要证据缺失使门禁失败；安全正控可导出并从新路径复验，不改原件或伪造完整性。 | #21 |
| REG21-EXPORT-012.image-tar | 镜像层归档不能进入公开bundle | 独立临时目录调用实际exporter，尝试本例合成镜像tar夹具；再用安全结果/截图/hash目录正控。 | 不安全输入无法形成可发布bundle，退出非零或明确排除且必要证据缺失使门禁失败；安全正控可导出并从新路径复验，不改原件或伪造完整性。 | #21 |
| REG21-EXPORT-012.canary | 批准文本仍拒绝已登记的敏感值 | 独立临时目录调用实际exporter，尝试批准路径文本中含本例已登记的合成认证canary；再用安全结果/截图/hash目录正控。 | 不安全输入无法形成可发布bundle，退出非零或明确排除且必要证据缺失使门禁失败；安全正控可导出并从新路径复验，不改原件或伪造完整性。 | #21 |
