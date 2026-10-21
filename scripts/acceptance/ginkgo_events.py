"""Ginkgo 原生报告到稳定 selector 事件的安全投影；原日志只进入私有目录。"""
import json
import os
from pathlib import Path
import re


def command(selectors, report):
    """按原场景根选择 It；Ginkgo focus 包含 suite 名，须匹配空格后的完整根名。"""
    roots = sorted({selector.split('/')[0] for selector in selectors})
    if not roots:
        raise ValueError('empty-ginkgo-selection')
    return ['go', 'test', './internal/platform', '-count=1', '-timeout=15m', '-args',
            '-ginkgo.focus=(^| )(' + '|'.join(re.escape(root) for root in roots) + ')$',
            '-ginkgo.fail-on-empty', '-ginkgo.no-color', '-ginkgo.json-report=' + str(Path(report).resolve())]


def project(raw, roots, returncode):
    """只承认实际完成一次的 It 和完成标识；dry-run、重试、跳过或空报告均不能通过。"""
    try:
        reports = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(reports, list) or len(reports) != 1:
        return []
    report = reports[0]
    if not isinstance(report, dict) or report.get('SuiteConfig', {}).get('DryRun'):
        return []
    valid_suite = returncode == 0 and report.get('SuiteSucceeded') is True and not report.get('SuiteHasProgrammaticFocus')
    events, seen = [], set()
    for spec in report.get('SpecReports', []):
        root = spec.get('LeafNodeText')
        if root not in roots or spec.get('LeafNodeType') != 'It':
            continue
        valid = valid_suite and spec.get('State') == 'passed' and spec.get('NumAttempts') == 1
        state = 'pass' if valid else 'skip' if spec.get('State') == 'skipped' else 'fail'
        if root in seen:
            state = 'fail'
        seen.add(root)
        events.append(dict(Framework='ginkgo', Action=state, Test=root))
        if not valid:
            continue
        # 关键：不复制 CapturedStdOutErr、Failure、Location 或任意 report value，防止凭据进入公开证据。
        children = set()
        for entry in spec.get('ReportEntries', []):
            if entry.get('Name') != '完成子例':
                continue
            try:
                selector = json.loads(entry['Value']['AsJSON'])
            except (ValueError, KeyError, TypeError):
                continue
            if not isinstance(selector, str) or not selector.startswith(root + '/'):
                continue
            duplicate = selector in children
            children.add(selector)
            events.append(dict(Framework='ginkgo', Action='fail' if duplicate else 'pass', Test=selector))
    return events


def passed(events):
    """父场景任何失败都阻止同根子例通过，避免只看到局部通过事件就放行。"""
    rejected = {e['Test'].split('/')[0] for e in events if e['Action'] != 'pass'}
    return {e['Test'] for e in events if e['Action'] == 'pass' and e['Test'].split('/')[0] not in rejected}


def run(selectors, private_report, execute, **kwargs):
    """执行原生 suite，读取本次新报告并返回安全事件；不复用历史报告。"""
    report = Path(private_report)
    report.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if report.exists():
        raise ValueError('ginkgo-report-already-exists')
    argv = command(selectors, report)
    result = execute(argv, **kwargs)
    if report.is_file():
        os.chmod(report, 0o600)
    events = project(report.read_bytes() if report.is_file() else b'', {s.split('/')[0] for s in selectors}, result.returncode)
    return argv, result, events
