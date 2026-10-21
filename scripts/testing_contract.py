"""新 UT/E2E 共用的最小结果与清理边界；不再解释旧 profile/收据矩阵。"""
from collections import Counter


def validate_specs(report, expected, candidate, actual_candidate):
    """关键：只有同来源、逐项唯一、无重试/跳过的完整 Ginkgo 报告才能通过。"""
    if candidate != actual_candidate:
        raise ValueError('candidate-mismatch')
    if len(report) != 1 or not report[0].get('SuiteSucceeded'):
        raise ValueError('suite-not-passed')
    suite = report[0]
    if suite.get('SuiteConfig', {}).get('DryRun') or suite.get('SuiteHasProgrammaticFocus'):
        raise ValueError('partial-suite')
    specs = [s for s in suite.get('SpecReports', []) if s.get('LeafNodeType') == 'It']
    actual = [s['LeafNodeText'].split(' ', 1)[0] for s in specs]
    if Counter(actual) != Counter(expected) or len(set(expected)) != len(expected):
        raise ValueError('missing-or-duplicate-case')
    if any(s.get('State') != 'passed' or s.get('NumAttempts') != 1 for s in specs):
        raise ValueError('case-not-passed')
    return specs


def owned_identity(run_id, recorded, actual):
    """关键：按原 UID 和本 run 标签判断清理资格；同名替代对象不能被借用。"""
    return (bool(recorded.get('uid')) and actual.get('uid') == recorded['uid']
            and actual.get('runId') == recorded.get('runId') == run_id)
