> 2026-10-23 修订：配套 ServiceMonitor 由 monitor-op 管理。本正文替代旧职责描述；历史计划/验收评论保留原语义，不代表本修订已完成。PR #31 尚未合并，实施前应核实并复用其中适用部分，不重复开发已具备的行为。

## 父规格

[fjsxy/monitor-mgr#23 exporter 配套 ServiceMonitor、实例创建归属与管理员实例保护规格](https://github.com/fjsxy/monitor-mgr/issues/23)。

## 直接依赖

- **仍未满足：** [#25](https://github.com/fjsxy/monitor-mgr/issues/25)（身份/授权）、[#27](https://github.com/fjsxy/monitor-mgr/issues/27)（安全恢复），两项均 OPEN；本 Issue 仍被这两项阻塞。
- **已满足：** [monitor-in-hand#87](https://github.com/fjsxy/monitor-in-hand/issues/87) 已随 [PR #89](https://github.com/fjsxy/monitor-in-hand/pull/89) 合并并关闭。依据 2026-10-26 用户明确决定，接受同源完整本地验收及已发布的可安装候选。原生依赖关系保留追溯，#87 关闭后不再计入开放 blocker；当前开放 blocker 数为 2。
- Operator 依赖以以下冻结候选、契约和报告为依据；平台仍负责配置及 Agent 关联。

## Operator 接入基线（2026-10-26）

[候选下载与升级说明](https://github.com/fjsxy/monitor-in-hand/releases/tag/v0.2.0-rc.4)。以下锁定值已从远程 release 回读；`release.json` 和完整报告已重新下载验证 SHA256。此为本次允许平台接入的候选基线，不使用 latest，不用合并后的新 SHA 改写原发布来源。

| 项目 | 锁定值 |
| --- | --- |
| 候选 Operator / Chart | `0.2.0-rc.4`（prerelease） |
| Chart 资产 | `monitor-in-hand-0.2.0-rc.4.tgz` |
| CRD / RBAC 资产 | `kubestatemetrics.yaml` / `manager-role.yaml` |
| 镜像 repository | `docker.io/fjsxy/monitor-in-hand` |
| OCI index | `sha256:1a9ec41de21c7b2b633ee7179f5f3f4c1a9e098f1dc4e74a9e720dbee73f19ff` |
| linux/amd64 | `sha256:76fb66b77b9b9ed6094ef70fc84fb85a0f22d7b63416414bde09c6ca289f9065` |
| linux/arm64 | `sha256:0eadb4af76f5a1c72eb3f5bb4b8372eab43dbabb0a4dfb194866d58cf854ebde` |
| source / testedCommit | `8fb5ccb8ec2e9c0742fc7b83316d01c833af9aee` |
| main 合并提交 | `7c4a7bd7a8d26c4038ae370eeb92e94e58f0d3dc`（与受测树一致） |
| release.json SHA256 | `4b1aa74567d0c3a0c97017c83f7fc29d7fe0b87d9e0a81c25e7c98bd69606c4d` |
| 机器契约 | [servicemonitor-contract.json](https://github.com/fjsxy/monitor-in-hand/releases/download/v0.2.0-rc.4/servicemonitor-contract.json) |
| 契约 SHA256 | `f2bd8e8c62ef535e6659a9c8ab2376cd90dc30039829b096bccf1e41ae3da887` |
| 完整本地报告 | [local-full-8fb5ccb8-attempt3.tar.gz](https://github.com/fjsxy/monitor-in-hand/releases/download/v0.2.0-rc.4/local-full-8fb5ccb8-attempt3.tar.gz) |
| 报告 SHA256 | `f5b31ec65f98d37b771447dfad66984a88a2285f391e040fb7874df1a627ec1d` |

CRD 以本候选 `kubestatemetrics.yaml` 为 schema 基线，SHA256=`5ed40ce718cd67e9b923986b81fa0d79193709ab379c4301e4fa6fdf1b67cd6d`；RBAC SHA256=`33280af80d6c11971f80e0bd5c109eb372c5879330d9972846c484fb2a3cb5bb`。

安装/升级使用候选提供的 `release-verify`、`release-upgrade` 入口：先校验 bundle/source/digest，既有 CRD 只在已知旧/新 spec 下通过 UID/resourceVersion 条件原位升级，再 Helm upgrade/install 同包 `--skip-crds`。旧 CR 升级后保持关闭采集；平台在显式发布时才写入意图。消费新字段前必须读取已部署 served schema 并核对契约能力，可用 `go run ./cmd/release capability --file deployed-crd.yaml`；不支持时在 CR 写入前拒绝。

本地 225 个稳定场景 / 83 个 E2E 已通过（1.19.16 为 41、1.28.15 为 42），覆盖实际发布包安装/升级、受限平台身份、Operator 自动配套和真实 Agent/remote_write 样本。核心 UT native 84.5%、Linux Go 1.22 为 81.6%，各核心包 ≥70%；测试集群已清理。

本次经用户授权采用本地验收；云端 CI 因账单/额度未执行、真实 amd64 Kubernetes 节点未验收的限制保持记录。后续恢复 CI 时单独补跑，不作为本次 #87 关闭或 #28 Operator 依赖满足的等待条件。环境启动失败及缓存恢复证据保留在完整报告。

## 交付行为

平台在 exporter 草稿中表达配套采集意图，明确发布时只写 exporter CR；只读观察 Operator 生成的 ServiceMonitor 和清理结果。移除旧候选中的 SM 生成、独立派发和 Service 算法复制。

## 验收标准

- [ ] 新增草稿/HTTP/Thrift/Kitex/页面字段表达采集开关、interval、scrapeTimeout；归属标签由固定创建用户派生，不能任意覆盖。字段、默认值与校验遵循 [fjsxy/monitor-in-hand#86](https://github.com/fjsxy/monitor-in-hand/issues/86) 的冻结契约。
- [ ] 新 exporter 草稿默认开启，保存草稿零集群写入；明确预览并发布后才将采集意图写入 exporter CR。关闭配套采集也通过 CR 表达，不直接删 SM。
- [ ] 预览、不可变版本和重试固定原 CR 及采集意图；不冻结或生成一份由平台独立下发的 SM YAML，不复制 KSM Service 命名、长名称摘要、labels、端口或分片算法。
- [ ] 预检确认新 CRD schema/Operator 能力、目标 namespace、CR 写权限与观察读取权限；不支持新契约时明确阻止发布，不能让旧 schema 静默裁剪字段。ServiceMonitor 的存在本身不证明 monitor-op 支持。
- [ ] 平台凭据有必要读取权限但无 ServiceMonitor 写权限时，创建、更新、关闭采集和实例删除仍能完成；记录 API 请求证明平台对 SM 的 create/update/patch/delete 均为零。
- [ ] 按 Operator status 的引用/UID 和实际 owner CR UID 回读配套对象；当前 KSM 是 cluster-scoped，配套对象在 spec.targetNamespace。区分读取失败、未知、未收敛、已收敛及观察过期。
- [ ] 页面分别呈现 CR 下发结果、配套收敛/故障及实际采集证据；SM 失败保留运行中的 exporter，Operator 自动修复，平台不提示重写第二份 CR。合法子资源重建 UID 变化不改变 exporter 身份。
- [ ] 平台以已确认 exporter UID 发起删除，由 Operator finalizer 清理；平台只读核验整体资源消失，不误删同名替代对象；挂起、停用、恢复与其他实例隔离保持有效。
- [ ] Ginkgo 覆盖公开 HTTP→真实 RPC→SQLite、错误 schema、写前失权、父 CR 响应丢失/未知 UID及零 SM 写入；真实集群和浏览器覆盖两个实例的发布、参数修改、故障修复、子资源重建、关闭与删除。锁定 Operator 发布的 chart/CRD/镜像 digest。

## 独立演示

以没有 SM 写权限的平台凭据发布两个 exporter；回读 Operator 创建的配套对象并观察其中一个故障后的自动修复；调整参数、关闭采集并删除该实例，验证另一实例不变且平台 SM 写请求始终为零。

## 实施与验收约束

- 本轮只同步任务范围，不领取、不编码。开始或恢复实现前，回读父规格、依赖及评论，按 `docs/agents/implementation.md` 发布并回读五列用例计划，同步本地 `docs/e2e/issue-<编号>.md`；既有稳定 ID 保留，变化的输入和预期先修订再实现。
- Go UT/E2E 使用 Ginkgo，UT 覆盖率至少 70%；主要行为入口为公开 HTTP → 真实 Kitex RPC → SQLite → worker，必要时补直接 RPC、受控 TLS Kubernetes 目标、真实集群和浏览器。
- 每项自行交付对应测试，不把测试集中留给 #30。Makefile 固化重复命令，部署使用 Helm；关键授权、UID/RV 条件写、发送边界和恢复禁重放添加中文注释。
- 证据分别记录本地测试、真实集群、浏览器、远端 CI、清理和 testedCommit；旧 PR #31 的绿色结果不能替代新边界验收。
- 不创建独立采集配置 CRUD 产品入口；平台不直接 create/update/patch/delete 配套 ServiceMonitor，不复制 Operator 的 Service 规则，不把 user 标签用作授权依据。
