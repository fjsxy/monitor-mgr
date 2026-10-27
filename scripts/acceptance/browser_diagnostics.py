"""Playwright 原报告保持私有，只投影固定步骤、当前源码位置与白名单状态枚举。"""
import json
from pathlib import Path
import re

FILES = ('web/tests/phase1.spec.ts', 'web/tests/issue25.ts', 'web/tests/issue29.ts', 'web/tests/issue30.ts')
STATES = {'Ready', 'NotReady', 'Reconciling', 'Pending', 'Disabled', 'Paused', 'Degraded', 'Unknown',
          'SUCCEEDED', 'FAILED', 'CHECKING', 'ACCEPTED', 'true', 'false', 'undefined', 'null', '0', '1',
          '200', '201', '202', '400', '401', '403', '404', '409', '422', '500', '503'}


def browser_summary(report, root):
    """关键：不输出报告标题、错误原文、附件、响应或任意路径；未知值统一降为 unknown。"""
    root = Path(root).resolve()
    sources = {name: (root/name).read_text().splitlines() for name in FILES if (root/name).is_file()}
    titles = {title for lines in sources.values() for line in lines
              for title in re.findall(r"test\.step\(['\"]([^'\"]+)['\"]", line)}
    result = dict(caseId='E2E-10', failures=[])

    def frames(error):
        found = []
        location = error.get('location', {})
        locations = [location] if isinstance(location, dict) else []
        stack = error.get('stack', '')
        if isinstance(stack, str):
            locations += [dict(file=file, line=int(line), column=int(column)) for file, line, column
                          in re.findall(r'\(([^()\r\n]+):(\d+):(\d+)\)', stack)]
        for location in locations:
            file, line = location.get('file'), location.get('line')
            if not isinstance(file, str) or type(line) is not int:
                continue
            try:
                path = Path(file)
                path = (root/path).resolve() if not path.is_absolute() else path.resolve()
                name = str(path.relative_to(root))
            except (ValueError, OSError):
                continue
            if name in sources and 1 <= line <= len(sources[name]):
                frame = dict(file=name, line=line)
                if frame not in found:
                    found.append(frame)
        return found[:8]

    def failure(error, title):
        if not isinstance(error, dict):
            return
        locations = frames(error)
        value = dict(step=title, expected='unknown', actual='unknown', frames=locations, **(locations[0] if locations else {}))
        # 仅解析有可信源码位置的精确 Expected/Received 行；颜色码、消息其余部分永不转发。
        message = error.get('message', '')
        if 'file' in value and isinstance(message, str):
            message = re.sub(r'\x1b\[[0-9;]*m', '', message)
            for label, key in [('Expected', 'expected'), ('Received', 'actual')]:
                for match in re.findall(r'^' + label + r':\s*([^\r\n]+)$', message, re.MULTILINE):
                    state = match.strip().strip('"\'')
                    if state in STATES:
                        value[key] = state
        if value not in result['failures']:
            result['failures'].append(value)

    def steps(items, parent='unknown'):
        if not isinstance(items, list):
            return
        for step in items:
            if not isinstance(step, dict):
                continue
            title = step.get('title')
            title = title if isinstance(title, str) and title in titles else parent
            if step.get('error'):
                failure(step['error'], title)
            steps(step.get('steps', []), title)

    def suites(items):
        if not isinstance(items, list):
            return
        for suite in items:
            if not isinstance(suite, dict):
                continue
            for spec in suite.get('specs', []):
                for test in spec.get('tests', []):
                    for attempt in test.get('results', []):
                        if attempt.get('status') in ('failed', 'timedOut', 'interrupted'):
                            before = len(result['failures'])
                            steps(attempt.get('steps', []))
                            if len(result['failures']) == before:
                                failure(attempt.get('error', {}), 'unknown')
            suites(suite.get('suites', []))
    suites(report.get('suites', []) if isinstance(report, dict) else [])
    return result


def save_browser_summary(report_path, destination, root):
    """超时或损坏报告也生成固定诊断，不公开解析异常正文。"""
    try:
        path = Path(report_path)
        report = json.loads(path.read_text()) if path.is_file() and path.stat().st_size <= 10*1024*1024 else {}
        value = browser_summary(report, root)
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        value = dict(caseId='E2E-10', failures=[], diagnostic='unavailable')
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    Path(destination).write_text(json.dumps(value, ensure_ascii=False, indent=2))
