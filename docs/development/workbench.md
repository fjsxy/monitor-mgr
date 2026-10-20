# 工作台查询与来源同步

`GET /api/v1/clusters/{id}/instances?page=1&pageSize=20` 经生成 `QueryInstances` RPC，在当前授权的完整集群集合中计算独立统计，再应用名称字面包含（忽略大小写）、类型、组件、摘要的 AND 筛选与分页。`%`/`_` 没有模式含义。无 query 的旧 List 接缝仍用于显式完整发现。明确的创建、保存、发布、重试、取消、删除在原事务中递增持久活动序号；查看、预览、观察与异步完成不改变顺序。v9 从完整 UTC 纳秒时间和确定性 ID 回填旧库，不以浮点时间排序。

现有 worker 生命周期每10秒安排后台观察，`-cr-poll-interval` 仅接受1至20整数秒。平台按实例/原UID/来源保存最后成功内容、每次尝试时间和错误；CR成功不能替Pod/PVC/关联续期。失败立即当前未知；仅距成功大于30秒才过期。重启 epoch 不匹配的历史保持未知，首次成功后才能恢复当前判断。完整分页失败保留该组件历史；KSM与Agent发现相互独立。

关键：网络前固定原实例/集群UID和凭据修订，提交来源时在事务中再比较。成功、读取失败及身份预读失败均受相同限制，旧修订不能覆盖新成功或重建后的对象。目标 GET 按当前凭据及完整路径合并在途读取，不缓存已结束结果；变更不合并、不重试。RPC采用 TTHeader 传递剩余预算，服务端始终将每次RPC限制为原deadline与10秒的较小值；不足1ms在客户端发起前耗尽。目标8秒预算包含全局8/单集群4的排队时间，HTTP总预算15秒。

HTTP安全整数范围保留 JSON number；更大 i64 输出十进制字符串，请求字符串精确解析。超出安全范围的 JSON number、非整数、越界值拒绝。RPC wire i64 保持原协议；前端修订类型为 number|string，不以 Number 转换。当前版本入口只在服务端确认已部署且身份匹配时提供；DELETED/Missing/冲突或外部只读对象无该入口，但平台不可变历史版本继续保留，读投影不改数据库版本指针。

重复入口：

- `make e2e-workbench-q`：应用层Q与补充回归，不替代K/B。
- `make workbench-q-receipt E2E_ARGS='--profile LOCAL_FULL --output /private/evidence'`：固定8Q逐ID收据；`--layer REG` 单列3补充回归，HTTP i64还需浏览器证据。
- `make fault-proxy-check`：实际loopback HTTP fixture机制自测，不能记为真实K/mTLS通过。
- `make e2e-workbench PYTHON=/private/venv/bin/python E2E_ARGS='--layer K --profile LOCAL_FULL ...'`：4K；改`--layer B`为3B。必须显式传`--kubectl`、`--control-config`、`--data-config`、`--control-context`、`--data-context`、两项`--*-pod-config`、`--image`、`--source`、`--api-lane`、`--output`、唯一`--prefix`。只有获分配的独占lane才能启动，不能提前排队。

K/B继承既有双目标本地环境验证（3+1节点、Linux arm64、v1.28.15），逐例新namespace/PVC/平台Pod。B经本例Service访问，fixture以0600 JSON传递，trace关闭；U05原版本依赖真实KSM/Agent Operator运行，并以精确非dryRun路径拒绝实际写入。大修订fixture仅在应用缩为0且保留原PVC/密钥时设置初值，随后浏览器实际HTTP保存。测试工具按原UID正常GC资源；读取失败不当作空集，无force/finalizer移除。最终CI_SMOKE单目标环境由#21组合适配，不能把本地双目标结果换标为CI。

计划与实际结果分别保存；固定manifest继续planned，实际testedCommit/runnerCommit/镜像/架构及失败重跑不改写。

提前评审补充入口（原15责任收据继续保留历史来源）：

- 规范 `GET /clusters/{id}/capabilities` 通过生成 `GetCluster` 返回同一安全能力数组；当前会话、账号和归属仍由RPC核验。
- 规范 `DELETE /instances/{id}` 空确认只可删除未部署草稿；携 `versionId/previewId` 复用已部署删除状态机返回202，兼容 `POST /deletions`。规范 `POST /operations/{id}/continue` 先通过授权 `GetOperation` 读取不可变action，PUBLISH/DELETE复用带修订和删除预览的 `ContinueLifecycle`，集群候选沿原管理员白名单；兼容 `continue-unsent`。
- 凭据受理与人工继续的幂等缓存只找回原operation身份，在当前授权事务中读取最新结果。独立卷任务在身份网络预读之前固定原集群UID/ACTIVE/credentialRevision，成功与失败都以同一CAS限制落盘；Pending卷责任仍阻止注销/转移，凭据轮换继续允许。
- `make e2e-review-q` 跑应用层回归；`make review-q-receipt E2E_ARGS='--profile LOCAL_FULL --output /private/review-q'` 输出9条补充REG及全部必选leaf实际pass/no-skip证据。`make e2e-review PYTHON=/private/venv/bin/python E2E_ARGS='--layer B --profile LOCAL_FULL ...'` 运行2条真实Pod浏览器REG；显式双目标/镜像/source/lane参数与原工作台driver相同。B首次实际红、UI修复后新镜像的绿分别记录，不以Host或旧15收据替代。
