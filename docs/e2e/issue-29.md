# #29 历史 exporter 显式启用与旧 ServiceMonitor 迁移测试计划

2026-10-26 修订。基于 integration `28535ad8762299c0708699a1e7b0f1ab6f08c95b`，替代旧平台双 CR 写入预期。保留原 I29 ID；计划不表示已执行。主要接缝为已确认的公开 HTTP → 真实 Kitex RPC → SQLite → worker，辅以直接 RPC、受控 TLS Kubernetes API、独立真实旧 schema、真实 Helm/PVC 与唯一 Playwright 流程。固定 18 UT / 10 E2E 不增加顶层用例。

| 用例编号 | 验证目的 | 验证步骤 | 预期结果 | 所属 Issue 编号 |
| --- | --- | --- | --- | --- |
| I29-U01 | 历史升级、补身份、确认与轮询不启用采集 | 公开HTTP建立KSM与旧发布，停平台裁真实v10并移除实际旧CR新字段；升级、补身份、确认、轮询/重启 | 原父UID/旧版本/任务/frozen bytes不变；父spec未加serviceMonitor；平台父/SM业务写增量0 | #29 |
| I29-U02 | 新明确发布才启用，沿用原creator和父UID | 另一管理员对可靠creator历史KSM明确保存enabled=true、预览、确认发布；读新旧版本和请求 | 新versionID仅一份父KSM并含采集意图与原creator标签；原UID条件PUT；平台SM四类写0；旧版本仍原集合 | #29 |
| I29-U03 | 权限允许不能伪造未知creator | 真v10删除CREATE_INSTANCE证据、升级并允许管理；HTTP和直接RPC尝试enabled=true预览/发布 | creator仍空，依赖user标签明确拒绝；无owner/currentActor回退；父/SM写增量0 | #29 |
| I29-U04.failed | 旧单CR确定失败仅重试原冻结资源 | 真v10 FAILED单CR升级；编辑新草稿；缺SM CRD时明确retry | 仅旧父冻结版本，attempt增加；不带新采集字段、不要求SM写权限；旧版本/原path不重算 | #29 |
| I29-U04.unsent | 旧未发单CR只读恢复再明确继续 | 真v10未发任务升级/轮询，再continue-unsent；期间换草稿 | 轮询0写；明确继续只写原父资源，忽略新草稿/采集默认值 | #29 |
| I29-U04.sent | 旧已发父CR只读确认 | 真v10保留父原UID和possiblySent；升级、refresh与重启 | 原UID/冻结spec只读核验，无父/SM重放，无新采集字段 | #29 |
| I29-U05.uid | 历史父UID替换保护 | 历史父同名换UID，再尝试新enabled发布及旧互斥释放 | 拒绝接管，不改平台原UID，不解除父保护，父/SM写0 | #29 |
| I29-U05.drift | 历史父漂移与明确覆盖 | 原父UID保留但spec外改；无overwrite确认发布；另明确漂移确认的新发布 | 未确认拒绝；沿原UID/RV明确写父新版本；SM写0；迁移互斥释放本身不覆盖漂移 | #29 |
| I29-U05.foreign | 外部/不同名旧SM阻断新启用 | 用旧历史path放原SM或同名替代SM，Operator新名不同；预览/发布 | 仍阻断并展示原path/UID或替代原因；不派生/接管新旧SM，不写SM | #29 |
| I29-U05.permission | 迁移权限边界总例 | 执行以下四个独立权限输入 | 各子例分别判定，不要求SM create SSAR | #29 |
| I29-U05.permission-parent | 缺父更新权限 | 允许完整盘点但拒绝父update，新明确发布 | 当前能力拒绝，父/SM业务写0 | #29 |
| I29-U05.permission-sm-read | 旧SM读取不明 | 旧原path GET Forbidden，尝试确认和新启用 | 保留保护，原因可操作，不能视作404 | #29 |
| I29-U05.permission-agent-list | Agent影响范围读取不明 | Agent LIST Forbidden，导出盘点并尝试确认流程 | 明确影响未确认，不能显示无Agent或自动清理；管理员必须获得完整盘点再处理 | #29 |
| I29-U05.permission-no-sm-write | 平台不依赖SM写权限 | 允许父写和必要读取，拒绝全部SM写；显式迁移后新启用 | 父发布可完成，平台SM四类写0，Operator独立创建 | #29 |
| I29-U05.lost | 新父条件更新响应丢失只读恢复 | 历史父已有已确认UID；新明确启用父PUT生效后断响应，重启refresh | 按原父UID/冻结意图核验，不重放；Operator收敛独立；SM平台写0 | #29 |
| I29-U06.inventory-success | 成功旧SM也完整盘点 | 独立v12/v13 SUCCEEDED旧SM，不同于新Operator名称；读migration与历史操作 | 返回旧operation/key/path/fingerprint/发送事实，父与SM原UID/当前UID/RV/owner/spec，仍阻断新启用 | #29 |
| I29-U06.inventory-unknown | 未知UID和未知Agent影响不伪造 | 独立unknown旧SM；Agent LIST或namespace label读取失败；读取盘点 | 原UID明确未知，live UID仅实时观察；影响范围未确认，不显示无Agent/不认领 | #29 |
| I29-U06.inventory-agent | Agent选择范围完整解释 | 旧SM A/B不同名称；Agent跨namespace规则、namespace labels、选择器表达式和分页列表分别输入 | 列出所有实际匹配旧SM的Agent与原因；namespace不覆盖者明确不匹配；LIST不完整不能作安全确认 | #29 |
| I29-U06.resolve-known | 已知UID旧SM外部处理后显式确认 | 保存盘点快照，外部按原UID/RV处理，原path404；管理员提交原operation/key/path/UID/fingerprint，重启回读 | 独立resolution持久actor/time；旧任务/UID/possiblySent/frozen bytes不变；新guard每次仍GET404 | #29 |
| I29-U06.resolve-unknown | 未知原UID显式处理后可解阻 | unknown原UID、实际对象先存在时确认被拒；管理员独立外部处理后GET404，用显式空原UID确认 | 保存original_uid空；旧IDENTITY_UNCERTAIN/confirmedUid空不变，不补认；父边界另判定 | #29 |
| I29-U06.resolve-authorization | 当前授权覆盖HTTP/RPC/幂等 | 普通用户HTTP/RPC确认；管理员同键重试/换内容；管理员降级/停用后旧键重放 | 普通/失权拒绝；相同输入幂等，不同输入冲突；无重复记录/审计，Kubernetes写0 | #29 |
| I29-U06.resolve-binding | 精确原冻结绑定 | 分别错instance、operation、resourceKey、objectPath、UID、fingerprint，或用等价重marshal字节指纹确认 | 每项拒绝；不能给其他旧step解阻；不使用客户端任意path发请求 | #29 |
| I29-U06.resolve-still-present | 404是唯一缺失证据 | 原对象存在、替代UID存在、Forbidden、超时分别确认；含原UID未知输入 | 都拒绝/保留保护，SM写0，不把404以外错误当不存在 | #29 |
| I29-U06.resolve-race | 确认与新写前重复核验 | resolution完成后旧path重现；分别在preview后、受理后/worker写前出现替代对象或读失败 | 该阶段阻断后续父写；旧对象/替代对象零平台SM写 | #29 |
| I29-U07.parent-known | SM处理后安全释放精确旧互斥 | old父step已确认UID、live frozen path/UID/spec一致，所有旧SM确认+GET404；显式释放再新发布 | 独立父解阻记录；只删精确旧operation protection，新发布成功；旧状态/attempt/possiblySent不变 | #29 |
| I29-U07.parent-unknown-present | SM不存在不能绕过未知父身份 | 父原UID空但同名同spec对象在场；SM全确认404，再释放/新发布 | 拒绝，不补认父UID、不删除保护，不写父/SM | #29 |
| I29-U07.parent-unknown-absent | 外部处理未知父后仅新的明确创建 | 独立未知父与旧SM任务；管理员明确处理父，原父path404，单独提交父处置确认，再新预览发布 | 旧unknown与原空UID保持；独立处置审计；只有新操作可创建，不能把旧任务改成功或续写 | #29 |
| I29-U07.parent-drift | 父漂移/读取不明不能释放 | 依次替代UID、同UID spec漂移、Forbidden/超时；SM均已显式404 | 每项保留父保护；提供可操作原因，不执行父或SM写 | #29 |
| I29-U07.other-protection | 不误删别的操作互斥 | 旧resolution请求绑定其他operation或保护已变为新operation；另旧DELETE任务 | 拒绝/不释放不相关保护；不会凭SM已清理当作父删除或新任务完成 | #29 |
| I29-U08.rename-ksm | 稳定ID与冻结父路径不被原创建名影响 | 公开创建后首次发布前合法改name/targetNamespace；真实v10升级，读旧版本/恢复/盘点 | creator可靠恢复；父GET用发布冻结name且cluster-scoped，旧SM GET用自身冻结namespace/name | #29 |
| I29-U08.frozen-bytes | 新旧草稿路径与原SM字节分离 | 独立v12/v13旧task原namespace A、当前draft B，SM文档非canonical字节；迁移/确认/重启/新发布 | 仅GET原A路径，原fingerprint按bytes；旧document/path/状态不变，新意图只在新父版本 | #29 |
| I29-U08.rollback | v16迁移失败保留真实旧库 | 分别v10/v12/v13独立DDL制造v16同名冲突，启动失败后核版本/快照/原关系；移除冲突重启 | 原版本和UID/密文/任务/frozen bytes保留，失败不推进；成功resolution空表，原库可恢复 | #29 |
| I29-K01 | 真实纯单CR升级零写与显式启用 | 独占Helm/PVC真实v10、实际旧CR无开关；升级补身份/确认/轮询；另一管理员明确enabled发布 | 升级各阶段父/SM平台mutation增量0；新明确父PUT保留UID，Operator创建SM；旧/新版本差异与审计可核 | #29 |
| I29-K02 | 真实旧SM退出管理与未知结果保护 | 独立有旧SM成功/未知发送夹具，原旧SM名称不同；盘点Agent影响与快照，先新启用被拒；管理员外部按身份处理后确认、释放父互斥、新发布；再重建Pod | 原历史不改、平台SM零写；Operator不接管旧对象；处理后GET404才新发布；父unknown仍在子例拒绝；真实targets/样本无重复；记录空窗与cleanup | #29 |
| I29-B01 | 页面看清旧事实、盘点、显式确认与新父意图 | E2E-10唯一test，I26最终注销前打开历史版本/原操作、迁移盘点；旧对象存在确认受阻；外部管理员处理后通过页面确认并明确新发布 | 旧单CR/旧双CR语义原样，creator/原UID/未知理由/Agent影响/actor-time可见；新版本只有父CR采集意图；无SM写/修复/删除按钮；不把OperatorReady当采集成功 | #29 |
| I29-R01.gone | 仅已证明消失的旧fixtureUID退休 | 本run登记旧SM原UID；按UID条件处理、wait后再次GET404；检查清理登记 | 仅移除对应旧UID条目，不改其他UID/父清理责任；与新Operator子UID分别登记 | #29 |
| I29-R01.blocked | 清理未证实不遗忘责任 | wait失败、wait后原对象仍在分别跑真实转换helper | 转换失败，旧登记保留；不继续新发布/不跳过原父清理 | #29 |
| I29-R02.confirmed | 浏览器发布精确交接父UID | 页面新发布受理点保存operation/instance/version；Python finally在forward/代理存活时公开HTTP回读精确原操作/只读Operator引用，再真实cleanup | 只交接本run确定UID；父按原UID清理，子由Operator清理并只读核验；旧手工SM登记不与新子UID混淆 | #29 |
| I29-R02.unknown | fixture清理不凭同名补认 | 可能已发送而confirmedUID空；同名同spec在场；持久未发送空UID独立输入 | 未知发送明确登记失败、外部对象零DELETE；未发空UID不认领也不误报已发送；平台历史确认不替代fixture归属 | #29 |
| I29-R02.foreign | 跨实例/版本交接拒绝 | 错instance/version/operation或非本run资源分别HTTP回读交接 | 各项拒绝登记，不覆盖既有UID，错误对象零DELETE | #29 |
| I29-R03.replaced | 实际cleanup拒绝替代UID | 本run已登记原父或旧SM，随后同名换UID，执行cleanup | cleanup失败门禁保留，替代对象零DELETE；分类identity/uid-mismatch | #29 |
| I29-R03.read | 清理读取失败不泄露异常 | cleanup目标返回读取错误，异常含私有sentinel | cleanup失败且固定read/read-failed，公共JSON无异常正文/URL/sentinel | #29 |
| I29-R03.write | fixture外部清理失败阶段可区分 | 本run自有fixture finalizer条件PATCH或UID DELETE分别拒绝 | 固定finalizers/finalizer-patch-failed、delete/delete-failed；不移除外部finalizer；平台产品无SM写API | #29 |


执行映射：U01/U02/U03/U04/U08 → UT-16；U06/U07 → UT-11/12/17；U05 → UT-12；R01/R02/R03 → UT-18；K01 → E2E-01；K02 → E2E-08；B01 → E2E-10。复合输入在用例内以稳定后缀独立 By/ReportEntry 记录，结果逐输入保存。真实集群及浏览器由父规格统一运行；本项交付编排与 Go 原始证据断言，不将语法/编译当作真实通过。

实施不新增平台 ServiceMonitor 写入 API。管理员在平台外核验采集影响并处理旧对象后，平台仅记录独立确认；原 UID 未知保持空，旧 state/possiblySent/frozen bytes 不改写。SM 处理不能解除未知父 CR 身份保护；仅精确原 operation 可在独立父身份核验或明确不存在确认后解除互斥。每次新启用仍 GET 原旧 SM path，只有404放行。计划与实际结果分开保存，绑定 testedCommit。
