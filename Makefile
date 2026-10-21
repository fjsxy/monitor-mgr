SHELL := /bin/sh
.DEFAULT_GOAL := help
GO ?= go
NPM ?= npm
PYTHON ?= python3
E2E_ARGS ?=
BUILDX_BUILDER ?=
COVERAGE_FILE ?= .scratch/coverage.out
HTTP_ADDR ?= 127.0.0.1:18080
RPC_ADDR ?= 127.0.0.1:18888
DATA_DIR ?= data/local
IMAGE ?= monitor-mgr:development
SOURCE_SHA ?= $(shell git rev-parse HEAD)
PLATFORM ?= linux/arm64

.PHONY: help ut e2e check test test-race generate check-generated vet web-install web-build build run image ci-cases ci-cases-check ci-builder ci-builder-cleanup
help: ## 查看常用命令
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "%-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
ut: ci-cases-check check-generated vet web-build ## 18 个 Ginkgo UT，一次 race 与不少于70%的产品语句覆盖率
	mkdir -p .scratch
	MONITOR_MGR_PYTHON="$(PYTHON)" $(GO) test -race -count=1 -timeout 15m -coverpkg=./internal/control,./internal/platform -coverprofile="$(COVERAGE_FILE)" ./internal/control ./internal/platform -args -ginkgo.no-color -ginkgo.json-report=.scratch/ut.json
	$(PYTHON) scripts/check-coverage.py "$(COVERAGE_FILE)" --minimum 70
	$(PYTHON) scripts/check-tests.py
e2e: ## 自动拉起独占双集群并运行10个Ginkgo E2E，最后按原身份清理；可用E2E_ARGS指定output/run-id/monitor-op-source
	$(PYTHON) scripts/e2e.py $(E2E_ARGS)
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
	$(NPM) --prefix web ci
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

ci-builder: ## CI 创建本 run 独占、固定版本的 BuildKit；须指定 BUILDX_BUILDER=mm-...
	$(PYTHON) scripts/ci-builder.py create --name "$(BUILDX_BUILDER)"
ci-builder-cleanup: ## 按记录的原容器 ID 清理本 run 构建器
	$(PYTHON) scripts/ci-builder.py cleanup --name "$(BUILDX_BUILDER)"
