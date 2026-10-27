> 2026-10-23 修订：配套 ServiceMonitor 由 monitor-op 管理。本正文替代旧职责描述；历史计划/验收评论保留原语义，不代表本修订已完成。PR #31 尚未合并，实施前应核实并复用其中适用部分，不重复开发已具备的行为。

## 父规格

[fjsxy/monitor-mgr#23 exporter 配套 ServiceMonitor、实例创建归属与管理员实例保护规格](https://github.com/fjsxy/monitor-mgr/issues/23)。

## 直接依赖

[fjsxy/monitor-mgr#26](https://github.com/fjsxy/monitor-mgr/issues/26)、[fjsxy/monitor-mgr#28](https://github.com/fjsxy/monitor-mgr/issues/28)。经 #28 传递依赖 [fjsxy/monitor-in-hand#87](https://github.com/fjsxy/monitor-in-hand/issues/87)，不重复增加传递边。

## 交付行为

历史实例保持原 exporter UID 和管理历史；新的明确发布才启用 Operator 配套采集。若实际存在旧方案的 SM 或在途任务，按显式迁移路径退出旧管理者，避免自动接管、双写和重复采集。

## 验收标准

- [ ] 平台/Operator 升级、历史创建者补齐、管理员权限确认及轮询不为历史 CR 自动启用新配置，不生成新 SM；旧 CR 未声明开关时保持关闭。
- [ ] 下一次新预览、新发布将采集意图加入现有 exporter CR，保持原 CR UID，不重复创建 exporter；旧冻结版本、旧任务与重试不追溯追加字段或资源。
- [ ] 已知创建者沿用原 user ID；未知创建者明确阻止依赖归属的采集发布，不回退当前用户或集群所属用户。管理员权限确认不补造创建事实。
- [ ] 先盘点实际部署版本、旧任务发送边界、SM/CR UID、owner、spec、Agent 选择范围并留快照；不能推定未合并 PR 已部署。仅有历史单 CR 时不要求人为制造旧 SM。
- [ ] 切换后旧 SM 自动派发、重试、继续和重启恢复不续写；已发请求只读核验并保留审计。管理员显式处理旧任务，不能把未知结果假记成功或把旧任务改为新模式。
- [ ] 旧平台 SM 不被 Operator 自动接管。交付管理员切换操作文档与必要的只读盘点/核验入口：核实 UID/RV、配置和采集影响后显式清理旧对象，再由新发布启用 Operator；平台不增加通用 SM 写入或迁移删除 API。
- [ ] 检查所有 Agent 选中的旧对象，包括新旧名称不同的情况；切换后只保留目标配置，不双写、不重复采集。采集空窗明确安排并记录，不宣称无损迁移。
- [ ] 遇到外部同名对象、原 exporter UID 替换、漂移、缺权限、响应丢失或清理未完成时保持保护，给出可操作原因，不自动继续发布或删除无关对象。
- [ ] Ginkgo/独立旧库及任务夹具覆盖无旧 SM、有旧 SM、未知发送结果和 UID 替换；真实集群与浏览器验证升级零新增写入、显式切换前后差异、无重复采集和完整清理，数据库快照与历史记录可核对。

## 独立演示

分别升级只有 exporter CR 和已存在旧平台 SM 的两个夹具：前者新发布后启用，后者先展示盘点与待处理状态，经管理员显式清理再发布；回读原 CR UID、旧记录和实际采集目标，证明无自动接管或重复采集。

## 实施与验收约束

- 本轮只同步任务范围，不领取、不编码。开始或恢复实现前，回读父规格、依赖及评论，按 `docs/agents/implementation.md` 发布并回读五列用例计划，同步本地 `docs/e2e/issue-<编号>.md`；既有稳定 ID 保留，变化的输入和预期先修订再实现。
- Go UT/E2E 使用 Ginkgo，UT 覆盖率至少 70%；主要行为入口为公开 HTTP → 真实 Kitex RPC → SQLite → worker，必要时补直接 RPC、受控 TLS Kubernetes 目标、真实集群和浏览器。
- 每项自行交付对应测试，不把测试集中留给 #30。Makefile 固化重复命令，部署使用 Helm；关键授权、UID/RV 条件写、发送边界和恢复禁重放添加中文注释。
- 证据分别记录本地测试、真实集群、浏览器、远端 CI、清理和 testedCommit；旧 PR #31 的绿色结果不能替代新边界验收。
- 不创建独立采集配置 CRUD 产品入口；平台不直接 create/update/patch/delete 配套 ServiceMonitor，不复制 Operator 的 Service 规则，不把 user 标签用作授权依据。
