#!/usr/bin/env python3
"""按#21独立五列计划生成名称边界Q报告；不注册中央207/104键或执行浏览器。"""
import argparse
from workbench import ROOT, run_q


def main():
    parser = argparse.ArgumentParser(description='REG21名称边界独立Q报告，仍经真实HTTP/Kitex')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    args.profile, args.layer, args.allow_dirty_runner = 'LOCAL_FULL', 'REG', False
    variant = 'REG21-AGENT-NAME-001'
    rows = [line.strip('|').split('|') for line in
            (ROOT / 'docs/e2e/issue-21-u05-name.md').read_text().splitlines()]
    rows = [[cell.strip() for cell in row] for row in rows]
    selected = [row for row in rows if len(row) == 5 and row[0] == variant]
    assert len(selected) == 1, '名称边界计划必须唯一且保留五列'
    row = selected[0]
    contract = {'variantId': variant, 'caseId': variant, 'layer': 'Q', 'kind': 'supplementary',
                'purpose': row[1], 'steps': row[2], 'expected': row[3], 'profiles': ['LOCAL_FULL'],
                'executionScope': '独立名称边界Q；真实Pod名称B子步骤另报，不计中央矩阵键',
                'formalProfilePassed': False, 'fullRegressionComplete': False, 'centralMatrixKey': False}
    selectors = ['TestAgentRevisionNameBudgetHTTP', 'TestAgentRevisionNameBudgetLegacyAndWorker']
    return run_q(args, {variant: contract}, [variant], case_tests={variant: selectors}, owned_count=0)


if __name__ == '__main__':
    raise SystemExit(main())
