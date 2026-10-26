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


二次 Spec 复核及 6f6 CI 反馈的追加修复，基线为 `626f0f0f9c9bf935bd016d233e13deda9f1c5c34`。最终五列计划：<https://github.com/fjsxy/monitor-mgr/issues/23#issuecomment-6418392363>，GitHub 正文与本地逐字一致。下列测试执行在未提交的追加工作树；原报告和日志保留真实来源，不将其伪造绑定到最终提交 SHA。

| 输入 | 实际结果 | 证据/限制 |
|---|---|---|
| I23-R20 | 预算静态核查通过 | 根据6f6 CI全18项passed/platform约1083s/完整命令19分30，将Go包预算20m调整25m；CI job30m不变；旧exit2原因仍未知 |
| I23-R21.manager / UT-08 | 局部 UT 通过 | review-sa-mount-red.log → review-sa-mount-green.json；最终review-followup-targeted.json UT-08 passed/attempt1 |
| I23-R21.template / UT-08 | 局部 UT 通过 | review-sa-mount-red.log → review-sa-mount-green.json；最终review-followup-targeted.json UT-08 passed/attempt1 |
| I23-R21.source-hostpath / UT-08 | 局部 UT 通过 | review-sa-mount-red.log → review-sa-mount-green.json；最终review-followup-targeted.json UT-08 passed/attempt1 |
| I23-R21.source-configmap / UT-08 | 局部 UT 通过 | review-sa-mount-red.log → review-sa-mount-green.json；最终review-followup-targeted.json UT-08 passed/attempt1 |
| I23-R21.token-path / UT-08 | 局部 UT 通过 | review-sa-mount-red.log → review-sa-mount-green.json；最终review-followup-targeted.json UT-08 passed/attempt1 |
| I23-R21.token-audience / UT-08 | 局部 UT 通过 | review-sa-mount-red.log → review-sa-mount-green.json；最终review-followup-targeted.json UT-08 passed/attempt1 |
| I23-R21.subpath / UT-08 | 局部 UT 通过 | review-sa-mount-red.log → review-sa-mount-green.json；最终review-followup-targeted.json UT-08 passed/attempt1 |
| I23-R21.lifecycle / UT-08 | 局部 UT 通过 | review-sa-mount-red.log → review-sa-mount-green.json；最终review-followup-targeted.json UT-08 passed/attempt1 |
| I23-R21.sidecar / UT-08 | 局部 UT 通过 | review-sa-mount-red.log → review-sa-mount-green.json；最终review-followup-targeted.json UT-08 passed/attempt1 |
| I23-R21.writable-root / UT-08 | 局部 UT 通过 | review-sa-mount-red.log → review-sa-mount-green.json；最终review-followup-targeted.json UT-08 passed/attempt1 |
| I23-R21.normal / UT-08 | 局部 UT 通过 | review-sa-mount-red.log → review-sa-mount-green.json；最终review-followup-targeted.json UT-08 passed/attempt1 |
| I23-R22.leftover-Service / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.denied-Service / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.leftover-ServiceAccount / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.denied-ServiceAccount / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.leftover-Role / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.denied-Role / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.leftover-RoleBinding / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.denied-RoleBinding / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.leftover-ClusterRole / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.denied-ClusterRole / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.leftover-ClusterRoleBinding / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.denied-ClusterRoleBinding / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.leftover-Endpoints / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.denied-Endpoints / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.leftover-EndpointSlice / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.denied-EndpointSlice / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.leftover-ServiceMonitor / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.denied-ServiceMonitor / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.shape / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.page-denied / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.page-repeat / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.replacement / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.empty / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.api-absent / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R23.namespace-permission / E2E-02 | 静态检查通过；真实 E2E 未执行 | 6f6 CI权限失败为原始red；仅补Operator和完整图只读权限，原namespace workload写负例保持；父最终真实E2E-02核验 |
| I23-R24.unknown-window / E2E-08 | 静态检查通过；真实 E2E 未执行 | 6f6 CI全历史POST计数污染为原始red；改用子例事件窗口与预存intentId，恢复/未知保护断言保留；父最终E2E-08核验 |
| I23-R25.collector-name / UT-18、E2E-08 | 工具 UT 通过；真实 E2E 未执行 | review-ci-fixtures-red.log → review-ci-fixtures-green.json；最终review-browser-diagnostic-green.json UT-18包含实际CI长run-id、最大39字符run-id和不同长前缀 |
| I23-R22.seen-child / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.optional-page / UT-11 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R22.active-rebuild / UT-14 | 局部 UT 通过 | review-complete-graph-red.log → review-followup-targeted.json UT-11/14 passed/attempt1；active-rebuild另有review-active-graph-red.log最小复现 |
| I23-R26.browser-diagnostic / UT-18、E2E-10 | 工具 UT 通过；真实 E2E 未执行 | review-browser-diagnostic-red.log → review-browser-diagnostic-green.json UT-18 passed/attempt1；固定step/源码/枚举正控及私有标记/未知路径负例；浏览器原断言与其预算未改 |

运行边界：新增 `deletion_graph.go` 只共享 RC4 正式受管类型、作用域、完整分页和原 UID 闭包，不复制 Service 命名或端口算法。活跃原父可识别 Operator 合法重建子 UID；历史删除要求完整图为空，未知读取、外部替代和任一原后代残留均拒绝。SM 仍只有读取。

