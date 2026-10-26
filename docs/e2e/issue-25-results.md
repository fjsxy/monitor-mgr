# #25 本地实施结果

修订五列计划已完整发布并精确回读：[Issue 评论](https://github.com/fjsxy/monitor-mgr/issues/25#issuecomment-6414005203)。本地计划见 `docs/e2e/issue-25.md`。本文件记录本地局部验证，最终集成结果另行记录。

创建关系在 `CreateInstance` 的授权事务中固定；列表、详情及类型化 RPC 分别表达 `readOnly` 纳管边界与 `canManage/readOnlyReason` 当前管理能力。HTTP 和直接 RPC 共用实例写授权；worker 在首次发布、删除及 409 条件重试的实际写入前复核持久账号和实例创建角色。已发送结果继续只读核验。schema v11 与旧 PR #31 的字段定义一致，历史未知角色先受保护，不补造历史创建角色。

| 用例编号 | 本地结果 | 执行入口与决定性证据 |
| --- | --- | --- |
| I25-01、I25-02 | 通过 | UT-07；公开 HTTP 与直接 Kitex RPC 新建 exporter/collector，固定实际创建身份；未知字段、伪造创建者及后续覆盖均被拒绝 |
| I25-03、I25-04、I25-05、I25-06 | 通过 | UT-07；HTTP/直接 RPC 各变更返回 FORBIDDEN；草稿修订、活动时间、操作修订、执行尝试和外部 API 写请求数量保持不变；补充 Agent 草稿保护 |
| I25-07、I25-08 | 通过 | UT-07；创建者降级后原 ADMIN 实例仍受保护；USER 创建快照不因升权改变，集群归属转移后现所属用户可管理 USER 实例 |
| I25-09、I25-09.disabled | 通过 | UT-10；受理后派发前降级或停用，受控 TLS 目标零新增写入 |
| I25-09.DELETE | 通过 | UT-10；删除受理后降级，真实 DELETE 请求数量为零，原 CR 保留 |
| I25-09.RV | 通过 | UT-10；首次 PUT 明确 409 后降级，不取得第二次派发资格；无额外成功写入 |
| I25-10 | 通过 | UT-10；已发送 PUT 丢响应后降级，操作通过只读核验恢复 SUCCEEDED；累计成功写入固定为两次，无回滚或重放 |
| I25-11 | 通过 | UT-16；从真实 v10 schema 迁移，草稿/版本/任务及原 UID 保留，创建身份未知；普通用户只读，当前管理员可管理 |
| I25-12 | 通过 | UT-07；无集群权限仍拒绝访问，外部实例仍只读，注销集群实例保留可读历史且当前管理员也不能变更 |
| I25-13、I25-14 | 未执行 | 依赖 #28 采集字段与新 Operator 契约集成后的联合回归；本切片没有平台 ServiceMonitor 写入实现 |
| I25-B01 | 未执行 | 浏览器由最终集成验收执行；本切片完成页面创建身份、只读原因和写控件禁用，类型检查/构建通过 |
| I25-K01.unsent、I25-K01.sent | 未执行 | 真实双 kind、Helm Pod 与 Kubernetes API 代理由主集成串行验收 |

重复入口为 `make ut-instance-identity`，原生局部报告保存在 `internal/platform/.scratch/issue-25.json`；其只选择 UT-07/10/16，不是完整 18 项报告。交付消息列出最终 testedCommit，`.scratch/issue-25-receipt.json` 绑定提交与局部结果。生成一致性、`make vet`、`make ci-cases-check` 及前端类型检查/构建均通过。

环境和完整性：默认 `python3` 缺少锁定的 PyYAML；预检确认后主动中止首轮完整 `make ut`，该轮平台报告为 8 passed、1 interrupted、7 skipped，不能视为完整通过。使用已安装 PyYAML 6.0.3 的隔离 Python 环境单独复核 UT-18 通过，不能与其他报告合并冒充全量通过。完整 18 项、race、产品语句覆盖率 ≥70%、真实集群、浏览器及远端 CI 均以最终集成提交的独立证据为准。
