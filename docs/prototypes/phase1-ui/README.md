# 一期关键页面与配置下发原型

本原型回答：用户能否通过页面完成选集群、编辑配置、保存草稿、明确下发、查看组件运行状态，并理解权限、漂移、失败与删除？

使用纯 HTML/CSS/JavaScript，在同一路径通过 `?variant=A|B|C` 切换三个结构不同的界面：实例工作台、分步下发、表格与抽屉。全部数据是假数据，修改只保存在当前页面内存中；不连接 HTTP/Kitex 业务服务、数据库、Kubernetes 或通知服务，刷新页面会重置。此代码只作为原型来源，不进入主线产品。

## 运行

```sh
python3 docs/prototypes/serve-phase1-ui-prototype.py
```

默认打开 `http://127.0.0.1:8765/phase1-ui/?variant=A`。也可直接打开 `index.html`。

底部原型栏可切换布局、模拟身份和故障场景。左右方向键切换布局；编辑输入时不会拦截方向键。所有布局使用相同模拟数据和状态，切换布局不会丢失当前操作。

## 评审范围

- 管理员与集群所属用户的集群列表、登记、归属与账号操作。
- KSM 和 Agent 多实例、外部 CR 只读、结构化表单、不可变字段、只读 YAML 和差异预览。
- 保存与下发分开，已接受的版本冻结；错误、过期草稿、漂移、重复点击、在途任务与取消边界。
- 运行摘要、可用数量、当前未知、历史、过期及重启；不包含 Operator 状态报告或 generation 页面。
- 删除、清理受阻、PVC 独立结果、保留策略先行生效和新实例恢复。

表单反馈和依赖检查均为演示。真实后端的 Go regexp、Kubernetes 名称/LabelSelector/quantity 校验、服务端权限、UID/resourceVersion 条件写入、PVC 自动删除能力和执行持久化仍需实现与验收。

## 已确认决策来源

