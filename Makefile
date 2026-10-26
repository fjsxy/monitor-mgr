SHELL := /bin/sh
.DEFAULT_GOAL := help
GO ?= go
NPM ?= npm
PYTHON ?= python3
E2E_ARGS ?=
E2E_SUMMARY_ARGS ?=
BUILDX_BUILDER ?=
COVERAGE_FILE ?= .scratch/coverage.out
HTTP_ADDR ?= 127.0.0.1:18080
RPC_ADDR ?= 127.0.0.1:18888
DATA_DIR ?= data/local
# 本地部署保留 IMAGE_TAG 入口；CI 和自定义仓库仍可通过 IMAGE 指定完整镜像。
IMAGE ?= monitor-mgr:$(IMAGE_TAG)
SOURCE_SHA ?= $(shell git rev-parse HEAD)
PLATFORM ?= linux/arm64
HELM ?= helm
KUBECTL ?= kubectl
KUBE_CONFIG ?=
KUBE_CONTEXT ?=
NAMESPACE ?= monitor-system
RELEASE ?= monitor-mgr
IMAGE_TAG ?= development
VM_RELEASE ?= vmcluster
VM_NAMESPACE ?= monitoring

.PHONY: help ut e2e e2e-summary check test test-race generate check-generated vet web-install web-build build run image ci-cases ci-cases-check ci-builder ci-builder-cleanup
help: ## 查看常用命令
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "%-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
ut: ci-cases-check check-generated vet web-build ## 18 个 Ginkgo UT，一次 race 与不少于70%的产品语句覆盖率
	mkdir -p .scratch
	MONITOR_MGR_PYTHON="$(PYTHON)" $(GO) test -race -count=1 -timeout 15m -coverpkg=./internal/control,./internal/platform -coverprofile="$(COVERAGE_FILE)" ./internal/control ./internal/platform -args -ginkgo.no-color -ginkgo.json-report=.scratch/ut.json
	$(PYTHON) scripts/check-coverage.py "$(COVERAGE_FILE)" --minimum 70
	$(PYTHON) scripts/check-tests.py
e2e: ## 自动拉起独占双集群，默认运行10项；E2E_ARGS可指定--shard topology/storage/recovery，最后按原身份清理
	$(PYTHON) scripts/e2e.py $(E2E_ARGS)
e2e-summary: ## 严格汇总三个CI组为完整10项；E2E_SUMMARY_ARGS指定input/output/candidate/run-id/jobs-result
	$(PYTHON) scripts/e2e_shards.py $(E2E_SUMMARY_ARGS)
check: ut ## 完整本地 UT 质量检查；真实集群另执行 make e2e
test: ut ## 与 make ut 一致，避免旧入口重复运行相同用例
test-race: ut ## race 已包含在唯一 UT 入口中
generate: ## 按锁定版本生成 Thrift/Kitex
	bash scripts/generate-rpc.sh
check-generated: generate ## 检查生成文件一致
	git diff --exit-code -- kitex_gen
vet: ## Go 静态检查
	$(GO) vet ./...
web-install: ## 按 lockfile 安装前端依赖
	$(NPM) --prefix web ci --no-audit
web-build: ## 前端只做类型检查和构建
	$(NPM) --prefix web run build
build: ## 构建平台
	mkdir -p bin
	$(GO) build -o bin/monitor-mgr ./cmd/monitor-mgr
run: build web-build ## 启动本地平台
	./bin/monitor-mgr -data-dir "$(DATA_DIR)" -http "$(HTTP_ADDR)" -rpc "$(RPC_ADDR)" -static web/dist
image: ## 构建指定来源和架构镜像
	docker build --platform "$(PLATFORM)" --provenance=false --sbom=false --build-arg SOURCE_SHA="$(SOURCE_SHA)" -t "$(IMAGE)" .
ci-cases: ## 生成完整28项CI清单及选择标准
	$(PYTHON) scripts/ci-cases.py
ci-cases-check: ## 核对固定目录、代码、CI和本地清单
	$(PYTHON) scripts/ci-cases.py --check
