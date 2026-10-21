"""由 Ginkgo It 逐键驱动原业务 Session；Python 主线程保留 lane、账本与清理责任。"""
import json
import os
from pathlib import Path
import socket
import tempfile

from execution import reference
from session import atomic_json


def run_specs(session, entries, execute, *, fail_fast=False):
    """接收已登记用例的一次性请求；本例结束后才允许 Ginkgo 调度下一例。"""
    if not entries:
        return
    expected = {(e['variantId'], e['layer']): e for e in entries}
    if len(expected) != len(entries):
        raise ValueError('duplicate-ginkgo-dispatch-key')
    completed = set()
    report = session.private / 'ginkgo-e2e.json'
    if report.exists():
        raise ValueError('ginkgo-dispatch-report-already-exists')
    # macOS 的 Unix socket 路径长度有限；临时目录由系统创建为 0700，并在 finally 中移除。
    with tempfile.TemporaryDirectory(prefix='mm-ginkgo-') as directory:
        address = str(Path(directory) / 'dispatch.sock')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(address)
            os.chmod(address, 0o600)
            server.listen(1)
            server.settimeout(.1)
            plan = session.private / 'ginkgo-dispatch.json'
            atomic_json(plan, dict(socket=address, entries=[dict(variantId=e['variantId'], layer=e['layer']) for e in entries]))
            env = dict(os.environ, MONITOR_MGR_GINKGO_PLAN=str(plan))
            command = ['go', 'test', './tests/e2e', '-count=1', '-timeout=90m', '-args',
                       '-ginkgo.no-color', '-ginkgo.fail-on-empty', '-ginkgo.json-report=' + str(report)]
            if fail_fast:
                command.append('-ginkgo.fail-fast')
            child = session.processes.popen(command, label='ginkgo-e2e-scheduler', cwd=Path(__file__).resolve().parents[2], env=env)
            while child.poll() is None:
                session.checkpoint()
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue
                with connection:
                    connection.settimeout(5)
                    with connection.makefile('rwb') as stream:
                        # 关键：只接受本计划中尚未执行的键，禁止通过 IPC 指定任意命令或重复写入集群。
                        raw = stream.readline(4097)
                        if len(raw) > 4096 or not raw.endswith(b'\n'):
                            raise ValueError('invalid-ginkgo-dispatch-request')
                        request = json.loads(raw)
                        key = (request.get('variantId'), request.get('layer'))
                        if key not in expected or key in completed:
                            raise ValueError('unregistered-or-repeated-ginkgo-case')
                        completed.add(key)
                        entry = expected[key]
                        execute(entry)
                        result = next(r for r in session.index['results'] if (r['variantId'], r['layer']) == key)
                        stream.write(json.dumps(dict(variantId=key[0], layer=key[1], outcome=result['outcome'])).encode() + b'\n')
                        stream.flush()
    # 只导出逐例状态和尝试次数；含 traceback、浏览器日志与私有路径的原报告不公开。
    reports = json.loads(report.read_text()) if report.is_file() else []
    projection = dict(framework='ginkgo', commandExitCode=child.returncode, specs=[])
    valid = len(reports) == 1 and reports[0].get('SuiteSucceeded') is True and not reports[0].get('SuiteConfig', {}).get('DryRun')
    if reports:
        projection['specs'] = [dict(key=s.get('LeafNodeText'), state=s.get('State'), attempts=s.get('NumAttempts'))
                               for s in reports[0].get('SpecReports', []) if s.get('LeafNodeType') == 'It']
        os.chmod(report, 0o600)
    safe = session.out / 'events' / 'ginkgo-e2e.json'
    atomic_json(safe, projection)
    session.index['ginkgoDispatchRef'] = reference(safe, session.out)
    session.flush()
    actual = {s['key'] for s in projection['specs'] if s['state'] == 'passed' and s['attempts'] == 1}
    required = {layer + ':' + variant for variant, layer in expected}
    if child.returncode or not valid or completed != set(expected) or actual != required or len(projection['specs']) != len(expected):
        raise ValueError('actual-ginkgo-e2e-not-passed')
