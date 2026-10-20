#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# 锁定生成器与运行库版本；生成文件入库，升级必须显式执行。
mkdir -p .tools
GOTOOLCHAIN=go1.27.1 GOBIN="$PWD/.tools" go install github.com/cloudwego/kitex/tool/cmd/kitex@v0.16.3
GOTOOLCHAIN=go1.27.1 GOBIN="$PWD/.tools" go install github.com/cloudwego/thriftgo@v0.4.4
# 关键：保留未知 wire 字段供业务授权后拒绝，避免 Thrift 静默丢字段后命中旧幂等结果。
PATH="$PWD/.tools:$PATH" .tools/kitex -module github.com/fjsxy/monitor-mgr -thrift no_default_serdes,keep_unknown_fields idl/control.thrift
