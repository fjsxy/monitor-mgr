# E2E 用例与 Issue 评论

每个 Issue 的用例正文保存在 `issue-<编号>.md`，按“用例编号、验证目的、验证步骤、预期结果、所属 Issue 编号”五列维护。实施前完整发布正文为对应 Issue 评论并回读；正文更新后发布新修订，评论链接记录在本页。实际运行收据单独保存，不把计划文件改成通过证明。

#14、#15 的本次表格为新增约束后的标准化补录；此前实施前计划及真实运行来源保留。#16 在继续新增实现前补齐新格式。#17–#21 的表格预先保存并发布，后续领取时再次核对是否与本地一致。

重复命令从 `make help` 查找。首次前端操作运行 `make web-install`；本地完整自动化使用 `make ut PYTHON=<已具备PyYAML的解释器>`。真实集群和浏览器统一使用 `make e2e PYTHON=<已具备PyYAML的解释器> E2E_ARGS='<脚本要求的显式参数>'`，自动准备独占集群、平台与浏览器并按本 run 原身份清理；运行 `scripts/e2e.py --help` 查看候选提交、run ID、输出目录及分组参数。凭据只通过受限文件路径提供。

| Issue | 本地用例 | 标准化评论 |
| --- | --- | --- |
| #14 | [issue-14.md](issue-14.md) | [已发布并回读](https://github.com/fjsxy/monitor-mgr/issues/14#issuecomment-6249364562) |
| #15 | [issue-15.md](issue-15.md) | [已发布并回读](https://github.com/fjsxy/monitor-mgr/issues/15#issuecomment-6249364906) |
| #16 | [issue-16.md](issue-16.md) | [已发布并回读](https://github.com/fjsxy/monitor-mgr/issues/16#issuecomment-6249352132) |
| #17 | [issue-17.md](issue-17.md) | [已发布并回读](https://github.com/fjsxy/monitor-mgr/issues/17#issuecomment-6250672039) |
| #18 | [issue-18.md](issue-18.md) | [已发布并回读](https://github.com/fjsxy/monitor-mgr/issues/18#issuecomment-6252666518) |
| #19 | [issue-19.md](issue-19.md) | [已发布并回读](https://github.com/fjsxy/monitor-mgr/issues/19#issuecomment-6254247315) |
| #20 | [issue-20.md](issue-20.md) | [已发布并回读](https://github.com/fjsxy/monitor-mgr/issues/20#issuecomment-6338843923) |
| #21 | [issue-21.md](issue-21.md) | [已发布并回读](https://github.com/fjsxy/monitor-mgr/issues/21#issuecomment-6254819921) |
| #25 | [issue-25.md](issue-25.md) | [已发布并回读](https://github.com/fjsxy/monitor-mgr/issues/25#issuecomment-6336357215) |
| #26 | [issue-26.md](issue-26.md) | [已发布并回读](https://github.com/fjsxy/monitor-mgr/issues/26#issuecomment-6339463762) |
| #27 | [issue-27.md](issue-27.md) | [已发布并回读](https://github.com/fjsxy/monitor-mgr/issues/27#issuecomment-6338083215) |
| #28 | [issue-28.md](issue-28.md) | [已发布并回读](https://github.com/fjsxy/monitor-mgr/issues/28#issuecomment-6338844553) |
| #29 | [issue-29.md](issue-29.md) | [已发布并回读](https://github.com/fjsxy/monitor-mgr/issues/29#issuecomment-6340305094) |
| #30 | [issue-30.md](issue-30.md) | [已发布并回读](https://github.com/fjsxy/monitor-mgr/issues/30#issuecomment-6337276378) |
