> 2026-10-23 修订：配套 ServiceMonitor 由 monitor-op 管理。本正文替代旧职责描述；历史计划/验收评论保留原语义，不代表本修订已完成。PR #31 尚未合并，实施前应核实并复用其中适用部分，不重复开发已具备的行为。

## 父规格

[fjsxy/monitor-mgr#23 exporter 配套 ServiceMonitor、实例创建归属与管理员实例保护规格](https://github.com/fjsxy/monitor-mgr/issues/23)。

## 直接依赖

[fjsxy/monitor-mgr#26](https://github.com/fjsxy/monitor-mgr/issues/26)、[fjsxy/monitor-mgr#28](https://github.com/fjsxy/monitor-mgr/issues/28)。经 #28 传递依赖 [fjsxy/monitor-in-hand#87](https://github.com/fjsxy/monitor-in-hand/issues/87)；本任务不必等待 #29 的历史迁移，可以使用新建实例验收。

## 交付行为

保留 Agent 按创建用户快捷关联与自定义规则，在真实集群证明 Agent 动态发现 Operator 生成的配套 ServiceMonitor，并将对应指标经 remote_write 送达可查询的接收端。

## 验收标准

- [ ] 快捷关联默认取 Agent 固定创建用户，展示具体目标用户并保存到配置；管理员编辑不得改成当前登录用户，创建用户未知时说明无法使用且不回退替代身份。
- [ ] 保持原生 serviceMonitorSelector、自定义 matchLabels/matchExpressions、默认不关联及匹配预览；不把快捷选项变成全选。
- [ ] object selector 与 serviceMonitorNamespaceSelector 范围分别配置；区分零匹配、读取失败和权限不足，覆盖跨 namespace 与受限可见范围。
- [ ] user 标签由平台通过 exporter CR 传递后经 Operator 投影；真实夹具中的 SM 必须由新 Operator 创建，具有实际 KSM CR UID 的 controller ownerReference，不允许平台或测试手写目标 SM 冒充集成成功。
- [ ] 标签仅供采集分组，不代替平台授权；普通用户不能通过 Agent 关联修改管理员创建的实例；实例创建/发布/集群归属变化均不静默改写已保存关联目标。
- [ ] 在 namespace 和读取权限满足时，新增同创建用户 exporter 可由既有 Agent 动态发现；Agent UID、已发布配置版本不因新增 exporter 被平台重写。
- [ ] 取得与测试 exporter 一一对应的实际指标样本、remote_write 接收与查询结果，区分用户组且检查无额外目标；CR Ready、owner、selector、targets/config 单独均不足以作为数据面通过证据。
- [ ] Ginkgo/真实 Kitex/SQLite 覆盖用户与规则语义，浏览器覆盖快捷/自定义/跨 namespace 主流程；真实集群锁定新 Operator 发布产物并确认平台零 SM 写入。沿用既有场景 ID、稳定过滤及 Python/Playwright 编排，改动范围内的 Go 场景仍使用 Ginkgo。
- [ ] 逐例记录 Operator/Agent/SM 身份、样本、平台/Operator testedCommit、镜像 digest 和清理结果；其他切片自行完成各自行为测试，本任务不替代 Operator 双版本或历史迁移验收。

## 独立演示

管理员和普通用户各发布 exporter 与 Agent，验证 user 标签和 owner 来自 Operator；动态新增 exporter，保持 Agent UID/版本不变并查询两组实际样本；管理员再编辑普通用户 Agent，确认目标 user ID 保持。

## 实施与验收约束

- 本轮只同步任务范围，不领取、不编码。开始或恢复实现前，回读父规格、依赖及评论，按 `docs/agents/implementation.md` 发布并回读五列用例计划，同步本地 `docs/e2e/issue-<编号>.md`；既有稳定 ID 保留，变化的输入和预期先修订再实现。
- Go UT/E2E 使用 Ginkgo，UT 覆盖率至少 70%；主要行为入口为公开 HTTP → 真实 Kitex RPC → SQLite → worker，必要时补直接 RPC、受控 TLS Kubernetes 目标、真实集群和浏览器。
- 每项自行交付对应测试，不把测试集中留给 #30。Makefile 固化重复命令，部署使用 Helm；关键授权、UID/RV 条件写、发送边界和恢复禁重放添加中文注释。
- 证据分别记录本地测试、真实集群、浏览器、远端 CI、清理和 testedCommit；旧 PR #31 的绿色结果不能替代新边界验收。
- 不创建独立采集配置 CRUD 产品入口；平台不直接 create/update/patch/delete 配套 ServiceMonitor，不复制 Operator 的 Service 规则，不把 user 标签用作授权依据。
