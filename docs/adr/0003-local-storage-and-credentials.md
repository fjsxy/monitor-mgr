# 一期使用单实例 SQLite，并独立保存集群凭据加密密钥

一期采用 SQLite WAL 持久化账号、集群归属、配置草稿、发布版本、操作记录和最近一次运行状态，由一个管控服务统一访问数据库。部署位置已由 [ADR0014](0014-control-cluster-platform-deployment.md) 修订：数据库目录与独立密钥文件持久化至平台 Pod 的卷，保留 WAL 所需目录及锁/同步语义，以保留 Pod 重建后的管理状态；平台管理数据卷与 Agent WAL PVC 分别管理。

上传的 kubeconfig 仅接受所选 context 使用内嵌 CA、内嵌客户端证书与私钥或 token 的 HTTPS 配置，并保持 TLS 校验；集群凭据在数据库中使用 AES-GCM 加密，随机主密钥独立存入权限为 0600、owner 与平台运行 UID 匹配的持久文件，已有密文时缺失密钥必须明确失败，以避免仅获取数据库副本即可读取凭据以及静默生成新密钥导致数据无法解密。

凭据由平台管理员明确更新，验证成功后切换、失败保留旧凭据；remote_write 认证仅引用 PrometheusAgent 所在 namespace 中已有的 Secret，一期不承担 Secret 管理，以保持凭据来源及管理职责清晰。
