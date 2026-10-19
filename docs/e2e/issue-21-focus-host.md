# #21 focus-layer Host 平台隔离回归计划

当前 C12 `7d5353da2054dbf56cd98570200d6b3cc26cabfa` 的新增 Host 夹具把真实运行机器当作合成 LOCAL_FULL 环境，Linux CI 因真实 host/profile guard 拒绝而失败。该 guard 是正确行为；修复仅让隔离 Host 夹具显式声明合成 `darwin/arm64`，不得修改 `run.py` 或真实 profile guard。

本计划的 `REG21-FOCUS-LAYER-002` 是独立 Host 回归，不加入中央 207/104 seed。验证接缝为原公开 CLI → 原 batch/index/关闭协议，Git/环境快照及 child 结束消息继续使用原隔离 Host 模型；真实不匹配 host 的拒绝分支不得 mock。只修改 `scripts/acceptance/focus_layer_test.py` 与本文件。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| REG21-FOCUS-LAYER-002.linux-red-green | Linux/amd64 可复现并修复 Host 夹具泄漏 | 在外置 Host harness 强制 `sys.platform=linux`、`platform.machine()=x86_64`，先执行 C12 原 dispatch Host 方法并保留真实红；再仅为该方法的合成 LOCAL_FULL 调用显式声明 darwin/arm64，重复同一命令 | 修复前因实际 host/profile 不匹配而零业务派发，原 Host 断言红；修复后原独立 20 键断言绿，104 全 seed、84 not_run、partial strict `incomplete-run` 原样 | #21 |
| REG21-FOCUS-LAYER-002.real-host-reject | 合成 Host 声明不放宽真实运行边界 | 新增 Host 子例对未声明合成平台的 LOCAL_FULL 调用，分别提供 linux/x86_64、linux/aarch64、darwin/x86_64 的真实不匹配边界；使用合法合成 source object，所有真实进程/环境验证/业务派发均设拒绝探针 | 原 batch 的 host guard 拒绝，driver=1、productGate=false，原 seed 保留；环境验证、child、Go/Kube/Docker 零派发；不是 partial 通过 | #21 |
| REG21-FOCUS-LAYER-002.related-host | 保持 C12 已有协议与名称/状态回归范围 | 运行既有 `make acceptance-status-fixture-check`，随后以强制 Linux/amd64 harness 重跑本文件的原 dispatch 方法与新增 host 拒绝方法；保留各实际命令/原输出 | 既有 27 项相关 Host 检查及新增 portable Host 子例均绿；不运行任何产品 Q/K/B，不用 Host 结果冒称真实 Kubernetes 通过 | #21 |
| REG21-FOCUS-LAYER-002.scope | 固定正式计划与执行范围 | 对两个变更路径做 compile、diff-check、原 acceptance-plan；逐项比较中央 LOCAL_FULL 207 / supplemental 104 原 entry 对象及 registry/gate/source-lock 字节 | 中央键、合同与 guard 不变；仅测试及文档提交，C13 单列最终 CI 来源 | #21 |

继续保留 C12 sole owner 正在执行的原 17K3B、C9/C10/C11 已有 207 分源结果及原 81Q，不重跑业务。C13 仅新 head 的 CI 校验；本计划不是最终全规格双轴 review，也不是通过记录。原 C12 CI 失败及原 artifact 不改写。用户已确认 Ginkgo、UT 覆盖率 >=70%、Helm 新规约仅适用于后续开发，本次不追溯迁移。
