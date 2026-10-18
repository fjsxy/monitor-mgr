# KSM 配置与发布

平台通过 HTTP → 生成 Thrift/Kitex → control 应用层访问 SQLite 与目标 API。HTTP 层不读取数据库或直连 Kubernetes。KubeStateMetrics 是 cluster-scoped CR；`targetNamespace` 决定 Operator 工作负载所在 namespace，不是 CR metadata namespace。平台不会建立 namespace、RBAC、Secret 或 PVC 依赖。

## 数据与状态

`ObservationInstance.id` 是平台身份，`name` 是用户填写的 CR DNS subdomain（最长253），二者独立。草稿可离线保存，`expectedDraftRevision` 防止覆盖其他编辑；保存不触发目标写入。首次成功 Create 后 name/targetNamespace 固定。复制建立新草稿且名称为空。外部发现使用独立只读读模型，不建立草稿、版本或删除所有权。

KSM typed 配置包含 Deployment、StatefulSetSharded、DaemonSetSharded、运行意图、通用/角色 resources、28项 collectors、metric 与 namespace 过滤。高级角色资源整块覆盖通用资源；隐藏或切换模式仍保存输入，只把当前有效配置映射到 CR。CR mapper、默认值等价归一化、校验位于 `internal/control/ksm_config.go`；固定上游依据见 #16 E2E 计划与组件合同。

预览绑定已保存草稿修订、目标 UID 和规范化 spec。发布受理在一个事务内冻结版本来源、operation、attempt、幂等结果和实例保护，提交后才返回202。保存下一修订不会改变已接受版本。当前版本只在写入响应充分确认 UID/spec 后推进；CR 写入成功与组件可用分别表达。

执行先重新读取当前权限、集群 UID、依赖与预览基线。最后派发事务再次验证 actor/归属/ACTIVE/credentialRevision，再持久化 `possibly_sent=1`，随后仅发送一次写请求。Create 使用 POST，已有同名拒绝接管；更新使用原 UID、最新 RV 和当前 metadata/status/finalizers。未知响应、断连、5xx、成功后无法解码均保留不确定状态及保护，重启不重放。可靠401/403/404/409/422才视为明确拒绝。`ContinueOperation` 当前只接受凭据只读核验，不可用于重发发布。

运行信息来自真实 CR 当前 generation 的 status。缺少/陈旧/不完整 status 表示 Unknown；Suspended、Disabled、NoNamespaceMatched 必须有当前 generation 证据，不能凭0/0猜测。模式切换由 Operator 清理旧工作负载后创建新 UID，平台不直接删除工作负载，也不承诺无损。

## 后续扩展接缝

| 职责 | 入口与约束 |
| --- | --- |
| 组件配置 | `decodeKSM`、`KSMConfig.spec`、`normalizedSpec`、`ksmCR`；Agent 应增加 typed adapter，不能让 YAML/未知字段透传替代校验 |
| 预览/受理 | `PreviewPublication`、`PublishInstance`、`validatePublication`；复用当前授权、修订、UID/spec与事务保护，不能另建HTTP旁路 |
| 派发 | `launchPublications`、`executePublication`；组件扩展应在此抽取 CR path、mapper、依赖/观察接口，保留统一派发资格事务和单次写 |
| 运输结果 | `Target.Mutate` → `MutationOutcome`；可靠HTTP拒绝与可能已发送必须保持结构化，不能只按通用APIError.Code解锁 |
| 生命周期 | `finishPublication`、`execution_attempts`、`instance_protections`；#17恢复/重试/删除须沿用原operation/版本身份与保护，不凭同名认领 |
| 观察 | `refreshObserved`、`interpretKSM`、`instance_observations`；重启前缓存不冒充新观察，后台调度由后续任务扩展 |
| schema | 当前schema4；已有schema3密文先用原密钥核验再迁移/快照。错误密钥不得推进schema或替换库，已知schema1/2显式按旧结构处理 |

## 重复执行

`make e2e-ksm-q` 执行真实HTTP/Kitex/SQLite快速回归；`make ksm-q-receipt E2E_ARGS='--output /绝对路径 --profile LOCAL_FULL'` 输出逐Q子例收据。`make e2e-ksm ... --layer K` 和 `make e2e-ksm-b ...` 对独立平台Pod执行真实K/B。参数须经 `E2E_ARGS` 显式提供：

```sh
make image IMAGE=monitor-mgr:ksm-candidate PLATFORM=linux/arm64
make ksm-rpc-image IMAGE=monitor-mgr:ksm-candidate RPC_IMAGE=monitor-mgr:ksm-rpc-candidate
make e2e-ksm PYTHON=/path/to/venv/bin/python E2E_ARGS='--kubectl /path/to/kubectl-v1.28.15 --control-config /private/control.kubeconfig --data-config /private/data.kubeconfig --control-pod-config /private/control-pod.kubeconfig.json --data-pod-config /private/data-pod.kubeconfig.json --image monitor-mgr:ksm-candidate --rpc-image monitor-mgr:ksm-rpc-candidate --source ACTUAL_IMAGE_SOURCE_SHA --output /absolute/evidence/run --prefix mm-phase1-ksm-RUN_ID --workers 1'
```

构建镜像后须加载至本轮独占control kind，所有目标节点预载固定KSM镜像；环境依据以本轮 `environment/README-agents.md` 为准。`--cases K02.primary` 支持稳定ID定向执行；`--list` 列出51责任子例。C04是全局CRD故障，runner在其他并发fixture清理后独占执行并finally恢复。只清本run prefix+targetNamespace资源；完整列表读取失败会让cleanup失败，不能冒充无残留。

B层使用 Chromium，trace关闭，凭据只经进程环境/原0600文件传递，收据只保留安全页面截图。故障proxy只供测试，mTLS自检、Service与上游读取通过后才切换凭据；proxy的pause表示平台已经可能发送，不能当成产品未发送屏障。产品未派发边界由应用层专用测试hook验证。Q的子进程崩溃测试在提交前/后真正终止进程，重启后按命令键找回事务结果。

每份收据绑定 `testedCommit`、runnerCommit、实际镜像manifest/configID、Linux/arm64目标节点与profile。Host Q、Host B、真实Pod B、K及CI不能互相替代；旧来源证据不改写为新来源。固定 `docs/acceptance/phase1.json` 仍为计划目录，运行结果独立存储。

K/B门禁须独占目标namespace写入lane。固定monitor-op对All也将当前完整namespace集合写入`--namespaces`（planner.go:220），其他fixture建删namespace会触发工作负载重新滚动，不能把这种并发扰动误判为资源配置失败。runner默认串行；`--workers >1`仅显式`--parallel-diagnostic`诊断使用。B的全局CR inventory不变检查也应在K清理完成后执行。
