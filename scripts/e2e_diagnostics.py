"""失败时只投影冻结源码位置；异常正文、HTTP 响应与局部变量保持私有。"""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


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
