# monitor-mgr Helm 部署

此 chart 要求 Kubernetes 1.28 或更高版本，本次本地部署使用 1.28.15。平台部署在管控集群，目标集群仍需在平台中明确注册。每个管控集群和数据集群分别安装自己的 monitor-op；需要 Agent 时还需安装 Prometheus Operator。

在仓库根目录执行，显式选择目标，镜像 tag 与本次构建保持一致：

```sh
make chart-check
make deploy-platform \
  KUBE_CONFIG=/absolute/path/control.kubeconfig \
  KUBE_CONTEXT=kind-monitor-control \
  IMAGE_TAG=local-<commit>
make platform-forward \
  KUBE_CONFIG=/absolute/path/control.kubeconfig \
  KUBE_CONTEXT=kind-monitor-control
```

访问 `http://127.0.0.1:18080`，空库首次初始化管理员。镜像需提前推送至目标可访问的仓库，或使用 `kind load docker-image` 加载至管控集群。自定义镜像仓库、存储与资源可通过 `helm upgrade --install` 的 values 覆盖。

`IMAGE_TAG` 用于默认的 `monitor-mgr:<tag>`，自定义仓库时可向 Makefile 传入 `IMAGE=registry.example/monitor-mgr:<tag>`。chart 的 `image` 接收完整镜像字符串，`imagePullPolicy`、`source` 和 `runId` 沿用 E2E 的参数；`storage` 与 `resources` 保留本地部署的覆盖入口。

关键约束：

- 固定一个 Pod，升级使用 `Recreate`；Kitex 只监听 Pod loopback，Service 只公开 HTTP。
- 数据目录、SQLite WAL 和独立 `master.key` 保存在同一 PVC。StorageClass 必须实际支持 SQLite WAL、文件锁和 fsGroup；PVC Bound 本身不证明这些能力。
- Pod 使用 UID/GID 10001，关闭 ServiceAccount token 自动挂载；平台不凭自身 Pod 身份绕过集群注册。
- PVC 带 `helm.sh/resource-policy: keep`，卸载 release 保留数据。重新使用或删除旧 PVC 前，应确认数据库与原密钥一并保留。
- 本地单节点 kind 的持久卷可抵抗 Pod 重建；删除 kind 集群会销毁节点内数据，不提供节点级容灾。
