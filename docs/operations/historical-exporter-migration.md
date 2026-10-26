# 历史 exporter 显式启用与旧 ServiceMonitor 切换

升级平台或 Operator、补齐创建用户、确认管理权限和轮询都不启用历史采集。新明确发布只写带 `spec.serviceMonitor` 意图的父 KubeStateMetrics CR；ServiceMonitor 始终由 Operator 管理。没有旧平台 SM 的历史单 CR 无需人为制造旧对象，直接新预览、明确发布即可。

## 切换顺序

1. 保存原数据库一致快照、平台镜像来源及正在运行的 Operator chart/CRD/RBAC/镜像 digest。使用已验证的 `0.2.0-rc.4`；不能仅凭 SM CRD 存在认定能力已升级。
2. 在实例“变更历史 → 盘点历史采集迁移”读取并保存快照。包含 schema/当前发布版本、原任务/资源 key/发送边界、原冻结 path/文档指纹、父与 SM 原 UID、当前 UID/RV/owner/spec、所有实际 Agent 的对象/namespace 选择范围。成功旧 SM、不同名称旧 SM 均纳入。权限或分页读取失败时不能把影响当作零。
3. 明确采集空窗及需要核验的 Agent。先停止旧平台 SM 写入；新版旧多 CR 任务只能只读核验，retry/continue/cancel 不续写。确认没有另一旧平台进程继续维护它们。
4. 集群管理员使用独立集群凭据在平台外核对旧对象的实际 UID/RV、owner、spec 和 Agent 选择影响，显式处理旧 SM。删除请求应带已核验的 UID/RV 前置条件；确认同一路径 GET 返回404。替代 UID、外部对象或身份未知时应先核清影响，不根据名称、标签或相同 spec 自动认领/删除。平台没有通用 SM 删除或修复 API。
5. 刷新盘点，由当前有效平台管理员勾选已保存快照及显式外部处理声明，执行“确认旧 ServiceMonitor 已外部处理”。原 UID 未确认时明确传空并保留未知；确认只追加独立记录，旧 operation/attempt/state/possiblySent/confirmedUid/frozen bytes 不变。每次新的采集启用仍 GET 原路径，重现、替代对象、Forbidden/超时均阻断。
6. 若旧多 CR 操作仍占用实例互斥，单独执行“独立核验父 CR 并解除原任务互斥”。父原 UID 已确认时，核原 frozen path、原 UID 及完整 frozen spec；当前已知 UID 或较新版本不能被覆盖。部分成功时仅可从持久已确认父 UID CAS 恢复仍空实例 UID，不提升旧操作或 currentVersion。父原 UID 未确认且对象仍在时拒绝；必须先由管理员独立外部处理父资源并证明原 path404，才可单独明确确认进入全新创建。SM404不证明父身份。
7. 保存采集开关、参数，读取新预览并明确发布。保留原已确认父 UID，以当前 UID/RV 条件更新；未知父已显式清理的路线形成新操作的首次创建。新版本仅冻结父 CR，旧版本资源集合不变。
8. 核对 Operator status 的 reference UID 与实际 SM controller owner UID、观察 generation 和原父 UID；确认 Agent 实际进程已采用新 jobs、活动 target 集合精确无旧配置/重复目标，并在独立 receiver 查询本例 exporter 的真实 UID/endpoint/Pod 样本与原采样时间。Ready、selector 匹配和 SM 数量不代替数据面证据。
9. 归档快照、独立确认人/时间、旧/新版本及实际采集/清理收据。记录从旧对象清理到新目标采用的空窗，不宣称无损迁移。失败保留保护和原历史，不回填未知、不自动接管、不盲重放旧请求。

## 类型化平台入口

- `GET /api/v1/instances/:id/historical-migration` / `GetHistoricalMigration`：当前集群访问权限下的只读 Kubernetes 盘点，保存独立时间快照，返回 `snapshotId`。导出 JSON 包含原冻结 YAML 与安全投影后的实时 UID/RV/owner/spec；不含集群凭据。
- `POST /api/v1/instances/:id/historical-servicemonitor-resolutions` / `ResolveHistoricalServiceMonitor`：只确认原旧 SM 已外部处理。
- `POST /api/v1/instances/:id/historical-operation-releases` / `ReleaseHistoricalOperation`：只确认原父身份/外部不存在事实，并解除精确旧 operation 互斥。

两个 POST 均要求当前有效管理员、ACTIVE 纳管 KSM、幂等键及完整字段：`snapshotId,operationId,resourceKey,objectPath,originalUid,documentFingerprint,confirmExternalHandling=true`。`originalUid` 必填，未知明确为空；不能使用 `currentUid` 代替。服务端以 instance/operation/key/path/原 frozen bytes SHA256 重新关联，并重新 GET 原路径；客户端不能指定任意 Kubernetes 地址。原确认不改变历史创建角色或创建用户。

数据库 v16 新增独立快照、SM resolution 和父互斥释放表。升级前保留一致快照，DDL 失败回滚版本和原关系；恢复必须使用原密钥及原数据库，不能换空库掩盖旧任务。
