"""失败时只投影冻结源码位置；异常正文、HTTP 响应与局部变量保持私有。"""
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def browser_result_summary(report, source):
    """只投影本候选固定test.step、合法源码坐标和数字统计，不读取异常正文或stack。"""
    result = dict(stats={}, failures=[])
    if not isinstance(report, dict):
        return result
    source = Path(source).resolve()
    try:
        lines = source.read_text().splitlines()
    except OSError:
        lines = []
    titles = set(re.findall(r"\btest\.step\(\s*'([^'\\\r\n]*)'", '\n'.join(lines)))
    stats = report.get('stats')
    if isinstance(stats, dict):
        for key in ('expected', 'unexpected', 'flaky', 'skipped'):
            value = stats.get(key)
            if type(value) is int and 0 <= value <= 10000:
                result['stats'][key] = value
        value = stats.get('duration')
        if type(value) in (int, float) and 0 <= value <= 1e9 and math.isfinite(value):
            result['stats']['duration'] = value

    def items(value, key):
        selected = value.get(key) if isinstance(value, dict) else None
        return selected[:1000] if isinstance(selected, list) else []

    def add_location(value, locations):
        if not isinstance(value, dict) or value.get('file') not in (str(source), 'web/tests/phase1.spec.ts', 'tests/phase1.spec.ts'):
            return
        line, column = value.get('line'), value.get('column')
        # 关键：只输出固定文件名与实际源码内坐标；不能凭后缀认领其他文件或公开文本片段。
        if type(line) is int and type(column) is int and 1 <= line <= len(lines) and 1 <= column <= len(lines[line-1]) + 1:
            location = dict(file='web/tests/phase1.spec.ts', line=line, column=column)
            if location not in locations:
                locations.append(location)

    def project_failure(value):
        if len(result['failures']) >= 64:
            return
        failure = dict(steps=[], locations=[])
        for error in [value.get('error'), *items(value, 'errors')]:
            if isinstance(error, dict):
                add_location(error.get('location'), failure['locations'])
        pending = [(step, [], 0) for step in items(value, 'steps')]
        for _ in range(1000):
            if not pending:
                break
            step, parents, depth = pending.pop(0)
            if not isinstance(step, dict) or depth > 20:
                continue
            title = step.get('title')
            chain = parents + ([title] if isinstance(title, str) and title in titles else [])
            error = step.get('error')
            # JSON reporter可能只保留title/duration；-1表示未完成，不能要求有category或异常正文。
            if isinstance(error, dict) or step.get('duration') == -1:
                for known in chain:
                    if known not in failure['steps']:
                        failure['steps'].append(known)
                add_location(step.get('location'), failure['locations'])
                if isinstance(error, dict):
                    add_location(error.get('location'), failure['locations'])
            pending.extend((child, chain, depth+1) for child in items(step, 'steps'))
        failure['locations'].sort(key=lambda location: (location['line'], location['column']))
        result['failures'].append(failure)

    pending = items(report, 'suites')
    for _ in range(1000):
        if not pending or len(result['failures']) >= 64:
            break
        suite = pending.pop(0)
        pending.extend(items(suite, 'suites'))
        for spec in items(suite, 'specs'):
            for test in items(spec, 'tests'):
                for value in items(test, 'results'):
                    if isinstance(value, dict) and value.get('status') in ('failed', 'timedOut', 'interrupted'):
                        project_failure(value)
    return result


def failure_summary(error):
    """关键：API 消息必须精确命中本候选的固定字面量，才可输出其源码位置。"""
    frames = []
    trace = error.__traceback__
    while trace:
        path = Path(trace.tb_frame.f_code.co_filename).resolve()
        if path.is_relative_to(ROOT / 'scripts'):
            frames.append(dict(file=str(path.relative_to(ROOT)), line=trace.tb_lineno))
        trace = trace.tb_next
    result = dict(frames=frames[-8:])
    args = error.args
    if not isinstance(error, AssertionError) or len(args) != 1 or not isinstance(args[0], tuple) or len(args[0]) != 4:
        return result
    _, status, code, message = args[0]
    if type(status) is not int or not 100 <= status <= 599 or not isinstance(message, str):
        return result
    result['httpStatus'] = status
    sites = []
    for path in sorted((ROOT / 'internal/control').glob('*.go')):
        if path.name.endswith('_test.go'): continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            for expected_code, literal in re.findall(r'Error\("([A-Z_]+)",\s*("(?:[^"\\]|\\.)*")\)', line):
                if code == expected_code and message == json.loads(literal):
                    sites.append(dict(file=str(path.relative_to(ROOT)), line=number))
    if sites:
        result.update(apiCode=code, apiSites=sites)
    return result
