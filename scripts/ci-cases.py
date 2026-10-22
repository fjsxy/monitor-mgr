#!/usr/bin/env python3
"""从唯一固定清单生成 CI 文档，并核验 Go UT/真实集群场景没有额外/缺失 ID。"""
import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from e2e_shards import SHARDS, selection

ROOT=Path(__file__).resolve().parents[1]
DESTINATION=ROOT/'docs/acceptance/ci-cases.md'


def document():
    catalog=json.loads((ROOT/'docs/acceptance/cases.json').read_text());cases=catalog['cases']
    assert Counter(c['type'] for c in cases)=={'ut':18,'e2e':10}
    assert len({c['id'] for c in cases})==28
    actual=[]
    for path in (ROOT/'internal').rglob('*_test.go'):
        text=path.read_text();actual+=re.findall(r'ginkgo.It\("(UT-\d+) ',text)
        traditional=re.findall(r'^func (Test\w+)\(',text,re.M)
        assert not traditional or path.name=='ginkgo_suite_test.go',str(path)
        assert 't.Run(' not in text,str(path)
    assert Counter(actual)==Counter(c['id'] for c in cases if c['type']=='ut'),actual
    tree=ast.parse((ROOT/'scripts/e2e_scenarios.py').read_text())
    mapping=next(n.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='CASES' for t in n.targets))
    assert [k.value for k in mapping.keys]==[c['id'] for c in cases if c['type']=='e2e']
    workflow=(ROOT/'.github/workflows/phase1.yml').read_text();make=(ROOT/'Makefile').read_text()
    assert 'make ut' in workflow and 'make e2e' in workflow
    selection()
    assert 'shard: ['+', '.join(SHARDS)+']' in workflow and 'needs: e2e' in workflow and 'make e2e-summary' in workflow
    lines=['# CI 测试清单与选择标准','', '本文件由 `make ci-cases` 生成；`make ci-cases-check` 核对 18 个 Ginkgo UT 与 10 条 Ginkgo E2E 实现。每行一个 It，同行中的必要输入/阶段用 By 定位，不能跳过后算通过。','',
           '| CI job | 执行入口与内容 | 通过标准 |','| --- | --- | --- |',
           '| UT | `make ut`：18 个 Ginkgo UT，一次 race/覆盖率；生成一致性、vet、前端类型检查/构建 | 全部18项通过，无漏跑/skip/retry/race，手写产品语句覆盖率≥70% |',
           '| E2E | 三个 matrix runner 并行；各自 `make ci-builder` 和 `make e2e --shard`（通过 E2E_ARGS 传入）；独立 control(1cp+2worker)/data(1可调度cp)，Helm 部署固定依赖和平台；组内串行 | 三组 job 均成功；`make e2e-summary` 校验同候选/本次run、恰好10项各执行一次，无skip/retry，场景/原生Ginkgo/清理均成功；部分通过不是完整E2E通过 |','',
           '两类 job 在可信同仓 PR 和 main 使用同一清单；E2E 依赖私有 Operator 源码，凭据仅在获取来源步骤使用。构建/生成检查不另算测试类型。','',
           '选取标准：UT 覆盖授权、凭据、UID/PVC、事务、恢复和协议的独立规则；E2E 只保留 Kubernetes/RBAC/Operator/真实卷和浏览器接线才能证明的行为。等价输入合并复用前置，后端接口优先；浏览器仅 E2E-10。','',
           '明确不自动化：B-31 细布局/像素/横向溢出，B-32 轮询时焦点/滚动/多选展示，B-33 下拉联动/排序的浏览器组合；共3/33已识别风险组（9.1%）。后端筛选排序和大 i64 精度仍保留。该比例不是语句覆盖率。','']
    lines+=['## E2E 并行分配','','按 run 39620800818 的逐例实测分组，三组用例本身约308/296/271秒；每组重新准备环境，增加 runner 总用量以减少等待。组名进入 runId、集群名、BuildKit 名和 artifact；私有源码/镜像不跨 job 上传或缓存。某组失败不取消其他组，最后汇总不能跳过失败组。','','| matrix.shard | 固定用例 |','| --- | --- |']
    lines += ['| '+shard+' | '+ '、'.join(ids)+' |' for shard,ids in SHARDS.items()]
    lines += ['', '`make e2e` 默认仍执行全部10项；单组使用 `make e2e E2E_ARGS="--shard topology ..."`。单组报告标记 `e2e-shard`，只有三个组通过汇总才形成 `e2e-complete`。分组和完整结果分别保存，缺组/错组/错SHA/错run/失败/skip/retry/清理失败均不能通过。','']
    for kind,title in [('ut','UT：18 个'),('e2e','E2E：10 个')]:
        lines+=['## '+title,'','| ID / 验证目的 | 必要动作 | 决定性结果 / 进入 CI 的理由 |','| --- | --- | --- |']
        for c in cases:
            if c['type']==kind:lines.append('| '+c['id']+' '+c['purpose']+' | '+c['steps'].replace('|','\\|')+' | '+c['expected'].replace('|','\\|')+' |')
        lines+=['']
    lines+=['## 来源与报告','', '`cases.json` 为当前固定目录，`test-plan.md` 保存旧 ID 的合并/保留/放弃映射。`phase1.json` 和旧组件 runner 只供历史追溯/复用断言，不再作为 CI profile 或额外 case；旧 254 项工具自测不再执行，UT-18 仅三组必要可信边界。','',
            '`make ut` 生成 `.scratch/ut-results.json` 和 `.scratch/coverage.out`；`make e2e` 输出 `.scratch/e2e/<runId>/public/results.json`（含阶段/逐例耗时）、`cleanup.json` 和浏览器截图。CI 三组 artifact 为 `e2e-<runId>-<attempt>-<shard>`，完整汇总为 `e2e-complete-<runId>-<attempt>`。原始日志/报告、kubeconfig、数据库、证书、私有源码仅在同 run 的 `.private/`。CI 只上传 public 和 UT 安全摘要。','',
            '入口摘要：`Makefile` SHA256='+hashlib.sha256(make.encode()).hexdigest()+'；workflow SHA256='+hashlib.sha256(workflow.encode()).hexdigest()+'。','']
    return '\n'.join(lines)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args();expected=document()
    if args.check:
        if not DESTINATION.is_file() or DESTINATION.read_text()!=expected:raise SystemExit('CI 清单过时：执行 make ci-cases')
        print('CI 清单一致：18 UT / 10 E2E')
    else:DESTINATION.write_text(expected)
