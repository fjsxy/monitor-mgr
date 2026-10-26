# #30 Agent 创建用户关联与真实采集测试计划

2026-10-26 修订：从新集成基线28535ad实施，保留原 I30 稳定ID并展开必选输入。公开测试边界已在父规格/实施任务确认：HTTP → 真实 Kitex → SQLite/worker，补直接RPC、受控TLS目标、真实集群和唯一Playwright test。配套SM由锁定RC4 Operator创建，平台只写父CR意图。计划不表示执行通过；旧PR31结果不继承。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| I30-U01.none | 不改变默认不关联 | HTTP分别创建普通用户/管理员Agent，回读草稿、RPC与YAML | 四类Object None/namespace Own；CR selector省略，未点击快捷不写user规则 | #30 |
| I30-U01.user | 普通用户固定目标并冻结 | 保存creator原生Labels+自定义表达式，预览/发布/读版本 | user label精确等创建user ID，typed/RPC/DB/YAML/版本一致 | #30 |
| I30-U01.admin | 管理员Agent关联自身 | 管理员独立创建/保存/预览/发布Agent | 目标等管理员自身固定creator，不取集群owner | #30 |
| I30-U01.edit | 管理员编辑他人Agent | 管理员修改用户Agent CPU，回读草稿/预览/冻结版本 | 已存目标user、其他labels/expressions/namespace不变 | #30 |
| I30-U01.transfer | 归属变化不改目标 | 保存规则后转移cluster，再读草稿/版本 | creator/原规则不变；权限按当前cluster管理关系 | #30 |
| I30-U01.role | 角色变化不改历史 | 原创建用户升/降角色后由有权actor回读/保存非关联字段 | 创建事实/已存目标不变，不按账号当前角色重算 | #30 |
| I30-U02.own | object与namespace独立 | 仅外namespace有同user SM，namespace Own预览 | 零匹配且lastSuccessAt非空，无error | #30 |
| I30-U02.namespace-labels | 显式跨namespace | object保留user+表达式，namespace Labels选择目标ns | 仅精确范围内对象匹配，namespace对象规则独立保留 | #30 |
| I30-U02.namespace-all | 原生All namespace | 独立改namespace All再预览/读YAML | namespaceSelector={}，按实际可见/权限范围匹配，不把对象改All | #30 |
| I30-U02.object-all | 保留原生对象All | 用户明确选object All，预览/读YAML；再改回None | All投影{}，None省略；快捷不自动覆盖自定义选择 | #30 |
| I30-U02.expression-in | 保留In | user label叠加有values的In，经HTTP/RPC保存/预览/版本 | fields与精确匹配集合保留，不扩大为全选 | #30 |
| I30-U02.expression-notin | 保留NotIn | 独立输入NotIn及排除对象 | 排除结果正确，往返不丢values | #30 |
| I30-U02.expression-exists | 保留Exists | 独立输入Exists，分别nil/显式空values沿已有契约往返 | 合法presence语义保留，CR符合原生规则 | #30 |
| I30-U02.expression-notexists | 保留DoesNotExist | 独立输入DoesNotExist及带禁止label对象 | 只排除对应对象，其他user条件不丢 | #30 |
| I30-U02.dynamic | 动态匹配不改草稿 | TLS目标增加同user SM并重复同revision预览 | 精确集合增加；无需修改Agent草稿/发布 | #30 |
| I30-U02.zero | 零匹配是成功读取 | TLS目标返回空items | 空matches、无error、有lastSuccessAt | #30 |
| I30-U02.read-failure | 读取失败不是空成功 | TLS目标真实返回500/连接失败 | TARGET_ERROR类明确，preview无lastSuccessAt；运行历史按原规则保留 | #30 |
| I30-U02.forbidden | 权限不足独立 | TLS目标SM list 403 | TARGET_FORBIDDEN类明确，与读取失败原因不同 | #30 |
| I30-U02.operator-scope | Operator可见范围独立 | 配置匹配namespace但Operator实际args排除该namespace | 明确范围受限，不伪装零匹配或采集成功 | #30 |
| I30-U02.operator-permission | Operator/Agent SA读取权限独立 | 各自SAR拒绝匹配SM或目标发现资源get/list/watch | 对应依赖原因可判定，不能以平台可读替代实际运行SA权限 | #30 |
| I30-U03.http | label不绕过管理员实例保护 | 普通owner HTTP给管理员Agent保存相同user label | FORBIDDEN、草稿/版本不变、零目标写 | #30 |
| I30-U03.rpc | 直接RPC也保护 | 同一输入经真实Kitex SaveDraft | FORBIDDEN；label不参与平台授权 | #30 |
| I30-U03.known-user-unknown-role | 可靠user与角色未知分开 | 独立旧库恢复可靠user/未知role，管理员读取；明确管理确认后普通owner自定义保存 | 可靠user不丢；权限独立，未确认普通owner只读；允许后按既有规则保存 | #30 |
| I30-U03.unknown-user | unknown不猜且保留自定义 | 未知creator但已允许管理的历史Agent，经HTTP/RPC读取并保存自定义team selector | creator仍空，不从owner/actor补造；自定义合法规则可保存；快捷禁用由B02核 | #30 |
| I30-K01.user | 普通用户真实样本与Operator来源 | 受限平台SA经公开API发布user KSM/Agent，按父status取SM，读采用配置/receiver查询 | SM owner UID与父相同，creator label正确；全部实际合法endpoint对应的正样本绑定namespace/Service/PodUID/IP:port/Agent，样本时间>=发布 | #30 |
| I30-K01.admin | 管理员真实样本与Operator来源 | 独立管理员组重复K01流程 | admin自身creator，实际采用/样本精确对应其全部合法endpoint；不以user组证据代替 | #30 |
| I30-K01.negative-user | user组不收admin目标 | receiver以user external identity+每个admin exporter UID/namespace查询 | 每项真实查询为空；不是只看config未包含 | #30 |
| I30-K01.negative-admin | admin组不收user目标 | 动态前后以admin identity+全部user exporter UID/namespace查询 | 每项为空，新增后仍无交叉 | #30 |
| I30-K01.exact | 无额外目标 | 查询两组全部kube_pod_info并与实际exporter tuples比较；jobs精确集合比较 | user最终2个exporter、admin1个exporter；样本和目标精确对应每个exporter的全部实际合法endpoint，无额外样本/目标/job | #30 |
| I30-K01.receiver | receiver只能由remote_write供样本 | Helm空scrape receiver，前后读原Pod/容器/config和镜像内容链 | scrape_configs=[]、无remote_write、原UID/container不变，查询确实经本receiver | #30 |
| I30-K01.audit | 平台零SM写且Operator实际创建 | 主流程前后真实SA权限/身份和metadata审计，含父写/SM读正控及RC4 SA创建事件 | 平台SM create/update/patch/delete/deletecollection尝试0；正控存在；来源digest/source精确锁定 | #30 |
| I30-K02.dynamic | 新exporter动态到达 | user Agent已发布后新增同creator exporter，不发布Agent，读原Agent/采用配置和新样本 | AgentUID/版本/父spec不变；config hash/jobs按新SM变化；新样本时间>=新增发布 | #30 |
| I30-K02.edit | 管理员编辑不改目标 | 管理员保存用户Agent非采集字段后回读 | target固定原creator，namespace/表达式保持，不重发Agent充当动态发现 | #30 |
| I30-K02.namespace | 真实Own与跨namespace | user label+Own预览；再显式namespace Labels后预览/发布 | Own空成功，范围覆盖后精确匹配；匹配预览不宣称样本已达 | #30 |
| I30-K03.process | 原进程实际能力 | 有限等待公开依赖预检；核同Prometheus Operator Pod/container AgentReady及ScrapeConfig informer | 同原进程能力满足；不足时明确拒绝，不以Deployment Ready代替 | #30 |
| I30-K03.read-failure | 真路径读取故障 | 独占故障接口对关联只读路径返回失败，保留真实受限上游身份并恢复 | 公开来源读失败、无成功时间，与Forbidden不同；主采集证据不伪报成功 | #30 |
| I30-K03.forbidden | 真实受限凭据拒绝 | 用真实SA/token切换到缺SM list权限，实际SSR/SSAR及预览后恢复原受限SA | 真实RBAC拒绝，错误明确，不合成为零匹配；恢复不抬高到admin | #30 |
| I30-B01.quick | 快捷固定目标与保存重开 | 唯一Playwright流程user Agent默认None/Own，点击快捷、保存重开、匹配/下发预览 | 展示具体creatorUserId与当前target；原生user标签保存，None只在明确点击后改变 | #30 |
| I30-B01.custom | namespace/表达式主流程 | 快捷后独立切namespace Labels/All、编辑自定义表达式并保存 | 原生字段保留，预览状态可见；快捷保留其他labels/expressions | #30 |
| I30-B01.admin-edit | 管理员编辑他人Agent | 管理员登录修改user Agent CPU再重开 | 当前target仍原user，namespace/表达式不变 | #30 |
| I30-B02.known | 可靠user但角色未知 | 当前管理员打开历史可靠user/未知role Agent | 快捷可用目标为可靠user，角色未知事实保留；普通用户权限单独受保护 | #30 |
| I30-B02.unknown | unknown与自定义 | 管理员或已允许的owner打开未知creator Agent，尝试快捷再保存自定义team规则 | 明确原因/快捷disabled，无fallback；有权时自定义可保存 | #30 |
| I30-B02.protected | 管理员Agent只读 | 普通owner打开管理员创建Agent及user标签配置 | 无保存入口或控件禁用，标签相同不能修改实例 | #30 |


执行映射：U01/U03 → UT-07/08/17；U02 → UT-08/14，既有UT-06的9组原生规则继续保留；K01/K02/K03 → 现E2E-04；B01/B02 → 现E2E-10唯一test、I26注销之前。每个必选输入有独立By/ReportEntry/test.step或原始观测。

新增Go业务断言使用Ginkgo/Gomega。Python/Playwright沿用编排与原始采证；Scenario.acceptance_evidence[issue30]经共享wire提交给Go，E2E-04必须提供proof，testedCommit精确绑定MONITOR_MGR_E2E_CANDIDATE。receiver为独占空TSDB、无自身scrape/remote_write，只接受Agent发送，测试不手工注入样本。Agent动态发现前后UID/currentVersionId/spec保持。

真实集群/浏览器及全量UT/race/覆盖率/CI/cleanup由父集成统一执行；本ticket先交针对性UT、真实编排与Go断言、Helm receiver和browser主流程。结果另存docs/e2e/issue-30-results.md，明确区分通过、失败、未执行、环境失败。