.PHONY: chart-check deploy-platform platform-forward
chart-check: ## 检查平台 Helm chart
	$(HELM) lint charts/monitor-mgr
deploy-platform: ## Helm 部署平台，必须显式指定 KUBE_CONFIG/KUBE_CONTEXT/IMAGE_TAG
	@test -n "$(KUBE_CONFIG)" && test -n "$(KUBE_CONTEXT)" || (echo '必须指定 KUBE_CONFIG 和 KUBE_CONTEXT' >&2; exit 1)
	$(HELM) upgrade --install "$(RELEASE)" charts/monitor-mgr --kubeconfig "$(KUBE_CONFIG)" --kube-context "$(KUBE_CONTEXT)" --namespace "$(NAMESPACE)" --create-namespace --set-string image="$(IMAGE)" --set-string source="$(SOURCE_SHA)" --wait --timeout 180s
platform-forward: ## 将管控集群平台转发至本机 18080；前台运行
	@test -n "$(KUBE_CONFIG)" && test -n "$(KUBE_CONTEXT)" || (echo '必须指定 KUBE_CONFIG 和 KUBE_CONTEXT' >&2; exit 1)
	$(KUBECTL) --kubeconfig "$(KUBE_CONFIG)" --context "$(KUBE_CONTEXT)" -n "$(NAMESPACE)" port-forward --address 127.0.0.1 service/"$(RELEASE)" 18080:8080
.PHONY: vm-chart-deps vm-chart-check deploy-vmcluster
vm-chart-deps: ## 下载锁定的 VictoriaMetrics 官方 Helm chart 依赖
	$(HELM) dependency build charts/local-victoriametrics
vm-chart-check: ## 校验本地 VictoriaMetrics Cluster chart（先执行 vm-chart-deps）
	$(HELM) lint charts/local-victoriametrics
deploy-vmcluster: ## Helm 部署无认证 VM Cluster；必须显式指定 KUBE_CONFIG/KUBE_CONTEXT
	@test -n "$(KUBE_CONFIG)" && test -n "$(KUBE_CONTEXT)" || (echo '必须指定 KUBE_CONFIG 和 KUBE_CONTEXT' >&2; exit 1)
	$(HELM) upgrade --install "$(VM_RELEASE)" charts/local-victoriametrics --kubeconfig "$(KUBE_CONFIG)" --kube-context "$(KUBE_CONTEXT)" --namespace "$(VM_NAMESPACE)" --create-namespace --wait --timeout 5m

ci-builder: ## CI 创建本 run 独占、固定版本的 BuildKit；须指定 BUILDX_BUILDER=mm-...
	$(PYTHON) scripts/ci-builder.py create --name "$(BUILDX_BUILDER)"
ci-builder-cleanup: ## 按记录的原容器 ID 清理本 run 构建器
	$(PYTHON) scripts/ci-builder.py cleanup --name "$(BUILDX_BUILDER)"

.PHONY: test-issue27
test-issue27: ## #27 单 CR、旧 SM 只读、冻结身份、删除与持久化 Ginkgo 回归，不启动 kind
	$(GO) test -race -count=1 -timeout 5m ./internal/platform -args -ginkgo.focus='UT-(07|10|11|12|13|16)' -ginkgo.no-color

.PHONY: ut-instance-identity
ut-instance-identity: ## #25 实例创建身份、直接 RPC 与 worker 授权局部行为回归
	$(GO) test -count=1 -timeout 8m ./internal/platform -args -ginkgo.no-color -ginkgo.focus="UT-07|UT-10|UT-16" -ginkgo.json-report=.scratch/issue-25.json

.PHONY: ut-exporter-collection
ut-exporter-collection: ## #28 采集意图、能力、只读观察与原身份删除局部回归
	$(GO) test -count=1 -timeout 8m ./internal/platform -args -ginkgo.no-color -ginkgo.focus="UT-08|UT-10|UT-11|UT-12|UT-14|UT-17" -ginkgo.json-report=.scratch/issue-28.json
