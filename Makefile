SHELL := /bin/sh
.DEFAULT_GOAL := help
GO ?= go
NPM ?= npm
PYTHON ?= python3
HTTP_ADDR ?= 127.0.0.1:18080
RPC_ADDR ?= 127.0.0.1:18888
DATA_DIR ?= data/local
IMAGE ?= monitor-mgr:dev
RPC_IMAGE ?= monitor-mgr:ksm-rpc-fixture
LIFECYCLE_FIXTURE_IMAGE ?= monitor-mgr:lifecycle-fixture
SOURCE_SHA ?= $(shell git rev-parse HEAD)
PLATFORM ?= linux/arm64
BASE_URL ?= http://127.0.0.1:18080
E2E_ARGS ?=

.PHONY: help generate check-generated test test-race vet web-install web-build web-e2e build run image acceptance-plan e2e-clusters check e2e-ksm e2e-ksm-q ksm-rpc-image ksm-q-receipt e2e-ksm-b agent-seed-check e2e-lifecycle-q e2e-lifecycle lifecycle-q-receipt lifecycle-fixture-image agent-projection-check agent-projection-k agent-projection-fixture-check
help: ## 查看重复操作入口
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "%-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
generate: ## 按锁定版本生成 Thrift/Kitex
	bash scripts/generate-rpc.sh
check-generated: generate ## 检查生成文件与已提交结果一致
	git diff --exit-code -- kitex_gen
test: ## 执行 Go 测试
	$(GO) test ./...
test-race: ## 执行 Go race 测试
	$(GO) test -race ./...
vet: ## 执行 Go 静态检查
	$(GO) vet ./...
web-install: ## 按 lockfile 安装前端依赖
	$(NPM) --prefix web ci
web-build: ## 构建前端
	$(NPM) --prefix web run build
web-e2e: ## 对 BASE_URL 运行真实浏览器测试
	BASE_URL="$(BASE_URL)" $(NPM) --prefix web run test:e2e -- $(E2E_ARGS)
build: ## 构建平台二进制
	mkdir -p bin
	$(GO) build -o bin/monitor-mgr ./cmd/monitor-mgr
run: build web-build ## 启动本地平台（DATA_DIR/HTTP_ADDR/RPC_ADDR 可配置）
	./bin/monitor-mgr -data-dir "$(DATA_DIR)" -http "$(HTTP_ADDR)" -rpc "$(RPC_ADDR)" -static web/dist
image: ## 构建固定平台镜像（IMAGE/PLATFORM 可配置）
	docker build --platform "$(PLATFORM)" --provenance=false --sbom=false --build-arg SOURCE_SHA="$(SOURCE_SHA)" -t "$(IMAGE)" .
acceptance-plan: ## 校验验收目录元数据，不代表运行通过
	$(PYTHON) scripts/validate-acceptance.py
e2e-clusters: ## 真实集群验收；通过 E2E_ARGS 显式指定环境与来源
	$(PYTHON) scripts/acceptance/clusters.py $(E2E_ARGS)
check: acceptance-plan test-race vet web-build ## 运行本地质量检查，不替代完整集群门禁

e2e-ksm: ## KSM真实集群验收；E2E_ARGS必须明确环境、来源和输出
	$(PYTHON) scripts/acceptance/ksm.py $(E2E_ARGS)
e2e-ksm-q: ## KSM快速层，仍经过真实HTTP/Kitex/SQLite
	$(GO) test ./internal/platform -count=1 -run 'Test(K10|P0[245]|ExternalKSM|F03KSM|R03KSM|F02KSM|F05Publication|F04Encrypted|PublicationPreflight)'

ksm-rpc-image: ## 构建只追加RPC探针的测试镜像（IMAGE产品基线/RPC_IMAGE输出）
	docker build --platform "$(PLATFORM)" --provenance=false --sbom=false --build-arg PLATFORM_IMAGE="$(IMAGE)" -f scripts/acceptance/Dockerfile.rpcprobe -t "$(RPC_IMAGE)" .
