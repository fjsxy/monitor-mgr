# #30 Agent 创建用户关联与真实采集局部执行结果

本记录只报告本分支实际执行。代码来源 `testedCommit=a254466743c7d28e7e75174557db8086bc6ab66d`，已包含集成基线 `8fe0f7be4d2a4b3c7876374d749375d0c4c31c39`。后续仅记录结果的提交不替代该代码来源。测试计划修订 2：[Issue 完整计划评论](https://github.com/fjsxy/monitor-mgr/issues/30#issuecomment-6415719569)，已与本地 `docs/e2e/issue-30.md` 精确回读一致。

## 交付行为

显式快捷将 Agent 固定 `creatorUserId` 写入原生 ServiceMonitor selector，保留其他 labels/expressions；默认 None/Own 不变。未知创建用户时禁用快捷并解释原因，有权用户仍可保存自定义规则。管理员编辑、归属和角色变化不重算已存目标；label 不参与实例授权。未扩展后端 API 或 schema。

E2E-04 新增两个创建角色的真实 KSM/Agent 编排、独占锁定 Prometheus receiver、前后原进程配置/activeTargets、实际 SM→Service→Endpoints→PodUID 矩阵、双向正负查询及实际样本时间。动态新增前后 Agent UID/版本/spec 与写审计共同防止重发。真实受限 SA 与 metadata audit 保留父写、SM 读、Operator 创建正控，平台所有 SM mutation 尝试必须为 0。Go 通过共享 evidence registry 独立断言，字段缺失/null 不作为 false/0/空集合放行；来源精确绑定 `MONITOR_MGR_E2E_CANDIDATE`。

## 实际检查

| 检查 | 实际结果与边界 |
| --- | --- |
| `make ut-agent-collection` | UT-07/08/14/17 共 4 个 It 通过，每项 1 次；本包 12 项因 focus 未执行。29 个计划 UT 输入均有稳定原生 ReportEntry。不是全量 18 UT。 |
| `make check-issue30-e2e PYTHON=...` | Python 语法及真实模块 import、Go E2E proof 编译通过；Playwright 仍只发现 1 个 E2E-10 test。未执行真实 E2E。 |
| `make receiver-chart-check` | 锁定 Prometheus digest 的 Helm lint 通过；未部署 receiver。 |
| `make web-build` | TypeScript 与生产构建通过。 |
| `make check-generated` | Kitex 生成通过，`kitex_gen` 零 diff。 |
| `make vet` | 通过。 |
| `make ci-cases-check` | 固定清单一致：18 UT / 10 E2E，没有新增顶层用例。 |
| `git diff --check` | 通过。 |

以上最终检查对应同一代码来源。原生报告位于本机 `internal/platform/.scratch/issue-30.json`，定向日志 `/tmp/monitor-mgr-spec23-20261026/issue30-final-targeted.log`，其余检查日志 `/tmp/monitor-mgr-spec23-20261026/issue30-final-checks.log`。

## 测试先行与拒绝负控

- 本地真实 Chromium 组件 tracer：组件不存在时失败；实现后验证 None 不自动写、固定 creator、保留 custom、unknown 禁用通过。日志 `issue30-ui-red.log` / `issue30-ui-green.log`；此处不冒充完整平台浏览器 B01/B02。
- 本地真实 TLS 代理 tracer：仅 token 上游配置原先失败；支持后验证固定受限 bearer、剥离前端任意身份、只读 503 和日志无 token 通过。日志 `issue30-proxy-red.log` / `issue30-proxy-green.log`；此处不冒充真实 Kubernetes RBAC。
- 共享 Unix wire 负控：Python 发出 passed 但仅提供 testedCommit、缺少 runId 的 issue30 proof，原生 Ginkgo E2E-04 实际失败一次并明确拒绝缺证据。`issue30-missing-proof-native.json` 中 SuiteSucceeded=false、NumAttempts=1、原因“缺少原始证据字段 runId”。这是预期拒绝，不计真实 E2E-04 通过。
- 上述 tracer/负控日志位于本机 `/tmp/monitor-mgr-spec23-20261026/`，完整真实运行必须由最终集成重新产生原始证据。

## 每个必选输入

| 用例编号 | 实际状态 | 证据边界 |
| --- | --- | --- |
| I30-U01.none | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U01.none`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U01.user | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U01.user`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U01.admin | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U01.admin`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U01.edit | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U01.edit`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U01.transfer | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U01.transfer`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U01.role | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U01.role`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U02.own | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U02.own`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U02.namespace-labels | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U02.namespace-labels`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U02.namespace-all | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U02.namespace-all`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U02.object-all | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U02.object-all`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U02.expression-in | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U02.expression-in`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U02.expression-notin | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U02.expression-notin`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U02.expression-exists | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U02.expression-exists`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U02.expression-notexists | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U02.expression-notexists`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U02.dynamic | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U02.dynamic`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U02.zero | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U02.zero`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U02.read-failure | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U02.read-failure`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U02.forbidden | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U02.forbidden`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U02.operator-scope | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U02.operator-scope`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U02.operator-permission.operator-get | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U02.operator-permission.operator-get`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U02.operator-permission.operator-list | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U02.operator-permission.operator-list`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U02.operator-permission.operator-watch | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U02.operator-permission.operator-watch`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U02.operator-permission.agent-get | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U02.operator-permission.agent-get`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U02.operator-permission.agent-list | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U02.operator-permission.agent-list`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U02.operator-permission.agent-watch | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U02.operator-permission.agent-watch`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U03.http | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U03.http`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U03.rpc | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U03.rpc`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U03.known-user-unknown-role | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U03.known-user-unknown-role`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-U03.unknown-user | 通过（定向 UT） | 原生 Ginkgo ReportEntry `I30-U03.unknown-user`；HTTP/真实 Kitex/SQLite 或受控 TLS 目标；不代表真实集群。 |
| I30-K01.user | 未执行（真实集群） | 已交付 E2E-04 编排和独立 Go 原始 proof 断言；编译通过，等待父集成真实执行。 |
| I30-K01.admin | 未执行（真实集群） | 已交付 E2E-04 编排和独立 Go 原始 proof 断言；编译通过，等待父集成真实执行。 |
| I30-K01.negative-user | 未执行（真实集群） | 已交付 E2E-04 编排和独立 Go 原始 proof 断言；编译通过，等待父集成真实执行。 |
| I30-K01.negative-admin | 未执行（真实集群） | 已交付 E2E-04 编排和独立 Go 原始 proof 断言；编译通过，等待父集成真实执行。 |
| I30-K01.exact | 未执行（真实集群） | 已交付 E2E-04 编排和独立 Go 原始 proof 断言；编译通过，等待父集成真实执行。 |
| I30-K01.receiver | 未执行（真实集群） | 已交付 E2E-04 编排和独立 Go 原始 proof 断言；编译通过，等待父集成真实执行。 |
| I30-K01.audit | 未执行（真实集群） | 已交付 E2E-04 编排和独立 Go 原始 proof 断言；编译通过，等待父集成真实执行。 |
| I30-K02.dynamic | 未执行（真实集群） | 已交付 E2E-04 编排和独立 Go 原始 proof 断言；编译通过，等待父集成真实执行。 |
| I30-K02.edit | 未执行（真实集群） | 已交付 E2E-04 编排和独立 Go 原始 proof 断言；编译通过，等待父集成真实执行。 |
| I30-K02.namespace | 未执行（真实集群） | 已交付 E2E-04 编排和独立 Go 原始 proof 断言；编译通过，等待父集成真实执行。 |
| I30-K03.process | 未执行（真实集群） | 已交付 E2E-04 编排和独立 Go 原始 proof 断言；编译通过，等待父集成真实执行。 |
| I30-K03.read-failure | 未执行（真实集群） | 已交付 E2E-04 编排和独立 Go 原始 proof 断言；编译通过，等待父集成真实执行。 |
| I30-K03.forbidden | 未执行（真实集群） | 已交付 E2E-04 编排和独立 Go 原始 proof 断言；编译通过，等待父集成真实执行。 |
| I30-B01.quick | 未执行（完整浏览器） | 已交付唯一 E2E-10 中的独立 test.step；发现与构建通过，等待父集成真实执行。 |
| I30-B01.custom.all | 未执行（完整浏览器） | 已交付唯一 E2E-10 中的独立 test.step；发现与构建通过，等待父集成真实执行。 |
| I30-B01.custom.labels | 未执行（完整浏览器） | 已交付唯一 E2E-10 中的独立 test.step；发现与构建通过，等待父集成真实执行。 |
| I30-B01.admin-edit | 未执行（完整浏览器） | 已交付唯一 E2E-10 中的独立 test.step；发现与构建通过，等待父集成真实执行。 |
| I30-B02.known | 未执行（完整浏览器） | 已交付唯一 E2E-10 中的独立 test.step；发现与构建通过，等待父集成真实执行。 |
| I30-B02.unknown | 未执行（完整浏览器） | 已交付唯一 E2E-10 中的独立 test.step；发现与构建通过，等待父集成真实执行。 |
| I30-B02.protected | 未执行（完整浏览器） | 已交付唯一 E2E-10 中的独立 test.step；发现与构建通过，等待父集成真实执行。 |

## 未执行与环境状态

本分支未创建 kind；没有宣称真实样本、真实 browser、真实 cleanup、全量 UT/race/产品覆盖率或远端 CI 通过。真实 E2E-04/10、完整 18 UT/10 E2E、race、产品 statement coverage≥70%、最终来源绑定与清理留给父集成统一验收。本地针对性执行没有环境失败；预期拒绝负控单列如上。Issue 保持 OPEN，未创建独立 PR。
