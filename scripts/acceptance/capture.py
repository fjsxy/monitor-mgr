#!/usr/bin/env python3
"""在公开 console 前捕获子进程原字节；公开信息仅有固定 ID、exit 与 hash。"""
import argparse
import ast
import hashlib
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import time
import uuid

from session import atomic_json

ROOT = pathlib.Path(__file__).resolve().parents[2]
# 仅覆盖固定workflow的质量命令及其静态依赖；不按前缀认领动态target。
QUALITY_MAKE_TARGETS = frozenset(('acceptance-plan', 'acceptance-host-check', 'test-race', 'vet',
                                 'web-build', 'generate', 'check-generated'))
# 固定Go1.27.1 testing.runningList使用time.Duration.String；只识别格式，不公开动态时长。
GO_DURATION = r'(?:[0-9]+(?:\.[0-9]+)?(?:ns|us|µs|ms|s|m|h))+'
# 关键：只公开审阅过的PrepareError字面量；动态label/转入异常值保持unknown，不能按前缀放行。
PREPARE_CODES = frozenset('''
PREPARE_AGENT_CONTAINER_NOT_RUNNING PREPARE_AGENT_POD_OWNER_MISMATCH PREPARE_AGENT_PROBE_GC_INCOMPLETE
PREPARE_AGENT_RELOADER_PATH_NOT_EXECUTED PREPARE_AGENT_STATEFULSET_NOT_CREATED PREPARE_AGENT_STATEFULSET_OWNER_MISMATCH
PREPARE_APPLIED_UID_MISSING PREPARE_CANDIDATE_DIRTY_OR_WRONG PREPARE_CLUSTER_EXISTS PREPARE_CLUSTER_UID_CHANGED
PREPARE_CONTAINER_CHECKPOINT_INVALID PREPARE_CONTAINER_ID_MISSING PREPARE_CONTROLLER_IMAGE_MISMATCH
PREPARE_CONTROLLER_NOT_READY PREPARE_CONTROLLER_RUNTIME_DIGEST_MISMATCH PREPARE_CRD_NOT_ESTABLISHED
PREPARE_CRI_IMAGE_OR_VERSION_TAG_MISMATCH PREPARE_DATA_NODE_IDENTITY_CHANGED PREPARE_DATA_NODE_NOT_SCHEDULABLE
PREPARE_DATA_NODE_RESPONSE_INVALID PREPARE_DATA_NOT_SCHEDULABLE PREPARE_DEFAULT_STORAGE_AMBIGUOUS
PREPARE_KUBECONFIG_EXISTS PREPARE_LOCAL_CRI_IMAGE_MISMATCH PREPARE_NODE_NOT_READY PREPARE_NODE_TOPOLOGY_MISMATCH
PREPARE_OCI_AMD64_LOCK_MISMATCH PREPARE_OCI_ARM64_LOCK_MISMATCH PREPARE_OCI_CONTENT_BYTES_MISMATCH
PREPARE_OCI_CONTENT_CHANGED PREPARE_OCI_PLATFORM_AMBIGUOUS PREPARE_OCI_PLATFORM_MISMATCH PREPARE_POD_API_ADDRESS_MISSING
PREPARE_PRIVATE_CRD_CHANGED PREPARE_PRIVATE_DOCKERFILE_CHANGED PREPARE_PRIVATE_ROOT_INVALID
PREPARE_PRIVATE_SOURCE_MISMATCH PREPARE_PRIVATE_SOURCE_REQUIRED PREPARE_PROBE_STILL_PRESENT PREPARE_PROBE_UID_CHANGED
PREPARE_PROFILE_INVALID PREPARE_PUBLIC_BUNDLE_DIGEST_MISMATCH PREPARE_PVC_GATE_NOT_ENABLED PREPARE_RELOADER_NOT_EXECUTED
PREPARE_RELOADER_VERSION_EXIT_FAILED PREPARE_RELOADER_VERSION_MISMATCH PREPARE_RESPONSE_INVALID
PREPARE_RUNTIME_CONFIG_DIGEST_MISMATCH PREPARE_RUNTIME_CONTAINER_ID_MISSING PREPARE_RUNTIME_IMAGE_DIGEST_MISMATCH
PREPARE_RUNTIME_IMAGE_REF_MISMATCH PREPARE_RUN_ID_INVALID PREPARE_SMOKE_HOST_MISMATCH PREPARE_SMOKE_REQUIRES_LINUX_AMD64
PREPARE_SOURCE_INVALID PREPARE_TARGETS_INVALID PREPARE_TARGET_IDENTITY_INVALID PREPARE_TOOL_VERSION_MISMATCH
PREPARE_VOLUME_CAPABILITY_FAILED PREPARE_VOLUME_POD_IDENTITY_MISSING PREPARE_VOLUME_RUNTIME_NOT_CONFIRMED
RECOVER_CLUSTER_STILL_PRESENT RECOVER_CLUSTER_UID_CHANGED RECOVER_CLUSTER_UID_UNKNOWN
RECOVER_CONTAINER_UID_CHANGED RECOVER_CONTAINER_UID_UNKNOWN RECOVER_TARGETS_INVALID
'''.split())


