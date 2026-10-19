# #21 环境准备与来源

本文件说明版本化准备协议，不是执行或通过收据。固定用例、层与 profile 仍以 `phase1.json` 和 `issue-21.md` 为准。

## 入口与唯一执行者

主入口 `scripts/acceptance/run.py run` 在同一个 `BatchSession` 取得整批 `apiLane` 后，遇到私有 environment JSON 的 `lifecycle=prepare`，调用 `prepare.prepare_environment(request, execute=..., ledger=...)`。准备、真实 case 和 finally 属于同一个 child；本模块不建锁、Session 或独立 subprocess。`execute` 必须先捕获原始输出；`ledger` 必须同步持久化，写入失败时不发出变更。

`prepare.py` 不提供直接变更环境的 CLI。公开接口为 `prepare_environment`、`verify_environment`、`cleanup_environment`。执行器接收 `argv` 和 `label/input/cwd/env/timeout`，返回原始 `subprocess.CompletedProcess`。ledger 先写 `prepare_intent`，后写 `prepare_observed`、`prepare_cluster_observed`、`prepare_cluster_uid`；每个 target 完成后写完整 `prepare_target_checkpoint`，成功写 `prepare_ready`。环境全局基线不属于某个业务 case 的 finally，不应提前删除 CRD/controller。

创建失败、中断及其他异常附带 `error.descriptor`，同时尝试写 `prepare_failed`，保留此前已观察的容器 ID、cluster UID 和对象 UID。缺少身份就报告未知；不能靠前缀清理、force 或重试创建覆盖旧资源。

## 私有 request

`LOCAL_FULL` 必须含 `targets.control` 和 `targets.data`。`CI_SMOKE` 只含 control，平台及真实 Host 均须 Linux amd64；`CI_FAST` 的 targets 为空，也不需要私有源码。下面是 FULL 示例，实际路径和名称由 root 在执行前明确传入：

```json
{
  "schemaVersion": 1,
  "profile": "LOCAL_FULL",
  "runId": "mm-phase1-20261019-final",
  "candidate": "完整干净候选C的40位SHA",
  "source": "与candidate相同",
  "platform": "linux/arm64",
  "lifecycle": "prepare",
  "privateRoot": "/absolute/private/final-environment",
  "apiLane": "/absolute/private/final-environment/api-lane.lock",
  "kubectl": "/absolute/tooling/kubectl",
  "kind": "/absolute/tooling/kind",
  "monitorOpSource": "/absolute/private/fixed-monitor-op-git",
  "targets": {
    "control": {"name": "mm-phase1-final-control-1019", "context": "kind-mm-phase1-final-control-1019", "kubeconfig": "/absolute/private/final-environment/control.kubeconfig"},
    "data": {"name": "mm-phase1-final-data-1019", "context": "kind-mm-phase1-final-data-1019", "kubeconfig": "/absolute/private/final-environment/data.kubeconfig"}
  }
}
```

`candidate/source` 均须实际完整 SHA；示例占位符不能运行。`privateRoot` 和 kubeconfig 必须在仓库外，context 必须与显式 kind name 匹配。已有同名集群或 kubeconfig 直接拒绝，不 reset/clean 用户 checkout，不删除默认 kubeconfig 或其他集群。新的独立 kubeconfig 不依赖默认 context。

默认从 C 构建平台、RPC/lifecycle/volume fixture，以及固定 monitor-op。可用 `image`、`fixtureImages.rpc/lifecycle/environment`、`monitorOpImage` 指定本 run 唯一 tag；`buildImages=false` 仅用于由调用者预先构建同源镜像，仍实际核查 revision、平台及各 node CRI config digest。fixture 标签继承同源平台，不能用旧平台镜像。每张本构建镜像从 Docker-save 原 `manifest.json` 和实际 Config 字节实算 config digest，存在 OCI Descriptor 时再验证原 root/manifest→config 内容链；`docker inspect.Id` 仅是观测字段，不能当 config digest。只保留 hash 绑定的原元数据，含 layer 的临时 tar 随即删除。

## 拓扑与实际门禁

control 是 1 control-plane + 2 worker；FULL data 是 1 control-plane，显式去除其调度 taint 并回读。kind 0.33.0 与 Kubernetes 1.28.15、`StatefulSetAutoDeletePVC=true` 固定。每个新集群重新部署两个固定 Operator、全局 CRD/RBAC、默认 provisioner，并回读节点 Ready/version/architecture、CRD Established、controller Pod UID/实际 imageID 和唯一 default StorageClass。Pod 访问的 kubeconfig 使用已观察 kind 网络 IP，保留 TLS 校验，不依赖宿主 loopback 或 Docker 名称解析。

镜像固定值集中在 `scripts/acceptance/source-lock.json`，使用原 OCI index digest；Linux amd64 再核固定 config digest。每个 node 的 `ctr images pull` 保留原官方 index，随后实际读取 index、该架构 child manifest、config 三段原字节并逐段 SHA256 核验，保留完整官方 version tag；`crictl inspecti` 还须绑定 config、tag 与 runtime repoDigests。每个 node 另启动原 reloader index 的 `--version` Pod，每个 target 启动真实 Operator 调谐的 Agent、init/config-reloader，逐容器核原 index/manifest/config 与 `crictl inspect` 的实际 config。正常原 UID 删除探针并回读 NotFound 后才允许准备完成；Operator Ready 或 image metadata 不能替代这条运行路径。controller Pod imageID 也必须落在对应 node 的已核集合。monitor-op 固定 `2b2a8b79a52070fb89a08f5afae2b7698901c999`，只接受该 git checkout 且 clean；其 Dockerfile/CRD 另核原字节 SHA，运行时仅在私有临时路径把两个 FROM 替换为固定基础 image digest。私有源码、镜像 tar 和 layer 不进入公开 artifact/cache。旧业务卷/凭据 fixture 继续用独立的 `python:3.13.2-alpine`，与 Host CPython3.14.7 是两个角色；原官方 Python image index 及 amd64/arm64 child/config 另锁，每个 node 载入并保留完整官方 tag，不升级业务 fixture。

每个 target 通过 PVC Job 以 UID10001 实际验证 SQLite WAL、synchronous=FULL、竞争写锁、0600 key 和关闭后重开。探针镜像只追加 `volumeprobe`，基于同源产品镜像；Host 的 Go test 不替代这项真实 PVC 运行。预检 Job/PVC 属于环境基线，保留到整体环境清理。

## 恢复与清理

先保存原结果、严格白名单导出并上传安全证据，再由独立 `recover --environment-only` child 取得同一 lane。`cleanup_environment` 逐个回读原容器 ID 和 `kube-system` Namespace UID，只在全部匹配时顺序 `kind delete cluster --name ... --kubeconfig ...`。读取失败、未知 UID、同名替代身份均拒绝删除。清理后再次实际回读容器为空；不将读取错误当不存在，不做 Docker prune，不更改旧 run/index。

原 run 失败/中断保持原状态，环境恢复写独立安全收据。SIGKILL/runner 丢失可能令 finally 无法运行；没有独立恢复实证就不能声称清理完成。
