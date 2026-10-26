# Issue #23 review 修复实际结果

五列计划已完整发布并逐字回读：<https://github.com/fjsxy/monitor-mgr/issues/23#issuecomment-6417472195>。本地计划正文与该评论一致。该表是计划；下表才是本次执行状态。

实现位于独立 `codex/spec23-review-fixes`，起点 `6b8f166522f5e048df87c5e91e73f635e9e7d086`。未修改 integration 或原 dirty main，未 push / 创建 PR / 合并 integration。

- Standards：移除 generation 可见文字；补齐点名的中文职责及关键边界注释；source/fetch/verify 参数化 Make target 供 CI/Environment 复用；冻结身份复用 `resourceInstance`。
- Spec：RC4 运行能力绑定已批准 OCI index/双平台 manifest 与实际 Deployment→ReplicaSet→Pod 原 UID/Ready/containerID/imageID；原 SA admission volumeMount 不影响进程判断。只读权限复用既有 SA，SM 写权限始终为零。CR 已写入、Operator 配套收敛、真实采集继续分别表达。
- 旧删除显式收尾绑定原 UID、冻结快照、每个原路径404及完整 workload/Pod UID 图，不重写旧执行状态或未知事实。集群互斥只排除精确原 operation 的独立处理事实，其他未知任务仍阻止转移与注销。
- 实测夹具修复：父停用前重新启用真实 SM；父当前 Disabled 与 SM 旧 generation 分开保存并另核原图清空；授权子例先于未知任务；finalizer 只对原 UID/RV 明确冲突有界重读，最后 hold 释放404可退休责任；业务断言完成后，原 CR Foreground 发出到 wait 之间仅加速事先固定的本 run 原 Pod UID；真实 v10 浏览器先断言关闭再明确勾选。
- 诊断：固定阶段、固定错误码、真实 exit；任意 label 降为 unknown，原日志不上传。UT 输出采用有界首尾解析，并读取同一次捕获开始后生成的原生报告，只公开固定 UT-ID/状态/冻结源码位置；不公开 Failure.Message/原路径/原文。

证据目录：`/tmp/monitor-mgr-spec23-20261026/`；最终快照原生报告复制到 `review-delivery-evidence/`。完整 UT/race/≥70% 覆盖率、最终真实 kind / Chromium / Agent 样本 / 全部清理由集成任务对最终合入 head 重跑；本表不将旧 6b8 的真实结果移记为本修复通过。

| 补充计划输入 | 实际状态 | 执行证据与限制 |
| --- | --- | --- |
| I23-R01 / E2E-03 | 静态检查通过；真实 E2E 未执行 | `review-make-checks-final.log`；最终完整真实运行由集成任务执行 |
| I23-R02 / UT-18 | 参数/help/缺参检查及真实 verify 通过；source/fetch 的远端运行未执行 | `review-release-verify.log`；CI/Environment 已复用 Make targets |
| I23-R03 / UT-18 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R04 / UT-18 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R05 / UT-18 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R06 / UT-18 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R07 / UT-10 / UT-12 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R08 | 通过 | 中文职责注释审查、Go 格式/vet、Python 语法与前端构建 |
| I23-R09.none / UT-08 / UT-10 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R09.old / UT-08 / UT-10 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R09.tag / UT-08 / UT-10 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R09.digest / UT-08 / UT-10 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R09.ready / UT-08 / UT-10 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R09.read / UT-08 / UT-10 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R09.preview / UT-08 / UT-10 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R09.dispatch / UT-08 / UT-10 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R09.compatible / UT-08 / UT-10 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R09.disabled / UT-08 / UT-10 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R10 / UT-11 / UT-12 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R11.parent / UT-11 / UT-12 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R11.sm / UT-11 / UT-12 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R11.workload / UT-11 / UT-12 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R11.read / UT-11 / UT-12 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R11.snapshot / UT-11 / UT-12 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R11.uid / UT-11 / UT-12 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R11.unresolved / UT-11 / UT-12 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R11.ordinary / UT-11 / UT-12 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R12 / E2E-03 | 静态检查通过；真实 E2E 未执行 | `review-make-checks-final.log`；最终完整真实运行由集成任务执行 |
| I23-R13 / E2E-08 | 静态检查通过；真实 E2E 未执行 | `review-make-checks-final.log`；最终完整真实运行由集成任务执行 |
| I23-R14 / E2E-09 | 工具 HTTP 负例通过；真实 E2E 未执行 | UT-18 / `review-finalizer-green.json`，不替代真实 finalizer 验收 |
| I23-R15 / UT-18 | 通过；新云端运行未执行 | UT-18 / 原始日志尾部红绿及原生报告消息/路径/marker 负例 |
| I23-R16 / UT-18 | 通过；新云端运行未执行 | UT-18 / 原始日志尾部红绿及原生报告消息/路径/marker 负例 |
| I23-R17.publish / UT-11 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R17.delete / UT-11 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R17.other / UT-11 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |
| I23-R18 / E2E-04 | 静态检查通过；真实 E2E 未执行 | `review-make-checks-final.log`；最终完整真实运行由集成任务执行 |
| I23-R14.gone / UT-18 | 通过 | UT-18 / `review-finalizer-green.json`，真实受控 HTTP helper；不触真实集群 |
| I23-R14.conflict / UT-18 | 通过 | UT-18 / `review-finalizer-green.json`，真实受控 HTTP helper；不触真实集群 |
| I23-R14.replaced / UT-18 | 通过 | UT-18 / `review-finalizer-green.json`，真实受控 HTTP helper；不触真实集群 |
| I23-R19 / E2E-10 | 静态检查通过；真实 E2E 未执行 | `review-make-checks-final.log`；最终完整真实运行由集成任务执行 |
| I23-R09.env / UT-08 | 通过 | 最终局部 UT-08，实际公开预览拒绝运行环境覆盖 |
| I23-R09.command / UT-08 | 通过 | 最终局部 UT-08，实际公开预览拒绝入口覆盖 |
| I23-R20 | 静态预算检查通过；完整执行由集成任务完成 | Makefile go timeout=20m；CI timeout-minutes=30；不以旧exit2定性超时 |
| I23-R09.admission / UT-08 | 通过 | `review-fixes.json` 的 UT-08/10/11/12/18 原生逐输入 By；工具边界另见 `review-tooling-expanded.log` |

局部原生执行：UT-08/10/11/12/18 五项均 passed、NumAttempts=1，其他稳定用例是显式 focus 未选，不构成全量18项通过。最初 `review-focused.json` 中四项 race passed、UT-18 因新测试在 unittest 清空 traceback 后才观察源码帧而失败；已修正观察时点，单独 UT-18 race 复测及最终五项均通过，原失败报告保留。

真实旧 6b8 失败的修复依据来自集成任务保存的原始状态/安全来源帧；未将 exit=2 推断为超时，未重建 RC4 发布品。按集成任务明确决定，全量 Go UT 从15m增加到20m、CI job仍30m，原因是6b8本地全量716s、云端相同head冷启动总命令17m30及新增必选矩阵需要有界余量；这不是旧失败原因的定性，也未减少输入或断言。
