# 一期 UT / E2E

当前固定清单为 [cases.json](cases.json)：18 个 UT、10 个 E2E。详细目的、必要步骤、保留/合并/放弃的旧覆盖映射见 [test-plan.md](test-plan.md)；CI 跑什么以及选取理由见 [ci-cases.md](ci-cases.md)。

```sh
make ut PYTHON=<带 PyYAML 的 Python>
make e2e PYTHON=<带 PyYAML 的 Python>
```

`make ut` 一次执行 Ginkgo/race/覆盖率（至少70%）、生成一致性、vet、前端类型检查和构建。只统计 `internal/control`、`internal/platform` 手写产品代码，不把生成代码/参数入口/测试夹具计入覆盖率。工具只保留 UT-18 的三组可信边界。

`make e2e` 要求 Docker、kind、kubectl、Helm、Go、Node/npm、Python/PyYAML、openssl、curl 与 lockfile 中的 Playwright Chromium 可用；源码必须为干净提交。自动生成 runId，创建独占 control(1cp+2worker) 和 data(1可调度cp)，为每例 Helm 部署独立平台/PVC。默认从主仓库同级 `monitor-op` 克隆锁定 SHA，不使用其未提交内容；也可指定来源：

```sh
make e2e PYTHON=<Python> E2E_ARGS='--monitor-op-source <本地源码目录> --run-id mm-example --output /tmp/mm-example'
python scripts/e2e.py --cleanup /tmp/mm-example # 进程中断后，独立核验原身份再清理
```

每个 Ginkgo It 通过私有 Unix socket 调度 Python；每阶段为中文 By，必要断言失败则整例失败。10例全部通过、环境与清理成功才通过门禁；缺失/失败/skip/重复/重试/来源不符均拒绝。报告分别记录成功、失败、未执行和环境失败。

默认输出 `.scratch/e2e/<runId>/public/` 中的 `results.json`、`cleanup.json` 与截图；UT 输出 `.scratch/ut-results.json`。同 run 的 `.private/` 仅本地保留原始进程/平台日志、Ginkgo/Playwright 原报告、kubeconfig、私有 Operator checkout 与认证材料。CI 只上传 public 与安全 UT 摘要。绝不上传整个输出根目录。

集群使用独立 kubeconfig 和显式 context，kind 不写默认 context；清理核对原容器集合/cluster UID，逐集群顺序删除。用例内 Kubernetes DELETE 使用原 UID precondition，测试 finalizer 只删除自己添加的 token，不泛清 foreign finalizer。环境失败不能跳过当作通过。版本沿用 `scripts/acceptance/source-lock.json`。

旧 `phase1.json`、历史 `issue-*.md` 结果与 `scripts/acceptance` 中未被新入口使用的 profile/export/recover 代码仅供历史追溯。旧 80/207、58 Q、17 Smoke 不再是当前 CI 的数量门槛；历史通过记录保持原 SHA 和原含义，不改写为新的 18+10 已通过。

构建阶段先通过主机 HTTPS 校验固定 Docker Hub index、目标架构 manifest 和所有 config/layer，再以[本地 OCI build context](https://docs.docker.com/reference/cli/docker/buildx/build/#additional-build-contexts---build-context)交给 BuildKit。可复用的纯公开构建输入缓存为 `.scratch/oci-inputs/`；版本/摘要不变，不依赖 Docker Hub 登录材料。先构建后创建集群，避免同时占用编译和四节点内存。
