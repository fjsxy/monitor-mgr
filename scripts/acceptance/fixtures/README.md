# 故障代理fixture

`fault_proxy.py` 固定来源为本轮仓库外机制原型，原文件 SHA256 `0212f165296110cb21e679b38b17cf41939117e21104fe356e6a6b389dfce8da`，复制时未修改机制。它只供独占验收，由 `ksm_proxy.py` 生成本例mTLS材料、限制控制端为Pod loopback，并原样转发真实API请求，不重试。

`pause_before_send` 暂停的是proxy到backend的发送；平台到达proxy前已经取得派发资格并持久化possibly-sent。它不能证明产品未派发，也不能替代取消/未发恢复用例的应用事务屏障。`drop_after_upstream` 仅在真实上游2xx完成后丢响应，非2xx直接转发。

SSR看到backend固定管理员。身份、TLS、RBAC正向验收必须走真实直连凭据。私有材料仅在内存、0600投影或受限临时目录；日志白名单不含body、凭据或Secret值。fixture源码机制自测与真实K case分别记录，不能互相替代。
