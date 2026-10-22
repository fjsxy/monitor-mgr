# #26 实施结果与待验收项

实施前计划：`docs/e2e/issue-26.md`；完整评论已逐字回读一致：https://github.com/fjsxy/monitor-mgr/issues/26#issuecomment-6336384174 。本文件区分执行证据与待执行检查。

- RED1：真实 v10 唯一创建证据读取时 creatorUserId 仍为空；GREEN1：可靠 KSM/Agent、证据缺失、矛盾、错误 target/name/component/namespace、actor 不存在、损坏 JSON 全部定向通过。
- RED2：管理员权限确认入口为 404；GREEN2：HTTP→真实 Kitex→SQLite 及直接 RPC，确认 actor/time/result、允许/撤销、重启、幂等、当前管理员授权和已知角色保护通过。后台派发前撤销后零目标写入。
- 迁移故障：真实裁掉 v11+ 结构，故意制造 v13 DDL 冲突；旧库版本、密文、UID、审计及一致快照保持，消除冲突后原冻结版本、任务和用户 Session 可读。
- 合并 #27 后修复 v12 迁移依赖后续运行时读模型的问题：v12 仅读当时已有身份字段；v11/v12/v13 依次为创建身份、逐资源进度、历史创建补齐与权限确认。v1–v10 未改。
- `make ut-historical-identity` 最终定向通过（UT-07/10/16，24.661s）；`make web-build`、Playwright `--list`（现有唯一 E2E-10）、Python 编译与 `git diff --check` 通过。完整 `make ut` 在提交后执行，精确 testedCommit 和覆盖率记录于本轮实施交接及 Ginkgo 收据。
- 真实验收 E2E-01/10 已接入可执行旧库/Pod/浏览器步骤及 `public/historical-identity.json`，待根协调在最终同源提交执行；未使用现有用户集群或 18080/18481。远程 CI 本票尚未执行。

## 真实 E2E 启动根因修复

首轮远程 CI candidate `586caca890b3a9aa2c7b3e2a911f4fdaee6c0d64` 的 topology 用例在 `Scenario.deploy` 失败：Helm release 为 `platform`，chart 以 `.Release.Name` 命名，而验收随后寻找 `Deployment/monitor-mgr`。将验收 release 统一为 `monitor-mgr`；`helm template monitor-mgr charts/monitor-mgr` 已实际核对 Deployment/Service 为 `monitor-mgr`、PVC 为 `monitor-mgr-data`。最终真实启动结果由集中 E2E 给出，不以渲染通过代替集群通过。
