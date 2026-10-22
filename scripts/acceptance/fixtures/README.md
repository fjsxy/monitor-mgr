# 故障代理fixture

`fault_proxy.py` 固定来源为本轮仓库外机制原型，原文件 SHA256 `0212f165296110cb21e679b38b17cf41939117e21104fe356e6a6b389dfce8da`，复制时未修改机制。它只供独占验收，由 `ksm_proxy.py` 生成本例mTLS材料、限制控制端为Pod loopback，并原样转发真实API请求，不重试。

`pause_before_send` 暂停的是proxy到backend的发送；平台到达proxy前已经取得派发资格并持久化possibly-sent。它不能证明产品未派发，也不能替代取消/未发恢复用例的应用事务屏障。`drop_after_upstream` 仅在真实上游2xx完成后丢响应，非2xx直接转发。

SSR看到backend固定管理员。身份、TLS、RBAC正向验收必须走真实直连凭据。私有材料仅在内存、0600投影或受限临时目录；日志白名单不含body、凭据或Secret值。fixture源码机制自测与真实K case分别记录，不能互相替代。

#19 在原机制上追加受认证的 `POST /faults`，精确字段为 `readMode`（`all`/`aux`/`none`）与 `rejectWritePath`（空值或单一无 query API 路径）。读取故障用于独立来源/重启历史；写拒绝仅作用于该路径的非 dryRun 变更，用于验证预览成功而实际发布失败时不提升当前版本。`/reset` 同时清除全部故障。控制材料只驻私有 fixture，不能进入页面或 trace。`make fault-proxy-check` 经真实 loopback HTTP 验证控制认证、严格字段、精确路径、dryRun 排除及恢复；这不代表 K/mTLS 验收通过。原来源 hash 保留，新版本由 Git blob 与执行收据绑定。

#28 补充可选规则字段 `ssarResource`，当前仅允许 `kubestatemetrics` 且必须使用 `POST /apis/authorization.k8s.io/v1/selfsubjectaccessreviews`。代理只解析合法 SSAR 的 `spec.resourceAttributes.group/resource` 来选择 KSM 预检；ServiceMonitor 受理预检、畸形正文与 dryRun 不消费规则，正文不写入事件或公开状态。未传该字段的旧规则保持原语义。`make ut-runtime-repairs` 通过 UT-18 中的真实 loopback HTTP 核验规则选择与旧暂停规则；TLS/真实集群写前授权仍由 E2E-08 验证。
