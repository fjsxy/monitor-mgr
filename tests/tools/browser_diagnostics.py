"""UT-18：浏览器私有报告只能公开固定步骤、合法源码坐标与数字统计。"""
import copy
import json
from pathlib import Path


def check_browser_diagnostics(test):
    from e2e_diagnostics import browser_result_summary
    source = Path(__file__).resolve().parents[2] / 'web/tests/phase1.spec.ts'
    lines = source.read_text().splitlines()
    title = 'I29-B01 历史单资源版本只读，新明确发布才补建并展示独立进度'
    line = next(index for index, value in enumerate(lines, 1) if title in value)
    location = dict(file=str(source), line=line, column=5)
    safe_location = dict(file='web/tests/phase1.spec.ts', line=line, column=5)
    private = 'PRIVATE_COOKIE_CONFIG_DOM_URL_ERROR_SENTINEL'
    failure = dict(status='failed', error=dict(message=private, stack=private, snippet=private, location=location),
                   steps=[dict(title=title, category='test.step', location=location,
                               steps=[dict(title=private, category='expect', location=location, error=dict(message=private))])])
    report = dict(stats=dict(expected=0, unexpected=1, flaky=0, skipped=0, duration=100.125, startTime=private),
                  suites=[dict(title=private, specs=[dict(title=private, file=private, tests=[dict(results=[failure])])])],
                  config=dict(cookie=private), errors=[dict(message=private)])
    expected = dict(stats=dict(expected=0, unexpected=1, flaky=0, skipped=0, duration=100.125),
                    failures=[dict(steps=[title], locations=[safe_location])])
    test.assertEqual(browser_result_summary(report, source), expected)
    test.assertNotIn(private, json.dumps(browser_result_summary(report, source)))
    # 真实JSON reporter的超时步骤只有title/duration，未完成步骤为-1，无category/error/location。
    actual_shape = dict(stats=dict(expected=0, unexpected=1, duration=180000), suites=[dict(specs=[dict(tests=[dict(results=[dict(
        status='timedOut', error=dict(message=private, stack=private), steps=[
            dict(title='登录并用表单创建 KSM 草稿', duration=10), dict(title=title, duration=-1),
            dict(title=private, duration=-1)])])])])])
    test.assertEqual(browser_result_summary(actual_shape, source), dict(stats=dict(expected=0, unexpected=1, duration=180000),
                                                                     failures=[dict(steps=[title], locations=[])]))
    # 关键：文件后缀相同仍不是本候选源码；无效坐标、动态标题与非数字统计不能进入公共产物。
    for invalid in [dict(file='/tmp/private/web/tests/phase1.spec.ts', line=line, column=5),
                    dict(file=str(source), line=0, column=5), dict(file=str(source), line=len(lines)+1, column=5),
                    dict(file=str(source), line=line, column=0), dict(file=str(source), line=line, column=len(lines[line-1])+2)]:
        broken = copy.deepcopy(report)
        result = broken['suites'][0]['specs'][0]['tests'][0]['results'][0]
        result['error']['location'] = invalid
        result['steps'] = [dict(title=private, category='test.step', location=invalid, error=dict(message=private))]
        broken['stats'] = dict(expected=True, unexpected=private, duration=float('nan'), secret=private)
        safe = browser_result_summary(broken, source)
        test.assertEqual(safe, dict(stats={}, failures=[dict(steps=[], locations=[])]))
        test.assertNotIn(private, json.dumps(safe))
    for invalid in (None, private, [], dict(suites=private, stats=[])):
        test.assertEqual(browser_result_summary(invalid, source), dict(stats={}, failures=[]))
