# 一期 18 UT / 10 E2E 实施结果

执行日期：2026-10-21。被测代码提交：`bb99cfe4c7d73a5a81ac1703af3284927b977452`；分支：`codex/phase1-ginkgo`。启动与结束来源检查均为干净工作树；结果文档的后续提交不改写被测提交。

## 最终结论

- UT：18/18 通过，race 通过；产品语句覆盖率 **76.77%**（要求≥70%）。
- E2E：10/10 通过；全部为真实独占 kind/Helm/Pod/Operator/API/PVC 场景，浏览器只运行1条主流程。无失败、未执行、跳过或 Ginkgo 用例重试；E2E-08的显式业务 retry 属于该用例的预期动作。
- 清理：两套本 run 集群均已按原容器身份/cluster UID 顺序删除；每例原资源清理均成功。
- 质量：生成一致性、go vet、前端 TypeScript 检查与构建、CI 清单核对通过。
- CI：已调整为 UT / E2E 两个 job，可信同仓 PR 与 main 使用相同18+10清单；本轮没有触发远端 GitHub Actions，不能据此宣称远端 CI 已通过。

## 执行环境与来源

- 平台运行架构：`linux/arm64`；Kubernetes `v1.28.15`，kind `v0.33.0`，Helm `v4.3.0`。
- control：1 control-plane + 2 workers；data：1 可调度 control-plane。平台/每例数据卷与 namespace 独立。
- 平台 config digest：`sha256:aad028394c57d658a646476f2014ba1654640eb9159ddda765845516de96a5d1`；实际 Pod UID/容器 ID/CRI config 逐次绑定该摘要，Service port-forward 的监听就绪记录也绑定本次进程。
- monitor-op：`2b2a8b79a52070fb89a08f5afae2b7698901c999`；Prometheus Operator `v0.83.0`，Prometheus `v2.55.1`，KSM `v2.11.0`。Operator 实际容器与原 config 校验通过。
- 构建输入保留固定 index → 目标架构 manifest → config/layers 的原字节校验；Docker inspect.Id 与 config digest 分开处理。
- control：`mm-1021-28-v6-control`，cluster UID `b21a576d-ff1b-4058-a5d7-43537ddadd83`，3节点；私有 kubeconfig/显式 context。
- data：`mm-1021-28-v6-data`，cluster UID `65a87672-56f6-4ef3-8b55-7fb3f2fbd5b4`，1节点；私有 kubeconfig/显式 context。

## UT 实际结果

| ID | 验证目的 | 结果 | Ginkgo 耗时 |
| --- | --- | --- | --- |
| UT-01 | 认证与 Session | 通过 | 11.09s |
| UT-02 | 账号与当前授权 | 通过 | 81.93s |
| UT-03 | kubeconfig 安全解析 | 通过 | 11.90s |
| UT-04 | 集群登记与管理状态 | 通过 | 15.76s |
| UT-05 | KSM 配置规则与映射 | 通过 | 0.00s |
| UT-06 | Agent selector 与发送配置 | 通过 | 0.01s |
| UT-07 | 实例身份与不可变字段 | 通过 | 7.98s |
| UT-08 | 保存、预览与版本冻结 | 通过 | 5.16s |
| UT-09 | 幂等与受理事务 | 通过 | 7.16s |
| UT-10 | 派发、取消与任务授权 | 通过 | 12.76s |
| UT-11 | 重试与重启恢复 | 通过 | 10.01s |
| UT-12 | 预览与删除保护 | 通过 | 32.51s |
| UT-13 | PVC 危险动作决策 | 通过 | 46.49s |
| UT-14 | 状态与工作台查询 | 通过 | 17.51s |
| UT-15 | 请求预算与并发 | 通过 | 23.34s |
| UT-16 | 数据库、主密钥与敏感读模型 | 通过 | 12.99s |
| UT-17 | HTTP/Kitex 协议契约 | 通过 | 12.67s |
| UT-18 | 测试工具的最小可信边界 | 通过 | 0.08s |

UT-06 用受控只读 adapter 验证9组实际匹配集合；UT-15复用一个平台处理四类 metadata，仅保留一次完整预算计时。UT-16 保留 v1 最旧库以及一个带冻结历史的 v5 样本，后者同时验证 DDL 失败回滚和修复恢复；未重复所有中间 schema。UT-18 仅三组工具可信边界。UT-12补充了真实Operator status/RV并发冲突的确定性回归及安全反例，未增加 It。

## E2E 实际结果

