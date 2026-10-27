# #29 实施与局部验证结果

2026-10-26。计划、实现、局部验证与真实验收分开记录；Issue 保持开放，由父规格统一合入和执行真实验收。

- 产品实现：`e66831675d0db2693b3fe4c50b3960cf7be60348`；合并 integration `8fe0f7be4d2a4b3c7876374d749375d0c4c31c39` 为 `76ffba8a580381c2d728b5c9980561751b44396f`。
- 产品最终 testedCommit：`f2cf0e420f513939efe078b917d2d9a5a977ddcd`。UT-11/12/16/17/18 原生 Ginkgo 报告 5 passed / 0 failed；73.497s。包含真实 HTTP → Kitex → SQLite → worker 与受控 TLS 目标。
- 工具收据存在性补强 testedCommit：`4e361de5e9e7866591e70916d68669a40e49b1d7`。UT-18 再跑通过，7.411s；false、空列表、空 error 必须存在且非 null。
- 最终五列计划评论：https://github.com/fjsxy/monitor-mgr/issues/29#issuecomment-6416165446，完整正文精确回读一致。

v16 新增独立盘点、旧 SM 外部处理事实和原操作解阻记录。管理员确认绑定原 instance/operation/resource/path/UID/原始字节指纹；每次新启用仍读旧路径，只有404放行。旧状态、possiblySent、原未知 UID 和冻结字节保留。父身份单独核验，只释放精确旧 operation 互斥；空实例 UID 仅从旧步骤已持久 confirmedUID 恢复，不回退较新身份或版本，不提升失败/部分旧版本。平台无 SM 写 API。

页面提供历史盘点、快照下载、Agent 选择影响、显式 SM 处理确认与独立父确认。真实采证通过 `Scenario.acceptance_evidence['issue29']` 交给 Go，精确绑定 `MONITOR_MGR_E2E_CANDIDATE`，不采用 Python passed 代替业务断言。

| 局部顶层用例 | 结果 | 原生用例耗时 |
| --- | --- | --- |
| UT-11 重试与重启恢复 | 通过 | 28.240s |
| UT-12 预览与删除保护 | 通过 | 6.950s |
| UT-18 测试结果来源与清理边界 | 通过 | 6.506s |
| UT-17 HTTP 与 Kitex 协议契约 | 通过 | 2.619s |
| UT-16 数据库主密钥与敏感投影 | 通过 | 28.461s |

以下仅列原生报告实际产生的 #29 子例结果；组合 ReportEntry 保留其原名称，不将其伪称多个独立运行。