`review-followup-checks.log` 包含18 UT/10 E2E清单一致、Python语法、Go E2E断言编译与唯一Playwright用例发现；`review-followup-vet.log` 通过。没有在 fixer 树运行完整18项/race/覆盖率或真实10case，最终由集成任务绑定最终merge SHA执行。6f6云端UT/storage已绿属于旧候选，不挪作本次修复后证据。


118a 实测后的夹具/诊断追加阶段（本阶段基线 `ca5cd9a373fce75e6b6671f6e94156d14f863b28`）。最新补充计划已发布并逐字回读：<https://github.com/fjsxy/monitor-mgr/issues/23#issuecomment-6419257061>。生产业务代码、UID/RV保护、旧报告来源过滤和25m预算保持；下列本地UT执行在未提交工作树，不伪造其testedCommit。

| 输入 | 实际结果 | 证据与限制 |
|---|---|---|
| I23-R27.rv-conflict | 真实server dry-run及工具UT通过 | `conditional-merge-probe/receipt.json`：错误RV为Conflict；`review-conditional-final.json`：首次Conflict后使用新读RV且原UID不变 |
| I23-R27.uid-mismatch | 真实server dry-run及工具UT通过 | 错误UID为Invalid；重读同名替代UID立即停止，零后续PATCH，`review-conditional-final.json` |
| I23-R27.unchanged | 真实server dry-run及工具UT通过 | 正确UID/RV无变化dry-run成功，前后namespace UID/RV完全一致；工具保留其它finalizer与最后hold释放404路径 |
| I23-R27.unknown | 工具UT通过 | 泛化422/403/网络错误均单次即失败，连续409最多3次；固定operation/phase/reason/attempts摘要，无原文/路径/凭据 |
| I23-R27.legacy | 工具UT通过；新真实E2E未执行 | 三处历史fixture仅MergePatch `spec.serviceMonitor=null`，原UID/RV复核，完整其它spec保留 |
| I23-R27.rules | 工具UT通过；新真实E2E未执行 | exporter/Agent管理员RBAC故障注入同用条件MergePatch，仅替换既定rules并恢复完整原规则；平台SM写权限未增加 |
| I23-R28.frames | 工具UT通过；新真实浏览器未执行 | `review-browser-frames-red.log` → `.green.json`；最终 `review-conditional-final.json` 包含多个同候选白名单源码调用帧与外部私有路径拒绝 |
| I23-R28.http | 工具UT通过；新真实浏览器未执行 | 有限200/201/202/400/401/403/404/409/422/500/503枚举投影；未知数字/任意标记仍unknown |
| I23-R30.current-mtime | 可重复red→green | 固定start=1700000000.5、量化mtime=1700000000重现KeyError(nativeFailures)；fixture显式设report在start之后，恰好相等边界也验证接受；`review-report-mtime-*` |
| I23-R30.old-mtime | 工具UT通过 | 明确旧mtime被拒绝，生产`capture.py`的`mtime<started`来源条件完全未变；最终`review-conditional-final.json` |
| I23-R29.browser-order | 原118a真实red已确证；顺序修复静态通过，新真实浏览器未执行 | 118a原report首次transfer Expected200/Received409，send issue25.ts8→24→phase1.spec.ts66；先I29原显式迁移/独立release再I25，所有原断言和旧未知发送事实保持 |

协议依据：[Kubernetes v0.28.15 patch.go](https://github.com/kubernetes/apiserver/blob/v0.28.15/pkg/endpoints/handlers/patch.go#L364) 将JSONPatch应用失败转换为泛化422，kubectl不会保留可判别的RV test失败文字。因此不能把任意Invalid当RV冲突。严格metadata-only/server-dry-run实测确认MergePatch错误RV为Conflict、错误UID为Invalid、正确条件成功；仅明确Conflict可有界重读。118a云端失败未取得原始错误正文，不能据此声称每次原失败都已证实为RV冲突；修复后的真实验收仍由父执行。

最终本阶段 `review-conditional-final.json` UT-18 passed/attempt1，包含10项真实受控HTTP协议输入；`review-118a-checks.log` 完成固定18/10目录、Python语法、Go E2E编译及唯一Playwright用例发现。未触碰正在118a验收的资源，未创建集群或对象；唯一真实API诊断均为server dry-run，原始stdout/stderr只存私有目录。


R29最终补充计划已发布并逐字回读：<https://github.com/fjsxy/monitor-mgr/issues/23#issuecomment-6419386224>。118a本地真实完整E2E的08/09均passed，10原Playwright报告确证 `/clusters/{id}/transfer` Expected200/Received409。只将原I29 browser step移到I25之前，不修改产品guard、旧operation结果或直接外部清锁。原I29仍明确验证旧SM `IDENTITY_UNCERTAIN` 与空confirmedUID，原I25全部创建身份、管理权限及归属恢复断言保持。该小改仅做浏览器用例发现/编译等静态检查，完整真实green由父在最终merge SHA执行。

R29导航准备已同步计划并逐字回读：<https://github.com/fjsxy/monitor-mgr/issues/23#issuecomment-6419407241>。真实118a失败截图显示large实例DELETED详情仍打开，原I25的reload此前才隐式关闭。现上段删除业务断言完成后显式关闭该详情并确认退出，再执行I29；最终顺序为I29→I25→I30→I26末尾注销。仅准备导航起点，未删除任何业务断言。
