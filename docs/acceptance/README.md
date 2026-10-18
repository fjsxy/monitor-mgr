# 一期验收入口

固定目录为 `phase1.json`：`LOCAL_FULL` 207 键、`CI_FAST` 58 键、`CI_SMOKE` 17 键，共 282 个 profile 执行键。`--supplemental` 单独选择 `LOCAL_FULL` 的 104 个补充层键，不替代固定用例。完整步骤与期望见 `../e2e/issue-21.md`。

正式运行前，由独立集成得到唯一干净候选 C。checkout、runner、平台及应用屏障镜像均使用 C；修改任何代码后重新冻结候选并执行该候选完整门禁。原组件收据和 `--focus` 诊断不能包装成最终 C 通过。

## 执行与证据

重复操作使用 `make acceptance-list/run/validate/export/recover`，参数由 `E2E_ARGS` 显式传入。Python 使用锁定运行时及 PyYAML，Linux 安装入口为 `acceptance-bootstrap`，工具与镜像来源见 `scripts/acceptance/source-lock.json`。

```sh
make acceptance-list E2E_ARGS='--profile LOCAL_FULL'
make acceptance-list E2E_ARGS='--profile LOCAL_FULL --supplemental'
make acceptance-run E2E_ARGS='--profile LOCAL_FULL --candidate <C> --run-id <本次执行ID> --environment-json <私有环境请求绝对路径> --output <全新外置目录> --fail-fast'
make acceptance-validate E2E_ARGS='--index <外置目录>/index.json --profile LOCAL_FULL --candidate <C>'
make acceptance-export E2E_ARGS='--index <外置目录>/index.json --profile LOCAL_FULL --candidate <C> --destination <全新安全目录>'
```

环境请求为 0600 JSON，目录为 0700，包含 `profile/runId/candidate/source/platform/privateRoot/apiLane/kubectl/kind/targets`。新建环境设 `lifecycle=prepare`，另给固定且 clean 的 `monitorOpSource`；FULL 的 `targets` 明确列出 control/data 的 `name/kubeconfig/context`，SMOKE 仅 control，FAST 为空。所有 kubeconfig 路径必须在本次私有目录内，context 必须为 `kind-<name>`。FULL 使用 1cp+2worker control 与 1 可调度 cp data，SMOKE 使用 Linux amd64 的单 control 三节点。

`run.py` 是唯一准备和业务执行入口。Session 构造时先写完整 pending index，再等整批 `flock`，并持锁至全部 child、finally、独立恢复及收据完成；调用方不能再外包同一把锁。`prepare.py` 不提供独立集群写入口。FULL 通过后，独立 REG run 可使用其同 C 的 ready descriptor；`executionRunId` 属于本次执行，`environmentOwnerRunId` 保留最初 prepare owner，不能改写环境标签来消除差异。REG 命令另加 `--supplemental --parent-index <同C完整FULL index>`，先独立验证父收据，再派生已有 PATTERN/i64 浏览器子步骤，不重复计一次浏览器执行。

每例原件、wrapper、实际测试事件和 OCI JSON 按 hash 绑定。验证器读取原 `candidate.commit` 对象，离线核 C→tree，再检查原收据来源、完整键、未 skip、实际 Pod/Service 身份及清理结果。八项 REG21 Kubernetes 协议用例核真实 API/UID/信号/恢复原件；其中合法尚无平台 Pod 的 setup-failure 不借其他 Pod 补身份。

## 失败与恢复

TERM/INT 停止新动作并执行一次 finally，重复信号不重入。KILL/timeout 原记录保持失败或无 finally；新的恢复收据不能把它改绿。恢复只消费原 UID、实际进程出生身份及私有故障基线，不按名字或前缀认领，不把 403 当 404，不强制回收 namespace 或泛清 finalizer。

```sh
make acceptance-export E2E_ARGS='--index <外置目录>/index.json --profile LOCAL_FULL --candidate <C> --destination <全新诊断目录> --diagnostic'
make acceptance-recover E2E_ARGS='--output <原外置目录> --environment-json <原私有descriptor> --candidate <C> --recovery-output <全新独立恢复目录>'
```

诊断导出始终 `productGate=false`，保留 failed/not_run/cleanup 结果及允许的闭合引用。独立恢复不修改原 index、ledger 或原件；创建 UID 缺失、替代 UID、读取失败、持久化失败均保留失败边界。环境清理另加 `--environment-only`，仅在安全证据已持久化并从新路径离线核验后，顺序清理原 owner 的目标集群。

原 stdout/stderr、私有 Operator checkout、镜像 tar/层、数据库、kubeconfig、认证及 session 材料留在私有目录，不进入公开 console、cache 或 artifact。公开目录仅来自严格 exporter；远端 artifact 下载后再核原 digest 与离线完整性，不能上传整个 run 或 workspace。
