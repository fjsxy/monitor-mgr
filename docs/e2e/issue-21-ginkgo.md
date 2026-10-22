# Issue #21：一期 Ginkgo 迁移测试计划

本次为一期验收的后续测试框架迁移。沿用 `docs/acceptance/phase1.json` 与 `scripts/acceptance/registry.py` 中的稳定用例 ID、必选输入、profile 和证据边界；CI_FAST 固定 58 个 Q 键，CI_SMOKE 固定 17 个键，LOCAL_FULL 固定 207 个键。迁移不修改产品契约。

用户确认的迁移范围：Go UT 使用原生 Ginkgo；产品 K/B E2E 由 Ginkgo 逐例调度，保留 Python/Playwright 业务场景及断言；Python 验收工具自测继续使用 unittest。原连续子例改为 By 步骤，保持撤权、禁用等依赖状态的操作顺序。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| GINKGO-UT-001 | 全量 Go 测试迁移且不遗漏原断言 | 对照迁移前测试清单，执行全部 Ginkgo suite，核对原顶层场景和参数子例；检查除 suite 入口外不存在传统 Test 函数 | 原场景与必选子例均可执行；失败、跳过、清理行为由 Ginkgo 报告，断言保持原强度 | #21 |
| GINKGO-UT-002 | 覆盖率与并发安全满足约束 | 执行 Makefile 中覆盖率门禁及 race；覆盖率统计手写产品 Go 包，排除生成代码与测试工具 | 产品语句总覆盖率至少 70%；race 无报告；未覆盖范围明确记录 | #21 |
| GINKGO-Q-001 | 固定 Q 键绑定真实 Ginkgo 结果 | 逐项列举 CI_FAST 的 58 个 Q 键和 selector，执行对应 specs 并独立验证原报告 | 每个 selector 均有真实通过事件；缺失、失败、跳过、篡改或空选择均不能产生 passing 收据 | #21 |
| GINKGO-E2E-001 | E2E 框架迁移保持环境与证据真实性 | 对照 207 个 LOCAL_FULL 与 17 个 CI_SMOKE 键，检查 Q/K/B 的场景归属、调用与实际结果解析；执行可用环境的回归 | 用例 ID、源码身份、UID 条件恢复、独占 lane、敏感数据私有保存、清理均保持；环境不足记未执行 | #21 |
| GINKGO-CI-001 | CI 用例文档可自动核对 | 从 workflow、注册表、Ginkgo suite 和工具测试清单生成本地 Markdown；执行文档一致性检查 | 文档逐例说明执行入口、目的、选取标准；新增删除或 profile 变化必须同步，陈旧文档导致检查失败 | #21 |
| GINKGO-SELECT-001 | 聚焦运行仍能精确选择子例 | 使用 Makefile 聚焦入口选择一个场景，并按稳定子例选择所需根场景；核对 Ginkgo 报告后独立校验收据 | 只执行目标根场景；根内必选输入均有独立完成标识；稳定 selector 不因 Test 入口变为 suite 而失效；空集与未执行不通过 | #21 |

实际执行结果单独记录在 `docs/e2e/issue-21-ginkgo-results.md`；本计划不代表测试已经通过。
