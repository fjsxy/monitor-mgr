# #29 历史 exporter 边界验证记录

testedCommit：`2e7b91190fa1e8837f3d2a4207fc4ff5162fcdfe`。

`make ut PYTHON=/tmp/monitor-mgr-spec23-tools.3nB8bT/venv/bin/python` 退出 0：18/18 Ginkgo UT、race、生成一致性、vet、前端构建全部通过；合并产品语句覆盖率 **77.73%**（门槛 70%），platform 包 493.148s。`.scratch/ut-results.json` 的 candidate 与干净 HEAD 已核对一致。`make ut-historical-exporter` 的 UT-12/16 定向验证也通过。

本票无产品 Go、迁移或 IDL 改动。#26/#27/#28 的组合实现已满足历史发布分界，新增公开行为回归和真实验收步骤，避免为已有正确行为引入多余路径。

| 用例编号 | 本地结果 | 实际观察 |
| --- | --- | --- |
| I29-U01 | 通过 | 真实 v10 schema 升级补可靠 user ID；创建角色未知；升级、明确权限确认、状态/操作轮询均 0 SM mutation |
| I29-U02 | 通过 | 另一管理员新明确发布生成新 versionID、双冻结资源；原 KSM UID/RV 更新，SM 使用原创建用户标签；旧版本/操作仍单资源 |
| I29-U03 | 通过 | 未知创建用户经管理员确认仍未知；公开 HTTP 与直接 Kitex RPC 预览/发布均拒绝；无 owner/currentActor 回退、0 目标写入 |
| I29-U04.failed | 通过 | SM CRD 不可用/create 拒绝时，旧 FAILED 人工 retry 只写原 component，保留原版本、创建新 attempt；新草稿不生效 |
| I29-U04.unsent | 通过 | 旧未发送操作升级变为等待明确继续，轮询 0 写；continue-unsent 只恢复原 component |
| I29-U04.sent | 通过 | 旧已发送且已知原 UID 的操作只读核验后成功；0 KSM/SM 重放，不增加 SM 依赖 |
| I29-U05.uid | 通过 | 原 KSM UID 替换后拒绝预览，0 新 SM 写 |
| I29-U05.drift | 通过 | 未确认漂移拒绝发布，明确覆盖后沿原身份补建 |
| I29-U05.foreign | 通过 | 同名外部 SM 不接管，0 业务写 |
| I29-U05.permission | 通过 | SM create 权限拒绝时预览失败，原 exporter 保留、0 业务写 |
| I29-U05.lost | 通过 | 新补建 SM POST 生效丢响应，重启/refresh 仍无 confirmedUID、IDENTITY_UNCERTAIN，保留 KSM、不认领或重放 SM |
| I29-K01 | 未执行 | 已接 E2E-01：真实独占 Helm/PVC 旧升级与明确补建，输出 historical-exporter.json |
| I29-K02 | 未执行 | 已接 E2E-08：真实旧 exporter SM 拒绝补齐及丢响应重启，输出 historical-exporter-recovery.json |
| I29-B01 | 未执行 | 已接唯一 E2E-10 test.step；--list 仅验证仍一个 test，不是浏览器通过证据 |

真实旧库夹具删除 v11+ 五表及 creator 字段，剥离新 CREATE_INSTANCE 结果字段并清空历史 KSM 不存在的 SM 预览指纹。当前版本公开发布前置曾生成的 SM 仅在核对本 run 已确认原 UID 后清除；该删除仅为测试夹具，不是产品后台行为。

有效切片首次运行即 GREEN，无产品 RED。开发期间未使用 import 的编译失败和 revision 夹具误用导致的 409 已修正，不计产品缺陷。首轮全量因随后补了夹具被主动终止，日志 issue29-ut-obsolete-548d09a-interrupted.log 不作最终收据。