| 实际 ReportEntry | 结果 | 所属 UT |
| --- | --- | --- |
| `I29-U08.frozen-bytes` | 通过 | `UT-11` |
| `I29-U06.inventory-success` | 通过 | `UT-11` |
| `I29-U06.resolve-SUCCEEDED` | 通过 | `UT-11` |
| `I29-U06.resolve-IDENTITY_UNCERTAIN` | 通过 | `UT-11` |
| `I29-U07.parent-known` | 通过 | `UT-11` |
| `I29-U07.parent-partial` | 通过 | `UT-11` |
| `I29-U07.parent-partial-original-only` | 通过 | `UT-11` |
| `I29-U07.parent-unknown-present` | 通过 | `UT-11` |
| `I29-U07.parent-unknown-absent` | 通过 | `UT-11` |
| `I29-U07.parent-newer-uid` | 通过 | `UT-11` |
| `I29-U07.parent-newer-version` | 通过 | `UT-11` |
| `I29-U07.parent-drift` | 通过 | `UT-11` |
| `I29-U06.resolve-binding.snapshotId` | 通过 | `UT-11` |
| `I29-U06.resolve-binding.operationId` | 通过 | `UT-11` |
| `I29-U06.resolve-binding.resourceKey` | 通过 | `UT-11` |
| `I29-U06.resolve-binding.objectPath` | 通过 | `UT-11` |
| `I29-U06.resolve-binding.originalUid` | 通过 | `UT-11` |
| `I29-U06.resolve-binding.documentFingerprint` | 通过 | `UT-11` |
| `I29-U06.resolve-still-present.foreign` | 通过 | `UT-11` |
| `I29-U06.resolve-still-present.forbidden` | 通过 | `UT-11` |
| `I29-U06.resolve-authorization.rpc` | 通过 | `UT-11` |
| `I29-U06.resolve-authorization.demoted` | 通过 | `UT-11` |
| `I29-U06.resolve-race.preview` | 通过 | `UT-11` |
| `I29-U06.inventory-agent` | 通过 | `UT-11` |
| `I29-U08.rename-ksm` | 通过 | `UT-11` |
| `I29-U06.inventory-unknown.pagination` | 通过 | `UT-11` |
| `I29-new-publication.old-path-reappeared` | 通过 | `UT-11` |
| `I29-new-publication.parent-response-lost` | 通过 | `UT-11` |
| `I29-R01.gone` | 通过 | `UT-18` |
| `I29-R01.blocked` | 通过 | `UT-18` |
| `I29-R03.replaced` | 通过 | `UT-18` |
| `I29-R03.read` | 通过 | `UT-18` |
| `I29-R03.finalizers` | 通过 | `UT-18` |
| `I29-R03.delete` | 通过 | `UT-18` |
| `I29-R02.confirmed` | 通过 | `UT-18` |
| `I29-R02.unknown` | 通过 | `UT-18` |
| `I29-R02.unsent` | 通过 | `UT-18` |
| `I29-R02.foreign-instance` | 通过 | `UT-18` |
| `I29-R02.foreign-version` | 通过 | `UT-18` |
| `I29-R02.foreign-operation` | 通过 | `UT-18` |
| `I29-U08.rollback.v10` | 通过 | `UT-16` |
| `I29-U08.rollback.v12` | 通过 | `UT-16` |
| `I29-U08.rollback.v13` | 通过 | `UT-16` |
| `I29-U01/U02` | 通过 | `UT-16` |
| `I29-U03.rpc-preview` | 通过 | `UT-16` |
| `I29-U03.rpc-publish` | 通过 | `UT-16` |
| `I29-U03` | 通过 | `UT-16` |
| `I29-U04.failed` | 通过 | `UT-16` |
| `I29-U04.unsent` | 通过 | `UT-16` |
| `I29-U04.sent` | 通过 | `UT-16` |

生成代码一致、`go vet ./...`、前端 TypeScript/Vite build、E2E Python 语法、Go E2E 编译、Playwright 唯一 test 发现均通过；固定清单仍为18 UT / 10 E2E。未在本分支执行完整 UT/race/覆盖率门禁，最终覆盖率不少于70%的要求由父规格完整验证。

重复入口：`MONITOR_MGR_PYTHON=<venv>/bin/python make ut-historical-exporter`、`make check-issue29-e2e PYTHON=<venv>/bin/python`、`make check-generated ci-cases-check`。

保留的先前失败：三个公开接口先分别 HTTP404；original-only 在修订3发布回读后的 red 为错误放行200，修复后409；首次完整局部回归4通过/1失败，根因是 `agent_url_projection_test.go` 仍断言 schema15，改16后保留原 effective_spec/HMAC/迁移回滚核验并全部通过。更早 UT16 真实v13夹具失败由 integration `fe7d928` 修复，原失败日志保留，没有覆盖成旧成功结论。

主机证据目录 `/tmp/monitor-mgr-spec23-20261026/`：`issue29-final-native.json`、`issue29-final-targeted-green.log`、`issue29-final-presence.log`、`issue29-final-checks.log`、`issue29-final-e2e-static.log`、`issue29-final-web.log`、`issue29-final-vet-presence.log`。受控测试日志留在本机，不上传私有原始数据。

未执行：I29-K01/K02/B01 真实 Helm/PVC 升级、真实 Operator/Agent/receiver targets 与样本切换、Pod 重建、真实浏览器及最终 cleanup；这些不能由本次语法/编译/受控 TLS 通过替代。计划中其余权限/超时/竞争复合输入的最终完整验收也不由本局部报告推断为通过。父任务统一执行真实验收；本子任务没有运行 kind。

交接：E2E-01 在 #26 历史升级采证结束后明确启用；E2E-08 执行旧 SM 成功/unknown 两输入并读取真实 targets/endpoint×Pod 样本；E2E-10 在 I26 最终注销前调用页面 hook。采集 helper 复用 #30 的 `agent_collection.py` 与 `charts/test-receiver`，最终集成须同时含 #30；合并浏览器入口时保留 `issue30KnownAgent` 和两个 hook。确认后的旧 SM 原路径必须继续404；管理员外部测试清理按已登记 UID/RV，不能凭同名对象接管清理责任。
