# monitor-mgr 部署在管控集群，Operator 可随目标集群部署

用户于 2026-10-18 明确「管控组件」仅指 monitor-mgr 平台。平台部署在管控集群（主集群）；管控集群与数据集群（隔离集群）都允许部署观测实例。验收采用各集群本地 Operator 管理本集群 CR 与工作负载，避免把平台集中部署误解为必须集中 Operator 或依赖跨集群调谐。

本决策替换 ADR0004 的宿主机运行前提，并修订 ADR0003 的数据存放位置；单 Go 进程、真实 HTTP→Kitex、单数据库责任方及已确认授权/任务状态机保留。平台采用单 Pod/单写实例，HTTP 监听 Pod 可达地址并通过 Service 与本机 port-forward 访问，Kitex 监听 Pod loopback；SQLite WAL 目录与独立 0600 密钥文件持久化到符合 WAL/锁语义的卷，Pod 重建不得丢失数据库、密钥、Session 或任务记录。

注册管控集群与数据集群均使用从平台 Pod 可达且 TLS 正确的明确 kubeconfig context，不以平台 Pod 身份跳过注册和授权。完整拓扑、版本和验收矩阵见 [确定一期端到端验收与测试集群矩阵](https://github.com/fjsxy/monitor-mgr/issues/11#issuecomment-6245825277)；本文件记录决策，尚未构建镜像、部署或执行真实集群验收。
