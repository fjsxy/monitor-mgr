# #30 实际执行结果

本文件区分实施前计划、本地执行与尚未执行的真实集群/浏览器。五列计划见 `docs/e2e/issue-30.md`，已完整发布并回读：[计划评论](https://github.com/fjsxy/monitor-mgr/issues/30#issuecomment-6337276378)。

| 层次 | 来源与入口 | 实际结果 |
| --- | --- | --- |
| 完整本地门禁 | `testedCommit=b1aa1457535a4ab4038327d5d81542d5683ea468`；`make ut PYTHON=/tmp/monitor-mgr-spec23-tools.3nB8bT/venv/bin/python` | 退出 0；18/18 Ginkgo UT、race、生成一致性、vet、前端构建通过；产品语句覆盖率 77.79%，平台测试 475.787s。`.scratch/ut-results.json` 的 candidate/count 已回读核对。 |
| 合并后定向行为 | 合入集成 `2d895bf28e51e016bc3ffc90b988c8a6b980054c`，代码收敛至 `6fd2f2a9305e628ab147ade50f57538b423c227c`；`make ut-agent-creator` 与定向 `UT-12/16` | UT-08/18 通过（6.258s），历史/删除 UT-12/16 通过（26.728s）；包含 #29/30 组合，未将定向结果冒充最终 head 的全量门禁。 |
| 前端、脚本、Helm 接线 | `npm --prefix web run build`、Playwright `--list`、隔离 Python `py_compile`/模块导入、`receiver-chart-check`、`ci-cases-check`、`git diff --check` | 全部通过；仍为 18 UT / 10 E2E / 唯一 Playwright test。`--list` 只证明注册，未执行浏览器。 |
| 真实集群 I30-K01/02/03 | `make e2e` 的 E2E-04 | **未执行**。根协调者将在最终同一 head 的独占环境执行；当前没有真实 remote_write 样本、实际采用配置或有序 Helm 准备通过收据。 |
| 浏览器 I30-B01/02 | `make e2e` 的 E2E-10 唯一 Playwright test | **未执行**。当前仅编译和注册通过，不能把上游旧 head 的浏览器通过替代新增交互验收。 |
| 远程 CI | 最终集成 head | **未执行/未核验本切片结果**，由集成协调者统一回读。 |

公开 HTTP → 真实 Kitex RPC → SQLite/worker 的既有原生 selector 接口已经支持所需后端行为；新增 Ginkgo 验证固定创建用户、管理员编辑、版本冻结、自定义表达式、namespace、动态匹配、错误区分及直接 RPC 保护。首次定向运行暴露的是测试对 optional 空集合 JSON 字段的错误假设，已修正为公开 DTO 的可选语义；不将其描述为产品缺陷红绿。

新的采集证据门禁先失败后通过：缺 validator 的 RED，补实现后 GREEN；后续缺 run/boundary 的 RED，再 GREEN。独立负例拒绝缺样本、错误 exporter Pod UID、旧样本、缺实际采用 job、receiver 自行采集、错误创建用户、动态版本变化、配置未变化、错误 candidate/run 和缺原进程 informer 证据；完整汇总另拒绝缺 E2E-04 采集收据。

当前 E2E fixture 以同一个 Helm release 分两阶段准备 Operator：先 CRD/SA/RBAC/Service，等待全部 CRD Established，实际验证原 SA 的 discovery/list 和 get/list/watch 权限，再创建 Deployment。产品的同原 Pod/container/metrics 能力验证继续执行。锁定 Operator 源码在启动时一次性决定 ScrapeConfig 支持，因此单纯延长等待不能使已禁用 informer 的进程自行开启。[锁定启动源码](https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/5cf2f5d37906d64b2259a262e319df4c74639e31/cmd/operator/main.go)

旧 CI 的公共收据仅证明联合运行能力判据未满足；没有该次私有启动日志和指标原文，**不能将准备顺序缺口断言为旧 CI 已证实的实际根因**。最终真实运行仍须核验原进程和样本证据。