ksm-q-receipt: ## 生成KSM Q逐例收据；通过E2E_ARGS指定output/profile/cases
	$(PYTHON) scripts/acceptance/ksm.py --layer Q $(E2E_ARGS)

e2e-ksm-b: ## KSM真实平台Pod浏览器验收；凭据和trace不进入收据
	$(PYTHON) scripts/acceptance/ksm.py --layer B $(E2E_ARGS)

agent-seed-check: ## 共享组件/旧预览迁移与脱敏回归，不替代Agent真实验收
	$(GO) test ./internal/platform -count=1 -run 'Test(Shared|P04|F04Encrypted|F03KSM)'

e2e-agent-q: ## Agent快速层，仍经过真实HTTP/Kitex/SQLite
	$(GO) test ./internal/platform -count=1 -run 'Test(Agent|F01Agent|F03Agent|R03Agent|Shared)'
e2e-agent: ## Agent逐例真实Pod验收；E2E_ARGS必须显式环境、context、来源、API锁
	$(PYTHON) scripts/acceptance/agent.py $(E2E_ARGS)
agent-q-receipt: ## Agent Q逐例收据；E2E_ARGS指定output/profile/cases
	$(PYTHON) scripts/acceptance/agent.py --layer Q $(E2E_ARGS)
agent-projection-check: ## 解析失败URL/状态文本及旧cache迁移Q，真实HTTP/Kitex/SQLite/TLS
	$(GO) test ./internal/platform -count=1 -run '^TestAgentMalformedURLReadProjection$$'
agent-regressions: ## Agent已发布28项Q补充回归，单独收据，不增加69 owned计数
	$(PYTHON) scripts/acceptance/agent.py --layer REG $(E2E_ARGS)
agent-projection-fixture-check: ## 013证据不留原文及真实状态分支门禁，仅验收工具纯检查
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) scripts/acceptance/agent_projection_test.py
agent-projection-k: ## 仅两项显式PROJECTION K；E2E_ARGS必须明确环境/独立来源/镜像/API锁
	$(PYTHON) scripts/acceptance/agent.py --layer K $(E2E_ARGS) --cases REG18-PROJECTION-013.url-parse-error REG18-PROJECTION-013.proxy-parse-error
e2e-agent-b: ## Agent真实平台Pod浏览器，逐例独立fixture并持完整API锁
	$(PYTHON) scripts/acceptance/agent.py --layer B $(E2E_ARGS)
e2e-lifecycle-q: ## 生命周期快速层，真实HTTP/Kitex/SQLite与确定性派发边界
	$(GO) test ./internal/platform -count=1 -run 'Test(P08Cancellation|P07Retry|PublicationUnknown|T08Shutdown|P14Foreground|I06Cookie|P13Restart|DeletionPreview|DeletionCross|DeletionCompletion|Deletion404|ShutdownCloses|F04Lifecycle|Shared|F04Encrypted)' $(E2E_ARGS)

e2e-lifecycle: ## 发布删除恢复真实集群验收，E2E_ARGS必须明确环境与冻结来源
	$(PYTHON) scripts/acceptance/lifecycle.py $(E2E_ARGS)
lifecycle-q-receipt: ## 生命周期Q层逐稳定子例收据，保留真实宿主与profile
	$(PYTHON) scripts/acceptance/lifecycle.py --layer Q $(E2E_ARGS)
lifecycle-fixture-image: ## 仅验收应用派发屏障镜像，产品源码和基础镜像必须同源
	docker build --platform "$(PLATFORM)" --provenance=false --sbom=false --build-arg PLATFORM_IMAGE="$(IMAGE)" --build-arg SOURCE_SHA="$(SOURCE_SHA)" -f scripts/acceptance/Dockerfile.lifecycle -t "$(LIFECYCLE_FIXTURE_IMAGE)" .