| ID | 验证目的 | 结果 | Ginkgo 耗时 |
| --- | --- | --- | --- |
| E2E-01 | 平台部署、就绪与 Pod 重建 | 通过 | 31.00s |
| E2E-02 | 双集群登记、凭据与归属 | 通过 | 52.71s |
| E2E-03 | KSM 从草稿到三种真实拓扑 | 通过 | 101.07s |
| E2E-04 | Agent 采集关联与发送配置 | 通过 | 92.92s |
| E2E-05 | Agent 分片与 PVC 完整生命周期 | 通过 | 175.30s |
| E2E-06 | 发布冻结、漂移与版本回看 | 通过 | 36.57s |
| E2E-07 | 真实目标故障与状态隔离 | 通过 | 96.22s |
| E2E-08 | 真实写响应丢失与重启禁重放 | 通过 | 65.31s |
| E2E-09 | 真实前台删除与阻塞保护 | 通过 | 54.43s |
| E2E-10 | 唯一浏览器主流程 | 通过 | 36.78s |

E2E-05 包括两种反向混合策略、shards 2→1、挂起零副本→恢复→删除、同名替代 PVC 的 UID 保护和 emptyDir。E2E-08 包括已知 UID 更新响应丢失后的重启只读核验、首次创建身份不明禁止补认/补写/删除，以及原冻结版本显式 retry。E2E-10 的浏览器把 revision `9007199254740993` 精确保存到 `9007199254740994` 后完成发布、版本回看和删除。

## 复现与证据

```sh
make ut PYTHON=.tools/ginkgo-venv/bin/python
make e2e PYTHON=.tools/ginkgo-venv/bin/python E2E_ARGS='--run-id mm-example --output /tmp/mm-example --monitor-op-source /Users/sunxuanyi/code/monitor-op'
```

- [UT 安全结果](/Users/sunxuanyi/.codex/worktrees/phase1-ginkgo/monitor-mgr/.scratch/ut-results.json)，[coverage profile](/Users/sunxuanyi/.codex/worktrees/phase1-ginkgo/monitor-mgr/.scratch/coverage.out)。
- [E2E 安全结果](/private/tmp/monitor-mgr-28cases-v6/public/results.json)，[镜像和实际容器来源](/private/tmp/monitor-mgr-28cases-v6/public/images.json)，[清理结果](/private/tmp/monitor-mgr-28cases-v6/public/cleanup.json)。
- [浏览器截图目录](/private/tmp/monitor-mgr-28cases-v6/public/browser)。
- 原始 Ginkgo/Playwright/进程输出、kubeconfig、认证材料与私有 Operator checkout 只在本 run `.private/`，不进入 CI artifact。

## 修复过程的结果边界

- `e516ad2` 的首次准备在镜像元信息获取阶段停止：E2E 0执行/10未执行，已清理。
- `05de784` 完整执行10个 E2E：8通过/2失败。E2E-02 重复创建同 namespace 的锚点导致 Pod 数断言不收敛；E2E-08 未预登记代理创建身份，原 UID 取证缺失。二者修复后完整重跑，没有拼接不同提交的局部通过记录。
- `cc962c8` 在新增来源预检中发现 inspect.Id 与 config digest 不同：环境失败，E2E 0执行/10未执行，已清理。改为核验原 docker-save Config 字节。
- `72ba115` 完整执行10个 E2E：9通过/1失败，已清理。E2E-08 代理报告使用嵌套 originalCreate.uid，取证字段读取修正；同提交 UT 检出测试日志缓冲区并发读写，改为已有加锁记录器。
- `cf0a8e0` 完整18 UT/race通过，覆盖率76.58%；10个 E2E 为9通过/1失败，已清理。真实 Operator 在读取与条件 PUT 间更新 status，明确拒绝的409被直接结束；0.4秒屏障复现连续两次失败，仅移动到读取前即通过。
- 修复只处理明确拒绝的更新409，原 attempt 内最多3次条件写；每次重新核验原UID、spec、存储影响、当前账号与凭据revision。UT-12内的 status、spec、UID、撤权、换凭据、持续冲突六组屏障回归及race通过；POST、超时和丢响应禁止重放。
- 最终 `bb99cfe` 重新完整执行18 UT和10 E2E，以上最终结论仅引用这一轮同源结果。

CI 跑什么及选择标准见 [ci-cases.md](../acceptance/ci-cases.md)，保留/合并/放弃映射见 [test-plan.md](../acceptance/test-plan.md)。