class _FrozenSources:
    """仅查询当前HEAD的固定tracked blob；缓存按需读取，不执行测试或公开Git诊断原文。"""
    def __init__(self, candidate, root, private_root):
        self.root = pathlib.Path(root).resolve();self.private = pathlib.Path(private_root)
        self.cache = {};self.unknown = False;self.deadline = time.monotonic() + 10
        if not re.fullmatch('[a-f0-9]{40}', candidate or ''):
            raise ValueError('source-unavailable')
        self.candidate = candidate
        if self.git('rev-parse', 'HEAD').decode().strip() != candidate:
            raise ValueError('source-unavailable')
        self.paths = set()
        for item in self.git('ls-tree', '-rz', '--full-tree', candidate).split(b'\0'):
            if not item:continue
            metadata, name = item.split(b'\t', 1);mode, kind, _ = metadata.split()
            name = name.decode()
            if mode in (b'100644', b'100755') and kind == b'blob' and (name == 'Makefile' or name.endswith('.py') or name.endswith('_test.go')):
                self.paths.add(name)

    def git(self, *args):
        # 关键：只读Git原字节先0600捕获；不用信号代理，避免覆盖主Session的stop处理器。
        self.private.mkdir(parents=True, exist_ok=True, mode=0o700)
        paths = [self.private / ('source-' + uuid.uuid4().hex + '.' + name + '.raw') for name in ('stdout', 'stderr')]
        streams = [os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') for path in paths]
        try:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:raise ValueError('source-unavailable')
            result = subprocess.run(['git', '--no-optional-locks', *args], cwd=self.root, stdout=streams[0], stderr=streams[1],
                timeout=min(5, remaining), env={'PATH': os.environ.get('PATH', ''), 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'})
        finally:
            for stream in streams:stream.flush();os.fsync(stream.fileno());stream.close()
        if result.returncode:raise ValueError('source-unavailable')
        return paths[0].read_bytes()

    def relative(self, filename):
        try:
            path = pathlib.Path(filename)
            if not path.is_absolute():path = self.root / path
            relative = path.relative_to(self.root)
            if any(part in ('.', '..') for part in relative.parts):return None
            current = self.root
            for part in relative.parts:
                current = current / part
                if current.is_symlink():return None
            name = relative.as_posix()
            return name if name in self.paths and path.resolve().is_relative_to(self.root) else None
        except (ValueError, OSError):return None

    def load(self, name):
        if name not in self.cache:
            if self.relative(name) != name:raise ValueError('source-unavailable')
            data = (self.root / name).read_bytes()
            if len(data) > 2 * 1024 * 1024 or data != self.git('cat-file', 'blob', self.candidate + ':' + name):
                raise ValueError('source-unavailable')
            self.cache[name] = (data, hashlib.sha256(data).hexdigest())
        return self.cache[name]

    def frame(self, filename, line):
        try:
            name = self.relative(filename)
            if not name or not name.endswith('.py'):raise ValueError('source-unavailable')
            data, digest = self.load(name)
            if not 1 <= line <= len(data.splitlines()):raise ValueError('source-unavailable')
            functions = [node for node in ast.walk(ast.parse(data)) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and node.lineno <= line <= node.end_lineno]
            function = min(functions, key=lambda node: node.end_lineno - node.lineno).name if functions else '<module>'
            return dict(file=name, line=line, function=function, sourceBlobSHA256=digest)
        except Exception:self.unknown = True;return None

    def go_frame(self, basename, line):
        """Go testing只报告basename时须唯一绑定冻结blob；函数未知，不从输出猜函数或复制message。"""
        try:
            matches = [name for name in self.paths if name.endswith('_test.go') and pathlib.PurePosixPath(name).name == basename]
            if len(matches) != 1:raise ValueError('source-unavailable')
            name = matches[0];data, digest = self.load(name)
            if not 1 <= line <= len(data.splitlines()):raise ValueError('source-unavailable')
            return dict(file=name, line=line, function='<go-source>', sourceBlobSHA256=digest)
        except Exception:self.unknown = True;return None

    def make_frame(self, target, line):
        """报错行必须属于唯一冻结质量rule的连续tab recipe；不能仅凭target名字放行。"""
        try:
            if target not in QUALITY_MAKE_TARGETS:raise ValueError('source-unavailable')
            data, digest = self.load('Makefile');lines = data.decode().splitlines()
            rules = [n for n, text in enumerate(lines) if re.fullmatch(re.escape(target) + r':(?![:=])[^;]*', text)]
            if len(rules) != 1 or not rules[0] + 1 < line <= len(lines):raise ValueError('source-unavailable')
            if not all(text.startswith('\t') for text in lines[rules[0] + 1:line]):raise ValueError('source-unavailable')
            return dict(file='Makefile', line=line, function='<make-recipe>', target=target, sourceBlobSHA256=digest)
        except Exception:self.unknown = True;return None

    def test(self, method, identity=None):
        matches = []
        for name in sorted(self.paths):
            try:
                if identity is not None:
                    parts = identity.split('.')
                    if len(parts) < 3 or parts[-1] != method or pathlib.PurePosixPath(name).stem != parts[-3] or not name.endswith('.py'):continue
                    data, digest = self.load(name)
                    for cls in ast.walk(ast.parse(data)):
                        if isinstance(cls, ast.ClassDef) and cls.name == parts[-2]:
                            for node in cls.body:
                                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method:
                                    matches.append(dict(testId=name + '::' + cls.name + '.' + node.name, file=name,
                                        line=node.lineno, function=node.name, sourceBlobSHA256=digest))
                elif name.endswith('_test.go'):
                    if self.relative(name) != name:raise ValueError('source-unavailable')
                    pattern = r'^func (' + re.escape(method) + r')\(\s*[A-Za-z_]\w*\s+\*testing\.T\s*\)'
                    if not re.search(pattern, (self.root / name).read_text(), re.M):continue
                    data, digest = self.load(name)
                    found = re.search(pattern, data.decode(), re.M)
                    matches.append(dict(testId=name + '::' + method, file=name,
                        line=data.decode()[:found.start()].count('\n') + 1, function=method, sourceBlobSHA256=digest))
            except Exception:self.unknown = True
        if len(matches) != 1:self.unknown = True;return None
        return matches[0]


def project_output(result, *, label, candidate, private_root, root=ROOT):
    """同一次原命令完成后仅识别源码已验证的失败标识；原message/assertdiff永不投影。"""
    value = dict(schemaVersion=1, kind='process-diagnostic', productGate=False,
        operation=label if label in ('quality', 'generated') else 'capture', exit=result.returncode,
        state='completed' if result.returncode == 0 else 'failed', unknown=result.returncode != 0,
        testFailures=[], runningTests=[], observedMarkers=[], repoFrames=[], raw={name: dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            for name, data in [('stdout', result.stdout), ('stderr', result.stderr)]})
    if result.returncode == 0:return value
    try:
        source = _FrozenSources(candidate, root, private_root)
        # 只读有界前缀；完整原bytes/hash仍在上面的私有捕获摘要中，截断明确unknown。
        limit = 1024 * 1024;attempts = 0
        source.unknown = len(result.stdout) > limit or len(result.stderr) > limit
        # 关键：同一有界前缀先排静态阶段/timeout/失败根ID，普通位置不能耗尽查询预算后饿死末尾Make错误。
        # 每组最多保留65候选以检测64上限；总查询、frame、ID上限保持原值，不重复执行命令。
        groups = [[], [], [], []];timeout_stage = 0
        def queue(priority, item):
            if len(groups[priority]) < 65:groups[priority].append(item)
            else:source.unknown = True
        for line in (result.stdout[:limit] + b'\n\n' + result.stderr[:limit]).decode(errors='replace').splitlines():
            if len(line) > 2048:
                if '_test.go:' in line or line.startswith(('make', 'panic: test timed out after')) or timeout_stage:
                    source.unknown = True
                timeout_stage = 0
                continue
            make = re.fullmatch(r'make(?:\[[0-9]+\])?: \*\*\* \[Makefile:([0-9]+): ([A-Za-z0-9_-]+)\](?: .*)?', line)
            if make:queue(0, ('make', make[2], int(make[1])));timeout_stage = 0;continue
            if re.fullmatch(r'panic: test timed out after ' + GO_DURATION, line):
                queue(1, ('timeout',));timeout_stage = 1;continue
            if timeout_stage == 1 and line == 'running tests:':timeout_stage = 2;continue
            if timeout_stage == 2 and line.startswith('\t'):
                running = re.fullmatch(r'\t(Test[A-Za-z0-9_]+) \(' + GO_DURATION + r'\)', line)
                if running:queue(1, ('running', running[1], None))
                else:source.unknown = True
                continue
            timeout_stage = 0
            unit = re.fullmatch(r'(?:FAIL|ERROR): (test_[A-Za-z0-9_]+) \(([A-Za-z0-9_.]+)\)', line)
            go = re.fullmatch(r'--- FAIL: (Test[A-Za-z0-9_]+) \([0-9.]+s\)', line)
            frame = re.fullmatch(r'\s*File "([^"]+)", line ([0-9]+), in [^\r\n]+', line)
            # 关键：仅消费Go行的静态basename/行号；冒号后的原断言、子例和动态路径一律不投影。
            go_location = re.match(r'[ \t]+([A-Za-z_][A-Za-z0-9_]*_test\.go):([0-9]+):', line)
            if not (unit or go or frame or go_location):
                if (line.startswith(('FAIL: ', 'ERROR: ', '--- FAIL: ')) or line.lstrip().startswith('File "') or
                        re.match(r'[ \t]+\S.*_test\.go:[0-9]+:', line) or
                        re.match(r'make(?:\[[0-9]+\])?: \*\*\*', line) or line.startswith('panic: test timed out after')):
                    source.unknown = True
                continue
            if unit:queue(2, ('failed', unit[1], unit[2]))
            elif go:queue(2, ('failed', go[1], None))
            elif go_location:queue(3, ('go', go_location[1], int(go_location[2])))
            elif frame:queue(3, ('python', frame[1], int(frame[2])))
        for kind, *args in sum(groups, []):
            attempts += 1
            if attempts > 64:source.unknown = True;break
            if kind == 'timeout':
                if 'go-test-timeout' not in value['observedMarkers']:value['observedMarkers'].append('go-test-timeout')
                continue
            if kind in ('failed', 'running'):
                item = source.test(*args);field = 'runningTests' if kind == 'running' else 'testFailures'
                if item and item not in value[field]:
                    if len(value['testFailures']) + len(value['runningTests']) < 32:value[field].append(item)
                    else:source.unknown = True
            else:
                item = source.make_frame(*args) if kind == 'make' else source.go_frame(*args) if kind == 'go' else source.frame(*args)
                if item and item not in value['repoFrames']:
                    if len(value['repoFrames']) < 8:value['repoFrames'].append(item)
                    else:source.unknown = True
        value['unknown'] = source.unknown or not (value['testFailures'] or value['runningTests'] or value['repoFrames'])
    except Exception:pass
    return value


def project_exception(error, *, candidate, private_root, root=ROOT, prepare_type=None):
    """实际traceback只取冻结repo静态frame；固定PrepareError之外明确unknown，不反射动态类型。"""
    value = dict(schemaVersion=1, kind='source-diagnostic', productGate=False, errorCode='unknown', repoFrames=[], unknown=True)
    if prepare_type is not None and isinstance(error, prepare_type) and len(error.args) == 1:
        code = error.args[0]
        if type(code) is str and code in PREPARE_CODES:value['errorCode'] = code
    try:
        source = _FrozenSources(candidate, root, private_root);tb = error.__traceback__;attempts = 0
        while tb is not None:
            attempts += 1
            if attempts > 64:source.unknown = True;break
            item = source.frame(tb.tb_frame.f_code.co_filename, tb.tb_lineno)
            if item and item not in value['repoFrames']:
                if len(value['repoFrames']) < 8:value['repoFrames'].append(item)
                else:source.unknown = True
            tb = tb.tb_next
        value['unknown'] = source.unknown or not value['repoFrames']
    except Exception:pass
    return value


def capture(argv, *, private_root, label, cwd=None, env=None, timeout=None, publish=True):
    """执行真正子进程；原 stdout/stderr 从第一字节起即写入 0600 文件。"""
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,80}', label):
        raise ValueError('CAPTURE_LABEL_INVALID')
    root = pathlib.Path(private_root)
    if not root.is_absolute() or root.is_symlink():
        raise ValueError('CAPTURE_ROOT_INVALID')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    prefix = label + '-' + uuid.uuid4().hex
    paths = [root / (prefix + '.' + stream + '.raw') for stream in ('stdout', 'stderr')]
    handles = [os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') for path in paths]
    started = time.time()
    process = None
    previous = {}
    signals = []
    timed_out = False
    try:
        process = subprocess.Popen(argv, stdout=handles[0], stderr=handles[1], cwd=cwd, env=env, start_new_session=True)
        def forward(number, frame):
            signals.append(number)
            # 关键：只转发首次信号，重复信号只留记录，避免再次杀掉正在 finally 的清理进程。
            if len(signals) > 1:
                return
            try:
                os.killpg(process.pid, number)
            except ProcessLookupError:
                pass
        for number in (signal.SIGTERM, signal.SIGINT):
            previous[number] = signal.signal(number, forward)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            code = 124
    except OSError:
        # 启动失败也只公开固定 exit；异常中的 argv/路径可能含私有值。
        code = 127
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        for handle in handles:
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
    child_exit = process.returncode if process is not None else None
    if signals and code == 0:
        code = 128 + signals[0]
    receipt = {'id': label, 'exit': code, 'childExit': child_exit, 'timedOut': timed_out, 'startedAt': started, 'finishedAt': time.time(),
               'signals': signals, 'raw': [{'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                          'bytes': path.stat().st_size} for path in paths]}
    receipt_path = root / (prefix + '.json')
    descriptor = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(receipt, stream, indent=2)
        stream.write('\n')
    if publish:
        print(json.dumps({'id': label, 'exit': code, 'stdoutBytes': receipt['raw'][0]['bytes'], 'stderrBytes': receipt['raw'][1]['bytes']}), flush=True)
    return subprocess.CompletedProcess(argv, code, paths[0].read_bytes(), paths[1].read_bytes())


def main():
    parser = argparse.ArgumentParser(description='受保护子进程输出捕获')
    parser.add_argument('--private-root', required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--public-report')
    parser.add_argument('--candidate')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('必须有明确命令')
    try:
        if args.public_report and (not pathlib.Path(args.public_report).is_absolute() or pathlib.Path(args.public_report).is_symlink()):
            raise ValueError('CAPTURE_REPORT_INVALID')
        result = capture(command, private_root=args.private_root, label=args.label)
        if args.public_report:
            try:
                value = project_output(result, label=args.label, candidate=args.candidate, private_root=args.private_root)
                atomic_json(args.public_report, value)
            except Exception:
                # 关键：诊断IO失败不能抹掉原失败；原命令成功时也不能伪装完整成功。
                print(json.dumps({'id': 'capture-diagnostic', 'errorCode': 'diagnostic-write-failed'}), flush=True)
                return (result.returncode if result.returncode >= 0 else 128 - result.returncode) or 125
        return result.returncode if result.returncode >= 0 else 128 - result.returncode
    except (ValueError, OSError):
        print(json.dumps({'id': 'capture', 'exit': 125}), flush=True)
        return 125


if __name__ == '__main__':
    sys.exit(main())
