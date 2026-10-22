"""固定 E2E 分组与完整汇总；只改变调度，不改变10项业务覆盖。"""
import argparse
from collections import Counter
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 按 run 39620800818 的真实耗时平衡三组；每组使用独立 runner 与双集群。
SHARDS = {
    'topology': ['E2E-01', 'E2E-03', 'E2E-04', 'E2E-10'],
    'storage': ['E2E-05', 'E2E-09'],
    'recovery': ['E2E-02', 'E2E-06', 'E2E-07', 'E2E-08'],
}


def selection(shard=None):
    """关键：分组必须完整且互斥；默认入口不能意外变成部分运行。"""
    expected = [c['id'] for c in json.loads((ROOT / 'docs/acceptance/cases.json').read_text())['cases'] if c['type'] == 'e2e']
    assigned = [id for ids in SHARDS.values() for id in ids]
    if len(expected) != 10 or len(set(expected)) != 10 or Counter(assigned) != Counter(expected):
        raise ValueError('invalid-shard-partition')
    if shard is not None and shard not in SHARDS:
        raise ValueError('unknown-shard')
    return list(SHARDS[shard] if shard is not None else expected)


def aggregate(reports, candidate, run_id, jobs_result):
    """关键：矩阵 job（含清理）成功与逐例成功均必需，缺组/错来源不能借其他组变绿。"""
    expected = selection()
    if jobs_result != 'success':
        raise ValueError('shard-jobs-not-passed')
    if Counter(r.get('shard') for r in reports) != Counter(SHARDS.keys()):
        raise ValueError('missing-or-duplicate-shard')
    combined = {}
    for report in reports:
        shard = report['shard']; ids = selection(shard)
        if (report.get('schemaVersion') != 3 or report.get('kind') != 'e2e-shard'
                or report.get('candidate') != candidate or report.get('runId') != run_id + '-' + shard):
            raise ValueError('shard-source-mismatch')
        if (report.get('assigned') != ids or report.get('passed') is not True
                or report.get('cleanup') is not True or report.get('environment') != 'ready'):
            raise ValueError('shard-not-passed')
        cases = report.get('results', {}); specs = report.get('ginkgo', [])
        if Counter(cases.keys()) != Counter(ids) or Counter(s.get('id') for s in specs) != Counter(ids):
            raise ValueError('missing-or-duplicate-case')
        if any(s.get('state') != 'passed' or s.get('attempts') != 1 for s in specs):
            raise ValueError('ginkgo-case-not-passed')
        if any(c.get('id') != id or c.get('outcome') != 'passed' or c.get('cleanup') != [] for id, c in cases.items()):
            raise ValueError('scenario-not-passed')
        combined.update(cases)
    if Counter(combined.keys()) != Counter(expected):
        raise ValueError('incomplete-e2e')
    return dict(schemaVersion=3, kind='e2e-complete', candidate=candidate, runId=run_id,
                passed=True, cleanup=True, shards=list(SHARDS), results={id: combined[id] for id in expected})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True); parser.add_argument('--output', required=True)
    parser.add_argument('--candidate', required=True); parser.add_argument('--run-id', required=True)
    parser.add_argument('--jobs-result', required=True)
    args = parser.parse_args()
    reports = [json.loads(p.read_text()) for p in sorted(Path(args.input).rglob('results.json'))]
    result = aggregate(reports, args.candidate, args.run_id, args.jobs_result)
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print('E2E 汇总通过：三个独立组，10项恰好各一次，清理成功；candidate=' + args.candidate)


if __name__ == '__main__': main()