- [集群注册、账号与归属权限生命周期](https://github.com/fjsxy/monitor-mgr/issues/5#issuecomment-6097118451)
- [KSM 实例与配置编辑契约](https://github.com/fjsxy/monitor-mgr/issues/6#issuecomment-6137938413)
- [Agent 实例与关联采集配置契约](https://github.com/fjsxy/monitor-mgr/issues/7#issuecomment-6139516950)
- [配置发布、漂移与删除状态机](https://github.com/fjsxy/monitor-mgr/issues/8#issuecomment-6142918240)
- [状态同步、过期标记与故障展示契约](https://github.com/fjsxy/monitor-mgr/issues/9#issuecomment-6149704214)
- [待实际评审的原型任务](https://github.com/fjsxy/monitor-mgr/issues/10)

原型的提供不代表用户已经评审，也不代表平台实现或真实集群验收通过。

## 浏览器检查与评审入口

2026-10-14 在 Chrome 中检查三个布局及主要模拟路径：保存不写集群、在途任务冻结版本但可继续保存、原版本重试与尝试历史、外部 KSM/Agent 只读、所属用户可见范围、无 status/断联、漂移覆盖与草稿冲突、selectors 省略与 `{}`、关联预览读取失败、PVC 策略先行生效、结果待确认与首次创建身份保护、删除受阻/Retain PVC、集群受限登记和最后管理员限制。布局切换保留编辑，输入框中的方向键不触发切换。

JavaScript 与静态启动脚本通过语法检查；未运行产品测试、远程 CI 或真实集群验收。浏览器检查只证明原型可以评审。

初版三个布局的截图：[A · 实例工作台](screenshots/variant-A.jpg)、[B · 分步下发](screenshots/variant-B.jpg)、[C · 表格与抽屉](screenshots/variant-C.jpg)。

建议先体验修改资源 → 保存草稿 → 预览下发 → 确认下发，再从底部场景栏查看故障路径。已收到用户对 C 布局的页面评审意见并逐条调整；最终布局选型仍待确定。

## 2026-10-14 页面评论调整

- Comment 1：列表统一展示「部署命名空间」，不显示 `targetNamespace` 或 `namespace` 字段名。
- Comment 2：实例类型使用 `exporter` / `collector`；分别列出具体组件 kube-state-metrics / Prometheus Agent。CR API 名称保持原样。
- Comment 3：列表和共享运行信息使用「运行状态」文案。
- Comment 4：列表行新增「删除」，复用已有确认及删除流程；外部只读实例、权限不足或未结束任务仍禁止删除。
- Comment 5：各布局只保留统一「添加」入口，进入独立添加页面，先选实例类型，再选具体组件并填写配置。列表不为每个组件增加按钮。

在客户端内置浏览器中验证了两类组件的添加路径、原 CR API 标识、列表删除的准确目标、外部及在途任务删除限制，并按评论中的 1137×954 视口确认列表没有横向溢出。类别选择本身不创建实例；进入具体组件配置后只创建内存中的未部署草稿。

本轮截图：[更新后的 C 列表](screenshots/variant-C-review2.jpg)、[统一添加页面](screenshots/add-instance.jpg)。原型静态服务使用 `Cache-Control: no-store`，防止评审时继续加载旧脚本。

## 2026-10-14 第二轮页面评论调整

本轮落实集群管理的组件分组评论，以及后续 9 条实例和抽屉评论：

- 组件能力分为「允许」和「不允许」，列出具体组件名称；空组显示「无」。沿用原有能力检查。
- 统计改为「平台纳管实例 / 平台未纳管实例」；未纳管实例继续只读。
- 顶部明确展示「账号：admin · 角色：平台管理员」，解决示例账号名与角色同名的重复。
- 列表移除部署命名空间，列标题改为「类型与组件」。部署配置中的 namespace 选择保留。
- 表单「名称」直接作为 CR 的 metadata.name；不支持中文，按 DNS subdomain 格式演示校验，不再生成 CR name 或提供另一显示名称。新建和复制草稿的名称为空，必须填写；下发开始后保护名称，创建后固定，改名走新实例。
- collectors 将 pods、nodes、deployments、statefulsets、daemonsets、services 等常用项前置。勾选直接更新选择与草稿状态，不再重建页面；后台重绘保留抽屉、选项区及页面滚动位置。
- namespace 名称改为下拉多选，来自所选集群的模拟 namespace 列表。保留名称正则输入，匹配数量随多选即时更新；无法访问集群时保留原选择并说明读取不可用。
- 下发预览改为左右完整 CR YAML 行 diff，带行号与红删绿增高亮。首次创建左侧说明尚无 CR，漂移覆盖中的额外 spec 字段在左侧标为删除。
- 运行信息显示已观察到的部署模式：KSM 的 Deployment / StatefulSetSharded / DaemonSetSharded，Agent 的 StatefulSet 及 shards；当前未知时明确标为上次观测。未下发草稿不改变此显示。

本轮命名决策取代前置 KSM / Agent 契约中的「自动生成 CR name 与可修改显示名称分离」；名称与 UID 的稳定绑定、只管理自身创建资源等边界继续有效。

客户端内置浏览器已检查中文名称拒绝、空名称不可保存、两类组件名称与 CR YAML 一致、下发期间名称锁定、namespace 多选映射、常用 collector 顺序、连续勾选与后台刷新滚动位置保持、新建 / 修改 / 漂移覆盖 YAML diff，以及未下发草稿与实际部署模式分开显示。原型仍无真实集群请求；namespace 和 CR 均为模拟数据，真实获取与服务端校验留待产品实现。

截图：[组件能力分组](screenshots/cluster-capabilities-review3.jpg)、[更新后的列表](screenshots/variant-C-review3.jpg)、[namespace 多选](screenshots/namespace-picker-review3.jpg)、[左右 YAML diff](screenshots/yaml-diff-review3.jpg)。

## 2026-10-14 第三轮页面评论调整（6 条）

- 列表第一列改为「名称/命名空间」，在名称下展示已部署配置的 namespace；未部署草稿使用已保存的 namespace。不再放在组件列。
- 确认下发后立即进入该实例的「运行信息」，C 布局直接打开抽屉；重试也采用相同导航。任务接受和 CR 已写入仍分别表达。
- 集群组件能力移除允许 / 不允许及空组文字，只展示组件标签：可下发为绿色，不可下发为灰色，悬停可查看能力含义；原有下发检查保持。
- 集群选择框内合并集群名、Kubernetes 版本和归属，不再在框外重复展示元信息。
- 列表「版本」只展示最近确认写入的发布版本，不展示最新草稿版本。点击打开对应版本来源草稿的只读配置快照，显示发布版本和来源草稿版本；当前草稿及未保存修改保留，可明确返回当前草稿。快照在接受发布时另存，成功写入后才更新列表版本；失败不会提升版本。未部署、未纳管、已删除或资源身份无法确认时不虚构可点击版本。
- YAML 序列对象使用常见的 `- url: ...` 排版，后续字段保持缩进，不再把对象项的 `-` 单独放一行。仍保留数组、嵌套对象、空 `{}` / `[]` 和省略字段的语义。

此轮取代上轮「列表省略命名空间」和「组件能力分组文字」的展示选择；其他命名、类型、权限和身份边界继续有效。

在客户端内置浏览器验证：未保存草稿 CPU 为 300m 时，点击当前发布 v2 仍显示 100m 的只读配置，返回草稿后 300m 编辑保留；下发接受时版本仍为 v2，写入后变为 v3，其配置为 300m、来源草稿 v4；写入失败时保持 v2。确认下发 / 重试均直接显示运行信息。1137×954 下列表 clientWidth / scrollWidth 均为 1065，选择框完整显示集群版本和归属；组件标签按能力为绿 / 灰，页面没有控制台错误。

从实际渲染的 Agent 左右 diff 提取 YAML，用系统 Ruby YAML parser 验证两个 remoteWrite endpoint、嵌套 TLS、PVC accessModes 数组、空 ServiceMonitor selector 与省略 PodMonitor selector 均正确，且没有孤立数组对象标记。JavaScript 语法及 diff 格式检查通过；未新增依赖、产品测试、远程 CI 或真实集群调用。

截图：[新版 C 列表](screenshots/variant-C-review4.jpg)、[绿 / 灰组件能力](screenshots/cluster-capabilities-review4.jpg)、[对应版本配置](screenshots/published-config-review4.jpg)、[YAML 序列排版](screenshots/yaml-sequences-review4.jpg)。为保留用户旧页内存中的测试实例，新版在独立浏览器标签页验证；原评审标签页未刷新或关闭。
