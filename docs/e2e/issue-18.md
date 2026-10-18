# #18 E2E 测试用例

实施前 E2E 计划；发布并回读后才能开始实现。

所属任务：[Agent 配置、采集关联与发送管理](https://github.com/fjsxy/monitor-mgr/issues/18)。本地副本：`docs/e2e/issue-18.md`。

以下为计划，不代表执行通过。完整层级、profile、前置、清理与证据要求继承固定 `docs/acceptance/phase1.json`；稳定子例及原定语义保持不变。每例独立建立前置，只操作本 runId 资源，故障 finally 恢复，凭据不得进入报告。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| T01.primary | 固定运行来源 | 按profile准备集群和依赖；回读节点、CRD、controller镜像及构建revision。 本子例输入：按profile准备集群和依赖；回读节点、CRD、controller镜像及构建revision。。 | 均符合锁定版本/摘要；main为1控制面2worker；FULL另有1控制面数据集群；缺锁或不Ready即环境失败。 | #18 |
| T03.primary | 两集群均可部署观测组件 | 从平台Pod分别注册管控/数据集群，在两边各下发KSM和Agent并读取对象。 本子例输入：从平台Pod分别注册管控/数据集群，在两边各下发KSM和Agent并读取对象。。 | 目标UID独立；CR/workload出现在所选API；本地Operator分别调谐；同集群平台也经过注册授权。 | #18 |
| C04.agent-crd | 缺CRD可登记但不能下发 | 在独占数据集群fixture中移除相应CRD，使用有效身份注册/重检后发起下发。 本子例输入：独占 fixture 缺 PrometheusAgent CRD。 | 允许登记，Agent 下发受阻并说明原因 | #18 |
| A01.primary | Agent实际StatefulSet与预设 | 默认运行配置下发后读取Agent、StatefulSet和Pod容器声明。 本子例输入：默认运行配置下发后读取Agent、StatefulSet和Pod容器声明。。 | 每shard正常1副本、paused=false；200m/512Mi/1Gi声明正确；真实工作负载就绪。 | #18 |
| A03.primary | Agent实例与存储身份隔离 | 创建多个Agent及外部Agent，同名配置在不同namespace/集群分别操作。 本子例输入：创建多个Agent及外部Agent，同名配置在不同namespace/集群分别操作。。 | 实例、UID及各卷独立；不越界；外部只读不生成平台发布/重试历史。 | #18 |
| A04.primary | 默认PVC为每卷1Gi | 以默认StorageClass和平台默认存储发布Agent；读取PVC请求、绑定及Pod挂载。 本子例输入：以默认StorageClass和平台默认存储发布Agent；读取PVC请求、绑定及Pod挂载。。 | PVC每卷请求1Gi、RWO、实际Bound并被目标Pod挂载；不沿用旧fixture的64Mi。 | #18 |
| A05.primary | 无默认StorageClass阻止PVC发布 | 在独占数据集群fixture取消默认StorageClass，使用默认PVC配置预览/发布。 本子例输入：在独占数据集群fixture取消默认StorageClass，使用默认PVC配置预览/发布。。 | 明确依赖受阻且不创建Agent负载；原草稿保留；恢复默认class后明确重试可继续。 | #18 |
| A10.initial-capacity | 容量与模式变更必须新实例 | 首次创建前自定义容量；创建后尝试改容量/存储模式/name/namespace，再按新实例重建。 本子例输入：首次创建前使用非默认合法容量。 | 新 PVC 请求为该容量 | #18 |
| A10.fixed-capacity | 容量与模式变更必须新实例 | 首次创建前自定义容量；创建后尝试改容量/存储模式/name/namespace，再按新实例重建。 本子例输入：创建成功后修改容量。 | 后端拒绝，不虚报旧 PVC 扩容成功 | #18 |
| A10.fixed-identity | 容量与模式变更必须新实例 | 首次创建前自定义容量；创建后尝试改容量/存储模式/name/namespace，再按新实例重建。 本子例输入：创建成功后修改 namespace/name。 | 后端拒绝，不迁移原资源 | #18 |
| A11.servicemonitor-none-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ServiceMonitor：object=none，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.servicemonitor-none-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ServiceMonitor：object=none，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.servicemonitor-none-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ServiceMonitor：object=none，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.servicemonitor-all-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ServiceMonitor：object=all，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.servicemonitor-all-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ServiceMonitor：object=all，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.servicemonitor-all-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ServiceMonitor：object=all，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.servicemonitor-labels-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ServiceMonitor：object=labels，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.servicemonitor-labels-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ServiceMonitor：object=labels，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.servicemonitor-labels-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ServiceMonitor：object=labels，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.podmonitor-none-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：PodMonitor：object=none，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.podmonitor-none-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：PodMonitor：object=none，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.podmonitor-none-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：PodMonitor：object=none，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.podmonitor-all-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：PodMonitor：object=all，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.podmonitor-all-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：PodMonitor：object=all，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.podmonitor-all-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：PodMonitor：object=all，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.podmonitor-labels-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：PodMonitor：object=labels，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.podmonitor-labels-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：PodMonitor：object=labels，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.podmonitor-labels-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：PodMonitor：object=labels，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.probe-none-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：Probe：object=none，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.probe-none-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：Probe：object=none，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.probe-none-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：Probe：object=none，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.probe-all-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：Probe：object=all，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.probe-all-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：Probe：object=all，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.probe-all-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：Probe：object=all，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.probe-labels-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：Probe：object=labels，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.probe-labels-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：Probe：object=labels，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.probe-labels-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：Probe：object=labels，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.scrapeconfig-none-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ScrapeConfig：object=none，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.scrapeconfig-none-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ScrapeConfig：object=none，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.scrapeconfig-none-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ScrapeConfig：object=none，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.scrapeconfig-all-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ScrapeConfig：object=all，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.scrapeconfig-all-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ScrapeConfig：object=all，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.scrapeconfig-all-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ScrapeConfig：object=all，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.scrapeconfig-labels-own | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ScrapeConfig：object=labels，namespace=own；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.scrapeconfig-labels-all | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ScrapeConfig：object=labels，namespace=all；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A11.scrapeconfig-labels-labels | 四类采集关联及动态变化 | 对ServiceMonitor/PodMonitor/Probe/ScrapeConfig分别配置对象与namespace模式；增删匹配CR并预览。 本子例输入：ScrapeConfig：object=labels，namespace=labels；增删/改标签并检查独立预览。 | none 对象选择映射省略，all 映射 {}，labels 映射条件；own namespace 省略，all 为 {}，labels 为 Namespace 条件；零匹配有效且不是 Pod 数或采集成功，读取失败不是空集 | #18 |
| A12.http-none | remoteWrite与认证TLS正确引用 | 配置HTTP/HTTPS、多endpoint、BasicAuth/Bearer及TLS引用；读Agent和实际生成配置。 本子例输入：HTTP endpoint，无认证。 | 合法 URL 映射正确，不能声称样本已到达 | #18 |
| A12.https-basic | remoteWrite与认证TLS正确引用 | 配置HTTP/HTTPS、多endpoint、BasicAuth/Bearer及TLS引用；读Agent和实际生成配置。 本子例输入：HTTPS endpoint，BasicAuth username/password Secret key 引用。 | 引用与受控生成配置正确，平台不保存 Secret 内容 | #18 |
| A12.https-bearer | remoteWrite与认证TLS正确引用 | 配置HTTP/HTTPS、多endpoint、BasicAuth/Bearer及TLS引用；读Agent和实际生成配置。 本子例输入：HTTPS endpoint，authorization Bearer credentials Secret 引用。 | 不退化为明文 token 字段；证据脱敏 | #18 |
| A12.tls-ca | remoteWrite与认证TLS正确引用 | 配置HTTP/HTTPS、多endpoint、BasicAuth/Bearer及TLS引用；读Agent和实际生成配置。 本子例输入：HTTPS endpoint，Secret CA 引用。 | 保持 TLS 校验开启且引用准确 | #18 |
| A12.tls-client | remoteWrite与认证TLS正确引用 | 配置HTTP/HTTPS、多endpoint、BasicAuth/Bearer及TLS引用；读Agent和实际生成配置。 本子例输入：HTTPS endpoint，成对客户端证书/私钥和 serverName。 | 配对及 serverName 映射正确，秘密不泄露 | #18 |
| A12.multiple | remoteWrite与认证TLS正确引用 | 配置HTTP/HTTPS、多endpoint、BasicAuth/Bearer及TLS引用；读Agent和实际生成配置。 本子例输入：多个 endpoint，分别设置唯一可选 name、合法 remoteTimeout。 | 全部受支持字段保持，重复 name 拒绝；不开放 queue/headers/writeRelabel 等高级透传 | #18 |
| A13.missing-secret | 缺失Secret key或SA不能下发 | 参数化缺Secret、key和ServiceAccount后发起预览/发布，再修复相应fixture。 本子例输入：配置引用不存在的 Secret。 | 依赖错误，阻止发布 | #18 |
| A13.missing-key | 缺失Secret key或SA不能下发 | 参数化缺Secret、key和ServiceAccount后发起预览/发布，再修复相应fixture。 本子例输入：Secret 存在但所需 key 缺失。 | 明确 key 错误，不能 optional 绕过 | #18 |
| A13.forbidden-secret | 缺失Secret key或SA不能下发 | 参数化缺Secret、key和ServiceAccount后发起预览/发布，再修复相应fixture。 本子例输入：Secret 存在但访问被拒绝。 | 不能当作依赖可用或空值，不泄露内容 | #18 |
| A13.missing-sa | 缺失Secret key或SA不能下发 | 参数化缺Secret、key和ServiceAccount后发起预览/发布，再修复相应fixture。 本子例输入：ServiceAccount 不存在。 | 阻止发布，平台不自动补建 | #18 |
| A13.insufficient-sa | 缺失Secret key或SA不能下发 | 参数化缺Secret、key和ServiceAccount后发起预览/发布，再修复相应fixture。 本子例输入：已有 ServiceAccount 明确缺少所需采集权限。 | 显示原因并阻止发布，不自动补 RBAC | #18 |
| R03.agent-paused | 缺status与合法零值分开 | 参数化无status、字段合法省略、有效挂起/停用/零匹配及已删除。 本子例输入：外部只读 Agent paused=true，仍有 Pod。 | 不能声称 Pod 已停止 | #18 |
| R07.pvc | 辅助来源新鲜度独立 | 令CR持续成功而PVC/关联预览读取被拒绝，再修复权限。 本子例输入：CR 成功，PVC 读取失败。 | PVC 历史/错误独立，CR 成功不能刷新 PVC 时间 | #18 |
| R07.association | 辅助来源新鲜度独立 | 令CR持续成功而PVC/关联预览读取被拒绝，再修复权限。 本子例输入：CR 成功，采集关联预览读取失败。 | 关联未知不是零匹配，恢复按来源更新时间 | #18 |
| U02.primary | 统一添加与名称行为 | 由单一添加入口按类型选择两类组件；尝试中文/空名、保存、发布及复制。 本子例输入：由单一添加入口按类型选择两类组件；尝试中文/空名、保存、发布及复制。。 | 展示exporter/collector与具体组件；名称直接成为CR name且不自动生成；创建后锁定，复制须新身份。 | #18 |
| U06.primary | YAML预览及确认后导航 | 预览两类CR的左右diff（含数组/selector），确认下发并查看运行信息与尝试历史。 本子例输入：预览两类CR的左右diff（含数组/selector），确认下发并查看运行信息与尝试历史。。 | YAML可解析且无孤立对象项横杠；确认立即进入运行信息，已接受/CR写入/运行分开，实际CR与预览语义一致。 | #18 |
| F01.absent | Thrift往返保留显式配置语义 | 用实际锁定生成参数往返编码外层缺失、空对象、空集合、显式模式及未知枚举。 本子例输入：实际生成类型的缺失 optional 外层配置。 | 缺失不意外变为空对象 | #18 |
| F01.empty-object | Thrift往返保留显式配置语义 | 用实际锁定生成参数往返编码外层缺失、空对象、空集合、显式模式及未知枚举。 本子例输入：显式全选模式产生空 selector 对象。 | {} 与省略保持不同语义 | #18 |
| F01.empty-list | Thrift往返保留显式配置语义 | 用实际锁定生成参数往返编码外层缺失、空对象、空集合、显式模式及未知枚举。 本子例输入：合法显式清空集合与模式配套。 | 清空语义保持，非法组合拒绝 | #18 |
| F01.unknown-mode | Thrift往返保留显式配置语义 | 用实际锁定生成参数往返编码外层缺失、空对象、空集合、显式模式及未知枚举。 本子例输入：未知枚举或缺失必需显式 mode。 | 拒绝，不默认退化为全选或不选 | #18 |
| F03.selector | 后端字段校验及稳定错误 | 直接发送非法quantity/正则/selector/URL/字段组合和未知字段，绕过页面禁用。 本子例输入：分别经 HTTP/直接 RPC 提交 非法 LabelSelector 或不相容模式。 | 稳定字段级错误与 HTTP 类别，CR 无写入，敏感值不泄漏 | #18 |
| F03.url | 后端字段校验及稳定错误 | 直接发送非法quantity/正则/selector/URL/字段组合和未知字段，绕过页面禁用。 本子例输入：分别经 HTTP/直接 RPC 提交 非法 URL、零 remoteWrite endpoint 或重复 name。 | 稳定字段级错误与 HTTP 类别，CR 无写入，敏感值不泄漏 | #18 |
| F03.tls-pair | 后端字段校验及稳定错误 | 直接发送非法quantity/正则/selector/URL/字段组合和未知字段，绕过页面禁用。 本子例输入：分别经 HTTP/直接 RPC 提交 未配对客户端证书/私钥或非法认证组合。 | 稳定字段级错误与 HTTP 类别，CR 无写入，敏感值不泄漏 | #18 |

## 共享安全接缝补充回归

以下是共享引擎迁移与敏感读链回归，不增加或替代固定69个责任子例；最终#21联合回归继续包含这些边界。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| REG18-SHARED-001 | 旧KSM预览升级保留安全判定 | 固定v4带账号/凭据/草稿/版本/预览/保护库；启动新版本升级，使用原会话和原previewId确认；另分别修改spec及仅RV/status后确认。 | ID/关系及原预览保留；相同spec或仅RV/status变化可继续，spec变更拒绝；target_spec以带域隔离的HMAC保存，不存原目标敏感值。 | #18 |
| REG18-SHARED-002 | 身份迁移区分CR scope | 从带历史v4库升级，创建KSM跨部署namespace同名及Agent跨CR namespace同名；对同namespace同名重试；注入迁移失败后检查原库和快照。 | KSM同集群同名冲突；Agent跨namespace成功、同namespace冲突；历史FK不变；失败回滚并保留原库/恢复依据。 | #18 |
| REG18-SHARED-003 | 目标敏感字段不反向进入平台 | 在外部和已纳管漂移CR加入inline认证、URL userinfo、敏感last-applied，读取发现/预览/运行及历史，检查DB/HTTP/直接RPC/YAML/日志；敏感值变化后再确认，最后明确覆盖。 | 产品持久化和输出无秘密；敏感值变化使旧预览失效，仅RV/status变化不失效；更新保留原metadata/status/finalizers，不以脱敏改写真实资源。 | #18 |

## Operator 启动能力补充回归

以下两条同样不增加固定69个owned责任子例。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| REG18-CAP-004 | 后补Agent CRD/RBAC不能冒充controller已启动 | Operator启动时缺Agent支持；随后恢复CRD/权限而不重启，配置SC None的Agent并预览/发布；再重启原固定Operator。 | 仅当前CRD/SAR成功不能放行；原Pod缺Agent ready=1时阻止，重启后按原Pod身份与真实Agent ready证据重新核验。 | #18 |
| REG18-CAP-005 | 后补ScrapeConfig支持不能冒充informer已启动 | Operator启动时缺ScrapeConfig API/权限；随后恢复但不重启，配置非None ScrapeConfig并预览；再重启固定Operator。 | Agent ready本身不足；同一原Pod缺SC资源endpoint请求证据时阻止，重启后Agent ready及SC informer证据同时满足才放行。 | #18 |

## 输入与依赖范围补充回归

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| REG18-CAP-004.single-dash-namespace | Go flag单横线namespace范围生效 | 固定Operator实际Pod使用 `-namespaces=other`，Agent位于不同namespace且当前API/SAR可用，发起预览。 | 阻止发布，不将单横线当作全namespace；不创建Agent。 | #18 |
| REG18-CAP-004.single-dash-selector | Go flag单横线实例selector生效 | 实际Pod使用 `-prometheus-instance-selector=team=other`，平台CR不匹配，发起预览。 | 明确范围受阻，不修改CR labels绕过。 | #18 |
| REG18-CAP-004.override | Operator访问身份/目标override不能按Pod SA推断 | 实际Pod带 `--as` 或访问API/TLS override，模板及metrics可读，发起预览。 | 无法确认真实Operator身份/目标时阻止，不把Pod SA的SAR当其真实权限。 | #18 |
| REG18-CAP-004.old-rs | 旧RS的绿色Pod不能代表新template | 同Deployment UID旧RS Pod的实际SA/args与新template不符，而旧Podmetrics绿色，发起预览。 | 阻止，原Pod/template绑定不能被绿色指标绕过。 | #18 |
| REG18-CAP-004.none-short-circuit | None规则不依赖未关联的采集API | 四类None，撤掉ScrapeConfig API/读取权限，保留已运行Agent controller与Agent CR能力后预览。 | 不因SC依赖被拒绝；None不读取全namespace/SC API，不虚称SC informer已验证。 | #18 |
| REG18-VALID-006 | Prometheus Duration校验含范围上限 | 经HTTP和直接RPC分别提交合法 `1y2w3d4h5m6s7ms`、重复/逆序单位及超长年份 `999999999999999999999999y`。 | 合法组合保持；重复/逆序/溢出稳定字段拒绝且CR无写，不退化为Go duration。 | #18 |

## 草稿与运行来源隔离补充回归

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| REG18-SOURCE-007 | 草稿关联预览不得覆盖已部署运行来源 | 发布规则A并刷新实际CR关联；保存未发布规则B并预览，再读取运行来源；预览过程中另保存新修订。浏览器分别查看配置区和运行区并继续编辑。 | B仅返回所请求draftRevision的预览，运行来源及时间仍来自A；修订并发变化返回冲突且不落库；UI草稿预览独立保存并标明过期，轮询不覆盖预览，运行页不展示未发布B。 | #18 |

## 固定字段与任意headers脱敏补充回归

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| REG18-FIXED-008 | 首次发布在途保存的草稿不能绕过存储固定字段 | 以1Gi预览并受理首次发布，在实际派发/确认前另保存2Gi草稿；首次写入成功或可能已发后用新草稿预览/确认，并从旧预览/固定版本恢复派发。 | 预览、受理和派发均重新对照已确认或可能已发的冻结版本；原PVC容量不可被改写，目标实际PVC仍1Gi；本例复现容量链，统一fixed-field接缝继续供后续存储模式规则复用，单点SaveDraft校验不构成唯一防线。 | #18 |
| REG18-HEADERS-009 | remoteWrite任意header值不进入平台读模型 | 外部及已纳管目标加入Authorization、X-API-Key及任意自定义header值，读取发现/运行/左右YAML/历史并检查DB/日志；仅修改header值后确认旧预览，最后明确覆盖。 | headers上下文全部值脱敏，HMAC按原配置识别变化；秘密不持久化或输出；Secret refs保持可读，更新不以脱敏覆盖目标metadata/status/finalizers。 | #18 |

## Operator 参数解析边界补充回归

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| REG18-CAP-010.positional | Go flag首个非参数终止不能误扩大范围 | 实际Pod与模板均为 `--namespaces=other positional --namespaces=`，Agent在monitoring；原Podmetrics与当前SAR均可读，预览/发布。 | 明确unsupported；不继续解析positional后的伪范围，不写Agent，不将解析失败当作全范围。 | #18 |
| REG18-CAP-010.terminator | 双横线终止不能误扩大范围 | 实际Pod与模板均为 `--namespaces=other -- --namespaces=`，Agent在monitoring，发起预览。 | 明确unsupported；不把终止后的参数作为实际范围，不写Agent。 | #18 |
| REG18-CAP-010.duplicate-scope | 重复集合flag不得按last-wins丢失deny范围 | 实际Pod与模板均为 `--deny-namespaces=monitoring --deny-namespaces=other`，Agent在monitoring，发起预览。 | 明确unsupported；固定源码StringSet累加语义不能被普通map覆盖，不写Agent。 | #18 |
| REG18-CAP-010.boolean-arity | Go布尔flag的空格值不能被当作值消费 | 实际Pod与模板均为 `--deny-namespaces=monitoring --kubelet-endpoints false --deny-namespaces=other`，Agent在monitoring；发起预览。 | 明确unsupported；false是Go flag停止点，不能继续采用后面的伪deny范围；不写Agent。 | #18 |
| REG18-CAP-010.known-scope-space | 已知scope字符串的空格形式仍可核验 | 实际Pod和模板均用 `-namespaces monitoring -prometheus-instance-selector ''`，Agent在monitoring；固定controller与依赖可用后预览。 | 正常预览，保留已知字符串参数的单/双横线及空格形式，不把安全拒绝退化为拒绝所有有效参数。 | #18 |
| REG18-CAP-010.env-expansion | Pod声明中的变量引用不能冒充进程实参 | 实际Pod和模板args为 `--deny-namespaces=$(BLOCKED_NS)`，容器env中BLOCKED_NS=monitoring；原Podcontroller/metrics正常，在monitoring预览。 | 明确unsupported；平台不猜测Kubelet展开后的scope，也不读取Secret env值来扩大能力，不写Agent。 | #18 |

## 浏览器账号规则补充回归

此条为真实浏览器B补充，可嵌入U02独立记录；固定69个owned责任子例及20个Go补充回归保持原计数。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| REG18-PATTERN-011 | 初始化、登录与新账号的账号规则在Chromium实际生效 | 在U02真实Chromium中读取初始化/登录和新账号input.pattern，以JavaScript `v` flag实际编译；逐项填写含点、横线、下划线的合法账号及中文、空格、斜线等非法账号，检查patternMismatch并记录console。 | 两处共用明确安全写法；合法账号无patternMismatch，非法字符被拒绝；console无非法正则语法错误，独立输出此B补充证据，不把它计为额外Go回归或owned子例。 | #18 |

## LabelSelector CR 输出补充回归

以下6条为独立Q补充：经真实HTTP、生成Kitex RPC、SQLite与受控TLS Kubernetes API验证完整发布链，API按固定CRD省略非nullable的null字段；真实集群四类关联继续由A11完整矩阵覆盖。原69个owned责任子例保持不变；补充回归共27个唯一ID，其中26个Q，PATTERN-011为B，SOURCE-007同时执行Q/B，共28个补充执行键。

关键：平台typed模型与Thrift往返继续区分缺失和显式空集合；仅CR输出边界省略nil的表达式values，显式空数组继续保留。不得全局删除null、折叠外层selector的省略/{}，或改写未知字段语义。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| REG18-SELECTOR-012.exists-nil | Exists缺失values不产生API裁剪后的身份误判 | 经HTTP/RPC保存并预览含Exists且values缺失的Labels规则，确认发布；受控API按固定CRD省略null，回读实际CR及操作终态。并检查外层None/All映射。 | CR表达式省略values，发布成功且无IDENTITY_UNCERTAIN；typed缺失语义保留；None仍省略、All仍{}，无额外写入或不安全重试。 | #18 |
| REG18-SELECTOR-012.exists-empty | Exists显式空values保持已约定的空集合语义 | 经HTTP/RPC保存values=[]的Exists规则，检查生成类型、DB草稿、预览YAML及发布后的实际CR。 | typed/RPC/DB中的显式空集合不变，CR保持values: []且发布成功；不把合法空数组改成null或与外层selector省略混同。 | #18 |
| REG18-SELECTOR-012.not-exists-nil | DoesNotExist缺失values不产生身份误判 | 经HTTP/RPC保存并预览含DoesNotExist且values缺失的Labels规则，确认发布；API按固定CRD省略null，再回读操作。 | CR表达式省略values，实际CR与冻结版本一致，发布成功且无IDENTITY_UNCERTAIN；不放宽未知字段或依赖校验。 | #18 |
| REG18-SELECTOR-012.not-exists-empty | DoesNotExist显式空values保持空集合 | 经HTTP/RPC保存values=[]的DoesNotExist规则，检查RPC/DB/预览并完成发布回读。 | 显式[]在typed和CR中保持，发布成功；没有null字段和重复创建，不改变外层None/All。 | #18 |
| REG18-SELECTOR-012.in | In的非空values不可被省略规则吞掉 | 经HTTP/RPC保存In及两个合法values，预览、发布、回读实际CR与冻结版本。 | 非空values内容及顺序保持，正确匹配并发布成功；空值规范化不能改变集合选择含义。 | #18 |
| REG18-SELECTOR-012.not-in | NotIn的非空values不可被省略规则吞掉 | 经HTTP/RPC保存NotIn及两个合法values，预览、发布、回读实际CR与冻结版本。 | 非空values完整保留，正确排除并发布成功；未越过当前集群授权或原UID校验。 | #18 |
