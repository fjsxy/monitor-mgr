# Issue #21：Ginkgo 迁移实际验证结果

执行日期：2026-10-21；宿主：macOS arm64；Go：1.27.1；Ginkgo：2.33.1；Gomega：1.44.0。Python 工具自测使用独立虚拟环境及锁定的 PyYAML 6.0.3，前端依赖使用现有 lockfile 安装。

测试计划原评论：[初版](https://github.com/fjsxy/monitor-mgr/issues/21#issuecomment-6313032671)；用户确认范围后发布并回读的[修订版](https://github.com/fjsxy/monitor-mgr/issues/21#issuecomment-6313327646)。本地计划为 `issue-21-ginkgo.md`，正文与修订评论一致。

## 验证结果

| 用例编号 | 实际执行与结果 | 证据 / 范围 |
| --- | --- | --- |
| GINKGO-UT-001 | 通过：115 个原顶层场景均改为 It；114 个通过，1 个显式 Host 浏览器夹具按原约定跳过；原根与子例 376 条通过记录逐项一致，遗漏 0、新增 0 | 三个包的 `.scratch/ginkgo-report.json`；根目录 `.scratch/ginkgo-case-comparison.json`；普通 UT 的 E2E 环境入口另行跳过 |
| GINKGO-UT-002 | 通过：全量 race 无报告；手写产品语句覆盖率 79.62%，门槛 70% | `make check PYTHON=.tools/ginkgo-venv/bin/python`；根目录 `.scratch/coverage.out`；`go tool cover` 交叉核对为 79.6% |
| GINKGO-Q-001 | 原生报告的根/子例安全投影与漏例、跳过、重复、重试、dry-run、非零退出负控通过；58 Q 的干净提交收据验证将在冻结候选后补记 | 当前不能据此宣称 CI_FAST Linux amd64 已通过 |
| GINKGO-E2E-001 | Host 调度正负控通过：真实 Go/Ginkgo child 逐键调用合成业务回调；成功、失败后继续、fail-fast 停止、重复键拒绝均符合预期 | 4 项 dispatcher 自测；没有访问 Kubernetes，没有执行真实产品 K/B |
| GINKGO-CI-001 | 通过：CI 文档与 115 个 It、254 项 Python 自测、58 Q / 17 Smoke 键及当前入口一致；新增传统 Go Test 会被检查拒绝 | `make ci-cases-check acceptance-plan`；固定目录仍为 80 个基础 case、207 个稳定子例 |
| GINKGO-SELECT-001 | 通过：Ginkgo 聚焦运行两项崩溃恢复场景；原 CLI 按 Issue/层级求交集，空交集拒绝；原生报告选择零场景不能通过 | `focus_layer_test.py` 6 项通过；崩溃子进程须选择 TestPlatform suite 与具体 It，保留退出码 97 的真实断言 |

最终 Python 工具自测：`unittest discover -s scripts/acceptance -p '*test.py'`，254 项全部通过。`make check` 内先前的 253 项同样通过；新增所属 Issue 选择自测后又完整执行 254 项。

`make vet`、`make web-build`、`make check-generated`、`git diff --check` 均通过；IDL 与 `kitex_gen` 没有变更。上述完整质量检查在提交前工作树执行，以 `ddbd4b5d8378d41b6d9ba0254314177eeb433842` 为分支基线；不能将基线 SHA 当作本次测试的干净候选 testedCommit。

本地日志位于 `/tmp/monitor-mgr-ginkgo-check.log`、`/tmp/monitor-mgr-ginkgo-host-final.log`、`/tmp/monitor-mgr-ginkgo-generated.log`；临时日志与 `.scratch` 原生报告未提交，仅作为本机验证原件。

## 未执行

真实 Kubernetes 的 LOCAL_FULL 207 键、CI_SMOKE 17 键、真实平台 Pod 浏览器场景和 GitHub Actions 均未在本轮执行。现有用户集群未被用于本次验证。Host 合成调度、普通 Go 测试与构建结果不替代这些环境的通过证据。
