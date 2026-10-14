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
