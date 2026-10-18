# #21 注册、门禁与安全导出

本协议只聚合实际结果，不执行 Kubernetes 操作。固定 `docs/acceptance/phase1.json` 字节不变；`LOCAL_FULL/CI_FAST/CI_SMOKE` 分别精确 207/58/17 键，补充集合仅 `LOCAL_FULL`，100 个 ID、104 个 ID/layer 键。两套门禁分别运行，不能抵扣。`focus` 只用于查询与诊断，不能缩减完整门禁。

```sh
python scripts/acceptance/registry.py --profile CI_FAST
python scripts/acceptance/registry.py --profile LOCAL_FULL --supplemental
python scripts/acceptance/gate.py validate --index "$RUN_DIR/index.json" --candidate "$C" --profile CI_FAST
python scripts/acceptance/exporter.py export --index "$RUN_DIR/index.json" --destination "$NEW_BUNDLE_DIR" --candidate "$C" --profile CI_FAST --canary-file "$PRIVATE_CANARY_FILE"
```

`PRIVATE_CANARY_FILE` 是受保护的 JSON 字符串数组，包含本次生成的认证敏感值；值只在内存参与扫描，不写公开摘要或 bundle。原始子进程输出必须先由 `exporter.capture_command(command, private_directory, case_id)` 捕获。等价 CLI 为 `exporter.py capture --private-directory "$PRIVATE_DIR" --case-id "$ID" -- <command...>`；输出直接落 0600 原件，目录 0700，公开仅稳定 ID、原退出码、状态、private hash，失败不能改绿。

`gate.validate(index_path, candidate, profile, supplemental=False)` 失败抛 `GateError`；CLI 非零并只输出稳定错误码。index 的 `schemaVersion=1`，包含 `runId/profile/candidateCommit/testedCommit/testedTree/runnerCommit/dirty/manifestHash/supplemental/expectedKeys/results/environment/startedAt/finishedAt/state/cleanup`。`results` 每项为 `{variantId, layer, receipt:{path,sha256,visibility,kind}}`。门禁从 registry 独立重算必选键，不信 index 自报总数与 `expectedKeys`。

receipt 包含同一候选的来源字段、`contractFingerprint/outcome/cleanup/testSelectors/executionBoundary/originalReceipt/evidenceRefs`。所有规范 ref 相对 index 所在 run 根；禁止绝对路径、`..`、根内 symlink、缺失或 hash 不符。run 根先 canonicalize，因此 macOS `/tmp` 的系统入口有效。原组件 `originalReceipt` 保留原字节，嵌套原 ref 默认相对原件所在目录；wrapper 必须将每项重新定位到 run 根，并显式引用原路径/hash。新原件若本身采用 run 根，可设 `originalEvidenceBase=""`。原件本身的 tested/runner/source.commit/tree/dirty/bridge、失败与 cleanup 都必须核验，不能借新 wrapper 包装旧源通过。

`environment` 使用真实 `runtimeOS/runtimeArch`；FULL 为 darwin/arm64，control 3 节点加 data 1 节点；FAST 为 linux/amd64 无集群；SMOKE 为 linux/amd64 仅 control 3 节点。`targets` 每项含 `name/clusterUID/nodeCount`，不同目标必须不同 UID。K/B receipt 的 runtime 为 linux 与对应架构，`platform` 绑定实际 `podUID/nodeName/imageID/clusterUID/serviceName`；`productImage` 包含 `revision/os/architecture/manifestDigest/configDigest/manifestRef/configRef`，原始 OCI 字节必须闭合 manifest→config→候选 revision，与 Pod imageID 对齐。

Q Go 使用真实 `goEventRef` JSONL、`command/commandExitCode/noSkipped`，所需根/leaf 必须实际 pass、所属根不得 skip/fail。B 使用实际 Playwright JSON reporter `browserEventRef`，匹配全部登记测试，结果必须实际 passed 且无 skip/error；`I07.disabled-owner` 三个入口全部必需，PATTERN-011 复用真实 U02 测试。协议 Q 使用 `protocolEventRef`，由真实 unittest 子进程产生，不能代替产品 K。

```sh
python scripts/acceptance/gate.py protocol-tests --events "$RUN_DIR/protocol-events.json" --selectors gate_test.ValidatorTests.test_wrong_source exporter_test.ExporterTests.test_console
python -m unittest discover -s scripts/acceptance -p '*_test.py'
```

第二条需已安装项目固定 PyYAML 才能同时加载既有 fixture 测试；本次新增 registry/gate/exporter 本身仅用标准库。`protocol-tests` 原 traceback/stdout/stderr 保存在同目录 `.private/` 的 0600 日志，公开事件包含真实 source 与每个实际测试结果。REG21 的十二 validator Q、六 exporter Q 各有独立 selector；合成基线显式 `validator-test`，只能加 `--validator-test` 验证，成功摘要仍为 `productGate=false`，不能算固定产品通过。

exporter 在完整门禁后，仅复制显式 public 类型白名单闭包；私有源、kubeconfig、SQLite/WAL、镜像 tar、认证材料、未批准类型和已登记 canary 均拒绝。必要 private ref 缺失或无法公开，门禁/导出仍失败。原件和安全派生各自 hash，不能通过排除或重写 hash 补绿。失败不留下可发布目录；成功 bundle 包含注册源、标准库验证器与文件 hash 清单，原始 index/receipt/ref 字节保持不变。

在新路径独立复验，不依赖原仓库或工作目录：

```sh
cd /
python "$NEW_BUNDLE_DIR/scripts/acceptance/gate.py" validate --index "$NEW_BUNDLE_DIR/index.json" --candidate "$C" --profile CI_FAST
```

本说明与协议 fixture 不构成产品 `fresh282`、真实 Kubernetes/B 或 CI 通过证据；最终候选 C 必须按正式计划实际执行。
