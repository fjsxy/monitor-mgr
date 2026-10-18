#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Keep generator and runtime upgrades explicit. Generated output is committed.
mkdir -p .tools
GOTOOLCHAIN=go1.27.1 GOBIN="$PWD/.tools" go install github.com/cloudwego/kitex/tool/cmd/kitex@v0.16.3
GOTOOLCHAIN=go1.27.1 GOBIN="$PWD/.tools" go install github.com/cloudwego/thriftgo@v0.4.4
PATH="$PWD/.tools:$PATH" .tools/kitex -module github.com/fjsxy/monitor-mgr -thrift no_default_serdes idl/control.thrift
