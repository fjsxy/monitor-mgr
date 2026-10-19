# #21 CI 与公开输出边界

`.github/workflows/phase1.yml` 在独立 Ubuntu 24.04 hosted Linux amd64 job 执行 `CI_FAST` 的 58 Q 和 `CI_SMOKE` 的 17 固定键；跳过、未触发或只安装环境不能算通过。真正结果依赖主 runner 的逐键收据、原证据 hash、来源和 cleanup 验证。

## 权威候选与触发

integration 分支只由 base main 的 PR opened/reopened/synchronize 触发；push 只监听 main，避免 integration push 与同 PR synchronize 产生两套 Smoke。PR 显式 checkout `github.event.pull_request.head.sha`，不是合成 merge SHA。首次使用已存在 PR 的可信 integration push 导致 synchronize，无须提前合 main 或依赖尚未存在于默认分支的 workflow_dispatch。dispatch 保留供工作流进入默认分支后使用。

Smoke 仅接受同仓 `codex/phase1-implementation` head、main push，或这两个可信分支的 dispatch；fork 和其他分支不读取源密钥。Fast 独立 job，不引用任何私有 secret。concurrency 的 `cancel-in-progress=false` 防止新提交主动打断旧 finally；GitHub 取消、job timeout 或 runner 丢失仍可能中断清理，不能把 always 语法当实际清理成功。

## 固定工具与分发字节

`bootstrap_linux.py` 直接校验官方 URL 的 SHA256，并安装到本 job 全新 prefix，不依赖预装 tool cache、actions/setup-go、actions/setup-node 或它们默认开启的缓存。Go1.27.1、Node26.4.0、kind0.33.0、kubectl1.28.15、CPython3.14.7、PyYAML6.0.3 保持固定；所有 URL 和摘要在 `source-lock.json`。未配置 Go/Docker 公共 cache 或 cache-to/export，私有构建输出仅留本 job。

Python 使用 actions/python-versions 的固定 `3.14.7-31064857500` Linux24.04 x64 分发，官方 asset SHA256 `76d5ddab6d2dd89a39c06220f6efeda486a48ed481eae97bfc596c74ac3623db`。实际执行该已核包内的官方 `setup.sh`，将 `AGENT_TOOLSDIRECTORY/RUNNER_TOOL_CACHE` 指向新 prefix。该脚本会强制升级 pip，故仅允许固定官方 pip26.2.1 wheel 的离线目录；随后 PyYAML 用固定 wheel、`--require-hashes --only-binary=:all: --no-deps` 安装。实际记录 CPython版本、ABI/GIL、pip/PyYAML版本。`setup-python` 的固定代码 SHA 在来源锁中留作溯源，工作流没有假借它命中旧分发缓存。

Playwright1.64.1 由原 `web/package-lock.json` 与 `npm ci` 安装。Smoke 用该版本的 `install-deps chromium` 安装 33 个官方声明的 Ubuntu 依赖；固定 Chromium156.0.8078.4/revision1248、headless-shell 和 ffmpeg 的官方 URL，下载 zip 原件后记录实际 SHA256，再安全解压到该固定版本 registry 目录并实际启动浏览器。上游未提供独立 archive SHA256，字段明确 `authority=observed_download_only`、`officialSHA256=null`，不把源码/npm integrity 冒充浏览器包 digest。APT 包与传递依赖未被上游固定 snapshot；记录实际包版本及 origin host，变化或不可用按环境失败处理，不静默升级项目工具版本。

来源：Python [固定安装脚本模板](https://github.com/actions/python-versions/blob/49858b91c92d27403416ee43117032e91d00ce24/installers/nix-setup-template.sh)、[固定分发](https://github.com/actions/python-versions/releases/tag/3.14.7-31064857500)、[pip26.2.1 元数据](https://pypi.org/pypi/pip/26.2.1/json)。这些是来源合同，不是本机或 Actions 已完成安装的证明。

## 私有 Operator 与出口

只有 trusted Smoke 的单独 fetch 步骤得到 `MONITOR_OP_DEPLOY_KEY/MONITOR_OP_SOURCE_REPOSITORY`，`fetch_source.py` 仅接受授权仓库和固定 SHA，SSH 使用从 [GitHub 官方 meta](https://api.github.com/meta) 固定的 host keys，不运行 ssh-keyscan。专用 key 是整仓只读权限，不能称单 SHA 权限。key 临时文件 0600，child 不继承 secret env，成功/失败均移除；不复用主机 PAT、主机 SSH agent 或其他凭据。源码与 git 对象仅在 0700 私有目录。

原 Go/build/browser/kubectl 输出在公开 console 前即由 `capture.py` 或主 Session 捕获为 0600 原字节日志。console 只输出固定 ID、exit 与 byte/count；子进程非零保持非零。原件与安全派生 hash 独立，不能先 tee 原文后声称安全，也不能改原件冒充原 hash。

工作流只上传实际 exporter 产生的 `SAFE_OUTPUT/`，以及独立恢复的明确 `public/environment-cleanup.json`；没有整 workspace/output/privateRoot 的上传。kubeconfig、Session/Cookie、SQLite/WAL、真实 key、私有源和镜像层不在公开白名单。严格 exporter 失败后另调用 `--diagnostic`，安全诊断明确 `gatePassed=false/diagnosticOnly=true`，整个 step 仍非零；它不能补齐 passing 键。先验证并导出/上传原结果，再清理本 run 的 owned clusters，再单独上传安全清理收据；任一步失败保持 job 失败。root 最终按真实 event、run ID、attempt、head C 和下载后的离线复验认定 CI，不按 workflow 文件存在认定通过。

本次 Host 协议使用合成 canary、实际受控子进程和外部临时文件；不触发 workflow、下载正式工具或访问集群，不建立任何固定 profile passing 键。