.PHONY: check-issue25-e2e
check-issue25-e2e: ## #25 真实验收夹具语法、Go 编译与浏览器用例发现；不表示真实集群执行通过
	$(PYTHON) -m py_compile scripts/acceptance/instance_identity.py scripts/acceptance/ksm_proxy.py scripts/acceptance/fixtures/fault_proxy.py scripts/e2e_scenarios.py
	$(GO) test ./tests/e2e -run '^$$'
	$(NPM) --prefix web run test:e2e -- tests/phase1.spec.ts --list

.PHONY: ut-historical-identity
ut-historical-identity: ## #26 历史身份迁移、管理员确认与 worker 撤销局部回归
	mkdir -p .scratch
	$(GO) test -count=1 -timeout 8m ./internal/platform -args -ginkgo.no-color -ginkgo.focus="UT-07|UT-10|UT-16" -ginkgo.json-report=.scratch/issue-26.json
	$(PYTHON) -m py_compile scripts/acceptance/historical_identity.py scripts/e2e_scenarios.py

.PHONY: check-issue28-e2e
check-issue28-e2e: ## #28 真实编排语法、Go 证据断言编译与唯一浏览器用例发现；不创建 kind
	$(PYTHON) -m py_compile scripts/acceptance/exporter_collection.py scripts/e2e.py scripts/e2e_scenarios.py
	$(GO) test ./tests/e2e -run '^$$'
	$(NPM) --prefix web run test:e2e -- tests/phase1.spec.ts --list

.PHONY: ut-historical-exporter check-issue29-e2e
ut-historical-exporter: ## #29 历史盘点、显式处理、父身份与新单 CR 发布局部 Ginkgo 回归
	mkdir -p .scratch
	$(GO) test -count=1 -timeout 10m ./internal/platform -args -ginkgo.no-color -ginkgo.focus="UT-11|UT-12|UT-16|UT-17|UT-18" -ginkgo.json-report=.scratch/issue-29.json
check-issue29-e2e: ## #29 真实编排语法、Go 原始证据断言编译与唯一浏览器用例发现；不启动 kind
	$(PYTHON) -m py_compile scripts/acceptance/historical_exporter.py scripts/acceptance/historical_collection.py scripts/acceptance/historical_cleanup.py scripts/acceptance/historical_cleanup_probe.py scripts/acceptance/historical_identity.py scripts/e2e_scenarios.py
	$(GO) test ./tests/e2e -run '^$$'
	$(NPM) --prefix web run test:e2e -- tests/phase1.spec.ts --list

.PHONY: ut-agent-collection check-issue30-e2e receiver-chart-check
ut-agent-collection: ## #30 固定创建用户、原生selector和独立权限的Ginkgo局部回归
	$(GO) test -count=1 -timeout 8m ./internal/platform -args -ginkgo.no-color -ginkgo.focus='UT-07|UT-08|UT-14|UT-17' -ginkgo.json-report=.scratch/issue-30.json
check-issue30-e2e: ## #30 真实采集编排语法、模块加载、Go证据编译及唯一浏览器发现；不创建kind
	$(PYTHON) -m py_compile scripts/acceptance/agent_collection.py scripts/acceptance/ksm_proxy.py scripts/acceptance/fixtures/fault_proxy.py scripts/e2e_scenarios.py
	PYTHONPATH=scripts:scripts/acceptance $(PYTHON) -c 'import agent_collection'
	$(GO) test ./tests/e2e -run '^$$'
	$(NPM) --prefix web run test:e2e -- tests/phase1.spec.ts --list
receiver-chart-check: ## 校验独占空scrape receiver的Helm声明；镜像锁定与现有Prometheus一致
	$(HELM) lint charts/test-receiver --set-string 'image=quay.io/prometheus/prometheus@sha256:2659f4c2ebb718e7695cb9b25ffa7d6be64db013daba13e05c875451cf51b0d3' --set-string runId=mm-issue30-static
