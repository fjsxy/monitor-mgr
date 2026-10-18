SHELL := /bin/sh
.DEFAULT_GOAL := help
GO ?= go
NPM ?= npm
PYTHON ?= python3
HTTP_ADDR ?= 127.0.0.1:18080
RPC_ADDR ?= 127.0.0.1:18888
DATA_DIR ?= data/local
IMAGE ?= monitor-mgr:dev
PLATFORM ?= linux/arm64
BASE_URL ?= http://127.0.0.1:18080
E2E_ARGS ?=

.PHONY: help generate check-generated test test-race vet web-install web-build web-e2e build run image acceptance-plan e2e-clusters check
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
	BASE_URL="$(BASE_URL)" $(NPM) --prefix web run test:e2e
build: ## 构建平台二进制
	mkdir -p bin
	$(GO) build -o bin/monitor-mgr ./cmd/monitor-mgr
run: build web-build ## 启动本地平台（DATA_DIR/HTTP_ADDR/RPC_ADDR 可配置）
	./bin/monitor-mgr -data-dir "$(DATA_DIR)" -http "$(HTTP_ADDR)" -rpc "$(RPC_ADDR)" -static web/dist
image: ## 构建固定平台镜像（IMAGE/PLATFORM 可配置）
	docker build --platform "$(PLATFORM)" --provenance=false --sbom=false -t "$(IMAGE)" .
acceptance-plan: ## 校验验收目录元数据，不代表运行通过
	$(PYTHON) scripts/validate-acceptance.py
e2e-clusters: ## 真实集群验收；通过 E2E_ARGS 显式指定环境与来源
	$(PYTHON) scripts/acceptance/clusters.py $(E2E_ARGS)
check: acceptance-plan test-race vet web-build ## 运行本地质量检查，不替代完整集群门禁
