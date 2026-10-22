# 本地 VictoriaMetrics Cluster

用于同一集群中 Prometheus Agent 的无认证 remote write。依赖官方 `victoria-metrics-cluster` chart `0.51.0`，组件版本为 `v1.152.0`，镜像使用项目测试基线中的固定 digest。

## 部署

在仓库根目录执行，并显式指定目标集群：

```sh
make vm-chart-deps vm-chart-check
make deploy-vmcluster \
  KUBE_CONFIG=/absolute/path/monitor-data-a.kubeconfig \
  KUBE_CONTEXT=kind-monitor-data-a
```

默认 Helm release 为 `vmcluster`，namespace 为 `monitoring`。`vminsert`、`vmselect`、`vmstorage` 各运行一个副本；Service 名称固定，同一 namespace 安装一套。

## Agent 配置

在 monitor-mgr 的 Agent remote write 配置中填写以下 URL，认证方式选择「无认证」：

```text
http://vminsert.monitoring.svc.cluster.local:8480/insert/0/prometheus/api/v1/write
```

`0` 是 VictoriaMetrics 租户编号；此地址使用集群内 DNS。`vmauth` 关闭，HTTP 入口使用 ClusterIP。

查询 API：

```text
http://vmselect.monitoring.svc.cluster.local:8481/select/0/prometheus/api/v1/query
```

## 数据与资源

`vmstorage` 使用默认本地 StorageClass `standard` 的 5Gi RWO PVC，保留 7 天数据；Pod 重建使用原卷。`vmselect` 的查询缓存使用临时目录。

三个组件合计请求 512Mi 内存、200m CPU，内存上限合计 1Gi，适用于本地联调。单副本不提供节点级容灾；kind 集群删除时节点内数据也会丢失。
