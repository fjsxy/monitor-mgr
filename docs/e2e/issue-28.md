# #28 新 exporter 配套 ServiceMonitor 全生命周期测试计划

测试接缝沿用已确认的公开 HTTP → 真实 Kitex RPC → SQLite → worker、直接 RPC、真实集群及浏览器；计划不是执行结果。真实集群由集成协调者在独占环境运行。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| I28-U01 | 草稿零写、两资源预览与冻结 | 经公开 HTTP 创建 KSM 并保存草稿；读取目标写计数；预览；发布并读取版本/操作资源 | 草稿0写；预览包含KSM与ServiceMonitor；冻结两资源；SM namespace等于部署namespace，user-id等于实际creator | #28 |
| I28-U02.deployment | Operator标签与端口契约 | 以 Deployment、含点CR名发布；读SM selector/endpoint | instance标签使用锁定Operator规范化值，http-metrics与/metrics；不写Service | #28 |
| I28-U02.stateful | StatefulSet分片选择 | 以StatefulSetSharded发布；读取SM | selector覆盖本实例分片Service，独立实例不匹配 | #28 |
| I28-U02.daemon | DaemonSet多Service选择 | 以DaemonSetSharded发布；读取SM | selector同时匹配node/control Service，不限定role/endpoint-group，不匹配其他实例 | #28 |
| I28-U02.long | 长名称规范化 | 以超过43字符合法CR名预览；核对规范化已知literal | selector使用Operator截断+SHA256摘要规范，SM名称稳定合法 | #28 |
| I28-U03 | 发布前依赖和同名保护 | 分别令SM CRD/list不可用、namespace缺失、create/update权限不足、同名外部SM存在；预览/受理 | 拒绝并0组件写；不按name/spec/label接管；预览后目标变化拒绝受理 | #28 |
| I28-U04 | 部分成功及人工原版本补齐 | 第二资源明确拒绝；读进度；编辑草稿；人工retry | exporter保留且SUCCEEDED；SM失败；retry先核验exporter原UID/冻结配置，只补SM且不采用新草稿 | #28 |
| I28-U04.uid | 补齐前核验 exporter UID | SM失败后替换已成功exporter UID，再显式retry | 新尝试只读核验失败、0新增SM写，历史成功事实保留 | #28 |
| I28-U04.drift | 补齐前核验 exporter 配置 | SM失败后改已成功exporter spec，再显式retry | 新尝试结果待确认、0新增SM写，不回滚或覆盖漂移 | #28 |
| I28-U05.first | 首次SM丢响应无UID禁认领 | SM实际POST生效但丢响应；刷新、重启平台、尝试retry | component成功保留；SM IDENTITY_UNCERTAIN，无confirmedUID；只读核验不能凭相同配置认领、不再次写 | #28 |
| I28-U05.known | 已知SM更新丢响应恢复 | 先成功发布；更新SM写响应丢失；刷新/重启 | 保持独立原UID；只读核验冻结配置后完成；不重放 | #28 |
| I28-U06 | 第二步骤写前失权 | exporter成功后、SM派发前降级/停用操作者；释放worker | exporter保留；SM0写；操作失败且显示准确进度 | #28 |
| I28-U07 | 独立UID、漂移和整体删除 | 发布后替换SM UID或改spec/平台label；预览；恢复原目标后更新、删除 | 替代UID拒绝；漂移需明确确认；DELETE逐资源UID/RV条件写且均消失才完成 | #28 |
| I28-U08 | 旧冻结单资源不可暗加SM | 从真实旧schema迁移冻结版本并retry | 原版本仍仅component，0隐式SM写 | #28 |
| I28-K01 | 实际合法部署模式和生命周期 | 独占kind环境部署锁定Operator；创建含点名称Deployment、StatefulSetSharded、DaemonSetSharded并发布；读取真实Service标签、SM、Endpoint；更新并删除一个 | SM匹配实际本实例所有指标Service且端口正确；两份CR有独立UID；其他实例不变；全部清理后删除成功 | #28 |
| I28-K02 | 实际部分失败、未知响应和重启 | 真实代理分别拒绝SM POST、丢SM POST响应、丢已知SM PUT响应；重建平台Pod；显式retry/refresh；在真实exporter生效、响应返回前撤权，释放后检查SM未发 | 保留exporter；拒绝可补齐；未知UID不认领；已知UID只读恢复；目标请求计数证明不重放 | #28 |
| I28-B01 | 页面展示配套配置和逐资源状态 | 独占平台运行现有E2E-10唯一Playwright test；在预览/版本/操作页检查SM YAML与资源结果；失败后查看可执行修复入口 | 用户可看到SM namespace/selector/user标签/冻结配置及独立UID、失败原因、补齐动作 | #28 |

执行映射：I28-U01/U02/U03 → UT-08；I28-U04/U05 → UT-11；I28-U06 → UT-10；I28-U07 → UT-12；I28-U08 → UT-16；I28-K01 → E2E-03/E2E-09；I28-K02 → E2E-08；I28-B01 → E2E-10。
