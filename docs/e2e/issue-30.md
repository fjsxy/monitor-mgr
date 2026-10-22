# #30 Agent 按创建用户关联与实际采集闭环自动化计划

主要边界已经批准：公开 HTTP → 真实 Kitex RPC → SQLite/worker；补充直接 RPC、独占真实集群与唯一 Playwright test。保持 18 UT / 10 E2E 顶层目录，子例接入相关 `It` / `By` / `test.step`。本文件是计划，实际结果另记并绑定 `testedCommit`。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| I30-U01（UT-07/08） | 固定创建用户与原生选择规则 | 普通用户创建 Agent，确认初始 `serviceMonitor.object.mode=None`；明确写入 `monitor-mgr.io/user-id=<creatorUserId>` 原生 Labels 规则；管理员修改其他字段后回读草稿、预览、发布版本；分别创建管理员 Agent 并按其 creatorUserId 关联。 | None 默认不变；两个 Agent 各固定其实际创建用户，管理员编辑用户 Agent 不改成管理员 ID；版本中原生 `serviceMonitorSelector` 与明确保存值一致。 | #30 |
| I30-U02（UT-08/14） | 自定义与 namespace 独立、动态匹配 | 使用受控 TLS Kubernetes 目标，通过公开 HTTP 保存 user label 加自定义 matchExpressions；分别 Own、Labels、All namespace；新增同 user ServiceMonitor 并再次预览；分别给零匹配、目标读取失败、Operator 权限不足。 | 原生规则可表达且保存不丢字段；Own 不越界，显式范围才跨 namespace；新增同 user 动态出现；零匹配有成功读取时间且空集合，失败保留明确错误并不伪装空成功。 | #30 |
| I30-U03（UT-07/17） | 关联不能绕过实例保护 | 普通用户对管理员创建 Agent 经 HTTP、直接真实 Kitex RPC 保存 user label 规则；管理员保存同一配置；读取未知创建角色但可靠 creatorUserId 的历史 Agent。 | 普通用户写入返回 FORBIDDEN；管理员可写；采集标签不决定平台授权；可靠 user ID 不因角色未知而丢失，权限仍独立执行。 | #30 |
| I30-K01（E2E-04） | 普通用户与管理员真实采集分组 | 本 run 独占 data namespace 用 Helm 部署锁定 Prometheus2.55.1 receiver，关闭自身 scrape；经平台公开 API 由普通用户、管理员分别发布 exporter 与 Agent，以固定创建用户原生 selector 关联并 remote_write 到 receiver；读取 Agent 采用配置并通过 receiver `/api/v1/query` 查询 `kube_pod_info`。 | 实际配置含本例配套 ServiceMonitor；receiver 查询得到各 exporter 对应 namespace/service/instance 的实际样本与时间；样本绑定 exporter/Service/Pod/Agent/receiver UID、锁定镜像与本候选提交；不同 creator 分组不会交叉采用。 | #30 |
| I30-K02（E2E-04） | 动态新 exporter、跨 namespace 与管理员编辑 | 先发布普通用户 Agent 后在已明确发现范围内由该用户发布第二个 exporter；不重新发布 Agent，等待采用新配套 SM 并收到新目标样本；管理员编辑用户 Agent 后核对固定 selector；同 label 但 namespace 不在 Own 范围时预览零匹配，显式 namespace Labels 后可见。 | 新 exporter 动态采集且有新增前后样本证据；Agent 原 UID 与版本保持；管理员编辑不改目标 user；namespace 范围独立生效，预览不声称采集成功。 | #30 |
| I30-K03（E2E-04） | 原进程能力证据与失败区分 | 选四类采集 CR 前经公开依赖预检等待同原 Operator Pod/container 的 Agent controller 及 ScrapeConfig informer 指标收敛；保留有限超时；用独占代理/受限凭据分别产生读取失败与权限不足。 | 不以 Deployment Ready 替代原进程能力；能力未满足仍明确拒绝发布；读失败、权限不足分别可判断，不误判为零匹配或真实采集成功。 | #30 |
| I30-B01（E2E-10） | 完整快捷关联与自定义交互 | 在唯一 Playwright test 的最后注销之前，普通用户打开自己的 Agent，确认 None；选择按创建用户关联并看到具体 user ID；保存、重开、匹配预览和下发预览；切 namespace 和自定义表达式；管理员打开用户 Agent 改其他字段并保存。 | 明确选择后投影为原生 user label，保存重开固定；namespace 单独可配，自定义规则可继续编辑，管理员登录不改目标；页面可见预览状态。 | #30 |
| I30-B02（E2E-10） | 历史未知与保护交互 | 使用本 run 可靠创建用户但角色未知、创建用户未知、管理员创建 Agent 夹具；分别打开快捷配置并尝试普通用户写入。 | 可靠 user ID 可作为快捷目标而不放宽管理权限；未知 user ID 明确禁用快捷且不 fallback，自定义规则仍可按已有权限编辑；管理员 Agent 普通用户只读。 | #30 |

真实集群和浏览器执行由集成协调者独占运行；实现阶段不借用用户既有集群、平台账号、`18080` 或 `18481`。接收端自身 `scrape_configs: []`，只有 Agent remote_write 能产生该 run 的样本。
