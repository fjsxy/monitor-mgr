# Issue #21：Ginkgo 迁移实际验证结果

执行日期：2026-10-21；宿主：macOS arm64；Go：1.27.1；Ginkgo：2.33.1；Gomega：1.44.0。Python 工具自测使用独立虚拟环境及锁定的 PyYAML 6.0.3，前端依赖使用现有 lockfile 安装。

测试计划原评论：[初版](https://github.com/fjsxy/monitor-mgr/issues/21#issuecomment-6313032671)；用户确认范围后发布并回读的[修订版](https://github.com/fjsxy/monitor-mgr/issues/21#issuecomment-6313327646)。本地计划为 `issue-21-ginkgo.md`，正文与修订评论一致。

## 验证结果

| 用例编号 | 实际执行与结果 | 证据 / 范围 |
| --- | --- | --- |
| GINKGO-UT-001 | 通过：115 个原顶层场景均改为 It；114 个通过，1 个显式 Host 浏览器夹具按原约定跳过；原根与子例 376 条通过记录逐项一致，遗漏 0、新增 0 | 三个包的 `.scratch/ginkgo-report.json`；根目录 `.scratch/ginkgo-case-comparison.json`；普通 UT 的 E2E 环境入口另行跳过 |
| GINKGO-UT-002 | 通过：全量 race 无报告；手写产品语句覆盖率 79.62%，门槛 70% | `make check PYTHON=.tools/ginkgo-venv/bin/python`；根目录 `.scratch/coverage.out`；`go tool cover` 交叉核对为 79.6% |
| GINKGO-Q-001 | 通过：干净提交 b8aafe6 的 58 个 Q 键均生成 passed 原收据；源码身份、清理及实际 Ginkgo 根/子例事件经独立 gate 函数重新核验。漏例、跳过、重复、重试、dry-run、非零退出负控也通过 | 本次为 macOS arm64 的 LOCAL_FULL 聚焦 Q；149 个 K/B 键保持 not_run，完整门禁正确拒绝为 incomplete-run；不代表 CI_FAST Linux amd64 通过 |
| GINKGO-E2E-001 | Host 调度正负控通过：真实 Go/Ginkgo child 逐键调用合成业务回调；成功、失败后继续、fail-fast 停止、重复键拒绝均符合预期 | 4 项 dispatcher 自测；没有访问 Kubernetes，没有执行真实产品 K/B |
| GINKGO-CI-001 | 通过：CI 文档与 115 个 It、254 项 Python 自测、58 Q / 17 Smoke 键及当前入口一致；新增传统 Go Test 会被检查拒绝 | `make ci-cases-check acceptance-plan`；固定目录仍为 80 个基础 case、207 个稳定子例 |
| GINKGO-SELECT-001 | 通过：Ginkgo 聚焦运行两项崩溃恢复场景；原 CLI 按 Issue/层级求交集，空交集拒绝；原生报告选择零场景不能通过 | `focus_layer_test.py` 6 项通过；崩溃子进程须选择 TestPlatform suite 与具体 It，保留退出码 97 的真实断言 |

最终 Python 工具自测：`unittest discover -s scripts/acceptance -p '*test.py'`，254 项全部通过。`make check` 内先前的 253 项同样通过；新增所属 Issue 选择自测后又完整执行 254 项。

`make vet`、`make web-build`、`make check-generated`、`git diff --check` 均通过；IDL 与 `kitex_gen` 没有变更。上述完整质量检查在提交前工作树执行，以 `ddbd4b5d8378d41b6d9ba0254314177eeb433842` 为分支基线；不能将基线 SHA 当作本次测试的干净候选 testedCommit。

本地日志位于 `/tmp/monitor-mgr-ginkgo-check.log`、`/tmp/monitor-mgr-ginkgo-host-final.log`、`/tmp/monitor-mgr-ginkgo-generated.log`；临时日志与 `.scratch` 原生报告未提交，仅作为本机验证原件。

## 未执行

真实 Kubernetes 的 LOCAL_FULL 207 键、CI_SMOKE 17 键、真实平台 Pod 浏览器场景和 GitHub Actions 均未在本轮执行。现有用户集群未被用于本次验证。Host 合成调度、普通 Go 测试与构建结果不替代这些环境的通过证据。

## 干净候选的 58 项 Q 收据

testedCommit：`b8aafe6ed8aec94bb9b122d215cc2feece3a071b`；dirty：`false`。以下从本次原 index 逐项读取，没有重写通过状态。每项原件、wrapper 和 Ginkgo 事件引用均按 hash 独立核验；清理成功。原件目录为 `/tmp/monitor-mgr-ginkgo-q-b8aafe6`，本机独立核验摘要为 `.scratch/ginkgo-q-validation.json`。

```sh
make acceptance-run PYTHON=.tools/ginkgo-venv/bin/python E2E_ARGS='--profile LOCAL_FULL --candidate b8aafe6ed8aec94bb9b122d215cc2feece3a071b --run-id ginkgo-q-20261021 --focus-layer Q --output /tmp/monitor-mgr-ginkgo-q-b8aafe6'
```

该命令的完整 index 保留 207 个必选键：58 passed、149 not_run，`index.state` 为 failed；`run.py` 退出码为 1（外层 make 为 2），命令输出摘要中的 `productGate` 为 false；这是聚焦结果不能冒充完整 profile 的预期保护。独立完整验证器返回 `incomplete-run`。下面的单键通过结果不改变这个结论。

| 稳定子例 ID | 层 | 原收据结果 |
| --- | --- | --- |
| T07.db | Q | 通过 |
| T07.rpc | Q | 通过 |
| T07.listener | Q | 通过 |
| T07.target-offline | Q | 通过 |
| I02.default-expiry | Q | 通过 |
| I02.configured-expiry | Q | 通过 |
| I02.cookie | Q | 通过 |
| I06.primary | Q | 通过 |
| C03.missing | Q | 通过 |
| C03.forbidden | Q | 通过 |
| C03.anonymous | Q | 通过 |
| C03.error | Q | 通过 |
| C09.http | Q | 通过 |
| C09.skip-tls | Q | 通过 |
| C09.external-ca | Q | 通过 |
| C09.external-cert | Q | 通过 |
| C09.token-file | Q | 通过 |
| C09.exec | Q | 通过 |
| C09.auth-provider | Q | 通过 |
| C09.proxy | Q | 通过 |
| K10.name | Q | 通过 |
| K10.collision | Q | 通过 |
| K10.fixed-target | Q | 通过 |
| K10.unsupported | Q | 通过 |
| P04.draft-race | Q | 通过 |
| P04.draft-after-preview | Q | 通过 |
| P04.target-after-preview | Q | 通过 |
| P08.cancel-wins | Q | 通过 |
| P08.dispatch-wins | Q | 通过 |
| R01.intervals | Q | 通过 |
| R01.freshness | Q | 通过 |
| R01.early-error | Q | 通过 |
| R03.missing-status | Q | 通过 |
| R03.incomplete-status | Q | 通过 |
| R03.ksm-suspended | Q | 通过 |
| R03.ksm-disabled | Q | 通过 |
| R03.ksm-no-match | Q | 通过 |
| R03.agent-zero | Q | 通过 |
| R03.agent-paused | Q | 通过 |
| R03.deleted | Q | 通过 |
| R05.page | Q | 通过 |
| R05.item | Q | 通过 |
| R06.primary | Q | 通过 |
| F01.absent | Q | 通过 |
| F01.empty-object | Q | 通过 |
| F01.empty-list | Q | 通过 |
| F01.unknown-mode | Q | 通过 |
| F02.primary | Q | 通过 |
| F03.quantity | Q | 通过 |
| F03.regexp | Q | 通过 |
| F03.selector | Q | 通过 |
| F03.url | Q | 通过 |
| F03.tls-pair | Q | 通过 |
| F03.unknown-field | Q | 通过 |
| F04.success | Q | 通过 |
| F04.failure | Q | 通过 |
| F05.before-commit | Q | 通过 |
| F05.after-commit | Q | 通过 |
