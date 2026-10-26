# Issue #23 review 修复执行记录

当前为阶段性实现记录，最终集成 testedCommit 与真实 E2E 结果由集成任务补充。

- 补充五列计划已发布并逐字回读：<https://github.com/fjsxy/monitor-mgr/issues/23#issuecomment-6416900648>；正文与 `docs/e2e/issue-23-review-fixes.md` 一致。
- 独立 worktree 基于 `6b8f166522f5e048df87c5e91e73f635e9e7d086`，integration 和原工作目录未修改。
- 运行能力最小复现先出现公开预览 200 的误放行，修复后 UT-08 通过。来源为 #28 已批准 RC4 release.json 的 OCI index/双平台 manifest；实际 Deployment→ReplicaSet→Pod 原 UID、Running/Ready、容器 ID 与 imageID 逐项核验，发布受理与写前重检。真实平台 SA 已有相应只读权限，SM 不增加写权限。
- 旧 DELETE 最小复现先出现 409“旧删除或非发布操作不能从此入口解除”，修复后 UT-11 通过。管理员明确确认原 UID/冻结快照，原父、各冻结对象及全部 workload/Pod 消失后独立留痕和释放互斥；旧状态、发送标记及未知事实保持原样。
- 局部 race 回归的 UT-08/10/11/12 均通过；UT-18 初次失败来自新测试在 unittest 清空 traceback 后才检查源码帧，已修正测试观察时点并单独复测。原始报告在 `/tmp/monitor-mgr-spec23-20261026/review-focused.json`，不能将其中初次 UT-18 失败抹成全绿。
- 工具边界 5 项、Python 语法、前端类型检查/构建、Make 参数与 help、CI 固定清单 18 UT / 10 E2E 均通过。新增 source/fetch/verify target 的真实远端消费仍由最终集成 E2E 执行。
- command-failure 仅公开固定阶段、错误码和有界整数 exit；未知 label 降为 unknown，原输出均为私有文件。大体积 UT 失败日志首尾解析已完成红绿验证；不会凭 exit=2 推断超时。

尚在处理：真实 E2E-03 的 RC4 停用状态采证、E2E-08 授权子场景顺序、E2E-09 原 UID/RV finalizer 竞态及清理404、Agent fixture 清理次序、历史处理完成后集群变更互斥、原生 UT 报告安全投影。此前 6b8 的真实运行结果属于旧候选，不作为本修复提交通过证据。
